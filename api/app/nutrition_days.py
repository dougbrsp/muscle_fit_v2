"""Dia e semana do diário de nutrição (plano de nutrição §8.1, §10 e §13, Lote
L4). Vive na api-core (`{proxy+}`).

Rotas (prefixo `/nutrition`):
- `GET /days/{date}?user_id=`   meta do dia (materializada; dia que congelou sem
                                meta é calculado e gravado UMA vez, decisão do
                                motor), refeições, total do dia, água/fome, kcal
                                do treino (informada × estimada, com a proveniência)
                                e o balanço meta × ingestão
- `PUT /days/{date}?user_id=`   `NutritionDayIn`: água, fome e anotação (400 com o
                                código do motor)
- `GET /weeks?start=&user_id=`  a semana de `start` (segunda a domingo): metas,
                                total de cada dia, orçamento, banco e aderência

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7 e §52): meta e
congelamento de `daily_target` (`diary_target_action`), totais de `meal_math`,
semana, balanço, banco e aderência de `weekly_budget`, kcal do treino de
`energy_expenditure.compare_training_kcal` (natureza) e de
`domain/calories_source` (proveniência manual × Intervals), sujeito do dia de
`subject`, faixas do dia de `diary`. "Hoje" é o dia civil do ALUNO
(`time_windows.user_today_for`). Acesso: `nutrition_access` (D-L). Tabelas
ausentes: 503 citando a migração.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .db import get_conn
from .db_helpers import connect_or_503
from .domain.calories_source import calories_source_label, resolve_calories_source
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.daily_target import day_context_for, diary_target_notice
from .domain.nutrition.energy_expenditure import (
    NO_EXTRA_ACTIVITY_KCAL,
    TdeeBasis,
    TdeeMode,
    activity_total_kcal,
    compare_sessions_kcal,
    met_for_session,
    tdee_breakdown_from_stored,
)
from .domain.nutrition.subject import resolve_subject
from .domain.nutrition.weekly_budget import complete_energy_kcal, day_balance, target_macros, week_days, week_report
from .intelligence.dashboard.time_windows import user_today_for
from .nutrition_access import fetch_target_user, resolve_target_user
from . import nutrition_adjustments as adjustments
from .nutrition_energy_service import DEFAULT_BMR_FORMULA_VALUE, DEFAULT_CALORIE_MODE, horizon_days, load_day_energy
from .nutrition_meals import day_totals, meals_public, read_meal_rows, totals_public
from .nutrition_schema import require_adjustment_tables, require_diary_tables, require_nutrition_tables
from .nutrition_targets_service import (
    DONE_SESSION_STATUS,
    history_key_for_day,
    ensure_diary_targets,
    UserInputs,
    load_user_inputs,
    read_stored_tdee,
)
from .request_models import ActivityCorrectionIn, NutritionDayIn
from .security import Identity, get_current_identity

router = APIRouter(prefix="/nutrition", tags=["nutrition"])
logger = logging.getLogger(__name__)

DAYS = "nutrition_days"
# Chave interna de `energy_detail`: a origem da completude sobe para o topo do dia (`completeness_source`).
COMPLETENESS_KEY = "completeness_source"
DAY_LOG_SQL = f"SELECT local_date, notes, updated_by, updated_at FROM {DAYS} WHERE user_id = %s AND local_date = %s"
UPSERT_DAY_LOG_SQL = (
    f"INSERT INTO {DAYS} (user_id, local_date, notes, updated_by) VALUES (%s, %s, %s, %s) "
    "ON DUPLICATE KEY UPDATE notes = VALUES(notes), updated_by = VALUES(updated_by)"
)
# Sessões CONCLUÍDAS do dia (mesma condição da materialização, `DONE_SESSION_STATUS`)
# com a kcal do link do Intervals (a proveniência é resolvida por
# `domain/calories_source`, nunca aqui).
SESSIONS_OF_DAY_SQL = (
    "SELECT a.id, a.name, a.calories_kcal, a.calories_source, a.duration_seconds, "
    "ail.calories_kcal AS intervals_calories_kcal "
    "FROM activities a LEFT JOIN activity_intervals_link ail ON ail.activity_id = a.id "
    f"WHERE a.user_id = %s AND a.session_status = '{DONE_SESSION_STATUS}' AND a.local_date = %s "
    "ORDER BY a.started_at, a.id"
)


def _connect():
    return connect_or_503(get_conn)


def _today_for(conn: Any, user_id: int) -> date:
    """Dia civil do ALUNO (fuso do perfil). Isolado para os testes congelarem."""
    return user_today_for(conn, user_id)


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value) if isinstance(value, (Decimal, int, float)) else float(str(value))


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _require_tables(cur: Any) -> None:
    require_nutrition_tables(cur)
    require_diary_tables(cur)


def target_of(row: Optional[dict[str, Any]]):
    if row is None:
        return None
    return target_macros(row.get("kcal"), row.get("protein_g"), row.get("carbs_g"), row.get("fat_g"), row.get("fiber_g"))


def day_log_public(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    return {
        "notes": row.get("notes"),
        "updated_at": _iso(row.get("updated_at")),
    }


def _read_day_log(cur: Any, user_id: int, day: date) -> Optional[dict[str, Any]]:
    cur.execute(DAY_LOG_SQL, (int(user_id), day))
    return cur.fetchone()


def _session_public(row: dict[str, Any]) -> dict[str, Any]:
    origem = resolve_calories_source(
        calories_kcal=row.get("calories_kcal"),
        stored_source=row.get("calories_source"),
        intervals_calories_kcal=row.get("intervals_calories_kcal"),
    )
    return {
        "id": int(row["id"]),
        "name": row.get("name"),
        "calories_kcal": _num(row.get("calories_kcal")),
        "calories_source": origem,
        "calories_source_label": calories_source_label(origem),
        "duration_seconds": int(row["duration_seconds"]) if row.get("duration_seconds") is not None else None,
    }


def _training(
    cur: Any, user_id: int, day: date, today: date, extra: Optional[dict[str, Any]] = None, basis: Optional[str] = None,
    entradas: Optional[UserInputs] = None, bmr_kcal: Optional[float] = None,
) -> dict[str, Any]:
    """Linha "informado × estimado": sessões concluídas (com a proveniência do
    dono), contexto do dia e a comparação de naturezas do motor."""
    cur.execute(SESSIONS_OF_DAY_SQL, (int(user_id), day))
    sessoes = list(cur.fetchall() or [])
    entradas = entradas if entradas is not None else load_user_inputs(cur, user_id, day, day, today)
    contexto = day_context_for(day, today, entradas.sessions.get(day, ()))
    comparacao = None
    if contexto.is_training_day:
        sujeito = resolve_subject(entradas.series.readings_on(day), day)
        # A MESMA função do motor que a meta do dia usa (soma por sessão; dia misto = parcialmente estimado).
        comparacao = compare_sessions_kcal(
            contexto.training_sessions, sujeito.subject.weight_kg, met_for_session(contexto.session_kind), bmr_kcal
        )
    # Treino de força (o usado pelo motor) + cardio/caminhada do Intervals = gasto de atividade do dia, tudo pelo motor.
    # `extra` é o que o motor gravou na meta do dia (só o plano automático sincronizado tem); os modos clássicos não
    # mostram nada além do treino.
    extra = extra or NO_EXTRA_ACTIVITY_KCAL.to_dict()
    treino_kcal = comparacao.used.kcal if comparacao is not None else None
    return {
        "is_training_day": contexto.is_training_day,
        "training_known": contexto.training_known,
        "sessions": [_session_public(sessao) for sessao in sessoes],
        "comparison": comparacao.to_dict() if comparacao is not None else None,
        # Como o gasto do dia foi montado (o motor gravou): no fallback do automático a tela avisa que treino e cardio
        # NÃO foram somados.
        # Rótulos prontos do motor para o bloco de atividades (a tela não escreve texto de regra).
        "labels": nutrition_labels.activity_block_labels(),
        "tdee_basis": basis or TdeeBasis.CLASSIC.value,
        "tdee_basis_note": nutrition_labels.TDEE_BASIS_NOTES.get(basis or ""),
        "extra_activity": extra,
        "activity_kcal": {
            "training_kcal": treino_kcal,
            "extra_kcal": extra["kcal"],
            "total_kcal": activity_total_kcal(treino_kcal if contexto.is_training_day else 0.0, extra["kcal"]),
        },
    }


_NOT_READ: Any = object()  # `energy_detail`: o `tdee` gravado ainda não foi lido por quem chama


def energy_detail(
    cur: Any, user_id: int, day: date, today: date, meta: Optional[dict[str, Any]], entradas: Optional[UserInputs] = None,
    gravado_lido: Any = _NOT_READ,
) -> dict[str, Any]:
    """Explicação do gasto do dia (só leitura; nada é recalculado nem gravado). `breakdown` = composição do gasto relida
    do que o motor GRAVOU na meta (`energy_expenditure.tdee_breakdown_from_stored` + rótulos do dono); `activities` = o
    `DayActivity` do dia tirado do MESMO histórico que a meta usa (`nutrition_energy_service.load_day_activity`: mesmas
    execuções do sync, TMB recente, trava de descanso e modo da kcal do plano que vale no dia), com o selo
    confirmado/pendente; `energy` = o retrato do dia já gravado (só plano adaptativo). Sem meta no dia: tudo `null`.
    Dia fora do horizonte do histórico (`adaptive_energy.REPLAY_HISTORY_DAYS` dias terminando hoje) ou sem Intervals
    importado: `activities` é `null`. Cada parte falha sozinha (vira `null`, log só com o tipo da exceção). Custo: 1 SELECT
    da composição; sem Intervals +1 (plano) +1 (prova de sincronização); com Intervals, além disso, a carga do histórico
    SEM perfil/medidas (reaproveita `entradas`, já lidas pela rota): refeições, TMB gravada, atividades do horizonte,
    execuções e fuso."""
    detalhe: dict[str, Any] = {"breakdown": None, "activities": None, "energy": None, COMPLETENESS_KEY: None}
    if meta is None:
        return detalhe
    detalhe["energy"] = meta.get("energy")
    try:
        gravado = read_stored_tdee(cur, user_id, day) if gravado_lido is _NOT_READ else gravado_lido
        base = TdeeBasis(meta.get("tdee_basis") or TdeeBasis.CLASSIC.value)
        extra = (meta.get("extra_activity") or {}).get("kcal")
        itens_extra = (meta.get("extra_activity") or {}).get("items")
        if gravado is not None:
            detalhe["breakdown"] = nutrition_labels.tdee_breakdown_public(tdee_breakdown_from_stored(gravado, extra, base, itens_extra))
    except Exception as erro:  # noqa: BLE001 - seção aditiva: o diário não cai por causa dela
        logger.warning("nutrition.day_breakdown_failed error=%s", type(erro).__name__)
    try:
        # O selo vem do MESMO histórico da meta (`nutrition_energy_service.load_history`): modo da kcal e fórmula da TMB do
        # plano que vale no dia (a chave do histórico); sem plano, os padrões do dono. Fora do horizonte: `null`.
        modo, formula = history_key_for_day(cur, user_id, day) or (DEFAULT_CALORIE_MODE, DEFAULT_BMR_FORMULA_VALUE)
        # Plano adaptativo (a meta traz o retrato `energy`): o histórico é lido mesmo sem Intervals, porque o fechamento do
        # dia muda a completude (origem `completeness_source`).
        visao = load_day_energy(
            cur, user_id, day, today,
            # Série ATÉ HOJE (a rota a carrega assim): a mesma do histórico da meta/dashboard, de que a TMB recente do
            # desconto do repouso depende.
            entradas.series if entradas is not None else (lambda: load_user_inputs(cur, user_id, today, today, today).series),
            modo, None, formula, need_history=meta.get("energy") is not None,
        )
        detalhe["activities"] = visao.activity.to_dict() if visao.activity is not None else None
        if meta.get("energy") is not None:  # só plano adaptativo: no legado a origem da completude não existe
            detalhe[COMPLETENESS_KEY] = visao.completeness_source
    except Exception as erro:  # noqa: BLE001
        logger.warning("nutrition.day_activities_failed error=%s", type(erro).__name__)
    return detalhe


def balance_public(balanco) -> dict[str, Any]:
    corpo = balanco.to_dict()
    corpo["limitations"] = nutrition_labels.limitation_items(balanco.limitations)
    return corpo


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


def completeness_label(code: Optional[str]) -> Optional[str]:
    """Rótulo pt-BR da origem da completude (dono: `domain/nutrition/labels.py`); `None` sem origem."""
    return nutrition_labels.COMPLETENESS_SOURCE_LABELS.get(code) if code else None


def build_day(conn: Any, aluno: int, local_date: date) -> dict[str, Any]:
    """O dia do diário (corpo de `GET /days/{date}`), reaproveitado pelas rotas de fechamento e correção, que devolvem o
    dia JÁ atualizado pelo mesmo caminho de leitura e recomputação (`ensure_diary_targets` + histórico do motor)."""
    with conn.cursor() as cur:
        usuario = fetch_target_user(cur, aluno)
    hoje = _today_for(conn, aluno)
    metas = ensure_diary_targets(conn, aluno, [local_date], today=hoje)
    meta = metas.rows.get(local_date)
    acao = metas.actions[local_date]
    with conn.cursor() as cur:
        linhas = read_meal_rows(cur, aluno, local_date, local_date)
        refeicoes = meals_public(cur, linhas)
        diario = _read_day_log(cur, aluno, local_date)
        # UMA carga de perfil, medidas e sessões, usada pelo treino e pela explicação do gasto.
        # Medidas e série até HOJE (ou até o dia, se futuro): o histórico da explicação usa a mesma série da meta; o
        # treino do dia só consulta o próprio dia (`readings_on`, `sessions.get(dia)`).
        entradas = load_user_inputs(cur, aluno, local_date, max(local_date, hoje), hoje)
        # O desconto do repouso só vale onde o gasto o aplica (explícito/automático sincronizado): o modo vem do `tdee`
        # gravado; no embutido, no fallback do automático e no adaptativo o treino segue bruto, como sempre.
        gravado: Any = _NOT_READ
        desconta = False
        if meta:
            try:
                gravado = read_stored_tdee(cur, aluno, local_date)
                desconta = (gravado or {}).get("mode") == TdeeMode.EXPLICIT_TRAINING.value
            except Exception as erro:  # noqa: BLE001 - sem o modo gravado o treino segue bruto; a seção de explicação trata a falha
                gravado = _NOT_READ
                logger.warning("nutrition.day_stored_tdee_failed error=%s", type(erro).__name__)
        treino = _training(
            cur, aluno, local_date, hoje, (meta or {}).get("extra_activity"), (meta or {}).get("tdee_basis"), entradas,
            (meta or {}).get("bmr_kcal") if desconta else None,
        )
        detalhe_gasto = energy_detail(cur, aluno, local_date, hoje, meta, entradas, gravado)
        fechamento = adjustments.read_closure(cur, aluno, local_date)
    origem = detalhe_gasto.pop(COMPLETENESS_KEY, None)
    totais = day_totals(linhas)
    balanco = day_balance(target_of(meta), totais, meta.get("tdee_kcal") if meta else None)
    return {
        "user": usuario,
        "local_date": local_date.isoformat(),
        "today": hoje.isoformat(),
        "target": meta,
        "target_action": acao.value,
        "target_notices": nutrition_labels.limitation_items(diary_target_notice(acao)),
        "meals": refeicoes,
        "totals": totals_public(totais),
        "balance": balance_public(balanco),
        "day_log": day_log_public(diario),
        "training": treino,
        "energy_detail": detalhe_gasto,
        # Lote G: fechamento manual do dia e origem da completude (`intake_rule` | `manual_close` | null; só plano adaptativo).
        "closure": fechamento,
        "completeness_source": origem,
        "completeness_source_label": completeness_label(origem),
    }


@router.get("/days/{local_date}")
def get_day(
    local_date: date,
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    aluno = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
        return build_day(conn, aluno, local_date)


@router.put("/days/{local_date}/close")
def close_day(
    local_date: date,
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """"Fechei o dia": o dia conta como completo para o gasto adaptativo (o motor decide; reversível). Dia futuro: 422."""
    return _set_closure(local_date, user_id, identity, closed=True)


@router.delete("/days/{local_date}/close")
def reopen_day(
    local_date: date,
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """Reabre o dia (volta à regra da ingestão). Idempotente."""
    return _set_closure(local_date, user_id, identity, closed=False)


def _set_closure(local_date: date, user_id: Optional[int], identity: Identity, *, closed: bool) -> dict[str, Any]:
    aluno = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            require_adjustment_tables(cur)
            fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        if closed and local_date > hoje:  # só reabrir é permitido em dia futuro (no-op); fechar o que não aconteceu não
            raise _error(422, "future_day_cannot_be_closed", "Só dias até hoje podem ser fechados.")
        if closed and local_date < horizon_days(hoje)[0]:  # a janela do motor (`REPLAY_HISTORY_DAYS`): antes dela não vale
            raise _error(422, "day_outside_window", "Esse dia está fora da janela em que o fechamento vale.")
        with conn.cursor() as cur:
            (adjustments.close_day if closed else adjustments.reopen_day)(cur, aluno, local_date)
        return build_day(conn, aluno, local_date)


@router.put("/activities/{activity_id}/correction")
def put_activity_correction(
    activity_id: str,
    payload: ActivityCorrectionIn,
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """Corrige uma atividade importada do Intervals (excluir ou reclassificar); o motor aplica a correção antes de normalizar."""
    return _set_correction(activity_id, user_id, identity, payload)


@router.delete("/activities/{activity_id}/correction")
def delete_activity_correction(
    activity_id: str,
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """Desfaz a correção (idempotente). Só atividade do próprio aluno (404 caso contrário)."""
    return _set_correction(activity_id, user_id, identity, None)


def _set_correction(
    activity_id: str, user_id: Optional[int], identity: Identity, payload: Optional[ActivityCorrectionIn]
) -> dict[str, Any]:
    aluno = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            require_adjustment_tables(cur)
            fetch_target_user(cur, aluno)
            dia = adjustments.activity_day(cur, aluno, activity_id)
            if dia is None:
                raise _error(404, "activity_not_found", "Atividade não encontrada.")
            chave = adjustments.canonical_activity_id(activity_id)  # `007` e `7` são a mesma atividade
            if payload is None:
                adjustments.clear_correction(cur, aluno, chave)
            else:
                adjustments.set_correction(cur, aluno, chave, payload.action, payload.energy_kind)
        return build_day(conn, aluno, dia)


@router.put("/days/{local_date}")
def put_day(
    local_date: date,
    payload: NutritionDayIn,
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    aluno = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_diary_tables(cur)
            fetch_target_user(cur, aluno)
            cur.execute(
                UPSERT_DAY_LOG_SQL,
                (aluno, local_date, payload.notes, int(identity.user_id)),
            )
            linha = _read_day_log(cur, aluno, local_date)
    return {"user_id": aluno, "local_date": local_date.isoformat(), "day_log": day_log_public(linha)}


@router.get("/weeks")
def get_week(
    start: Optional[date] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """A semana (segunda a domingo, `weekly_budget.week_days`) que contém
    `start` (padrão: hoje do aluno)."""
    aluno = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            usuario = fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        dias = week_days(start if start is not None else hoje)
        metas = ensure_diary_targets(conn, aluno, dias, today=hoje)
        with conn.cursor() as cur:
            linhas = read_meal_rows(cur, aluno, dias[0], dias[-1])
    por_dia: dict[date, list[dict[str, Any]]] = {}
    for linha in linhas:
        dia = linha["local_date"] if isinstance(linha["local_date"], date) else date.fromisoformat(str(linha["local_date"])[:10])
        por_dia.setdefault(dia, []).append(linha)
    totais = [day_totals(por_dia.get(dia, [])) for dia in dias]
    relatorio = week_report(dias, [target_of(metas.rows.get(dia)) for dia in dias], totais)
    registrados = set(relatorio.logged_days)
    incompletos = set(relatorio.incomplete_days)
    corpo = relatorio.to_dict()
    corpo["limitations"] = nutrition_labels.limitation_items(relatorio.limitations)
    return {
        "user": usuario,
        "today": hoje.isoformat(),
        "start": dias[0].isoformat(),
        "end": dias[-1].isoformat(),
        "days": [
            {
                "local_date": dia.isoformat(),
                "target": metas.rows.get(dia),
                "target_action": metas.actions[dia].value,
                "totals": totals_public(total),
                # kcal só de total completo (`None` se incompleto ou sem refeição): a tela não decide.
                "intake_kcal": complete_energy_kcal(total),
                "logged": dia in registrados,
                "intake_incomplete": dia in incompletos,
            }
            for dia, total in zip(dias, totais)
        ],
        "report": corpo,
    }


__all__ = ["day_log_public", "router"]
