"""I/O da avaliacao por metodologia de volume (Fase 4, PR E).

O motor (`methodology_volume.py`) e PURO: recebe slots, contribuicoes PRIMARY
e grupos como entrada. Este modulo e quem monta essas entradas a partir do
banco e devolve o resultado em JSON - nada mais. Ele serve os dois caminhos
de `workout_plan_templates.py`:

- `POST /workout-plan-templates/methodology-volume-preview` (payload do
  builder, em memoria, nada e salvo) - `evaluate_slots`;
- `GET /workout-plan-templates/{id}/methodology-volume` e o gancho aditivo
  `_attach_methodology_volume_warnings` do POST/PUT - `evaluate_template`.

Os dois recebem a MESMA forma de slot (o Pydantic `WorkoutPlanTemplateSlotIn`
do builder ou o dict cru de `_get_template`), reduzida aqui ao que a
metodologia le: `mapping_id`, `sets_count`, `muscle_group_id`, o canonico e o
tipo do slot.

Tres leituras, todas planas (sem JOIN novo em `exercise_catalog` - a migracao
028 enumera as FKs validas do catalogo e recusa novas; aqui nao ha FK nem JOIN,
so `WHERE id IN (...)`):

1. `exercise_muscle_contributions` ATIVAS nos papeis do modo de contagem
   (`methodology_volume.COUNTED_ROLES_BY_MODE`: so PRIMARY em `primary_only`,
   PRIMARY + SECONDARY em `primary_secondary`; estabilizador nunca), com o
   `stimulus_weight` cadastrado - `load_volume_contributions`. Nao
   reaproveita `_resolve_muscle_contributions_for_mappings` de proposito:
   aquela devolve os tres papeis para a regua de series equivalentes, com a
   politica "sem contribuicao -> Outros"; a metodologia so quer os papeis do
   modo. Lidas pelo `mapping_id` E pelo canonico dele: item que ainda aponta
   para um id absorvido herda as contribuicoes do canonico (ver
   `load_methodology_inputs`).
2. `exercise_legacy_id_map` + `exercise_catalog` (ids ativos) para o
   exercicio CANONICO: a mesma regra de `services._resolve_canonical_exercise_id`
   / `exercise_mapping_canonical.UNRESOLVED_LINK_JOINS`
   (`COALESCE(elm.canonical_exercise_id, mapping_id)` que resolva para um
   catalogo ATIVO), so que em lote. Sem canonico ativo o motor cai para o
   `mapping_id` e conta em `data_quality.items_without_canonical`.
3. `muscle_groups` inteira (`id, name, volume_class_id,
   methodology_parent_group_id` e o `size_class` deprecated): o motor precisa
   de TODOS os grupos para declarar `NOT_DIRECTLY_TRAINED` em quem o Modelo
   nao toca.
4. Fase 6: o cadastro de macro-grupos (`muscle_volume_classes` +
   `muscle_volume_class_ranges`, migracao 057) - as faixas por bucket e a
   meta semanal, que antes eram constantes do motor (`load_volume_classes`).
   Desde a 073, POR MODO DE CONTAGEM: as tabelas da 057 sao o cadastro de
   `primary_only`; os outros modos tem as faixas em
   `muscle_volume_class_mode_ranges` e a meta semanal em
   `muscle_volume_class_weekly_targets`. E o UNICO lugar que escolhe a fonte
   pelo modo.

Modo A (`percent_budget`): a coluna `sets_count` e `NOT NULL DEFAULT 3`, entao
"item so com percentual" nao e "item sem sets_count" no banco. A regra aqui e
a do motor de ficha v2: item de grupo COM `priority_min_pct`/`priority_max_pct`
e governado pelo percentual (o `sets_count` e o default legado, ignorado pela
geracao) e entra no motor com `sets_count=None`; item SEM percentual entra
com o `sets_count` gravado. E o que faz `MODE_A_PERCENT_ONLY` acontecer em um
Modelo v2 real, e `MODE_A_NO_EXERCISES` em um Modelo legado por series.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from ..db import transaction
from ..domain.free_exercise_item import is_free_exercise_item
from .methodology_volume import (
    COUNTED_SETS_UNIT_LABELS,
    DEFAULT_VOLUME_COUNTING_MODE,
    ContributionRow,
    CountingModeComparison,
    GroupHierarchy,
    MethodologyGroupInput,
    MethodologyIntent,
    MethodologyItemInput,
    MethodologyRange,
    MethodologySeverity,
    MethodologySlotInput,
    MethodologyVolumeEvaluation,
    MethodologyVolumeStatus,
    RangeSource,
    VolumeClassSpec,
    VolumeContributions,
    VolumeCountingMode,
    compare_counting_modes,
    counted_roles,
    counted_roles_for_modes,
    counting_mode_for_window,
    counting_mode_or_default,
    evaluate_methodology_volume,
    hierarchy_of_group_name,
    index_hierarchy_by_name,
    resolve_group_hierarchy,
)
from .technique_volume import new_catalog, planned_item_equivalent

logger = logging.getLogger("fitness.methodology_volume")

# Eixos do payload que viram aviso no gancho aditivo (nome do campo na
# `GroupEvaluation` -> rotulo curto). Nunca `sets`/`volume` sozinhos.
_WARNING_AXES: tuple[tuple[str, str], ...] = (
    ("direct_exercises_axis", "direct_exercises"),
    ("direct_sets_axis", "direct_sets"),
    # Fase 6: meta semanal do macro-grupo.
    ("weekly_sets_axis", "weekly_sets"),
)
_WARNING_STATUSES = frozenset({MethodologyVolumeStatus.BELOW_RANGE, MethodologyVolumeStatus.ABOVE_RANGE})


# ---------------------------------------------------------------------------
# Leituras (unicas funcoes com banco neste modulo)
# ---------------------------------------------------------------------------


def _placeholders(values: Iterable[Any]) -> str:
    return ",".join(["%s"] * len(tuple(values)))


def _optional_int(value: Any) -> Optional[int]:
    return int(value) if value is not None else None


def _number(value: Any) -> Optional[float | int]:
    """DECIMAL(5,1) do cadastro -> numero do motor. Valor inteiro volta como
    `int` (15.0 -> 15): o payload e as mensagens seguem com "15-20", como na
    v1, e a meta fracionaria (12.5) segue fracionaria. PyMySQL entrega
    `Decimal`; a Data API pode entregar string - os dois passam por float."""
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def _source(value: Any) -> Optional[RangeSource]:
    return RangeSource(str(value)) if value is not None else None


def _group_input(row: Mapping[str, Any]) -> MethodologyGroupInput:
    return MethodologyGroupInput(
        id=int(row["id"]),
        name=str(row["name"] or ""),
        volume_class_id=_optional_int(row["volume_class_id"]),
        methodology_parent_group_id=_optional_int(row["methodology_parent_group_id"]),
        size_class=str(row["size_class"] or "unclassified"),
    )


def load_methodology_groups(cur: Any) -> tuple[MethodologyGroupInput, ...]:
    """Todos os grupos, com macro-grupo (057), pai da metodologia e o porte
    da 055 (deprecated, so ecoado no payload)."""
    cur.execute(
        "SELECT id, name, size_class, methodology_parent_group_id, volume_class_id "
        "FROM muscle_groups ORDER BY id ASC"
    )
    return tuple(_group_input(row) for row in cur.fetchall() or [])


# Grupos + nome e ordem do macro-grupo de cada um, num statement so (LEFT JOIN:
# grupo sem classe continua na lista). Leitura das telas de volume que agrupam
# por grupo pai / macro-grupo (`load_group_hierarchy`).
_GROUP_HIERARCHY_SQL = (
    "SELECT g.id, g.name, g.size_class, g.methodology_parent_group_id, g.volume_class_id, "
    "c.name AS volume_class_name, c.sort_order AS volume_class_sort_order "
    "FROM muscle_groups g LEFT JOIN muscle_volume_classes c ON c.id = g.volume_class_id "
    "ORDER BY g.id ASC"
)


def group_hierarchy_by_name(rows: Iterable[Mapping[str, Any]]) -> dict[str, GroupHierarchy]:
    """Linhas de `_GROUP_HIERARCHY_SQL` -> `nome do grupo -> GroupHierarchy`
    pela regra do motor (`resolve_group_hierarchy`). A ordem das classes e a
    do cadastro (`sort_order, id`, a mesma de `load_volume_classes`)."""
    rows = list(rows)
    classes: dict[int, tuple[Any, str]] = {}
    for row in rows:
        class_id = _optional_int(row.get("volume_class_id"))
        if class_id is not None and row.get("volume_class_name"):
            classes[class_id] = (row.get("volume_class_sort_order"), str(row["volume_class_name"]))
    ordered = sorted(
        classes.items(), key=lambda item: (item[1][0] is None, float(item[1][0] or 0), item[0])
    )
    hierarchy = resolve_group_hierarchy(
        (_group_input(row) for row in rows), {class_id: name for class_id, (_, name) in ordered}
    )
    return index_hierarchy_by_name(hierarchy)


def load_group_hierarchy() -> dict[str, GroupHierarchy]:
    """`nome do grupo -> GroupHierarchy` (grupo pai e macro-grupo resolvidos
    pelo motor) em UMA leitura fora de transacao (`db.fetch_rows`: 1 chamada na
    Data API), memoizada por requisicao. `muscle_groups.name` e UNIQUE: as
    leituras de volume entregam o grupo pelo nome."""
    from .. import request_memo
    from ..db import fetch_rows

    rows = request_memo.memoize(("muscle_groups", "hierarchy"), lambda: fetch_rows(_GROUP_HIERARCHY_SQL, ()))
    return group_hierarchy_by_name(rows)


def group_hierarchy_fields(hierarchy: GroupHierarchy) -> dict[str, Any]:
    """Campos por linha das rotas de volume (aditivos): grupo pai e macro-grupo
    com id e rotulo, e a posicao do macro-grupo no cadastro para a tela ordenar."""
    return {
        "parent_group_id": hierarchy.parent_group_id,
        "parent_group_name": hierarchy.parent_group_name,
        "volume_class_id": hierarchy.volume_class_id,
        "volume_class_name": hierarchy.volume_class_name,
        "volume_class_order": hierarchy.volume_class_order,
    }


def annotate_group_hierarchy(
    rows: list[dict[str, Any]],
    by_name: Mapping[str, GroupHierarchy],
    name_key: str = "muscle_group",
) -> list[dict[str, Any]]:
    """Acrescenta `group_hierarchy_fields` a cada linha (no lugar) pelo nome do
    grupo em `name_key`. Nao toca em nenhum numero da linha."""
    for row in rows:
        row.update(group_hierarchy_fields(hierarchy_of_group_name(str(row.get(name_key) or ""), by_name)))
    return rows


# Fontes por modo (073): as tabelas da 057 SAO o cadastro de `primary_only`
# (as que o codigo anterior lia, intocadas); os outros modos tem filhas
# proprias. LEFT JOIN da meta: classe sem linha do modo = sem meta semanal
# naquele modo (eixo semanal fora), nunca a de outro modo.
_CLASSES_PRIMARY_ONLY_SQL = (
    "SELECT id, name, weekly_sets_min, weekly_sets_max, weekly_source, weekly_evidence "
    "FROM muscle_volume_classes ORDER BY sort_order ASC, id ASC"
)
_RANGES_PRIMARY_ONLY_SQL = (
    "SELECT class_id, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max, "
    "sets_source, sets_evidence, exercises_source, exercises_evidence "
    "FROM muscle_volume_class_ranges"
)
_CLASSES_BY_MODE_SQL = (
    "SELECT c.id, c.name, w.weekly_sets_min, w.weekly_sets_max, w.weekly_source, w.weekly_evidence, "
    "w.copied_from_counting_mode AS weekly_copied_from_counting_mode "
    "FROM muscle_volume_classes c "
    "LEFT JOIN muscle_volume_class_weekly_targets w ON w.class_id = c.id AND w.counting_mode = %s "
    "ORDER BY c.sort_order ASC, c.id ASC"
)
_RANGES_BY_MODE_SQL = (
    "SELECT class_id, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max, "
    "sets_source, sets_evidence, exercises_source, exercises_evidence, copied_from_counting_mode "
    "FROM muscle_volume_class_mode_ranges WHERE counting_mode = %s"
)


def load_volume_classes(
    cur: Any, counting_mode: VolumeCountingMode | str = DEFAULT_VOLUME_COUNTING_MODE
) -> dict[int, VolumeClassSpec]:
    """Cadastro de macro-grupos (057): `id -> VolumeClassSpec`, com as faixas
    por bucket e a meta semanal DO MODO DE CONTAGEM pedido (073; default
    `primary_only`; teto de ativacao e realizado pedem o modo da sessao desde
    o Lote 4). Duas leituras
    planas (classes, faixas) no MESMO cursor/transacao do chamador; cadastro
    vazio -> `{}` (todos os grupos ficam `UNCLASSIFIED_GROUP`, nunca uma
    faixa inventada). Modo sem linha para um bucket = `NO_RANGE_FOR_FREQUENCY`
    (nunca a faixa de outro modo)."""
    mode = VolumeCountingMode(counting_mode)
    if mode is DEFAULT_VOLUME_COUNTING_MODE:
        cur.execute(_CLASSES_PRIMARY_ONLY_SQL)
        classes = cur.fetchall() or []
        cur.execute(_RANGES_PRIMARY_ONLY_SQL)
    else:
        cur.execute(_CLASSES_BY_MODE_SQL, (mode.value,))
        classes = cur.fetchall() or []
        cur.execute(_RANGES_BY_MODE_SQL, (mode.value,))
    ranges: dict[int, dict[str, MethodologyRange]] = {}
    for row in cur.fetchall() or []:
        ranges.setdefault(int(row["class_id"]), {})[str(row["frequency_bucket"])] = MethodologyRange(
            sets_min=_number(row["sets_min"]),  # type: ignore[arg-type]
            sets_max=_number(row["sets_max"]),  # type: ignore[arg-type]
            sets_source=RangeSource(str(row["sets_source"] or "admin")),
            sets_evidence=row["sets_evidence"],
            exercises_min=_optional_int(row["exercises_min"]),
            exercises_max=_optional_int(row["exercises_max"]),
            exercises_source=_source(row["exercises_source"]),
            exercises_evidence=row["exercises_evidence"],
            # 073: copia de `primary_only` ainda nao calibrada (so nas filhas).
            uncalibrated=row.get("copied_from_counting_mode") is not None,
        )
    return {
        int(row["id"]): VolumeClassSpec(
            id=int(row["id"]),
            name=str(row["name"] or ""),
            ranges=ranges.get(int(row["id"]), {}),
            weekly_sets_min=_number(row["weekly_sets_min"]),
            weekly_sets_max=_number(row["weekly_sets_max"]),
            weekly_source=_source(row["weekly_source"]),
            weekly_evidence=row["weekly_evidence"],
            weekly_uncalibrated=row.get("weekly_copied_from_counting_mode") is not None,
        )
        for row in classes
    }


def load_volume_contributions(
    cur: Any,
    exercise_ids: Iterable[int],
    mode: VolumeCountingMode | str = DEFAULT_VOLUME_COUNTING_MODE,
) -> dict[int, tuple[ContributionRow, ...]]:
    """`exercise_id -> linhas CRUAS (grupo, papel, stimulus_weight)` das
    contribuicoes ATIVAS nos papeis contados pelo modo (`counted_roles`), so
    para nao trazer linha que nenhum modo usa. Nao junta nada: o que cada
    linha vale no modo (peso 1 em `primary_only`, mesmo grupo em dois papeis)
    quem decide e o motor (`methodology_volume.counted_contributions`). Um
    statement, qualquer que seja o numero de exercicios."""
    return load_volume_contributions_for_modes(cur, exercise_ids, (mode,))


def load_volume_contributions_for_modes(
    cur: Any,
    exercise_ids: Iterable[int],
    modes: Iterable[VolumeCountingMode | str],
) -> dict[int, tuple[ContributionRow, ...]]:
    """`load_volume_contributions` para avaliar janelas em VARIOS modos com uma
    leitura so: os papeis lidos sao a uniao dos modos (`counted_roles_for_modes`,
    do motor). A linha crua de um papel que um modo nao conta nao muda nada
    nele: `counted_contributions` filtra pelo modo. So `primary_only` = a
    mesma leitura de `load_volume_contributions(cur, ids)`. Um statement."""
    ids = sorted({int(value) for value in exercise_ids if value})
    if not ids:
        return {}
    roles = sorted(counted_roles_for_modes(modes))
    cur.execute(
        f"""
        SELECT exercise_id, muscle_group_id, role, stimulus_weight
        FROM exercise_muscle_contributions
        WHERE exercise_id IN ({_placeholders(ids)}) AND role IN ({_placeholders(roles)}) AND is_active = 1
        """,
        (*ids, *roles),
    )
    grouped: dict[int, list[ContributionRow]] = {}
    for row in cur.fetchall() or []:
        raw = row.get("stimulus_weight")
        grouped.setdefault(int(row["exercise_id"]), []).append(
            ContributionRow(
                muscle_group_id=int(row["muscle_group_id"]),
                role=str(row.get("role") or ""),
                stimulus_weight=float(raw) if raw is not None else None,
            )
        )
    return {exercise_id: tuple(rows) for exercise_id, rows in grouped.items()}


# ---------------------------------------------------------------------------
# Modo de contagem POR SESSAO (Lote 4): atividade -> ficha -> versao do Modelo
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _session_mode_select() -> str:
    """SELECT/FROM comum das duas leituras do modo por sessao. A versao que
    governa a ficha da sessao e a de `template_versions.plan_version_expr`
    (a MESMA regra de todos os leitores por data: a versao gravada na ficha -
    066 - ou, em ficha antiga, a vigente no fim do `plan_date`, ou a vigente).
    Sessao sem ficha, ficha sem Modelo ou versao nao encontrada -> NULL -> o
    default do motor (`counting_mode_or_default`). Montado na primeira
    chamada (o import de `template_versions` aqui criaria ciclo no pacote)."""
    from ..template_versions import plan_version_expr

    return (
        "SELECT a.id AS activity_id, v.volume_counting_mode AS volume_counting_mode "
        "FROM activities a "
        "LEFT JOIN workout_plans wp ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id "
        f"LEFT JOIN workout_plan_template_versions v ON v.id = {plan_version_expr('wp', by_date=True)} "
    )


def load_session_counting_modes(cur: Any, activity_ids: Iterable[int]) -> dict[int, VolumeCountingMode]:
    """`activity_id -> modo de contagem` da versao do Modelo que originou cada
    sessao (`counting_mode_or_default`: sem Modelo = `primary_only`). UM
    statement para a janela inteira, qualquer que seja o numero de sessoes;
    sem sessao, nenhum."""
    ids = sorted({int(value) for value in activity_ids if value})
    if not ids:
        return {}
    cur.execute(f"{_session_mode_select()}WHERE a.id IN ({_placeholders(ids)})", tuple(ids))
    modes = {int(row["activity_id"]): counting_mode_or_default(row.get("volume_counting_mode")) for row in cur.fetchall() or []}
    return {activity_id: modes.get(activity_id, DEFAULT_VOLUME_COUNTING_MODE) for activity_id in ids}


def load_counting_mode_at(cur: Any, user_id: int, day: Any) -> VolumeCountingMode:
    """Modo da janela do REALIZADO que termina em `day` (dia do usuario): o da
    sessao MAIS RECENTE ate `day` (`counting_mode_for_window`), entre as
    atividades com exercicio mapeado - as que entram em
    `VolumeService.get_daily_muscle_activation`. Um statement: a subconsulta
    escolhe PRIMEIRO o `id` da sessao (so `activities` do usuario e
    `exercises` por atividade) e so essa linha faz o JOIN com a ficha e a
    versao - nunca o JOIN para todo o historico antes do `LIMIT`."""
    cur.execute(
        f"{_session_mode_select()}"
        "WHERE a.id = (SELECT s.id FROM activities s "
        "WHERE s.user_id = %s AND s.local_date <= %s "
        # Subconsulta escalar correlata (nao EXISTS): o MySQL 8 transforma o
        # EXISTS em semijoin e materializa os exercicios mapeados de TODOS os
        # usuarios; assim e uma busca por atividade em
        # `idx_exercises_activity_sequence` (EXPLAIN no §41).
        "AND (SELECT 1 FROM exercises e WHERE e.activity_id = s.id AND e.mapping_id IS NOT NULL LIMIT 1) IS NOT NULL "
        "ORDER BY s.local_date DESC, COALESCE(s.ended_at, s.started_at, s.created_at) DESC, s.id DESC LIMIT 1)",
        (int(user_id), str(day)[:10]),
    )
    row = cur.fetchone()
    return counting_mode_for_window([row.get("volume_counting_mode")] if row else [])


def load_canonical_exercise_ids(cur: Any, mapping_ids: set[int]) -> dict[int, Optional[int]]:
    """`mapping_id -> id canonico ATIVO em exercise_catalog` (None quando nao
    resolve). Mesma regra de `services._resolve_canonical_exercise_id`:
    `exercise_legacy_id_map` primeiro, o proprio id quando nao ha linha; e so
    e canonico se esse id estiver ativo no catalogo."""
    ids = sorted({int(value) for value in mapping_ids if value})
    if not ids:
        return {}
    cur.execute(
        f"""
        SELECT old_exercise_id, canonical_exercise_id
        FROM exercise_legacy_id_map
        WHERE old_exercise_id IN ({_placeholders(ids)})
        """,
        tuple(ids),
    )
    legacy = {int(row["old_exercise_id"]): int(row["canonical_exercise_id"]) for row in (cur.fetchall() or [])}
    resolved = {mapping_id: legacy.get(mapping_id, mapping_id) for mapping_id in ids}
    candidates = sorted(set(resolved.values()))
    cur.execute(
        f"SELECT id FROM exercise_catalog WHERE id IN ({_placeholders(candidates)}) AND is_active = 1",
        tuple(candidates),
    )
    active = {int(row["id"]) for row in (cur.fetchall() or [])}
    return {mapping_id: (canonical if canonical in active else None) for mapping_id, canonical in resolved.items()}


def load_inherited_contributions(
    cur: Any, mapping_ids: Iterable[int], counting_mode: VolumeCountingMode | str = DEFAULT_VOLUME_COUNTING_MODE
) -> tuple[dict[int, Optional[int]], dict[int, tuple[ContributionRow, ...]]]:
    """`(canonical_ids, mapping_id -> linhas cruas)` dos papeis do modo, lidas
    pelo `mapping_id` E pelo canonico: id absorvido pela consolidacao do
    catalogo herda as contribuicoes do canonico. Leitura de
    `load_methodology_inputs` e do contexto do prompt da Periodizacao (o
    catalogo da academia no modo ponderado), no cursor de quem chama."""
    ids = {int(value) for value in mapping_ids if value}
    canonical_ids = load_canonical_exercise_ids(cur, ids)
    lookup_ids = ids | {c for c in canonical_ids.values() if c is not None}
    by_exercise = load_volume_contributions(cur, lookup_ids, counting_mode)
    contributions: dict[int, tuple[ContributionRow, ...]] = {}
    for mapping_id in ids:
        own = by_exercise.get(mapping_id)
        if own:
            contributions[mapping_id] = own
            continue
        canonical = canonical_ids.get(mapping_id)
        inherited = by_exercise.get(canonical) if canonical is not None else None
        if inherited:
            contributions[mapping_id] = inherited
    return canonical_ids, contributions


@dataclasses.dataclass(frozen=True)
class MethodologyInputs:
    """As leituras, ja reduzidas ao que o motor recebe."""

    canonical_ids: Mapping[int, Optional[int]]
    # `mapping_id -> linhas cruas (ContributionRow)` nos papeis de `counting_mode`
    # (um conjunto de grupos e aceito pelo motor como atalho de primary_only).
    contributions: VolumeContributions
    groups: tuple[MethodologyGroupInput, ...]
    # Fase 6: cadastro de macro-grupos (057). Default vazio para quem monta
    # `MethodologyInputs` a mao (testes antigos): sem cadastro, nada e avaliado.
    volume_classes: Mapping[int, VolumeClassSpec] = dataclasses.field(default_factory=dict)
    # Modo em que `contributions` foi lido (o motor recebe o mesmo).
    counting_mode: VolumeCountingMode = DEFAULT_VOLUME_COUNTING_MODE


def load_methodology_inputs(
    mapping_ids: set[int], counting_mode: VolumeCountingMode | str = DEFAULT_VOLUME_COUNTING_MODE
) -> MethodologyInputs:
    """UMA transacao para todas as leituras (na RDS Data API cada
    `transaction()` e um begin/commit a mais) - inclusive o cadastro de
    macro-grupos (Fase 6). E a unica costura com banco deste modulo - os
    testes unitarios trocam so ela.

    As contribuicoes sao lidas pelo `mapping_id` E pelo canonico: um item que
    ainda aponta para um id absorvido pela consolidacao do catalogo herda as
    contribuicoes do canonico em vez de aparecer como "sem contribuicao".

    Faixas e meta semanal: as do PROPRIO modo (`load_volume_classes`, 073)."""
    counting_mode = VolumeCountingMode(counting_mode)
    with transaction() as conn:
        with conn.cursor() as cur:
            canonical_ids, contributions = load_inherited_contributions(cur, mapping_ids, counting_mode)
            groups = load_methodology_groups(cur)
            volume_classes = load_volume_classes(cur, counting_mode)
    return MethodologyInputs(
        canonical_ids=canonical_ids,
        contributions=contributions,
        groups=groups,
        volume_classes=volume_classes,
        counting_mode=counting_mode,
    )


# ---------------------------------------------------------------------------
# Normalizacao (pura): slot do builder OU linha de `_get_template` -> entrada
# ---------------------------------------------------------------------------


def _field(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _as_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _item_input(
    item: Any,
    volume_mode: str,
    canonical_ids: Mapping[int, Optional[int]],
    catalog: Any = None,
    item_index: Optional[int] = None,
) -> MethodologyItemInput:
    mapping_id = _as_int(_field(item, "mapping_id"))
    sets_count = _as_int(_field(item, "sets_count"))
    muscle_group_id = _as_int(_field(item, "muscle_group_id"))
    if volume_mode == "percent_budget":
        # Item de grupo governado pelo percentual (motor v2): o `sets_count`
        # gravado e o default da coluna, nao uma prescricao - ver docstring.
        percent_only = _field(item, "priority_min_pct") is not None or _field(item, "priority_max_pct") is not None
        return MethodologyItemInput(
            mapping_id=None,
            canonical_exercise_id=None,
            sets_count=None if percent_only else sets_count,
            muscle_group_id=muscle_group_id if _field(item, "item_kind") == "muscle_group" else None,
        )
    return MethodologyItemInput(
        mapping_id=mapping_id,
        canonical_exercise_id=canonical_ids.get(mapping_id) if mapping_id is not None else None,
        sets_count=sets_count,
        muscle_group_id=muscle_group_id,
        # v4 (Fase 5, PR 5): `direct_sets = sets_count + equivalente planejado`.
        technique_equivalent=planned_item_equivalent(item, catalog),
        # Lote 3: posicao no corpo recebido, para o eco da fatia por item.
        item_index=item_index,
        # M101c: item sem par conta no grupo primario (dono `domain/free_exercise_item`).
        free_exercise=is_free_exercise_item(item),
    )


def build_slot_inputs(
    slots: Iterable[Any], volume_mode: str, canonical_ids: Mapping[int, Optional[int]], catalog: Any = None
) -> tuple[MethodologySlotInput, ...]:
    """Reduz os slots (Pydantic do builder ou dicts de `_get_template`) ao que
    o motor le, ordenados por `sequence` (mesma regra de `_build_cycle`).

    `catalog`: catalogo de tecnicas (`code -> linha`) para o equivalente
    planejado; sem ele, um preguicoso - so le o banco se algum item tiver
    tecnica com aplicacoes."""
    if catalog is None:
        catalog = new_catalog()
    ordered = sorted(slots, key=lambda slot: int(_field(slot, "sequence", 0) or 0))
    return tuple(
        MethodologySlotInput(
            sequence=int(_field(slot, "sequence", 0) or 0),
            slot_type=str(_field(slot, "slot_type", "normal") or "normal"),
            name=str(_field(slot, "name", "") or ""),
            items=tuple(
                _item_input(item, volume_mode, canonical_ids, catalog, index)
                for index, item in enumerate(_field(slot, "items") or [])
                # Cardio nao e trabalho de grupo em nenhum modo: fora antes do
                # motor, para nao inflar `items_without_mapping` com algo que
                # nao e falha de cadastro. O indice e o do corpo RECEBIDO.
                if _field(item, "item_kind") != "cardio"
            ),
        )
        for slot in ordered
    )


def mapping_ids_of(slots: Iterable[Any]) -> set[int]:
    return {
        int(_field(item, "mapping_id"))
        for slot in slots
        for item in (_field(slot, "items") or [])
        if _field(item, "mapping_id")
    }


# ---------------------------------------------------------------------------
# Avaliacao (carrega + chama o motor)
# ---------------------------------------------------------------------------


def evaluate_slots(
    slots: Iterable[Any],
    volume_mode: str,
    intent: MethodologyIntent,
    counting_mode: VolumeCountingMode | str = DEFAULT_VOLUME_COUNTING_MODE,
) -> MethodologyVolumeEvaluation:
    """Caminho unico dos dois endpoints e do gancho: carrega as tres leituras e
    entrega ao motor puro. Modo A nao tem `mapping_id` que importe (o item ja e
    o grupo), entao as leituras de exercicio ficam vazias. `counting_mode`: o
    do Modelo (073) - quem chama resolve (`evaluate_template` le o gravado; o
    preview usa o do corpo ou o do Modelo)."""
    slots = tuple(slots)
    mapping_ids = mapping_ids_of(slots) if volume_mode == "equipment_series" else set()
    inputs = load_methodology_inputs(mapping_ids, counting_mode)
    return evaluate_methodology_volume(
        build_slot_inputs(slots, volume_mode, inputs.canonical_ids),
        inputs.contributions,
        inputs.groups,
        volume_mode,
        intent,
        inputs.volume_classes,
        inputs.counting_mode,
    )


def counting_mode_of_template(template: Optional[Mapping[str, Any]]) -> VolumeCountingMode:
    """Modo PERSISTIDO do Modelo (dict de `_get_template`: o da versao lida -
    a vigente, ou a da data em leitura historica). Ausente/NULL (Modelo lido
    sem a coluna) = o default do motor."""
    value = (template or {}).get("volume_counting_mode")
    return VolumeCountingMode(value) if value else DEFAULT_VOLUME_COUNTING_MODE


def evaluate_template(template: Mapping[str, Any], intent: MethodologyIntent) -> MethodologyVolumeEvaluation:
    """Modelo SALVO (dict de `_get_template`), no modo de contagem gravado nele."""
    return evaluate_slots(
        template.get("slots") or [],
        str(template.get("volume_mode") or "percent_budget"),
        intent,
        counting_mode_of_template(template),
    )


def evaluate_template_all_modes(template: Mapping[str, Any], intent: MethodologyIntent) -> CountingModeComparison:
    """O Modelo SALVO avaliado em CADA `VolumeCountingMode`, lado a lado
    (`methodology_volume.compare_counting_modes`), para o usuario ver o efeito
    antes de trocar o modo. Uma transacao so: as contribuicoes sao lidas uma
    vez com os papeis de todos os modos (o motor filtra pelo modo em
    `counted_contributions`) e as faixas/metas, uma vez por modo."""
    slots = tuple(template.get("slots") or [])
    volume_mode = str(template.get("volume_mode") or "percent_budget")
    mapping_ids = mapping_ids_of(slots) if volume_mode == "equipment_series" else set()
    modes = tuple(VolumeCountingMode)
    with transaction() as conn:
        with conn.cursor() as cur:
            widest = max(modes, key=lambda mode: len(counted_roles(mode)))
            canonical_ids, contributions = load_inherited_contributions(cur, mapping_ids, widest)
            groups = load_methodology_groups(cur)
            classes_by_mode = {mode: load_volume_classes(cur, mode) for mode in modes}
    slot_inputs = build_slot_inputs(slots, volume_mode, canonical_ids)
    evaluations = [
        evaluate_methodology_volume(
            slot_inputs, contributions, groups, volume_mode, intent, classes_by_mode[mode], mode
        )
        for mode in modes
    ]
    return compare_counting_modes(evaluations, counting_mode_of_template(template))


# ---------------------------------------------------------------------------
# Serializacao e avisos
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (frozenset, set)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def comparison_to_payload(comparison: CountingModeComparison) -> dict[str, Any]:
    """Comparacao entre modos -> dict JSON (mesma serializacao da avaliacao)."""
    return _jsonable(comparison)


def evaluation_to_payload(evaluation: MethodologyVolumeEvaluation) -> dict[str, Any]:
    """Dataclasses -> dict JSON (enums pelo valor, frozenset como lista
    ordenada). As chaves sao as do motor: `direct_sets*`, nunca `sets`."""
    return _jsonable(evaluation)


def warnings_from_evaluation(evaluation: MethodologyVolumeEvaluation) -> list[dict[str, Any]]:
    """Recorte para o gancho aditivo do POST/PUT: so `BELOW_RANGE`/`ABOVE_RANGE`
    com `severity: warning`. `informational` (desvio que a intencao declarada
    espera), `NOT_DIRECTLY_TRAINED` e `NOT_EVALUABLE` nao viram aviso - o
    plano e explicito que falso alarme ensina o usuario a ignorar o painel."""
    warnings: list[dict[str, Any]] = []
    for group in evaluation.groups:
        for attr, axis in _WARNING_AXES:
            evaluated = getattr(group, attr)
            if evaluated.status not in _WARNING_STATUSES or evaluated.severity is not MethodologySeverity.WARNING:
                continue
            warnings.append(
                {
                    "muscle_group_id": group.muscle_group_id,
                    "muscle_group_name": group.muscle_group_name,
                    "volume_class_id": group.volume_class_id,
                    "volume_class_name": group.volume_class_name,
                    "axis": axis,
                    "status": evaluated.status.value,
                    "severity": evaluated.severity.value,
                    "value_per_session": evaluated.value_per_session,
                    "value_weekly": evaluated.value_weekly,
                    "range_min": evaluated.range_min,
                    "range_max": evaluated.range_max,
                    "range_source": evaluated.range_source.value if evaluated.range_source else None,
                    "frequency_bucket": group.frequency_bucket,
                    "message": _warning_message(group, axis, evaluated, evaluation.counting_mode),
                }
            )
    return warnings


def _warning_message(
    group: Any, axis: str, evaluated: Any, counting_mode: VolumeCountingMode | str = DEFAULT_VOLUME_COUNTING_MODE
) -> str:
    """Texto do aviso, com o nome do macro-grupo (Fase 6). O eixo semanal fala
    de séries por SEMANA contra a meta semanal; os por sessão, da faixa. A
    unidade das séries é a do modo de contagem (`COUNTED_SETS_UNIT_LABELS`)."""
    sets_unit = COUNTED_SETS_UNIT_LABELS[VolumeCountingMode(counting_mode)]
    below = evaluated.status is MethodologyVolumeStatus.BELOW_RANGE
    label = group.muscle_group_name
    if group.volume_class_name:
        label = f"{label} (macro-grupo {group.volume_class_name})"
    if axis == "weekly_sets":
        return (
            f"{label}: {evaluated.value_weekly:g} {sets_unit} por semana; "
            f"meta semanal {evaluated.range_min:g}–{evaluated.range_max:g}; "
            f"{'abaixo' if below else 'acima'} da meta."
        )
    unit = "exercícios diretos" if axis == "direct_exercises" else sets_unit
    return (
        f"{label}: {evaluated.value_per_session:g} {unit} por sessão "
        f"em {group.weekly_frequency:g}x/semana; faixa {evaluated.range_min:g}–{evaluated.range_max:g}; "
        f"{'abaixo' if below else 'acima'} da faixa."
    )


__all__ = [
    "annotate_group_hierarchy",
    "build_slot_inputs",
    "group_hierarchy_by_name",
    "group_hierarchy_fields",
    "load_group_hierarchy",
    "comparison_to_payload",
    "counting_mode_of_template",
    "evaluate_slots",
    "evaluate_template",
    "evaluate_template_all_modes",
    "evaluation_to_payload",
    "MethodologyInputs",
    "load_canonical_exercise_ids",
    "load_inherited_contributions",
    "load_methodology_groups",
    "load_methodology_inputs",
    "load_volume_contributions",
    "load_volume_classes",
    "mapping_ids_of",
    "warnings_from_evaluation",
]
