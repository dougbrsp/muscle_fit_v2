"""Modo B DETERMINISTICO - `volume_mode='equipment_series'` (PR-12;
PLANO_20260912.md secoes 6.2, 5.9 e decisoes D9, D13, D14).

Antes deste PR o modo B vivia em `app/workout_plan_templates_engine_equipment.py`
e prescrevia pelo caminho legado (`progression_service.suggest_load_and_reps`
via `assign_progression_indicators`). Aquele arquivo agora e
`generation/mode_b_legacy.py`, congelado, e sai no PR-19/20; este e o motor
novo, ligado pela flag `GENERATION_ENGINE` (ver `generation/__init__.py`).

**O que muda de verdade.** A divisao de responsabilidades da secao 6.2:

| decide | quem |
|---|---|
| grupo, `mapping_id`, `equipment_id`, `sets_count`, descanso, tecnica e parametros, aquecimento, ajuste, pico | o **modelo** (copiado integralmente) |
| reps e carga | a **progressao v2** (trilhas + fases, `domain/progression_engine.py`) |
| teto de RPE, falha permitida, validade da tecnica | a **seguranca** (`domain/training_safety.py`) |

Tres consequencias que o codigo abaixo materializa:

1. **Series sao fixas pelo modelo.** A F5 (+1 serie) nunca altera a ficha:
   vira recomendacao de edicao ao PT, com `blocked_by=MODEL_FIXED_SETS`, e o
   grupo segue para F6 (D9).
2. **`rpe_target` do modelo e intencao, nao ordem.** Passa por
   `training_safety.compose`; acima do teto vira clamp auditado + aviso na
   ficha, e a geracao NUNCA e bloqueada por isso.
3. **Equipamento indisponivel na academia** segue a D13 (`_resolve_item_equipment`
   abaixo): (a) outro equipamento do MESMO exercicio, seguro e compativel;
   (a') **M6** - senao, OUTRO EXERCICIO do mesmo grupo muscular, escolhido por
   ranking deterministico em `generation/exercise_substitution.py` (estrelas
   do par, `max_safe_rpe` >= o do original, tecnica fixa valida, nunca um
   exercicio que ja esta no slot), com a ficha tecnica do exercicio CERTO;
   (b) senao `requires_user_choice`. A troca por grupo do motor legado
   (`_recommend_for_group`, que emprestava a descricao de outro exercicio -
   ficha 110) continua fora daqui; o modulo legado sai no PR-19.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from ..domain import cardio_prescription
from .. import equipment_recommender
from ..db import transaction
from ..domain.equipment_pair import is_pair_coherent
from ..domain.prescription import (
    WARNING_EQUIPMENT_SUBSTITUTED,
    WARNING_EQUIPMENT_UNRESOLVED,
    WARNING_EXERCISE_AUTO_CHOSEN,
    WARNING_MODEL_TECHNIQUE_INVALID,
    parse_rpe,
)
from ..domain.training_safety import resolve_training_safety_bulk, validate_technique
from . import exercise_substitution as subst
from .deterministic_prescription import (
    MODE_B_ENGINE_VERSION,
    PairRequest,
    prescribe_pairs,
    readiness_by_group,
)
from .fallback_log import log_generation_fallback
from ..intelligence.technique_volume import activation_payload
from ..template_versions import current_version_id_of
from ..domain.prescription_defaults import (
    goal_adjustments_text,
    item_reps_target,
    item_rest_seconds,
    normalize_item_for_goal,
)
from ..domain.set_measures import is_rep_based, normalize_measure
from ..domain.training_goal import resolved_goal_value
from ..domain.laboral import laboral_row_fields
from ..laboral_catalog import load_laboral_defaults

logger = logging.getLogger("fitness.generation")

# Fonte unica do valor: `deterministic_prescription`, que e quem o grava no
# snapshot da ficha - os dois tem de ser o MESMO texto.
ENGINE_VERSION = MODE_B_ENGINE_VERSION

# `reason_code` do `generation.fallback` (secao 14) no Modo B. A substituicao
# automatica (caminho (a) da D13) NAO entra aqui: ela e `substitutions[]` na
# auditoria e entrega um par utilizavel. Fallback e so a degradacao do caminho
# (b) - nenhum equipamento seguro/compativel na academia, a ficha sai exigindo
# escolha do usuario no modo treino - e o par incoerente descartado.
FALLBACK_EQUIPMENT_UNRESOLVED = "EQUIPMENT_UNRESOLVED"
FALLBACK_INCOHERENT_PAIR = "INCOHERENT_PAIR_DISCARDED"

# M101b - exercicio livre no Modelo: item com grupo e SEM par (nem exercicio
# nem equipamento). O motor escolhe o EXERCICIO do grupo na geracao e o
# equipamento vem junto (`_resolve_free_exercise`). Motivo proprio na
# auditoria (`substitutions[]`), nunca `EXERCISE_UNAVAILABLE_IN_GYM`: nada foi
# substituido, o Modelo pediu a escolha.
REASON_EXERCISE_FREE_IN_TEMPLATE = "EXERCISE_FREE_IN_TEMPLATE"
KIND_FREE_EXERCISE = "FREE_EXERCISE"
# Candidato do ramo livre cujo teto (`max_safe_rpe`) fica abaixo do RPE-alvo
# do item: so e escolhido quando nenhum candidato alcanca o alvo.
REJECT_BELOW_ITEM_TARGET = "MAX_SAFE_RPE_BELOW_ITEM_TARGET"


# ---------------------------------------------------------------------------
# Leituras de catalogo (sem regra de negocio)
# ---------------------------------------------------------------------------


def sets_for_recovery(model_sets: Any, prescription: Any, recovery_status: Optional[str]) -> Any:
    """Séries do item na ficha do Modo B, já com a dose reduzida (M76 B2,
    decisão 1 do usuário): `ceil(S × 0,75)`, mínimo 1, num grupo REDUCED_DOSE.
    Antes o Modo B só avisava e copiava as séries do Modelo, enquanto o
    congelamento já gravava a dose reduzida no snapshot.

    Com prescrição, vale `decision.suggested_sets` - o motor (`prescribe`)
    já aplicou a regra única (`MuscleRecoveryService.reduced_sets`), e é o
    mesmo número que o congelamento grava. Sem prescrição (par não resolvido),
    a própria regra única. Com o Modelo fixando as séries, o motor nunca
    soma série no Modo B: a decisão só pode igualar ou reduzir o Modelo.
    NO_DIRECT_WORK mantém o Modelo (o Modo B não remove itens; o aviso cobre)."""
    if not model_sets:
        return model_sets
    if prescription is not None:
        suggested = getattr(prescription.decision, "suggested_sets", None)
        return min(int(model_sets), int(suggested)) if suggested else model_sets
    if recovery_status == "REDUCED_DOSE":
        from ..intelligence.muscle_recovery_service import MuscleRecoveryService

        return MuscleRecoveryService.reduced_sets(model_sets, recovery_status)
    return model_sets


def planned_slot_targets(
    group_ids: Iterable[int],
    group_names: dict[int, Optional[str]],
    activation_by_group_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """`template_slot_targets` do Modo B (M76 E4): por grupo dos itens do slot,
    as series EQUIVALENTES planejadas da ficha - `total_equivalent` da
    ativacao (`stimulus_weight` x series, com o equivalente da tecnica e a
    dose reduzida da recuperacao ja aplicada). E a mesma unidade da ativacao
    realizada da sessao (`/analytics/muscle-activation-by-activity`), contra a
    qual o alvo e comparado. Antes: soma das series BRUTAS dos itens do
    grupo, sem tecnica e sem secundarios. Sem teto no Modo B."""
    targets: list[dict[str, Any]] = []
    for group_id in sorted(group_ids):
        name = group_names.get(group_id)
        row = activation_by_group_name.get(name) if name else None
        targets.append(
            {
                "muscle_group_id": group_id,
                "muscle_group_name": name,
                "sets_count": round(float(row["total_equivalent"]), 2) if row else 0.0,
                "effective_max_sets": None,
            }
        )
    return targets


def item_measure_and_reps(item: Mapping[str, Any], prescription: Any, training_goal: Any = None) -> dict[str, Any]:
    """Medida da serie da linha da ficha (Lote T2) e o alvo/sugestao de reps.

    A medida e os alvos de tempo vem do MODELO, como as series. Em `reps`
    (ou ausente), o alvo de reps e o de sempre: o da progressao, senao o do
    item, senao o default do OBJETIVO do item (`prescription_defaults.
    item_reps_target`, M97 F2; hipertrofia = `DEFAULT_REPS_TARGET`). Fora de
    `reps` (tempo / maximo de reps,
    `set_measures.is_rep_based`) nao ha alvo nem sugestao de repeticoes."""
    measure = item.get("measure")
    rep_based = is_rep_based(measure)
    reps_target = None
    suggested_reps = None
    if rep_based:
        reps_target = (
            (prescription.reps_target if prescription else None)
            or item.get("reps_target")
            or item_reps_target(training_goal)
        )
        suggested_reps = prescription.decision.suggested_reps_max if prescription else None
    return {
        "measure": normalize_measure(measure).value,
        "duration_seconds_target": item.get("duration_seconds_target"),
        "time_window_seconds": item.get("time_window_seconds"),
        "reps_target": reps_target,
        "suggested_reps": suggested_reps,
    }


def _progression_technique_applications(prescription: Any) -> Optional[int]:
    """Aplicacoes da tecnica escolhida pela PROGRESSAO (F4), de
    `advanced_technique_parameters`. Tecnica do Modelo (o Modelo manda nos
    parametros) ou FST-7/GVT (sem aplicacoes) -> None."""
    params = getattr(getattr(prescription, "decision", None), "advanced_technique_parameters", None) or {}
    if params.get("source") != "progression" or params.get("sets_only"):
        return None
    return params.get("applications")


def _load_names(conn: Any, table: str, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, name FROM {table} WHERE id IN ({placeholders})", tuple(sorted(ids)))
        return {row["id"]: row["name"] for row in cur.fetchall() or []}


def _load_amazfit_names(conn: Any, exercise_ids: set[int]) -> dict[int, str]:
    """Nome Amazfit/Zepp (quando cadastrado). Campo informativo: ausencia
    nunca bloqueia a geracao."""
    if not exercise_ids:
        return {}
    placeholders = ",".join(["%s"] * len(exercise_ids))
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT exercise_id, external_name FROM exercise_external_mappings "
            f"WHERE provider = 'AMAZFIT' AND is_active = 1 AND exercise_id IN ({placeholders}) ORDER BY id ASC",
            tuple(sorted(exercise_ids)),
        )
        names: dict[int, str] = {}
        for row in cur.fetchall() or []:
            names.setdefault(row["exercise_id"], row["external_name"])
        return names


def _gym_equipment_for_exercise(conn: Any, gym_id: int, mapping_id: int) -> list[dict[str, Any]]:
    """Associacoes ATIVAS da academia para o MESMO exercicio (id canonico dos
    dois lados via `exercise_legacy_id_map`), ja no ranking unico do usuario
    (estrelas, recencia dele, nome, id). NUNCA devolve associacao de outro
    exercicio."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ge.id, ge.mapping_id, ge.equipment_id, eq.name AS equipment_name
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
        return list(cur.fetchall() or [])


