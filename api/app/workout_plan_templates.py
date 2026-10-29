"""CRUD + atribuição + preview de ativação para a feature "Modelos de Ficha
de Treino Personalizados" (Fase 1, sem IA — ver plano completo em
melhoria-eager-origami.md, seções 1 e 2).

Todos os endpoints exigem `require_admin_or_pt` (admin ou personal_trainer).
Personal Trainer é um papel amplo nesta fase (sem vínculo formal PT<->aluno):
vê/atribui modelos para qualquer usuário.

Mesmo padrão de conexão de `workout_plans.py`/`services.py` — `.db.transaction()`
(pymysql com `DictCursor`, `autocommit=False`), não a conexão própria usada por
`gyms.py`/`exercise_catalog_admin.py`, já que aqui usamos `Depends(...)` do
FastAPI normalmente (sem necessidade de resolver identidade manualmente a
partir do header).

Regras de negócio (seção 1.6 do plano) centralizadas em
`_validate_template_payload`: descanso sem itens de grupo muscular, máx. 3
priorizados por slot e rejeição de item_kind 'exercise' (removido a partir da
Fase 1 revisada — modelos trabalham só com grupo muscular/cardio) — sem
CHECK/trigger no banco, mesmo padrão do restante do projeto.

LOTE 1 (segundo modo de operação, ortogonal ao motor v2 por % de orçamento):
`volume_mode` ('percent_budget', default, ou 'equipment_series') no
cabeçalho do template decide qual item_kind é aceito em cada slot —
'muscle_group' só em 'percent_budget', 'equipment' (grupo muscular +
equipamento + exercício + séries fixas) só em 'equipment_series'; 'cardio'
continua permitido em ambos.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import async_jobs, request_memo, template_drafts
from .advanced_techniques import ensure_valid_codes
from .db import is_duplicate_key_error, transaction
from .domain import cardio_prescription
from .domain.free_exercise_item import FREE_EXERCISE_PRIMARY_WEIGHT, is_free_exercise_item
from .domain.set_measures import (
    MEASURE_ONLY_ON_EQUIPMENT,
    declares_non_rep_measure,
    has_advanced_block,
    item_validation_message,
    validate_item_measure,
)
from .models import (
    CardioPrescriptionCheckIn,
    PeriodizationDraftIn,
    WorkoutPlanTemplateAssignIn,
    WorkoutPlanTemplateIn,
    WorkoutPlanTemplateSlotIn,
    WorkoutPlanTemplateSlotItemIn,
    counting_mode_value,
)
from .periodization.limits import MAX_PRIORITIZED_PER_SLOT
from .security import Identity, get_current_identity, require_admin_or_pt
from .template_safety_validation import (
    build_safety_report,
    has_safety_relevant_input,
    split_report_by_authorship,
)
from .template_versions import (
    TemplateVersionConflict,
    canonical_header,
    canonical_intent,
    canonical_slots,
    counting_mode_of,
    current_version,
    load_version_tree,
    resolve_version_now,
    revert_to_version,
    training_goal_of,
    version_of_template,
    write_structural_version,
)

logger = logging.getLogger("fitness.workout_plan_templates")

# Teto de slots de um Modelo: a grade do editor tem no máximo 2 linhas x 4
# colunas. Modelo legado com mais slots gravados continua editável, mas não
# cresce (`max_slots` calculado no PUT).
MAX_TEMPLATE_SLOTS = 8

router = APIRouter(
    prefix="/workout-plan-templates",
    tags=["workout-plan-templates"],
    dependencies=[Depends(require_admin_or_pt)],
)

# Router SEM require_admin_or_pt: qualquer usuário autenticado (inclusive
# role='regular') pode ver os PRÓPRIOS modelos atribuídos — necessário para o
# popup "Gerar novo plano" (WorkoutPlansClient.tsx) mostrar os modelos do
# usuário logado antes de ele escolher explicitamente qual usar em
# POST /workout-plans/generate (payload.template_id).
me_router = APIRouter(prefix="/workout-plan-templates", tags=["workout-plan-templates"])


@me_router.get("/hr-zones")
def get_template_hr_zones(
    template_id: Optional[int] = Query(None, ge=1),
    identity: Identity = Depends(get_current_identity),
):
    """Zonas de FC que o editor do Modelo mostra ao lado do seletor de zona
    (M98, correção da C1). DE QUEM são as zonas é regra de autorização de
    `athlete_hr_zones.resolve_template_zones_subject`: admin -> as do logado;
    aluno -> as próprias; personal trainer -> as do aluno atribuído ao Modelo
    (só aluno vinculado). `template_id` ausente = Modelo novo, ainda sem
    aluno. Exige sessão (mesma regra de `/users/me/hr-zones`).

    Fica no `me_router` (sem `require_admin_or_pt`) para o aluno receber as
    próprias zonas, e vem antes de `/{template_id}` (o `me_router` é incluído
    primeiro em `main.py`)."""
    from . import athlete_hr_zones
    from .users import require_session_identity

    require_session_identity(identity)
    with transaction() as conn:
        with conn.cursor() as cur:
            subject = athlete_hr_zones.resolve_template_zones_subject(cur, identity, template_id)
            athlete = (
                athlete_hr_zones.load_athlete_zones(cur, subject.user_id) if subject.user_id is not None else None
            )
    return {**athlete_hr_zones.zones_payload(athlete), "subject": subject.to_dict()}


@me_router.get("/assigned-to-me")
def list_my_assigned_templates(identity: Identity = Depends(get_current_identity)):
    """Modelos VIGENTES do usuário — depois do M23 (migração 049), no máximo
    UM. A lista continua sendo uma lista (o contrato do frontend não muda e o
    popup "Gerar novo plano" segue funcionando sem alteração), mas ela deixa
    de oferecer modelos que o usuário já trocou: era exatamente isso que fazia
    o acompanhamento e a geração seguirem modelos diferentes."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, t.name, t.description, t.template_type
                FROM workout_plan_template_assignments a
                JOIN workout_plan_templates t ON t.id = a.template_id
                WHERE a.user_id = %s AND a.unassigned_at IS NULL AND t.is_active = 1
                  AND t.deleted_at IS NULL
                ORDER BY a.assigned_at DESC
                """,
                (identity.user_id,),
            )
            templates = cur.fetchall()

            # Slots lightweight (sem itens) de cada template atribuído —
            # necessário para o frontend montar o seletor "A partir de qual
            # slot?" (escolha manual) no popup "Gerar novo plano" sem precisar
            # de outra chamada (WorkoutPlansClient.tsx).
            template_ids = [t["id"] for t in templates]
            slots_by_template: dict[int, list[dict[str, Any]]] = defaultdict(list)
            if template_ids:
                placeholders = ",".join(["%s"] * len(template_ids))
                cur.execute(
                    f"""
                    SELECT s.template_id AS template_id, s.sequence AS sequence,
                           s.name AS name, s.slot_type AS slot_type
                    FROM workout_plan_template_slots s
                    -- So a versao VIGENTE (065): as anteriores sao historico.
                    JOIN workout_plan_template_versions v
                      ON v.id = s.version_id AND v.current_template_id = s.template_id
                    WHERE s.template_id IN ({placeholders})
                    ORDER BY s.sequence ASC
                    """,
                    tuple(template_ids),
                )
                for row in cur.fetchall():
                    slots_by_template[row["template_id"]].append(
                        {"sequence": row["sequence"], "name": row["name"], "slot_type": row["slot_type"]}
                    )

            for template in templates:
                template["slots"] = slots_by_template.get(template["id"], [])

            return {"items": templates}


class WorkoutPlanTemplatePreviewIn(BaseModel):
    """Payload do preview de ativação semanal: slots EM MEMÓRIA (ainda não
    salvos), mesmo formato de `WorkoutPlanTemplateIn.slots`. Definido aqui (e
    não em models.py) por ser um DTO auxiliar só deste endpoint, sem uso em
    nenhum outro lugar do contrato — os 4 modelos pedidos no plano (seção 2.6)
    ficam em models.py."""

    slots: list[WorkoutPlanTemplateSlotIn] = Field(default_factory=list)
    # Mesmo Literal de WorkoutPlanTemplateIn.volume_mode. 'percent_budget'
    # (default) preserva o comportamento legado (soma de sets_count);
    # 'equipment_series' usa VolumeService.get_muscle_activation_for_exercises
    # (mesmo motor de POST /analytics/muscle-activation-preview) sobre os
    # itens item_kind='equipment' de cada slot — ver
    # _compute_slot_activation_equipment_series.
    volume_mode: Literal["percent_budget", "equipment_series"] = "percent_budget"
    # Aditivo e opcional: tipo de treino do Modelo em edição. Só o modo
    # 'percent_budget' usa: com ele, o item 'muscle_group' cujo grupo tem meta
    # entra no preview SEMPRE pelo PONTO MÉDIO projetado (`sets_count` é
    # ignorado, como no motor v2) (meta semanal x (min+max)/2 / 100) e a resposta
    # ganha `item_budgets`. Ausente = comportamento anterior.
    training_type_id: Optional[int] = None


class MethodologyVolumePreviewIn(WorkoutPlanTemplatePreviewIn):
    """Payload de `POST /methodology-volume-preview` (Fase 4, PR E): os MESMOS
    `slots` + `volume_mode` do preview de ativacao (o builder ja monta esse
    payload minimo em `activation-preview-payload.ts`), mais a intencao
    declarada - que ate o PR H viaja como parametro, default `standard`.
    Nada e salvo."""

    intent: Literal["standard", "specialization", "maintenance", "cutting"] = "standard"
    # Grupos-alvo da intencao (fatia ou pai; o motor resolve para o bloco).
    target_group_ids: list[int] = Field(default_factory=list)
    # Modo de contagem do volume (073). Precedencia: este campo > o modo
    # PERSISTIDO na versao vigente do Modelo `template_id` > `primary_only`.
    volume_counting_mode: Optional[str] = None
    # Modelo em edicao (opcional): so para herdar o modo gravado quando o
    # corpo nao traz `volume_counting_mode`.
    template_id: Optional[int] = None

    @field_validator("volume_counting_mode")
    @classmethod
    def _counting_mode_valido(cls, value: Optional[str]) -> Optional[str]:
        return counting_mode_value(value)


# ---------------------------------------------------------------------------
# Validação de negócio (seção 1.6 do plano)
# ---------------------------------------------------------------------------


def _validate_technique_codes(payload: WorkoutPlanTemplateIn) -> None:
    """Códigos de técnica contra o catálogo `advanced_techniques` (422 para
    inexistente/inativo). Separado de `_validate_template_payload`, que é puro
    (sem banco); sem técnica no payload não há leitura alguma."""
    ensure_valid_codes(item.advanced_technique for slot in payload.slots for item in slot.items)


def clear_sets_only_technique_fields(payload: WorkoutPlanTemplateIn, catalog: Any = None) -> list[dict[str, Any]]:
    """FST-7/GVT (Fase 5, PR 5): técnica que só preenche o número de séries
    (`default_sets_count` no catálogo) não tem aplicações nem pausa próprias.
    Zera (`None`) os dois campos desses itens e devolve o que mudou -
    `{slot, item, technique, applications, duration_seconds}` com os valores
    ANTERIORES -, para quem chama registrar (o validador da IA vira auto-fix).
    `sets_count` não é tocado: o builder o preenche com o padrão e ele continua
    editável. Sem item com técnica + campo preenchido, não lê o catálogo."""
    from .domain.technique_accounting import is_sets_only_technique
    from .intelligence.technique_volume import new_catalog

    if catalog is None:
        catalog = new_catalog()
    changes: list[dict[str, Any]] = []
    for slot in payload.slots:
        for item in slot.items:
            if not item.advanced_technique:
                continue
            if item.advanced_technique_applications is None and item.advanced_technique_duration_seconds is None:
                continue
            if not is_sets_only_technique(item.advanced_technique, catalog):
                continue
            changes.append(
                {
                    "slot": slot.sequence,
                    "item": item.item_order,
                    "technique": item.advanced_technique,
                    "applications": item.advanced_technique_applications,
                    "duration_seconds": item.advanced_technique_duration_seconds,
                }
            )
            item.advanced_technique_applications = None
            item.advanced_technique_duration_seconds = None
    return changes


def _validate_item_measure(slot_name: str, item: WorkoutPlanTemplateSlotItemIn) -> None:
    """Medida da serie do item (Lote T2): a forma e a do dono
    (`set_measures.validate_item_measure`); a primeira violacao vira 422 com a
    mensagem do dono."""
    if item.item_kind != "equipment":
        if declares_non_rep_measure(item.measure, item.duration_seconds_target, item.time_window_seconds):
            raise HTTPException(
                status_code=422,
                detail=f"Slot '{slot_name}': {item_validation_message(MEASURE_ONLY_ON_EQUIPMENT)}.",
            )
        return
    errors = validate_item_measure(
        item.measure,
        item.duration_seconds_target,
        item.time_window_seconds,
        item.reps_target,
        has_advanced_block(item),
    )
    if errors:
        raise HTTPException(
            status_code=422,
            detail=f"Slot '{slot_name}': {item_validation_message(errors[0])}.",
        )


def _validate_template_payload(payload: WorkoutPlanTemplateIn, max_slots: int = MAX_TEMPLATE_SLOTS) -> None:
    if len(payload.slots) < 1:
        raise HTTPException(
            status_code=422,
            detail="Um modelo precisa de pelo menos 1 slot.",
        )
    if len(payload.slots) > max_slots:
        raise HTTPException(
            status_code=422,
            detail=f"Um modelo pode ter no máximo {max_slots} slots.",
        )
    if payload.grid_rows is not None and payload.grid_cols is not None:
        capacity = payload.grid_rows * payload.grid_cols
        if len(payload.slots) > capacity:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"A grade {payload.grid_rows}x{payload.grid_cols} comporta {capacity} slots, "
                    f"mas o modelo tem {len(payload.slots)}."
                ),
            )
    sequences = sorted(slot.sequence for slot in payload.slots)
    if sequences != list(range(1, len(payload.slots) + 1)):
        raise HTTPException(
            status_code=422,
            detail=f"Os slots devem ter sequence únicos de 1 a {len(payload.slots)}.",
        )

    for slot in payload.slots:
        kinds_present = {item.item_kind for item in slot.items}

        # Faixa de prioridade percentual do slot: opcional, mas quando
        # informada exige os dois lados preenchidos e 0 <= min <= max <= 100.
        # Apenas captura nesta fase — não consumido pelo motor de geração.
        has_min = slot.priority_min_pct is not None
        has_max = slot.priority_max_pct is not None
        if has_min != has_max:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Slot '{slot.name}': faixa de prioridade exige mínimo e máximo "
                    "preenchidos juntos (ou nenhum dos dois)."
                ),
            )
        if has_min and has_max:
            if not (0 <= slot.priority_min_pct <= 100) or not (0 <= slot.priority_max_pct <= 100):
                raise HTTPException(
                    status_code=422,
                    detail=f"Slot '{slot.name}': faixa de prioridade deve estar entre 0 e 100.",
                )
            if slot.priority_min_pct > slot.priority_max_pct:
                raise HTTPException(
                    status_code=422,
                    detail=f"Slot '{slot.name}': prioridade mínima não pode ser maior que a máxima.",
                )

        if slot.slot_type == "descanso":
            if kinds_present - {"cardio"}:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Slot '{slot.name}' (descanso) não pode ter itens de grupo muscular "
                        "ou exercício — apenas cardio é permitido."
                    ),
                )

        prioritized_count = sum(1 for item in slot.items if item.prioritize)
        if prioritized_count > MAX_PRIORITIZED_PER_SLOT:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Slot '{slot.name}': no máximo {MAX_PRIORITIZED_PER_SLOT} itens podem ser "
                    "marcados como priorizados."
                ),
            )

        for item in slot.items:
            if item.item_kind != "equipment" and (
                item.advanced_technique is not None
                or item.warmup_reps is not None
                or item.feeler_set_reps is not None
                or item.peak_contraction_seconds is not None
                or item.rest_seconds is not None
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Slot '{slot.name}': técnica avançada, aquecimento configurado, série de "
                        "ajuste, pico de contração e descanso por exercício só são suportados em "
                        "item_kind='equipment' (modo 'equipment_series')."
                    ),
                )

            if item.item_kind != "equipment":
                _validate_item_measure(slot.name, item)

            if item.item_kind == "muscle_group":
                if payload.volume_mode == "equipment_series":
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Slot '{slot.name}': modo 'equipment_series' não permite itens de "
                            "grupo muscular (item_kind='muscle_group') — use item_kind='equipment'."
                        ),
                    )
                if not item.muscle_group_id or item.mapping_id:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Slot '{slot.name}': item de grupo muscular exige muscle_group_id "
                            "e não pode ter mapping_id."
                        ),
                    )
                # Motor de ficha v2: priority_min_pct/priority_max_pct são
                # opcionais (item ainda não configurado para o v2 fica
                # UNDER_ALLOCATED, não é erro de validação), mas quando
                # informados precisam ser uma faixa 0-100 coerente.
                if item.priority_min_pct is not None and not (0 <= item.priority_min_pct <= 100):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Slot '{slot.name}': priority_min_pct deve estar entre 0 e 100.",
                    )
                if item.priority_max_pct is not None and not (0 <= item.priority_max_pct <= 100):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Slot '{slot.name}': priority_max_pct deve estar entre 0 e 100.",
                    )
                if (
                    item.priority_min_pct is not None
                    and item.priority_max_pct is not None
                    and item.priority_min_pct > item.priority_max_pct
                ):
                    raise HTTPException(
                        status_code=422,
                        detail=f"Slot '{slot.name}': priority_min_pct não pode ser maior que priority_max_pct.",
                    )
            elif item.item_kind == "exercise":
                # Fase 1 revisada: modelos trabalham só com grupos musculares
                # (e cardio) — itens de exercício específico deixaram de ser
                # suportados na criação/edição. Templates antigos que já
                # tinham itens 'exercise' continuam legíveis via GET, mas um
                # novo save (POST/PUT) com esse item_kind é rejeitado aqui;
                # o usuário precisa removê-los manualmente na UI ao editar.
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Slot '{slot.name}': item_kind 'exercise' não é mais suportado — "
                        "use apenas 'muscle_group', 'cardio' ou 'equipment'."
                    ),
                )
            elif item.item_kind == "equipment":
                # Item do modo volume_mode='equipment_series' (LOTE 1): grupo
                # muscular + equipamento + exercício + séries fixas, sem % de
                # orçamento — usuário escolhe tudo, IA não decide nada.
                if payload.volume_mode != "equipment_series":
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Slot '{slot.name}': item_kind 'equipment' só é permitido quando "
                            "volume_mode='equipment_series'."
                        ),
                    )
                # M101b: o item exige só o grupo. Vale o PAR completo
                # (exercício + equipamento) ou o EXERCÍCIO LIVRE (os dois
                # vazios: a geração escolhe o exercício do grupo e o
                # equipamento vem junto, `generation/mode_b._resolve_free_exercise`).
                if not item.muscle_group_id:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Slot '{slot.name}': item de equipamento exige muscle_group_id preenchido.",
                    )
                if bool(item.equipment_id) != bool(item.mapping_id):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Slot '{slot.name}': item de equipamento exige o par equipamento + exercício "
                            "completo, ou os dois vazios (exercício livre: o motor escolhe)."
                        ),
                    )
                if item.sets_count is None or item.sets_count < 1:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Slot '{slot.name}': item de equipamento exige sets_count >= 1.",
                    )
                _validate_item_measure(slot.name, item)
            elif item.item_kind == "cardio":
                if item.muscle_group_id or item.mapping_id:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Slot '{slot.name}': item de cardio não usa muscle_group_id/mapping_id.",
                    )
                # M98 C0: a prescrição (Normal/HIIT, mínimos, tetos, zonas) é do
                # dono `domain/cardio_prescription.py`; aqui só vira 422.
                try:
                    cardio_prescription.validate(item)
                except cardio_prescription.CardioPrescriptionError as exc:
                    raise HTTPException(status_code=422, detail=f"Slot '{slot.name}': {exc}") from exc
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"Slot '{slot.name}': item_kind inválido '{item.item_kind}'.",
                )


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _current_version_ref(row: dict[str, Any]) -> dict[str, Any]:
    """Troca as colunas planas da versao vigente por `current_version`
    (`{"id", "number"}`, ou `None` para Modelo sem versao - so o codigo
    antigo, na janela de deploy da 065, cria um assim)."""
    version_id = row.pop("current_version_id", None)
    number = row.pop("current_version_number", None)
    row["current_version"] = (
        {"id": int(version_id), "number": int(number)} if version_id is not None else None
    )
    return row


def _list_templates(is_active: Optional[bool]) -> list[dict[str, Any]]:
    # Excluido logicamente (065) nao aparece na lista.
    where = ["t.deleted_at IS NULL"]
    params: list[Any] = []
    if is_active is not None:
        where.append("t.is_active = %s")
        params.append(1 if is_active else 0)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    t.id, t.name, t.description, t.template_type, t.training_type_id,
                    t.volume_mode, t.volume_counting_mode, t.training_goal, t.is_active, t.origin,
                    t.grid_rows, t.grid_cols, t.created_by_user_id, u.username AS created_by_username,
                    t.created_at, t.updated_at,
                    cv.id AS current_version_id, cv.version_number AS current_version_number,
                    COUNT(a.id) AS assignments_count
                FROM workout_plan_templates t
                LEFT JOIN users u ON u.id = t.created_by_user_id
                LEFT JOIN workout_plan_template_versions cv ON cv.current_template_id = t.id
                -- Só as VIGENTES (M23): na lista de modelos, "3 atribuições"
                -- tem de significar "3 pessoas seguindo este modelo hoje".
                -- Contar o histórico faria o número só crescer, para sempre.
                LEFT JOIN workout_plan_template_assignments a
                       ON a.template_id = t.id AND a.unassigned_at IS NULL
                {where_sql}
                GROUP BY t.id, t.name, t.description, t.template_type, t.training_type_id,
                    t.volume_mode, t.volume_counting_mode, t.training_goal, t.is_active, t.origin,
                    t.grid_rows, t.grid_cols, t.created_by_user_id, u.username, t.created_at, t.updated_at,
                    cv.id, cv.version_number
                ORDER BY t.created_at DESC
                """,
                tuple(params),
            )
            rows = [_current_version_ref(dict(row)) for row in (cur.fetchall() or [])]
            # Rascunho (067): so o selo (`has_draft`/`draft_updated_at`) para o
            # badge da lista - uma consulta para todos, tolerante a falha.
            stamps = template_drafts.draft_stamps(cur, [int(row["id"]) for row in rows])
            return [template_drafts.attach_draft_flags(row, stamps) for row in rows]


