"""Motor de geração do modo `volume_mode='equipment_series'` (LOTE 2) dos
Modelos de Ficha de Treino Personalizados (`workout_plan_templates`).

Diferente do motor v2 orientado a Tipo de Treinamento
(`generation/mode_a_legacy.py`, orçamento percentual + IA escolhendo
exercício), este modo é 100% determinístico: o usuário já escolheu, item a
item do slot, o grupo muscular, o equipamento, o exercício e o número fixo
de séries ao montar o modelo (`workout_plan_templates.py::_validate_template_payload`,
item_kind='equipment') — este motor só PRECISA resolver esses ids contra o
catálogo de equipamentos da ACADEMIA informada (`gym_id`) no momento da
geração (a academia usada para gerar pode ter equipamento diferente da
academia em que o modelo foi originalmente configurado), aplicar
recuperação/progressão via os motores oficiais e devolver o rascunho no
MESMO formato de dict que `generate_plan_draft_from_template_v2` produz, para
o restante do pipeline de `POST /workout-plans/generate` (serialização,
consumo pelo frontend) funcionar sem nenhuma mudança de contrato.

Reaproveita SEMPRE as funções oficiais já existentes — nunca reimplementa:
- `services.is_gym_equipment_pair_active` para confirmar o par (mapping_id,
  equipment_id) contra a academia (o fallback de equipamento filtra pelo
  MESMO exercício, ver `_find_gym_equipment_for_exercise`);
- `workout_plan_ai.gather_ficha_context`/`project_recovery_for_date` para
  recuperação muscular projetada para `plan_date`/`plan_time`;
- `workout_plan_ai.assign_progression_indicators` (que por sua vez chama
  `progression_service.suggest_load_and_reps`) para carga/reps sugeridos;
- `VolumeService.get_muscle_activation_for_exercises` para a ativação
  muscular real usada no resumo de auditoria.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from ..domain import cardio_prescription
from .. import equipment_recommender
from ..db import transaction
from ..template_versions import current_version_id_of
from ..domain.prescription_defaults import DEFAULT_REPS_TARGET, midpoint_rest_seconds

logger = logging.getLogger("fitness.generation")

# ---------------------------------------------------------------------------
# Helpers de leitura (nomes/linhas auxiliares — sem lógica de negócio)
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


def _load_exercise_names(mapping_ids: set[int]) -> dict[int, str]:
    if not mapping_ids:
        return {}
    with transaction() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(mapping_ids))
            cur.execute(
                f"SELECT id, name FROM exercise_catalog WHERE id IN ({placeholders})",
                tuple(mapping_ids),
            )
            return {row["id"]: row["name"] for row in cur.fetchall()}


def _load_amazfit_names(mapping_ids: set[int]) -> dict[int, str]:
    """Nome Amazfit/Zepp (quando cadastrado) de cada mapping_id — mesma fonte
    usada por `GET /gym-equipment/recommend(-all)` (`exercise_external_mappings`,
    provider='AMAZFIT'). Campo informativo (paridade com o shape do motor v2);
    ausência não bloqueia a geração."""
    if not mapping_ids:
        return {}
    with transaction() as conn:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(mapping_ids))
            cur.execute(
                f"""
                SELECT exercise_id, external_name
                FROM exercise_external_mappings
                WHERE provider = 'AMAZFIT' AND is_active = 1 AND exercise_id IN ({placeholders})
                ORDER BY id ASC
                """,
                tuple(mapping_ids),
            )
            names: dict[int, str] = {}
            for row in cur.fetchall():
                names.setdefault(row["exercise_id"], row["external_name"])
            return names


def _find_active_gym_equipment(gym_id: int, mapping_id: int, equipment_id: int) -> Optional[dict[str, Any]]:
    """Linha completa (`gym_equipment.id` + nome do equipamento) do par já
    confirmado ATIVO por `services.is_gym_equipment_pair_active` — separado
    numa consulta própria porque aquela função só devolve bool (contrato
    pedido para ela), e aqui precisamos do `id` (usado como
    `preferred_equipment_id`, FK real de `workout_plan_exercises` para
    `gym_equipment.id`) e do nome do equipamento para exibição.

    Resolve os dois lados do `mapping_id` por `exercise_legacy_id_map` (ids
    absorvidos pela consolidação do catálogo canônico) EXATAMENTE com o mesmo
    critério de `services.is_gym_equipment_pair_active` — se as duas consultas
    divergissem, o par voltaria como ativo mas sem linha, e o item perderia
    silenciosamente `equipment_name`/`preferred_equipment_id`."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ge.id, ge.equipment_id, eq.name AS equipment_name
                FROM gym_equipment ge
                JOIN equipment eq ON eq.id = ge.equipment_id
                LEFT JOIN exercise_legacy_id_map ge_elm ON ge_elm.old_exercise_id = ge.mapping_id
                LEFT JOIN exercise_legacy_id_map in_elm ON in_elm.old_exercise_id = %s
                WHERE ge.gym_id = %s
                  AND COALESCE(ge_elm.canonical_exercise_id, ge.mapping_id)
                      = COALESCE(in_elm.canonical_exercise_id, %s)
                  AND ge.equipment_id = %s
                  AND ge.is_active = 1
                LIMIT 1
                """,
                (mapping_id, gym_id, mapping_id, equipment_id),
            )
            return cur.fetchone()


def _find_gym_equipment_for_exercise(
    gym_id: int, mapping_id: int, user_id: Optional[int] = None
) -> Optional[dict[str, Any]]:
    """Associação ATIVA da academia para o MESMO exercício (`mapping_id`,
    comparado pelo id canônico dos dois lados via `exercise_legacy_id_map`,
    mesmo critério de `_find_active_gym_equipment`), com qualquer
    equipamento. Entre várias, vale o ranking único do usuário (PR-08:
    estrelas, depois o menos usado recentemente POR ELE, nome, id — o mesmo
    de `GET /gym-equipment/recommend`). Nunca devolve associação de outro
    exercício do grupo muscular."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ge.id, ge.mapping_id, ge.equipment_id, eq.name AS equipment_name,
                       COALESCE(ge_elm.canonical_exercise_id, ge.mapping_id) AS canonical_exercise_id
                FROM gym_equipment ge
                JOIN equipment eq ON eq.id = ge.equipment_id
                LEFT JOIN exercise_legacy_id_map ge_elm ON ge_elm.old_exercise_id = ge.mapping_id
                LEFT JOIN exercise_legacy_id_map in_elm ON in_elm.old_exercise_id = %s
                WHERE ge.gym_id = %s
                  AND COALESCE(ge_elm.canonical_exercise_id, ge.mapping_id)
                      = COALESCE(in_elm.canonical_exercise_id, %s)
                  AND ge.is_active = 1
                """,
                (mapping_id, gym_id, mapping_id),
            )
            ranked = equipment_recommender.rank_gym_equipment_rows(
                cur, cur.fetchall() or [], gym_id=gym_id, user_id=user_id
            )
    if not ranked:
        return None
    return {key: ranked[0][key] for key in ("id", "mapping_id", "equipment_id", "equipment_name")}


def _recommend_for_group(gym_id: int, group_name: str, user_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Fallback final (grupo muscular sem NENHUM equipamento para o
    `mapping_id` original na academia): o recomendado do grupo no ranking do
    usuário — a MESMA função que atende `GET /gym-equipment/recommend`,
    chamada em Python com o `user_id` de quem pediu a ficha (PR-08; antes ia
    por HTTP interno com o token mestre, que resolveria como o admin)."""
    with transaction() as conn:
        return equipment_recommender.recommend_for_group(
            conn, gym_id=gym_id, user_id=user_id, group=group_name
        ).get("recommended")