# ---------------------------------------------------------------------------
# D13 - equipamento indisponivel na academia
# ---------------------------------------------------------------------------


@dataclass
class ResolvedEquipment:
    mapping_id: Optional[int]
    equipment_id: Optional[int]
    equipment_name: Optional[str]
    preferred_equipment_id: Optional[int]
    requires_user_choice: bool = False
    warnings: tuple[str, ...] = ()
    audit: dict[str, Any] = field(default_factory=dict)


def resolve_item_equipment(
    conn: Any,
    item: dict[str, Any],
    gym_id: Optional[int],
    *,
    user_id: Optional[int] = None,
    exclude_exercise_ids: Iterable[int] = (),
) -> ResolvedEquipment:
    """`_resolve_item_equipment` + a checagem de coerencia do par (D13).

    A decisao da D13 esta em `_resolve_item_equipment`; aqui garantimos que o
    que sai dela e um par que EXISTE: o `gym_equipment` escolhido serve o
    `exercise_id` do item (o do modelo ou, no M6, o substituto). Par
    incoerente e erro do motor, nunca dado do usuario - no runtime ele
    degrada para o caminho (b) com auditoria, e
    `equipment_pair.assert_pair_coherent` e como um teste o transforma em
    falha alta."""
    resolved = _resolve_item_equipment(
        conn, item, gym_id, user_id=user_id, exclude_exercise_ids=exclude_exercise_ids
    )
    if not resolved.preferred_equipment_id or not resolved.mapping_id:
        return resolved
    with conn.cursor() as cur:
        if is_pair_coherent(cur, resolved.mapping_id, resolved.preferred_equipment_id):
            return resolved
    logger.error(
        "D13: par incoerente produzido pelo Modo B (exercise_id=%s gym_equipment_id=%s); "
        "item cai para requires_user_choice",
        resolved.mapping_id,
        resolved.preferred_equipment_id,
    )
    return ResolvedEquipment(
        resolved.mapping_id,
        None,
        None,
        None,
        requires_user_choice=True,
        warnings=(WARNING_EQUIPMENT_UNRESOLVED,),
        audit={
            "from_equipment_id": item.get("equipment_id"),
            "to_equipment_id": None,
            "incoherent_pair_discarded": resolved.preferred_equipment_id,
        },
    )


