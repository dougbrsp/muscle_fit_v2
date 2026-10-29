"""CRUD para a feature "Tipo de Treinamento" (meta semanal de volume por
grupo muscular) — ver plano completo em melhoria-eager-origami.md, seções
1 e 2.

Mesmo esqueleto de `workout_plan_templates.py`: todos os endpoints exigem
`require_admin_or_pt` (admin ou personal_trainer), replace-all transacional
para os `targets` em POST/PUT, validação centralizada sem CHECK/trigger no
banco.

Escopo original desta entrega: só o cadastro + associação (via
`WorkoutPlanTemplateIn.training_type_id`, persistido em
`workout_plan_templates.py`). `get_active_training_type_id_for_user`/
`get_training_type_muscle_group_targets` abaixo são consumidas pelo motor
de geração de ficha v2 orientado a template (`template_volume_planner.py`).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .db import transaction
from .models import TrainingTypeIn, TrainingTypeMuscleGroupTargetIn
from .rag_chunking import list_training_type_rag_folders
from .security import Identity, require_admin_or_pt

router = APIRouter(
    prefix="/training-types",
    tags=["training-types"],
    dependencies=[Depends(require_admin_or_pt)],
)


# ---------------------------------------------------------------------------
# Validação de negócio (seção 2.2 do plano)
# ---------------------------------------------------------------------------


def _validate_rag_folder(rag_folder: Optional[str]) -> None:
    """Se `rag_folder` vier não-nulo, valida contra as pastas reais de
    `workspace/rag/markdown/TIPOS_TREINO/` (protege contra path traversal e
    contra referenciar uma pasta inexistente/renomeada)."""
    if rag_folder is None:
        return
    valid_folders = list_training_type_rag_folders()
    if rag_folder not in valid_folders:
        raise HTTPException(status_code=422, detail="Pasta de RAG inválida.")


def _validate_training_type_payload(
    payload: TrainingTypeIn,
) -> list[TrainingTypeMuscleGroupTargetIn]:
    """Retorna a lista de targets a persistir (já descartando os zerados).
    Levanta HTTPException 422 em caso de violação de regra."""
    _validate_rag_folder(payload.rag_folder)

    seen_muscle_group_ids: set[int] = set()
    targets_to_persist: list[TrainingTypeMuscleGroupTargetIn] = []

    for target in payload.targets:
        # Grupo não configurado: min=target=max=0 não é persistido (mesma
        # filosofia de "ignorar item incompleto" de
        # get_muscle_activation_for_exercises).
        if target.min_weekly_sets == 0 and target.target_weekly_sets == 0 and target.max_weekly_sets == 0:
            continue

        if target.muscle_group_id in seen_muscle_group_ids:
            raise HTTPException(
                status_code=422,
                detail=f"Grupo muscular {target.muscle_group_id} duplicado nas metas.",
            )
        seen_muscle_group_ids.add(target.muscle_group_id)

        if not (0 <= target.min_weekly_sets <= target.target_weekly_sets <= target.max_weekly_sets):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Grupo muscular {target.muscle_group_id}: valores inválidos "
                    f"(mínimo={target.min_weekly_sets}, alvo={target.target_weekly_sets}, "
                    f"máximo={target.max_weekly_sets}) — exige 0 <= mínimo <= alvo <= máximo."
                ),
            )

        targets_to_persist.append(target)

    return targets_to_persist


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _list_training_types(is_active: Optional[bool]) -> list[dict[str, Any]]:
    where = []
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
                    t.id, t.name, t.description, t.rag_folder, t.is_active,
                    t.created_by_user_id, u.username AS created_by_username,
                    t.created_at, t.updated_at,
                    COUNT(g.id) AS muscle_groups_count
                FROM training_types t
                LEFT JOIN users u ON u.id = t.created_by_user_id
                LEFT JOIN training_type_muscle_group_targets g
                       ON g.training_type_id = t.id AND g.superseded_at IS NULL
                {where_sql}
                GROUP BY t.id, t.name, t.description, t.rag_folder, t.is_active,
                    t.created_by_user_id, u.username, t.created_at, t.updated_at
                ORDER BY t.created_at DESC
                """,
                tuple(params),
            )
            return cur.fetchall()


