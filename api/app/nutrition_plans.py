"""Plano nutricional do aluno: rascunho, preview, publicação e metas diárias
(plano de nutrição §8.1, §13 L2b). Vive na api-core (`{proxy+}`).

Rotas (prefixo `/nutrition`):
- `GET    /plans?user_id=`          admin/PT: versões do aluno (vigente, histórico, rascunhos)
- `POST   /plans`                   admin/PT: `NutritionPlanIn` -> rascunho (201)
- `PUT    /plans/{id}`              admin/PT: só rascunho (409 se publicado)
- `DELETE /plans/{id}`              admin/PT: só rascunho (409 se publicado)
- `POST   /plans/{id}/preview`      admin/PT: sujeito, TMB, TDEE, alvo, macros e os 7
                                    dias da semana atual, SEM gravar; 422 lista os
                                    dados do aluno que faltam (códigos do motor)
- `POST   /plans/{id}/publish`      admin/PT: `{expected_current_id}` (a vigente que a tela
                                    viu, ou null) — concorrência otimista: a vigente é lida
                                    com `FOR UPDATE` e, se divergir, 409 `publish_conflict`;
                                    substitui a vigente e materializa a janela de publicação
                                    do motor (passadas intactas)
- `POST   /targets/recompute`       admin/PT: `{user_id, date_from, date_to?}` na janela de
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

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .db import get_conn, is_duplicate_key_error
from .db_helpers import connect_or_503
from .domain.nutrition import ENGINE_VERSION
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition.daily_target import (
    TARGET_RECOMPUTE_MAX_DAYS,
    missing_subject_data,
    publication_window,
    recompute_window,
    week_window,
)
from .domain.nutrition.plan import PlanSettings, PlanStatus, build_plan_settings, goal_summary, plan_state
from .intelligence.dashboard.time_windows import user_today_for
from .nutrition_access import fetch_target_user, require_plan_manager, resolve_target_user
from .nutrition_schema import require_nutrition_tables
from .nutrition_targets_service import (
    DayComputation,
    UserInputs,
    compute_day,
    day_public,
    ensure_daily_targets,
    last_target_date,
    load_published_plans,
    load_user_inputs,
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
from .template_assignments import db_now

router = APIRouter(prefix="/nutrition", tags=["nutrition"])

TABLE = "nutrition_plans"

_PLAN_COLUMNS = (
    "id, user_id, created_by, published_by, status, strategy, advanced, bmr_formula, activity_level, "
    "training_kcal_mode, goal_body_fat_percent, goal_weight_kg, weekly_rate_share, surplus_share, "
    "protein_g_per_kg, fat_kcal_share, carb_min_g_per_kg, meals_per_day, notes, engine_version, "
    "effective_from, superseded_at, published_at, created_at, updated_at"
)

# Ordem das colunas de configuração na escrita (INSERT/UPDATE).
_SETTINGS_WRITE_COLUMNS = (
    "strategy", "advanced", "bmr_formula", "activity_level", "training_kcal_mode", "goal_body_fat_percent",
    "goal_weight_kg", "weekly_rate_share", "surplus_share", "protein_g_per_kg", "fat_kcal_share",
    "carb_min_g_per_kg", "meals_per_day", "notes", "engine_version",
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
    f"UPDATE {TABLE} SET status = %s, effective_from = %s, published_at = %s, published_by = %s, engine_version = %s "
    "WHERE id = %s AND status = %s"
)
_BY_ID_SQL = f"SELECT {_PLAN_COLUMNS} FROM {TABLE} WHERE id = %s"
_BY_USER_SQL = f"SELECT {_PLAN_COLUMNS} FROM {TABLE} WHERE user_id = %s ORDER BY created_at DESC, id DESC"

DRAFT = PlanStatus.DRAFT.value
PUBLISHED = PlanStatus.PUBLISHED.value


def _connect():
    return connect_or_503(get_conn)


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
        carb_cycling_enabled=payload.carb_cycling_enabled,
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
        notes,
        ENGINE_VERSION,
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
        "effective_from": _iso(row.get("effective_from")),
        "superseded_at": _iso(row.get("superseded_at")),
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
        "bmr": meta.bmr.to_dict(),
        "scale_comparison": meta.scale_comparison.to_dict() if meta.scale_comparison else None,
        "tdee": meta.tdee.to_dict(),
        "training": meta.training_kcal.to_dict(),
        "kcal_target": alvo,
        "macros": meta.macros.to_dict(),
        "protein_per_meal_g": meta.protein_per_meal_g,
    }


def _goal(comp: DayComputation) -> dict[str, Any]:
    """Meta de composição do motor (`plan.goal_summary`, D-C)."""
    return goal_summary(comp.settings, comp.subject.subject).to_dict()


def _compute_week(cur: Any, user_id: int, settings: PlanSettings, today: date) -> list[DayComputation]:
    janela = week_window(today)
    entradas = load_user_inputs(cur, user_id, janela.date_from, janela.date_to, today)
    return [compute_day(entradas, settings, dia, today) for dia in janela.days()]


def _subject_incomplete(comp: DayComputation) -> HTTPException:
    return _error(
        422,
        "nutrition_subject_incomplete",
        "Faltam dados do aluno para calcular a meta de hoje.",
        missing=nutrition_labels.limitation_items(missing_subject_data(comp.target)),
        subject=comp.subject.to_dict(),
    )


def _preview_payload(row: dict[str, Any], user: dict[str, Any], semana: list[DayComputation], today: date) -> dict[str, Any]:
    hoje = next(comp for comp in semana if comp.day == today)
    limitacoes: list[str] = []
    for comp in semana:
        limitacoes.extend(comp.target.limitations)
    return {
        "plan": plan_public(row),
        "user": user,
        "today": today.isoformat(),
        "engine_version": ENGINE_VERSION,
        "subject": hoje.subject.to_dict(),
        "target": _target_detail(hoje, today),
        "goal": _goal(hoje),
        "week": [day_public(comp, today) for comp in semana],
        "limitations": nutrition_labels.limitation_items(limitacoes),
    }


# ---------------------------------------------------------------------------
# Rotas admin/PT
# ---------------------------------------------------------------------------


@router.get("/plans")
def list_plans(
    user_id: int = Query(...),
    identity: Identity = Depends(require_plan_manager),
):
    alvo = resolve_target_user(identity, user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            usuario = fetch_target_user(cur, alvo)
            cur.execute(_BY_USER_SQL, (alvo,))
            linhas = cur.fetchall() or []
    return {"user": usuario, "items": [plan_public(linha) for linha in linhas]}


@router.post("/plans", status_code=201)
def create_plan(payload: NutritionPlanIn, identity: Identity = Depends(require_plan_manager)):
    settings = _validate_payload(payload)
    alvo = resolve_target_user(identity, payload.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            fetch_target_user(cur, alvo)
            cur.execute(_INSERT_SQL, (alvo, identity.user_id, DRAFT, *_settings_write_values(settings, payload.notes)))
            novo_id = cur.lastrowid
            if not novo_id:  # pragma: no cover - defesa: Data API sempre devolve o id gerado
                raise _error(500, "plan_insert_failed", "Plano não encontrado após gravar.")
            linha = _fetch_plan(cur, int(novo_id))
    return plan_public(linha)


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, payload: NutritionPlanSettingsIn, identity: Identity = Depends(require_plan_manager)):
    settings = _validate_payload(payload)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            resolve_target_user(identity, int(linha["user_id"]))
            _require_draft(linha)
            cur.execute(_UPDATE_DRAFT_SQL, (*_settings_write_values(settings, payload.notes), int(plan_id), DRAFT))
            if int(cur.rowcount or 0) == 0:
                # Publicado ou apagado por outra chamada entre a leitura e o UPDATE.
                raise _error(409, "plan_not_draft", "O rascunho mudou durante a edição; recarregue.")
            linha = _fetch_plan(cur, plan_id)
    return plan_public(linha)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, identity: Identity = Depends(require_plan_manager)):
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            resolve_target_user(identity, int(linha["user_id"]))
            _require_draft(linha)
            cur.execute(_DELETE_DRAFT_SQL, (int(plan_id), DRAFT))
            if int(cur.rowcount or 0) == 0:
                raise _error(409, "plan_not_draft", "O rascunho mudou durante a exclusão; recarregue.")
    return {"status": "deleted", "id": int(plan_id)}


@router.post("/plans/{plan_id}/preview")
def preview_plan(plan_id: int, identity: Identity = Depends(require_plan_manager)):
    """Calcula a semana atual com ESTE plano, sem gravar nada."""
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            aluno = resolve_target_user(identity, int(linha["user_id"]))
            usuario = fetch_target_user(cur, aluno)
        hoje = _today_for(conn, aluno)
        with conn.cursor() as cur:
            semana = _compute_week(cur, aluno, settings_from_row(linha), hoje)
    comp_hoje = next(comp for comp in semana if comp.day == hoje)
    if not comp_hoje.target.computable:
        raise _subject_incomplete(comp_hoje)
    return _preview_payload(linha, usuario, semana, hoje)


@router.post("/plans/{plan_id}/publish")
def publish_plan(
    plan_id: int,
    payload: NutritionPlanPublishIn,
    identity: Identity = Depends(require_plan_manager),
):
    """Rascunho -> vigente a partir de HOJE (dia do aluno). Concorrência
    otimista: a vigente é travada (`FOR UPDATE`) e comparada com
    `expected_current_id`; outra publicação no meio -> 409 `publish_conflict`.
    Materializa a janela de publicação do motor (`publication_window`): nenhuma
    meta futura não congelada fica com o plano antigo; o passado fica intacto."""
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            linha = _fetch_plan(cur, plan_id)
            aluno = resolve_target_user(identity, int(linha["user_id"]))
            _require_draft(linha)
            cur.execute(_CURRENT_FOR_UPDATE_SQL, (aluno, PUBLISHED))
            vigente = cur.fetchone()
            vigente_id = int(vigente["id"]) if vigente else None
            if vigente_id != payload.expected_current_id:
                raise _error(
                    409,
                    "publish_conflict",
                    "O plano vigente do aluno mudou desde que a tela carregou; recarregue antes de publicar.",
                    current_id=vigente_id,
                )
        hoje = _today_for(conn, aluno)
        settings = settings_from_row(linha)
        with conn.cursor() as cur:
            janela = publication_window(hoje, last_target_date(cur, aluno))
            entradas = load_user_inputs(cur, aluno, janela.date_from, janela.date_to, hoje)
            comp_hoje = compute_day(entradas, settings, hoje, hoje)
            if not comp_hoje.target.computable:
                raise _subject_incomplete(comp_hoje)
            agora = db_now(cur)
            try:
                cur.execute(_SUPERSEDE_SQL, (agora, aluno, PUBLISHED, int(plan_id)))
                cur.execute(_PUBLISH_SQL, (PUBLISHED, hoje, agora, identity.user_id, ENGINE_VERSION, int(plan_id), DRAFT))
            except Exception as exc:  # noqa: BLE001 - corrida no UNIQUE da vigente vira 409, não 500
                if is_duplicate_key_error(exc):
                    raise _error(409, "publish_conflict", "Outro plano foi publicado para este aluno agora; recarregue.") from exc
                raise
            if int(cur.rowcount or 0) == 0:
                raise _error(409, "plan_not_draft", "O rascunho já foi publicado ou apagado; recarregue.")
        metas = ensure_daily_targets(conn, aluno, janela, today=hoje, inputs=entradas)
        with conn.cursor() as cur:
            publicado = _fetch_plan(cur, plan_id)
    return {"plan": plan_public(publicado), "window": janela.to_dict(), "targets": metas}


@router.post("/targets/recompute")
def recompute_targets(payload: NutritionTargetsRecomputeIn, identity: Identity = Depends(require_plan_manager)):
    """Recalcula as metas na janela de recálculo do motor (`recompute_window`)
    com o plano em vigor em cada dia."""
    aluno = resolve_target_user(identity, payload.user_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
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
            raise _no_published_plan("O aluno não tem plano publicado.")
        metas = ensure_daily_targets(conn, aluno, janela, today=hoje)
    return {"user_id": aluno, **janela.to_dict(), "targets": metas}


# ---------------------------------------------------------------------------
# Rota do aluno
# ---------------------------------------------------------------------------


def _my_week(conn: Any, aluno: int, settings: PlanSettings, hoje: date) -> tuple[UserInputs, DayComputation]:
    janela = week_window(hoje)
    with conn.cursor() as cur:
        entradas = load_user_inputs(cur, aluno, janela.date_from, janela.date_to, hoje)
    return entradas, compute_day(entradas, settings, hoje, hoje)


@router.get("/me/plan")
def my_plan(identity: Identity = Depends(get_current_identity)):
    """O plano em vigor HOJE do próprio usuário (motor: `plan_in_force`), só
    leitura, o sujeito de hoje e as metas da semana atual (materializa os dias
    não congelados da semana)."""
    aluno = resolve_target_user(identity, None)
    with _connect() as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            planos = load_published_plans(cur, aluno)
        hoje = _today_for(conn, aluno)
        em_vigor = planos.for_day(hoje)
        if em_vigor is None:
            raise _no_published_plan("Você ainda não tem plano nutricional publicado.")
        plano_id, settings = em_vigor
        with conn.cursor() as cur:
            vigente = _fetch_plan(cur, plano_id)
        janela = week_window(hoje)
        entradas, comp_hoje = _my_week(conn, aluno, settings, hoje)
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


__all__ = ["plan_public", "router"]