def _resolve_item_equipment(
    conn: Any,
    item: dict[str, Any],
    gym_id: Optional[int],
    *,
    user_id: Optional[int] = None,
    exclude_exercise_ids: Iterable[int] = (),
) -> ResolvedEquipment:
    """Decisao D13 (+ M6), em quatro casos e nenhum a mais, depois do ramo do
    item de EXERCICIO LIVRE (M101b, so o grupo: `_resolve_free_exercise`):

    - o par do modelo esta ATIVO na academia -> usa o par do modelo;
    - (a) existe OUTRO equipamento para o **mesmo `exercise_id`**, ativo,
      compativel com a tecnica fixa do item e com `max_safe_rpe` **>= o do
      original** -> substituicao automatica + `EQUIPMENT_SUBSTITUTED`;
    - (a') **M6**: senao, existe OUTRO EXERCICIO com PRIMARY no mesmo grupo
      muscular do item, com equipamento ativo na academia, `max_safe_rpe` >=
      o do par original, tecnica fixa valida e que nao esta em
      `exclude_exercise_ids` (os demais exercicios do slot) -> substituicao
      automatica do EXERCICIO + `EQUIPMENT_SUBSTITUTED` com auditoria
      `kind=EXERCISE` (`generation/exercise_substitution.py`, ranking
      deterministico, sem LLM);
    - (b) senao -> `requires_user_choice=True` e `preferred_equipment_id=None`:
      o modo treino exige a escolha no `EquipmentSubstituteModal` antes de
      iniciar o exercicio. O usuario e o FALLBACK, nao o primeiro recurso.

    O criterio de `max_safe_rpe` e explicitamente **nao-degradante** nos dois
    caminhos automaticos: a substituicao nunca coloca o usuario num par
    MENOS seguro que o escolhido pelo PT. Um par mais restritivo continua
    disponivel - mas por escolha explicita dele, no caminho (b).

    No (a') o `mapping_id` devolvido e o do SUBSTITUTO: tudo o que a ficha
    mostra dele (nome, Amazfit, descricao, prescricao) e derivado desse id
    pelo chamador - nunca do item original (bug da ficha 110).
    """
    from .. import services

    mapping_id = item.get("mapping_id")
    equipment_id = item.get("equipment_id")
    technique = item.get("advanced_technique") or None

    if not mapping_id and not equipment_id:
        # M101b: exercicio livre, ANTES de qualquer substituicao.
        return _resolve_free_exercise(
            conn, item, gym_id, user_id=user_id, exclude_exercise_ids=exclude_exercise_ids
        )

    if not gym_id:
        # Sem academia nao ha catalogo a confrontar: o modelo vale como esta.
        return ResolvedEquipment(mapping_id, equipment_id, None, None)

    if mapping_id and equipment_id and services.is_gym_equipment_pair_active(gym_id, mapping_id, equipment_id):
        rows = [
            row
            for row in _gym_equipment_for_exercise(conn, gym_id, mapping_id)
            if int(row["equipment_id"]) == int(equipment_id)
        ]
        row = rows[0] if rows else None
        return ResolvedEquipment(
            mapping_id,
            equipment_id,
            (row or {}).get("equipment_name"),
            (row or {}).get("id"),
        )

    if not mapping_id:
        return ResolvedEquipment(
            mapping_id,
            equipment_id,
            None,
            None,
            requires_user_choice=True,
            warnings=(WARNING_EQUIPMENT_UNRESOLVED,),
        )

    rows = _gym_equipment_for_exercise(conn, gym_id, mapping_id)
    with conn.cursor() as cur:
        candidates = equipment_recommender.rank_gym_equipment_rows(cur, rows, gym_id=gym_id, user_id=user_id)
    candidates = [row for row in candidates if int(row["equipment_id"]) != int(equipment_id or 0)]
    rejected: list[dict[str, Any]] = []
    original_ceiling = 0
    if candidates:
        wanted = [(int(mapping_id), int(row["equipment_id"])) for row in candidates]
        if equipment_id:
            wanted.append((int(mapping_id), int(equipment_id)))
        safety_by_pair = resolve_training_safety_bulk(conn, wanted)
        original = safety_by_pair.get((int(mapping_id), int(equipment_id))) if equipment_id else None
        original_ceiling = original.max_safe_rpe if original else 0
        for row in candidates:
            candidate_safety = safety_by_pair.get((int(mapping_id), int(row["equipment_id"])))
            if candidate_safety is None:
                continue
            if candidate_safety.max_safe_rpe < original_ceiling:
                rejected.append({"equipment_id": int(row["equipment_id"]), "reason": subst.REJECT_MAX_SAFE_RPE_LOWER})
                continue
            if technique and not validate_technique(technique, candidate_safety).valid:
                rejected.append({"equipment_id": int(row["equipment_id"]), "reason": subst.REJECT_TECHNIQUE_INCOMPATIBLE})
                continue
            return ResolvedEquipment(
                mapping_id,
                int(row["equipment_id"]),
                row.get("equipment_name"),
                int(row["id"]),
                warnings=(WARNING_EQUIPMENT_SUBSTITUTED,),
                audit=subst.equipment_substitution_audit(
                    reason=subst.REASON_EQUIPMENT_UNAVAILABLE_IN_GYM,
                    exercise_id=int(mapping_id),
                    from_equipment_id=equipment_id,
                    to_equipment_id=int(row["equipment_id"]),
                    to_gym_equipment_id=int(row["id"]),
                    rejected=rejected,
                    original_max_safe_rpe=original_ceiling,
                    chosen_max_safe_rpe=candidate_safety.max_safe_rpe,
                ),
            )

    # (a') M6: nenhum equipamento deste exercicio serve -> outro exercicio do
    # MESMO grupo muscular, por ranking deterministico (nunca LLM).
    substitution, group_rejected = subst.search_exercise_substitute(
        conn,
        gym_id=int(gym_id),
        user_id=user_id,
        exercise_id=int(mapping_id),
        equipment_id=int(equipment_id) if equipment_id else None,
        muscle_group_id=item.get("muscle_group_id"),
        technique=technique,
        prescribed_rpe=parse_rpe(item.get("rpe_target")),
        exclude_exercise_ids=exclude_exercise_ids,
    )
    if substitution is not None:
        chosen = substitution.candidate
        audit = substitution.audit()
        audit["rejected"] = rejected + list(group_rejected)
        return ResolvedEquipment(
            int(chosen.exercise_id),
            int(chosen.equipment_id),
            chosen.equipment_name,
            int(chosen.gym_equipment_id),
            warnings=(WARNING_EQUIPMENT_SUBSTITUTED,),
            audit=audit,
        )

    # (b) nenhum par seguro/compativel para este exercicio nem para o grupo
    # na academia: o usuario escolhe no modo treino.
    return ResolvedEquipment(
        mapping_id,
        None,
        None,
        None,
        requires_user_choice=True,
        warnings=(WARNING_EQUIPMENT_UNRESOLVED,),
        audit={"from_equipment_id": equipment_id, "to_equipment_id": None, "rejected": rejected + list(group_rejected)},
    )