def _get_training_type(training_type_id: int) -> Optional[dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, description, rag_folder, is_active,
                       created_by_user_id, created_at, updated_at
                FROM training_types
                WHERE id = %s
                """,
                (training_type_id,),
            )
            training_type = cur.fetchone()
            if not training_type:
                return None

            cur.execute(
                """
                SELECT g.muscle_group_id, mg.name AS muscle_group_name,
                       g.min_weekly_sets, g.target_weekly_sets, g.max_weekly_sets
                FROM training_type_muscle_group_targets g
                JOIN muscle_groups mg ON mg.id = g.muscle_group_id
                WHERE g.training_type_id = %s AND g.superseded_at IS NULL
                ORDER BY mg.name ASC
                """,
                (training_type_id,),
            )
            training_type["targets"] = cur.fetchall()

    return training_type


@router.get("")
def list_training_types(is_active: Optional[bool] = Query(default=None)):
    return {"items": _list_training_types(is_active)}


@router.get("/rag-folders")
def get_training_type_rag_folders():
    return {"items": list_training_type_rag_folders()}


@router.get("/{training_type_id}")
def get_training_type(training_type_id: int):
    training_type = _get_training_type(training_type_id)
    if not training_type:
        raise HTTPException(status_code=404, detail="Tipo de treinamento não encontrado.")
    return training_type


# ---------------------------------------------------------------------------
# Escrita (replace-all transacional)
# ---------------------------------------------------------------------------


def _replace_training_type_targets(
    cur, training_type_id: int, targets: list[TrainingTypeMuscleGroupTargetIn]
) -> None:
    """Substitui o conjunto de metas PRESERVANDO o passado (M12, migracao
    052): o DELETE fisico que existia aqui apagava a meta anterior e, com
    ela, a unica forma de saber contra qual objetivo um ciclo antigo do
    dashboard deveria ser lido. A regra de vigencia mora em
    `training_type_targets`, que tambem e quem responde as leituras - o mesmo
    modulo escreve e le o intervalo, como `template_assignments` faz para a
    atribuicao de Modelo desde o M23."""
    from .template_assignments import db_now
    from .training_type_targets import replace_targets

    replace_targets(cur, training_type_id, targets, moment=db_now(cur))


@router.post("")
def create_training_type(payload: TrainingTypeIn, identity: Identity = Depends(require_admin_or_pt)):
    targets_to_persist = _validate_training_type_payload(payload)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO training_types (name, description, rag_folder, is_active, created_by_user_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (payload.name, payload.description, payload.rag_folder, payload.is_active, identity.user_id),
            )
            training_type_id = cur.lastrowid
            _replace_training_type_targets(cur, training_type_id, targets_to_persist)

    return _get_training_type(training_type_id)


@router.post("/{training_type_id}/duplicate")
def duplicate_training_type(training_type_id: int, identity: Identity = Depends(require_admin_or_pt)):
    original = _get_training_type(training_type_id)
    if not original:
        raise HTTPException(status_code=404, detail="Tipo de treinamento não encontrado.")

    targets_to_persist = [
        TrainingTypeMuscleGroupTargetIn(
            muscle_group_id=target["muscle_group_id"],
            min_weekly_sets=target["min_weekly_sets"],
            target_weekly_sets=target["target_weekly_sets"],
            max_weekly_sets=target["max_weekly_sets"],
        )
        for target in original["targets"]
    ]

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO training_types (name, description, rag_folder, is_active, created_by_user_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    f"{original['name']} (cópia)",
                    original["description"],
                    original.get("rag_folder"),
                    True,
                    identity.user_id,
                ),
            )
            new_training_type_id = cur.lastrowid
            _replace_training_type_targets(cur, new_training_type_id, targets_to_persist)

    return _get_training_type(new_training_type_id)


@router.put("/{training_type_id}")
def update_training_type(training_type_id: int, payload: TrainingTypeIn):
    targets_to_persist = _validate_training_type_payload(payload)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM training_types WHERE id = %s", (training_type_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Tipo de treinamento não encontrado.")

            cur.execute(
                """
                UPDATE training_types
                SET name = %s, description = %s, rag_folder = %s, is_active = %s
                WHERE id = %s
                """,
                (payload.name, payload.description, payload.rag_folder, payload.is_active, training_type_id),
            )
            _replace_training_type_targets(cur, training_type_id, targets_to_persist)

    return _get_training_type(training_type_id)


@router.patch("/{training_type_id}/status")
def set_training_type_status(training_type_id: int, body: dict = Body(...)):
    is_active = bool(body.get("is_active"))

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM training_types WHERE id = %s", (training_type_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Tipo de treinamento não encontrado.")
            cur.execute(
                "UPDATE training_types SET is_active = %s WHERE id = %s",
                (is_active, training_type_id),
            )

    return _get_training_type(training_type_id)


@router.delete("/{training_type_id}")
def delete_training_type(training_type_id: int):
    # Exclusão física: CASCADE limpa training_type_muscle_group_targets;
    # workout_plan_templates.training_type_id cai para NULL via
    # ON DELETE SET NULL, nunca bloqueia a exclusão.
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM training_types WHERE id = %s", (training_type_id,))
            deleted = cur.rowcount > 0

    if not deleted:
        raise HTTPException(status_code=404, detail="Tipo de treinamento não encontrado.")
    return {"status": "ok", "training_type_id": training_type_id}


# ---------------------------------------------------------------------------
# Leitura interna (não é CRUD novo — consumida pelo motor de geração de
# ficha v2 orientado a template, ver
# `intelligence/template_volume_planner.py`). Sem router/endpoint HTTP
# próprio.
# ---------------------------------------------------------------------------


def resolve_active_training_type_for_user(user_id: int) -> Optional[int]:
    """Resolve o `training_type_id` "ativo" de um usuario para o dashboard
    novo (`GET /analytics/dashboard-muscle-status`,
    ver plano em C:\\Users\\dougb\\.claude\\plans\\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md,
    Fase 1): o `training_type_id` do Modelo VIGENTE do usuario.

    Depois da migracao 049 (M23) "vigente" e um fato do banco - a unica
    atribuicao com `unassigned_at IS NULL` -, e nao mais o desempate
    silencioso por `ORDER BY assigned_at DESC LIMIT 1` sobre varias
    atribuicoes simultaneas. A resolucao vem de
    `template_assignments.resolve_active_assignment`, o unico lugar que
    responde "qual e o Modelo deste usuario".

    MUDANCA DE COMPORTAMENTO EM UM CASO: antes, um Modelo vigente SEM
    `training_type_id` era PULADO e a busca continuava para o anterior, que
    podia ter um. Agora o vigente e o unico consultado - se ele nao tem tipo
    de treino, a resposta e `None` e o chamador cai no fallback generico. E o
    comportamento correto: herdar a meta de um modelo que o usuario ja
    trocou e exatamente o que o M23 veio corrigir.

    Retorna `None` quando o usuario nao tem Modelo vigente ATIVO ou quando o
    vigente nao tem `training_type_id` preenchido - o chamador deve cair no
    fallback generico (mesmos limiares de `MuscleActivationTable.tsx`/
    `activationAssessment`, nunca um segundo padrao de fallback)."""
    from .template_assignments import resolve_active_assignment

    assignment = resolve_active_assignment(user_id)
    if assignment is None:
        return None
    return assignment.training_type_id


def resolve_training_type_for_window(user_id: int, date_to: Optional[Any] = None) -> Optional[int]:
    """`training_type_id` que governa uma janela que TERMINA em `date_to`
    (PR3 do versionamento de Modelo) - a pergunta HISTORICA.

    Sem `date_to`: o do Modelo vigente hoje, exatamente
    `resolve_active_training_type_for_user`. Com ele: o da VERSAO do Modelo atribuido no fim da janela
    (`template_assignments.resolve_assignment_at`, que le a versao daquele
    instante no MESMO statement) - uma janela que atravessa duas versoes e
    lida pela do fim, a regra do M12. Um statement nos dois caminhos."""
    from .template_assignments import resolve_assignment_at

    if date_to is None:
        return resolve_active_training_type_for_user(user_id)
    assignment = resolve_assignment_at(user_id, date_to)
    return assignment.training_type_id if assignment is not None else None


def get_training_type_muscle_group_targets(training_type_id: int) -> dict[int, dict[str, float]]:
    """`muscle_group_id -> {"min": ..., "target": ..., "max": ...}` (séries
    semanais) VIGENTES de um `training_type`. Reaproveitada pelo
    `TemplateVolumePlanner` (motor de geração de ficha v2) como fonte da
    meta semanal por grupo muscular.

    Desde o M12 (migração 052) a tabela guarda TODAS as versões da meta, e
    esta função continua respondendo o que sempre respondeu: a vigente. Quem
    precisa da meta de uma data — e só o histórico do dashboard precisa —
    chama `training_type_targets.resolve_targets_for_window`. Manter a
    assinatura preserva os oito chamadores existentes (geração, periodização,
    alertas, analytics) exatamente como estavam: planejar o futuro se faz
    contra o alvo de hoje."""
    from .training_type_targets import resolve_targets_now

    return resolve_targets_now(training_type_id)