# Nome do memo por requisicao (`request_memo`) das leituras do Modelo.
TEMPLATE_MEMO = "workout_plan_template"


def _load_version_tree(cur, version_id: int) -> list[dict[str, Any]]:
    """Slots (com `items`) de UMA versao, na forma de `_get_template`. Unico
    lugar que le a arvore de uma versao para a API: o detalhe do Modelo (a
    vigente) e o detalhe de uma versao do historico (PR4) passam por aqui."""
    return load_version_tree(cur, int(version_id))


def _get_template(template_id: int, *, as_of: Optional[Any] = None) -> Optional[dict[str, Any]]:
    """Detalhe completo do Modelo, lido UMA vez por requisicao GET (PERF-01,
    `request_memo`): dashboard, aderencia e relatorio chamam `get_template_if_assigned`
    na mesma chamada e relam ~7 statements cada vez. Fora de escopo (escritas,
    geracao, jobs) le o banco a cada chamada, como sempre ("vinculo vivo")."""
    from .template_assignments import _as_moment

    momento = _as_moment(as_of) if as_of is not None else None
    return request_memo.memoize(
        (TEMPLATE_MEMO, int(template_id), momento), lambda: _read_template(template_id, as_of=as_of)
    )


def _read_template(template_id: int, *, as_of: Optional[Any] = None) -> Optional[dict[str, Any]]:
    """Detalhe completo (slots + itens + assignments). Reaproveitada por
    `workout_plan_templates_engine.get_active_assigned_template` — mantém uma
    única fonte de verdade para "o que é um template completo".

    Versionamento (065): os slots são os da versão VIGENTE, e a resposta
    traz `current_version` (`{"id", "number"}`). Modelo excluído logicamente
    (`deleted_at`) é `None` — para quem lê, ele não existe mais (404 nas
    rotas, fora da geração).

    `as_of` (PR3, leitura HISTÓRICA): slots e cabeçalho estrutural
    (`volume_mode`, `training_type_id`, `template_type`, grade) da versão que
    vigia NAQUELE instante (uma data vira o fim do dia), e a resposta ganha
    `version` (`{"id", "number"}` da versão lida). Sem versão naquele instante
    (Modelo criado depois), a vigente. MESMOS statements: a versão da data
    entra por JOIN no cabeçalho."""
    from .template_assignments import _as_moment
    from .template_versions import version_at_condition

    momento = _as_moment(as_of) if as_of is not None else None
    with transaction() as conn:
        with conn.cursor() as cur:
            if momento is None:
                cur.execute(
                    """
                    SELECT t.id, t.name, t.description, t.template_type, t.training_type_id, t.volume_mode,
                           t.volume_counting_mode, t.training_goal, t.is_active, t.origin, t.generation_log_id,
                           t.grid_rows, t.grid_cols,
                           t.created_by_user_id, t.created_at, t.updated_at,
                           cv.id AS current_version_id, cv.version_number AS current_version_number
                    FROM workout_plan_templates t
                    LEFT JOIN workout_plan_template_versions cv ON cv.current_template_id = t.id
                    WHERE t.id = %s AND t.deleted_at IS NULL
                    """,
                    (template_id,),
                )
            else:
                cur.execute(
                    f"""
                    SELECT t.id, t.name, t.description, t.template_type, t.training_type_id, t.volume_mode,
                           t.volume_counting_mode, t.training_goal, t.is_active, t.origin, t.generation_log_id,
                           t.grid_rows, t.grid_cols,
                           t.created_by_user_id, t.created_at, t.updated_at,
                           cv.id AS current_version_id, cv.version_number AS current_version_number,
                           av.id AS as_of_version_id, av.version_number AS as_of_version_number,
                           av.template_type AS as_of_template_type, av.training_type_id AS as_of_training_type_id,
                           av.volume_mode AS as_of_volume_mode, av.grid_rows AS as_of_grid_rows,
                           av.grid_cols AS as_of_grid_cols, av.volume_counting_mode AS as_of_volume_counting_mode,
                           av.training_goal AS as_of_training_goal
                    FROM workout_plan_templates t
                    LEFT JOIN workout_plan_template_versions cv ON cv.current_template_id = t.id
                    LEFT JOIN workout_plan_template_versions av
                           ON av.template_id = t.id AND {version_at_condition("av")}
                    WHERE t.id = %s AND t.deleted_at IS NULL
                    """,
                    (momento, momento, template_id),
                )
            template = cur.fetchone()
            if not template:
                return None
            template = _current_version_ref(dict(template))
            current_version = template["current_version"]
            if momento is not None:
                as_of_id = template.pop("as_of_version_id", None)
                as_of_number = template.pop("as_of_version_number", None)
                header = {key: template.pop(f"as_of_{key}", None) for key in (
                    "template_type", "training_type_id", "volume_mode", "grid_rows", "grid_cols",
                    "volume_counting_mode", "training_goal",
                )}
                if as_of_id is not None:
                    template.update(header)
                    current_version = {"id": int(as_of_id), "number": int(as_of_number)}
                template["version"] = current_version

            # Arvore da versao (PR4: a mesma leitura serve o detalhe de uma
            # versao do historico - `_load_version_tree`).
            template["slots"] = (
                request_memo.memoize(
                    (TEMPLATE_MEMO, "tree", int(current_version["id"])),
                    lambda: _load_version_tree(cur, current_version["id"]),
                )
                if current_version
                else []
            )

            def _load_assignments() -> list[dict[str, Any]]:
                cur.execute(
                    """
                    -- HISTÓRICO INTEIRO de propósito (vigentes e encerradas).
                    -- `get_template_if_assigned` (validação de posse do motor de
                    -- geração e dos blocos de Modelo do dashboard) lê daqui: um
                    -- ciclo antigo continua sendo legível depois que o usuário
                    -- trocou de Modelo, que é a metade "sem perder as evoluções"
                    -- do M23. Quem quer só quem segue o modelo HOJE filtra por
                    -- `unassigned_at IS NULL` (é o que faz `list_assignments`).
                    SELECT a.id, a.user_id, u.username, u.full_name,
                           a.assigned_by_user_id, a.assigned_at,
                           a.unassigned_at, a.unassigned_by_user_id
                    FROM workout_plan_template_assignments a
                    JOIN users u ON u.id = a.user_id
                    WHERE a.template_id = %s
                    ORDER BY a.assigned_at DESC, a.id DESC
                    """,
                    (template_id,),
                )
                return cur.fetchall()

            template["assignments"] = request_memo.memoize(
                (TEMPLATE_MEMO, "assignments", int(template_id)), _load_assignments
            )
            template["methodology_intent"] = request_memo.memoize(
                (TEMPLATE_MEMO, "intent", int(template_id)), lambda: _load_methodology_intent(cur, template_id)
            )

    return template