def _resolve_free_exercise(
    conn: Any,
    item: dict[str, Any],
    gym_id: Optional[int],
    *,
    user_id: Optional[int],
    exclude_exercise_ids: Iterable[int] = (),
) -> ResolvedEquipment:
    """M101b - item do Modelo com grupo muscular e SEM par: o motor escolhe o
    EXERCICIO e o equipamento vem por consequencia.

    - sem academia ou sem grupo: nao ha de onde escolher -> `requires_user_choice`;
    - candidatos: os pares exercicio+equipamento ATIVOS da academia cujo
      exercicio tem o grupo do item como PRIMARY, sem laboral (M100) -
      `exercise_substitution.load_group_candidates`, a mesma leitura do M6;
    - fora: exercicio ja usado em outro item do slot, fixo ou livre
      (`exclude_exercise_ids`, que o orquestrador acumula a cada item
      resolvido: dois livres do mesmo grupo nunca caem no mesmo exercicio) e
      tecnica fixa do item incompativel com o par;
    - ordem: ranking unico (`domain/equipment_ranking.py`: estrelas do par
      DESC, recencia ASC), com a recencia contando tambem as fichas geradas e
      nao executadas (`include_planned`), para variar a cada geracao sem
      treino no meio;
    - teto de seguranca explicito: o `max_safe_rpe` do candidato contra o
      RPE-alvo do item (`parse_rpe`). Vale o 1o do ranking que alcanca o alvo;
      se nenhum alcanca (ou o item nao tem alvo), vale o 1o compativel e o RPE
      passa pelo clamp auditado de `training_safety.compose` na prescricao.
      Nunca um piso 0 implicito;
    - nenhum candidato: `requires_user_choice`.
    """
    technique = item.get("advanced_technique") or None
    item_target_rpe = parse_rpe(item.get("rpe_target"))
    group_id = item.get("muscle_group_id")
    if not gym_id or not group_id:
        return _free_exercise_unresolved([], item_target_rpe)

    excluded = {int(value) for value in exclude_exercise_ids if value}
    rejected: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for candidate in subst.load_group_candidates(
        conn, gym_id=int(gym_id), muscle_group_id=int(group_id), user_id=user_id
    ):
        row = {
            "id": int(candidate.gym_equipment_id),
            "canonical_exercise_id": int(candidate.exercise_id),
            "equipment_id": int(candidate.equipment_id),
            "equipment_name": candidate.equipment_name,
        }
        if int(candidate.exercise_id) in excluded:
            rejected.append(_free_rejection(row, subst.REJECT_EXERCISE_ALREADY_IN_SLOT))
            continue
        rows.append(row)
    if not rows:
        return _free_exercise_unresolved(rejected, item_target_rpe)

    with conn.cursor() as cur:
        ranked = equipment_recommender.rank_gym_equipment_rows(
            cur, rows, gym_id=int(gym_id), user_id=user_id, include_planned=True
        )
    safety_by_pair = resolve_training_safety_bulk(
        conn, sorted({(int(row["canonical_exercise_id"]), int(row["equipment_id"])) for row in ranked})
    )
    compatible: list[tuple[dict[str, Any], Any]] = []
    for row in ranked:
        safety = safety_by_pair.get((int(row["canonical_exercise_id"]), int(row["equipment_id"])))
        if safety is None:
            continue
        if technique and not validate_technique(technique, safety).valid:
            rejected.append(_free_rejection(row, subst.REJECT_TECHNIQUE_INCOMPATIBLE))
            continue
        compatible.append((row, safety))
    if not compatible:
        return _free_exercise_unresolved(rejected, item_target_rpe)

    chosen = compatible[0]
    if item_target_rpe is not None:
        reaching = [pair for pair in compatible if int(pair[1].max_safe_rpe) >= int(item_target_rpe)]
        if reaching:
            chosen = reaching[0]
            for row, safety in compatible[: compatible.index(chosen)]:
                rejected.append(
                    {**_free_rejection(row, REJECT_BELOW_ITEM_TARGET), "max_safe_rpe": int(safety.max_safe_rpe)}
                )
    row, safety = chosen
    exercise_id = int(row["canonical_exercise_id"])
    return ResolvedEquipment(
        exercise_id,
        int(row["equipment_id"]),
        row.get("equipment_name"),
        int(row["id"]),
        warnings=(WARNING_EXERCISE_AUTO_CHOSEN,),
        audit=_free_exercise_audit(
            exercise_id,
            int(row["equipment_id"]),
            int(row["id"]),
            rejected=rejected,
            item_target_rpe=item_target_rpe,
            chosen_max_safe_rpe=int(safety.max_safe_rpe),
        ),
    )


