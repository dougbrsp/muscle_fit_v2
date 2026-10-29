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
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from .body_measurements_schema import has_body_measurements_table
from .domain.nutrition import ENGINE_VERSION, compute_daily_target
from .domain.nutrition import labels as nutrition_labels
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
from .domain.nutrition.plan import PlanSettings, PublishedPlanRef, build_plan_settings, plan_in_force
from .domain.nutrition.subject import NAVY_SITE_FIELDS, Reading, ResolvedSubject, SubjectSeries, resolve_subject
from .intelligence.dashboard.time_windows import user_today_for

PLANS_TABLE = "nutrition_plans"
TARGETS_TABLE = "nutrition_daily_targets"
PUBLISHED = "published"

# Colunas do plano que viram `PlanSettings` (nomes da 078).
PLAN_SETTINGS_COLUMNS = (
    "strategy, advanced, bmr_formula, activity_level, training_kcal_mode, goal_body_fat_percent, goal_weight_kg, "
    "weekly_rate_share, surplus_share, protein_g_per_kg, fat_kcal_share, carb_min_g_per_kg, meals_per_day"
)

# `FOR SHARE`: dentro da transação, a leitura dos planos espera uma publicação em
# andamento (que tem os planos travados para escrita) e trava a publicação
# seguinte até este cálculo terminar.
_PUBLISHED_PLANS_SQL = (
    f"SELECT id, effective_from, published_at, {PLAN_SETTINGS_COLUMNS} FROM {PLANS_TABLE} "
    "WHERE user_id = %s AND status = %s AND effective_from IS NOT NULL FOR SHARE"
)

_TARGET_COLUMNS = (
    "local_date, plan_id, day_kind, is_training_day, training_known, phase_kind, kcal_target, protein_g, carbs_g, "
    "fat_g, fiber_g, water_ml, bmr_kcal, bmr_formula, tdee_kcal, training_kcal, training_kcal_nature, "
    "limitations_json, inputs_hash, engine_version, computed_at"
)
_TARGETS_SQL = (
    f"SELECT {_TARGET_COLUMNS} FROM {TARGETS_TABLE} "
    "WHERE user_id = %s AND local_date BETWEEN %s AND %s ORDER BY local_date"
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
    cur.execute(_PUBLISHED_PLANS_SQL, (int(user_id), PUBLISHED))
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


def _decimal_free(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in row.items()}


def _series(rows: Iterable[dict[str, Any]], column: str) -> tuple[Reading, ...]:
    return tuple(
        Reading(_float(row.get(column)), _as_date(row.get("local_date")))
        for row in rows
        if row.get(column) is not None
    )


def load_user_inputs(cur: Any, user_id: int, date_from: date, date_to: date, today: date) -> UserInputs:
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
    return UserInputs(series=series, sessions=sessoes)


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
    sujeito = resolve_subject(inputs.series.readings_on(day), day, settings.activity_level)
    contexto = day_context_for(day, today, inputs.sessions.get(day, ()))
    meta = compute_daily_target(
        sujeito.subject, settings.plan_params(sujeito.ffm_age_days), contexto, scale_bmr=sujeito.scale_bmr_kcal
    )
    return DayComputation(day=day, target=meta, subject=sujeito, context=contexto, settings=settings)


def inputs_fingerprint(plan_id: int, comp: DayComputation) -> str:
    """Hash das ENTRADAS do cálculo de um dia (só para comparar igualdade e não
    regravar o que não mudou; nenhuma regra mora aqui)."""
    entradas = {
        "plan_id": int(plan_id),
        "engine_version": ENGINE_VERSION,
        "subject": comp.subject.to_dict(),
        "plan": comp.settings.to_dict(),
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
            entradas = inputs if inputs is not None else load_user_inputs(cur, user_id, dias[0][0], dias[-1][0], hoje)
            for dia, plano_id, settings in dias:
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
    entradas = load_user_inputs(cur, user_id, dias[0][0], dias[-1][0], today)
    lote = []
    for dia, plano_id, settings in dias:
        comp = compute_day(entradas, settings, dia, today)
        lote.append(_upsert_params(user_id, plano_id, comp, inputs_fingerprint(plano_id, comp), backfilled_on=today))
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
    "ensure_daily_targets",
    "ensure_diary_targets",
    "inputs_fingerprint",
    "last_target_date",
    "load_published_plans",
    "load_user_inputs",
    "read_targets",
    "settings_from_row",
    "target_row_public",
    "week_rows",
]
