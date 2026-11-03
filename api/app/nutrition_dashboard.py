"""Dashboard de nutrição (reformulação N5, L7 do plano de nutrição). Vive na
api-core (`{proxy+}`), junto do diário.

Rota (prefixo `/nutrition`):
- `GET /dashboard?days=&user_id=`  janela de `days` dias corridos que termina hoje
                                   (28 por padrão, 7 a 90): aderência por dia e
                                   por semana, kcal e macros × meta, peso/%G/massa
                                   muscular, projeção até o alvo do plano e o TDEE
                                   adaptativo (só sugestão, `applied` sempre falso)

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §82): a agregação é de
`domain/nutrition/dashboard` (compõe `weekly_budget`, `adaptive_tdee` e
`phases.project_goal`); aqui só se leem as linhas gravadas (metas materializadas,
totais gravados das refeições, composição corporal) e se monta a resposta. Não há
soma de nutrientes nem regra de nutrição neste arquivo. A rota não materializa
metas de dias passados (só a de hoje, como o diário): dia sem meta gravada fica
fora da aderência e é avisado. Acesso: `nutrition_access` (D-L). Tabelas
ausentes: 503 citando a migração.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import dashboard as dash
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.body_composition import resolved_fat_mass_kg
from .domain.nutrition.phases import project_goal
from .domain.nutrition.plan import PlanSettings, plan_in_force
from .domain.nutrition.subject import resolve_subject
from .domain.nutrition.weekly_budget import target_macros
from .intelligence.dashboard.time_windows import user_today_for
from .nutrition_access import fetch_target_user, resolve_target_user
from .nutrition_meals import day_totals, read_meal_rows
from .nutrition_schema import require_diary_tables, require_nutrition_tables
from .nutrition_targets_service import (
    ensure_diary_targets,
    load_published_plans,
    load_user_inputs,
    read_targets,
    target_row_public,
)
from .security import Identity, get_current_identity

router = APIRouter(prefix="/nutrition", tags=["nutrition"])

# Uma linha por dia e por local (a última do dia vale). `muscle_mass_kg` é da
# bioimpedância (ESTIMATED); o peso e o %G seguem a mesma tabela do motor.
BODY_WINDOW_SQL = (
    "SELECT local_date, weight_kg, body_fat_percent, muscle_mass_kg FROM body_composition "
    "WHERE user_id = %s AND local_date BETWEEN %s AND %s "
    "AND (weight_kg IS NOT NULL OR body_fat_percent IS NOT NULL OR muscle_mass_kg IS NOT NULL) "
    "ORDER BY local_date, id"
)
# Ponto de partida do caminho até o alvo: a primeira medida a partir do início do plano.
FIRST_WEIGHT_SQL = (
    "SELECT weight_kg FROM body_composition WHERE user_id = %s AND local_date >= %s AND weight_kg IS NOT NULL "
    "ORDER BY local_date, id LIMIT 1"
)
FIRST_BODY_FAT_SQL = (
    "SELECT body_fat_percent FROM body_composition WHERE user_id = %s AND local_date >= %s "
    "AND body_fat_percent IS NOT NULL ORDER BY local_date, id LIMIT 1"
)


def _connect():
    return connect_or_503(get_conn)


def _today_for(conn: Any, user_id: int) -> date:
    """Dia civil do ALUNO (fuso do perfil). Isolado para os testes congelarem."""
    return user_today_for(conn, user_id)


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10]) if value else None


def _num(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _first_value(cur: Any, sql: str, user_id: int, since: date, column: str) -> Optional[float]:
    cur.execute(sql, (int(user_id), since))
    linha = cur.fetchone()
    return _num(linha.get(column)) if linha else None


def _plan_public(plan_id: int, settings: PlanSettings, effective_from: date) -> dict[str, Any]:
    return {
        "id": plan_id,
        "strategy": settings.strategy.value,
        "effective_from": effective_from.isoformat(),
        "goal_weight_kg": settings.goal_weight_kg,
        "goal_body_fat_percent": settings.goal_body_fat_percent,
    }


@router.get("/dashboard")
def get_dashboard(
    days: Optional[int] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    aluno = resolve_target_user(identity, user_id)
    janela = dash.normalize_window_days(days)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            require_diary_tables(cur)
            usuario = fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        dias = dash.window_dates(hoje, janela)
        inicio = dias[0]
        ensure_diary_targets(conn, aluno, [hoje], today=hoje)
        with conn.cursor() as cur:
            metas_linhas = [target_row_public(linha, hoje) for linha in read_targets(cur, aluno, inicio, hoje)]
            refeicoes = read_meal_rows(cur, aluno, inicio, hoje)
            cur.execute(BODY_WINDOW_SQL, (aluno, inicio, hoje))
            corpo_linhas = list(cur.fetchall() or [])
            planos = load_published_plans(cur, aluno)
            em_vigor = planos.for_day(hoje)
            entradas = load_user_inputs(cur, aluno, inicio, hoje, hoje)
            ref = plan_in_force(planos.refs, hoje)
            partida_peso = partida_gordura = None
            if ref is not None:
                partida_peso = _first_value(cur, FIRST_WEIGHT_SQL, aluno, ref.effective_from, "weight_kg")
                partida_gordura = _first_value(cur, FIRST_BODY_FAT_SQL, aluno, ref.effective_from, "body_fat_percent")

    # Metas e totais gravados, alinhados aos dias da janela.
    metas_por_dia = {linha["local_date"]: linha for linha in metas_linhas}
    metas = [metas_por_dia.get(dia.isoformat()) for dia in dias]
    alvos = [
        None
        if meta is None
        else target_macros(meta["kcal"], meta["protein_g"], meta["carbs_g"], meta["fat_g"], meta["fiber_g"])
        for meta in metas
    ]
    refeicoes_por_dia: dict[date, list[dict[str, Any]]] = {}
    for linha in refeicoes:
        refeicoes_por_dia.setdefault(_as_date(linha["local_date"]), []).append(linha)  # type: ignore[arg-type]
    totais = [day_totals(refeicoes_por_dia.get(dia, [])) for dia in dias]

    linhas_dia = dash.day_rows(dias, alvos, totais)
    aderencia = dash.window_adherence(linhas_dia)

    # Corpo: a última leitura do dia vale (a consulta já vem ordenada).
    por_data: dict[date, dict[str, Any]] = {}
    for linha in corpo_linhas:
        dia = _as_date(linha["local_date"])
        atual = por_data.setdefault(dia, {})  # type: ignore[arg-type]
        for coluna in ("weight_kg", "body_fat_percent", "muscle_mass_kg"):
            if linha.get(coluna) is not None:
                atual[coluna] = _num(linha[coluna])
    pesos = dash.positive_series([por_data.get(dia, {}).get("weight_kg") for dia in dias])
    gorduras = [por_data.get(dia, {}).get("body_fat_percent") for dia in dias]
    musculos = dash.positive_series([por_data.get(dia, {}).get("muscle_mass_kg") for dia in dias])
    pontos = dash.body_points(dias, pesos, gorduras, musculos)

    sujeito = resolve_subject(entradas.series.readings_on(hoje), hoje).subject
    massa_gorda = resolved_fat_mass_kg(sujeito)

    # Projeção até o alvo do plano (dono: `phases.project_goal`).
    plano_publico = None
    projecao = None
    if em_vigor is not None and ref is not None:
        plano_id, configuracao = em_vigor
        plano_publico = _plan_public(plano_id, configuracao, ref.effective_from)
        if configuracao.goal_weight_kg is not None or configuracao.goal_body_fat_percent is not None:
            ultima_tdee = next((m["tdee_kcal"] for m in reversed(metas) if m and m.get("tdee_kcal") is not None), None)
            resultado = project_goal(
                configuracao.strategy_params(),
                current_weight_kg=sujeito.weight_kg,
                goal_weight_kg=configuracao.goal_weight_kg,
                goal_body_fat_percent=configuracao.goal_body_fat_percent,
                current_body_fat_percent=sujeito.body_fat_percent,
                start_weight_kg=partida_peso,
                start_body_fat_percent=partida_gordura,
                as_of=hoje,
                tdee_kcal=ultima_tdee,
                fat_mass_kg=massa_gorda,
            )
            projecao = resultado.to_dict()
            projecao["reason_items"] = nutrition_labels.limitation_items(resultado.reasons)

    # TDEE adaptativo: só sugere (o bloco devolve `applied: false`).
    formula_tdee = next((m["tdee_kcal"] for m in reversed(metas) if m and m.get("tdee_kcal") is not None), None)
    adaptativo = dash.adaptive_block(dash.complete_intake_kcal(totais), pesos, massa_gorda, formula_tdee)
    for bloco in (adaptativo["observed"], adaptativo["suggestion"]):
        bloco["limitation_items"] = nutrition_labels.limitation_items(bloco["limitations"])

    tendencia_peso = dash.weight_trend_public(pesos)
    tendencia_peso["limitation_items"] = nutrition_labels.limitation_items(tendencia_peso["limitations"])
    semanas = dash.week_blocks(dias, alvos, totais)
    for bloco in semanas:
        bloco["report"]["limitations"] = nutrition_labels.limitation_items(bloco["report"]["limitations"])

    vazios = dash.empty_states(
        has_plan=plano_publico is not None,
        has_goal=projecao is not None,
        weight_readings=sum(1 for p in pesos if p is not None),
        bioimpedance_readings=sum(1 for g, m in zip(gorduras, musculos) if g is not None or m is not None),
        logged_days=aderencia.logged_days,
        days_without_target=aderencia.days_without_target,
    )
    return {
        "user": usuario,
        "today": hoje.isoformat(),
        "start": inicio.isoformat(),
        "end": hoje.isoformat(),
        "window_days": janela,
        "plan": plano_publico,
        "empty_states": nutrition_labels.limitation_items(vazios),
        "days": [linha.to_dict() for linha in linhas_dia],
        "adherence": aderencia.to_dict(),
        "weeks": semanas,
        "body": {"points": pontos, "weight_trend": tendencia_peso},
        "projection": projecao,
        "adaptive_tdee": adaptativo,
    }


__all__ = ["router"]