# ---------------------------------------------------------------------------
# Intencao declarada da metodologia de volume (Fase 4, PR H; migracao 056).
# Sem linha = `standard`. A leitura e tolerante a falha de proposito: ela e
# uma segunda leitura informativa anexada ao Modelo e NUNCA pode derrubar o
# `_get_template` - e o que mantem a janela inversa (codigo novo, 056 ainda
# nao aplicada) segura: o Modelo continua legivel, com `standard`.
# ---------------------------------------------------------------------------

METHODOLOGY_INTENT_DEFAULT: dict[str, Any] = {"intent": "standard", "target_group_ids": []}


def _load_methodology_intent(cur, template_id: int) -> dict[str, Any]:
    try:
        cur.execute(
            "SELECT intent FROM workout_plan_template_methodology_intent WHERE template_id = %s",
            (template_id,),
        )
        row = cur.fetchone()
        if not row:
            return dict(METHODOLOGY_INTENT_DEFAULT, target_group_ids=[])
        cur.execute(
            """
            SELECT muscle_group_id FROM workout_plan_template_methodology_intent_targets
            WHERE template_id = %s ORDER BY muscle_group_id ASC
            """,
            (template_id,),
        )
        targets = [int(r["muscle_group_id"]) for r in (cur.fetchall() or [])]
        return {"intent": str(row["intent"]), "target_group_ids": targets}
    except Exception:  # noqa: BLE001 - leitura informativa, nunca derruba o Modelo
        logger.warning("methodology_intent indisponivel para o modelo %s", template_id, exc_info=True)
        return dict(METHODOLOGY_INTENT_DEFAULT, target_group_ids=[])


def _persisted_methodology_intent(template: Optional[dict[str, Any]]):
    """`MethodologyIntent` a partir do que `_get_template` anexou (ou
    `standard` quando o dict nao traz o campo - ex.: Modelo recem-criado)."""
    declared = (template or {}).get("methodology_intent") or METHODOLOGY_INTENT_DEFAULT
    return _methodology_intent(str(declared.get("intent") or "standard"), declared.get("target_group_ids"))


@router.get("")
def list_templates(is_active: Optional[bool] = Query(default=None)):
    return {"items": _list_templates(is_active)}


# ---------------------------------------------------------------------------
# Busca mínima de usuários (evita expor GET /users, admin-only puro, a PT).
# Registrada ANTES de "/{template_id}" (rota dinâmica de único segmento):
# FastAPI/Starlette casam rotas na ORDEM de registro, então "/users-lookup"
# precisa vir antes para não ser capturado por "/{template_id}" (que tentaria
# converter "users-lookup" para int e falhar com 422 em vez de rotear aqui).
# ---------------------------------------------------------------------------


@router.get("/users-lookup")
def users_lookup(search: Optional[str] = Query(default=None)):
    query = "SELECT id, username, full_name FROM users WHERE is_active = 1"
    params: list[Any] = []
    if search:
        query += " AND (username LIKE %s OR full_name LIKE %s)"
        like = f"%{search}%"
        params.extend([like, like])
    query += " ORDER BY username ASC LIMIT 50"

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            return {"items": cur.fetchall()}


def _with_draft_flags(template: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """`has_draft`/`draft_updated_at` (067) nas respostas das ROTAS do
    Modelo. Fora de `_get_template` de proposito: o motor de geracao tambem le
    por ela, e nenhum leitor alem das rotas do editor toca o rascunho."""
    if not template:
        return template
    with transaction() as conn:
        with conn.cursor() as cur:
            stamps = template_drafts.draft_stamps(cur, [int(template["id"])])
    return template_drafts.attach_draft_flags(template, stamps)


@router.get("/{template_id}")
def get_template(template_id: int):
    template = _get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
    return _with_draft_flags(_attach_safety_warnings(template))


# ---------------------------------------------------------------------------
# Escrita (versionada desde a 065)
#
# O antigo `_replace_template_slots` (DELETE + reinsert no lugar) saiu: slots
# e itens agora sao gravados SO por `template_versions.write_structural_version`
# (via `insert_version_slots`), sob o `version_id` da versao nova, sem DELETE.
# As linhas de versoes anteriores nunca mudam.
# ---------------------------------------------------------------------------


def _structural_payload(
    payload: WorkoutPlanTemplateIn | dict[str, Any],
    template_type: str,
    slots: list[Any],
    *,
    counting_mode: Optional[str] = None,
    training_goal: Optional[str] = None,
) -> dict[str, Any]:
    """Cabecalho ESTRUTURAL (o que versiona) + slots, com o `template_type`
    ja resolvido. Nome, descricao e `is_active` ficam de fora de proposito.
    `counting_mode`: o modo JA RESOLVIDO (`_resolve_counting_mode`); ausente,
    o do proprio `payload` (duplicar copia o do original). `training_goal`
    (093): o objetivo JA RESOLVIDO (`_resolve_training_goal`), mesma regra."""
    get = payload.get if isinstance(payload, dict) else (lambda key: getattr(payload, key, None))
    return {
        "training_type_id": get("training_type_id"),
        "volume_mode": get("volume_mode") or "percent_budget",
        "template_type": template_type,
        "grid_rows": get("grid_rows"),
        "grid_cols": get("grid_cols"),
        # Modo de contagem do volume (073): estrutural - trocar abre versao.
        "volume_counting_mode": counting_mode or counting_mode_of(payload),
        # Objetivo de treino (093): estrutural, mas so entra na forma canonica
        # quando difere do default (`template_versions.canonical_header`).
        "training_goal": training_goal or training_goal_of(payload),
        "slots": slots,
    }


def _write_version(cur, template_id: int, payload: Any, intent: Any, *, actor: Optional[int]) -> Optional[int]:
    """`write_structural_version` com o conflito de concorrencia virando 409
    (duas edicoes simultaneas do mesmo Modelo: a segunda tenta de novo)."""
    try:
        return write_structural_version(cur, template_id, payload, intent, actor=actor)
    except TemplateVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="O modelo foi alterado por outra pessoa ao mesmo tempo; recarregue e tente de novo.",
        ) from exc


def _attach_volume_coverage_warnings(template: dict[str, Any]) -> dict[str, Any]:
    """Anexa `volume_coverage_warnings` (não-bloqueante) ao dict do template
    quando ele tiver `training_type_id` — motor de ficha v2 (Fase 2,
    `template_volume_planner.validate_template_volume_coverage`). Import
    tardio (evita ciclo: template_volume_planner importa deste módulo
    indiretamente via `_get_template`). Qualquer erro aqui é engolido: esta
    é só uma checagem informativa de UI/preview, nunca deve quebrar
    CRUD de template (seção 11 do documento de especificação: "não impedir
    necessariamente o cadastro... gerar warning")."""
    if not template or not template.get("training_type_id"):
        return template
    try:
        from .intelligence.template_volume_planner import validate_template_volume_coverage

        template["volume_coverage_warnings"] = validate_template_volume_coverage(template)
    except Exception:  # noqa: BLE001 - checagem informativa, nunca deve quebrar o CRUD
        template["volume_coverage_warnings"] = []
    return template


def _attach_methodology_volume_warnings(template: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Anexa `methodology_volume_warnings` (Fase 4, PR E) ao dict do modelo:
    so os grupos `BELOW_RANGE`/`ABOVE_RANGE` com `severity: warning` da
    avaliacao pela metodologia de volume (`intelligence/methodology_volume.py`),
    com a intencao PERSISTIDA do Modelo (PR H: `methodology_intent` anexado por
    `_get_template`; sem declaracao = `standard`). No POST ainda nao ha
    intencao gravada, entao a primeira avaliacao e sempre `standard`.

    Mesmo molde de `_attach_volume_coverage_warnings`: import tardio, campo
    ADITIVO, e qualquer erro e engolido (log em warning) - a avaliacao e uma
    segunda leitura informativa e NUNCA bloqueia POST/PUT do Modelo. A leitura
    completa (todos os grupos, sessoes, qualidade de dado) fica em
    `GET /{template_id}/methodology-volume`."""
    if not template:
        return template
    try:
        from .intelligence.methodology_volume_service import evaluate_template, warnings_from_evaluation

        template["methodology_volume_warnings"] = warnings_from_evaluation(
            evaluate_template(template, _persisted_methodology_intent(template))
        )
    except Exception:  # noqa: BLE001 - leitura informativa, nunca deve quebrar o CRUD
        logger.warning(
            "methodology_volume_warnings indisponivel para o modelo %s", template.get("id"), exc_info=True
        )
        template["methodology_volume_warnings"] = []
    return template


def _enforce_template_safety(payload: WorkoutPlanTemplateIn, existing: Optional[dict[str, Any]]) -> None:
    """Validacao de seguranca do save (D14, `template_safety_validation.py`).

    Item NOVO ou ALTERADO com tecnica/RPE incompativel -> **422**. Item legado
    nao tocado -> so warning (devolvido depois em `safety_warnings`), para que
    modelos em uso continuem salvaveis. So `equipment_series` tem itens de
    equipamento, entao os outros modos nem chegam a ler o banco.
    """
    if payload.volume_mode != "equipment_series" or not has_safety_relevant_input(payload.slots):
        return
    with transaction() as conn:
        report = build_safety_report(conn, payload.slots)
    if not report:
        return
    errors, _ = split_report_by_authorship(report, payload.slots, (existing or {}).get("slots"))
    if errors:
        raise HTTPException(
            status_code=422,
            detail=(
                "Itens novos ou alterados com incompatibilidade de segurança: "
                + " ".join(entry["message"] for entry in errors)
            ),
        )


def _attach_safety_warnings(template: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """`safety_warnings` no dict do modelo: o que o builder mostra item a item
    e a versao por-modelo do relatorio de "modelos a revisar" (secao 12).
    Nunca quebra a leitura - e informativo."""
    if not template:
        return template
    if template.get("volume_mode") != "equipment_series":
        template["safety_warnings"] = []
        return template
    try:
        with transaction() as conn:
            template["safety_warnings"] = build_safety_report(conn, template.get("slots") or [])
    except Exception:  # noqa: BLE001 - relatorio informativo, nunca derruba o CRUD
        template["safety_warnings"] = []
    return template


DEFAULT_TEMPLATE_TYPE = "full_body"


def _resolve_template_type(payload: WorkoutPlanTemplateIn, current: Optional[str]) -> str:
    """`template_type` a gravar (M24).

    O campo "Tipo de modelo" saiu da tela de Modelos de Treino: ele so
    alimentava o `plan_type` da ficha gerada (`generation/mode_a.py`,
    `mode_b.py` e os caminhos legados), que por sua vez so e lido por rotas
    legadas. `payload.template_type is None` significa "o cliente nao disse
    nada sobre o campo":

    - **edicao** preserva o valor JA GRAVADO (um modelo 'abc' salvo pelo
      cliente novo continua 'abc' — nenhum dado existente e reescrito);
    - **criacao** usa o default historico da coluna ('full_body').

    Cliente antigo que ainda envia o campo continua sendo obedecido. A COLUNA
    nao sai aqui: a remocao e da F2.4, junto das rotas legadas de `plan_type`.
    """
    if payload.template_type is not None:
        return payload.template_type
    return current or DEFAULT_TEMPLATE_TYPE


def _resolve_counting_mode(payload: WorkoutPlanTemplateIn, current: Optional[str]) -> str:
    """Modo de contagem do volume a gravar (073), com a semântica de
    `_resolve_template_type`: `payload.volume_counting_mode is None` = o
    cliente não disse nada (o editor anterior ao Lote 3 não conhece o campo),
    então a edição PRESERVA o gravado e a criação usa o default da coluna
    (`primary_only`, o comportamento de antes). Valor enviado já vem validado
    pelo enum do motor (`models.counting_mode_value`)."""
    if payload.volume_counting_mode is not None:
        return payload.volume_counting_mode
    return counting_mode_of({"volume_counting_mode": current})


def _resolve_training_goal(payload: WorkoutPlanTemplateIn, current: Optional[str]) -> str:
    """Objetivo de treino a gravar (093), com a semântica de
    `_resolve_counting_mode`: `payload.training_goal is None` = o cliente não
    disse nada (o editor ainda não conhece o campo), então a edição PRESERVA o
    gravado e a criação usa o default do dono. Valor enviado já vem validado
    (`training_goal.optional_goal_value`)."""
    if payload.training_goal is not None:
        return payload.training_goal
    return training_goal_of({"training_goal": current})


def _resolve_origin(cur, payload: WorkoutPlanTemplateIn, user_id: int) -> tuple[str, Optional[int]]:
    """`(origin, generation_log_id)` a gravar (migracao 038).

    `origin='periodization'` exige um `generation_log_id` que seja uma geracao
    DO PROPRIO usuario (`template_generation_logs`) - senao a procedencia do
    modelo seria apenas o que o cliente afirmou. Sem `generation_log_id`, e
    sempre `manual`: e assim que todo modelo nasceu ate aqui, e o default da
    coluna diz o mesmo para as linhas antigas."""
    if payload.generation_log_id is None:
        return "manual", None
    cur.execute(
        "SELECT id FROM template_generation_logs WHERE id = %s AND user_id = %s",
        (payload.generation_log_id, user_id),
    )
    if not cur.fetchone():
        raise HTTPException(
            status_code=422,
            detail="generation_log_id nao corresponde a uma geracao automatica deste usuario.",
        )
    return "periodization", payload.generation_log_id


@router.post("")
def create_template(payload: WorkoutPlanTemplateIn, identity: Identity = Depends(require_admin_or_pt)):
    _validate_template_payload(payload)
    _validate_technique_codes(payload)
    # FST-7/GVT salvos com aplicações/pausa (antes da Fase 5, PR 5): gravados
    # zerados. A resposta devolve o Modelo relido, já sem os dois campos.
    clear_sets_only_technique_fields(payload)
    # Criacao: nao existe item legado, entao TODA incompatibilidade e 422.
    _enforce_template_safety(payload, None)

    with transaction() as conn:
        with conn.cursor() as cur:
            origin, generation_log_id = _resolve_origin(cur, payload, identity.user_id)
            cur.execute(
                """
                INSERT INTO workout_plan_templates (
                    name, description, template_type, training_type_id, volume_mode,
                    is_active, created_by_user_id, origin, generation_log_id,
                    grid_rows, grid_cols, volume_counting_mode, training_goal
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    payload.name,
                    payload.description,
                    _resolve_template_type(payload, None),
                    payload.training_type_id,
                    payload.volume_mode,
                    payload.is_active,
                    identity.user_id,
                    origin,
                    generation_log_id,
                    payload.grid_rows,
                    payload.grid_cols,
                    _resolve_counting_mode(payload, None),
                    _resolve_training_goal(payload, None),
                ),
            )
            template_id = cur.lastrowid
            # v1 com `effective_from` = agora (065): o Modelo nasce versionado.
            _write_version(
                cur,
                template_id,
                _structural_payload(
                    payload,
                    _resolve_template_type(payload, None),
                    payload.slots,
                    counting_mode=_resolve_counting_mode(payload, None),
                    training_goal=_resolve_training_goal(payload, None),
                ),
                dict(METHODOLOGY_INTENT_DEFAULT, target_group_ids=[]),
                actor=identity.user_id,
            )
            if generation_log_id is not None:
                # Fecha o ciclo da auditoria: este rascunho virou ESTE modelo.
                cur.execute(
                    "UPDATE template_generation_logs SET saved_template_id = %s WHERE id = %s",
                    (template_id, generation_log_id),
                )

    return _attach_safety_warnings(
        _attach_methodology_volume_warnings(_attach_volume_coverage_warnings(_get_template(template_id)))
    )