# ---------------------------------------------------------------------------
# Resolução do par (mapping_id, equipment_id) contra a academia
# ---------------------------------------------------------------------------


def _resolve_item_equipment(
    item: dict[str, Any],
    gym_id: Optional[int],
    muscle_group_names: dict[int, str],
    warnings: list[str],
    user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Implementa o passo 1 do pipeline (ver docstring do módulo): confirma o
    par (mapping_id, equipment_id) do item contra `gym_id`, com dois níveis
    de fallback, sem nunca travar a geração (pior caso: mantém o item
    original e registra aviso)."""
    from .. import services

    mapping_id = item.get("mapping_id")
    equipment_id = item.get("equipment_id")
    group_id = item.get("muscle_group_id")
    group_name = muscle_group_names.get(group_id) if group_id is not None else None

    if not gym_id:
        # Sem academia informada não há catálogo de equipamentos para
        # confrontar — mantém o item exatamente como configurado no modelo.
        return {
            "mapping_id": mapping_id,
            "equipment_id": equipment_id,
            "equipment_name": None,
            "preferred_equipment_id": None,
        }

    if mapping_id and equipment_id and services.is_gym_equipment_pair_active(gym_id, mapping_id, equipment_id):
        active_row = _find_active_gym_equipment(gym_id, mapping_id, equipment_id)
        return {
            "mapping_id": mapping_id,
            "equipment_id": equipment_id,
            "equipment_name": (active_row or {}).get("equipment_name"),
            "preferred_equipment_id": (active_row or {}).get("id"),
        }

    # Fallback intermediário: MESMO exercício (mapping_id canônico), outro
    # equipamento da academia. Filtra estritamente pelo exercício — antes
    # reaproveitava `services.resolve_equipment_id`, que escolhe pelo GRUPO
    # MUSCULAR PRIMÁRIO e devolvia a associação de OUTRO exercício do grupo
    # (ficha 110 em produção, 2026-09-17: "Desenvolvimento de ombros na
    # máquina" com a associação de "Desenvolvimento com halteres" e
    # "Extensão de tríceps na máquina" com a de "Tríceps pulley com barra V"
    # no Crossover), mantendo nome/descrição do exercício original com o
    # equipamento de outro.
    if mapping_id:
        fallback_row = _find_gym_equipment_for_exercise(gym_id, mapping_id, user_id)
        if fallback_row:
            warnings.append(
                f"{group_name or f'mapping_id {mapping_id}'}: equipamento original indisponível na "
                "academia; substituído automaticamente por outro equipamento para o mesmo exercício."
            )
            return {
                "mapping_id": mapping_id,
                "equipment_id": fallback_row.get("equipment_id"),
                "equipment_name": fallback_row.get("equipment_name"),
                "preferred_equipment_id": fallback_row.get("id"),
            }

    # Fallback final: a academia não tem NENHUM equipamento para este
    # mapping_id — recomenda uma alternativa do MESMO grupo muscular
    # (pode trocar de exercício, não só de equipamento).
    if group_name:
        recommended = _recommend_for_group(gym_id, group_name, user_id)
        if recommended:
            warnings.append(
                f"{group_name}: exercício/equipamento original indisponível na academia; substituído por "
                "alternativa do mesmo grupo muscular."
            )
            return {
                "mapping_id": recommended.get("mapping_id"),
                "equipment_id": recommended.get("equipment_id"),
                "equipment_name": recommended.get("equipment_name"),
                "preferred_equipment_id": recommended.get("id"),
                "exercise_name": recommended.get("mapping_exercise_name"),
                "amazfit_name": recommended.get("mapping_amazfit_name"),
            }

    # Nada encontrado: mantém o item original mesmo assim (escolha fixa do
    # usuário, não travar a geração) — aviso claro para revisão manual.
    warnings.append(
        f"{group_name or f'mapping_id {mapping_id}'}: nenhum equipamento disponível na academia para este "
        "grupo muscular; exercício mantido conforme modelo, revise manualmente."
    )
    return {
        "mapping_id": mapping_id,
        "equipment_id": equipment_id,
        "equipment_name": None,
        "preferred_equipment_id": None,
    }


# ---------------------------------------------------------------------------
# Orquestração principal
# ---------------------------------------------------------------------------


def generate_plan_draft_from_equipment_template(
    template: dict[str, Any],
    slot: dict[str, Any],
    gym_id: Optional[int],
    gym_name: Optional[str],
    plan_date: Optional[str],
    user_id: int,
    min_rest_seconds: int,
    max_rest_seconds: int,
    plan_time: Optional[str] = None,
) -> dict[str, Any]:
    from ..intelligence.template_volume_domain import GenerationVolumeSummary
    from ..intelligence.volume_service import VolumeService
    from ..workout_plan_ai import (
        WorkoutPlanGenerationError,
        assign_progression_indicators,
        assign_warmup_flags,
        gather_ficha_context,
        normalize_load_target,
        project_recovery_for_date,
        resolve_gym_name,
    )
    from ..workout_plan_templates_engine import _empty_rest_day_draft

    if slot.get("slot_type") == "descanso":
        return _empty_rest_day_draft(template, slot, gym_id, gym_name, plan_date)

    template_rest_seconds = midpoint_rest_seconds(min_rest_seconds, max_rest_seconds)

    items = sorted(
        (item for item in (slot.get("items") or []) if item.get("item_kind") == "equipment"),
        key=lambda item: item.get("item_order") or 0,
    )
    if not items:
        raise WorkoutPlanGenerationError(
            "Este slot não tem nenhum item de equipamento configurado (modo 'equipment_series')."
        )

    try:
        muscle_group_ids: set[int] = {
            item["muscle_group_id"] for item in items if item.get("muscle_group_id") is not None
        }
        muscle_group_names = _load_muscle_group_names(muscle_group_ids)

        # Mesmo contexto usado pelo motor v2 (recent_exercise_logs para
        # progressão, muscle_recovery para recuperação, gyms para o nome de
        # exibição) — uma única chamada, sem reimplementar nada.
        ficha_context = gather_ficha_context(gym_id, user_id)
        gym_name = resolve_gym_name(gym_id, gym_name, ficha_context.get("gyms") or [])

        # Recuperação projetada para plan_date/plan_time: reaproveita
        # `project_recovery_for_date` (mesma fórmula usada por
        # template_volume_planner.py para o motor v2) sobre
        # `ficha_context["muscle_recovery"]` — já filtrado por `user_id` em
        # `gather_ficha_context` (correção de vazamento entre usuários de
        # 2026-09-01). `template_volume_planner.py` busca a recuperação de
        # novo via `MuscleRecoveryService.get_recovery_overview(user_id=...)`
        # (PR-09: antes usava `_internal_get` com token mestre = admin);
        # aqui reaproveitamos o contexto já carregado acima em vez de
        # duplicar essa busca —
        # mesma fonte de dados (`MuscleRecoveryService.get_recovery_overview`),
        # só sem a segunda ida à rede.
        projected_recovery = project_recovery_for_date(
            ficha_context.get("muscle_recovery") or [], plan_date, plan_time, user_id=user_id
        )
        recovery_status_by_group: dict[int, str] = {
            item["muscle_group_id"]: item.get("projected_readiness_status") or "READY"
            for item in projected_recovery
            if item.get("muscle_group_id") is not None
        }

        warnings: list[str] = []
        if not gym_id:
            warnings.append(
                "gym_id não informado: validação de equipamento contra a academia não realizada; "
                "itens do modelo usados exatamente como configurados."
            )

        resolved_items = [
            _resolve_item_equipment(item, gym_id, muscle_group_names, warnings, user_id) for item in items
        ]

        mapping_ids: set[int] = {r["mapping_id"] for r in resolved_items if r.get("mapping_id")}
        exercise_names = _load_exercise_names(mapping_ids)
        amazfit_names = _load_amazfit_names(mapping_ids)

        final_exercises: list[dict[str, Any]] = []
        for item, resolved in zip(items, resolved_items):
            mapping_id = resolved.get("mapping_id")
            group_id = item.get("muscle_group_id")
            final_exercises.append(
                {
                    "exercise_name": exercise_names.get(mapping_id) or resolved.get("exercise_name"),
                    "amazfit_name": amazfit_names.get(mapping_id) or resolved.get("amazfit_name"),
                    "muscle_primary": muscle_group_names.get(group_id),
                    "muscle_group_id": group_id,
                    "muscle_secondary": None,
                    "equipment_name": resolved.get("equipment_name"),
                    # `equipment_id` (informativo, id do CATÁLOGO `equipment`)
                    # e `preferred_equipment_id` (FK real esperada por
                    # `WorkoutPlanExerciseIn.preferred_equipment_id` ->
                    # `gym_equipment.id`) — o motor v2 não emite nenhum dos
                    # dois no draft (deixa o save-time auto-resolver via
                    # `services.resolve_equipment_id`, que escolhe o
                    # equipamento MENOS USADO do grupo, não necessariamente o
                    # mesmo da geração). Aqui isso reverteria exatamente a
                    # escolha explícita do usuário no modelo, então
                    # preenchemos `preferred_equipment_id` já resolvido nesta
                    # geração — ver ressalva no relatório final sobre este
                    # ponto (campo extra, ignorado por Pydantic se o
                    # frontend não o repassar).
                    "equipment_id": resolved.get("equipment_id"),
                    "preferred_equipment_id": resolved.get("preferred_equipment_id"),
                    "mapping_id": mapping_id,
                    # sets_count é SEMPRE o valor fixo do item do modelo —
                    # nunca recalculado por este motor.
                    "sets_count": item.get("sets_count"),
                    "reps_target": item.get("reps_target") or DEFAULT_REPS_TARGET,
                    "load_target": None,
                    "rpe_target": item.get("rpe_target") or "8",
                    # Descanso definido no Modelo (Fase 7, Frente B) SOBREPÕE o
                    # intervalo da geração, sem ser limitado pelo mín/máx; sem
                    # valor no item, vale o ponto médio do intervalo.
                    "rest_seconds": item.get("rest_seconds") or template_rest_seconds,
                    "warmup": False,
                    "substitution_options": [],
                    "notes": "",
                    # Espelho PURO (sem recalcular nada) dos 10 campos de
                    # técnica avançada/aquecimento configurado/ajuste/pico de
                    # contração do item do slot — CUIDADO:
                    # `warmup_reps`/`warmup_rest_seconds` são campos DIFERENTES
                    # da chave `warmup` (bool) acima, que é a heurística de
                    # assign_warmup_flags chamada logo abaixo e nunca deve ser
                    # confundida com estes.
                    "advanced_technique": item.get("advanced_technique"),
                    "advanced_technique_applications": item.get("advanced_technique_applications"),
                    "advanced_technique_duration_seconds": item.get("advanced_technique_duration_seconds"),
                    "warmup_sets_count": item.get("warmup_sets_count"),
                    "warmup_reps": item.get("warmup_reps"),
                    "warmup_rest_seconds": item.get("warmup_rest_seconds"),
                    "feeler_set_sets_count": item.get("feeler_set_sets_count"),
                    "feeler_set_reps": item.get("feeler_set_reps"),
                    "feeler_set_rest_seconds": item.get("feeler_set_rest_seconds"),
                    "peak_contraction_seconds": item.get("peak_contraction_seconds"),
                    # Campo informativo (não existe no shape do motor v2):
                    # status de recuperação projetado do grupo muscular deste
                    # exercício, para o usuário revisar no rascunho.
                    "recovery_status": recovery_status_by_group.get(group_id, "READY"),
                }
            )

        assign_warmup_flags(final_exercises)
        for index, exercise in enumerate(final_exercises, start=1):
            exercise["sequence"] = index

        # Motor oficial de progressão (suggest_load_and_reps via
        # assign_progression_indicators) — nunca recalculado aqui.
        assign_progression_indicators(final_exercises, user_id=user_id)
        for exercise in final_exercises:
            exercise["load_target"] = normalize_load_target(exercise.get("suggested_load_kg"))

        # Ativação muscular real (auditoria) via motor oficial
        # VolumeService.get_muscle_activation_for_exercises — usa
        # mapping_id/sets_count já FINAIS (pós-resolução de equipamento).
        # M76 E3: payload unico (com a tecnica do exercicio).
        from ..intelligence.technique_volume import activation_payload

        activation_input = [activation_payload(ex) for ex in final_exercises]
        activation_rows = VolumeService().get_muscle_activation_for_exercises(activation_input)
        activation_by_group_name = {row["muscle_group"]: row for row in activation_rows}

        def _group_sets_count(group_id: int) -> int:
            return sum(item.get("sets_count") or 0 for item in items if item.get("muscle_group_id") == group_id)

        volume_summary = GenerationVolumeSummary()
        for group_id in sorted(muscle_group_ids):
            group_name = muscle_group_names.get(group_id)
            if not group_name:
                continue
            activation_row = activation_by_group_name.get(group_name)
            total_equivalent = float(activation_row["total_equivalent"]) if activation_row else 0.0
            recovery_status = recovery_status_by_group.get(group_id, "READY")

            volume_summary.add_entry(
                GenerationVolumeSummary.Entry(
                    muscle_group_id=group_id,
                    muscle_group_name=group_name,
                    # weekly_target/slot_min/slot_max/deviation_status não
                    # fazem sentido no modo 'equipment_series' (sem
                    # orçamento percentual — o dataclass foi desenhado para
                    # o motor v2/percent_budget). Preenchidos com os valores
                    # mais neutros que o dataclass aceita (0.0/"BALANCED")
                    # só para reaproveitar a mesma estrutura de auditoria
                    # sem criar um dataclass paralelo — ver `note` abaixo.
                    weekly_target=0.0,
                    slot_min=0.0,
                    slot_max=0.0,
                    planned_activation=total_equivalent,
                    deviation_status="BALANCED",
                    recovery_status=recovery_status,
                    cycle_planned_total=total_equivalent,
                    weekly_volume_deficit=0.0,
                    note=(
                        "Modo 'equipment_series': séries fixas definidas pelo usuário no modelo, sem "
                        "orçamento percentual — weekly_target/slot_min/slot_max/deviation_status não se "
                        "aplicam aqui (dataclass reaproveitado do motor v2/percent_budget)."
                    ),
                )
            )

            if recovery_status == "NO_DIRECT_WORK":
                # Nunca remove o exercício automaticamente (escolha fixa do
                # usuário) — só avisa para revisão manual no rascunho.
                volume_summary.warnings.append(
                    f"{group_name}: grupo sem recuperação suficiente (NO_DIRECT_WORK) — exercício mantido "
                    "conforme o modelo (modo 'equipment_series' não remove itens automaticamente); revise "
                    "manualmente antes de salvar."
                )

        volume_summary.warnings.extend(warnings)

        template_slot_targets = [
            {
                "muscle_group_id": group_id,
                "muscle_group_name": muscle_group_names.get(group_id),
                "sets_count": _group_sets_count(group_id),
                # Sem teto/recuperação por percentual neste modo — None
                # (em vez de 0) para a tabela "Ativação muscular" do
                # frontend (MuscleActivationTable.tsx::activationAssessment)
                # não sinalizar falso "excesso" quando maxSets<=0.
                "effective_max_sets": None,
            }
            for group_id in sorted(muscle_group_ids)
        ]

        generation_summary_text = (
            f"Ficha gerada via motor de equipamento fixo (modo 'equipment_series') a partir do modelo "
            f"'{template.get('name')}', slot '{slot.get('name')}'. Grupo muscular, equipamento, exercício "
            f"e número de séries definidos diretamente pelo usuário no modelo — sem IA escolhendo "
            f"exercício ou volume; apenas o par equipamento/exercício foi confrontado contra o catálogo "
            f"da academia."
        )

        return {
            "name": f"{template.get('name')} — {slot.get('name')}",
            "plan_type": template.get("template_type") or "full_body",
            "goal": template.get("description"),
            "plan_date": plan_date,
            "gym_id": gym_id,
            "gym_name": gym_name,
            "template_id": template.get("id"),
            "template_slot_id": slot.get("id"),
            # 066: versao do Modelo que gerou o rascunho (congela no save).
            "template_version_id": current_version_id_of(template),
            "template_slot_sequence": slot.get("sequence"),
            "template_slot_targets": template_slot_targets,
            "is_rest_day": False,
            # M98 C0: o cardio do slot chega ao rascunho (dono `cardio_prescription`).
            **cardio_prescription.draft_cardio(slot.get("items")),
            "exercises": final_exercises,
            "excluded_due_to_recovery": [],
            "excluded_due_to_duplicate": [],
            "adjusted_due_to_catalog_mismatch": [],
            "excluded_due_to_no_equipment": [],
            "reduced_dose_groups": [],
            "insufficient_target_volume_groups": [],
            "generation_summary": generation_summary_text,
            "raw_payload": {
                "template_id": template.get("id"),
                "slot_id": slot.get("id"),
                "slot_sequence": slot.get("sequence"),
                "engine_version": "equipment_series",
                "used_ai": False,
                "repair_log": [],
                "volume_planning": volume_summary.as_dict(),
            },
        }
    except WorkoutPlanGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - qualquer erro irrecuperável de banco/dados vira
        # WorkoutPlanGenerationError (nunca propaga exception genérica —
        # ponto 8 do escopo do LOTE 2), que o chamador (workout_plans.py)
        # converte em HTTP 502.
        # SEG-12: a excecao interna vai para o log, nunca para o `detail`.
        logger.exception("Falha inesperada na geracao 'equipment_series' (legado)")
        raise WorkoutPlanGenerationError(
            "Falha ao gerar ficha via modo 'equipment_series'."
        ) from exc
