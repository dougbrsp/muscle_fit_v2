from __future__ import annotations

from types import MappingProxyType
from typing import Any, Final, Mapping

# Modulo neutro (Fase de series equivalentes por atividade, 2026-08-22):
# concentra a resolucao de contribuicoes musculares ponderadas por
# mapping_id (`_resolve_muscle_contributions_for_mappings`) e o acumulo
# `sets_count * stimulus_weight` de um exercicio (`_accumulate_exercise_contribution`).
#
# Estas duas funcoes viviam originalmente em `abc_volume_reinforcement.py`
# (reforco de volume minimo por grupo alvo do fluxo ABC). Foram movidas para
# ca porque uma segunda feature (series equivalentes por atividade, ver
# `VolumeService.get_equivalent_sets_by_activity`) precisa reaproveitar
# exatamente a mesma logica, e `intelligence/volume_service.py` nao pode
# importar de `abc_volume_reinforcement.py` sem criar um ciclo (este ultimo
# ja importa `VolumeService` de `intelligence/volume_service.py`). Colocando
# as duas funcoes num modulo neutro dentro do proprio pacote `intelligence/`,
# tanto `abc_volume_reinforcement.py` quanto `volume_service.py` podem
# importar dali sem ciclo.
#
# Import de `VolumeService` e tardio (dentro da funcao) pelo mesmo motivo -
# evita qualquer risco de ciclo mesmo que `volume_service.py` importe este
# modulo no topo do arquivo no futuro.


# ---------------------------------------------------------------------------
# Pesos por papel (M76 Lote 3c) - DONO UNICO dos defaults de ESCRITA
# ---------------------------------------------------------------------------
#
# Peso que uma contribuicao recebe quando e GRAVADA sem peso explicito: o
# formulario legado de `/exercise-mapping-catalog` (so informa grupo primario/
# secundario) e o valor inicial que o admin ve em `MuscleContributionsSection`
# (frontend, via `motor-constants.generated.json`) antes de calibrar. Vale
# para estimulo E fadiga. Sao os valores do backfill original
# (`001_schema.sql`: PRIMARY 1,00, SECONDARY 0,50) mais o de STABILIZER que so
# o admin usava.
#
# LEITURA nunca usa estes numeros: quem le pondera pelo `stimulus_weight`
# cadastrado em `exercise_muscle_contributions` (fonte da verdade do peso de
# cada exercicio). O default so existe para o momento da escrita e para o
# fallback estrutural de `VolumeService._resolve_contributions`.
DEFAULT_WRITE_WEIGHTS: Final[Mapping[str, float]] = MappingProxyType(
    {
        "PRIMARY": 1.0,
        "SECONDARY": 0.5,
        "STABILIZER": 0.25,
    }
)


def _resolve_muscle_contributions_for_mappings(mapping_ids: set[int]) -> dict[int, list[dict[str, Any]]]:
    """Reaproveita VolumeService._fetch_contributions_by_mapping +
    VolumeService._resolve_contributions para obter, por mapping_id, a lista
    de contribuicoes (role + peso) por grupo muscular.

    Fase 4 da reestruturacao: o catalogo novo (`exercise_catalog`) NAO tem
    mais as colunas primary_group_id/secondary_group_id/secondary_participation
    (elas ficaram para tras, isoladas em `exercise_zepp_mappings`, que segue
    intacta mas deixou de ser fonte de leitura para os motores). O fallback
    legado que consultava essas colunas foi removido. Para mapping_ids sem
    nenhuma linha explicita em `exercise_muscle_contributions` (fonte unica
    agora), vale a MESMA politica de `VolumeService` (M76 Lote 3c; antes esta
    funcao devolvia lista vazia e o exercicio sumia das telas que a usam):
    uma contribuicao PRIMARY em "Outros", sem `muscle_group_id`. Em producao
    (2026-10-07) nenhum exercicio usado em 90 dias esta nessa situacao."""
    if not mapping_ids:
        return {}

    from .volume_service import VolumeService

    service = VolumeService()
    contributions_by_mapping = service._fetch_contributions_by_mapping(mapping_ids)

    from .. import request_memo

    muscle_group_names_by_id: dict[int, str] = (
        {
            row["id"]: row["name"]
            for row in request_memo.memoize(
                ("muscle_groups", "id_name"), lambda: service.fetch_all("SELECT id, name FROM muscle_groups")
            )
        }
        if contributions_by_mapping
        else {}
    )
    return _resolve_from_rows(mapping_ids, contributions_by_mapping, muscle_group_names_by_id)


