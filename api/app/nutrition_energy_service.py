"""Gasto adaptativo em SOMBRA (Lote A da nutrição adaptativa): só LEITURA.

Lê o histórico do aluno-alvo (refeições, TMB gravada, pesagens, atividades do Intervals e a sincronização), monta a
entrada diária e roda `adaptive_energy.replay(...)` em memória, a cada leitura do dashboard. Nada é gravado, nenhuma
meta é alterada e nenhum modo de gasto muda: o bloco só informa o que o gasto adaptativo estimaria (`applied: false`).

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7): este módulo só faz SQL, alinhamento por dia e chamadas ao
dono (nenhuma regra, limiar, precedência ou constante de exibição mora aqui). As contas são de `domain/nutrition/adaptive_energy` (estimador), `weight_trend` (peso), `activity_energy`
(kcal ativa, duplicata, dia confirmado), `weekly_budget` (dia completo, kcal do dia), `bmr`/`body_composition` (TMB e
massa gorda) e `intervals_activity_kind` (classe da atividade). O horizonte é `adaptive_energy.REPLAY_HISTORY_DAYS`.

Decisões e limitações (documentadas, a confirmar com o usuário quando o modo virar de verdade, Lote C):
- TMB por dia: a precedência (gravada na meta do dia, `nutrition_daily_targets.bmr_kcal`, se válida; senão a do
  sujeito daquele dia pela fórmula padrão) e a "TMB mais recente" do desconto do repouso são do dono:
  `adaptive_energy.resolve_day_bmr` e `latest_bmr`. Aqui só se entregam os valores crus.
- Força, sem dupla contagem: no adaptativo a FONTE é o Intervals. Entram TODAS as atividades de `intervals_activities`
  (inclui `WeightTraining`, com ou sem vínculo `activity_intervals_link`), uma vez só; `activities` (o treino do app)
  NÃO é somada, senão a sessão vinculada contaria duas vezes. Sessão do app sem atividade no Intervals não entra (sem
  relógio, o motor não tem kcal medida para ela); é o custo da fonte única, a reavaliar no Lote E.
- Modo da kcal: `unknown` neste lote (vira parâmetro do plano no Lote C): tratada como bruta, com a marca de precisão
  reduzida. A duração do desconto é de `activity_energy.activity_duration_seconds` (decorrido, com o movimento de reserva).
- Dia confirmado (`activity_energy.day_confirmed`): `last_sync_at` = `MAX(synced_at)` das atividades, convertido para o
  fuso do usuário; `tracking_started_on` = a data da primeira atividade importada. A `synced_at` de TODA atividade da
  janela do job é regravada a cada execução bem-sucedida (`intervals_store`), então o relógio avança mesmo sem atividade
  NOVA. Fora da janela do último sync (`activity_energy.INTERVALS_SYNC_WINDOW_DAYS`, 30) não há prova de que o dia foi
  buscado: dia sem atividade só é descanso (0) se a sequência de dias sem atividade que o contém for de até
  `MAX_CONFIRMED_REST_STREAK_DAYS`; lacuna maior (Intervals desconectado antes) fica None, nunca zero. Limite: sem
  registro de execuções do sync (Lote C, migração), um descanso real longo fora da janela também fica None; e DENTRO da
  janela de 30 dias o teto de 7 não vale: relógio parado com o Intervals conectado vira zero confirmado por até 30 dias
  (limitação aberta, a fechar no Lote C com o registro de execuções). Sem migração neste lote não há registro de execução do job; é a fonte mais confiável que existe.
- Dia local: o do fuso do perfil (`user_today_for`); a atividade pertence ao dia local em que começou.

Falha na sombra NUNCA derruba o dashboard (`shadow_block`): vira `available: false` e um log sem dados sensíveis.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional, Sequence, Union

from .domain.nutrition import activity_energy as activity_mod
from .domain.nutrition import adaptive_energy as adaptive_mod
from .domain.nutrition import bmr as bmr_mod
from .domain.nutrition import dashboard as dash
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.activity_energy import RawActivity
from .domain.nutrition.adaptive_energy import DayInput
from .domain.nutrition.body_composition import resolved_fat_mass_kg
from .domain.nutrition.bmr import BmrFormula
from .domain.nutrition.plan import DEFAULT_WATCH_CALORIE_MODE, PlanSettings, history_bmr_formula
from .domain.nutrition.subject import SubjectSeries, resolve_subject
from .domain.nutrition.weekly_budget import complete_energy_kcal, intake_macros
from .intelligence.dashboard.time_windows import user_now
from .intervals_activity_kind import classify
from .intervals_store import SYNC_RUN_STATUS_OK
from .nutrition_adjustments import load_closures, load_corrections
from .nutrition_meals import MEALS, NUTRIENTS, day_totals
from .nutrition_schema import SYNC_RUNS_TABLE, has_adjustment_tables, has_sync_runs_table
from .timezones import normalize_timezone

logger = logging.getLogger(__name__)

# Modo da kcal do relógio sem plano adaptativo: o PADRÃO do dono (`plan.DEFAULT_WATCH_CALORIE_MODE`), não um literal daqui.
DEFAULT_CALORIE_MODE = DEFAULT_WATCH_CALORIE_MODE.value
DEFAULT_BMR_FORMULA_VALUE = history_bmr_formula(None).value  # sem plano em jogo: a fórmula padrão do dono

TARGETS_TABLE = "nutrition_daily_targets"
# Mesmo SQL do dashboard (`nutrition_dashboard.MEAL_TOTALS_SQL`, teste de paridade): só o que `stored_meal_totals`
# precisa, sem texto livre nem JSON de itens (teto de 1 MiB da RDS Data API).
MEAL_TOTALS_SQL = (
    f"SELECT id, local_date, {', '.join(NUTRIENTS)}, totals_incomplete_json, totals_nature, item_count "
    f"FROM {MEALS} WHERE user_id = %s AND local_date BETWEEN %s AND %s ORDER BY local_date, id"
)
# TMB gravada na meta do dia: duas colunas por dia, sem `inputs_json`.
TARGET_BMR_SQL = (
    f"SELECT local_date, bmr_kcal FROM {TARGETS_TABLE} "
    "WHERE user_id = %s AND local_date BETWEEN %s AND %s ORDER BY local_date"
)
# Prova de sincronização e início do acompanhamento: uma linha. Sem atividade, as duas colunas voltam NULL.
INTERVALS_SYNC_SQL = (
    "SELECT MAX(synced_at) AS last_sync_at, MIN(activity_date) AS first_activity_date "
    "FROM intervals_activities WHERE user_id = %s"
)
# Execuções `ok` do sync cuja janela alcança o horizonte (migração 107; 1 SELECT, só com a tabela). A regra de "dia
# confirmado" é do motor (`activity_energy.SyncRun`); aqui só se entregam as linhas, com `finished_at` em UTC.
SYNC_RUNS_SQL = (
    f"SELECT finished_at, window_start, window_end FROM {SYNC_RUNS_TABLE} "
    "WHERE user_id = %s AND status = %s AND window_end >= %s ORDER BY finished_at, id"
)
# Atividades do horizonte, SEM `raw_json` (teto de 1 MiB da Data API) e SEM o vínculo com o treino do app: no
# adaptativo a fonte da força é o Intervals, uma vez só (ver o cabeçalho). A classe vem de `intervals_activity_kind`.
SHADOW_ACTIVITIES_SQL = (
    "SELECT id, external_id, activity_date, start_time, name, sport_type, moving_time_seconds, elapsed_time_seconds, "
    "calories_kcal FROM intervals_activities "
    "WHERE user_id = %s AND activity_date BETWEEN %s AND %s ORDER BY activity_date, start_time, id"
)

# Fuso do usuário lido pelo CURSOR (quem carrega as entradas da meta só tem o cursor; `user_timezone` pede a conexão).
USER_TIMEZONE_SQL = "SELECT timezone FROM users WHERE id = %s"

# Códigos de `unavailable_reason` (os rótulos pt-BR são de `domain/nutrition/labels.py`).
UNAVAILABLE_NO_DATA = nutrition_labels.ADAPTIVE_UNAVAILABLE_NO_DATA
UNAVAILABLE_ERROR = nutrition_labels.ADAPTIVE_UNAVAILABLE_ERROR


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("T", " ")) if value else None
    except ValueError:
        return None


def _float(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ShadowRows:
    """O que o SQL devolveu para a sombra (linhas cruas; nenhuma conta)."""

    meals: tuple[dict[str, Any], ...] = ()
    target_bmr: dict[date, float] = field(default_factory=dict)
    activities: tuple[RawActivity, ...] = ()
    last_sync_at: Optional[datetime] = None  # relógio do banco (UTC), naive
    first_activity_day: Optional[date] = None
    # Execuções `ok` do sync (migração 107), `finished_at` em UTC naive; vazio sem a tabela (regra antiga do motor).
    sync_runs: tuple[activity_mod.SyncRun, ...] = ()
    # Lote G (migração 108): dias fechados à mão e correções de atividade; vazios sem as tabelas (regra anterior). O
    # significado de cada um é do motor (`adaptive_energy`, `activity_energy.ActivityCorrection`).
    closed_days: frozenset[date] = frozenset()
    corrections: tuple[activity_mod.ActivityCorrection, ...] = ()


def corrections_of(linhas: Sequence[dict[str, Any]]) -> tuple[activity_mod.ActivityCorrection, ...]:
    """Linhas de `activity_energy_corrections` -> `ActivityCorrection` do motor (que valida a ação e o tipo)."""
    return tuple(
        activity_mod.ActivityCorrection(
            activity_id=str(l["activity_id"]), action=l.get("action"), energy_kind=l.get("energy_kind")  # type: ignore[arg-type]
        )
        for l in linhas
    )


def raw_activity_of(row: dict[str, Any], calorie_mode: str = DEFAULT_CALORIE_MODE) -> Optional[RawActivity]:
    """Linha de `intervals_activities` -> `RawActivity`. O dia é `activity_date` (o local de início); a hora vem de
    `start_time`. Duração = tempo decorrido, com o tempo em movimento de reserva. `calorie_mode` é o parâmetro do
    plano (`energy_params.watch_calorie_mode`); sem plano adaptativo (sombra) vale `unknown`."""
    dia = _as_date(row.get("activity_date"))
    if dia is None:
        return None
    inicio = _as_datetime(row.get("start_time"))
    hora = inicio.time() if inicio is not None else datetime.min.time()
    duracao = activity_mod.activity_duration_seconds(row.get("elapsed_time_seconds"), row.get("moving_time_seconds"))
    return RawActivity(
        id=str(row.get("id")),
        external_id=None if row.get("external_id") is None else str(row.get("external_id")),
        start=datetime.combine(dia, hora),
        duration_seconds=duracao,
        kind=classify(row.get("sport_type"), row.get("name")),
        name=row.get("name") or row.get("sport_type"),
        calories_kcal=_float(row.get("calories_kcal")),
        calorie_mode=calorie_mode,
    )


def load_sync_runs(cur: Any, user_id: int, first_day: date) -> tuple[activity_mod.SyncRun, ...]:
    """Execuções `ok` do sync do Intervals que alcançam o horizonte (1 SELECT; sem a tabela da 107 devolve vazio SEM
    consultar mais nada). Falha na leitura degrada para a regra antiga do motor (log só com o tipo da exceção)."""
    try:
        if not has_sync_runs_table(cur):
            return ()
        cur.execute(SYNC_RUNS_SQL, (int(user_id), SYNC_RUN_STATUS_OK, first_day))
        linhas = cur.fetchall() or []
    except Exception as erro:  # noqa: BLE001 - o registro é um refinamento: sem ele vale a regra antiga
        logger.warning("nutrition.sync_runs_read_failed error=%s", type(erro).__name__)
        return ()
    execucoes = []
    for linha in linhas:
        fim, inicio_janela, fim_janela = (
            _as_datetime(linha.get("finished_at")), _as_date(linha.get("window_start")), _as_date(linha.get("window_end"))
        )
        if fim is not None and inicio_janela is not None and fim_janela is not None:
            execucoes.append(activity_mod.SyncRun(finished_at=fim, window_start=inicio_janela, window_end=fim_janela))
    return tuple(execucoes)


def local_sync_runs(runs: Sequence[activity_mod.SyncRun], timezone_name: str) -> tuple[activity_mod.SyncRun, ...]:
    """Execuções do sync com o término no fuso LOCAL do usuário (naive), do mesmo jeito que `last_sync_at`."""
    return tuple(
        activity_mod.SyncRun(
            finished_at=user_now(timezone_name, r.finished_at), window_start=r.window_start, window_end=r.window_end
        )
        for r in runs
    )


def _adjustment_tables_available(cur: Any) -> bool:
    try:
        return has_adjustment_tables(cur)
    except Exception as erro:  # noqa: BLE001 - refinamento: sem as tabelas vale a regra anterior
        logger.warning("nutrition.adjustment_tables_check_failed error=%s", type(erro).__name__)
        return False


def load_shadow_rows(
    cur: Any, user_id: int, first_day: date, today: date, calorie_mode: str = DEFAULT_CALORIE_MODE,
    sync_row: Optional[dict[str, Any]] = None,
) -> ShadowRows:
    """Consultas de leitura da sombra (todas escopadas pelo `user_id` do alvo). Em regime (tabelas 107 e 108 já detectadas
    e com cache), SELECTs por carga: refeições (1), TMB gravada (1), prova de sincronização (1, se quem chama não a
    entregou), execuções do sync (1), fechamentos (1), correções (1) e, só se há atividade importada, as atividades (1):
    6 sem Intervals e 7 com Intervals. Até a tabela aparecer, cada detecção custa +1 (2 consultas de schema), e a das
    duas tabelas da 108 é UMA por carga."""
    uid = int(user_id)
    cur.execute(MEAL_TOTALS_SQL, (uid, first_day, today))
    refeicoes = tuple(cur.fetchall() or [])
    cur.execute(TARGET_BMR_SQL, (uid, first_day, today))
    # Valor cru gravado; a validade e a precedência são do dono (`adaptive_energy.resolve_day_bmr`).
    tmbs = {
        dia: valor
        for dia, valor in ((_as_date(r.get("local_date")), _float(r.get("bmr_kcal"))) for r in cur.fetchall() or [])
        if dia is not None
    }
    if sync_row is None:  # quem já leu a prova de sincronização (a rota do dia) a entrega e poupa o SELECT
        cur.execute(INTERVALS_SYNC_SQL, (uid,))
        sync_row = cur.fetchone() or {}
    ultimo = _as_datetime(sync_row.get("last_sync_at"))
    primeiro = _as_date(sync_row.get("first_activity_date"))
    atividades: tuple[RawActivity, ...] = ()
    if ultimo is not None:
        cur.execute(SHADOW_ACTIVITIES_SQL, (uid, first_day, today))
        atividades = tuple(a for a in (raw_activity_of(r, calorie_mode) for r in cur.fetchall() or []) if a is not None)
    tabelas_108 = _adjustment_tables_available(cur)  # UMA detecção por carga, para fechamentos e correções
    return ShadowRows(
        meals=refeicoes, target_bmr=tmbs, activities=atividades, last_sync_at=ultimo, first_activity_day=primeiro,
        sync_runs=load_sync_runs(cur, uid, first_day),
        closed_days=frozenset(load_closures(cur, uid, first_day, today, tabelas_108)),
        corrections=corrections_of(load_corrections(cur, uid, tabelas_108)),
    )


def horizon_days(today: date) -> list[date]:
    """Os dias que a sombra reprocessa: `REPLAY_HISTORY_DAYS` dias corridos terminando HOJE (hoje é dia aberto)."""
    n = adaptive_mod.REPLAY_HISTORY_DAYS
    return [today - timedelta(days=n - 1 - i) for i in range(n)]


def _weights_by_day(series: SubjectSeries, days: Sequence[date]) -> dict[date, tuple[float, ...]]:
    grupos = dash.group_by_day(days, ((r.local_date, r.number) for r in series.weight if r.local_date and r.number))
    return {dia: tuple(valores) for dia, valores in zip(days, grupos)}


def energy_key(settings: PlanSettings) -> tuple[str, str]:
    """Chave do histórico de UM plano adaptativo: (`watch_calorie_mode`, fórmula da TMB do plano). Planos do mesmo modo e da
    mesma fórmula compartilham a leitura e o `replay`; a fórmula é a do dono (`plan.history_bmr_formula`)."""
    return settings.energy_params.watch_calorie_mode.value, history_bmr_formula(settings).value


@dataclass(frozen=True)
class EnergyHistory:
    """O histórico já processado de UM usuário: os estados do `replay` (um por dia do horizonte, em ordem) e as
    atividades por dia. É a mesma fonte da meta do plano adaptativo e do bloco do dashboard, para o cartão e a meta de
    hoje mostrarem o mesmo estado. `has_data` = há refeição ou peso no horizonte."""

    states: tuple[adaptive_mod.AdaptiveEnergyState, ...] = ()
    days: tuple[activity_mod.DayActivity, ...] = ()
    has_data: bool = False
    calorie_mode: str = DEFAULT_CALORIE_MODE
    bmr_formula: str = DEFAULT_BMR_FORMULA_VALUE

    @property
    def state_today(self) -> Optional[adaptive_mod.AdaptiveEnergyState]:
        return self.states[-1] if self.has_data and self.states else None

    def completeness_source_on(self, day: date) -> Optional[str]:
        """Origem da completude do dia (`adaptive_energy.COMPLETENESS_SOURCE_*`), lida do estado que fecha NESSE dia (o
        último da janela é o próprio dia); `None` se o dia não é completo ou está fora do horizonte. Só leitura."""
        for estado in self.states:
            if estado.day == day:
                return next((w.completeness_source for w in estado.window if w.day == day), None)
        return None

    def estimate_for_day(self, day: date, today: date) -> Optional[adaptive_mod.DayEnergyEstimate]:
        """Estimativa do dia pelo dono (`adaptive_energy.estimate_for_day`); `None` sem estado (a meta cai na estimativa inicial)."""
        return adaptive_mod.estimate_for_day(self.states, day, today)


def compute_history(
    rows: ShadowRows, series: SubjectSeries, today: date, timezone_name: str,
    calorie_mode: str = DEFAULT_CALORIE_MODE, bmr_formula: str = DEFAULT_BMR_FORMULA_VALUE,
) -> EnergyHistory:
    """Roda o estimador sobre o horizonte (`REPLAY_HISTORY_DAYS` dias terminando hoje). `bmr_formula` é a TMB do plano em
    jogo (`plan.history_bmr_formula`) quando o dia não tem TMB gravada: a mesma da estimativa inicial."""
    formula = BmrFormula(bmr_formula)
    dias = horizon_days(today)
    totais = [
        day_totals(grupo)
        for grupo in dash.group_by_day(dias, ((_as_date(r["local_date"]), r) for r in rows.meals))
    ]
    pesos = _weights_by_day(series, dias)
    sem_dado = not any(t is not None for t in totais) and not any(pesos.values())

    sync_local = user_now(timezone_name, rows.last_sync_at) if rows.last_sync_at is not None else None
    sujeitos = {dia: resolve_subject(series.readings_on(dia), dia) for dia in dias}
    tmb_do_dia = {
        dia: adaptive_mod.resolve_day_bmr(
            rows.target_bmr.get(dia),
            bmr_mod.bmr(sujeitos[dia].subject, formula, sujeitos[dia].ffm_age_days).kcal,
        )
        for dia in dias
    }
    tmb_recente = adaptive_mod.latest_bmr(tmb_do_dia[dia] for dia in dias)

    # Execuções registradas do sync (107): o horário de término vai para o fuso local, como `last_sync_at`.
    execucoes = local_sync_runs(rows.sync_runs, timezone_name)
    atividades = activity_mod.summarize_days(
        rows.activities, tmb_recente, dias, sync_local, rows.first_activity_day, runs=execucoes,
        corrections=rows.corrections,
    )
    por_dia = {item.day: item for item in atividades}

    entradas = []
    for dia, total in zip(dias, totais):
        resumo = por_dia[dia]
        fechado = dia in rows.closed_days
        entradas.append(
            DayInput(
                day=dia,
                day_state=adaptive_mod.shadow_day_state(
                    dia, today, has_record=total is not None, is_complete=intake_macros(total) is not None,
                    manually_closed=fechado,
                ),
                intake_kcal=complete_energy_kcal(total),
                bmr_kcal=tmb_do_dia[dia],
                weights_kg=pesos[dia],
                fat_mass_kg=resolved_fat_mass_kg(sujeitos[dia].subject),
                active_kcal=resumo.active_kcal,
                activity_confirmed=resumo.confirmed,
                manually_closed=fechado,
            )
        )
    estados = adaptive_mod.replay(entradas)
    return EnergyHistory(
        states=tuple(estados), days=atividades, has_data=not sem_dado, calorie_mode=calorie_mode, bmr_formula=bmr_formula
    )


def compute_shadow(
    rows: ShadowRows, series: SubjectSeries, today: date, timezone_name: str,
    calorie_mode: str = DEFAULT_CALORIE_MODE,
) -> tuple[Optional[adaptive_mod.AdaptiveEnergyState], tuple[activity_mod.DayActivity, ...]]:
    """Estado de HOJE (None sem nenhum dado de refeição ou peso) e as atividades por dia (horizonte inteiro)."""
    historico = compute_history(rows, series, today, timezone_name, calorie_mode)
    return historico.state_today, historico.days


def user_timezone_name(cur: Any, user_id: int) -> str:
    """Fuso do usuário (perfil), normalizado; nunca falha (`normalize_timezone` cai no padrão)."""
    cur.execute(USER_TIMEZONE_SQL, (int(user_id),))
    return normalize_timezone((cur.fetchone() or {}).get("timezone"))


def load_history(
    cur: Any, user_id: int, today: date, series: SubjectSeries,
    calorie_mode: str = DEFAULT_CALORIE_MODE, timezone_name: Optional[str] = None,
    bmr_formula: str = DEFAULT_BMR_FORMULA_VALUE, sync_row: Optional[dict[str, Any]] = None,
) -> EnergyHistory:
    """Lê o histórico (3 a 4 SELECTs, mais 1 do fuso se `timezone_name` não vier) e processa. Só leitura, escopado pelo
    `user_id`. `sync_row` = a prova de sincronização já lida por quem chama (poupa 1 SELECT)."""
    fuso = timezone_name if timezone_name is not None else user_timezone_name(cur, user_id)
    linhas = load_shadow_rows(cur, user_id, horizon_days(today)[0], today, calorie_mode, sync_row)
    return compute_history(linhas, series, today, fuso, calorie_mode, bmr_formula)


@dataclass(frozen=True)
class DayEnergyView:
    """O que o diário mostra do dia, tirado do MESMO histórico da meta: as atividades (`None` sem Intervals ou fora do
    horizonte) e a origem da completude (`None` se o dia não é completo ou não há histórico)."""

    activity: Optional[activity_mod.DayActivity] = None
    completeness_source: Optional[str] = None


def load_day_energy(
    cur: Any, user_id: int, day: date, today: date, series: Union[SubjectSeries, Callable[[], SubjectSeries]],
    calorie_mode: str = DEFAULT_CALORIE_MODE, timezone_name: Optional[str] = None,
    bmr_formula: str = DEFAULT_BMR_FORMULA_VALUE, need_history: bool = False,
) -> DayEnergyView:
    """O `DayActivity` de UM dia e a origem da completude, tirados do MESMO histórico que a meta e o dashboard usam
    (`load_history`, mesmo corte do horizonte, mesmas execuções do sync, mesmas correções e fechamentos, mesma TMB
    recente, mesma trava de descanso): o selo do diário é, por construção, o do histórico (teste de paridade). Vazio
    quando `day` está FORA do horizonte atual (`REPLAY_HISTORY_DAYS` dias terminando hoje, hoje incluído). Sem atividade
    importada (só 1 SELECT de prova) o histórico só é carregado se `need_history` (plano adaptativo: o fechamento do dia
    muda a completude mesmo sem Intervals). Custo com histórico: a carga completa (ver `load_history`) mais a prova."""
    if day not in horizon_days(today):
        return DayEnergyView()
    cur.execute(INTERVALS_SYNC_SQL, (int(user_id),))
    prova = cur.fetchone() or {}
    if _as_datetime(prova.get("last_sync_at")) is None and not need_history:
        return DayEnergyView()
    # `series` pode vir como função: o perfil e as medidas só são lidos quando há histórico a processar.
    serie = series() if callable(series) else series
    historico = load_history(cur, user_id, today, serie, calorie_mode, timezone_name, bmr_formula, sync_row=prova)
    atividade = next((item for item in historico.days if item.day == day), None)
    return DayEnergyView(
        activity=atividade if _as_datetime(prova.get("last_sync_at")) is not None else None,
        completeness_source=historico.completeness_source_on(day) if historico.has_data else None,
    )


def load_day_activity(
    cur: Any, user_id: int, day: date, today: date, series: Union[SubjectSeries, Callable[[], SubjectSeries]],
    calorie_mode: str = DEFAULT_CALORIE_MODE, timezone_name: Optional[str] = None,
    bmr_formula: str = DEFAULT_BMR_FORMULA_VALUE,
) -> Optional[activity_mod.DayActivity]:
    """Só o `DayActivity` do dia (ver `load_day_energy`); `None` sem Intervals importado ou fora do horizonte."""
    return load_day_energy(cur, user_id, day, today, series, calorie_mode, timezone_name, bmr_formula).activity


def adaptive_states_for_report(
    cur: Any, user_id: int, today: date, settings: Optional[PlanSettings],
    series: Union[SubjectSeries, Callable[[], SubjectSeries]],
) -> tuple[adaptive_mod.AdaptiveEnergyState, ...]:
    """Estados do `replay` (um por dia do horizonte) para a seção "7. Nutrição" do relatório Markdown, do MESMO histórico
    da meta (fechamentos e correções inclusive). Vazio quando o plano que vale na janela não é adaptativo (`settings` é o do
    dono: `PlanSettings.is_adaptive`) ou quando a leitura falha (o relatório nunca cai por isso; log só com o tipo da
    exceção). O resumo é do motor (`report_window.adaptive_report_summary`); aqui só se carrega e se entrega."""
    if settings is None or not settings.is_adaptive:
        return ()
    try:
        modo, formula = energy_key(settings)
        serie = series() if callable(series) else series
        historico = load_history(cur, user_id, today, serie, modo, bmr_formula=formula)
        return historico.states if historico.has_data else ()
    except Exception as erro:  # noqa: BLE001 - o relatório degrada para "sem o bloco adaptativo"
        logger.warning("nutrition.report_adaptive_failed error=%s", type(erro).__name__)
        return ()


def block_of(
    state: Optional[adaptive_mod.AdaptiveEnergyState],
    days: Sequence[activity_mod.DayActivity],
    unavailable_reason: Optional[str] = None,
    applied: bool = False,
) -> dict[str, Any]:
    """Bloco público `adaptive_energy` do dashboard (contrato com o frontend). `applied` = o plano vigente é adaptativo
    e este estado é o que governa a meta."""
    disponivel = state is not None
    recentes = list(days)[-activity_mod.SHADOW_DISPLAY_DAYS:][::-1]  # os últimos dias, o mais recente primeiro
    return {
        "available": disponivel,
        "applied": bool(applied),
        "algorithm_version": adaptive_mod.ALGORITHM_VERSION,
        "unavailable_reason": None if disponivel else (unavailable_reason or UNAVAILABLE_NO_DATA),
        "unavailable_reason_label": None
        if disponivel
        else nutrition_labels.adaptive_unavailable_label(unavailable_reason or UNAVAILABLE_NO_DATA),
        "state": state.to_public_dict() if state is not None else None,
        "days": [item.to_dict() for item in recentes],
    }


def unavailable_block(reason: str, applied: bool = False) -> dict[str, Any]:
    return block_of(None, (), reason, applied)


def _error_site(erro: BaseException) -> str:
    """Onde o erro nasceu (arquivo:linha do frame mais interno), SEM a mensagem nem dados do usuário."""
    ponta = erro.__traceback__
    while ponta is not None and ponta.tb_next is not None:
        ponta = ponta.tb_next
    if ponta is None:
        return "?"
    return f"{os.path.basename(ponta.tb_frame.f_code.co_filename)}:{ponta.tb_lineno}"


def shadow_block(
    cur: Any, user_id: int, today: date, series: SubjectSeries, timezone_name: Optional[str] = None,
    calorie_mode: str = DEFAULT_CALORIE_MODE, applied: bool = False,
    history: Optional[EnergyHistory] = None, bmr_formula: str = DEFAULT_BMR_FORMULA_VALUE,
) -> dict[str, Any]:
    """Bloco `adaptive_energy` do dashboard. Qualquer falha vira `available: false` (log só com o tipo da exceção: a
    mensagem de erro do banco pode trazer valores); o dashboard nunca cai por causa da sombra. Com `history` (já
    carregado pela meta do plano adaptativo) não há SELECT novo."""
    try:
        historico = history if history is not None else load_history(cur, user_id, today, series, calorie_mode, timezone_name, bmr_formula)
        estado = historico.state_today
        if estado is None:  # sem refeição nem peso no horizonte: nada a mostrar
            return unavailable_block(UNAVAILABLE_NO_DATA, applied)
        return block_of(estado, historico.days, applied=applied)
    except Exception as erro:  # noqa: BLE001 - isolamento deliberado: a sombra não derruba o dashboard
        logger.warning("nutrition.adaptive_energy_shadow_failed error=%s at=%s", type(erro).__name__, _error_site(erro))
        return unavailable_block(UNAVAILABLE_ERROR, applied)
