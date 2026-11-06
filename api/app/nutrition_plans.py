"""Plano nutricional do aluno: rascunho, preview, publicação e metas diárias
(plano de nutrição §8.1, §13 L2b). Vive na api-core (`{proxy+}`).

Rotas (prefixo `/nutrition`). Plano autogerido (N2, D2): o usuário opera o PRÓPRIO plano;
admin e PT operam o de qualquer aluno; usuário comum nunca alcança o plano de outro (403 por
`user_id`, 404 por id de plano). Sem `user_id`, o alvo é o próprio usuário.
- `GET    /plans?user_id=`          versões do aluno (vigente, histórico, rascunhos); sem os arquivados,
                                    `include_archived=true` traz todos (cada plano leva `archived`,
                                    `archived_at` e `can_archive`)
- `POST   /plans/{id}/archive`      oculta um plano SUBSTITUÍDO (409 `plan_not_archivable` em rascunho/vigente;
                                    reversível, idempotente); `.../unarchive` desfaz. Só oculta: as metas por
                                    dia (`load_published_plans`) seguem lendo os arquivados
- `POST   /plans`                   `NutritionPlanIn` -> rascunho (201); gramas manuais fora da faixa
                                    salvam com `warnings`, bloqueadas ou sem peso do aluno dão 400
- `PUT    /plans/{id}`              só rascunho (409 se publicado)
- `DELETE /plans/{id}`              só rascunho (409 se publicado)
- `POST   /plans/{id}/preview`      sujeito, TMB, TDEE, alvo, macros, os 7 dias da semana atual,
                                    a projeção até o alvo e os avisos, SEM gravar; 422 lista os
                                    dados do aluno que faltam (códigos do motor)
- `POST   /plan-preview`            a mesma prévia para o formulário AINDA NÃO salvo (N6, sugestão por IA):
                                    `NutritionPlanIn` -> o que o motor calcula, sem gravar; `plan` = null
- `GET    /cycle-days/from-template` proposta dos dias alto/normal/baixo a partir do Modelo
                                    atribuído ("puxar do Modelo", D5); o usuário ajusta antes de salvar
- `POST   /plans/{id}/publish`      `{expected_current_id}` (a vigente que a tela
                                    viu, ou null) — concorrência otimista: a vigente é lida
                                    com `FOR UPDATE` e, se divergir, 409 `publish_conflict`;
                                    substitui a vigente e materializa a janela de publicação
                                    do motor (passadas intactas)
- `POST   /targets/recompute`       `{user_id?, date_from, date_to?}` na janela de
                                    recálculo do motor (400 com o código do motor)
- `GET    /me/plan`                 qualquer usuário: o plano em vigor hoje (só leitura),
                                    o resumo do sujeito e a semana; 404 `no_published_plan`

Regra dos motores (`docs/motores/FONTES_DA_VERDADE.md` §3.7 e §47): faixas,
defaults, vocabulário, rótulos, congelamento, janelas, vigência, %G de parada e
o cálculo da meta são do pacote `domain/nutrition`. Esta camada valida com
`plan.build_plan_settings`, consulta o banco e devolve o que o motor calculou.
"Hoje" é o dia civil do ALUNO (`time_windows.user_today_for`). Acesso:
`nutrition_access` (PT sem vínculo formal, decisão D-L).
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from .db import get_conn, is_duplicate_key_error
from .db_helpers import connect_or_503
from .domain.nutrition import ENGINE_VERSION
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.energy_expenditure import tdee_breakdown
from .domain.nutrition.daily_target import (
    TARGET_RECOMPUTE_MAX_DAYS,
    missing_subject_data,
    publication_window,
    recompute_window,
    week_window,
)
from .domain.nutrition.plan import (
    ERR_DRAFT_LIMIT,
    MAX_DRAFTS_PER_USER,
    NOTE_TEMPLATE_PARTIAL_WEEKDAYS,
    PLAN_LIST_MAX,
    PROJECTION_TRAINING_WINDOW_DAYS,
    auto_intervals_blockers,
    projection_tdee,
    rest_day_strategy_kcal,
    check_cycle_floors,
    partial_week_warnings,
    template_weekdays_note,
    NOTE_TEMPLATE_NOT_ASSIGNED,
    NOTE_TEMPLATE_NO_CYCLE,
    PlanSettings,
    PlanStatus,
    build_plan_settings,
    check_plan_manual_targets,
    goal_summary,
    manual_save_errors,
    manual_save_warnings,
    is_archivable_plan,
    plan_state,
    plan_warnings,
    project_plan_goal,
    propose_cycle_from_template,
    training_weekdays_from_template,
)
from .intelligence.dashboard.time_windows import user_today_for
from .intelligence.methodology_volume import is_training_slot_type
from .nutrition_access import fetch_target_user, plan_actor, resolve_plan_owner, resolve_target_user
from .nutrition_schema import require_nutrition_tables, require_plan_energy_columns
from .nutrition_targets_service import (
    DayComputation,
    UserInputs,
    compute_day,
    day_public,
    ensure_daily_targets,
    last_target_date,
    load_published_plans,
    load_user_inputs,
    past_training_kcal,
    settings_from_row,
    week_rows,
)
from .request_models import (
    NutritionPlanIn,
    NutritionPlanPublishIn,
    NutritionPlanSettingsIn,
    NutritionTargetsRecomputeIn,
)
from .security import Identity, get_current_identity
from .template_assignments import db_now, resolve_active_template_id

router = APIRouter(prefix="/nutrition", tags=["nutrition"])
logger = logging.getLogger(__name__)

TABLE = "nutrition_plans"

_PLAN_COLUMNS = (
    "id, user_id, created_by, published_by, status, strategy, advanced, bmr_formula, activity_level, "
    "training_kcal_mode, goal_body_fat_percent, goal_weight_kg, weekly_rate_share, surplus_share, "
    "protein_g_per_kg, fat_kcal_share, carb_min_g_per_kg, meals_per_day, notes, engine_version, "
    "macro_priority, manual_targets_json, carb_cycling_json, maintenance_kcal_adjust, "
    "energy_params_json, energy_model_version, "
    "effective_from, superseded_at, archived_at, published_at, created_at, updated_at"
)
# `published_preview_json` (106) fica FORA de `_PLAN_COLUMNS` de propósito: é a prévia gravada na publicação (dezenas de KB
# por plano) e nenhuma leitura de plano a usa; as listas de planos não pagam o peso (teto de 1 MiB da Data API).

# Ordem das colunas de configuração na escrita (INSERT/UPDATE).
_SETTINGS_WRITE_COLUMNS = (
    "strategy", "advanced", "bmr_formula", "activity_level", "training_kcal_mode", "goal_body_fat_percent",
    "goal_weight_kg", "weekly_rate_share", "surplus_share", "protein_g_per_kg", "fat_kcal_share",
    "carb_min_g_per_kg", "meals_per_day", "macro_priority", "manual_targets_json", "carb_cycling_json", "notes",
    "engine_version", "maintenance_kcal_adjust", "energy_params_json",
)

_INSERT_SQL = (
    f"INSERT INTO {TABLE} (user_id, created_by, status, {', '.join(_SETTINGS_WRITE_COLUMNS)}) "
    f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(_SETTINGS_WRITE_COLUMNS))})"
)
_UPDATE_DRAFT_SQL = (
    f"UPDATE {TABLE} SET {', '.join(f'{c} = %s' for c in _SETTINGS_WRITE_COLUMNS)} WHERE id = %s AND status = %s"
)
_DELETE_DRAFT_SQL = f"DELETE FROM {TABLE} WHERE id = %s AND status = %s"
# Trava da publicação: a linha vigente do aluno (a do UNIQUE da 078). Com
# `FOR UPDATE`, duas publicações para o mesmo aluno se enfileiram aqui; a
# segunda enxerga a vigente nova e responde 409 se a tela viu outra.
_CURRENT_FOR_UPDATE_SQL = (
    f"SELECT id FROM {TABLE} WHERE user_id = %s AND status = %s AND superseded_at IS NULL FOR UPDATE"
)
_SUPERSEDE_SQL = (
    f"UPDATE {TABLE} SET superseded_at = %s "
    "WHERE user_id = %s AND status = %s AND superseded_at IS NULL AND id <> %s"
)
_PUBLISH_SQL = (
    f"UPDATE {TABLE} SET status = %s, effective_from = %s, published_at = %s, published_by = %s, engine_version = %s, "
    "energy_model_version = %s "
    "WHERE id = %s AND status = %s"
)
# A prévia da publicação (auditoria) vai num UPDATE à parte e protegido: uma falha ao gravá-la nunca derruba a publicação.
_PUBLISHED_PREVIEW_SQL = f"UPDATE {TABLE} SET published_preview_json = %s WHERE id = %s"
# Teto do JSON da prévia gravada (bytes); acima dele grava NULL e registra só o tamanho. A Data API aceita 4 MiB por
# requisição; 256 KiB deixa folga enorme e a prévia real tem poucos KB (medida no teste).
PUBLISHED_PREVIEW_MAX_BYTES = 256 * 1024
_TEMPLATE_NAME_SQL = "SELECT name FROM workout_plan_templates WHERE id = %s"
# Slots da versão VIGENTE do Modelo (desde a 065 os leitores só enxergam a versão vigente).
_TEMPLATE_SLOTS_SQL = (
    "SELECT s.weekday, s.slot_type FROM workout_plan_template_slots s "
    "JOIN workout_plan_template_versions v ON v.id = s.version_id "
    "AND s.template_id = v.current_template_id WHERE v.current_template_id = %s ORDER BY s.sequence"
)
# Com `expected_updated_at`, a publicação só vale se o rascunho continua na versão lida (resolução de 1 s do DATETIME).
_PUBLISH_IF_UNCHANGED_SQL = _PUBLISH_SQL + " AND updated_at = %s"
_BY_ID_SQL = f"SELECT {_PLAN_COLUMNS} FROM {TABLE} WHERE id = %s"
_BY_USER_SQL = f"SELECT {_PLAN_COLUMNS} FROM {TABLE} WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s"
# Lista padrão: sem os arquivados (migração 105). `include_archived=true` usa `_BY_USER_SQL`.
_BY_USER_VISIBLE_SQL = (
    f"SELECT {_PLAN_COLUMNS} FROM {TABLE} WHERE user_id = %s AND archived_at IS NULL "
    "ORDER BY created_at DESC, id DESC LIMIT %s"
)
# Arquivar só oculta: o guarda de estado (`is_archivable_plan`) é conferido na rota; o UPDATE repete a condição
# (publicado e substituído) para uma publicação concorrente não arquivar o plano que acabou de virar vigente.
_ARCHIVE_SQL = (
    f"UPDATE {TABLE} SET archived_at = %s WHERE id = %s AND archived_at IS NULL "
    "AND status = %s AND superseded_at IS NOT NULL"
)
_UNARCHIVE_SQL = f"UPDATE {TABLE} SET archived_at = NULL WHERE id = %s AND archived_at IS NOT NULL"
_DRAFT_COUNT_SQL = f"SELECT COUNT(*) AS n FROM {TABLE} WHERE user_id = %s AND status = %s"

DRAFT = PlanStatus.DRAFT.value
PUBLISHED = PlanStatus.PUBLISHED.value


def _connect():
    return connect_or_503(get_conn)


def _require_plan_tables(cur: Any) -> None:
    """503 citando a migração quando as tabelas do plano (078/079) ou as colunas do modo adaptativo (106) faltam."""
    require_nutrition_tables(cur)
    require_plan_energy_columns(cur)


def _today_for(conn: Any, user_id: int) -> date:
    """Dia civil do ALUNO (fuso do perfil). Isolado para os testes congelarem."""
    return user_today_for(conn, user_id)


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def _invalid_plan(codes: tuple[str, ...]) -> HTTPException:
    return _error(
        400,
        "invalid_plan",
        "Parâmetros do plano fora das regras do motor de nutrição.",
        errors=nutrition_labels.limitation_items(codes),
    )


def _validate_payload(payload: NutritionPlanSettingsIn) -> PlanSettings:
    validacao = build_plan_settings(
        strategy=payload.strategy,
        advanced=payload.advanced,
        protein_g_per_kg=payload.protein_g_per_kg,
        fat_kcal_share=payload.fat_kcal_share,
        weekly_rate_share=payload.weekly_rate_share,
        surplus_share=payload.surplus_share,
        carb_min_g_per_kg=payload.carb_min_g_per_kg,
        bmr_formula=payload.bmr_formula,
        tdee_mode=payload.tdee_mode,
        activity_level=payload.activity_level,
        meals_per_day=payload.meals_per_day,
        goal_body_fat_percent=payload.goal_body_fat_percent,
        goal_weight_kg=payload.goal_weight_kg,
        macro_priority=payload.macro_priority,
        manual_targets=payload.manual_targets,
        cycle_days=payload.cycle_days,
        manual_training_mode=payload.manual_training_mode,
        maintenance_kcal_adjust=payload.maintenance_kcal_adjust,
        energy_params=payload.energy_params,
    )
    if not validacao.ok:
        raise _invalid_plan(validacao.errors)
    return validacao.settings  # type: ignore[return-value]


def _settings_write_values(settings: PlanSettings, notes: Optional[str]) -> tuple[Any, ...]:
    return (
        settings.strategy.value,
        1 if settings.advanced else 0,
        settings.bmr_formula.value,
        settings.activity_level.value if settings.activity_level else None,
        settings.tdee_mode.value,
        settings.goal_body_fat_percent,
        settings.goal_weight_kg,
        settings.weekly_rate_share,
        settings.surplus_share,
        settings.protein_g_per_kg,
        settings.fat_kcal_share,
        settings.carb_min_g_per_kg,
        settings.meals_per_day,
        settings.macro_priority.value,
        settings.manual_macros.to_dict() if settings.manual_macros else None,
        settings.cycle_days,
        notes,
        ENGINE_VERSION,
        settings.maintenance_kcal_adjust,
        # Só o que sai do padrão (a coluna fica NULL nos planos legados e nos adaptativos com o padrão).
        None if settings.energy_params.is_default else settings.energy_params.to_dict(),
    )


def plan_public(row: dict[str, Any]) -> dict[str, Any]:
    """Plano para a API: configuração (vocabulário do motor) + estado + datas."""
    settings = settings_from_row(row)
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "status": row.get("status"),
        "state": plan_state(row.get("status"), row.get("superseded_at")).value,
        **settings.to_dict(),
        "notes": row.get("notes"),
        "engine_version": row.get("engine_version"),
        # Lote C: parâmetros do modelo de gasto (sempre presentes; o padrão é `unknown`) e a versão do algoritmo gravada
        # na publicação do plano adaptativo (`None` nos legados e nos rascunhos).
        "energy_params": None if settings.energy_params.is_default else settings.energy_params.to_dict(),
        "energy_model_version": row.get("energy_model_version"),
        "effective_from": _iso(row.get("effective_from")),
        "superseded_at": _iso(row.get("superseded_at")),
        "archived_at": _iso(row.get("archived_at")),
        "archived": row.get("archived_at") is not None,
        "can_archive": row.get("archived_at") is None and is_archivable_plan(row.get("status"), row.get("superseded_at")),
        "published_at": _iso(row.get("published_at")),
        "created_by": row.get("created_by"),
        "published_by": row.get("published_by"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _fetch_plan(cur: Any, plan_id: int) -> dict[str, Any]:
    cur.execute(_BY_ID_SQL, (int(plan_id),))
    row = cur.fetchone()
    if not row:
        raise _error(404, "plan_not_found", "Plano não encontrado.")
    return row


def _require_draft(row: dict[str, Any]) -> None:
    if row.get("status") != DRAFT:
        raise _error(409, "plan_not_draft", "Só um rascunho pode ser alterado; o plano já foi publicado.")


def _plan_checks(conn: Any, aluno: int, settings: PlanSettings, hoje: date) -> tuple[Any, tuple[str, ...]]:
    """Confere o plano contra o sujeito de HOJE (peso e MLG do usuário): gramas manuais (D6) e pisos do
    ciclo (menor e maior dia, com e sem gramas manuais). Levanta 400 `invalid_plan` quando falta o peso ou o
    motor bloqueia; devolve (checagem das gramas, avisos). Sem gramas manuais nem ciclo não consulta nada. A
    meta da ESTRATÉGIA (sem as gramas) e a TMB servem só aos avisos de energia."""
    if settings.is_auto_intervals:
        # O fallback do automático usa o nível do PERFIL: sem ele não há fator e não se inventa um (bloqueia o plano).
        with conn.cursor() as cur:
            perfil = compute_day(load_user_inputs(cur, aluno, hoje, hoje, hoje), replace(settings, manual_macros=None, cycle_kinds=None), hoje, hoje)
        bloqueios = auto_intervals_blockers(settings, perfil.subject.subject.activity_level)
        if bloqueios:
            raise _invalid_plan(bloqueios)
    if settings.manual_macros is None and settings.cycle_kinds is None:
        return None, ()
    with conn.cursor() as cur:
        entradas = load_user_inputs(cur, aluno, hoje, hoje, hoje, [settings])
    comp = compute_day(entradas, replace(settings, manual_macros=None), hoje, hoje)
    sujeito = comp.subject.subject
    referencia = (
        rest_day_strategy_kcal(settings, sujeito, hoje, comp.subject.scale_bmr_kcal, comp.subject.ffm_age_days)
        if settings.manual_adds_training
        else comp.target.kcal_target.kcal
    )
    checagem = check_plan_manual_targets(settings, sujeito, referencia, comp.target.bmr.kcal)
    erros = list(manual_save_errors(checagem, sujeito))
    erros_ciclo, avisos_ciclo = check_cycle_floors(settings, sujeito, comp.subject.scale_bmr_kcal, comp.subject.ffm_age_days)
    erros.extend(erros_ciclo)
    if erros:
        raise _invalid_plan(tuple(erros))
    return checagem, (*manual_save_warnings(checagem), *avisos_ciclo)


def _needs_checks(settings: PlanSettings) -> bool:
    return settings.manual_macros is not None or settings.cycle_kinds is not None or settings.is_auto_intervals


def _manual_check_public(check: Any) -> Optional[dict[str, Any]]:
    """Checagem das gramas manuais para a API, com o rótulo de cada campo e o texto dos códigos."""
    if check is None:
        return None
    dados = check.to_dict()
    for campo in dados["fields"]:
        campo["label"] = nutrition_labels.field_label(campo["field"])
        campo["code_items"] = nutrition_labels.limitation_items(campo["codes"])
    return dados


def _saved_plan(row: dict[str, Any], warnings: tuple[str, ...]) -> dict[str, Any]:
    return {**plan_public(row), "warnings": nutrition_labels.limitation_items(warnings)}


def _no_published_plan(message: str) -> HTTPException:
    return _error(404, "no_published_plan", message)


# ---------------------------------------------------------------------------
# Preview (sem gravar)
# ---------------------------------------------------------------------------


def _target_detail(comp: DayComputation, today: date) -> dict[str, Any]:
    meta = comp.target
    alvo = meta.kcal_target.to_dict()
    alvo["clamped_by"] = nutrition_labels.limitation_items(meta.kcal_target.clamped_by)
    return {
        **day_public(comp, today),
        # Diferença REAL para o gasto total (já com gramas manuais, ciclo e prioridade); `kcal_target.delta_kcal`
        # é só a da estratégia.
        "kcal_delta": meta.kcal_delta,
        "bmr": meta.bmr.to_dict(),
        "scale_comparison": meta.scale_comparison.to_dict() if meta.scale_comparison else None,
        "tdee": meta.tdee.to_dict(),
        # Composição do gasto pronta para a tela (dono: `energy_expenditure.tdee_breakdown`; só saída pública).
        "tdee_breakdown": nutrition_labels.tdee_breakdown_public(
            tdee_breakdown(meta.tdee, meta.extra_activity.kcal, meta.tdee_basis)
        ),
        "training": meta.training_kcal.to_dict(),
        "kcal_target": alvo,
        "macros": meta.macros.to_dict(),
        "protein_per_meal_g": meta.protein_per_meal_g,
    }


def _goal(comp: DayComputation) -> dict[str, Any]:
    """Meta de composição do motor (`plan.goal_summary`, D-C)."""
    return goal_summary(comp.settings, comp.subject.subject).to_dict()


def _compute_week(
    cur: Any, user_id: int, settings: PlanSettings, today: date, entradas: Optional[UserInputs] = None
) -> list[DayComputation]:
    janela = week_window(today)
    if entradas is None:
        entradas = load_user_inputs(cur, user_id, janela.date_from, janela.date_to, today, [settings])
    return [compute_day(entradas, settings, dia, today) for dia in janela.days()]


def _week_and_past(
    cur: Any, aluno: int, settings: PlanSettings, today: date
) -> tuple[list[DayComputation], list[Optional[float]]]:
    """A semana atual e o gasto de treino dos dias anteriores com UMA carga de entradas (a janela cobre as duas): no plano
    adaptativo isso evita ler e reprocessar o histórico duas vezes na prévia e na publicação."""
    janela = week_window(today)
    inicio = today - timedelta(days=PROJECTION_TRAINING_WINDOW_DAYS)
    entradas = load_user_inputs(cur, aluno, min(janela.date_from, inicio), janela.date_to, today, [settings])
    return (
        _compute_week(cur, aluno, settings, today, entradas),
        _past_training(cur, aluno, settings, today, entradas),
    )


def subject_incomplete_error(comp: DayComputation) -> HTTPException:
    return _error(
        422,
        "nutrition_subject_incomplete",
        "Faltam dados do perfil para calcular a meta de hoje.",
        missing=nutrition_labels.limitation_items(missing_subject_data(comp.target)),
        subject=comp.subject.to_dict(),
    )


def _projection(comp: DayComputation, today: date, tdee: Optional[float], premissas: Sequence[str]) -> dict[str, Any]:
    """Projeção até o alvo do plano (`phases.project_goal` via `plan.project_plan_goal`), com a
    premissa `is_minimum_estimate` e os motivos rotulados. O TDEE é o ESTÁVEL do motor (`plan.projection_tdee`),
    que não depende do treino do dia em que a prévia é aberta."""
    projecao = project_plan_goal(comp.settings, comp.subject.subject, tdee, today, premissas).to_dict()
    projecao["reason_items"] = nutrition_labels.limitation_items(projecao["reasons"])
    return projecao


def _past_training(
    cur: Any, aluno: int, settings: PlanSettings, today: date, entradas: Optional[UserInputs] = None
) -> list[Optional[float]]:
    """Gasto de treino dos `PROJECTION_TRAINING_WINDOW_DAYS` dias ANTES de hoje (kcal do treino; 0 em dia sem
    treino; `None` em dia de treino sem kcal e sem duração ou peso), com a kcal já resolvida pelo motor
    (informada > MET), para o TDEE estável da projeção."""
    if entradas is None:
        inicio = today - timedelta(days=PROJECTION_TRAINING_WINDOW_DAYS)
        fim = today - timedelta(days=1)
        entradas = load_user_inputs(cur, aluno, inicio, fim, today, [settings])
    return past_training_kcal(entradas, settings, today)



def _preview_payload(
    row: Optional[dict[str, Any]],
    user: dict[str, Any],
    semana: list[DayComputation],
    today: date,
    past_training: Sequence[Optional[float]] = (),
) -> dict[str, Any]:
    hoje = next(comp for comp in semana if comp.day == today)
    limitacoes: list[str] = []
    for comp in semana:
        limitacoes.extend(comp.target.limitations)
    sujeito = hoje.subject.subject
    # O TDEE estável (não o do treino de hoje) vale para o aviso de ritmo e para a projeção.
    tdee_estavel, premissas = projection_tdee(hoje.target.tdee, past_training, hoje.settings.manual_adds_training)
    referencia = (
        rest_day_strategy_kcal(hoje.settings, sujeito, today, hoje.subject.scale_bmr_kcal, hoje.subject.ffm_age_days)
        if hoje.settings.manual_adds_training
        else hoje.target.kcal_target.kcal
    )
    manual = check_plan_manual_targets(hoje.settings, sujeito, referencia, hoje.target.bmr.kcal)
    erros_ciclo, avisos_ciclo = check_cycle_floors(hoje.settings, sujeito, hoje.subject.scale_bmr_kcal, hoje.subject.ffm_age_days)
    avisos = (
        *plan_warnings(hoje.settings, sujeito, tdee_estavel),
        *manual_save_warnings(manual),
        *avisos_ciclo,
        *partial_week_warnings(hoje.settings, today),
    )
    # Bloqueios (não são avisos): salvar já os recusaria; a tela desabilita Publicar com o motivo.
    bloqueios = (*manual_save_errors(manual, sujeito), *erros_ciclo)
    return {
        "plan": plan_public(row) if row is not None else None,
        "user": user,
        "today": today.isoformat(),
        "engine_version": ENGINE_VERSION,
        "subject": hoje.subject.to_dict(),
        "target": _target_detail(hoje, today),
        "goal": _goal(hoje),
        "week": [day_public(comp, today) for comp in semana],
        # Os dias que a publicação de hoje de fato cria (do dia de hoje ao fim da semana): a tela só mostra estes.
        "publish_days": [dia.isoformat() for dia in publication_window(today, None).days()],
        "limitations": nutrition_labels.limitation_items(limitacoes),
        "projection": _projection(hoje, today, tdee_estavel, premissas),
        "warnings": nutrition_labels.limitation_items(avisos),
        "blocking": nutrition_labels.limitation_items(bloqueios),
        "can_publish": not bloqueios,
        "manual_check": _manual_check_public(manual),
    }


# ---------------------------------------------------------------------------
# Rotas do plano (o próprio usuário; admin/PT para qualquer aluno)
# ---------------------------------------------------------------------------


@router.get("/plans")
def list_plans(
    user_id: Optional[int] = Query(default=None),
    include_archived: bool = Query(default=False),
    identity: Identity = Depends(plan_actor),
):
    alvo = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            usuario = fetch_target_user(cur, alvo)
            cur.execute(_BY_USER_SQL if include_archived else _BY_USER_VISIBLE_SQL, (alvo, PLAN_LIST_MAX))
            linhas = cur.fetchall() or []
    return {"user": usuario, "items": [plan_public(linha) for linha in linhas]}


@router.post("/plans", status_code=201)
def create_plan(payload: NutritionPlanIn, identity: Identity = Depends(plan_actor)):
    settings = _validate_payload(payload)
    alvo = resolve_target_user(identity, payload.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            fetch_target_user(cur, alvo)
            cur.execute(_DRAFT_COUNT_SQL, (alvo, DRAFT))
            if int((cur.fetchone() or {}).get("n") or 0) >= MAX_DRAFTS_PER_USER:
                raise _error(
                    409,
                    ERR_DRAFT_LIMIT,
                    nutrition_labels.limitation_label(ERR_DRAFT_LIMIT),
                    max_drafts=MAX_DRAFTS_PER_USER,
                )
        _, avisos = _plan_checks(conn, alvo, settings, _today_for(conn, alvo)) if _needs_checks(settings) else (None, ())
        with conn.cursor() as cur:
            cur.execute(_INSERT_SQL, (alvo, identity.user_id, DRAFT, *_settings_write_values(settings, payload.notes)))
            novo_id = cur.lastrowid
            if not novo_id:  # pragma: no cover - defesa: Data API sempre devolve o id gerado
                raise _error(500, "plan_insert_failed", "Plano não encontrado após gravar.")
            linha = _fetch_plan(cur, int(novo_id))
    return _saved_plan(linha, avisos)


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, payload: NutritionPlanSettingsIn, identity: Identity = Depends(plan_actor)):
    settings = _validate_payload(payload)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            aluno = resolve_plan_owner(identity, int(linha["user_id"]))
            _require_draft(linha)
        _, avisos = _plan_checks(conn, aluno, settings, _today_for(conn, aluno)) if _needs_checks(settings) else (None, ())
        with conn.cursor() as cur:
            cur.execute(_UPDATE_DRAFT_SQL, (*_settings_write_values(settings, payload.notes), int(plan_id), DRAFT))
            if int(cur.rowcount or 0) == 0:
                # Publicado ou apagado por outra chamada entre a leitura e o UPDATE.
                raise _error(409, "plan_not_draft", "O rascunho mudou durante a edição; recarregue.")
            linha = _fetch_plan(cur, plan_id)
    return _saved_plan(linha, avisos)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, identity: Identity = Depends(plan_actor)):
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            resolve_plan_owner(identity, int(linha["user_id"]))
            _require_draft(linha)
            cur.execute(_DELETE_DRAFT_SQL, (int(plan_id), DRAFT))
            if int(cur.rowcount or 0) == 0:
                raise _error(409, "plan_not_draft", "O rascunho mudou durante a exclusão; recarregue.")
    return {"status": "deleted", "id": int(plan_id)}


@router.post("/plans/{plan_id}/archive")
def archive_plan(plan_id: int, identity: Identity = Depends(plan_actor)):
    """Oculta da lista um plano SUBSTITUÍDO (reversível). Rascunho (tem DELETE) e vigente: 409
    `plan_not_archivable`. Já arquivado: 200 sem mudar nada (idempotente)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            resolve_plan_owner(identity, int(linha["user_id"]))
            if linha.get("archived_at") is None:
                if not is_archivable_plan(linha.get("status"), linha.get("superseded_at")):
                    raise _error(
                        409, "plan_not_archivable", "Só um plano substituído pode ser arquivado (rascunho se exclui; o vigente fica)."
                    )
                cur.execute(_ARCHIVE_SQL, (db_now(cur), int(plan_id), PUBLISHED))
                if int(cur.rowcount or 0) == 0:
                    # Corrida: outra chamada pode já ter arquivado (idempotente, 200) ou publicado/alterado (409).
                    if _fetch_plan(cur, plan_id).get("archived_at") is None:
                        raise _error(409, "plan_not_archivable", "O plano mudou durante o pedido; recarregue.")
            linha = _fetch_plan(cur, plan_id)
    return plan_public(linha)