@router.post("/{template_id}/duplicate")
def duplicate_template(template_id: int, identity: Identity = Depends(require_admin_or_pt)):
    original = _get_template(template_id)
    if not original:
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")

    # Pydantic v2 ignora chaves extras por padrão (nenhum modelo abaixo usa
    # `model_config = ConfigDict(extra="forbid")`), então os dicts crus vindos
    # de `_get_template` (com `id`/`slot_id`/`template_id` extras e sem
    # `assignments`, que não é copiado de propósito) podem ser passados direto
    # — inclusive `items` aninhado, validado automaticamente pelo Pydantic.
    slots_to_persist = [WorkoutPlanTemplateSlotIn(**slot) for slot in original["slots"]]

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO workout_plan_templates (
                    name, description, template_type, training_type_id, volume_mode,
                    is_active, created_by_user_id, grid_rows, grid_cols, volume_counting_mode,
                    source_template_id, training_goal
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    f"{original['name']} (cópia)",
                    original["description"],
                    original["template_type"],
                    original["training_type_id"],
                    original.get("volume_mode", "percent_budget"),
                    True,
                    identity.user_id,
                    original.get("grid_rows"),
                    original.get("grid_cols"),
                    counting_mode_of(original),
                    # Linhagem (090): a copia lembra a origem, e as sessoes das
                    # duas contam como o mesmo Modelo (`slot_session_comparison`).
                    template_id,
                    training_goal_of(original),
                ),
            )
            new_template_id = cur.lastrowid
            # A copia nasce com a v1 dela (mesma estrutura e intencao do
            # original); o historico do original nao e copiado.
            _write_version(
                cur,
                new_template_id,
                _structural_payload(original, original["template_type"], slots_to_persist),
                original.get("methodology_intent"),
                actor=identity.user_id,
            )
            _copy_methodology_intent(cur, template_id, new_template_id)

    return _attach_safety_warnings(
        _attach_methodology_volume_warnings(_attach_volume_coverage_warnings(_get_template(new_template_id)))
    )


@router.put("/{template_id}")
def update_template(
    template_id: int,
    payload: WorkoutPlanTemplateIn,
    identity: Identity = Depends(require_admin_or_pt),
):
    existing = _get_template(template_id)
    if not existing:
        # Inexistente ou excluido logicamente (065): nao aceita edicao.
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
    # Exceção legada: Modelo gravado antes do teto de 8 slots pode ser editado
    # com a quantidade que já tem, mas não pode crescer.
    legacy_slots = len((existing or {}).get("slots") or [])
    _validate_template_payload(payload, max_slots=max(MAX_TEMPLATE_SLOTS, legacy_slots))
    _validate_technique_codes(payload)
    # FST-7/GVT salvos com aplicações/pausa (antes da Fase 5, PR 5): gravados
    # zerados. A resposta devolve o Modelo relido, já sem os dois campos.
    clear_sets_only_technique_fields(payload)
    _enforce_template_safety(payload, existing)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, template_type, volume_counting_mode, training_goal FROM workout_plan_templates "
                "WHERE id = %s AND deleted_at IS NULL",
                (template_id,),
            )
            current = cur.fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
            template_type = _resolve_template_type(payload, current.get("template_type"))
            counting_mode = _resolve_counting_mode(payload, current.get("volume_counting_mode"))
            training_goal = _resolve_training_goal(payload, current.get("training_goal"))

            cur.execute(
                """
                UPDATE workout_plan_templates
                SET name = %s, description = %s, template_type = %s, training_type_id = %s,
                    volume_mode = %s, is_active = %s, grid_rows = %s, grid_cols = %s,
                    volume_counting_mode = %s, training_goal = %s
                WHERE id = %s
                """,
                (
                    payload.name,
                    payload.description,
                    template_type,
                    payload.training_type_id,
                    payload.volume_mode,
                    payload.is_active,
                    payload.grid_rows,
                    payload.grid_cols,
                    counting_mode,
                    training_goal,
                    template_id,
                ),
            )
            # Versao nova SO se a estrutura mudou (065). Trocar so o nome, a
            # descricao ou `is_active` nao cria fronteira de versao. A
            # intencao fica a declarada (a deste PUT nao a carrega).
            nova_versao = _write_version(
                cur,
                template_id,
                _structural_payload(
                    payload, template_type, payload.slots, counting_mode=counting_mode, training_goal=training_goal
                ),
                existing.get("methodology_intent"),
                actor=identity.user_id,
            )
            # "Publicar modelo" (067): o rascunho foi publicado - some na MESMA
            # transacao. Se qualquer validacao acima falhou (422/409), nada
            # chegou aqui e o rascunho fica intacto.
            template_drafts.discard_draft(cur, template_id)

    resposta = _with_draft_flags(
        _attach_safety_warnings(
            _attach_methodology_volume_warnings(_attach_volume_coverage_warnings(_get_template(template_id)))
        )
    )
    # PR4, aditivo: `false` = nada estrutural mudou e a versao foi mantida (a
    # tela avisa "Nenhuma mudança estrutural — versão mantida").
    resposta["version_created"] = nova_versao is not None
    return resposta


@router.patch("/{template_id}/status")
def set_template_status(template_id: int, body: dict = Body(...)):
    is_active = bool(body.get("is_active"))

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM workout_plan_templates WHERE id = %s AND deleted_at IS NULL", (template_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
            cur.execute(
                "UPDATE workout_plan_templates SET is_active = %s WHERE id = %s",
                (is_active, template_id),
            )

    return _get_template(template_id)