def _free_rejection(row: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "gym_equipment_id": int(row["id"]),
        "exercise_id": int(row["canonical_exercise_id"]),
        "equipment_id": int(row["equipment_id"]),
        "reason": reason,
    }


def _free_exercise_unresolved(rejected: list[dict[str, Any]], item_target_rpe: Optional[int]) -> ResolvedEquipment:
    return ResolvedEquipment(
        None,
        None,
        None,
        None,
        requires_user_choice=True,
        warnings=(WARNING_EQUIPMENT_UNRESOLVED,),
        audit=_free_exercise_audit(None, None, None, rejected=rejected, item_target_rpe=item_target_rpe),
    )


def _free_exercise_audit(
    to_exercise_id: Optional[int],
    to_equipment_id: Optional[int],
    to_gym_equipment_id: Optional[int],
    *,
    rejected: list[dict[str, Any]],
    item_target_rpe: Optional[int],
    chosen_max_safe_rpe: Optional[int] = None,
) -> dict[str, Any]:
    """Bloco de `generation_audit.substitutions[]` do exercicio livre (M101b)."""
    return {
        "kind": KIND_FREE_EXERCISE,
        "reason": REASON_EXERCISE_FREE_IN_TEMPLATE,
        "from_exercise_id": None,
        "from_equipment_id": None,
        "to_exercise_id": to_exercise_id,
        "to_equipment_id": to_equipment_id,
        "to_gym_equipment_id": to_gym_equipment_id,
        "item_target_rpe": item_target_rpe,
        "chosen_max_safe_rpe": chosen_max_safe_rpe,
        "rejected": rejected,
    }


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------


