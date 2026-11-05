"""Dashboard de nutrição (reformulação N5, L7 do plano de nutrição) e o balanço
de hoje para a home. Vive na api-core (`{proxy+}`), junto do diário.

Rotas (prefixo `/nutrition`):
- `GET /dashboard?days=&user_id=`  janela de `days` dias corridos que termina hoje
                                   (28 por padrão; fora de 7 a 90 é limitado; valor
                                   não inteiro = 422): aderência por dia e por semana,
                                   kcal e macros × meta, peso/%G/massa muscular,
                                   projeção até o alvo do plano e o TDEE adaptativo
                                   (só sugestão, `applied` sempre falso)
- `GET /today?user_id=`            só HOJE: materializa a meta de hoje (como o diário)
                                   e devolve meta, total e balanço; é o que o card da
                                   home mobile lê

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §82): a agregação é de
`domain/nutrition/dashboard` (compõe `weekly_budget` e `adaptive_tdee`); a
projeção até o alvo é a do plano (`plan.project_plan_goal` com o TDEE estável
`plan.projection_tdee`, o mesmo da prévia do plano do N2); aqui só se leem as linhas gravadas (metas materializadas,
totais gravados das refeições, composição corporal) e se monta a resposta. Não há
soma de nutrientes nem regra de nutrição neste arquivo. O dashboard NÃO materializa
metas de dias passados (sem backfill): dia com refeição e sem meta gravada é avisado.
Acesso: `nutrition_access` (D-L). Tabelas ausentes: 503 citando a migração.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import dashboard as dash
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.body_composition import resolved_fat_mass_kg
from .domain.nutrition.plan import PROJECTION_TRAINING_WINDOW_DAYS, plan_in_force, project_plan_goal, projection_tdee
from .domain.nutrition.subject import resolve_subject
from .domain.nutrition.weekly_budget import day_balance, target_macros
from .intelligence.dashboard.body_service import moving_average_points
from .intelligence.dashboard.metrics_registry import threshold
from .intelligence.dashboard.time_windows import user_today_for
from .nutrition_access import fetch_target_user, resolve_target_user
from .nutrition_days import balance_public, target_of
from .nutrition_meals import MEALS, NUTRIENTS, day_totals, totals_public
from .nutrition_schema import require_diary_tables, require_nutrition_tables
from .nutrition_targets_service import (
    compute_day,
    ensure_diary_targets,
    load_published_plans,
    load_user_inputs,
    past_training_kcal,
    read_targets,
    target_row_public,
)
from .security import Identity, get_current_identity

router = APIRouter(prefix="/nutrition", tags=["nutrition"])

# Só o que `stored_meal_totals` precisa (sem `description_raw`, `notes` nem JSON de itens: a
# resposta da RDS Data API tem teto de 1 MiB e 90 dias de refeições passariam dele).
MEAL_TOTALS_SQL = (
    f"SELECT id, local_date, {', '.join(NUTRIENTS)}, totals_incomplete_json, totals_nature, item_count "
    f"FROM {MEALS} WHERE user_id = %s AND local_date BETWEEN %s AND %s ORDER BY local_date, id"
)
# Massa muscular da bioimpedância (ESTIMATED); peso e %G vêm das séries do motor (`load_user_inputs`).
MUSCLE_WINDOW_SQL = (
    "SELECT local_date, muscle_mass_kg FROM body_composition "
    "WHERE user_id = %s AND local_date BETWEEN %s AND %s AND muscle_mass_kg IS NOT NULL ORDER BY local_date, id"
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


def _require_tables(cur: Any) -> None:
    require_nutrition_tables(cur)
    require_diary_tables(cur)


@router.get("/today")
def get_today(
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """Meta, total e balanço de HOJE do aluno (o `GET /nutrition/days/{date}` sem refeições, sem treino e sem semana)."""
    aluno = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_tables(cur)
            usuario = fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        metas = ensure_diary_targets(conn, aluno, [hoje], today=hoje)
        with conn.cursor() as cur:
            cur.execute(MEAL_TOTALS_SQL, (aluno, hoje, hoje))
            linhas = list(cur.fetchall() or [])
    meta = metas.rows.get(hoje)
    totais = day_totals(linhas)
    balanco = day_balance(target_of(meta), totais, meta.get("tdee_kcal") if meta else None)
    return {
        "user": usuario,
        "local_date": hoje.isoformat(),
        "today": hoje.isoformat(),
        "target": meta,
        "target_action": metas.actions[hoje].value,
        "totals": totals_public(totais),
        "balance": balance_public(balanco),
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
            _require_tables(cur)
            usuario = fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        dias_lidos = dash.window_dates(hoje, dash.fetch_days(janela))
        inicio_lido = dias_lidos[0]
        ensure_diary_targets(conn, aluno, [hoje], today=hoje)
        with conn.cursor() as cur:
            metas_linhas = [target_row_public(linha, hoje) for linha in read_targets(cur, aluno, inicio_lido, hoje)]
            cur.execute(MEAL_TOTALS_SQL, (aluno, inicio_lido, hoje))
            refeicoes = list(cur.fetchall() or [])
            cur.execute(MUSCLE_WINDOW_SQL, (aluno, inicio_lido, hoje))
            musculos_linhas = list(cur.fetchall() or [])
            planos = load_published_plans(cur, aluno)
            # Cobre também os dias de treino da projeção (TDEE estável do plano, N2).
            entradas = load_user_inputs(
                cur, aluno, hoje - timedelta(days=PROJECTION_TRAINING_WINDOW_DAYS), hoje, hoje, planos.settings.values()
            )

    # --- alinhamento por dia (lido = janela da tela ou a do TDEE observado, a maior) ---------------
    metas_por_dia = dash.last_by_day(dias_lidos, ((_as_date(m["local_date"]), m) for m in metas_linhas))
    alvos_lidos = [
        None if m is None else target_macros(m["kcal"], m["protein_g"], m["carbs_g"], m["fat_g"], m["fiber_g"])
        for m in metas_por_dia
    ]
    totais_lidos = [
        day_totals(grupo) for grupo in dash.group_by_day(dias_lidos, ((_as_date(r["local_date"]), r) for r in refeicoes))
    ]
    series = entradas.series
    pesos_lidos = dash.last_by_day(dias_lidos, ((r.local_date, r.number) for r in series.weight))
    gorduras_lidas = dash.positive_series(
        dash.last_by_day(dias_lidos, ((r.local_date, r.value) for r in series.bioimpedance_body_fat))
    )
    musculos_lidos = dash.positive_series(
        dash.last_by_day(dias_lidos, ((_as_date(r["local_date"]), _num(r["muscle_mass_kg"])) for r in musculos_linhas))
    )

    # --- janela da tela ----------------------------------------------------------------------------
    recorte = slice(-janela, None)
    dias = dias_lidos[recorte]
    alvos, totais = alvos_lidos[recorte], totais_lidos[recorte]
    pesos, gorduras, musculos = pesos_lidos[recorte], gorduras_lidas[recorte], musculos_lidos[recorte]

    versoes = [
        dash.PlanVersion(
            plan_id=ref.plan_id,
            effective_from=ref.effective_from,
            published_at=ref.published_at,
            strategy=planos.settings[ref.plan_id].strategy.value,
            goal_weight_kg=planos.settings[ref.plan_id].goal_weight_kg,
            goal_body_fat_percent=planos.settings[ref.plan_id].goal_body_fat_percent,
        )
        for ref in planos.refs
    ]
    estados = dash.day_states(dias, hoje, dash.first_plan_start(versoes), totais)
    linhas_dia = dash.day_rows(dias, alvos, totais, estados)
    aderencia = dash.window_adherence(linhas_dia)
    semanas = dash.week_blocks(dias, alvos, totais, estados)
    for bloco in semanas:
        bloco["report"]["limitations"] = nutrition_labels.limitation_items(bloco["report"]["limitations"])

    # --- corpo: média móvel do dono do dashboard de corpo, olhando para trás do início da janela ----
    pesados = [
        {"local_date": dia, "weight_kg": peso, "source": "body_composition"}
        for dia, peso in dash.daily_values((r.local_date, r.number) for r in series.weight)
    ]
    medias = moving_average_points(pesados, ma_days=int(threshold("weight_ma_days")), date_from=dias[0], date_to=hoje)
    medias_por_dia = {ponto["local_date"]: ponto["ma7_kg"] for ponto in medias if ponto["ma7_kg"] is not None}
    pontos = dash.body_points(dias, pesos, medias_por_dia, gorduras, musculos)
    tendencia_peso = dash.weight_trend_public(pesos)
    tendencia_peso["limitation_items"] = nutrition_labels.limitation_items(tendencia_peso["limitations"])

    sujeito = resolve_subject(series.readings_on(hoje), hoje).subject
    massa_gorda = resolved_fat_mass_kg(sujeito)

    # --- gasto: média do TDEE das metas dos últimos dias do TDEE observado ---------------------------
    tdees = [None if m is None else m.get("tdee_kcal") for m in metas_por_dia]
    gasto_formula = dash.window_tdee_kcal(dias_lidos, hoje, tdees)

    # --- plano em vigor, projeção até o alvo (dono: `plan.project_plan_goal`, a mesma do plano) --------------------------
    plano_publico = None
    projecao = None
    ref = plan_in_force(planos.refs, hoje)
    if ref is not None:
        configuracao = planos.settings[ref.plan_id]
        inicio_caminho = dash.goal_sequence_start(versoes, hoje) or ref.effective_from
        plano_publico = {
            "id": ref.plan_id,
            "strategy": configuracao.strategy.value,
            "effective_from": ref.effective_from.isoformat(),
            "goal_weight_kg": configuracao.goal_weight_kg,
            "goal_body_fat_percent": configuracao.goal_body_fat_percent,
        }
        if configuracao.goal_weight_kg is not None or configuracao.goal_body_fat_percent is not None:
            partida_peso = next((r.number for r in series.weight if r.local_date and r.local_date >= inicio_caminho and r.number), None)
            partida_gordura = next(
                (r.number for r in series.bioimpedance_body_fat if r.local_date and r.local_date >= inicio_caminho and r.number),
                None,
            )
            # Mesmo dono e mesmo TDEE estável da projeção do plano (N2): gramas manuais e modo do treino incluídos.
            comp_hoje = compute_day(entradas, configuracao, hoje, hoje)
            tdee_estavel, premissas = projection_tdee(
                comp_hoje.target.tdee, past_training_kcal(entradas, configuracao, hoje), configuracao.manual_adds_training
            )
            resultado = project_plan_goal(
                configuracao,
                comp_hoje.subject.subject,
                tdee_estavel,
                hoje,
                premissas,
                start_weight_kg=partida_peso,
                start_body_fat_percent=partida_gordura,
            )
            projecao = resultado.to_dict()
            projecao["reason_items"] = nutrition_labels.limitation_items(resultado.reasons)

    # --- TDEE adaptativo: só sugere (o bloco devolve `applied: false`) -------------------------------
    adaptativo = dash.adaptive_block(
        dash.adaptive_intake_kcal(dias_lidos, hoje, totais_lidos), pesos_lidos, massa_gorda, gasto_formula
    )
    for bloco in (adaptativo["observed"], adaptativo["suggestion"]):
        bloco["limitation_items"] = nutrition_labels.limitation_items(bloco["limitations"])

    vazios = dash.empty_states(
        has_plan=plano_publico is not None,
        has_goal=projecao is not None,
        weight_readings=dash.count_present(pesos),
        bioimpedance_readings=dash.count_present(g if g is not None else m for g, m in zip(gorduras, musculos)),
        logged_days=aderencia.logged_days,
        meal_days_without_target=aderencia.meal_days_without_target,
    )
    return {
        "user": usuario,
        "today": hoje.isoformat(),
        "start": dias[0].isoformat(),
        "end": hoje.isoformat(),
        "window_days": janela,
        "window_choices": list(dash.WINDOW_CHOICES_DAYS),
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