@router.delete("/{template_id}")
def delete_template(template_id: int, identity: Identity = Depends(require_admin_or_pt)):
    """Exclusao LOGICA (065): grava `deleted_at` e encerra as atribuicoes
    vigentes - como a desatribuicao do M23 (`unassigned_at` + quem encerrou),
    no MESMO instante. O Modelo some da lista, de `assigned-to-me`, da
    geracao e de toda rota por id (404); versoes, slots, itens, intencao e o
    vinculo das fichas ja salvas ficam - eram apagados em cascata pelo DELETE
    fisico de antes, e com eles o historico do usuario.

    Resposta igual a de antes (`{"status", "template_id"}`); 404 para
    inexistente ou ja excluido."""
    from .template_assignments import close_assignments_of_template_for_everyone, db_now

    with transaction() as conn:
        with conn.cursor() as cur:
            agora = db_now(cur)
            cur.execute(
                "UPDATE workout_plan_templates SET deleted_at = %s WHERE id = %s AND deleted_at IS NULL",
                (agora, template_id),
            )
            deleted = cur.rowcount > 0
            if deleted:
                close_assignments_of_template_for_everyone(
                    cur, template_id, moment=agora, by_user_id=identity.user_id
                )
                # O rascunho (067) de um Modelo excluido nao serve a ninguem.
                template_drafts.discard_draft(cur, template_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
    return {"status": "ok", "template_id": template_id}


# ---------------------------------------------------------------------------
# Historico de versoes e reversao (PR4 do versionamento de Modelo)
#
# Rotas de DOIS ou mais segmentos (`/{id}/versions...`): nao ha rota dinamica
# de mesmo formato que as capture antes (as de `/{id}/assignments` e
# `/{id}/methodology-*` tem o segundo segmento literal diferente).
# ---------------------------------------------------------------------------

VERSIONS_LIST_SQL = """
SELECT v.id, v.version_number, v.effective_from, v.superseded_at, v.change_summary, v.training_goal,
       v.created_by_user_id, u.username AS author_username, u.full_name AS author_full_name,
       r.version_number AS reverted_from_number
FROM workout_plan_template_versions v
LEFT JOIN users u ON u.id = v.created_by_user_id
LEFT JOIN workout_plan_template_versions r ON r.id = v.reverted_from_version_id
WHERE v.template_id = %s
ORDER BY v.version_number DESC
"""

LIVE_TEMPLATE_SQL = (
    "SELECT id, name, description, is_active FROM workout_plan_templates WHERE id = %s AND deleted_at IS NULL"
)


def _live_template_or_404(cur, template_id: int) -> dict[str, Any]:
    cur.execute(LIVE_TEMPLATE_SQL, (template_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
    return dict(row)


def _version_or_404(cur, template_id: int, version_id: int):
    version = version_of_template(cur, template_id, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Versão não encontrada neste modelo.")
    return version


def _author_name(row: dict[str, Any]) -> Optional[str]:
    return (row.get("author_full_name") or row.get("author_username") or None)


@router.get("/{template_id}/versions")
def list_template_versions(template_id: int):
    """Historico de versoes, da mais nova para a mais antiga. Lista LEVE:
    sem slots nem itens (o detalhe e `GET /{id}/versions/{vid}`)."""
    with transaction() as conn:
        with conn.cursor() as cur:
            _live_template_or_404(cur, template_id)
            cur.execute(VERSIONS_LIST_SQL, (template_id,))
            rows = list(cur.fetchall() or [])
    return {
        "template_id": template_id,
        "items": [
            {
                "id": int(row["id"]),
                "number": int(row["version_number"]),
                "effective_from": row.get("effective_from"),
                "superseded_at": row.get("superseded_at"),
                "author_user_id": row.get("created_by_user_id"),
                "author_name": _author_name(row),
                "reverted_from_number": row.get("reverted_from_number"),
                "change_summary": row.get("change_summary"),
                "is_current": row.get("superseded_at") is None,
                # M97 F3: objetivo da versao (badge do historico; dono `training_goal`).
                "training_goal": training_goal_of(row),
            }
            for row in rows
        ],
    }


# Nomes para o RESUMO somente leitura de uma versao (exercicio, equipamento,
# grupo) e o autor/origem da versao - um statement cada, sem N+1.
VERSION_AUTHOR_SQL = """
SELECT u.username AS author_username, u.full_name AS author_full_name,
       r.version_number AS reverted_from_number
FROM workout_plan_template_versions v
LEFT JOIN users u ON u.id = v.created_by_user_id
LEFT JOIN workout_plan_template_versions r ON r.id = v.reverted_from_version_id
WHERE v.id = %s
"""


def _item_names(cur, slots: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    """`(tipo, id) -> nome` dos exercicios, equipamentos e grupos citados nos
    itens: no maximo UMA consulta por catalogo (tres), so com os ids
    presentes. Separadas de proposito: um `UNION` entre tabelas de collations
    diferentes falha no MySQL."""
    wanted = {
        "ex": sorted({int(i["mapping_id"]) for s in slots for i in s["items"] if i.get("mapping_id")}),
        "eq": sorted({int(i["equipment_id"]) for s in slots for i in s["items"] if i.get("equipment_id")}),
        "mg": sorted({int(i["muscle_group_id"]) for s in slots for i in s["items"] if i.get("muscle_group_id")}),
    }
    tables = {"ex": "exercise_catalog", "eq": "equipment", "mg": "muscle_groups"}
    names: dict[tuple[str, int], str] = {}
    for kind, ids in wanted.items():
        if not ids:
            continue
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(f"SELECT id, name FROM {tables[kind]} WHERE id IN ({placeholders})", tuple(ids))
        names.update({(kind, int(row["id"])): row.get("name") for row in (cur.fetchall() or [])})
    return names


@router.get("/{template_id}/versions/{version_id}")
def get_template_version(template_id: int, version_id: int):
    """Uma versao do historico: cabecalho estrutural e intencao DAQUELA
    versao, nome e descricao do Modelo, e os slots com os NOMES de exercicio,
    equipamento e grupo de cada item (`exercise_name`, `equipment_name`,
    `muscle_group_name`) - a tela mostra um resumo somente leitura, sem
    precisar dos catalogos do editor."""
    with transaction() as conn:
        with conn.cursor() as cur:
            live = _live_template_or_404(cur, template_id)
            version = _version_or_404(cur, template_id, version_id)
            slots = _load_version_tree(cur, version.id)
            cur.execute(VERSION_AUTHOR_SQL, (version.id,))
            origem = cur.fetchone() or {}
            nomes = _item_names(cur, slots)
    for slot in slots:
        for item in slot["items"]:
            item["exercise_name"] = nomes.get(("ex", int(item["mapping_id"]))) if item.get("mapping_id") else None
            item["equipment_name"] = (
                nomes.get(("eq", int(item["equipment_id"]))) if item.get("equipment_id") else None
            )
            item["muscle_group_name"] = (
                nomes.get(("mg", int(item["muscle_group_id"]))) if item.get("muscle_group_id") else None
            )
    return {
        "id": template_id,
        "name": live.get("name"),
        "description": live.get("description"),
        "is_active": live.get("is_active"),
        "template_type": version.template_type,
        "training_type_id": version.training_type_id,
        "volume_mode": version.volume_mode,
        "grid_rows": version.grid_rows,
        "grid_cols": version.grid_cols,
        "volume_counting_mode": version.volume_counting_mode,
        "training_goal": version.training_goal,
        "methodology_intent": version.intent(),
        "version": {
            "id": version.id,
            "number": version.number,
            "effective_from": version.effective_from,
            "superseded_at": version.superseded_at,
            "is_current": version.is_current,
            "change_summary": version.change_summary,
            "author_name": origem.get("author_full_name") or origem.get("author_username") or None,
            "reverted_from_number": origem.get("reverted_from_number"),
        },
        "slots": slots,
    }


def _revert_validation_errors(conn, version, slots: list[dict[str, Any]], current_slots) -> list[str]:
    """O que impede reativar `version` HOJE - as MESMAS regras do save
    (`_validate_template_payload`, catalogo de tecnicas, `_enforce_template_
    safety`), mas devolvendo a LISTA inteira em vez de parar no primeiro, para
    a tela mostrar tudo de uma vez. Vazia = pode reverter."""
    from pydantic import ValidationError

    errors: list[str] = []
    try:
        payload = WorkoutPlanTemplateIn(
            name="revert",
            template_type=version.template_type,
            training_type_id=version.training_type_id,
            volume_mode=version.volume_mode,
            grid_rows=version.grid_rows,
            grid_cols=version.grid_cols,
            volume_counting_mode=version.volume_counting_mode,
            training_goal=version.training_goal,
            slots=[WorkoutPlanTemplateSlotIn(**slot) for slot in slots],
        )
    except ValidationError as exc:
        return [f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg')}" for err in exc.errors()]

    for check in (
        lambda: _validate_template_payload(payload, max_slots=max(MAX_TEMPLATE_SLOTS, len(current_slots or []))),
        lambda: _validate_technique_codes(payload),
    ):
        try:
            check()
        except HTTPException as exc:
            errors.append(str(exc.detail))

    if payload.volume_mode == "equipment_series" and has_safety_relevant_input(payload.slots):
        report = build_safety_report(conn, payload.slots)
        safety_errors, _ = split_report_by_authorship(report, payload.slots, current_slots)
        errors.extend(str(entry["message"]) for entry in safety_errors)
    return errors


@router.post("/{template_id}/versions/{version_id}/revert")
def revert_template_version(
    template_id: int,
    version_id: int,
    identity: Identity = Depends(require_admin_or_pt),
):
    """Reverte o Modelo para a versao `version_id`, gerando a versao n+1 com
    o conteudo dela (`reverted_from_version_id`); nada e apagado.

    - 404: versao de outro Modelo, inexistente, ou Modelo excluido;
    - 409: `version_id` ja e a vigente, a vigente ja tem o mesmo conteudo, ou
      outra edicao aconteceu ao mesmo tempo;
    - 422 `{"message", "errors": [...]}`: a versao nao passa HOJE nas regras
      do save (estrutura, catalogo de tecnicas, seguranca D14) - nunca se
      reativa algo inseguro.

    O cabecalho vivo (`training_type_id`, `volume_mode`, `template_type`,
    grade) e a intencao da 056 passam a refletir a versao reativada, na mesma
    transacao."""
    with transaction() as conn:
        with conn.cursor() as cur:
            _live_template_or_404(cur, template_id)
            source = _version_or_404(cur, template_id, version_id)
            if source.is_current:
                raise HTTPException(status_code=409, detail=f"A v{source.number} já é a versão vigente.")
            vigente = current_version(cur, template_id)
            source_slots = _load_version_tree(cur, source.id)
            current_slots = _load_version_tree(cur, vigente.id) if vigente is not None else []

            errors = _revert_validation_errors(conn, source, source_slots, current_slots)
            if errors:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": f"A v{source.number} não pode ser reativada hoje:",
                        "errors": errors,
                    },
                )
            if vigente is not None and (
                canonical_header(source) == canonical_header(vigente)
                and canonical_intent(source.intent()) == canonical_intent(vigente.intent())
                and canonical_slots(source_slots) == canonical_slots(current_slots)
            ):
                raise HTTPException(
                    status_code=409,
                    detail=f"A versão vigente já tem o mesmo conteúdo da v{source.number}.",
                )

            try:
                new_version_id = revert_to_version(cur, template_id, source, actor=identity.user_id)
            except TemplateVersionConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail="O modelo foi alterado por outra pessoa ao mesmo tempo; recarregue e tente de novo.",
                ) from exc

            # Estado vivo = a versao reativada.
            cur.execute(
                """
                UPDATE workout_plan_templates
                SET template_type = %s, training_type_id = %s, volume_mode = %s, grid_rows = %s, grid_cols = %s,
                    volume_counting_mode = %s, training_goal = %s
                WHERE id = %s
                """,
                (
                    source.template_type,
                    source.training_type_id,
                    source.volume_mode,
                    source.grid_rows,
                    source.grid_cols,
                    source.volume_counting_mode,
                    training_goal_of(source),
                    template_id,
                ),
            )
            cur.execute(
                "DELETE FROM workout_plan_template_methodology_intent_targets WHERE template_id = %s",
                (template_id,),
            )
            cur.execute(
                """
                INSERT INTO workout_plan_template_methodology_intent (template_id, intent)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE intent = VALUES(intent), updated_at = CURRENT_TIMESTAMP
                """,
                (template_id, source.methodology_intent),
            )
            for group_id in source.target_group_ids:
                cur.execute(
                    """
                    INSERT INTO workout_plan_template_methodology_intent_targets (template_id, muscle_group_id)
                    VALUES (%s, %s)
                    """,
                    (template_id, group_id),
                )
            # Reverter descarta o rascunho (067): ele partia da vigente
            # anterior. O editor pede confirmacao antes quando ha rascunho.
            template_drafts.discard_draft(cur, template_id)

    resposta = _with_draft_flags(
        _attach_safety_warnings(
            _attach_methodology_volume_warnings(_attach_volume_coverage_warnings(_get_template(template_id)))
        )
    )
    resposta["version_created"] = True
    resposta["reverted_from_version"] = {"id": source.id, "number": source.number}
    resposta["new_version_id"] = new_version_id
    return resposta


# ---------------------------------------------------------------------------
# Rascunho (migracao 067): "Salvar rascunho" x "Publicar modelo"
#
# Um rascunho por Modelo, compartilhado por quem pode editar (as mesmas
# permissoes do PUT: o `require_admin_or_pt` do router). O corpo e o MESMO do
# PUT, validado so pelo Pydantic (formato): as regras de metodologia, o
# catalogo de tecnicas e a seguranca D14 ficam para a publicacao. Gravar o
# rascunho nao cria versao nem toca slots, vigente ou colunas do Modelo.
# Publicar = `PUT /{id}` (apaga o rascunho na mesma transacao).
# ---------------------------------------------------------------------------

DRAFT_NOT_FOUND = "Este modelo não tem rascunho."


@router.get("/{template_id}/draft")
def get_template_draft(template_id: int):
    with transaction() as conn:
        with conn.cursor() as cur:
            _live_template_or_404(cur, template_id)
            draft = template_drafts.get_draft(cur, template_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=DRAFT_NOT_FOUND)
    return draft


@router.put("/{template_id}/draft")
def save_template_draft(
    template_id: int,
    payload: WorkoutPlanTemplateIn,
    identity: Identity = Depends(require_admin_or_pt),
):
    # `generation_log_id` so faz sentido na criacao (POST) - nao vai para o
    # rascunho de um Modelo que ja existe.
    body = payload.model_dump(mode="json", exclude={"generation_log_id"})
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                _live_template_or_404(cur, template_id)
                return template_drafts.save_draft(cur, template_id, body, actor=identity.user_id)
    except template_drafts.DraftTooLarge as exc:
        raise HTTPException(status_code=413, detail="Rascunho grande demais para ser salvo.") from exc
    except template_drafts.DraftTableMissing as exc:
        raise HTTPException(
            status_code=503, detail="Rascunho indisponível no momento (atualização em andamento)."
        ) from exc


@router.delete("/{template_id}/draft")
def delete_template_draft(template_id: int):
    with transaction() as conn:
        with conn.cursor() as cur:
            _live_template_or_404(cur, template_id)
            existed = template_drafts.discard_draft(cur, template_id)
    if not existed:
        raise HTTPException(status_code=404, detail=DRAFT_NOT_FOUND)
    return {"status": "ok", "template_id": template_id}


# ---------------------------------------------------------------------------
# "Aplicar no Modelo" da recomendação de cardio (M98 C3b)
# ---------------------------------------------------------------------------
# A rota é `POST /cardio-recommendations/{id}/apply` (dona da recomendação);
# a escrita mora aqui, com as outras do rascunho. Grava SEMPRE no rascunho
# (`template_drafts.save_draft`), nunca na versão publicada.

TEMPLATE_NOT_ACTIVE = "O Modelo desta recomendação não é mais o seu Modelo ativo. Gere uma recomendação nova."
TEMPLATE_INACTIVE = "O Modelo desta recomendação está inativo."
TEMPLATE_UNREADABLE = (
    "Não foi possível montar o rascunho deste Modelo automaticamente. Abra o editor do Modelo, salve o rascunho "
    "e aplique de novo."
)


def draft_payload_from_template(template: dict[str, Any]) -> dict[str, Any]:
    """Payload de rascunho (a forma do `PUT /{id}/draft`) a partir da versão
    vigente, para quando o Modelo ainda não tem rascunho. Mesmos campos que o
    editor manda (`buildPayload` do frontend): sem `template_type` (legado) e
    sem `generation_log_id` (só na criação)."""
    payload = WorkoutPlanTemplateIn.model_validate({**template, "template_type": None, "generation_log_id": None})
    return payload.model_dump(mode="json", exclude={"generation_log_id", "template_type"})


def apply_cardio_sessions_to_draft(
    template_id: int, sessions: list[dict[str, Any]], *, owner_user_id: int, actor: Optional[int]
) -> dict[str, Any]:
    """Aplica as sessões da recomendação de cardio no rascunho do Modelo
    `template_id` (regras em `template_drafts.apply_cardio_sessions`).

    Recusa o pedido inteiro (409) se o Modelo não é mais o ativo de
    `owner_user_id` (dono da recomendação) ou está inativo; 404 se foi
    excluído. Grava o rascunho só se alguma sessão mudou algo; devolve o
    resultado por sessão. A autorização de papel é da rota."""
    from .template_assignments import resolve_active_template_id

    if resolve_active_template_id(owner_user_id) != int(template_id):
        raise HTTPException(status_code=409, detail={"reason": "template_not_active", "message": TEMPLATE_NOT_ACTIVE})
    published = _get_template(template_id)
    if not published:
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                live = _live_template_or_404(cur, template_id)
                if not live.get("is_active"):
                    raise HTTPException(
                        status_code=409, detail={"reason": "template_inactive", "message": TEMPLATE_INACTIVE}
                    )
                draft = template_drafts.get_draft(cur, template_id)
                try:
                    base = draft["payload"] if draft else draft_payload_from_template(published)
                except ValidationError as exc:
                    raise HTTPException(
                        status_code=409, detail={"reason": "template_unreadable", "message": TEMPLATE_UNREADABLE}
                    ) from exc
                new_payload, results = template_drafts.apply_cardio_sessions(
                    base, sessions, template_id=int(template_id), published=published, has_draft=draft is not None
                )
                changed = any(r["status"] == template_drafts.APPLY_APPLIED for r in results)
                saved = draft
                if changed:
                    try:
                        parsed = WorkoutPlanTemplateIn.model_validate(new_payload)
                    except ValidationError as exc:
                        raise HTTPException(
                            status_code=409, detail={"reason": "template_unreadable", "message": TEMPLATE_UNREADABLE}
                        ) from exc
                    body = parsed.model_dump(mode="json", exclude={"generation_log_id"})
                    saved = template_drafts.save_draft(cur, template_id, body, actor=actor)
    except template_drafts.DraftTooLarge as exc:
        raise HTTPException(status_code=413, detail="Rascunho grande demais para ser salvo.") from exc
    except template_drafts.DraftTableMissing as exc:
        raise HTTPException(
            status_code=503, detail="Rascunho indisponível no momento (atualização em andamento)."
        ) from exc
    return {
        "template_id": int(template_id),
        "template_name": published.get("name"),
        "draft_saved": changed,
        "has_draft": saved is not None,
        "draft_updated_at": (saved or {}).get("updated_at"),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Atribuição
# ---------------------------------------------------------------------------


@router.get("/{template_id}/assignments")
def list_assignments(template_id: int, include_history: bool = Query(False)):
    """Atribuições VIGENTES deste modelo. `include_history=1` traz também as
    já encerradas, com `unassigned_at` — é assim que a tela de `/modelos-treino`
    mostra "quem seguiu este modelo e até quando" sem poluir a lista do dia a
    dia (M23)."""
    where_vigentes = "" if include_history else " AND a.unassigned_at IS NULL"
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM workout_plan_templates WHERE id = %s AND deleted_at IS NULL", (template_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")

            cur.execute(
                f"""
                SELECT a.id, a.user_id, u.username, u.full_name,
                       a.assigned_by_user_id, a.assigned_at,
                       a.unassigned_at, a.unassigned_by_user_id
                FROM workout_plan_template_assignments a
                JOIN users u ON u.id = a.user_id
                WHERE a.template_id = %s{where_vigentes}
                ORDER BY a.assigned_at DESC, a.id DESC
                """,
                (template_id,),
            )
            return {"items": cur.fetchall()}


@router.post("/{template_id}/assignments")
def create_assignment(
    template_id: int,
    payload: WorkoutPlanTemplateAssignIn,
    identity: Identity = Depends(require_admin_or_pt),
):
    """Atribui o modelo ao usuário, ENCERRANDO o modelo que ele seguia (M23).

    Um usuário só tem um Modelo vigente por vez — é o que o banco passa a
    garantir (UNIQUE em `active_user_id`, migração 049). Este endpoint, que
    antes só inseria e devolvia 409 quando o par já existia, agora faz a
    transição inteira DENTRO DE UMA TRANSAÇÃO: encerra a vigente com o mesmo
    instante em que a nova começa, e insere a nova. Sem isso existiria um
    intervalo — ainda que de milissegundos — em que o usuário não teria
    Modelo nenhum, e o dashboard leria `has_template: False` no meio de uma
    troca.

    409 continua existindo, para um caso só: o modelo pedido JÁ é o vigente.
    Reatribuir o mesmo modelo não é troca nenhuma; encerrar e reabrir a mesma
    vigência só criaria um corte falso no histórico."""
    from .template_assignments import close_active_assignment, db_now, insert_assignment

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM workout_plan_templates WHERE id = %s AND deleted_at IS NULL", (template_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")

            cur.execute("SELECT id FROM users WHERE id = %s AND is_active = 1", (payload.user_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Usuário não encontrado ou inativo.")

            cur.execute(
                """
                SELECT id, template_id FROM workout_plan_template_assignments
                WHERE user_id = %s AND unassigned_at IS NULL
                """,
                (payload.user_id,),
            )
            vigente = cur.fetchone()
            if vigente and int(vigente["template_id"]) == int(template_id):
                raise HTTPException(
                    status_code=409, detail="Este modelo já é o modelo vigente deste usuário."
                )

            # O MESMO instante fecha a anterior e abre a nova: a linha do
            # tempo fica contínua e `[assigned_at, unassigned_at)` de uma
            # encosta exatamente no início da seguinte.
            agora = db_now(cur)
            if vigente:
                close_active_assignment(cur, payload.user_id, moment=agora, by_user_id=identity.user_id)
            try:
                insert_assignment(
                    cur,
                    template_id,
                    payload.user_id,
                    assigned_by_user_id=identity.user_id,
                    moment=agora,
                )
            except Exception as exc:
                if not is_duplicate_key_error(exc):
                    raise
                # Só chega aqui numa corrida: outra requisição abriu uma
                # vigência entre o SELECT e o INSERT e o UNIQUE de
                # `active_user_id` recusou a segunda. O 409 é a resposta certa
                # — e a invariante foi mantida pelo banco, não pela ordem das
                # requisições.
                raise HTTPException(
                    status_code=409,
                    detail="Outra atribuição para este usuário foi feita ao mesmo tempo; tente de novo.",
                ) from exc

    return list_assignments(template_id)


@router.delete("/{template_id}/assignments/{user_id}")
def delete_assignment(
    template_id: int,
    user_id: int,
    identity: Identity = Depends(require_admin_or_pt),
):
    """Encerra a vigência — NÃO apaga a linha (M23).

    Até a migração 049 isto era `DELETE` físico e o passado sumia junto: o
    dashboard perdia a informação de que aquele Modelo existiu, e as
    evoluções por grupo muscular ficavam sem o plano que as gerou. Agora a
    linha fica, com `unassigned_at` marcando o fim da vigência.

    404 quando aquele modelo não é o vigente daquele usuário — inclusive
    quando ele JÁ foi encerrado antes: desatribuir duas vezes não deve criar
    um segundo fim nem reescrever o primeiro."""
    from .template_assignments import close_assignment_of_template, db_now

    with transaction() as conn:
        with conn.cursor() as cur:
            encerradas = close_assignment_of_template(
                cur, user_id, template_id, moment=db_now(cur), by_user_id=identity.user_id
            )

    if not encerradas:
        raise HTTPException(status_code=404, detail="Atribuição vigente não encontrada.")
    return {"status": "ok", "template_id": template_id, "user_id": user_id}


# ---------------------------------------------------------------------------
# Preview de ativação semanal (seção 4 do plano — regra de repetição cíclica)
# ---------------------------------------------------------------------------


def _load_muscle_group_names(muscle_group_ids: set[int]) -> dict[int, str]:
    if not muscle_group_ids:
        return {}
    with transaction() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(muscle_group_ids))
            cur.execute(
                f"SELECT id, name FROM muscle_groups WHERE id IN ({placeholders})",
                tuple(muscle_group_ids),
            )
            return {row["id"]: row["name"] for row in cur.fetchall()}


def _build_cycle(slots: list[WorkoutPlanTemplateSlotIn]) -> list[WorkoutPlanTemplateSlotIn]:
    """Monta o ciclo de repetição: TODOS os slots do payload, ordenados por
    `sequence`. Não há mais conceito de 'slot vazio' a excluir — o modelo
    agora tem exatamente os slots que o usuário adicionou (mínimo 1, sem
    teto), então o ciclo tem sempre N = quantidade real de slots."""
    return sorted(slots, key=lambda slot: slot.sequence)


def _compute_slot_activation(
    slot: WorkoutPlanTemplateSlotIn,
    muscle_group_names: dict[int, str],
    catalog: Any = None,
    projected_sets: Optional[dict[int, float]] = None,
) -> list[dict[str, Any]]:
    """Ativação de um slot: itens 'exercise' via a mesma fórmula de
    VolumeService.get_muscle_activation_for_exercises (que por sua vez usa
    _resolve_muscle_contributions_for_mappings/_build_muscle_activation_rows);
    itens 'muscle_group' como ativação primária pura, peso 1.0 (sem
    secundário/estabilizador — não há como derivar isso sem um exercício
    real). Slot 'descanso' é zero em todos os grupos (seção 4.3 do plano).

    `projected_sets` (posição do item em `slot.items` -> séries): ponto médio
    do orçamento do item 'muscle_group' com meta (ver `_project_item_budgets`);
    nesses itens `sets_count` é ignorado, como no motor v2."""
    if slot.slot_type == "descanso":
        return []

    from .intelligence.technique_volume import new_catalog
    from .intelligence.volume_service import VolumeService

    if catalog is None:
        catalog = new_catalog()
    exercise_items = [
        _activation_input(item, catalog) for item in slot.items if item.item_kind == "exercise" and item.mapping_id
    ]

    rows_by_group: dict[str, dict[str, float]] = {}
    if exercise_items:
        volume_service = VolumeService()
        for row in volume_service.get_muscle_activation_for_exercises(exercise_items):
            rows_by_group[row["muscle_group"]] = {
                "primary_equivalent": float(row.get("primary_equivalent") or 0.0),
                "secondary_equivalent": float(row.get("secondary_equivalent") or 0.0),
                "stabilizer_equivalent": float(row.get("stabilizer_equivalent") or 0.0),
            }

    for item_index, item in enumerate(slot.items):
        if item.item_kind != "muscle_group" or not item.muscle_group_id:
            continue
        group_name = muscle_group_names.get(item.muscle_group_id)
        if not group_name:
            continue
        bucket = rows_by_group.setdefault(
            group_name,
            {"primary_equivalent": 0.0, "secondary_equivalent": 0.0, "stabilizer_equivalent": 0.0},
        )
        bucket["primary_equivalent"] += _muscle_item_sets(item, item_index, projected_sets) * 1.0

    return [
        {
            "muscle_group": group_name,
            "primary_equivalent": round(values["primary_equivalent"], 2),
            "secondary_equivalent": round(values["secondary_equivalent"], 2),
            "stabilizer_equivalent": round(values["stabilizer_equivalent"], 2),
            "total_equivalent": round(
                values["primary_equivalent"] + values["secondary_equivalent"] + values["stabilizer_equivalent"], 2
            ),
        }
        for group_name, values in sorted(rows_by_group.items())
    ]


def _muscle_item_sets(
    item: WorkoutPlanTemplateSlotItemIn, item_index: int, projected_sets: Optional[dict[int, float]]
) -> float:
    """Séries de um item 'muscle_group' no preview. Com orçamento projetado
    (modo 'percent_budget' + tipo de treino + meta para o grupo), vale SEMPRE
    o ponto médio e `sets_count` é ignorado - igual ao motor v2 (o card nem
    mostra o campo nesse caso). Sem projeção, `sets_count` (ou 0), como antes."""
    if projected_sets and item_index in projected_sets:
        return float(projected_sets[item_index])
    return float(item.sets_count or 0)


def _load_weekly_targets(training_type_id: int) -> dict[int, float]:
    """`muscle_group_id -> weekly_target` vigente do tipo de treino - a MESMA
    fonte do planner v2 (`get_training_type_muscle_group_targets`)."""
    from .training_types import get_training_type_muscle_group_targets

    raw = get_training_type_muscle_group_targets(training_type_id)
    return {int(group_id): float(values["target"]) for group_id, values in raw.items()}


def _project_item_budgets(
    cycle: list[WorkoutPlanTemplateSlotIn], weekly_targets: dict[int, float]
) -> tuple[list[dict[str, Any]], dict[int, dict[int, float]]]:
    """Modo 'percent_budget' com tipo de treino: projeção do orçamento de
    TODOS os itens 'muscle_group' do ciclo (não só da janela de 7 dias - o
    resumo do slot recolhido precisa de todos), pela mesma função pura do
    planner v2 (`project_muscle_item_budgets`: faixa configurada ou divisão
    igual do que sobra até 100%, x meta semanal).

    Devolve `item_budgets` (aditivo à resposta) e, por `slot.sequence`, o
    ponto médio de cada item que tem meta (chave = posição
    do item em `slot.items`) - o que o preview soma."""
    from .intelligence.template_volume_planner import project_muscle_item_budgets

    item_budgets: list[dict[str, Any]] = []
    projected_by_slot: dict[int, dict[int, float]] = {}
    for slot in cycle:
        if slot.slot_type == "descanso":
            continue
        for projection in project_muscle_item_budgets(slot.items, weekly_targets):
            item = projection.item
            item_budgets.append(
                {
                    "slot_sequence": slot.sequence,
                    "item_index": projection.index,
                    "item_order": item.item_order,
                    "muscle_group_id": projection.muscle_group_id,
                    "pct_min": round(projection.pct_min, 2),
                    "pct_max": round(projection.pct_max, 2),
                    "sets_min": None if projection.sets_min is None else round(projection.sets_min, 2),
                    "sets_max": None if projection.sets_max is None else round(projection.sets_max, 2),
                    "has_target": projection.has_target,
                }
            )
            if projection.sets_mid is not None:
                projected_by_slot.setdefault(slot.sequence, {})[projection.index] = projection.sets_mid
    return item_budgets, projected_by_slot


def _activation_input(item: WorkoutPlanTemplateSlotItemIn, catalog: Any) -> dict[str, Any]:
    """Item -> entrada de `VolumeService.get_muscle_activation_for_exercises`.
    Fase 5 (PR 5): `sets_count` já vem com o equivalente PLANEJADO da técnica
    (`technique_volume.planned_item_sets`, regra única do catálogo), que o
    motor multiplica por `stimulus_weight` como qualquer série."""
    from .intelligence.technique_volume import planned_item_sets

    return {"mapping_id": item.mapping_id, "sets_count": planned_item_sets(item, catalog)}


def _compute_slot_activation_equipment_series(
    slot: WorkoutPlanTemplateSlotIn, catalog: Any = None, muscle_group_names: Optional[dict[int, str]] = None
) -> list[dict[str, Any]]:
    """Ativação de um slot no modo `volume_mode='equipment_series'`: delega
    100% do cálculo a `VolumeService.get_muscle_activation_for_exercises` — o
    MESMO motor usado por `POST /analytics/muscle-activation-preview` —, sem
    reimplementar a multiplicação por `stimulus_weight`. Monta a lista
    `[{mapping_id, sets_count}]` a partir dos itens `item_kind='equipment'`
    do slot e reformata o resultado na MESMA estrutura (`muscle_group`,
    `primary_equivalent`, `secondary_equivalent`, `stabilizer_equivalent`,
    `total_equivalent`) que `_compute_slot_activation` já produz para o
    modo 'percent_budget', para o frontend continuar funcionando sem nenhuma
    mudança. Slot 'descanso' ou sem nenhum item de equipamento retorna lista
    vazia (ativação zerada), mesmo comportamento que o outro modo já tem
    para dias vazios.

    M101b: o item de exercício livre (`domain/free_exercise_item.is_free_exercise_item`) entra como
    primário puro do grupo (séries planejadas x 1,0), como o item
    'muscle_group' em `_compute_slot_activation`."""
    if slot.slot_type == "descanso":
        return []

    from .intelligence.technique_volume import new_catalog

    if catalog is None:
        catalog = new_catalog()
    equipment_items = [
        _activation_input(item, catalog) for item in slot.items if item.item_kind == "equipment" and item.mapping_id
    ]
    free_items = [item for item in slot.items if is_free_exercise_item(item)]
    if not equipment_items and not free_items:
        return []

    rows: list[dict[str, Any]] = []
    if equipment_items:
        from .intelligence.volume_service import VolumeService

        rows = VolumeService().get_muscle_activation_for_exercises(equipment_items)

    from .intelligence.technique_volume import free_exercise_primary_sets

    free_primary: dict[str, float] = defaultdict(float)
    for item in free_items:
        group_name = (muscle_group_names or {}).get(item.muscle_group_id)
        if group_name:
            # M101c: regra única do item livre (a mesma da metodologia e do
            # planejado do dashboard), `technique_volume.free_exercise_primary_sets`.
            free_primary[group_name] += free_exercise_primary_sets(item, catalog)

    by_group: dict[str, dict[str, Any]] = {
        row["muscle_group"]: {
            "muscle_group": row["muscle_group"],
            "primary_equivalent": row["primary_equivalent"],
            "secondary_equivalent": row["secondary_equivalent"],
            "stabilizer_equivalent": row["stabilizer_equivalent"],
            "total_equivalent": row["total_equivalent"],
        }
        for row in rows
    }
    for group_name, value in free_primary.items():
        entry = by_group.setdefault(
            group_name,
            {
                "muscle_group": group_name,
                "primary_equivalent": 0.0,
                "secondary_equivalent": 0.0,
                "stabilizer_equivalent": 0.0,
                "total_equivalent": 0.0,
            },
        )
        entry["primary_equivalent"] = round(float(entry["primary_equivalent"] or 0.0) + value, 2)
        entry["total_equivalent"] = round(float(entry["total_equivalent"] or 0.0) + value, 2)
    return [by_group[name] for name in sorted(by_group)]


# ---------------------------------------------------------------------------
# M11: detalhamento POR ITEM do preview (aditivo à resposta). Mesma fórmula da
# tabela — `sets_count x stimulus_weight` de `exercise_muscle_contributions`
# por papel (PRIMARY/SECONDARY/STABILIZER), exatamente como
# `VolumeService._build_muscle_activation_rows` —, e item 'muscle_group' como
# primário puro peso 1.0 (mesma regra de `_compute_slot_activation`). O valor
# é o da JANELA do preview: contribuição por sessão x nº de vezes que o slot
# aparece na janela, para que as linhas de um grupo somem o total da tabela.
# ---------------------------------------------------------------------------

ITEM_ROLE_PRIMARY = "primary"
ITEM_ROLE_SECONDARY = "secondary"
ITEM_ROLE_STABILIZER = "stabilizer"
_ITEM_ROLE_ORDER = {ITEM_ROLE_PRIMARY: 0, ITEM_ROLE_SECONDARY: 1, ITEM_ROLE_STABILIZER: 2}


def _normalize_contribution_role(role: Optional[str]) -> str:
    """Mesmo mapeamento de `_build_muscle_activation_rows`: PRIMARY e
    STABILIZER explícitos; qualquer outro valor conta como secundário."""
    normalized = (role or "").upper()
    if normalized == "PRIMARY":
        return ITEM_ROLE_PRIMARY
    if normalized == "STABILIZER":
        return ITEM_ROLE_STABILIZER
    return ITEM_ROLE_SECONDARY


def _load_preview_contributions(mapping_ids: set[int]) -> dict[int, list[dict[str, Any]]]:
    """Contribuições por mapping_id — a MESMA fonte que
    `VolumeService.get_muscle_activation_for_exercises` usa por dentro."""
    if not mapping_ids:
        return {}
    from .intelligence.muscle_contribution_resolver import _resolve_muscle_contributions_for_mappings

    return _resolve_muscle_contributions_for_mappings(mapping_ids)


def _item_breakdown_source(item: WorkoutPlanTemplateSlotItemIn, volume_mode: str) -> Optional[str]:
    """Qual caminho do cálculo do dia o item percorre (None = não entra):
    espelha `_compute_slot_activation` (percent_budget: 'muscle_group' e
    'exercise' legado) e `_compute_slot_activation_equipment_series`
    (equipment_series: só 'equipment')."""
    if volume_mode == "equipment_series":
        if item.item_kind == "equipment" and item.mapping_id:
            return "mapping"
        # M101b: exercício livre = séries do grupo primário (peso 1,0).
        return "free_exercise" if is_free_exercise_item(item) else None
    if item.item_kind == "exercise" and item.mapping_id:
        return "mapping"
    if item.item_kind == "muscle_group" and item.muscle_group_id:
        return "muscle_group"
    return None


def _compute_item_breakdown(
    filled_cycle: list[WorkoutPlanTemplateSlotIn],
    days: list[dict[str, Any]],
    volume_mode: str,
    muscle_group_names: dict[int, str],
    catalog: Any = None,
    projected_by_slot: Optional[dict[int, dict[int, float]]] = None,
) -> dict[str, Any]:
    """`items`: uma entrada por item que contribui, com chave estável
    (`slot_sequence` + `item_index` = posição do item em `slot.items` do
    payload) e eco da identidade (`item_kind`, `mapping_id`,
    `muscle_group_id`) para o cliente casar com a linha; `contributions` traz
    `{muscle_group_id, muscle_group, role, value}` com `value` na janela.

    `group_totals`: total de cada grupo na janela, somado dos MESMOS `days`
    que a tabela soma (`total_equivalent`) — é o denominador da linha."""
    from .intelligence.technique_volume import free_exercise_primary_sets, new_catalog, planned_item_sets

    if catalog is None:
        catalog = new_catalog()
    occurrences: dict[int, int] = defaultdict(int)
    for day in days:
        occurrences[day["slot_sequence"]] += 1

    group_totals: dict[str, float] = defaultdict(float)
    for day in days:
        for group in day["groups"]:
            group_totals[group["muscle_group"]] += float(group.get("total_equivalent") or 0.0)

    mapping_ids = {
        item.mapping_id
        for slot in filled_cycle
        if slot.slot_type != "descanso"
        for item in slot.items
        if _item_breakdown_source(item, volume_mode) == "mapping" and (item.sets_count or 0) > 0
    }
    contributions_by_mapping = _load_preview_contributions(mapping_ids)

    group_ids_by_name: dict[str, int] = {name: group_id for group_id, name in muscle_group_names.items()}
    items: list[dict[str, Any]] = []
    for slot in filled_cycle:
        slot_occurrences = occurrences.get(slot.sequence, 0)
        if slot.slot_type == "descanso" or slot_occurrences == 0:
            continue
        for item_index, item in enumerate(slot.items):
            source = _item_breakdown_source(item, volume_mode)
            # Fase 5 (PR 5): séries + equivalente planejado da técnica, a
            # MESMA unidade que o dia soma (`_activation_input`).
            sets_count = planned_item_sets(item, catalog)
            if source == "muscle_group":
                # Mesmas séries do dia (`_compute_slot_activation`): com meta,
                # o ponto médio projetado do orçamento.
                sets_count = _muscle_item_sets(item, item_index, (projected_by_slot or {}).get(slot.sequence))
            elif source == "free_exercise":
                # M101c: o mesmo valor do dia (`free_exercise_primary_sets`, já com o peso).
                sets_count = free_exercise_primary_sets(item, catalog)
            if source is None or sets_count <= 0:
                continue

            per_session: dict[tuple[str, str], float] = defaultdict(float)
            if source in ("muscle_group", "free_exercise"):
                group_name = muscle_group_names.get(item.muscle_group_id)
                if group_name:
                    per_session[(group_name, ITEM_ROLE_PRIMARY)] += sets_count * 1.0
            else:
                for contribution in contributions_by_mapping.get(item.mapping_id, []):
                    group_name = contribution.get("muscle_group_name")
                    if not group_name:
                        continue
                    if contribution.get("muscle_group_id") is not None:
                        group_ids_by_name.setdefault(group_name, contribution["muscle_group_id"])
                    role = _normalize_contribution_role(contribution.get("role"))
                    per_session[(group_name, role)] += sets_count * float(contribution.get("stimulus_weight") or 0.0)

            contributions = [
                {
                    "muscle_group_id": group_ids_by_name.get(group_name),
                    "muscle_group": group_name,
                    "role": role,
                    "value": round(value * slot_occurrences, 2),
                }
                for (group_name, role), value in per_session.items()
                if value > 0
            ]
            contributions.sort(key=lambda c: (_ITEM_ROLE_ORDER[c["role"]], -c["value"], c["muscle_group"]))
            items.append(
                {
                    "slot_sequence": slot.sequence,
                    "item_index": item_index,
                    "item_order": item.item_order,
                    "item_kind": item.item_kind,
                    "mapping_id": item.mapping_id,
                    "muscle_group_id": item.muscle_group_id,
                    "occurrences": slot_occurrences,
                    "contributions": contributions,
                }
            )

    return {
        "items": items,
        "group_totals": [
            {
                "muscle_group_id": group_ids_by_name.get(name),
                "muscle_group": name,
                "total_equivalent": round(total, 2),
            }
            for name, total in sorted(group_totals.items())
            if total > 0
        ],
    }


# Janela do preview: sempre 7 dias a partir do primeiro slot de treino (mesma
# regra da metodologia de volume). `days[]`, `group_totals[]` e
# `items[].occurrences`/`contributions` refletem essa janela; `window_days` e
# 7 quando ha slot de treino e 0 quando o ciclo so tem descanso.
PREVIEW_WINDOW_DAYS = 7


def _slot_time_estimates(slots: list[Any]) -> list[dict[str, Any]]:
    """`slot_times[]` do preview: estimativa completa por slot (total em
    `estimated_seconds` + o detalhamento que o tooltip do builder mostra).
    Slot de descanso ou sem itens fica com 0 - quem exibe decide esconder."""
    from .periodization.time_estimate import estimate_slot_time

    result: list[dict[str, Any]] = []
    for slot in slots:
        estimate = estimate_slot_time(slot.items)
        result.append(
            {
                "slot_sequence": slot.sequence,
                "estimated_seconds": estimate["total_seconds"],
                "breakdown": estimate["breakdown"],
                "items_without_rest": estimate["items_without_rest"],
                "has_estimated_reps": estimate["has_estimated_reps"],
            }
        )
    return result


def _item_prior_fatigue(
    slots: list[WorkoutPlanTemplateSlotIn],
    volume_mode: str,
    muscle_group_names: dict[int, str],
    catalog: Any,
    projected_by_slot: Optional[dict[int, dict[int, float]]] = None,
) -> list[dict[str, Any]]:
    """`item_prior_fatigue[]` do preview: fadiga prévia de CADA item de CADA
    slot do ciclo (não só da janela), na ORDEM em que os itens vieram no corpo
    - reordenar no builder muda o corpo e, portanto, a resposta.

    A regra é toda do dono `domain/prior_fatigue.py`; aqui só se monta a
    sequência com as MESMAS entradas do preview: séries planejadas
    (`planned_item_sets`, com técnica; item de grupo = séries do dia de
    `_muscle_item_sets`) e pesos por papel de `_load_preview_contributions`
    (item de grupo = PRIMARY peso 1,0, como `_compute_item_breakdown`).
    Chave estável igual a `items[]`: `slot_sequence` + `item_index` + eco da
    identidade (`item_kind`, `mapping_id`, `muscle_group_id`)."""
    from .domain.prior_fatigue import (
        PRIMARY_ROLE,
        FatigueItem,
        MuscleContribution,
        contribution_from_mapping,
        prior_fatigue_for_sequence,
    )
    from .intelligence.technique_volume import planned_item_sets

    mapping_ids = {
        item.mapping_id
        for slot in slots
        if slot.slot_type != "descanso"
        for item in slot.items
        if _item_breakdown_source(item, volume_mode) == "mapping"
    }
    contributions_by_mapping = _load_preview_contributions(mapping_ids)

    result: list[dict[str, Any]] = []
    for slot in slots:
        if slot.slot_type == "descanso":
            continue
        indexed: list[tuple[int, WorkoutPlanTemplateSlotItemIn]] = []
        sequence: list[FatigueItem] = []
        for item_index, item in enumerate(slot.items):
            source = _item_breakdown_source(item, volume_mode)
            if source is None:
                continue
            if source in ("muscle_group", "free_exercise"):
                sets = (
                    _muscle_item_sets(item, item_index, (projected_by_slot or {}).get(slot.sequence))
                    if source == "muscle_group"
                    else planned_item_sets(item, catalog)
                )
                group_name = muscle_group_names.get(item.muscle_group_id)
                weight = FREE_EXERCISE_PRIMARY_WEIGHT if source == "free_exercise" else 1.0
                contributions: tuple[MuscleContribution, ...] = (
                    (MuscleContribution(item.muscle_group_id, group_name, PRIMARY_ROLE, weight),) if group_name else ()
                )
            else:
                sets = planned_item_sets(item, catalog)
                contributions = tuple(
                    contribution_from_mapping(row) for row in contributions_by_mapping.get(item.mapping_id, [])
                )
            indexed.append((item_index, item))
            sequence.append(FatigueItem(sets=sets, contributions=contributions))

        for (item_index, item), prior in zip(indexed, prior_fatigue_for_sequence(sequence)):
            result.append(
                {
                    "slot_sequence": slot.sequence,
                    "item_index": item_index,
                    "item_kind": item.item_kind,
                    "mapping_id": item.mapping_id,
                    "muscle_group_id": item.muscle_group_id,
                    "prior_fatigue": prior.as_dict() if prior is not None else None,
                }
            )
    return result


@router.post("/preview-activation")
def preview_activation(payload: WorkoutPlanTemplatePreviewIn):
    filled_cycle = _build_cycle(payload.slots)
    cycle_length = len(filled_cycle)

    if cycle_length == 0:
        return {
            "cycle_length": 0,
            "days": [],
            "message": "Preencha ao menos um slot para ver o preview de ativação.",
        }

    muscle_group_ids = {
        item.muscle_group_id
        for slot in filled_cycle
        for item in slot.items
        if (item.item_kind == "muscle_group" and item.muscle_group_id) or is_free_exercise_item(item)
    }
    muscle_group_names = _load_muscle_group_names(muscle_group_ids)

    # Modo 'percent_budget' com tipo de treino: o volume do item 'muscle_group'
    # é o orçamento (meta semanal x faixa %), não `sets_count` (ignorado quando
    # o grupo tem meta, como no motor v2). Falha ao ler as metas não derruba o preview: segue sem
    # projeção, como antes.
    item_budgets: Optional[list[dict[str, Any]]] = None
    projected_by_slot: dict[int, dict[int, float]] = {}
    if payload.volume_mode == "percent_budget" and payload.training_type_id:
        try:
            weekly_targets = _load_weekly_targets(payload.training_type_id)
            item_budgets, projected_by_slot = _project_item_budgets(filled_cycle, weekly_targets)
        except Exception:  # noqa: BLE001 - preview nunca pode quebrar a tela de edição
            item_budgets, projected_by_slot = None, {}

    # Fase 7: a janela e a MESMA da metodologia de volume
    # (`evaluation_window_days`): 7 dias a partir do primeiro slot de treino,
    # um slot = um dia, dando a volta no ciclo curto (3 slots -> 1,2,3,1,2,3,1)
    # e cortando o que passa do 7o dia no ciclo longo; descanso ocupa o dia.
    # Antes era `max(7, ciclo)` a partir do slot 1, e a janela flutuante
    # escalava por 7/ciclo - um ciclo de 8 slots mostrava 7/8 de cada ativacao.
    from .intelligence.methodology_volume import evaluation_window_days

    window_slots = evaluation_window_days(filled_cycle, PREVIEW_WINDOW_DAYS)
    window_days = len(window_slots)
    # Catálogo de técnicas da requisição (Fase 5, PR 5): lido uma vez, e só se
    # algum item tiver técnica com aplicações.
    from .intelligence.technique_volume import new_catalog

    catalog = new_catalog()
    days = []
    for day, slot in enumerate(window_slots, start=1):

        if payload.volume_mode == "equipment_series":
            # Mesmo padrão de try/except amplo dos demais blocos de preview:
            # a tela de edição do template nunca pode quebrar por causa do
            # preview — cai para ativação zerada neste dia.
            try:
                groups = _compute_slot_activation_equipment_series(slot, catalog, muscle_group_names)
            except Exception:  # noqa: BLE001 - preview nunca pode quebrar a tela de edição
                groups = []
        else:
            groups = _compute_slot_activation(
                slot, muscle_group_names, catalog, projected_by_slot.get(slot.sequence)
            )

        days.append(
            {
                "day": day,
                "slot_sequence": slot.sequence,
                "slot_name": slot.name,
                "slot_type": slot.slot_type,
                "groups": groups,
            }
        )

    response: dict[str, Any] = {"cycle_length": cycle_length, "days": days, "window_days": window_days}
    if payload.volume_mode == "equipment_series":
        # M76 (Lote 4c, E7): "Tempo total de treino" de CADA slot do ciclo (não
        # só da janela), pelo dono `periodization/time_estimate.py`. O builder
        # mostra o espelho TS enquanto o usuário digita (paridade completa com
        # a fixture gerada pelo dono); fora do builder vale este número.
        response["slot_times"] = _slot_time_estimates(filled_cycle)
    # Fadiga prévia por item (dono `domain/prior_fatigue.py`): a faixa de cor
    # de cada card do builder. Aditivo e nunca quebra a tela de edição.
    try:
        response["item_prior_fatigue"] = _item_prior_fatigue(
            filled_cycle, payload.volume_mode, muscle_group_names, catalog, projected_by_slot
        )
    except Exception:  # noqa: BLE001 - preview nunca pode quebrar a tela de edição
        logger.warning("preview: falha ao calcular item_prior_fatigue; resposta segue sem o campo", exc_info=True)
    if item_budgets is not None:
        response["item_budgets"] = item_budgets
    if not window_slots:
        # Ciclo só de descanso: janela vazia, sem treino para projetar.
        response["message"] = "Adicione ao menos um slot de treino para ver o preview de ativação."
        return response

    # M11 (aditivo): detalhamento por item + total por grupo na janela, para a
    # linha de ativação abaixo de cada item do builder. Mesma regra de nunca
    # quebrar a tela: se falhar, a resposta segue sem os campos novos e o
    # cliente simplesmente não mostra a linha.
    try:
        response.update(
            _compute_item_breakdown(
                filled_cycle, days, payload.volume_mode, muscle_group_names, catalog, projected_by_slot
            )
        )
    except Exception:  # noqa: BLE001 - preview nunca pode quebrar a tela de edição
        logger.warning("preview: falha no detalhamento por item; resposta segue sem os campos", exc_info=True)
    return response


# ---------------------------------------------------------------------------
# Metodologia de volume por grupo (Fase 4, PR E) - segunda leitura do Modelo,
# em OUTRA unidade (`direct_sets`/`direct_exercises` por sessao, sem
# ponderacao), ao lado da ativacao ponderada acima. Motor puro em
# `intelligence/methodology_volume.py`; carregamento em
# `intelligence/methodology_volume_service.py`.
#
# Rotas novas na api-core pelo catch-all `{proxy+}` do API Gateway
# (infra/api_gateway.tf): sem Terraform de rota. Autorizacao =
# `require_admin_or_pt` do proprio router, a mesma do preview de ativacao e
# do GET do Modelo.
# ---------------------------------------------------------------------------

MethodologyIntentKind = Literal["standard", "specialization", "maintenance", "cutting"]


def _methodology_intent(kind: str, target_group_ids: Optional[list[int]]):
    from .intelligence.methodology_volume import MethodologyIntent

    return MethodologyIntent(kind=kind, target_group_ids=frozenset(int(g) for g in (target_group_ids or [])))


class MethodologyIntentIn(BaseModel):
    """Body de `PUT /{template_id}/methodology-intent` (PR H). `intent` fora
    do dominio e 422 do proprio Pydantic; as regras cruzadas (alvo so com
    `specialization`, e `specialization` sempre com alvo) ficam em
    `_validate_methodology_intent`."""

    intent: MethodologyIntentKind = "standard"
    target_group_ids: list[int] = Field(default_factory=list)


def _validate_methodology_intent(cur, payload: MethodologyIntentIn) -> list[int]:
    """Devolve os alvos normalizados (sem repeticao, ordenados). 422 quando:
    `specialization` sem alvo (o motor exige alvo para saber quem esta ACIMA
    de proposito); alvo com intencao que nao e `specialization` (a persistida
    e a declaracao do Modelo inteiro - manutencao e cutting valem para todos;
    alvo parcial nessas continua disponivel como "e se" do builder, pela
    query); alvo que nao e um grupo existente."""
    targets = sorted({int(g) for g in payload.target_group_ids})
    if payload.intent == "specialization" and not targets:
        raise HTTPException(status_code=422, detail="Especialização exige ao menos um grupo-alvo (target_group_ids).")
    if payload.intent != "specialization" and targets:
        raise HTTPException(
            status_code=422,
            detail="target_group_ids só se aplica à intenção 'specialization'.",
        )
    if targets:
        placeholders = ",".join(["%s"] * len(targets))
        cur.execute(f"SELECT id FROM muscle_groups WHERE id IN ({placeholders})", tuple(targets))
        existentes = {int(row["id"]) for row in (cur.fetchall() or [])}
        faltando = [g for g in targets if g not in existentes]
        if faltando:
            raise HTTPException(status_code=422, detail=f"Grupo(s) muscular(es) inexistente(s): {faltando}.")
    return targets


def _copy_methodology_intent(cur, source_template_id: int, new_template_id: int) -> None:
    """`duplicate`: a copia herda a intencao declarada e os alvos do original
    (dois INSERT ... SELECT; sem linha no original, nada e copiado = `standard`).
    Tolerante a falha pela mesma razao de `_load_methodology_intent`: a
    intencao e aditiva ao Modelo e nao pode impedir a copia (janela inversa:
    a copia nasce `standard`)."""
    try:
        cur.execute(
            """
            INSERT INTO workout_plan_template_methodology_intent (template_id, intent)
            SELECT %s, intent FROM workout_plan_template_methodology_intent WHERE template_id = %s
            """,
            (new_template_id, source_template_id),
        )
        cur.execute(
            """
            INSERT INTO workout_plan_template_methodology_intent_targets (template_id, muscle_group_id)
            SELECT %s, muscle_group_id FROM workout_plan_template_methodology_intent_targets WHERE template_id = %s
            """,
            (new_template_id, source_template_id),
        )
    except Exception:  # noqa: BLE001 - aditivo; a copia do Modelo segue sem a intencao
        logger.warning(
            "methodology_intent nao copiado do modelo %s para %s", source_template_id, new_template_id, exc_info=True
        )


@router.get("/{template_id}/methodology-intent")
def get_template_methodology_intent(template_id: int):
    """Intencao declarada do Modelo (`standard` sem declaracao). 404 se o
    Modelo nao existe."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM workout_plan_templates WHERE id = %s AND deleted_at IS NULL", (template_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
            declared = _load_methodology_intent(cur, template_id)
    return {"template_id": template_id, **declared}


@router.put("/{template_id}/methodology-intent")
def put_template_methodology_intent(
    template_id: int,
    payload: MethodologyIntentIn,
    identity: Identity = Depends(require_admin_or_pt),
):
    """Declara (ou troca) a intencao do Modelo. Substituicao inteira: a linha
    da intencao e reescrita e os alvos anteriores sao apagados antes de gravar
    os novos - `standard`/`maintenance`/`cutting` ficam sem alvo.

    A intencao VERSIONA (065): trocar so ela grava a versao n+1 com os slots
    e itens da vigente copiados por `INSERT ... SELECT`. Declarar de novo a
    mesma intencao nao cria versao."""
    targets: list[int] = []
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM workout_plan_templates WHERE id = %s AND deleted_at IS NULL", (template_id,)
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
            targets = _validate_methodology_intent(cur, payload)
            cur.execute(
                "DELETE FROM workout_plan_template_methodology_intent_targets WHERE template_id = %s",
                (template_id,),
            )
            cur.execute(
                """
                INSERT INTO workout_plan_template_methodology_intent (template_id, intent)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE intent = VALUES(intent), updated_at = CURRENT_TIMESTAMP
                """,
                (template_id, payload.intent),
            )
            for group_id in targets:
                cur.execute(
                    """
                    INSERT INTO workout_plan_template_methodology_intent_targets (template_id, muscle_group_id)
                    VALUES (%s, %s)
                    """,
                    (template_id, group_id),
                )
            # Modelo sem versao vigente so existe se o codigo ANTIGO o criou na
            # janela de deploy da 065; ele ganha a v1 no proximo PUT completo.
            if current_version(cur, template_id) is not None:
                _write_version(
                    cur,
                    template_id,
                    None,
                    {"intent": payload.intent, "target_group_ids": targets},
                    actor=identity.user_id,
                )
    return {"template_id": template_id, "intent": payload.intent, "target_group_ids": targets}


@router.post("/cardio-prescription-check")
def check_cardio_prescription(payload: CardioPrescriptionCheckIn):
    """Valida UM item de cardio em memória pelo dono (M98 C1): o editor mostra
    a mensagem no card antes de salvar. Mesma regra do PUT
    (`_validate_template_payload` -> `cardio_prescription.validate`); nada é
    gravado. `{"error": null}` = válido."""
    item = {**payload.model_dump(), "item_kind": cardio_prescription.CARDIO_ITEM_KIND}
    return {"error": cardio_prescription.validation_error(item)}


@router.post("/methodology-volume-preview")
def preview_methodology_volume(payload: MethodologyVolumePreviewIn):
    """Avalia os slots EM MEMORIA (nao salva nada) pela metodologia.

    Modo de contagem (073): o do corpo; ausente, o gravado na versao VIGENTE
    do Modelo `template_id` (uma leitura); senao o default (`primary_only`)."""
    from .intelligence.methodology_volume_service import evaluate_slots, evaluation_to_payload

    counting_mode = payload.volume_counting_mode
    if counting_mode is None and payload.template_id is not None:
        counting_mode = counting_mode_of(resolve_version_now(int(payload.template_id)))
    evaluation = evaluate_slots(
        payload.slots,
        payload.volume_mode,
        _methodology_intent(payload.intent, payload.target_group_ids),
        counting_mode_of({"volume_counting_mode": counting_mode}),
    )
    return evaluation_to_payload(evaluation)


@router.get("/{template_id}/methodology-volume")
def get_template_methodology_volume(
    template_id: int,
    intent: Optional[MethodologyIntentKind] = Query(default=None),
    target_group_ids: Optional[list[int]] = Query(default=None),
):
    """Avalia o Modelo SALVO pela metodologia. 404 se nao existe; `intent`
    fora do dominio e 422 (validacao do Query).

    Precedencia da intencao: query explicita (o "e se" do builder, com os
    `target_group_ids` da propria query) > persistida no Modelo (PR H) >
    `standard`. Uma query com `intent` ignora a persistida por inteiro,
    inclusive os alvos gravados."""
    from .intelligence.methodology_volume_service import evaluate_template, evaluation_to_payload

    template = _get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
    applied = (
        _methodology_intent(intent, target_group_ids) if intent is not None else _persisted_methodology_intent(template)
    )
    evaluation = evaluate_template(template, applied)
    response = evaluation_to_payload(evaluation)
    response["template_id"] = template["id"]
    response["template_name"] = template["name"]
    return response


@router.get("/{template_id}/methodology-volume/counting-modes")
def compare_template_methodology_volume_counting_modes(template_id: int):
    """O Modelo SALVO avaliado em CADA modo de contagem do volume, lado a lado
    por grupo (series semanais, bucket, status e as transicoes entre os modos),
    com a intencao persistida. Nada e salvo: e o "e se" do diagnostico antes
    de trocar o modo no editor. As contas sao do motor
    (`methodology_volume.compare_counting_modes`); 404 se o Modelo nao existe."""
    from .intelligence.methodology_volume_service import comparison_to_payload, evaluate_template_all_modes

    template = _get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Modelo de treino não encontrado.")
    response = comparison_to_payload(evaluate_template_all_modes(template, _persisted_methodology_intent(template)))
    response["template_id"] = template["id"]
    response["template_name"] = template["name"]
    return response


# ---------------------------------------------------------------------------
# Motor de Periodizacao: rascunho de modelo gerado por LLM (secao 7 do
# PLANO_20260912.md). 202 + job_id, mesma mecanica de
# `POST /workout-plans/generate`: aqui so valida o pedido, cria a linha de
# auditoria e dispara a Lambda `plan-generator`. **Nenhuma escrita em
# `workout_plan_templates`** acontece neste fluxo - o rascunho volta no job e o
# usuario salva, se quiser, pelo `POST ""` acima.
#
# Rota nova na api-core pelo catch-all do API Gateway: sem Terraform de rota.
# Autorizacao = `require_admin_or_pt` do proprio router (D17).
# ---------------------------------------------------------------------------


def _validate_periodization_request(payload: PeriodizationDraftIn) -> None:
    """Pedido invalido -> 422 ANTES de criar job, log ou gastar token."""
    if payload.volume_mode == "equipment_series" and not payload.gym_id:
        raise HTTPException(
            status_code=422,
            detail="O modo 'equipment_series' exige uma academia (gym_id) para escolher equipamentos.",
        )

    if payload.rag_sources:
        from .rag_chunking import list_training_type_rag_folders

        available = list_training_type_rag_folders()
        unknown = [source for source in payload.rag_sources if source not in available]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Fonte(s) de RAG inexistente(s): {', '.join(unknown)}. "
                    f"Disponiveis: {', '.join(available) or '(nenhuma)'}."
                ),
            )

    if payload.training_type_id is None and payload.gym_id is None:
        return  # nada a conferir no banco: nem perfil de metas, nem academia

    with transaction() as conn:
        with conn.cursor() as cur:
            if payload.training_type_id is not None:
                cur.execute("SELECT id FROM training_types WHERE id = %s", (payload.training_type_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=422, detail="Tipo de treinamento (metas) nao encontrado.")
            if payload.gym_id is not None:
                cur.execute("SELECT id FROM gyms WHERE id = %s", (payload.gym_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=422, detail="Academia nao encontrada.")


@router.post("/periodization-drafts", status_code=202)
def create_periodization_draft(
    payload: PeriodizationDraftIn, identity: Identity = Depends(require_admin_or_pt)
):
    """Gera um RASCUNHO de modelo de treino por LLM, de forma assincrona:
    202 `{job_id}` e o resultado em `GET /jobs/{job_id}`.

    O rascunho vive no job (DynamoDB com TTL) + a auditoria em
    `template_generation_logs`. Nada e gravado em `workout_plan_templates`."""
    from .periodization.core import PROMPT_VERSION
    from .periodization.logs import (
        DAILY_GENERATION_LIMIT,
        count_generations_today,
        create_pending_log,
        sweep_stale_pending_logs,
    )

    _validate_periodization_request(payload)
    request_payload = payload.model_dump(mode="json")

    # M73: fecha as geracoes `pending` orfas (Lambda morta). Transacao propria
    # e best-effort: limpeza de auditoria nunca impede uma geracao nova.
    try:
        with transaction() as conn:
            sweep_stale_pending_logs(conn)
    except Exception:  # noqa: BLE001
        logger.exception("Falha no sweep de template_generation_logs pendentes (M73)")

    with transaction() as conn:
        # Limite de 10 geracoes/dia por usuario (secao 7.3): a propria linha de
        # auditoria e o contador, criada ANTES de invocar a Lambda.
        if count_generations_today(conn, identity.user_id) >= DAILY_GENERATION_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Limite de {DAILY_GENERATION_LIMIT} geracoes automaticas de modelo por dia "
                    "atingido. Tente novamente amanha."
                ),
            )
        generation_log_id = create_pending_log(conn, identity.user_id, request_payload, PROMPT_VERSION)

    job_id = async_jobs.create_job(
        "periodization",
        request={"payload": request_payload, "user_id": identity.user_id},
        owner_user_id=identity.user_id,
    )
    async_jobs.invoke_async(
        "PLAN_GENERATOR_FUNCTION_NAME",
        {
            "job_id": job_id,
            "job_kind": "periodization",
            "payload": request_payload,
            "user_id": identity.user_id,
            "generation_log_id": generation_log_id,
        },
    )
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": "pending", "generation_log_id": generation_log_id},
    )
