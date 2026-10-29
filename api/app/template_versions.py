"""Versoes do Modelo de Treino: UMA vigente por Modelo, com historico
imutavel (migracao 065; PR1 do versionamento de Modelo).

Mesma gramatica de `template_assignments` (M23, 049) e `training_type_targets`
(M12, 052): intervalo SEMIABERTO `[effective_from, superseded_at)`, invariante
"uma vigente" no banco (`current_template_id` + UNIQUE) e um escritor que
fecha a versao anterior e abre a seguinte no MESMO instante do relogio do
banco.

O que e uma versao: o cabecalho ESTRUTURAL (`training_type_id`,
`volume_mode`, `template_type`, grade, modo de contagem do volume - 073), a
intencao da metodologia e as linhas
de `workout_plan_template_slots` com aquele `version_id` (os itens vem pelo
`slot_id`). Nome, descricao e `is_active` NAO versionam: mudar o nome nao muda
o que o usuario treina, e uma fronteira de versao ali seria ficticia.

As linhas de uma versao nunca mudam depois de gravadas. Editar = gravar a
versao n+1; nada e apagado.

Tres perguntas de leitura, como em `training_type_targets`:

- `resolve_version_now(template_id)` - a vigente (`superseded_at IS NULL`);
- `resolve_version_at(template_id, momento)` - a que valia NAQUELE instante;
- `resolve_version_for_window(template_id, date_to)` - a que governa uma
  janela que TERMINA em `date_to` (mesma regra do M23/M12); sem `date_to`,
  a vigente.

`effective_from IS NULL` = vigente desde sempre (a v1 que a 065 criou para os
Modelos que ja existiam). Toda leitura por data trata NULL como -infinito.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from .db import transaction
from .domain import cardio_prescription
from .domain import training_goal as goal_domain
from .domain.set_measures import normalize_measure
from .template_assignments import _as_moment, db_now

TABLE = "workout_plan_template_versions"

def counting_mode_of(header: Any) -> str:
    """Modo de contagem de um cabecalho (payload, linha ou `TemplateVersion`);
    ausente/NULL = o default do motor (`methodology_volume.
    DEFAULT_VOLUME_COUNTING_MODE`, o mesmo da coluna da 073). NAO valida o
    dominio: quem grava valida (Pydantic do Modelo) e o ENUM do banco recusa o
    resto. Import tardio: este modulo e importado por leitores do dashboard que
    o pacote `intelligence` importa (ciclo no import)."""
    value = _get(header, "volume_counting_mode")
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    value = getattr(value, "value", value)  # enum do motor
    if value:
        return str(value)
    from .intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE

    return DEFAULT_VOLUME_COUNTING_MODE.value


# ---------------------------------------------------------------------------
# SQL (um lugar so)
# ---------------------------------------------------------------------------

_VERSION_COLUMNS = (
    "v.id, v.template_id, v.version_number, v.training_type_id, v.volume_mode, v.template_type, "
    "v.grid_rows, v.grid_cols, v.methodology_intent, v.methodology_target_group_ids, "
    "v.volume_counting_mode, v.training_goal, v.effective_from, v.superseded_at, v.created_by_user_id, v.reverted_from_version_id, "
    "v.change_summary, v.created_at"
)

# Vigente agora: o UNIQUE de `current_template_id` garante no maximo uma.
CURRENT_VERSION_SQL = f"SELECT {_VERSION_COLUMNS} FROM {TABLE} v WHERE v.current_template_id = %s"

# Vigente NAQUELE instante. `superseded_at` e EXCLUSIVO; uma versao de duracao
# zero (`[x, x)`, duas edicoes no mesmo segundo) nao governa instante nenhum.
VERSION_AT_SQL = (
    f"SELECT {_VERSION_COLUMNS} FROM {TABLE} v WHERE v.template_id = %s "
    "AND (v.effective_from IS NULL OR v.effective_from <= %s) "
    "AND (v.superseded_at IS NULL OR v.superseded_at > %s) "
    "ORDER BY v.version_number DESC LIMIT 1"
)

MAX_VERSION_NUMBER_SQL = f"SELECT COALESCE(MAX(version_number), 0) AS n FROM {TABLE} WHERE template_id = %s"

SUPERSEDE_SQL = f"UPDATE {TABLE} SET superseded_at = %s WHERE id = %s AND superseded_at IS NULL"

INSERT_VERSION_SQL = f"""
INSERT INTO {TABLE} (
    template_id, version_number, training_type_id, volume_mode, template_type,
    grid_rows, grid_cols, methodology_intent, methodology_target_group_ids,
    effective_from, created_by_user_id, reverted_from_version_id, change_summary,
    volume_counting_mode, training_goal
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

SLOT_FIELDS = ("sequence", "name", "weekday", "slot_type", "priority_min_pct", "priority_max_pct")

ITEM_FIELDS = (
    "item_order",
    "item_kind",
    "muscle_group_id",
    "mapping_id",
    "equipment_id",
    "sets_count",
    "peak_contraction_seconds",
    "reps_target",
    "rpe_target",
    "prioritize",
    "priority_min_pct",
    "priority_max_pct",
    "cardio_duration_minutes",
    "advanced_technique",
    "advanced_technique_applications",
    "advanced_technique_duration_seconds",
    "warmup_sets_count",
    "warmup_reps",
    "warmup_rest_seconds",
    "feeler_set_sets_count",
    "feeler_set_reps",
    "feeler_set_rest_seconds",
    "rest_seconds",
    # Medida da serie (Lote T2; colunas da 089). `measure` e NOT NULL DEFAULT
    # 'reps': gravada e comparada pelo `normalize_measure` do dono (ausente =
    # reps), para o cliente antigo nao abrir versao nem gravar NULL.
    "measure",
    "duration_seconds_target",
    "time_window_seconds",
    # Objetivo de treino do item (M97 F0; coluna NULL da 093; dono
    # `domain/training_goal.py`): override do objetivo do Modelo, NULL = herda.
    # Gravado pela leitura do dono e, na forma canonica, so entra quando
    # preenchido (`_canonical_items`) - Modelo sem override compara igual.
    "training_goal",
    # Prescricao de cardio (M98 C0; colunas da 095; dono
    # `domain/cardio_prescription.py`). Gravadas pela normalizacao do dono
    # (modo resolvido, so os campos do modo; item que nao e cardio = NULL) e,
    # na forma canonica, so entram quando diferem de um item antigo (so
    # duracao) - Modelo de antes da 095 compara igual.
    *cardio_prescription.NEW_COLUMNS,
)

# Tipo de cada campo na COLUNA (todas as numericas sao inteiras: TINYINT,
# SMALLINT, INT, BIGINT - escala 0). E por elas que a comparacao normaliza.
_STR_FIELDS = frozenset(
    {"name", "slot_type", "item_kind", "reps_target", "rpe_target", "advanced_technique", "measure",
     cardio_prescription.MODE_FIELD}
)
# Campos com leitura propria do dono (aplicada na escrita e na comparacao).
_MEASURE_FIELD = "measure"
_GOAL_FIELD = goal_domain.TRAINING_GOAL_COLUMN
# Campos da prescricao de cardio: valor pela normalizacao do dono (depende do
# item inteiro - tipo e modo -, nao so do campo).
_CARDIO_FIELDS = frozenset(cardio_prescription.PRESCRIPTION_FIELDS)
# Forma canonica do item: os campos de antes da 093, na mesma ordem (o
# objetivo e a prescricao de cardio da 095 entram a parte e so quando
# preenchidos/diferentes do item antigo).
_CANONICAL_ITEM_FIELDS = tuple(
    f for f in ITEM_FIELDS if f != _GOAL_FIELD and f not in cardio_prescription.NEW_COLUMNS
)
_BOOL_FIELDS = frozenset({"prioritize"})

VERSION_SLOTS_SQL = (
    "SELECT s.id, s.template_id, " + ", ".join(f"s.{f}" for f in SLOT_FIELDS) + " "
    "FROM workout_plan_template_slots s WHERE s.version_id = %s ORDER BY s.sequence ASC"
)

VERSION_ITEMS_SQL = (
    "SELECT i.id, i.slot_id, " + ", ".join(f"i.{f}" for f in ITEM_FIELDS) + " "
    "FROM workout_plan_template_slot_items i "
    "JOIN workout_plan_template_slots s ON s.id = i.slot_id "
    "WHERE s.version_id = %s ORDER BY i.slot_id ASC, i.item_order ASC, i.id ASC"
)

INSERT_SLOT_SQL = (
    "INSERT INTO workout_plan_template_slots (template_id, version_id, "
    + ", ".join(SLOT_FIELDS)
    + ") VALUES ("
    + ", ".join(["%s"] * (len(SLOT_FIELDS) + 2))
    + ")"
)

INSERT_ITEM_SQL = (
    "INSERT INTO workout_plan_template_slot_items (slot_id, "
    + ", ".join(ITEM_FIELDS)
    + ") VALUES ("
    + ", ".join(["%s"] * (len(ITEM_FIELDS) + 1))
    + ")"
)

# Copia a arvore de uma versao para outra (versao nova so de intencao): dois
# INSERT ... SELECT, sem trazer linha nenhuma para o Python. Os itens acham o
# slot novo pela `sequence`, que e UNICA por versao (065).
COPY_SLOTS_SQL = (
    "INSERT INTO workout_plan_template_slots (template_id, version_id, "
    + ", ".join(SLOT_FIELDS)
    + ") SELECT s.template_id, %s, "
    + ", ".join(f"s.{f}" for f in SLOT_FIELDS)
    + " FROM workout_plan_template_slots s WHERE s.version_id = %s"
)

COPY_ITEMS_SQL = (
    "INSERT INTO workout_plan_template_slot_items (slot_id, "
    + ", ".join(ITEM_FIELDS)
    + ") SELECT ns.id, "
    + ", ".join(f"i.{f}" for f in ITEM_FIELDS)
    + " FROM workout_plan_template_slot_items i "
    "JOIN workout_plan_template_slots os ON os.id = i.slot_id "
    "JOIN workout_plan_template_slots ns ON ns.version_id = %s AND ns.sequence = os.sequence "
    "WHERE os.version_id = %s"
)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _target_ids(value: Any) -> tuple[int, ...]:
    """Coluna JSON -> tupla ORDENADA de ids. O pymysql devolve `str`, a Data
    API tambem; um chamador pode passar a lista ja decodificada. A ordem do
    `JSON_ARRAYAGG` do backfill nao e garantida - por isso ordena."""
    if value is None or value == "":
        return ()
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        value = json.loads(value)
    return tuple(sorted({int(v) for v in (value or [])}))


@dataclass(frozen=True)
class TemplateVersion:
    """Uma linha de `workout_plan_template_versions`. `superseded_at is None`
    = vigente."""

    id: int
    template_id: int
    number: int
    training_type_id: Optional[int]
    volume_mode: str
    template_type: str
    grid_rows: Optional[int]
    grid_cols: Optional[int]
    methodology_intent: str
    target_group_ids: tuple[int, ...]
    effective_from: Optional[datetime]
    superseded_at: Optional[datetime]
    created_by_user_id: Optional[int] = None
    reverted_from_version_id: Optional[int] = None
    change_summary: Optional[str] = None
    # Modo de contagem do volume da metodologia (073): o VALOR de
    # `methodology_volume.VolumeCountingMode`, gravado na versao (sempre
    # preenchido por `row_to_version`; quem le usa `counting_mode_of`).
    volume_counting_mode: Optional[str] = None
    # Objetivo de treino do Modelo (093): o VALOR de `training_goal.TrainingGoal`
    # (sempre preenchido por `row_to_version`; linha sem a coluna = default).
    training_goal: Optional[str] = None

    @property
    def is_current(self) -> bool:
        return self.superseded_at is None

    def intent(self) -> dict[str, Any]:
        return {"intent": self.methodology_intent, "target_group_ids": list(self.target_group_ids)}


def _opt_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def row_to_version(row: Optional[Mapping[str, Any]]) -> Optional[TemplateVersion]:
    if not row:
        return None
    return TemplateVersion(
        id=int(row["id"]),
        template_id=int(row["template_id"]),
        number=int(row["version_number"]),
        training_type_id=_opt_int(row.get("training_type_id")),
        volume_mode=str(row.get("volume_mode") or "percent_budget"),
        template_type=str(row.get("template_type") or "full_body"),
        grid_rows=_opt_int(row.get("grid_rows")),
        grid_cols=_opt_int(row.get("grid_cols")),
        methodology_intent=str(row.get("methodology_intent") or "standard"),
        target_group_ids=_target_ids(row.get("methodology_target_group_ids")),
        effective_from=row.get("effective_from"),
        superseded_at=row.get("superseded_at"),
        created_by_user_id=_opt_int(row.get("created_by_user_id")),
        reverted_from_version_id=_opt_int(row.get("reverted_from_version_id")),
        change_summary=row.get("change_summary"),
        volume_counting_mode=counting_mode_of(row),
        training_goal=goal_domain.goal_value(row.get("training_goal")),
    )


# ---------------------------------------------------------------------------
# Versao de uma FICHA, como expressao SQL (PR3: leitores por data)
# ---------------------------------------------------------------------------


def plan_version_expr(plan_alias: str = "wp", *, by_date: bool, timezone_name: Optional[str] = None) -> str:
    """Expressao SQL com o `id` da versao do Modelo que governa uma ficha
    (`workout_plans` com alias `plan_alias`) - para entrar num JOIN ou numa
    coluna do statement que JA existe, sem ida extra ao banco (uma subconsulta
    correlata nao e um statement).

    - `by_date=True` (usado por todos os leitores desde a aposentadoria da
      flag, 2026-10-08): a versao
      gravada na ficha (066) ou, em ficha antiga, a vigente no FIM do
      `plan_date` (mesma regra de `_as_moment`: `effective_from` ate o fim do
      dia, `superseded_at` exclusivo) ou, por fim, a vigente;
    - `by_date=False`: a vigente (sem leitor de producao; so testes).

    O fim do dia e `< plan_date + 1 dia` (DATETIME sem fracao): igual a
    `<= 23:59:59` e sem literal com ':' (o shim da Data API trataria como
    parametro nomeado).

    M76 Lote 2b: `plan_date` e o dia do USUARIO e `effective_from`/
    `superseded_at` sao instantes do relogio do banco (UTC). O fim exclusivo
    do dia vai para o relogio do banco por `time_windows.user_day_end_db_sql`
    (`plan_date + 1 dia + 3h` em Sao Paulo) - mesma regra de `_as_moment`.
    `timezone_name` default = `DEFAULT_TIMEZONE` (o SQL e montado uma vez,
    no import dos leitores)."""
    from .intelligence.dashboard.time_windows import user_day_end_db_sql, utc_offset_minutes
    from .timezones import DEFAULT_TIMEZONE

    p = plan_alias
    day_end = user_day_end_db_sql(f"{p}.plan_date", utc_offset_minutes(timezone_name or DEFAULT_TIMEZONE))
    current = (
        f"(SELECT pvc.id FROM workout_plan_template_versions pvc WHERE pvc.current_template_id = {p}.template_id)"
    )
    if not by_date:
        return current
    return (
        f"COALESCE({p}.template_version_id, "
        f"(SELECT pva.id FROM workout_plan_template_versions pva WHERE pva.template_id = {p}.template_id "
        f"AND (pva.effective_from IS NULL OR pva.effective_from < {day_end}) "
        f"AND (pva.superseded_at IS NULL OR pva.superseded_at >= {day_end}) "
        f"ORDER BY pva.version_number DESC LIMIT 1), "
        f"{current})"
    )


def version_at_condition(alias: str) -> str:
    """Condicao "a versao `alias` vigia NAQUELE instante" (dois `%s`, o mesmo
    momento duas vezes) - para JOIN no statement existente. Intervalos de
    versoes do mesmo Modelo nao se sobrepoem, entao casa no maximo uma."""
    return (
        f"({alias}.effective_from IS NULL OR {alias}.effective_from <= %s) "
        f"AND ({alias}.superseded_at IS NULL OR {alias}.superseded_at > %s)"
    )


def current_version_id_of(template: Optional[Mapping[str, Any]]) -> Optional[int]:
    """`id` da versao vigente de um Modelo lido por `_get_template`
    (`template["current_version"]`), ou `None`. E o que o rascunho carrega
    como `template_version_id` (066): a versao que o usuario VIU na geracao."""
    ref = (template or {}).get("current_version") if isinstance(template, Mapping) else None
    if not isinstance(ref, Mapping) or ref.get("id") is None:
        return None
    return int(ref["id"])


def _query_one(sql: str, params: tuple) -> Optional[dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def current_version(cur: Any, template_id: int) -> Optional[TemplateVersion]:
    """Vigente, lida no cursor de quem chama (dentro da transacao de escrita)."""
    cur.execute(CURRENT_VERSION_SQL, (template_id,))
    return row_to_version(cur.fetchone())


def resolve_version_now(template_id: int) -> Optional[TemplateVersion]:
    """Versao VIGENTE do Modelo, ou `None` se ele nao tem nenhuma."""
    return row_to_version(_query_one(CURRENT_VERSION_SQL, (template_id,)))


def resolve_version_at(template_id: int, moment: date | datetime) -> Optional[TemplateVersion]:
    """Versao vigente NAQUELE instante. Uma DATA vira o fim do dia
    (`_as_moment`), como em M23/M12. Independe da flag."""
    at = _as_moment(moment)
    return row_to_version(_query_one(VERSION_AT_SQL, (template_id, at, at)))


def resolve_version_for_window(
    template_id: int, date_to: Optional[date | datetime]
) -> Optional[TemplateVersion]:
    """Versao que governa uma janela que TERMINA em `date_to` - a do FIM da
    janela (decisao do usuario em 2026-10-05, mesma regra das metas do M12).
    Sem `date_to`, a vigente."""
    if date_to is None:
        return resolve_version_now(template_id)
    return resolve_version_at(template_id, date_to)


# ---------------------------------------------------------------------------
# Forma canonica (o que "mudou" quer dizer)
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _norm(field: str, value: Any) -> Any:
    """Valor na ESCALA DA COLUNA. A Data API devolve inteiro como `int` ou
    `str` e DECIMAL como `str`; o pymysql, `Decimal`; o payload, `int`. Sem
    normalizar, "3" != 3 e cada PUT criaria uma versao identica."""
    if field == _MEASURE_FIELD:
        return normalize_measure(value).value
    if field == _GOAL_FIELD:
        return goal_domain.override_value(value)
    if value is None:
        return None
    if field in _BOOL_FIELDS:
        return bool(int(value)) if not isinstance(value, bool) else value
    if field in _STR_FIELDS:
        return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
    return int(round(float(value)))


def _canonical_item(item: Any) -> tuple:
    """Item na escala da coluna. O override do objetivo (093) so entra quando
    preenchido: um item sem override tem a MESMA forma de antes da 093, e um
    PUT de Modelo que nao conhece o campo nao abre versao. A prescricao de
    cardio (095) idem: a duracao vai pela normalizacao do dono e o resto so
    entra quando difere do item antigo (`cardio_prescription.canonical_extras`)."""
    cardio = cardio_prescription.normalize_fields(item)
    row = tuple(
        _norm(f, cardio[f] if f in _CARDIO_FIELDS else _get(item, f)) for f in _CANONICAL_ITEM_FIELDS
    )
    goal = _norm(_GOAL_FIELD, _get(item, _GOAL_FIELD))
    if goal is not None:
        row += ((_GOAL_FIELD, goal),)
    extras = cardio_prescription.canonical_extras(item)
    return row + (("cardio_prescription", extras),) if extras else row


def _canonical_items(items: Iterable[Any]) -> tuple:
    rows = [_canonical_item(item) for item in items or []]
    return tuple(sorted(rows, key=lambda r: (r[0] if r[0] is not None else -1, repr(r))))


def canonical_slots(slots: Iterable[Any]) -> tuple:
    """Slots + itens sem ids, ordenados por `sequence`/`item_order`."""
    rows = []
    for slot in slots or []:
        head = tuple(_norm(f, _get(slot, f)) for f in SLOT_FIELDS)
        rows.append(head + (_canonical_items(_get(slot, "items") or []),))
    return tuple(sorted(rows, key=lambda r: (r[0] if r[0] is not None else -1, repr(r))))


def canonical_header(header: Any) -> tuple:
    """Cabecalho ESTRUTURAL. O modo de contagem do volume (073) entra: trocar o
    modo muda a regua do Modelo e abre versao nova com `effective_from`.

    O objetivo de treino (093) tambem e estrutural, mas so entra quando difere
    do default do dono: o Modelo de hipertrofia (todos os de antes da 093) tem
    a MESMA forma canonica de antes, e nenhuma versao espuria nasce."""
    base = (
        _norm("training_type_id", _get(header, "training_type_id")),
        str(_get(header, "volume_mode") or "percent_budget"),
        str(_get(header, "template_type") or "full_body"),
        _norm("grid_rows", _get(header, "grid_rows")),
        _norm("grid_cols", _get(header, "grid_cols")),
        counting_mode_of(header),
    )
    goal = training_goal_of(header)
    return base if goal_domain.is_default(goal) else base + ((_GOAL_FIELD, goal),)


def training_goal_of(header: Any) -> str:
    """Objetivo de um cabecalho (payload, linha ou `TemplateVersion`) pela
    leitura tolerante do dono: ausente/NULL = `DEFAULT_GOAL`."""
    return goal_domain.goal_value(_get(header, _GOAL_FIELD))


def canonical_intent(intent: Optional[Mapping[str, Any]]) -> tuple:
    intent = intent or {}
    return (str(intent.get("intent") or "standard"), _target_ids(intent.get("target_group_ids") or []))


def _load_version_tree_rows(cur: Any, version_id: int) -> list[dict[str, Any]]:
    """Arvore de uma versao (dois statements, sem N+1; um so quando a versao
    nao tem slot)."""
    cur.execute(VERSION_SLOTS_SQL, (version_id,))
    slots = [dict(row) for row in (cur.fetchall() or [])]
    if not slots:
        return slots
    cur.execute(VERSION_ITEMS_SQL, (version_id,))
    by_slot: dict[int, list[dict[str, Any]]] = {}
    for row in cur.fetchall() or []:
        by_slot.setdefault(int(row["slot_id"]), []).append(row)
    for slot in slots:
        slot["items"] = by_slot.get(int(slot["id"]), [])
    return slots


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


class TemplateVersionConflict(Exception):
    """Outra escrita encerrou a versao vigente entre a leitura e o UPDATE.
    Nada foi gravado por esta (a transacao de quem chama desfaz o resto)."""


# Reversao (PR4): a versao n+1 nasce como COPIA da versao k - cabecalho e
# intencao por `INSERT ... SELECT` (aqui) e a arvore por `COPY_SLOTS_SQL` /
# `COPY_ITEMS_SQL`. Nada da arvore sobe para o Python.
REVERT_INSERT_SQL = f"""
INSERT INTO {TABLE} (
    template_id, version_number, training_type_id, volume_mode, template_type,
    grid_rows, grid_cols, methodology_intent, methodology_target_group_ids,
    effective_from, created_by_user_id, reverted_from_version_id, change_summary,
    volume_counting_mode, training_goal
)
SELECT template_id, %s, training_type_id, volume_mode, template_type,
       grid_rows, grid_cols, methodology_intent, methodology_target_group_ids,
       %s, %s, id, %s, volume_counting_mode, training_goal
FROM {TABLE}
WHERE id = %s AND template_id = %s
"""

VERSION_BY_ID_SQL = f"SELECT {_VERSION_COLUMNS} FROM {TABLE} v WHERE v.id = %s AND v.template_id = %s"


def version_of_template(cur: Any, template_id: int, version_id: int) -> Optional[TemplateVersion]:
    """Versao `version_id` SE for do Modelo `template_id` (senao `None`)."""
    cur.execute(VERSION_BY_ID_SQL, (version_id, template_id))
    return row_to_version(cur.fetchone())


def load_version_tree(cur: Any, version_id: int) -> list[dict[str, Any]]:
    """Slots (com `items`) de uma versao, ordenados por `sequence` /
    `item_order` - a mesma forma de `_get_template`. Dois statements."""
    return _load_version_tree_rows(cur, version_id)


def revert_to_version(cur: Any, template_id: int, source: TemplateVersion, *, actor: Optional[int]) -> int:
    """Grava a versao n+1 do Modelo como copia da versao `source`, com
    `reverted_from_version_id = source.id`. Mesmas garantias do escritor: o
    MESMO `db_now` fecha a vigente e abre a nova; vigente ja encerrada por
    outra escrita -> `TemplateVersionConflict`. Devolve o id da nova versao.

    Quem chama decide SE pode reverter (vigente, posse, seguranca)."""
    vigente = current_version(cur, template_id)
    if vigente is None:
        raise TemplateVersionConflict(f"modelo {template_id} sem versao vigente")
    moment = db_now(cur)
    cur.execute(SUPERSEDE_SQL, (moment, vigente.id))
    if not int(cur.rowcount or 0):
        raise TemplateVersionConflict(f"versao {vigente.id} do modelo {template_id} ja foi encerrada")
    cur.execute(
        REVERT_INSERT_SQL,
        (vigente.number + 1, moment, actor, f"Revertido para a v{source.number}", source.id, template_id),
    )
    version_id = int(cur.lastrowid)
    cur.execute(COPY_SLOTS_SQL, (version_id, source.id))
    cur.execute(COPY_ITEMS_SQL, (version_id, source.id))
    return version_id


def _item_column_value(item: Any, field: str, cardio: Optional[Mapping[str, Any]] = None) -> Any:
    """Valor do item para o INSERT: o do payload, salvo `measure`, que vai pela
    leitura do dono (None/ausente = `reps`; a coluna e NOT NULL), e os campos
    da prescricao de cardio, pela normalizacao do dono (`cardio`)."""
    if field in _CARDIO_FIELDS:
        return (cardio if cardio is not None else cardio_prescription.normalize_fields(item))[field]
    value = _get(item, field)
    if field == _MEASURE_FIELD:
        return normalize_measure(value).value
    if field == _GOAL_FIELD:
        return goal_domain.override_value(value)
    return value


def insert_version_slots(cur: Any, template_id: int, version_id: int, slots: Sequence[Any]) -> None:
    """Grava os slots e itens de UMA versao. So INSERT: as linhas de versoes
    anteriores ficam como estao."""
    for slot in slots:
        cur.execute(
            INSERT_SLOT_SQL,
            (template_id, version_id) + tuple(_get(slot, f) for f in SLOT_FIELDS),
        )
        slot_id = cur.lastrowid
        for item in _get(slot, "items") or []:
            cardio = cardio_prescription.normalize_fields(item)
            cur.execute(
                INSERT_ITEM_SQL, (slot_id,) + tuple(_item_column_value(item, f, cardio) for f in ITEM_FIELDS)
            )


def write_structural_version(
    cur: Any,
    template_id: int,
    payload: Any,
    intent: Optional[Mapping[str, Any]],
    *,
    actor: Optional[int],
    reverted_from: Optional[int] = None,
    change_summary: Optional[str] = None,
) -> Optional[int]:
    """Grava a versao n+1 do Modelo SE a estrutura mudou; devolve o `id` dela,
    ou `None` quando nada estrutural mudou (nada e gravado).

    - `payload`: cabecalho estrutural (`training_type_id`, `volume_mode`,
      `template_type` JA RESOLVIDO, `grid_rows`, `grid_cols`,
      `volume_counting_mode` - ausente = default) + `slots`.
      `None` = estrutura igual a da vigente (versao so de intencao): slots e
      itens sao copiados da vigente por `INSERT ... SELECT`.
    - `intent`: `{"intent", "target_group_ids"}`; `None` = mantem a da vigente.

    Recebe o CURSOR (nao abre transacao): encerrar a vigente e abrir a nova
    precisam ser atomicos, e o MESMO `db_now` fecha uma e abre a outra - sem
    buraco nem sobreposicao no intervalo semiaberto. Modelo sem versao
    nenhuma (recem-criado) ganha a v1 com `effective_from` = agora.
    """
    vigente = current_version(cur, template_id)
    if vigente is None and payload is None:
        raise ValueError("Modelo sem versao vigente exige a estrutura completa (payload).")

    novo_intent = canonical_intent(intent) if intent is not None else (
        canonical_intent(vigente.intent()) if vigente is not None else canonical_intent(None)
    )

    if vigente is not None:
        mesma_intencao = novo_intent == canonical_intent(vigente.intent())
        if payload is None:
            if mesma_intencao:
                return None
        else:
            mesmo_cabecalho = canonical_header(payload) == canonical_header(vigente)
            if mesmo_cabecalho and mesma_intencao:
                atuais = _load_version_tree_rows(cur, vigente.id)
                if canonical_slots(_get(payload, "slots") or []) == canonical_slots(atuais):
                    return None

    header = vigente if payload is None else payload
    moment = db_now(cur)
    if vigente is not None:
        cur.execute(SUPERSEDE_SQL, (moment, vigente.id))
        if not int(cur.rowcount or 0):
            raise TemplateVersionConflict(f"versao {vigente.id} do modelo {template_id} ja foi encerrada")
        numero = vigente.number + 1
    else:
        cur.execute(MAX_VERSION_NUMBER_SQL, (template_id,))
        row = cur.fetchone() or {}
        numero = int(row.get("n") or 0) + 1

    cur.execute(
        INSERT_VERSION_SQL,
        (
            template_id,
            numero,
            _get(header, "training_type_id"),
            _get(header, "volume_mode") or "percent_budget",
            _get(header, "template_type") or "full_body",
            _get(header, "grid_rows"),
            _get(header, "grid_cols"),
            novo_intent[0],
            json.dumps(list(novo_intent[1])),
            moment,
            actor,
            reverted_from,
            change_summary,
            counting_mode_of(header),
            training_goal_of(header),
        ),
    )
    version_id = int(cur.lastrowid)

    if payload is None:
        cur.execute(COPY_SLOTS_SQL, (version_id, vigente.id))
        cur.execute(COPY_ITEMS_SQL, (version_id, vigente.id))
    else:
        insert_version_slots(cur, template_id, version_id, _get(payload, "slots") or [])
    return version_id