def _warning_text(
    code: str,
    exercise_name: Optional[str],
    *,
    substituted_from: Optional[str] = None,
    equipment_name: Optional[str] = None,
    free_exercise: bool = False,
) -> str:
    label = exercise_name or "Exercício"
    if code == WARNING_EQUIPMENT_SUBSTITUTED and substituted_from:
        # M6: o EXERCICIO mudou. `label` ja e o substituto; `substituted_from`
        # e o que o modelo pedia.
        return (
            f"{substituted_from}: nenhum equipamento desta academia atende a este exercício; "
            f"substituído automaticamente por {label}"
            f"{f' ({equipment_name})' if equipment_name else ''}, exercício do mesmo grupo muscular, "
            "compatível com a técnica e ao menos tão seguro quanto o original."
        )
    if code == WARNING_EQUIPMENT_SUBSTITUTED:
        return (
            f"{label}: o equipamento do modelo não está disponível nesta academia; "
            "substituído automaticamente por outro equipamento do mesmo exercício, "
            "compatível com a técnica e ao menos tão seguro quanto o original."
        )
    if code == WARNING_EXERCISE_AUTO_CHOSEN:
        return (
            f"{label}"
            f"{f' ({equipment_name})' if equipment_name else ''}: exercício livre no modelo; escolhido "
            "pelo motor entre os exercícios do grupo nesta academia, pelas suas estrelas e pelo uso mais "
            "antigo (treinos e fichas geradas), compatível com a técnica do item."
        )
    if code == WARNING_EQUIPMENT_UNRESOLVED and free_exercise:
        return (
            f"{label}: exercício livre no modelo, mas nenhum exercício do grupo nesta academia atende ao "
            "item com segurança. Escolha o exercício no modo treino antes de iniciar."
        )
    if code == WARNING_EQUIPMENT_UNRESOLVED:
        return (
            f"{label}: nenhum equipamento disponível nesta academia atende a este exercício com segurança. "
            "Escolha o equipamento no modo treino antes de iniciar."
        )
    if code == WARNING_MODEL_TECHNIQUE_INVALID:
        return (
            f"{label}: a técnica avançada configurada no modelo não é válida para este equipamento "
            "(incompatível ou exige falha onde ela não é permitida) e foi removida da prescrição. "
            "O modelo continua salvo."
        )
    return f"{label}: {code}"


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
    """Rascunho do Modo B. Mesmo contrato de dict do motor v2 (o restante do
    pipeline de `POST /workout-plans/generate` nao muda), com `llm_used=false`
    e a auditoria da secao 14.

    Com a flag desligada, delega ao motor legado - `GENERATION_ENGINE=legacy`
    (default, inclusive em producao) e byte-a-byte o comportamento anterior.
    """
    from . import deterministic_enabled

    if not deterministic_enabled():
        from .mode_b_legacy import generate_plan_draft_from_equipment_template as legacy

        return legacy(
            template=template,
            slot=slot,
            gym_id=gym_id,
            gym_name=gym_name,
            plan_date=plan_date,
            user_id=user_id,
            min_rest_seconds=min_rest_seconds,
            max_rest_seconds=max_rest_seconds,
            plan_time=plan_time,
        )

    from ..intelligence.template_volume_domain import GenerationVolumeSummary
    from ..intelligence.volume_service import VolumeService
    from ..workout_plan_ai import (
        WorkoutPlanGenerationError,
        assign_warmup_flags,
        gather_ficha_context,
        normalize_load_target,
        project_recovery_for_date,
        resolve_gym_name,
    )
    from ..workout_plan_templates_engine import _empty_rest_day_draft

    if slot.get("slot_type") == "descanso":
        return _empty_rest_day_draft(template, slot, gym_id, gym_name, plan_date)

    items = sorted(
        (item for item in (slot.get("items") or []) if item.get("item_kind") == "equipment"),
        key=lambda item: item.get("item_order") or 0,
    )
    if not items:
        raise WorkoutPlanGenerationError(
            "Este slot não tem nenhum item de equipamento configurado (modo 'equipment_series')."
        )
    # Fix M97 (2026-10-26): item de objetivo fora do default com valor
    # incompativel com o perfil dele e ajustado AQUI, so na geracao (o Modelo
    # nao muda; dono `prescription_defaults.normalize_item_for_goal`).
    # Hipertrofia: os mesmos objetos, nenhum ajuste.
    normalized_items = [
        normalize_item_for_goal(item, resolved_goal_value(item.get("training_goal"), template.get("training_goal")))
        for item in items
    ]
    items = [item for item, _adjustments in normalized_items]
    adjustments_by_position = [adjustments for _item, adjustments in normalized_items]

    try:
        ficha_context = gather_ficha_context(gym_id, user_id)
        gym_name = resolve_gym_name(gym_id, gym_name, ficha_context.get("gyms") or [])
        projected_recovery = project_recovery_for_date(
            ficha_context.get("muscle_recovery") or [], plan_date, plan_time, user_id=user_id
        )
        readiness = readiness_by_group(projected_recovery)
        recovery_status_by_group = {
            item["muscle_group_id"]: item.get("projected_readiness_status") or "READY"
            for item in projected_recovery
            if item.get("muscle_group_id") is not None
        }

        warnings: list[str] = []
        if not gym_id:
            warnings.append(
                "Academia não informada: a validação de equipamento contra o catálogo não foi realizada; "
                "os itens do modelo foram usados exatamente como configurados."
            )

        with transaction() as conn:
            # M6: um substituto nunca repete um exercicio que ja esta no slot
            # (do modelo ou escolhido para outro item nesta mesma geracao).
            taken_exercise_ids = {int(item["mapping_id"]) for item in items if item.get("mapping_id")}
            resolved: list[ResolvedEquipment] = []
            for item in items:
                resolution = resolve_item_equipment(
                    conn, item, gym_id, user_id=user_id, exclude_exercise_ids=sorted(taken_exercise_ids)
                )
                if resolution.mapping_id:
                    taken_exercise_ids.add(int(resolution.mapping_id))
                resolved.append(resolution)

            group_ids = {item["muscle_group_id"] for item in items if item.get("muscle_group_id") is not None}
            group_names = _load_names(conn, "muscle_groups", group_ids)
            # Nomes do exercicio RESOLVIDO (no M6, o substituto) e do original
            # (para o aviso dizer o que o modelo pedia).
            exercise_ids = {r.mapping_id for r in resolved if r.mapping_id} | {
                int(item["mapping_id"]) for item in items if item.get("mapping_id")
            }
            exercise_names = _load_names(conn, "exercise_catalog", exercise_ids)
            amazfit_names = _load_amazfit_names(conn, exercise_ids)

            requests = [
                PairRequest(
                    exercise_id=int(r.mapping_id),
                    equipment_id=r.equipment_id,
                    muscle_group_id=item.get("muscle_group_id"),
                    model_sets_count=item.get("sets_count"),
                    model_technique=item.get("advanced_technique"),
                    model_rpe_target=item.get("rpe_target"),
                    model_technique_applications=item.get("advanced_technique_applications"),
                    model_technique_duration_seconds=item.get("advanced_technique_duration_seconds"),
                    sequence=index,
                    # M97 F1: a progressao do objetivo do item (> Modelo > default)
                    training_goal=resolved_goal_value(item.get("training_goal"), template.get("training_goal")),
                )
                for index, (item, r) in enumerate(zip(items, resolved), start=1)
                if r.mapping_id
            ]
            # session_order: os itens SAO o slot, na ordem; o contexto de fadiga de
            # cada item vem da posicao planejada (PR 2 da fadiga previa).
            prescriptions = prescribe_pairs(conn, user_id, requests, readiness=readiness, session_order=True)
            # M100: o par laboral fica fora da prescricao; o rascunho mostra o
            # padrao do catalogo (o mesmo que o congelamento grava).
            laboral_defaults = load_laboral_defaults(conn, (r.mapping_id for r in resolved if r.mapping_id))

        from ..intelligence.muscle_recovery_service import MuscleRecoveryService

        final_exercises: list[dict[str, Any]] = []
        safety_clamps: list[dict[str, Any]] = []
        substitutions: list[dict[str, Any]] = []
        reduced_dose_items: list[dict[str, Any]] = []
        goal_adjustments: list[dict[str, Any]] = []
        for index, (item, equipment) in enumerate(zip(items, resolved), start=1):
            mapping_id = equipment.mapping_id
            group_id = item.get("muscle_group_id")
            name = exercise_names.get(mapping_id) if mapping_id else None
            prescription = prescriptions.get((int(mapping_id), equipment.equipment_id)) if mapping_id else None
            item_goal = resolved_goal_value(item.get("training_goal"), template.get("training_goal"))

            exercise_substituted = bool(
                equipment.audit.get("kind") == subst.KIND_EXERCISE
                and equipment.audit.get("to_exercise_id") != equipment.audit.get("from_exercise_id")
            )
            free_exercise = equipment.audit.get("kind") == KIND_FREE_EXERCISE
            codes = list(equipment.warnings) + list(prescription.warnings if prescription else ())
            for code in codes:
                warnings.append(
                    _warning_text(
                        code,
                        name or (f"Exercício livre ({group_names.get(group_id) or 'grupo'})" if free_exercise else None),
                        substituted_from=(
                            exercise_names.get(equipment.audit.get("from_exercise_id"))
                            if exercise_substituted
                            else None
                        ),
                        equipment_name=(
                            equipment.equipment_name
                            if exercise_substituted or code == WARNING_EXERCISE_AUTO_CHOSEN
                            else None
                        ),
                        free_exercise=free_exercise,
                    )
                )
            item_adjustments = adjustments_by_position[index - 1]
            if item_adjustments:
                warnings.append(goal_adjustments_text(name, item_adjustments))
                goal_adjustments.append(
                    {
                        "sequence": index,
                        "exercise_id": mapping_id,
                        "training_goal": item_goal,
                        "adjustments": [adjustment.to_dict() for adjustment in item_adjustments],
                    }
                )
            if equipment.audit:
                substitutions.append({"sequence": index, "exercise_id": mapping_id, **equipment.audit})
            if equipment.requires_user_choice:
                # Candidato indisponivel forcando escolha alternativa: o item
                # sai da geracao sem equipamento resolvido.
                log_generation_fallback(
                    reason_code=(
                        FALLBACK_INCOHERENT_PAIR
                        if equipment.audit.get("incoherent_pair_discarded")
                        else FALLBACK_EQUIPMENT_UNRESOLVED
                    ),
                    user_id=user_id,
                    template_id=template.get("id"),
                    engine_version=ENGINE_VERSION,
                    volume_mode="equipment_series",
                    muscle_group_id=group_id,
                    exercise_id=mapping_id,
                )
            if prescription is not None:
                for clamp in prescription.decision.evidence.get("safety_clamps") or ():
                    safety_clamps.append({"sequence": index, "exercise_id": mapping_id, **dict(clamp)})

            model_sets = item.get("sets_count")
            sets_count = sets_for_recovery(model_sets, prescription, recovery_status_by_group.get(group_id, "READY"))
            if model_sets and sets_count != model_sets:
                reduced_dose_items.append(
                    {
                        "sequence": index,
                        "exercise_id": mapping_id,
                        "muscle_group_id": group_id,
                        "model_sets": int(model_sets),
                        "sets_count": int(sets_count),
                    }
                )
                warnings.append(
                    f"{name or 'Exercício'}: dose reduzida pela recuperação de {group_names.get(group_id) or 'grupo'} "
                    f"— {model_sets} → {sets_count} série(s) "
                    f"({MuscleRecoveryService.reduction_policy()['sets_rule']})."
                )

            final_exercises.append(
                {
                    "exercise_name": name,
                    "amazfit_name": amazfit_names.get(mapping_id) if mapping_id else None,
                    "muscle_primary": group_names.get(group_id),
                    "muscle_group_id": group_id,
                    "muscle_secondary": None,
                    "equipment_name": equipment.equipment_name,
                    "equipment_id": equipment.equipment_id,
                    "preferred_equipment_id": equipment.preferred_equipment_id,
                    "mapping_id": mapping_id,
                    # --- do MODELO (secao 6.2); séries com a dose reduzida
                    # da recuperação aplicada (M76 B2) ---
                    "sets_count": sets_count,
                    # Descanso definido no Modelo (Fase 7, Frente B) SOBREPÕE o
                    # intervalo da geração, sem ser limitado pelo mín/máx; sem
                    # valor no item, vale o ponto médio do intervalo (M97 F2:
                    # o do OBJETIVO do item, dono `prescription_defaults`).
                    "rest_seconds": item.get("rest_seconds")
                    or item_rest_seconds(item_goal, min_rest_seconds, max_rest_seconds),
                    # Fase 5, PR 6: tecnica que veio da PROGRESSAO (F4) nao
                    # tem aplicacoes no Modelo; sem elas o Modo Treino nao abre
                    # a etapa da tecnica. Vale o default da prescricao.
                    "advanced_technique_applications": item.get("advanced_technique_applications")
                    or _progression_technique_applications(prescription),
                    "advanced_technique_duration_seconds": item.get("advanced_technique_duration_seconds"),
                    "warmup_sets_count": item.get("warmup_sets_count"),
                    "warmup_reps": item.get("warmup_reps"),
                    "warmup_rest_seconds": item.get("warmup_rest_seconds"),
                    "feeler_set_sets_count": item.get("feeler_set_sets_count"),
                    "feeler_set_reps": item.get("feeler_set_reps"),
                    "feeler_set_rest_seconds": item.get("feeler_set_rest_seconds"),
                    "peak_contraction_seconds": item.get("peak_contraction_seconds"),
                    # Objetivo de treino (M97 F0): override do item > Modelo >
                    # default, pelo dono. Gravado na ficha; nenhum motor le ainda.
                    "training_goal": item_goal,
                    # --- da PROGRESSAO v2 (reps e carga) e da SEGURANCA ---
                    # Medida da serie + alvo/sugestao de reps (Lote T2).
                    **item_measure_and_reps(item, prescription, item_goal),
                    "load_target": normalize_load_target(prescription.suggested_load_kg) if prescription else None,
                    "suggested_load_kg": prescription.suggested_load_kg if prescription else None,
                    "rpe_target": (prescription.rpe_target if prescription else None) or item.get("rpe_target"),
                    # Tecnica: hierarquia da secao 5.9 (modelo vence a
                    # progressao; seguranca remove a invalida).
                    "advanced_technique": prescription.technique if prescription else item.get("advanced_technique"),
                    "progression_note": prescription.decision.reason if prescription else None,
                    "progression": None,
                    "requires_user_choice": equipment.requires_user_choice,
                    "warmup": False,
                    "substitution_options": [],
                    "notes": "",
                    "recovery_status": recovery_status_by_group.get(group_id, "READY"),
                }
            )
            if mapping_id and int(mapping_id) in laboral_defaults:
                final_exercises[-1].update(
                    laboral_row_fields(laboral_defaults[int(mapping_id)], final_exercises[-1], normalize_load_target)
                )

        assign_warmup_flags(final_exercises)
        for index, exercise in enumerate(final_exercises, start=1):
            exercise["sequence"] = index

        # M76 E3: payload unico de ativacao (o mesmo do item do Modelo) - a
        # tecnica de cada exercicio soma o equivalente planejado no backend.
        activation_rows = VolumeService().get_muscle_activation_for_exercises(
            [activation_payload(ex) for ex in final_exercises]
        )
        activation_by_group_name = {row["muscle_group"]: row for row in activation_rows}

        volume_summary = GenerationVolumeSummary()
        for group_id in sorted(group_ids):
            group_name = group_names.get(group_id)
            if not group_name:
                continue
            activation_row = activation_by_group_name.get(group_name)
            total_equivalent = float(activation_row["total_equivalent"]) if activation_row else 0.0
            recovery_status = recovery_status_by_group.get(group_id, "READY")
            volume_summary.add_entry(
                GenerationVolumeSummary.Entry(
                    muscle_group_id=group_id,
                    muscle_group_name=group_name,
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
                volume_summary.warnings.append(
                    f"{group_name}: grupo sem recuperação suficiente (NO_DIRECT_WORK) — exercício mantido "
                    "conforme o modelo (modo 'equipment_series' não remove itens automaticamente); revise "
                    "manualmente antes de salvar."
                )
        volume_summary.warnings.extend(warnings)

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
            "template_slot_targets": planned_slot_targets(group_ids, group_names, activation_by_group_name),
            "is_rest_day": False,
            # M98 C0: o cardio do slot chega ao rascunho (dono `cardio_prescription`).
            **cardio_prescription.draft_cardio(slot.get("items")),
            "exercises": final_exercises,
            "excluded_due_to_recovery": [],
            "excluded_due_to_duplicate": [],
            "adjusted_due_to_catalog_mismatch": [],
            "excluded_due_to_no_equipment": [],
            "reduced_dose_groups": sorted(
                {
                    group_names.get(entry["muscle_group_id"])
                    for entry in reduced_dose_items
                    if group_names.get(entry["muscle_group_id"])
                }
            ),
            "insufficient_target_volume_groups": [],
            "generation_summary": (
                f"Ficha gerada pelo motor determinístico do modo 'equipment_series' a partir do modelo "
                f"'{template.get('name')}', slot '{slot.get('name')}'. Grupo, exercício, equipamento, séries, "
                f"descanso, técnica, aquecimento, ajuste e pico vêm do modelo (séries com a dose reduzida da "
                f"recuperação aplicada aos grupos em REDUCED_DOSE); repetições e carga vêm da "
                f"progressão v2; o RPE do modelo é intenção e passa pelo teto de segurança. Sem IA."
            ),
            "raw_payload": {
                "template_id": template.get("id"),
                "slot_id": slot.get("id"),
                "slot_sequence": slot.get("sequence"),
                "engine_version": ENGINE_VERSION,
                "used_ai": False,
                "repair_log": [],
                "volume_planning": volume_summary.as_dict(),
                # Auditoria da secao 14.
                "generation_audit": {
                    "template_id": template.get("id"),
                    "slot_sequence": slot.get("sequence"),
                    "volume_mode": "equipment_series",
                    "engine_version": ENGINE_VERSION,
                    "safety_clamps": safety_clamps,
                    "substitutions": substitutions,
                    # M76 B2: itens cujas séries o Modo B reduziu pela
                    # recuperação (Modelo -> ficha) e a regra aplicada.
                    "reduced_dose": {
                        "policy": MuscleRecoveryService.reduction_policy(),
                        "items": reduced_dose_items,
                    }
                    if reduced_dose_items
                    else None,
                    "fallbacks": [],
                    "llm_used": False,
                    # Fix M97: ajustes ao objetivo (so quando houve; ficha de
                    # hipertrofia sai com a auditoria de sempre).
                    **({"goal_adjustments": goal_adjustments} if goal_adjustments else {}),
                },
            },
        }
    except WorkoutPlanGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - erro irrecuperavel vira 502 no chamador
        # SEG-12: a excecao interna (ex.: SQL do driver) vai para o log; a
        # mensagem vira o `detail` do 502 e o erro do job assincrono.
        logger.exception("Falha inesperada na geracao 'equipment_series'")
        raise WorkoutPlanGenerationError(
            "Falha ao gerar ficha via modo 'equipment_series' (motor determinístico)."
        ) from exc
