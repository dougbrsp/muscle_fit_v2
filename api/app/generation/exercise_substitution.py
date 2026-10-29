"""Substituicao DETERMINISTICA de exercicio por outro do MESMO grupo muscular
(M6) - o servico de substituicao compartilhado com a troca manual de
equipamento na ficha (M8).

Pergunta que este modulo responde: **a academia nao tem nenhum equipamento
que sirva este exercicio; qual outro exercicio do mesmo grupo muscular, com
qual equipamento desta academia, o usuario deve fazer no lugar?** - sem LLM,
sem aleatoriedade e sem emprestar a ficha tecnica de um exercicio para outro.

Por que uma reescrita e nao o `equipment_recommender.recommend_for_group`
(via `_recommend_for_group` do `mode_b_legacy.py`): aquela funcao escolhia
pelo grupo muscular PRIMARIO e o chamador mantinha o nome/descricao do item
original - foi o bug da ficha 110 em producao (2026-09-17: "Desenvolvimento
de ombros na maquina" com a ficha tecnica de "Desenvolvimento com halteres").
Aqui o candidato E o exercicio: `exercise_id` canonico + nome + Amazfit +
descricao vem TODOS de `exercise_catalog` do candidato, e quem consome o
resultado (`mode_b.py`) deriva o restante (prescricao, ficha tecnica) desse
`exercise_id` - nada do item original sobrevive alem de grupo, series,
tecnica e parametros do MODELO, que sao do slot e nao do exercicio.

Regras (D13a, decisoes do usuario em 2026-09-21):

- so entra candidato com contribuicao PRIMARY para o MESMO grupo muscular do
  item (o do modelo; sem ele, o PRIMARY do exercicio original);
- `max_safe_rpe` do par candidato >= o do par original - a substituicao nunca
  degrada a seguranca;
- tecnica fixa do item, quando houver, tem de ser valida no par candidato;
- exercicio que ja esta no slot nunca e candidato (a ficha nao repete);
- ranking DETERMINISTICO (`ranking_key`): estrelas do par (PR-08, ausente = 3)
  DESC, depois quantos clamps a composicao de seguranca aplicaria ao RPE do
  modelo ASC (`training_safety.compose`), `max_safe_rpe` DESC, peso de
  estimulo no grupo DESC, nome do exercicio, nome do equipamento e id da
  associacao. Sem recencia: variedade e desejavel numa recomendacao, nao numa
  substituicao automatica que precisa dar a MESMA resposta para a mesma
  entrada (teste de determinismo obrigatorio).

O resultado e auditavel: `ExerciseSubstitution.audit()` vai para
`generation_audit.substitutions[]` do rascunho e, no congelamento, para
`prescription_json.substitution` junto de `WARNING_EQUIPMENT_SUBSTITUTED`.

Tudo acima de "Acesso a dados" e funcao PURA (sem banco), coberta por
tests/unit/test_m6_substituicao_exercicio.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..domain import training_safety as ts
from ..domain.equipment_ranking import effective_stars
from ..domain.laboral import sql_not_laboral

# `substitution.kind` no snapshot / na auditoria.
KIND_EXERCISE = "EXERCISE"  # trocou o exercicio (M6)
KIND_EQUIPMENT = "EQUIPMENT"  # mesmo exercicio, outro equipamento (D13 caminho a / M8)

# `substitution.reason`
REASON_EXERCISE_UNAVAILABLE_IN_GYM = "EXERCISE_UNAVAILABLE_IN_GYM"  # M6: nenhum equipamento do exercicio na academia
REASON_EQUIPMENT_UNAVAILABLE_IN_GYM = "EQUIPMENT_UNAVAILABLE_IN_GYM"  # D13 (a): equipamento do modelo ausente
REASON_USER_CHOICE = "USER_CHOICE"  # M8: troca deliberada do usuario na ficha

# `rejected[].reason`
REJECT_MAX_SAFE_RPE_LOWER = "MAX_SAFE_RPE_LOWER"
REJECT_TECHNIQUE_INCOMPATIBLE = "TECHNIQUE_INCOMPATIBLE"
REJECT_EXERCISE_ALREADY_IN_SLOT = "EXERCISE_ALREADY_IN_SLOT"


@dataclass(frozen=True)
class SubstituteCandidate:
    """Uma associacao `gym_equipment` ATIVA da academia cujo exercicio
    (canonico) tem contribuicao PRIMARY para o grupo do item."""

    gym_equipment_id: int
    exercise_id: int  # canonico (`exercise_catalog.id`)
    exercise_name: str
    equipment_id: int
    equipment_name: str
    muscle_group_id: int
    stimulus_weight: float = 1.0
    amazfit_name: Optional[str] = None
    exercise_description: Optional[str] = None
    user_preference_stars: Optional[int] = None

    @property
    def pair(self) -> tuple[int, int]:
        return (int(self.exercise_id), int(self.equipment_id))


@dataclass(frozen=True)
class RankedCandidate:
    candidate: SubstituteCandidate
    safety: ts.TrainingSafety
    composition: ts.IntensityComposition

    def to_dict(self) -> dict[str, Any]:
        return {
            "gym_equipment_id": self.candidate.gym_equipment_id,
            "exercise_id": self.candidate.exercise_id,
            "equipment_id": self.candidate.equipment_id,
            "stars": effective_stars(self.candidate.user_preference_stars),
            "max_safe_rpe": self.safety.max_safe_rpe,
            "effective_rpe_ceiling": self.composition.ceiling,
        }


@dataclass(frozen=True)
class ExerciseSubstitution:
    """Resultado do M6: o candidato escolhido + o que foi rejeitado e por que."""

    chosen: RankedCandidate
    from_exercise_id: int
    from_equipment_id: Optional[int]
    original_max_safe_rpe: int
    rejected: tuple[dict[str, Any], ...] = ()
    ranking: tuple[dict[str, Any], ...] = ()
    reason: str = REASON_EXERCISE_UNAVAILABLE_IN_GYM

    @property
    def candidate(self) -> SubstituteCandidate:
        return self.chosen.candidate

    def audit(self) -> dict[str, Any]:
        """Bloco auditavel (rascunho `generation_audit.substitutions[]` e
        `prescription_json.substitution`)."""
        return {
            "kind": KIND_EXERCISE,
            "reason": self.reason,
            "from_exercise_id": int(self.from_exercise_id),
            "from_equipment_id": int(self.from_equipment_id) if self.from_equipment_id else None,
            "to_exercise_id": int(self.candidate.exercise_id),
            "to_equipment_id": int(self.candidate.equipment_id),
            "to_gym_equipment_id": int(self.candidate.gym_equipment_id),
            "original_max_safe_rpe": int(self.original_max_safe_rpe),
            "chosen_max_safe_rpe": int(self.chosen.safety.max_safe_rpe),
            "rejected": [dict(item) for item in self.rejected],
            "ranking": [dict(item) for item in self.ranking],
        }


# ---------------------------------------------------------------------------
# Funcoes puras
# ---------------------------------------------------------------------------


def ranking_key(ranked: RankedCandidate) -> tuple:
    """Ordem total e deterministica entre candidatos elegiveis (docstring do
    modulo). Nunca depende de recencia nem de ordem de dicionario."""
    candidate = ranked.candidate
    return (
        -effective_stars(candidate.user_preference_stars),
        len(ranked.composition.clamps),
        -int(ranked.safety.max_safe_rpe),
        -float(candidate.stimulus_weight or 0.0),
        str(candidate.exercise_name or "").casefold(),
        str(candidate.equipment_name or "").casefold(),
        int(candidate.gym_equipment_id),
    )


def rank_candidates(
    candidates: Iterable[SubstituteCandidate],
    safety_by_pair: Mapping[tuple[int, int], ts.TrainingSafety],
    *,
    original_max_safe_rpe: int,
    technique: Optional[str] = None,
    readiness: Optional[ts.Readiness] = None,
    prescribed_rpe: Optional[int] = None,
    exclude_exercise_ids: Iterable[int] = (),
) -> tuple[list[RankedCandidate], list[dict[str, Any]]]:
    """(elegiveis, ja em ordem de preferencia; rejeitados com motivo).

    `original_max_safe_rpe` e o teto do par ORIGINAL (D13a: candidato abaixo
    dele e rejeitado). `readiness`/`prescribed_rpe` alimentam
    `training_safety.compose` so para o desempate - nunca excluem ninguem
    (a prescricao final e quem clampa, auditando)."""
    excluded = {int(value) for value in exclude_exercise_ids if value}
    readiness = readiness or ts.readiness_for_status(ts.READY)
    eligible: list[RankedCandidate] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        entry = {
            "gym_equipment_id": int(candidate.gym_equipment_id),
            "exercise_id": int(candidate.exercise_id),
            "equipment_id": int(candidate.equipment_id),
        }
        if int(candidate.exercise_id) in excluded:
            rejected.append({**entry, "reason": REJECT_EXERCISE_ALREADY_IN_SLOT})
            continue
        safety = safety_by_pair.get(candidate.pair)
        if safety is None:
            # Par sem seguranca lida: nao ha como provar D13a -> fora.
            rejected.append({**entry, "reason": REJECT_MAX_SAFE_RPE_LOWER, "max_safe_rpe": None})
            continue
        if int(safety.max_safe_rpe) < int(original_max_safe_rpe):
            rejected.append({**entry, "reason": REJECT_MAX_SAFE_RPE_LOWER, "max_safe_rpe": safety.max_safe_rpe})
            continue
        if technique and not ts.validate_technique(technique, safety).valid:
            rejected.append({**entry, "reason": REJECT_TECHNIQUE_INCOMPATIBLE, "technique": technique})
            continue
        eligible.append(
            RankedCandidate(candidate=candidate, safety=safety, composition=ts.compose(safety, readiness, prescribed_rpe))
        )
    eligible.sort(key=ranking_key)
    return eligible, rejected


def choose_substitute(
    candidates: Iterable[SubstituteCandidate],
    safety_by_pair: Mapping[tuple[int, int], ts.TrainingSafety],
    *,
    from_exercise_id: int,
    from_equipment_id: Optional[int],
    original_max_safe_rpe: int,
    technique: Optional[str] = None,
    readiness: Optional[ts.Readiness] = None,
    prescribed_rpe: Optional[int] = None,
    exclude_exercise_ids: Iterable[int] = (),
) -> Optional[ExerciseSubstitution]:
    """`rank_candidates` + o primeiro da fila, ja com a auditoria montada.
    `None` = nenhum candidato elegivel (o chamador cai no usuario, D13 b)."""
    eligible, rejected = rank_candidates(
        candidates,
        safety_by_pair,
        original_max_safe_rpe=original_max_safe_rpe,
        technique=technique,
        readiness=readiness,
        prescribed_rpe=prescribed_rpe,
        exclude_exercise_ids=exclude_exercise_ids,
    )
    if not eligible:
        return None
    return ExerciseSubstitution(
        chosen=eligible[0],
        from_exercise_id=int(from_exercise_id),
        from_equipment_id=from_equipment_id,
        original_max_safe_rpe=int(original_max_safe_rpe),
        rejected=tuple(rejected),
        ranking=tuple(item.to_dict() for item in eligible),
    )


def equipment_substitution_audit(
    *,
    reason: str,
    exercise_id: int,
    from_equipment_id: Optional[int],
    to_equipment_id: Optional[int],
    to_gym_equipment_id: Optional[int] = None,
    rejected: Sequence[Mapping[str, Any]] = (),
    original_max_safe_rpe: Optional[int] = None,
    chosen_max_safe_rpe: Optional[int] = None,
) -> dict[str, Any]:
    """Bloco auditavel de uma troca de EQUIPAMENTO (mesmo exercicio) - D13
    caminho (a) na geracao e a troca manual do M8. Mesmo formato do
    `ExerciseSubstitution.audit()` para o snapshot ter UM leitor."""
    return {
        "kind": KIND_EQUIPMENT,
        "reason": reason,
        "from_exercise_id": int(exercise_id),
        "from_equipment_id": int(from_equipment_id) if from_equipment_id else None,
        "to_exercise_id": int(exercise_id),
        "to_equipment_id": int(to_equipment_id) if to_equipment_id else None,
        "to_gym_equipment_id": int(to_gym_equipment_id) if to_gym_equipment_id else None,
        "original_max_safe_rpe": original_max_safe_rpe,
        "chosen_max_safe_rpe": chosen_max_safe_rpe,
        "rejected": [dict(item) for item in rejected],
        "ranking": [],
    }


# ---------------------------------------------------------------------------
# Acesso a dados (somente SELECT)
# ---------------------------------------------------------------------------


def load_group_candidates(
    conn: Any, *, gym_id: int, muscle_group_id: int, user_id: Optional[int]
) -> list[SubstituteCandidate]:
    """Associacoes ATIVAS da academia cujo exercicio canonico tem
    contribuicao PRIMARY ativa para `muscle_group_id`, ja com nome, Amazfit,
    descricao e estrelas DO USUARIO para o par. Numero FIXO de SELECTs (2).

    O `mapping_id` de `gym_equipment` passa por `exercise_legacy_id_map`
    (mesma regra de `mode_b._gym_equipment_for_exercise`): id antigo absorvido
    pela consolidacao do catalogo vira o id ATIVO antes de qualquer JOIN, e e
    esse id canonico que sai como `exercise_id` do candidato.

    M100: exercicio LABORAL nunca substitui um item de grupo."""
    from ..equipment_recommender import load_user_stars

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ge.id AS gym_equipment_id,
                   COALESCE(elm.canonical_exercise_id, ge.mapping_id) AS exercise_id,
                   ec.name AS exercise_name,
                   ec.description AS exercise_description,
                   ge.equipment_id,
                   eq.name AS equipment_name,
                   emc.stimulus_weight,
                   (
                       SELECT zam.external_name
                       FROM exercise_external_mappings zam
                       WHERE zam.exercise_id = ec.id AND zam.provider = 'AMAZFIT' AND zam.is_active = 1
                       ORDER BY zam.id ASC LIMIT 1
                   ) AS amazfit_name
            FROM gym_equipment ge
            JOIN equipment eq ON eq.id = ge.equipment_id
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = ge.mapping_id
            JOIN exercise_catalog ec
              ON ec.id = COALESCE(elm.canonical_exercise_id, ge.mapping_id) AND ec.is_active = 1
             AND {sql_not_laboral('ec')}
            JOIN exercise_muscle_contributions emc
              ON emc.exercise_id = ec.id AND emc.muscle_group_id = %s
             AND UPPER(emc.role) = 'PRIMARY' AND emc.is_active = 1
            WHERE ge.gym_id = %s AND ge.is_active = 1
            ORDER BY ge.id ASC
            """,
            (int(muscle_group_id), int(gym_id)),
        )
        rows = list(cur.fetchall() or [])
        stars = load_user_stars(cur, int(user_id)) if user_id is not None else {}

    candidates: list[SubstituteCandidate] = []
    for row in rows:
        exercise_id = int(row["exercise_id"])
        equipment_id = int(row["equipment_id"])
        candidates.append(
            SubstituteCandidate(
                gym_equipment_id=int(row["gym_equipment_id"]),
                exercise_id=exercise_id,
                exercise_name=row.get("exercise_name") or "",
                equipment_id=equipment_id,
                equipment_name=row.get("equipment_name") or "",
                muscle_group_id=int(muscle_group_id),
                stimulus_weight=float(row.get("stimulus_weight") or 0.0),
                amazfit_name=row.get("amazfit_name"),
                exercise_description=row.get("exercise_description"),
                user_preference_stars=stars.get((exercise_id, equipment_id)),
            )
        )
    return candidates