@router.post("/plans/{plan_id}/unarchive")
def unarchive_plan(plan_id: int, identity: Identity = Depends(plan_actor)):
    """Volta o plano arquivado para a lista. Não arquivado: 200 sem mudar nada (idempotente)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            resolve_plan_owner(identity, int(linha["user_id"]))
            if linha.get("archived_at") is not None:
                cur.execute(_UNARCHIVE_SQL, (int(plan_id),))
                linha = _fetch_plan(cur, plan_id)
    return plan_public(linha)


@router.post("/plans/{plan_id}/preview")
def preview_plan(plan_id: int, identity: Identity = Depends(plan_actor)):
    """Calcula a semana atual com ESTE plano, sem gravar nada."""
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            aluno = resolve_plan_owner(identity, int(linha["user_id"]))
            usuario = fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        with conn.cursor() as cur:
            semana, passado = _week_and_past(cur, aluno, settings_from_row(linha), hoje)
    comp_hoje = next(comp for comp in semana if comp.day == hoje)
    if not comp_hoje.target.computable:
        raise subject_incomplete_error(comp_hoje)
    return _preview_payload(linha, usuario, semana, hoje, passado)


def published_preview(cur: Any, aluno: int, linha: dict[str, Any], settings: PlanSettings, hoje: date) -> Optional[dict[str, Any]]:
    """Prévia aprovada na publicação (`nutrition_plans.published_preview_json`, 106): a MESMA do `POST /plans/{id}/preview`
    (`_preview_payload` sobre a semana atual), calculada no servidor no instante da publicação e jsonável. Sem os campos
    `plan` (o rascunho, ainda não publicado) e `user` (dado pessoal, redundante): fica só o que foi calculado.

    Roda em TODA publicação (todos os modos): o custo extra (uma carga de entradas e o cálculo da semana) é só da publicação,
    nunca de leitura. É registro de auditoria: se montá-lo falhar, a publicação NÃO é bloqueada (`None` e log só com o tipo
    do erro)."""
    try:
        semana, passado = _week_and_past(cur, aluno, settings, hoje)
        corpo = _preview_payload(linha, {}, semana, hoje, passado)
    except Exception as erro:  # noqa: BLE001 - auditoria best-effort: nunca impede publicar
        logger.warning("nutrition.published_preview_failed error=%s", type(erro).__name__)
        return None
    corpo.pop("plan", None)
    corpo.pop("user", None)
    return jsonable_encoder(corpo)


def _preview_within_cap(previa: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """A prévia só vai para o banco dentro de `PUBLISHED_PREVIEW_MAX_BYTES`; acima, `None` e o log leva só o tamanho."""
    if previa is None:
        return None
    tamanho = len(json.dumps(previa, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
    if tamanho > PUBLISHED_PREVIEW_MAX_BYTES:
        logger.warning("nutrition.published_preview_too_large bytes=%s cap=%s", tamanho, PUBLISHED_PREVIEW_MAX_BYTES)
        return None
    return previa


def insert_draft(cur: Any, aluno: int, actor_id: int, settings: PlanSettings, notes: Optional[str]) -> dict[str, Any]:
    """Grava `settings` como RASCUNHO do aluno e devolve a linha (o mesmo INSERT de `POST /plans`)."""
    cur.execute(_INSERT_SQL, (int(aluno), int(actor_id), DRAFT, *_settings_write_values(settings, notes)))
    novo_id = cur.lastrowid
    if not novo_id:  # pragma: no cover - defesa: Data API sempre devolve o id gerado
        raise _error(500, "plan_insert_failed", "Plano não encontrado após gravar.")
    return _fetch_plan(cur, int(novo_id))


def publish_draft(
    conn: Any,
    linha: dict[str, Any],
    aluno: int,
    actor_id: int,
    expected_current_id: Optional[int],
    expected_updated_at: Optional[str] = None,
) -> tuple[dict[str, Any], Any, list[dict[str, Any]]]:
    """Rascunho -> vigente a partir de HOJE (dia civil de quem é dono do plano). Concorrência
    otimista: a vigente é travada (`FOR UPDATE`) e comparada com `expected_current_id`; outra
    publicação no meio -> 409 `publish_conflict`. Materializa a janela de publicação do motor
    (`publication_window`): nenhuma meta futura não congelada fica com o plano antigo; o passado
    fica intacto. É o MESMO caminho da rota `POST /plans/{id}/publish` e do aceite do check-in
    semanal (N7). Devolve (plano publicado, janela, metas)."""
    plan_id = int(linha["id"])
    with conn.cursor() as cur:
        _require_draft(linha)
        if expected_updated_at is not None and expected_updated_at != _iso(linha.get("updated_at")):
            raise _error(
                409,
                "draft_changed",
                "O rascunho foi alterado depois que a tela o carregou; recarregue e confira antes de publicar.",
                current_updated_at=_iso(linha.get("updated_at")),
            )
        cur.execute(_CURRENT_FOR_UPDATE_SQL, (aluno, PUBLISHED))
        vigente = cur.fetchone()
        vigente_id = int(vigente["id"]) if vigente else None
        if vigente_id != expected_current_id:
            raise _error(
                409,
                "publish_conflict",
                "O plano vigente mudou desde que a tela carregou; recarregue antes de publicar.",
                current_id=vigente_id,
            )
    hoje = _today_for(conn, aluno)
    settings = settings_from_row(linha)
    _plan_checks(conn, aluno, settings, hoje)
    with conn.cursor() as cur:
        janela = publication_window(hoje, last_target_date(cur, aluno))
        entradas = load_user_inputs(cur, aluno, janela.date_from, janela.date_to, hoje, [settings])
        comp_hoje = compute_day(entradas, settings, hoje, hoje)
        if not comp_hoje.target.computable:
            raise subject_incomplete_error(comp_hoje)
        agora = db_now(cur)
        # Registro de auditoria da publicação (106): a prévia aprovada (todos os modos) e a versão do algoritmo (só no adaptativo).
        previa = _preview_within_cap(published_preview(cur, aluno, linha, settings, hoje))
        try:
            cur.execute(_SUPERSEDE_SQL, (agora, aluno, PUBLISHED, plan_id))
            parametros = (
                PUBLISHED, hoje, agora, int(actor_id), ENGINE_VERSION, settings.energy_model_version, plan_id, DRAFT
            )
            if expected_updated_at is not None:
                # A versão lida entra no WHERE: edição concorrente depois da conferência vira 0 linhas (409).
                cur.execute(_PUBLISH_IF_UNCHANGED_SQL, (*parametros, linha.get("updated_at")))
            else:
                cur.execute(_PUBLISH_SQL, parametros)
        except Exception as exc:  # noqa: BLE001 - corrida no UNIQUE da vigente vira 409, não 500
            if is_duplicate_key_error(exc):
                raise _error(409, "publish_conflict", "Outro plano foi publicado agora; recarregue.") from exc
            raise
        if int(cur.rowcount or 0) == 0:
            if expected_updated_at is not None:
                raise _error(409, "draft_changed", "O rascunho foi alterado, publicado ou apagado durante a publicação; recarregue.")
            raise _error(409, "plan_not_draft", "O rascunho já foi publicado ou apagado; recarregue.")
    metas = ensure_daily_targets(conn, aluno, janela, today=hoje, inputs=entradas)
    with conn.cursor() as cur:
        if previa is not None:  # auditoria: gravação à parte e protegida (nunca derruba a publicação)
            try:
                cur.execute(_PUBLISHED_PREVIEW_SQL, (previa, plan_id))
            except Exception as erro:  # noqa: BLE001
                logger.warning("nutrition.published_preview_write_failed error=%s", type(erro).__name__)
        publicado = _fetch_plan(cur, plan_id)
    return publicado, janela, metas


@router.post("/plan-preview")
def preview_unsaved_plan(payload: NutritionPlanIn, identity: Identity = Depends(plan_actor)):
    """Calcula a semana atual com o formulário ainda não salvo, sem gravar nada: mesma validação do
    `POST /plans` (400 com o código do motor) e mesma prévia do plano salvo."""
    settings = _validate_payload(payload)
    aluno = resolve_target_user(identity, payload.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            usuario = fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        with conn.cursor() as cur:
            semana, passado = _week_and_past(cur, aluno, settings, hoje)
    comp_hoje = next(comp for comp in semana if comp.day == hoje)
    if not comp_hoje.target.computable:
        raise subject_incomplete_error(comp_hoje)
    return _preview_payload(None, usuario, semana, hoje, passado)


@router.post("/plans/{plan_id}/publish")
def publish_plan(
    plan_id: int,
    payload: NutritionPlanPublishIn,
    identity: Identity = Depends(plan_actor),
):
    """Rascunho -> vigente a partir de HOJE (`publish_draft`): concorrência otimista por
    `expected_current_id` (409 `publish_conflict`) e janela de publicação do motor."""
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            aluno = resolve_plan_owner(identity, int(linha["user_id"]))
        publicado, janela, metas = publish_draft(
            conn, linha, aluno, identity.user_id, payload.expected_current_id, payload.expected_updated_at
        )
    return {"plan": plan_public(publicado), "window": janela.to_dict(), "targets": metas}


@router.post("/targets/recompute")
def recompute_targets(payload: NutritionTargetsRecomputeIn, identity: Identity = Depends(plan_actor)):
    """Recalcula as metas na janela de recálculo do motor (`recompute_window`)
    com o plano em vigor em cada dia."""
    aluno = resolve_target_user(identity, payload.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        resultado = recompute_window(payload.date_from, payload.date_to, hoje)
        if resultado.window is None:
            codigo = resultado.error or "invalid_range"
            raise _error(
                400,
                codigo,
                nutrition_labels.limitation_label(codigo),
                today=hoje.isoformat(),
                max_days=TARGET_RECOMPUTE_MAX_DAYS,
            )
        janela = resultado.window
        with conn.cursor() as cur:
            planos = load_published_plans(cur, aluno)
        if planos.for_day(janela.date_to) is None:
            raise _no_published_plan("Não há plano publicado.")
        metas = ensure_daily_targets(conn, aluno, janela, today=hoje)
    return {"user_id": aluno, **janela.to_dict(), "targets": metas}


@router.get("/cycle-days/from-template")
def cycle_days_from_template(
    user_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(plan_actor),
):
    """Botão "puxar do Modelo" (D5): os dias da semana com treino no Modelo atribuído viram dia
    alto e os demais baixo (`carb_cycling.propose_day_kinds`). É só uma PROPOSTA: o usuário ajusta
    antes de salvar. Sem Modelo, ou com Modelo sem dia da semana nos slots, não propõe nada e
    explica em `notes`."""
    aluno = resolve_target_user(identity, user_id)
    modelo_id = resolve_active_template_id(aluno)
    notas: list[str] = []
    modelo: Optional[dict[str, Any]] = None
    proposta = None
    dias_treino: Optional[tuple[int, ...]] = None
    if modelo_id is None:
        notas.append(NOTE_TEMPLATE_NOT_ASSIGNED)
    else:
        with _connect() as conn:
            with conn.cursor() as cur:
                _require_plan_tables(cur)
                cur.execute(_TEMPLATE_NAME_SQL, (int(modelo_id),))
                nome = cur.fetchone() or {}
                cur.execute(_TEMPLATE_SLOTS_SQL, (int(modelo_id),))
                slots = [
                    (int(linha["weekday"]) if linha.get("weekday") is not None else None, is_training_slot_type(linha.get("slot_type")))
                    for linha in (cur.fetchall() or [])
                ]
        modelo = {"id": int(modelo_id), "name": nome.get("name")}
        proposta = propose_cycle_from_template(slots)
        dias_treino = training_weekdays_from_template(slots)
        if proposta is None:
            notas.append(template_weekdays_note(slots) or NOTE_TEMPLATE_PARTIAL_WEEKDAYS)
        elif not proposta.cycles:
            notas.append(NOTE_TEMPLATE_NO_CYCLE)
    return {
        "user_id": aluno,
        "template": modelo,
        "training_weekdays": list(dias_treino) if dias_treino is not None else None,
        # Só quando há ciclo: um Modelo que treina todo dia (ou nenhum) devolve null e não apaga o que o usuário marcou.
        "cycle_days": proposta.day_map if proposta is not None and proposta.cycles else None,
        "cycles": bool(proposta and proposta.cycles),
        "notes": nutrition_labels.limitation_items(notas),
    }


# ---------------------------------------------------------------------------
# Rota do aluno
# ---------------------------------------------------------------------------


def _my_week(
    conn: Any, aluno: int, settings: PlanSettings, hoje: date, planos: Optional[Any] = None
) -> tuple[UserInputs, DayComputation]:
    """Entradas da semana + a meta de hoje. Com `planos`, as entradas já cobrem TODOS os planos que valem em algum dia da
    semana (na semana da troca de plano, `ensure_daily_targets` não precisa recarregar o histórico)."""
    janela = week_window(hoje)
    da_semana = [settings]
    if planos is not None:
        da_semana += [em_vigor[1] for dia in janela.days() if (em_vigor := planos.for_day(dia)) is not None]
    with conn.cursor() as cur:
        entradas = load_user_inputs(cur, aluno, janela.date_from, janela.date_to, hoje, da_semana)
    return entradas, compute_day(entradas, settings, hoje, hoje)


@router.get("/me/plan")
def my_plan(identity: Identity = Depends(get_current_identity)):
    """O plano em vigor HOJE do próprio usuário (motor: `plan_in_force`), só
    leitura, o sujeito de hoje e as metas da semana atual (materializa os dias
    não congelados da semana)."""
    aluno = resolve_target_user(identity, None)
    with _connect() as conn:
        with conn.cursor() as cur:
            _require_plan_tables(cur)
            planos = load_published_plans(cur, aluno)
        hoje = _today_for(conn, aluno)
        em_vigor = planos.for_day(hoje)
        if em_vigor is None:
            raise _no_published_plan("Você ainda não tem plano nutricional publicado.")
        plano_id, settings = em_vigor
        with conn.cursor() as cur:
            vigente = _fetch_plan(cur, plano_id)
        janela = week_window(hoje)
        entradas, comp_hoje = _my_week(conn, aluno, settings, hoje, planos)
        linhas = ensure_daily_targets(conn, aluno, janela, today=hoje, inputs=entradas)
    dias = list(janela.days())
    semana = week_rows(linhas, dias)
    return {
        "plan": plan_public(vigente),
        "today": hoje.isoformat(),
        "subject": comp_hoje.subject.to_dict(),
        "goal": _goal(comp_hoje),
        "today_target": semana[dias.index(hoje)],
        "week": [{"local_date": dia.isoformat(), "target": meta} for dia, meta in zip(dias, semana)],
        "missing": nutrition_labels.limitation_items(missing_subject_data(comp_hoje.target)),
    }


__all__ = ["insert_draft", "plan_public", "publish_draft", "router"]
