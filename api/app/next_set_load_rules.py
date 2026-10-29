"""Conjunto de regras de carga da próxima série GRAVADO (regras de carga, PR R2).

Adaptador de I/O da tabela `next_set_load_rule_sets` (migração 088) para o
dono `domain/next_set_load.py`. Nenhuma regra aqui: a FORMA do JSON é
validada pelos modelos Pydantic abaixo (`extra="forbid"`, vocabulário pelos
enums do dono) e a SEMÂNTICA (limites, posições, magnitude, tokens) só por
`next_set_load.validate_ruleset`/`ruleset_errors`. Este módulo converte
(Pydantic <-> dataclasses do dono <-> linha) e grava.

- `load_published_ruleset(conn)` / `load_published_ruleset_autocommit()`: o
  conjunto VIGENTE (`current_published = 1`) para a avaliação de uma série;
  sem linha, tabela ausente, falha de leitura ou conjunto gravado que o dono
  recusa -> `DEFAULT_RULESET` (logado), nunca exceção. Um SELECT por
  avaliação, sem cache (Lambdas multi-instância: uma publicação vale na
  próxima série de qualquer instância).
- o chamador só lê a faixa de fadiga prévia quando alguma regra a exige
  (`next_set_load.ruleset_uses_fatigue`, do dono).
- `save_draft`, `publish`, `list_versions`, `load_draft`: a rota de cadastro
  (`next_set_load_rules_admin.py`). Publicar encerra o vigente e promove o
  rascunho NA MESMA TRANSAÇÃO; concorrência (versão esperada diferente,
  UNIQUE do vigente/rascunho/versão) -> `RuleSetConflictError`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from .db import fetch_rows, is_duplicate_key_error
from .domain import next_set_load as nsl
from .domain.next_set_load import DEFAULT_RULESET, RuleSet
from .domain.prior_fatigue import PriorFatigueBand
from .set_types import WORKING

logger = logging.getLogger("fitness.next_set_load_rules")

TABLE = "next_set_load_rule_sets"
STATUS_DRAFT = nsl.RULESET_SOURCE_DRAFT
STATUS_PUBLISHED = nsl.RULESET_SOURCE_PUBLISHED
NOTES_MAX_LENGTH = 1000  # VARCHAR(1000) da 088
_TOKEN_MAX_LENGTH = 16
_ACTION_MAX_LENGTH = 32

_COLUMNS = (
    "id, version_number, status, reps_floor, reps_ceiling, rules_json, notes, engine_version, "
    "created_by, published_by, published_at, superseded_at, created_at, updated_at"
)
_PUBLISHED_SQL = f"SELECT {_COLUMNS} FROM {TABLE} WHERE current_published = 1"
_DRAFT_SQL = f"SELECT {_COLUMNS} FROM {TABLE} WHERE current_draft = 1"
_BY_ID_SQL = f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s"
_VERSIONS_SQL = (
    f"SELECT {_COLUMNS} FROM {TABLE} "
    "ORDER BY (version_number IS NULL) DESC, version_number DESC, id DESC"
)
_CURRENT_PUBLISHED_FOR_UPDATE_SQL = f"SELECT id, version_number FROM {TABLE} WHERE current_published = 1 FOR UPDATE"
_DRAFT_BY_ID_FOR_UPDATE_SQL = f"SELECT {_COLUMNS} FROM {TABLE} WHERE id = %s AND status = %s FOR UPDATE"
_CURRENT_DRAFT_FOR_UPDATE_SQL = f"SELECT id FROM {TABLE} WHERE current_draft = 1 FOR UPDATE"
_NEXT_VERSION_SQL = f"SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version FROM {TABLE}"
_INSERT_DRAFT_SQL = (
    f"INSERT INTO {TABLE} (status, reps_floor, reps_ceiling, rules_json, notes, engine_version, created_by) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
_UPDATE_DRAFT_SQL = (
    f"UPDATE {TABLE} SET reps_floor = %s, reps_ceiling = %s, rules_json = %s, notes = %s, engine_version = %s "
    "WHERE id = %s AND status = %s"
)
_SUPERSEDE_SQL = f"UPDATE {TABLE} SET superseded_at = CURRENT_TIMESTAMP WHERE current_published = 1 AND id <> %s"
_PUBLISH_SQL = (
    f"UPDATE {TABLE} SET status = %s, version_number = %s, published_at = CURRENT_TIMESTAMP, published_by = %s, "
    "engine_version = %s WHERE id = %s AND status = %s"
)


# ---------------------------------------------------------------------------
# Forma (Pydantic) - vocabulário pelos enums do dono; semântica no dono
# ---------------------------------------------------------------------------

NumberOrTokenIn = Union[StrictInt, StrictFloat, StrictStr]


class ConditionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: nsl.ConditionOp
    value: NumberOrTokenIn
    value_to: Optional[NumberOrTokenIn] = None


class MagnitudeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: nsl.MagnitudeKind
    value: Union[StrictInt, StrictFloat]


class RuleIn(BaseModel):
    """Uma regra. `position` e `text` são aceitos e IGNORADOS (a ordem é a da
    lista; o texto é sempre gerado pelo dono), para a tela poder devolver o que
    leu. `reason_code` não é aceito: regra cadastrada usa o motivo genérico."""

    model_config = ConfigDict(extra="forbid")

    position: Optional[StrictInt] = None
    text: Optional[StrictStr] = None
    set_position: nsl.SetPosition = nsl.SetPosition.ANY
    fatigue: nsl.FatigueCondition = nsl.FatigueCondition.ANY
    reps: Optional[ConditionIn] = None
    rpe: Optional[ConditionIn] = None
    respect_planned_ramp: StrictBool = False
    action: StrictStr = Field(max_length=_ACTION_MAX_LENGTH)
    magnitude: Optional[MagnitudeIn] = None


class RuleSetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reps_floor: StrictInt
    reps_ceiling: StrictInt
    rules: list[RuleIn]


class DraftIn(RuleSetIn):
    notes: Optional[StrictStr] = Field(default=None, max_length=NOTES_MAX_LENGTH)


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: StrictInt
    # Versão vigente que a tela viu (0 = o padrão em código, nada publicado).
    expected_published_version: StrictInt = Field(ge=0)


class SetFactsIn(BaseModel):
    """Fatos de uma série para `/simulate` (os de `next_set_load.SetFacts`)."""

    model_config = ConfigDict(extra="forbid")

    set_type: StrictStr = WORKING
    working_set_index: Optional[StrictInt] = None
    fatigue_band: Optional[PriorFatigueBand] = None
    reps: Optional[Union[StrictInt, StrictFloat]] = None
    rpe: Optional[Union[StrictInt, StrictFloat]] = None
    rpe_ceiling: Optional[Union[StrictInt, StrictFloat]] = None
    planned_rpe: Optional[Union[StrictInt, StrictFloat]] = None
    weight_kg: Optional[Union[StrictInt, StrictFloat]] = None
    step_kg: Optional[Union[StrictInt, StrictFloat]] = None
    load_convention: Optional[StrictStr] = None
    joint_pain: Optional[StrictBool] = None
    muscle_pain: Optional[Union[StrictInt, StrictFloat]] = None

    def to_facts(self) -> nsl.SetFacts:
        return nsl.SetFacts(**self.model_dump())


def ruleset_from_input(
    payload: RuleSetIn, *, version: int = 0, source: str = nsl.RULESET_SOURCE_DRAFT
) -> RuleSet:
    """Pydantic -> `RuleSet` do dono (ordem da lista = posição 1..n). Valor que
    o dono não converte (texto que não é token nem número) -> `ValueError`."""
    data = payload.model_dump(mode="json", include={"reps_floor", "reps_ceiling", "rules"})
    for rule in data["rules"]:
        rule.pop("position", None)
        rule.pop("text", None)
    return nsl.ruleset_from_dict(data, version=version, source=source)


def ruleset_payload(ruleset: RuleSet) -> dict[str, Any]:
    """`ruleset_to_dict` do dono + `text` de cada regra (`describe_rule`)."""
    data = nsl.ruleset_to_dict(ruleset)
    for rule, item in zip(ruleset.rules, data["rules"]):
        item["text"] = nsl.describe_rule(rule, ruleset)
    return data


# ---------------------------------------------------------------------------
# Linha <-> conjunto
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredRuleSet:
    id: int
    status: str
    version_number: Optional[int]
    ruleset: RuleSet
    notes: Optional[str]
    engine_version: Optional[str]
    created_by: Optional[int]
    published_by: Optional[int]
    published_at: Any
    superseded_at: Any
    created_at: Any
    updated_at: Any

    @property
    def is_current(self) -> bool:
        return self.status == STATUS_PUBLISHED and self.superseded_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "version": self.version_number,
            "current": self.is_current,
            "notes": self.notes,
            "engine_version": self.engine_version,
            "created_by": self.created_by,
            "published_by": self.published_by,
            "published_at": iso_value(self.published_at),
            "superseded_at": iso_value(self.superseded_at),
            "created_at": iso_value(self.created_at),
            "updated_at": iso_value(self.updated_at),
            "ruleset": ruleset_payload(self.ruleset),
            "summary": nsl.describe_ruleset(self.ruleset),
        }


def iso_value(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _optional_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _rules_json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value) if isinstance(value, str) else value


def stored_from_row(row: dict[str, Any]) -> StoredRuleSet:
    """Linha da 088 -> `StoredRuleSet` (conversão pelo dono; JSON que o dono
    não lê -> `ValueError`)."""
    status = str(row["status"])
    version_number = _optional_int(row.get("version_number"))
    ruleset = nsl.ruleset_from_dict(
        {"reps_floor": row["reps_floor"], "reps_ceiling": row["reps_ceiling"], "rules": _rules_json(row["rules_json"])},
        version=version_number or 0,
        source=status,
    )
    return StoredRuleSet(
        id=int(row["id"]),
        status=status,
        version_number=version_number,
        ruleset=ruleset,
        notes=row.get("notes"),
        engine_version=row.get("engine_version"),
        created_by=_optional_int(row.get("created_by")),
        published_by=_optional_int(row.get("published_by")),
        published_at=row.get("published_at"),
        superseded_at=row.get("superseded_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _rules_param(ruleset: RuleSet) -> list[dict[str, Any]]:
    # Lista -> parâmetro JSON nos dois backends (`json_bind_value`).
    return [nsl.rule_to_dict(rule) for rule in ruleset.rules]


# ---------------------------------------------------------------------------
# Leitura do vigente (avaliação de série): nunca levanta
# ---------------------------------------------------------------------------


def _resolve_published(read_rows: Callable[[], Sequence[dict[str, Any]]]) -> tuple[RuleSet, Optional[StoredRuleSet]]:
    try:
        rows = list(read_rows() or [])
    except Exception as exc:  # noqa: BLE001 - tabela ausente/banco fora: vale o padrão
        # Warning sem stack: roda a cada série de trabalho; a 088 ausente não
        # pode inundar o log (revisão de impacto do R2).
        logger.warning(
            "next_set_load_rules.published_read_failed",
            extra={"error_type": type(exc).__name__, "error": str(exc)[:300]},
        )
        return DEFAULT_RULESET, None
    if not rows:
        logger.debug("next_set_load_rules.no_published_ruleset")
        return DEFAULT_RULESET, None
    try:
        stored = stored_from_row(rows[0])
    except Exception:  # noqa: BLE001 - JSON gravado ilegível: vale o padrão
        logger.exception("next_set_load_rules.published_unreadable")
        return DEFAULT_RULESET, None
    errors = nsl.ruleset_errors(stored.ruleset)
    if errors:
        logger.warning(
            "next_set_load_rules.published_invalid",
            extra={"ruleset_id": stored.id, "version": stored.version_number, "codes": errors},
        )
        return DEFAULT_RULESET, None
    return stored.ruleset, stored


def _cursor_rows(conn: Any, sql: str, params: Optional[Sequence[Any]] = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall() or [])


def resolve_published(conn: Any) -> tuple[RuleSet, Optional[StoredRuleSet]]:
    """(conjunto em vigor, linha publicada ou None = padrão em código)."""
    return _resolve_published(lambda: _cursor_rows(conn, _PUBLISHED_SQL))


def resolve_published_autocommit() -> tuple[RuleSet, Optional[StoredRuleSet]]:
    """Igual a `resolve_published`, numa leitura de um statement fora de
    transação (`fetch_rows`: 1 chamada à Data API)."""
    return _resolve_published(lambda: fetch_rows(_PUBLISHED_SQL))


def load_published_ruleset(conn: Any) -> RuleSet:
    """Conjunto vigente na conexão do chamador (salvamento da série)."""
    return resolve_published(conn)[0]


def load_published_ruleset_autocommit() -> RuleSet:
    """Conjunto vigente sem conexão aberta (IA por série)."""
    return resolve_published_autocommit()[0]


# ---------------------------------------------------------------------------
# Cadastro (rotas de admin): levanta erros de domínio
# ---------------------------------------------------------------------------


class RuleSetConflictError(Exception):
    def __init__(self, code: str, message: str, *, current_version: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_version = current_version


class RuleSetNotFoundError(Exception):
    pass


class RuleSetInvalidError(Exception):
    def __init__(self, codes: list[str]):
        super().__init__(", ".join(codes))
        self.codes = codes


def _fetch_stored(cur: Any, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[StoredRuleSet]:
    cur.execute(sql, params)
    row = cur.fetchone()
    return stored_from_row(row) if row else None


def load_draft(conn: Any) -> Optional[StoredRuleSet]:
    with conn.cursor() as cur:
        return _fetch_stored(cur, _DRAFT_SQL)


def list_versions(conn: Any) -> list[StoredRuleSet]:
    """Rascunho primeiro, depois as versões publicadas da mais nova à mais velha."""
    return [stored_from_row(row) for row in _cursor_rows(conn, _VERSIONS_SQL)]


def save_draft(conn: Any, ruleset: RuleSet, *, notes: Optional[str], user_id: Optional[int]) -> StoredRuleSet:
    """Grava o rascunho ÚNICO (cria ou substitui). O chamador já validou com o
    dono. Corrida com outro salvamento (UNIQUE do rascunho) -> conflito."""
    rules = _rules_param(ruleset)
    with conn.cursor() as cur:
        cur.execute(_CURRENT_DRAFT_FOR_UPDATE_SQL)
        current = cur.fetchone()
        try:
            if current:
                draft_id = int(current["id"])
                cur.execute(
                    _UPDATE_DRAFT_SQL,
                    (ruleset.reps_floor, ruleset.reps_ceiling, rules, notes, nsl.RULESET_ENGINE_VERSION,
                     draft_id, STATUS_DRAFT),
                )
                if int(cur.rowcount or 0) == 0:
                    raise RuleSetConflictError("draft_changed", "O rascunho mudou durante a edição; recarregue.")
            else:
                cur.execute(
                    _INSERT_DRAFT_SQL,
                    (STATUS_DRAFT, ruleset.reps_floor, ruleset.reps_ceiling, rules, notes,
                     nsl.RULESET_ENGINE_VERSION, user_id),
                )
                draft_id = int(cur.lastrowid)
        except RuleSetConflictError:
            raise
        except Exception as exc:  # noqa: BLE001 - corrida no UNIQUE do rascunho vira conflito
            if is_duplicate_key_error(exc):
                raise RuleSetConflictError("draft_conflict", "Outro rascunho foi salvo agora; recarregue.") from exc
            raise
        stored = _fetch_stored(cur, _BY_ID_SQL, (draft_id,))
    if stored is None:
        raise RuleSetNotFoundError(draft_id)
    return stored


def publish(conn: Any, draft_id: int, expected_published_version: int, user_id: Optional[int]) -> StoredRuleSet:
    """Rascunho -> versão publicada `max + 1`, encerrando a vigente, na mesma
    transação do chamador. A vigente é travada (`FOR UPDATE`) e comparada com
    `expected_published_version` (0 = nada publicado); divergência ou corrida
    num UNIQUE -> `RuleSetConflictError`. Rascunho que o dono recusa (limites
    mudaram desde a gravação) -> `RuleSetInvalidError`."""
    with conn.cursor() as cur:
        cur.execute(_CURRENT_PUBLISHED_FOR_UPDATE_SQL)
        current = cur.fetchone()
        current_version = int(current["version_number"] or 0) if current else 0
        if current_version != expected_published_version:
            raise RuleSetConflictError(
                "publish_conflict",
                "A versão vigente mudou desde que a tela carregou; recarregue antes de publicar.",
                current_version=current_version,
            )
        draft = _fetch_stored(cur, _DRAFT_BY_ID_FOR_UPDATE_SQL, (draft_id, STATUS_DRAFT))
        if draft is None:
            raise RuleSetNotFoundError(draft_id)
        errors = nsl.ruleset_errors(draft.ruleset)
        if errors:
            raise RuleSetInvalidError(errors)
        cur.execute(_NEXT_VERSION_SQL)
        next_version = int((cur.fetchone() or {}).get("next_version") or 1)
        try:
            cur.execute(_SUPERSEDE_SQL, (draft_id,))
            cur.execute(
                _PUBLISH_SQL,
                (STATUS_PUBLISHED, next_version, user_id, nsl.RULESET_ENGINE_VERSION, draft_id, STATUS_DRAFT),
            )
        except Exception as exc:  # noqa: BLE001 - corrida no UNIQUE da vigente/versão vira 409, não 500
            if is_duplicate_key_error(exc):
                raise RuleSetConflictError(
                    "publish_conflict", "Outra versão foi publicada agora; recarregue.", current_version=current_version
                ) from exc
            raise
        if int(cur.rowcount or 0) == 0:
            raise RuleSetConflictError("draft_changed", "O rascunho já foi publicado ou apagado; recarregue.")
        stored = _fetch_stored(cur, _BY_ID_SQL, (draft_id,))
    if stored is None:
        raise RuleSetNotFoundError(draft_id)
    return stored
