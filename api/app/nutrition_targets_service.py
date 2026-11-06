"""Materialização das metas diárias de nutrição (plano §5 E2 e §8.1; Lote L2b).

`ensure_daily_targets(conn, user_id, window)` é idempotente:

1. lê os planos PUBLICADOS do aluno (vigente e substituídos) com `FOR SHARE`
   dentro da transação (uma publicação concorrente espera este cálculo terminar,
   ou este espera a publicação: nunca se regrava uma meta com o plano antigo) e
   decide o plano de cada dia pelo motor (`domain/nutrition/plan.plan_in_force`);
2. pergunta ao motor quais dias da janela escrever
   (`daily_target.days_to_materialize`: nunca dia congelado; meta nova só até
   `window.create_until`);
3. lê as séries cruas do aluno — perfil (`users`), bioimpedância
   (`body_composition`), fita (`body_measurements`: pescoço, cintura, quadril) —
   e as sessões CONCLUÍDAS (`activities`, `session_status = 'done'`);
4. para cada dia: sujeito (`SubjectSeries.readings_on` → `resolve_subject`),
   contexto (`day_context_for`), parâmetros (`PlanSettings.plan_params`) e
   `compute_daily_target`; grava em lote (`executemany`) só o que mudou (plano,
   versão do motor ou o hash das entradas);
5. devolve as linhas da janela (inclusive as congeladas, intactas).

Diário (L4, risco R8): `ensure_diary_targets(conn, user_id, days)` pergunta ao
motor o que fazer com a meta de cada dia lido (`daily_target.diary_target_action`):
materializa os não congelados pelo fluxo acima (`diary_window`), só lê os
congelados com meta e grava UMA vez (`INSERT ... ON DUPLICATE KEY UPDATE id = id`,
nunca sobrescreve) a meta do dia que congelou sem meta, marcada pelo motor
(`backfill_limitations`, `backfill_marker`). Sem plano em vigor no dia, nada.

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7 e §47): este módulo
não calcula nada de nutrição. Escolha de leitura, %G, TMB, kcal do treino,
congelamento, janelas e vigência são do motor; aqui só há SQL, a cópia dos
campos do `DailyTarget` para as colunas e uma comparação de igualdade (hash das
entradas) para não regravar o que não mudou. Sem plano publicado, nada é
materializado.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from .body_measurements_schema import has_body_measurements_table
from .domain.nutrition import ENGINE_VERSION, compute_daily_target
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.energy_expenditure import (
    NO_EXTRA_ACTIVITY_KCAL as NO_EXTRA_ACTIVITY,
    ExtraActivity,
    TdeeBasis,
    activity_total_kcal,
    intervals_in_sync,
)
from .domain.nutrition.daily_target import (
    DailyTarget,
    DayContext,
    SessionFacts,
    TargetAction,
    TargetWindow,
    backfill_limitations,
    backfill_marker,
    day_context_for,
    days_to_materialize,
    diary_target_action,
    diary_window,
    is_target_frozen,
    was_backfilled,
)
from .domain.nutrition.plan import (
    PROJECTION_TRAINING_WINDOW_DAYS,
    PlanSettings,
    PublishedPlanRef,
    build_plan_settings,
    plan_in_force,
    training_kcal_of_day,
)
from .domain.nutrition.subject import NAVY_SITE_FIELDS, Reading, ResolvedSubject, SubjectSeries, resolve_subject
from .intelligence.dashboard.time_windows import user_today_for
from .intervals_activity_kind import classify
from .nutrition_energy_service import EnergyHistory, energy_key, load_history
from .nutrition_schema import has_plan_energy_columns

logger = logging.getLogger(__name__)

PLANS_TABLE = "nutrition_plans"
TARGETS_TABLE = "nutrition_daily_targets"
PUBLISHED = "published"

# Colunas do plano que viram `PlanSettings` (nomes da 078).
PLAN_SETTINGS_COLUMNS = (
    "strategy, advanced, bmr_formula, activity_level, training_kcal_mode, goal_body_fat_percent, goal_weight_kg, "
    "weekly_rate_share, surplus_share, protein_g_per_kg, fat_kcal_share, carb_min_g_per_kg, meals_per_day, "
    "macro_priority, manual_targets_json, carb_cycling_json, maintenance_kcal_adjust, energy_params_json"
)
# Sem a migração 106 a coluna `energy_params_json` não existe: a leitura dos planos publicados usa a lista SEM ela (planos
# legados seguem funcionando como antes); as rotas de PLANO é que respondem 503 `nutrition_plan_migration_pending`.
PLAN_SETTINGS_COLUMNS_WITHOUT_ENERGY = PLAN_SETTINGS_COLUMNS.replace(", energy_params_json", "")

# `FOR SHARE`: dentro da transação, a leitura dos planos espera uma publicação em
# andamento (que tem os planos travados para escrita) e trava a publicação
# seguinte até este cálculo terminar.
_PUBLISHED_PLANS_SQL = (
    f"SELECT id, effective_from, published_at, {PLAN_SETTINGS_COLUMNS} FROM {PLANS_TABLE} "
    "WHERE user_id = %s AND status = %s AND effective_from IS NOT NULL FOR SHARE"
)

_PUBLISHED_PLANS_WITHOUT_ENERGY_SQL = (
    f"SELECT id, effective_from, published_at, {PLAN_SETTINGS_COLUMNS_WITHOUT_ENERGY} FROM {PLANS_TABLE} "
    "WHERE user_id = %s AND status = %s AND effective_from IS NOT NULL FOR SHARE"
)

_TARGET_COLUMNS = (
    "local_date, plan_id, day_kind, is_training_day, training_known, phase_kind, kcal_target, protein_g, carbs_g, "
    "fat_g, fiber_g, water_ml, bmr_kcal, bmr_formula, tdee_kcal, training_kcal, training_kcal_nature, "
    "limitations_json, inputs_hash, engine_version, computed_at"
)
# O que o motor gravou em `inputs_json` e a tela precisa (cardio/caminhada somados e como o gasto foi montado): extraído
# no banco, sem trazer o JSON inteiro. `target_row_public` lê só estes aliases.
_TARGET_JSON_COLUMNS = (
    "JSON_EXTRACT(inputs_json, '$.extra_activity') AS extra_activity_json, "
    "JSON_EXTRACT(inputs_json, '$.tdee_basis') AS tdee_basis_json, "
    "JSON_EXTRACT(inputs_json, '$.energy') AS energy_json"
)
_TARGETS_SQL = (
    f"SELECT {_TARGET_COLUMNS}, {_TARGET_JSON_COLUMNS} FROM {TARGETS_TABLE} "
    "WHERE user_id = %s AND local_date BETWEEN %s AND %s ORDER BY local_date"
)
# Gasto gravado pelo motor na meta do dia (`inputs_json.tdee`): a composição do dia relê isto, nada se recalcula.
_STORED_TDEE_SQL = (
    f"SELECT JSON_EXTRACT(inputs_json, '$.tdee') AS tdee_json FROM {TARGETS_TABLE} "
    "WHERE user_id = %s AND local_date = %s"
)
_LAST_TARGET_DATE_SQL = f"SELECT MAX(local_date) AS ultimo FROM {TARGETS_TABLE} WHERE user_id = %s"

UPSERT_COLUMNS = (
    "user_id", "local_date", "plan_id", "day_kind", "is_training_day", "training_known", "phase_kind",
    "kcal_target", "protein_g", "carbs_g", "fat_g", "fiber_g", "bmr_kcal", "bmr_formula", "tdee_kcal",
    "training_kcal", "training_kcal_nature", "inputs_json", "limitations_json", "inputs_hash", "engine_version",
)
_UPDATE_ON_DUPLICATE = ", ".join(f"{c} = VALUES({c})" for c in UPSERT_COLUMNS if c not in ("user_id", "local_date"))
_UPSERT_SQL = (
    f"INSERT INTO {TARGETS_TABLE} ({', '.join(UPSERT_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(UPSERT_COLUMNS))}) "
    f"ON DUPLICATE KEY UPDATE {_UPDATE_ON_DUPLICATE}, computed_at = CURRENT_TIMESTAMP"
)
# Meta retroativa do diário (dia congelado sem meta): grava UMA vez; se outra
# requisição gravou no meio, a primeira fica (no-op no UNIQUE).
_INSERT_ONCE_SQL = (
    f"INSERT INTO {TARGETS_TABLE} ({', '.join(UPSERT_COLUMNS)}) "
    f"VALUES ({', '.join(['%s'] * len(UPSERT_COLUMNS))}) "
    "ON DUPLICATE KEY UPDATE id = id"
)

_PROFILE_SQL = "SELECT sex, birth_date, height_cm, activity_level FROM users WHERE id = %s"

# Histórico inteiro até o fim da janela: a escolha "por grandeza" (a leitura
# VÁLIDA mais recente de cada uma) é do motor e pode precisar de uma bioimpedância
# antiga quando as pesagens recentes só têm peso. Uma linha por dia e por local.
_COMPOSITION_SQL = (
    "SELECT local_date, weight_kg, body_fat_percent, fat_free_mass_kg, bmr_kcal FROM body_composition "
    "WHERE user_id = %s AND local_date <= %s "
    "AND (weight_kg IS NOT NULL OR body_fat_percent IS NOT NULL OR fat_free_mass_kg IS NOT NULL OR bmr_kcal IS NOT NULL) "
    "ORDER BY local_date, id"
)
_SITE_PLACEHOLDERS = ", ".join(["%s"] * len(NAVY_SITE_FIELDS))
_SITES_SQL = (
    "SELECT site, local_date, value_cm FROM body_measurements "
    f"WHERE user_id = %s AND site IN ({_SITE_PLACEHOLDERS}) AND local_date <= %s ORDER BY local_date, id"
)

# Sessão CONCLUÍDA: a mesma condição na materialização (contexto do dia) e na
# lista de sessões do diário (`nutrition_days`), para a lista bater com a kcal usada.
DONE_SESSION_STATUS = "done"
_SESSIONS_SQL = (
    "SELECT local_date, calories_kcal, duration_seconds FROM activities "
    f"WHERE user_id = %s AND session_status = '{DONE_SESSION_STATUS}' AND local_date BETWEEN %s AND %s"
)


# Atividades do Intervals do dia que NÃO são o treino de `activities` (sem vínculo `activity_intervals_link`: a
# vinculada já é a sessão de força). A CLASSE (cardio/força/outra) vem de `intervals_activity_kind` (dono), em
# Python; quem decide o que soma é `energy_expenditure.extra_activity_kcal`. Sem LIKE de classificação aqui.
EXTRA_ACTIVITIES_SQL = (
    "SELECT ia.activity_date, ia.sport_type, ia.name, ia.calories_kcal "
    "FROM intervals_activities ia "
    "LEFT JOIN activity_intervals_link l ON l.intervals_activity_id = ia.id AND l.user_id = ia.user_id "
    "WHERE ia.user_id = %s AND ia.activity_date BETWEEN %s AND %s AND l.id IS NULL "
    "ORDER BY ia.activity_date, ia.id"
)


# Conexão e frescor da sincronização do Intervals (modo automático): conectado = athlete id e chave gravados (a chave
# nunca sai daqui, só se existe). A PROVA de que o job rodou é só o `synced_at` das ATIVIDADES: o job (e o botão
# "Sincronizar") regrava `synced_at = CURRENT_TIMESTAMP` de TODA atividade da janela a cada execução bem-sucedida,
# com a chave válida; o wellness NÃO prova (edição manual e refresh só de wellness também o tocam, mesmo com a chave
# revogada). Sem atividade na janela do job não há prova: o resultado é fallback (na dúvida, nunca "em dia"). Idade em
# MINUTOS no relógio do banco; quem decide "em dia" é `energy_expenditure.intervals_in_sync`.
INTERVALS_CONNECTION_SQL = "SELECT intervals_athlete_id, intervals_api_key_encrypted FROM users WHERE id = %s"
INTERVALS_SYNC_AGE_SQL = (
    "SELECT TIMESTAMPDIFF(MINUTE, MAX(synced_at), CURRENT_TIMESTAMP) AS minutes_since_sync "
    "FROM intervals_activities WHERE user_id = %s"
)


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


# ---------------------------------------------------------------------------
# Plano do aluno
# ---------------------------------------------------------------------------


def settings_from_row(row: dict[str, Any]) -> PlanSettings:
    """`PlanSettings` de uma linha de `nutrition_plans`. Linha gravada já passou
    pela validação; se a faixa do motor mudar depois, o orquestrador avisa
    (`param_out_of_range`) em vez de recusar o plano publicado."""
    manual = _json_value(row.get("manual_targets_json"))
    modo_manual = None
    if isinstance(manual, dict):  # o modo mora junto das gramas no JSON (sem coluna nova)
        manual = dict(manual)
        modo_manual = manual.pop("training_mode", None)
    validacao = build_plan_settings(
        strategy=row.get("strategy"),
        advanced=bool(int(row.get("advanced") or 0)),
        protein_g_per_kg=_float(row.get("protein_g_per_kg")),
        fat_kcal_share=_float(row.get("fat_kcal_share")),
        weekly_rate_share=_float(row.get("weekly_rate_share")),
        surplus_share=_float(row.get("surplus_share")),
        carb_min_g_per_kg=_float(row.get("carb_min_g_per_kg")),
        bmr_formula=row.get("bmr_formula"),
        tdee_mode=row.get("training_kcal_mode"),
        activity_level=row.get("activity_level"),
        meals_per_day=int(row["meals_per_day"]) if row.get("meals_per_day") is not None else None,
        goal_body_fat_percent=_float(row.get("goal_body_fat_percent")),
        goal_weight_kg=_float(row.get("goal_weight_kg")),
        macro_priority=row.get("macro_priority"),
        manual_targets=manual,
        manual_training_mode=modo_manual,
        cycle_days=_json_value(row.get("carb_cycling_json")),
        maintenance_kcal_adjust=int(row["maintenance_kcal_adjust"]) if row.get("maintenance_kcal_adjust") else None,
        energy_params=_json_value(row.get("energy_params_json")),
    )
    if validacao.settings is None:  # pragma: no cover - estratégia gravada sempre veio do motor
        raise ValueError(f"plano {row.get('id')} com estratégia desconhecida: {row.get('strategy')!r}")
    return validacao.settings


@dataclass(frozen=True)
class PublishedPlans:
    refs: tuple[PublishedPlanRef, ...]
    settings: dict[int, PlanSettings]

    def for_day(self, day: date) -> Optional[tuple[int, PlanSettings]]:
        """Plano em vigor no dia, pelo motor (`plan_in_force`)."""
        ref = plan_in_force(self.refs, day)
        return None if ref is None else (ref.plan_id, self.settings[ref.plan_id])


def load_published_plans(cur: Any, user_id: int) -> PublishedPlans:
    # `has_plan_energy_columns` tem cache SÓ do positivo: depois da 106 não custa SELECT; antes dela checa a cada leitura.
    cur.execute(_PUBLISHED_PLANS_SQL if has_plan_energy_columns(cur) else _PUBLISHED_PLANS_WITHOUT_ENERGY_SQL, (int(user_id), PUBLISHED))
    refs: list[PublishedPlanRef] = []
    settings: dict[int, PlanSettings] = {}
    for row in cur.fetchall() or []:
        plano_id = int(row["id"])
        inicio = _as_date(row.get("effective_from"))
        if inicio is None:
            continue
        refs.append(PublishedPlanRef(plan_id=plano_id, effective_from=inicio, published_at=row.get("published_at")))
        settings[plano_id] = settings_from_row(row)
    return PublishedPlans(refs=tuple(refs), settings=settings)


# ---------------------------------------------------------------------------
# Leituras do aluno (só SQL; quem escolhe é `subject.SubjectSeries.readings_on`)
# ---------------------------------------------------------------------------


@dataclass
class UserInputs:
    """Séries cruas do aluno (motor) e as sessões concluídas por dia."""

    series: SubjectSeries = field(default_factory=SubjectSeries)
    sessions: dict[date, list[SessionFacts]] = field(default_factory=dict)
    extra_activities: dict[date, list[ExtraActivity]] = field(default_factory=dict)
    # Intervals conectado e sincronizado (decisão do motor, lida UMA vez por carga); só vale no modo automático.
    intervals_in_sync: bool = False
    # False = o Intervals NÃO foi consultado nesta carga (nenhum plano automático em jogo): os modos clássicos não pagam
    # as consultas novas.
    intervals_loaded: bool = False
    # Plano ADAPTATIVO em jogo (Lote C): o histórico processado por `watch_calorie_mode` (uma leitura e um `replay` por
    # carga). Vazio nos planos legados, que não pagam nenhuma consulta nova.
    energy: dict[tuple[str, str], EnergyHistory] = field(default_factory=dict)
    # Modos cuja leitura do histórico FALHOU nesta carga (sem entrada em `energy`): a meta do dia desse modo não pode ser
    # regravada com a estimativa inicial por causa de uma falha transitória (`ensure_daily_targets`).
    energy_failed: frozenset[tuple[str, str]] = frozenset()

    def covers(self, plans: Iterable[PlanSettings]) -> bool:
        """As entradas têm o histórico (ou a falha registrada) de TODO modo adaptativo dos `plans`?"""
        return all(energy_key(p) in self.energy or energy_key(p) in self.energy_failed for p in plans if p.is_adaptive)


def _decimal_free(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in row.items()}


def _series(rows: Iterable[dict[str, Any]], column: str) -> tuple[Reading, ...]:
    return tuple(
        Reading(_float(row.get(column)), _as_date(row.get("local_date")))
        for row in rows
        if row.get(column) is not None
    )


def load_user_inputs(
    cur: Any, user_id: int, date_from: date, date_to: date, today: date, plans: Iterable[PlanSettings] = ()
) -> UserInputs:
    """Perfil e séries cruas até `date_to` e as sessões concluídas de
    `[date_from, min(date_to, hoje)]`."""
    uid = int(user_id)
    cur.execute(_PROFILE_SQL, (uid,))
    perfil = _decimal_free(cur.fetchone() or {})

    cur.execute(_COMPOSITION_SQL, (uid, date_to))
    composicao = list(cur.fetchall() or [])

    locais: dict[str, tuple[Reading, ...]] = {}
    if has_body_measurements_table(cur):
        cur.execute(_SITES_SQL, (uid, *NAVY_SITE_FIELDS, date_to))
        medidas = list(cur.fetchall() or [])
        for site in NAVY_SITE_FIELDS:
            locais[site] = _series((m for m in medidas if str(m.get("site")) == site), "value_cm")

    series = SubjectSeries(
        sex=perfil.get("sex"),
        birth_date=_as_date(perfil.get("birth_date")),
        height_cm=perfil.get("height_cm"),
        activity_level=perfil.get("activity_level"),
        weight=_series(composicao, "weight_kg"),
        bioimpedance_body_fat=_series(composicao, "body_fat_percent"),
        bioimpedance_ffm=_series(composicao, "fat_free_mass_kg"),
        scale_bmr=_series(composicao, "bmr_kcal"),
        sites=locais,
    )

    sessoes: dict[date, list[SessionFacts]] = {}
    fim_sessoes = min(date_to, today)
    if fim_sessoes >= date_from:
        cur.execute(_SESSIONS_SQL, (uid, date_from, fim_sessoes))
        for row in cur.fetchall() or []:
            dia = _as_date(row.get("local_date"))
            if dia is not None:
                sessoes.setdefault(dia, []).append(
                    SessionFacts(calories_kcal=_float(row.get("calories_kcal")), duration_seconds=row.get("duration_seconds"))
                )
    # Conexão, frescor e atividades do Intervals só quando algum plano em jogo é o automático (planos clássicos não
    # fazem nenhum SELECT novo). `plans` = as configurações que vão usar estas entradas.
    com_intervals = any(plano.is_auto_intervals for plano in plans)
    extras: dict[date, list[ExtraActivity]] = {}
    if com_intervals and fim_sessoes >= date_from:
        cur.execute(EXTRA_ACTIVITIES_SQL, (uid, date_from, fim_sessoes))
        for row in cur.fetchall() or []:
            dia = _as_date(row.get("activity_date"))
            if dia is not None:
                extras.setdefault(dia, []).append(
                    ExtraActivity(
                        kind=classify(row.get("sport_type"), row.get("name")),
                        calories_kcal=_float(row.get("calories_kcal")),
                        label=row.get("name") or row.get("sport_type"),
                    )
                )
    em_dia = False
    if com_intervals:
        cur.execute(INTERVALS_CONNECTION_SQL, (uid,))
        conexao = cur.fetchone() or {}
        conectado = bool(conexao.get("intervals_athlete_id")) and bool(conexao.get("intervals_api_key_encrypted"))
        idade = None
        if conectado:
            cur.execute(INTERVALS_SYNC_AGE_SQL, (uid,))
            idade = (cur.fetchone() or {}).get("minutes_since_sync")
        em_dia = intervals_in_sync(conectado, idade)
    return UserInputs(
        series=series,
        sessions=sessoes,
        extra_activities=extras,
        intervals_in_sync=em_dia,
        intervals_loaded=com_intervals,
        **_load_energy(cur, uid, today, series, plans),
    )


def _load_energy(cur: Any, user_id: int, today: date, series: SubjectSeries, plans: Iterable[PlanSettings]) -> dict[str, Any]:
    """Histórico do gasto adaptativo, UMA vez por (`watch_calorie_mode`, fórmula da TMB) dos planos adaptativos em jogo (gate como
    `is_auto_intervals`: sem plano adaptativo, nenhuma consulta). Devolve `energy` (modo -> histórico) e `energy_failed`
    (modos cuja leitura ou `replay` falhou: NÃO derrubam o cálculo; o log traz só o tipo da exceção). Dia de plano de um modo
    que falhou sai com a estimativa inicial, SEM sobrescrever meta já gravada (`ensure_daily_targets`)."""
    chaves = list(dict.fromkeys(energy_key(p) for p in plans if p.is_adaptive))
    historicos: dict[tuple[str, str], EnergyHistory] = {}
    falhas: set[tuple[str, str]] = set()
    for chave in chaves:
        try:
            historicos[chave] = load_history(cur, user_id, today, series, chave[0], bmr_formula=chave[1])
        except Exception as erro:  # noqa: BLE001 - isolamento deliberado: a meta nunca devolve 500 por causa do histórico
            logger.warning("nutrition.adaptive_history_failed error=%s", type(erro).__name__)
            falhas.add(chave)
    return {"energy": historicos, "energy_failed": frozenset(falhas)}


# ---------------------------------------------------------------------------
# Um dia
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayComputation:
    day: date
    target: DailyTarget
    subject: ResolvedSubject
    context: DayContext
    settings: PlanSettings


def compute_day(inputs: UserInputs, settings: PlanSettings, day: date, today: date) -> DayComputation:
    """Meta de UM dia pelo motor (sem gravar)."""
    sujeito = resolve_subject(inputs.series.readings_on(day), day, settings.subject_activity_level)
    # Só o plano automático leva os extras e o estado da sincronização ao contexto (os modos clássicos ficam como
    # sempre, hash inclusive). Sem sincronização os extras não valem e nem entram no contexto.
    automatico = settings.is_auto_intervals
    extras = inputs.extra_activities.get(day, ()) if automatico and inputs.intervals_in_sync else ()
    estimativa = None
    historico_indisponivel = False
    if settings.is_adaptive:  # a estimativa do dia vem do `replay` do histórico (dono: `adaptive_energy.estimate_for_day`)
        chave = energy_key(settings)
        historico = inputs.energy.get(chave)
        if historico is not None:
            estimativa = historico.estimate_for_day(day, today)
        elif chave in inputs.energy_failed:
            # A leitura do histórico falhou: o motor usa a estimativa inicial MARCADA (`history_unavailable`); o dia que já
            # tem meta gravada nem chega aqui. O serviço só passa o sinal (nenhuma TMB aqui).
            historico_indisponivel = True
    contexto = day_context_for(
        day, today, inputs.sessions.get(day, ()), extras,
        intervals_in_sync=inputs.intervals_in_sync if automatico else None, energy_estimate=estimativa,
        energy_history_unavailable=historico_indisponivel,
    )
    meta = compute_daily_target(
        sujeito.subject, settings.plan_params(sujeito.ffm_age_days), contexto, scale_bmr=sujeito.scale_bmr_kcal
    )
    return DayComputation(day=day, target=meta, subject=sujeito, context=contexto, settings=settings)


def past_training_kcal(inputs: UserInputs, settings: PlanSettings, today: date) -> list[Optional[float]]:
    """Gasto de atividade dos `PROJECTION_TRAINING_WINDOW_DAYS` dias ANTES de hoje (kcal do treino MAIS o cardio e as
    caminhadas do Intervals, `energy_expenditure.activity_total_kcal`; 0 em dia sem treino; `None` em dia de treino sem kcal e sem duração ou peso), com a kcal já resolvida pelo motor
    (informada > MET), para o TDEE estável da projeção (`plan.projection_tdee`). As entradas precisam cobrir a
    janela. Usada pela prévia do plano e pelo dashboard de nutrição, que projetam com o MESMO TDEE."""
    inicio = today - timedelta(days=PROJECTION_TRAINING_WINDOW_DAYS)
    resultado: list[Optional[float]] = []
    for dia in (inicio + timedelta(days=i) for i in range(PROJECTION_TRAINING_WINDOW_DAYS)):
        comp = compute_day(inputs, settings, dia, today)
        treino = training_kcal_of_day(comp.context.is_training_day, comp.target.training_kcal.kcal)
        resultado.append(activity_total_kcal(treino, comp.target.extra_activity.kcal))
    return resultado


def inputs_fingerprint(plan_id: int, comp: DayComputation) -> str:
    """Hash das ENTRADAS do cálculo de um dia (só para comparar igualdade e não
    regravar o que não mudou; nenhuma regra mora aqui)."""
    entradas = {
        "plan_id": int(plan_id),
        "engine_version": ENGINE_VERSION,
        "subject": comp.subject.to_dict(),
        "plan": comp.settings.fingerprint_dict(),
        "day_context": comp.context.to_dict(),
    }
    texto = json.dumps(entradas, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def day_public(comp: DayComputation, today: date) -> dict[str, Any]:
    """Um dia calculado (preview), no mesmo formato da linha materializada."""
    meta = comp.target
    return {
        "local_date": comp.day.isoformat(),
        "plan_id": None,
        "day_kind": meta.day_kind.value,
        "is_training_day": comp.context.is_training_day,
        "training_known": comp.context.training_known,
        "phase_kind": meta.phase_kind.value if meta.phase_kind else None,
        "kcal": meta.macros.kcal,
        "protein_g": meta.macros.protein_g,
        "carbs_g": meta.macros.carb_g,
        "fat_g": meta.macros.fat_g,
        "fiber_g": meta.macros.fiber_g,
        "water_ml": None,
        "bmr_kcal": meta.bmr.kcal,
        "bmr_formula": meta.bmr.formula.value,
        "tdee_kcal": meta.tdee.kcal,
        "training_kcal": meta.training_kcal.kcal,
        "training_kcal_nature": meta.training_kcal.kcal_nature.value if meta.training_kcal.kcal_nature else None,
        "extra_activity": meta.extra_activity.to_dict(),
        "tdee_basis": meta.tdee_basis.value,
        "energy": meta.energy,
        "limitations": nutrition_labels.limitation_items(meta.limitations),
        "engine_version": meta.engine_version,
        "computed_at": None,
        "frozen": is_target_frozen(comp.day, today),
    }


def target_row_public(row: dict[str, Any], today: date) -> dict[str, Any]:
    """Linha de `nutrition_daily_targets` para a API (Decimal -> float, NULL fica
    None). `training_known` é a decisão do motor GRAVADA no cálculo; `frozen` é
    a regra do motor sobre o dia."""
    dia = _as_date(row.get("local_date"))
    limitacoes = _json_value(row.get("limitations_json")) or []
    computado = row.get("computed_at")
    return {
        "local_date": dia.isoformat() if dia else None,
        "plan_id": int(row["plan_id"]) if row.get("plan_id") is not None else None,
        "day_kind": row.get("day_kind"),
        "is_training_day": bool(int(row.get("is_training_day") or 0)),
        "training_known": bool(int(row.get("training_known") or 0)),
        "phase_kind": row.get("phase_kind"),
        "kcal": _float(row.get("kcal_target")),
        "protein_g": _float(row.get("protein_g")),
        "carbs_g": _float(row.get("carbs_g")),
        "fat_g": _float(row.get("fat_g")),
        "fiber_g": _float(row.get("fiber_g")),
        "water_ml": _float(row.get("water_ml")),
        "bmr_kcal": _float(row.get("bmr_kcal")),
        "bmr_formula": row.get("bmr_formula"),
        "tdee_kcal": _float(row.get("tdee_kcal")),
        "training_kcal": _float(row.get("training_kcal")),
        # Cardio/caminhada somados ao gasto (modo automático): o dicionário que o motor gravou em `inputs_json`.
        "extra_activity": _json_value(row.get("extra_activity_json")) or NO_EXTRA_ACTIVITY.to_dict(),
        "tdee_basis": _json_value(row.get("tdee_basis_json")) or TdeeBasis.CLASSIC.value,
        # Retrato público da estimativa adaptativa do dia (gravado pelo motor em `inputs_json.energy`); `None` nos legados.
        "energy": _json_value(row.get("energy_json")),
        "training_kcal_nature": row.get("training_kcal_nature"),
        "limitations": nutrition_labels.limitation_items(limitacoes),
        "engine_version": row.get("engine_version"),
        "computed_at": computado.isoformat() if hasattr(computado, "isoformat") else computado,
        "frozen": dia is not None and is_target_frozen(dia, today),
        "backfilled": was_backfilled(limitacoes),
    }


def _upsert_params(
    user_id: int, plan_id: int, comp: DayComputation, fingerprint: str, *, backfilled_on: Optional[date] = None
) -> tuple[Any, ...]:
    meta = comp.target
    entradas = {
        **meta.to_dict(),
        "subject": comp.subject.to_dict(),
        "plan": comp.settings.to_dict(),
        "day_context": comp.context.to_dict(),
    }
    limitacoes = meta.limitations
    if backfilled_on is not None:
        entradas["materialization"] = backfill_marker(backfilled_on)
        limitacoes = backfill_limitations(meta)
    natureza = meta.training_kcal.kcal_nature
    valores = {
        "user_id": int(user_id),
        "local_date": comp.day,
        "plan_id": int(plan_id),
        "day_kind": meta.day_kind.value,
        "is_training_day": 1 if comp.context.is_training_day else 0,
        "training_known": 1 if comp.context.training_known else 0,
        "phase_kind": meta.phase_kind.value if meta.phase_kind else None,
        "kcal_target": meta.macros.kcal,
        "protein_g": meta.macros.protein_g,
        "carbs_g": meta.macros.carb_g,
        "fat_g": meta.macros.fat_g,
        "fiber_g": meta.macros.fiber_g,
        "bmr_kcal": meta.bmr.kcal,
        "bmr_formula": meta.bmr.formula.value,
        "tdee_kcal": meta.tdee.kcal,
        "training_kcal": meta.training_kcal.kcal,
        "training_kcal_nature": natureza.value if natureza else None,
        "inputs_json": entradas,
        "limitations_json": list(limitacoes),
        "inputs_hash": fingerprint,
        "engine_version": meta.engine_version,
    }
    return tuple(valores[coluna] for coluna in UPSERT_COLUMNS)


def _history_failed(entradas: "UserInputs", settings: PlanSettings) -> bool:
    """O histórico da chave deste dia adaptativo falhou nesta carga?"""
    return settings.is_adaptive and energy_key(settings) in entradas.energy_failed


def _history_failed_and_stored(
    entradas: "UserInputs", settings: PlanSettings, plan_id: int, existente: Optional[dict[str, Any]]
) -> bool:
    """O histórico falhou E o dia já tem meta gravada DO MESMO PLANO? Então não se regrava (a estimativa inicial a
    degradaria). Meta de OUTRO plano (acabou de publicar o adaptativo ou republicou com outros parâmetros) é regravada
    com a estimativa inicial marcada: nenhuma meta não congelada pode ficar com o plano antigo."""
    return (
        _history_failed(entradas, settings)
        and existente is not None
        and existente.get("plan_id") is not None
        and int(existente["plan_id"]) == int(plan_id)
    )


def _unchanged(existing: Optional[dict[str, Any]], plan_id: int, fingerprint: str) -> bool:
    return (
        existing is not None
        and existing.get("plan_id") is not None
        and int(existing["plan_id"]) == int(plan_id)
        and existing.get("engine_version") == ENGINE_VERSION
        and existing.get("inputs_hash") == fingerprint
    )


# ---------------------------------------------------------------------------
# Materialização
# ---------------------------------------------------------------------------


def read_stored_tdee(cur: Any, user_id: int, day: date) -> Optional[dict[str, Any]]:
    """O `tdee` que o motor gravou na meta do dia (`TdeeResult.to_dict()`), ou `None` sem meta/sem o campo. 1 SELECT."""
    cur.execute(_STORED_TDEE_SQL, (int(user_id), day))
    valor = _json_value((cur.fetchone() or {}).get("tdee_json"))
    return valor if isinstance(valor, dict) else None


def history_key_for_day(cur: Any, user_id: int, day: date) -> Optional[tuple[str, str]]:
    """(`watch_calorie_mode`, fórmula da TMB) do plano que VALE no dia (motor: `plan_in_force`), a MESMA chave do histórico
    da meta (`energy_key`); plano não adaptativo usa o modo padrão do dono. `None` sem plano em vigor. 1 SELECT."""
    em_vigor = load_published_plans(cur, user_id).for_day(day)
    if em_vigor is None:
        return None
    return energy_key(em_vigor[1])


def last_target_date(cur: Any, user_id: int) -> Optional[date]:
    cur.execute(_LAST_TARGET_DATE_SQL, (int(user_id),))
    row = cur.fetchone() or {}
    return _as_date(row.get("ultimo"))


def read_targets(cur: Any, user_id: int, date_from: date, date_to: date) -> list[dict[str, Any]]:
    cur.execute(_TARGETS_SQL, (int(user_id), date_from, date_to))
    return list(cur.fetchall() or [])


def ensure_daily_targets(
    conn: Any,
    user_id: int,
    window: TargetWindow,
    *,
    today: Optional[date] = None,
    inputs: Optional[UserInputs] = None,
) -> list[dict[str, Any]]:
    """Materializa a `window` (janela do motor) e devolve as linhas dela na forma
    pública. Idempotente: com os mesmos dados nada é regravado (hash das
    entradas); dia congelado nunca é escrito; meta nova só até
    `window.create_until`; só dias com plano publicado em vigor.

    `today` (dia civil do aluno) e `inputs` (séries já carregadas até o fim da
    janela) existem para o chamador não repetir consultas."""
    hoje = today if today is not None else user_today_for(conn, user_id)
    with conn.cursor() as cur:
        planos = load_published_plans(cur, user_id)
        existentes = {_as_date(r.get("local_date")): r for r in read_targets(cur, user_id, window.date_from, window.date_to)}
        dias = [
            (dia, *em_vigor)
            for dia in days_to_materialize(window, hoje, existentes)
            if (em_vigor := planos.for_day(dia)) is not None
        ]
        lote: list[tuple[Any, ...]] = []
        if dias:
            planos_dos_dias = [settings for _, _, settings in dias]
            entradas = inputs
            # Entradas trazidas de fora podem ter sido carregadas só com o plano NOVO; se algum dia a gravar usa o plano
            # automático (ex.: ontem era automático e hoje é clássico) e o Intervals não foi lido, recarrega: senão
            # esse dia seria regravado no fallback.
            # O mesmo vale para o ADAPTATIVO: se algum dia a gravar é de um plano adaptativo (ou de outro `watch_calorie_mode`)
            # cujo histórico as entradas não trazem, recarrega; nenhum caminho grava meta adaptativa sem o histórico do modo certo.
            if (
                entradas is None
                or (any(p.is_auto_intervals for p in planos_dos_dias) and not entradas.intervals_loaded)
                or not entradas.covers(planos_dos_dias)
            ):
                entradas = load_user_inputs(cur, user_id, dias[0][0], dias[-1][0], hoje, planos_dos_dias)
            for dia, plano_id, settings in dias:
                if _history_failed_and_stored(entradas, settings, plano_id, existentes.get(dia)):
                    continue  # falha transitória do histórico: mantém a meta gravada (a estimativa inicial a degradaria)
                comp = compute_day(entradas, settings, dia, hoje)
                impressao = inputs_fingerprint(plano_id, comp)
                if not _unchanged(existentes.get(dia), plano_id, impressao):
                    lote.append(_upsert_params(user_id, plano_id, comp, impressao))
        if lote:
            # Data API: BatchExecuteStatement na MESMA transação (db_dataapi.Cursor.executemany).
            cur.executemany(_UPSERT_SQL, lote)
        linhas = read_targets(cur, user_id, window.date_from, window.date_to) if lote else list(existentes.values())
    return [target_row_public(linha, hoje) for linha in sorted(linhas, key=lambda r: _as_date(r.get("local_date")) or date.min)]


@dataclass(frozen=True)
class DiaryTargets:
    """Metas públicas dos dias lidos pelo diário e a decisão do motor por dia."""

    rows: dict[date, Optional[dict[str, Any]]]
    actions: dict[date, TargetAction]


def _backfill_frozen_days(cur: Any, user_id: int, days: Sequence[date], today: date) -> int:
    """Grava UMA vez a meta de cada dia congelado sem meta que tem plano em
    vigor (dados disponíveis hoje; marca do motor). Devolve quantas tentou."""
    planos = load_published_plans(cur, user_id)
    dias = [(dia, *em_vigor) for dia in sorted(days) if (em_vigor := planos.for_day(dia)) is not None]
    if not dias:
        return 0
    entradas = load_user_inputs(cur, user_id, dias[0][0], dias[-1][0], today, [settings for _, _, settings in dias])
    lote = []
    for dia, plano_id, settings in dias:
        if _history_failed(entradas, settings):
            continue  # gravaria PARA SEMPRE a estimativa inicial (`id = id`); a próxima leitura tenta de novo
        comp = compute_day(entradas, settings, dia, today)
        lote.append(_upsert_params(user_id, plano_id, comp, inputs_fingerprint(plano_id, comp), backfilled_on=today))
    if lote:
        cur.executemany(_INSERT_ONCE_SQL, lote)
    return len(lote)


def ensure_diary_targets(conn: Any, user_id: int, days: Sequence[date], *, today: date) -> DiaryTargets:
    """Metas dos dias que o diário lê, pela decisão do motor
    (`diary_target_action`): materializa os não congelados (`diary_window`),
    lê os congelados com meta e grava uma vez a meta do congelado sem meta.
    Um dia sem plano em vigor fica sem meta (`None`)."""
    dias = sorted(set(days))
    if not dias:
        return DiaryTargets(rows={}, actions={})
    with conn.cursor() as cur:
        existentes = {_as_date(r.get("local_date")) for r in read_targets(cur, user_id, dias[0], dias[-1])}
    acoes = {dia: diary_target_action(dia, today, dia in existentes) for dia in dias}
    janela = diary_window((dia for dia, acao in acoes.items() if acao == TargetAction.MATERIALIZE), today)
    if janela is not None:
        ensure_daily_targets(conn, user_id, janela, today=today)
    retroativos = [dia for dia, acao in acoes.items() if acao == TargetAction.BACKFILL]
    with conn.cursor() as cur:
        if retroativos:
            _backfill_frozen_days(cur, user_id, retroativos, today)
        linhas = read_targets(cur, user_id, dias[0], dias[-1])
    por_dia = {_as_date(linha.get("local_date")): target_row_public(linha, today) for linha in linhas}
    return DiaryTargets(rows={dia: por_dia.get(dia) for dia in dias}, actions=acoes)


def week_rows(rows: Iterable[dict[str, Any]], days: Sequence[date]) -> list[Optional[dict[str, Any]]]:
    """Linhas públicas alinhadas aos dias pedidos (None onde não há meta)."""
    por_dia = {linha["local_date"]: linha for linha in rows}
    return [por_dia.get(dia.isoformat()) for dia in days]


__all__ = [
    "DayComputation",
    "DiaryTargets",
    "PublishedPlans",
    "UPSERT_COLUMNS",
    "UserInputs",
    "compute_day",
    "day_public",
    "history_key_for_day",
    "ensure_daily_targets",
    "read_stored_tdee",
    "ensure_diary_targets",
    "inputs_fingerprint",
    "past_training_kcal",
    "last_target_date",
    "load_published_plans",
    "load_user_inputs",
    "read_targets",
    "settings_from_row",
    "target_row_public",
    "week_rows",
]