# Mesma leitura de `VolumeService._fetch_contributions_by_mapping` (tabela,
# filtro `is_active`, ordem), na conexao do CHAMADOR.
_CONTRIBUTIONS_SQL = """
SELECT exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight
FROM exercise_muscle_contributions
WHERE exercise_id IN ({placeholders}) AND is_active = 1
ORDER BY id ASC
"""


def resolve_muscle_contributions_with_cursor(
    cursor: Any, mapping_ids: set[int], group_names: Mapping[int, str] | None = None
) -> dict[int, list[dict[str, Any]]]:
    """`_resolve_muscle_contributions_for_mappings` na conexao do chamador (o
    mesmo SQL, a mesma politica de "Outros", os mesmos nomes): para quem ja
    esta dentro de uma transacao/conexao (avaliador da progressao, leitura das
    exposicoes do dashboard) e nao pode abrir outra. Duas leituras (contribuicoes
    + nomes dos grupos), qualquer que seja o numero de exercicios; uma so quando
    o chamador ja tem os nomes (`group_names`, ex.: a visao geral, que le
    `muscle_groups` UMA vez por requisicao)."""
    ids = sorted({int(value) for value in mapping_ids or () if value})
    if not ids:
        return {}
    cursor.execute(_CONTRIBUTIONS_SQL.format(placeholders=",".join(["%s"] * len(ids))), tuple(ids))
    contributions_by_mapping: dict[int, list[dict[str, Any]]] = {}
    for row in cursor.fetchall() or []:
        contributions_by_mapping.setdefault(int(row["exercise_id"]), []).append(row)
    if group_names is not None:
        return _resolve_from_rows(set(ids), contributions_by_mapping, group_names)
    names: dict[int, str] = {}
    group_ids = sorted(
        {int(row["muscle_group_id"]) for rows in contributions_by_mapping.values() for row in rows if row.get("muscle_group_id") is not None}
    )
    if group_ids:
        cursor.execute(
            f"SELECT id, name FROM muscle_groups WHERE id IN ({','.join(['%s'] * len(group_ids))})", tuple(group_ids)
        )
        names = {int(row["id"]): row["name"] for row in cursor.fetchall() or []}
    return _resolve_from_rows(set(ids), contributions_by_mapping, names)


def _resolve_from_rows(
    mapping_ids: set[int],
    contributions_by_mapping: Mapping[int, list[dict[str, Any]]],
    muscle_group_names_by_id: Mapping[int, str],
) -> dict[int, list[dict[str, Any]]]:
    from .volume_service import VolumeService

    result: dict[int, list[dict[str, Any]]] = {}
    for mapping_id in mapping_ids:
        # Politica unica para exercicio SEM contribuicao cadastrada (M76 Lote
        # 3c): a mesma de `VolumeService` - a serie conta como PRIMARY em
        # "Outros" (`muscle_group_id` None), com o peso PRIMARY default.
        # Quem agrega por `muscle_group_id` (reforco ABC, motor do Modo A)
        # ignora o id None; quem agrega por nome (ativacao muscular, series
        # equivalentes por atividade) mostra "Outros", como o Dashboard.
        resolved = VolumeService._resolve_contributions(
            mapping_id,
            contributions_by_mapping,
            None,
            None,
            None,
            None,
            None,
        )
        for item in resolved:
            if item["muscle_group_name"] is None and item["muscle_group_id"] is not None:
                item["muscle_group_name"] = muscle_group_names_by_id.get(item["muscle_group_id"])
        result[mapping_id] = resolved
    return result


def _accumulate_exercise_contribution(
    exercise: dict[str, Any],
    contributions_by_mapping_id: dict[int, list[dict[str, Any]]],
    totals: dict[int, float],
) -> None:
    """Soma sets_count * stimulus_weight de um exercicio em `totals` (por
    `muscle_group_id` — correcao 2026-08-25: antes era indexado por nome de
    grupo muscular, o que quebrava silenciosamente para qualquer grupo
    renomeado em `muscle_groups.name`; o id nunca muda). Ignora exercicio sem
    mapping_id/sets_count (nao ha como saber a que grupos ele contribui sem o
    mapping)."""
    mapping_id = exercise.get("mapping_id")
    sets_count = exercise.get("sets_count") or 0
    if not mapping_id or not sets_count:
        return
    for contribution in contributions_by_mapping_id.get(mapping_id, []):
        group_id = contribution.get("muscle_group_id")
        if group_id is None:
            continue
        totals[group_id] += sets_count * float(contribution.get("stimulus_weight") or 0.0)