def search_exercise_substitute(
    conn: Any,
    *,
    gym_id: int,
    user_id: Optional[int],
    exercise_id: int,
    equipment_id: Optional[int],
    muscle_group_id: Optional[int],
    technique: Optional[str] = None,
    readiness: Optional[ts.Readiness] = None,
    prescribed_rpe: Optional[int] = None,
    exclude_exercise_ids: Iterable[int] = (),
) -> tuple[Optional[ExerciseSubstitution], list[dict[str, Any]]]:
    """M6 ponta a ponta para UM item: candidatos do grupo -> seguranca em lote
    -> ranking -> escolha. Devolve `(escolha ou None, rejeitados)`: quando
    nao ha candidato elegivel, os motivos continuam auditaveis no caminho (b).

    O grupo e o do MODELO (`muscle_group_id`); sem ele, o PRIMARY do exercicio
    original. Sem grupo conhecido nao ha "mesmo grupo" a preservar.
    O teto do par original e lido do catalogo GLOBAL (o par pode nao existir
    nesta academia, e e exatamente por isso que estamos aqui); sem
    equipamento original nao ha teto a preservar (0)."""
    from .. import progression_state as ps

    group_id = int(muscle_group_id) if muscle_group_id else None
    if group_id is None:
        primary, _weights = ps.load_primary_groups(conn, [int(exercise_id)])
        group_id = primary.get(int(exercise_id))
    if group_id is None:
        return None, []

    candidates = load_group_candidates(conn, gym_id=int(gym_id), muscle_group_id=group_id, user_id=user_id)
    # O proprio exercicio nunca e candidato aqui: se a academia tivesse
    # equipamento para ele, a D13 caminho (a) ja teria resolvido.
    candidates = [candidate for candidate in candidates if int(candidate.exercise_id) != int(exercise_id)]
    if not candidates:
        return None, []

    pairs = [candidate.pair for candidate in candidates]
    original_pair = (int(exercise_id), int(equipment_id)) if equipment_id else None
    if original_pair:
        pairs.append(original_pair)
    safety_by_pair = ts.resolve_training_safety_bulk(conn, pairs)
    original = safety_by_pair.get(original_pair) if original_pair else None
    original_max_safe_rpe = int(original.max_safe_rpe) if original else 0

    chosen = choose_substitute(
        candidates,
        safety_by_pair,
        from_exercise_id=int(exercise_id),
        from_equipment_id=int(equipment_id) if equipment_id else None,
        original_max_safe_rpe=original_max_safe_rpe,
        technique=technique,
        readiness=readiness,
        prescribed_rpe=prescribed_rpe,
        exclude_exercise_ids=exclude_exercise_ids,
    )
    if chosen is not None:
        return chosen, list(chosen.rejected)
    _eligible, rejected = rank_candidates(
        candidates,
        safety_by_pair,
        original_max_safe_rpe=original_max_safe_rpe,
        technique=technique,
        readiness=readiness,
        prescribed_rpe=prescribed_rpe,
        exclude_exercise_ids=exclude_exercise_ids,
    )
    return None, rejected


def find_exercise_substitute(conn: Any, **kwargs: Any) -> Optional[ExerciseSubstitution]:
    """`search_exercise_substitute` para quem so quer a escolha."""
    return search_exercise_substitute(conn, **kwargs)[0]
