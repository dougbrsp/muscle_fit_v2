#!/usr/bin/env python3
"""Traz os dados ATUAIS de produção (só de UM usuário) para o MySQL local do DEV.

Uso (da raiz do repositório, depois de `aws login --profile musclefit`):

    python scripts/prod_to_dev.py --user-id 1 --dry-run   # só lê produção e grava o SQL
    python scripts/prod_to_dev.py --user-id 1 --yes       # backup do DEV + carga
    python scripts/prod_to_dev.py --restore backups/dev-AAAAMMDD-HHMM.sql

O que este script garante (ver docs/local-dev.md, "Trazer dados de produção"):

- Produção é SÓ LEITURA: todo SQL enviado à RDS Data API passa por
  `assert_read_only_sql` (SELECT/SHOW apenas). O cluster e o secret são
  descobertos por `rds:DescribeDBClusters` (o secret vai por ARN para a Data API;
  o valor dele nunca é lido aqui). Nenhuma escrita, snapshot ou export na AWS.
- Destino SÓ LOCAL: `validate_destination` recusa host que não seja o MySQL do
  compose, `DB_BACKEND=dataapi`, ARNs e bancos de sistema; depois de conectar,
  recusa Aurora e confere que o servidor é o mesmo container do compose (o do
  backup).
- Backup antes: `mysqldump` do banco local em `backups/dev-AAAAMMDD-HHMM.sql`.
- Só o usuário pedido: tabelas com dono filtradas por `user_id`; catálogo global
  inteiro; Modelos de treino só os criados pelo usuário ou referenciados pelos
  dados dele; referências a OUTROS usuários viram NULL (coluna anulável) ou um
  usuário placeholder anonimizado (coluna NOT NULL).
- Limpeza de credenciais: sessões, tokens de reset e tentativas de reset ficam
  vazios; chave da Intervals zerada; hash de token de dispositivo trocado por
  valor aleatório; só o `password_hash` do usuário copiado é mantido.

Este arquivo fica fora de `api/` de propósito: não é importado pelo app e não
entra em nenhuma imagem Docker (os contextos de build são `api/` e `frontend/`).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "api" / "db" / "migrations"
BACKUPS_DIR = REPO_ROOT / "backups"

DEFAULT_PROFILE = "musclefit"
DEFAULT_REGION = "sa-east-1"  # infra/variables.tf (aws_region)
DEFAULT_CLUSTER_ID = "musclefit-db"  # infra/aurora.tf (cluster_identifier)

# Usuário que substitui, nas colunas NOT NULL, qualquer outro usuário de produção
# referenciado pelos dados copiados (ex.: quem atribuiu um Modelo). Sem nome, sem
# e-mail, inativo e com hash de senha inválido (login impossível).
PLACEHOLDER_USER_ID = 999_999_999
PLACEHOLDER_USER = {
    "id": PLACEHOLDER_USER_ID,
    "username": "usuario-anonimizado",
    "password_hash": "!",
    "full_name": "Usuário anonimizado",
    "email": None,
    "role": "regular",
    "is_active": 0,
}

# ---------------------------------------------------------------------------
# Classificação das tabelas. TODA tabela base de produção precisa estar em
# exatamente um destes grupos: tabela nova não classificada aborta o script
# (`check_classification`), para que ninguém copie dado de terceiro sem decidir.
# ---------------------------------------------------------------------------

# Catálogo / dados globais que as telas precisam: copiados inteiros.
GLOBAL_TABLES: tuple[str, ...] = (
    "advanced_techniques",
    "equipment_types",
    "equipment",
    "equipment_exercises",
    "equipment_exercise_mappings",
    "equipment_exercise_technique_overrides",
    "equipment_technique_compatibility",
    "exercise_catalog",
    "exercise_aliases",
    "exercise_card_generations",
    "exercise_external_mappings",
    "exercise_legacy_id_map",
    "exercise_mappings",
    "exercise_muscle_contributions",
    "exercise_zepp_mappings",
    "exercise_zepp_mappings_retirement_audit",
    "gyms",
    "gym_equipment",
    "gym_equipment_catalog",
    "muscle_groups",
    "muscle_group_body_map",
    "muscle_volume_classes",
    "muscle_volume_class_ranges",
    "sources",
    "training_types",
    "training_type_muscle_group_targets",
    "weekly_summaries",  # legado sem dono (sem user_id)
)

# Tabelas com dono: só as linhas `user_id = <usuário>`.
USER_TABLES: tuple[str, ...] = (
    "activities",
    "activity_intervals_link",
    "body_composition",
    "body_measurements",
    "cardio_recommendations",
    "context_events",
    "daily_biometrics",
    "daily_checkins",
    "dashboard_alert_states",
    "devices",
    "generation_runs",
    "intervals_activities",
    "intervals_wellness",
    "manual_effort_metrics",
    "muscle_recovery_coefficients",
    "progression_decisions",
    "progression_group_states",
    "progression_tracks",
    "reports",
    "sleep_metrics",
    "template_generation_logs",
    "user_equipment_exercise_preferences",
    "workout_plans",
    "workout_plan_template_assignments",
)


@dataclass(frozen=True)
class ChildSpec:
    """Tabela sem `user_id` cujas linhas pertencem a um pai já selecionado."""

    table: str
    column: str
    parent_table: str
    parent_column: str = "id"


# Filhos das tabelas do usuário, em ordem de dependência.
USER_CHILD_TABLES: tuple[ChildSpec, ...] = (
    ChildSpec("activity_hr_zones", "activity_id", "activities"),
    ChildSpec("exercises", "activity_id", "activities"),
    ChildSpec("exercise_sets", "exercise_id", "exercises"),
    ChildSpec("workout_plan_exercises", "plan_id", "workout_plans"),
)

TEMPLATES_TABLE = "workout_plan_templates"

# Colunas (já selecionadas) que apontam para um Modelo de treino que o usuário usa.
TEMPLATE_REFERENCES: tuple[tuple[str, str], ...] = (
    ("workout_plans", "template_id"),
    ("workout_plan_template_assignments", "template_id"),
    ("generation_runs", "template_id"),
    ("template_generation_logs", "saved_template_id"),
)

# Filhos dos Modelos selecionados, em ordem de dependência.
TEMPLATE_CHILD_TABLES: tuple[ChildSpec, ...] = (
    ChildSpec("workout_plan_template_versions", "template_id", TEMPLATES_TABLE),
    ChildSpec("workout_plan_template_slots", "template_id", TEMPLATES_TABLE),
    ChildSpec("workout_plan_template_slot_items", "slot_id", "workout_plan_template_slots"),
    ChildSpec("workout_plan_template_drafts", "template_id", TEMPLATES_TABLE),
    ChildSpec("workout_plan_template_methodology_intent", "template_id", TEMPLATES_TABLE),
    ChildSpec(
        "workout_plan_template_methodology_intent_targets",
        "template_id",
        "workout_plan_template_methodology_intent",
        "template_id",
    ),
)

# Sem dono próprio: só as linhas referenciadas pelos dados já copiados.
REFERENCED_ONLY_TABLES: tuple[ChildSpec, ...] = (
    ChildSpec("imports", "id", "activities", "import_id"),
)

# Nunca copiadas (credenciais/controle) e ESVAZIADAS no DEV, exceto o controle de
# migrações, que é do próprio banco local e fica intocado.
EXCLUDED_TABLES: dict[str, str] = {
    "user_sessions": "sessões de login (tokens) - fica vazia",
    "password_reset_tokens": "tokens de redefinição de senha - fica vazia",
    "password_reset_attempts": "rate limit de reset (hash de e-mail/IP de terceiros) - fica vazia",
}
UNTOUCHED_TABLES: dict[str, str] = {
    "schema_migration_files": "controle de migrações do banco local",
    "schema_migration_lock": "lock do runner de migrações",
    "schema_migrations": "controle legado de migrações",
}

# Referências SEM FK declarada no schema (o fechamento usa FKs reais + estas).
SOFT_REFERENCES: tuple[tuple[str, str, str, str], ...] = (
    ("workout_plans", "template_version_id", "workout_plan_template_versions", "id"),
    ("generation_runs", "template_id", TEMPLATES_TABLE, "id"),
    ("generation_runs", "template_version_id", "workout_plan_template_versions", "id"),
    (TEMPLATES_TABLE, "generation_log_id", "template_generation_logs", "id"),
    ("workout_plan_template_versions", "reverted_from_version_id", "workout_plan_template_versions", "id"),
    ("workout_plan_template_versions", "training_type_id", "training_types", "id"),
    ("workout_plan_exercises", "progression_decision_id", "progression_decisions", "id"),
    ("progression_tracks", "exercise_id", "exercise_catalog", "id"),
    ("progression_group_states", "technique_target_exercise_id", "exercise_catalog", "id"),
    ("progression_group_states", "technique_target_equipment_id", "equipment", "id"),
    ("exercise_catalog", "card_generation_id", "exercise_card_generations", "id"),
    ("exercise_card_generations", "parent_generation_id", "exercise_card_generations", "id"),
)

# Colunas que guardam id de usuário SEM FK para `users` (as com FK vêm do schema).
USER_SOFT_REFERENCES: tuple[tuple[str, str], ...] = (
    ("exercise_card_generations", "created_by"),
    ("exercise_card_generations", "approved_by"),
    ("exercise_catalog", "execution_description_reviewed_by"),
    ("workout_plan_template_assignments", "unassigned_by_user_id"),
    ("workout_plan_template_drafts", "updated_by_user_id"),
    ("workout_plan_template_versions", "created_by_user_id"),
)

# Colunas sensíveis e o tratamento de cada uma (também listado no PR/docs).
SENSITIVE_COLUMNS: dict[str, str] = {
    "users.password_hash": "mantido SÓ para o usuário copiado; placeholder recebe '!' (login impossível)",
    "users.intervals_api_key_encrypted": "NULL (chave da Intervals.icu)",
    "users.intervals_athlete_id": "mantido (identificador público do atleta, sem a chave não sincroniza)",
    "users.failed_login_attempts": "0",
    "users.locked_until": "NULL",
    "devices.token_hash": "trocado por hash aleatório (o token real do relógio não autentica no DEV)",
    "user_sessions.token_hash": "tabela não copiada e esvaziada",
    "password_reset_tokens.token_hash": "tabela não copiada e esvaziada",
    "password_reset_tokens.created_ip": "tabela não copiada e esvaziada",
    "password_reset_attempts.subject_hash": "tabela não copiada e esvaziada",
}

SYSTEM_DATABASES = frozenset({"mysql", "information_schema", "performance_schema", "sys"})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "mysql"})


class AbortError(RuntimeError):
    """Condição que impede a cópia com segurança (mensagem já é para o usuário)."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass
class Column:
    name: str
    nullable: bool
    generated: bool = False


@dataclass
class TableSchema:
    name: str
    columns: list[Column]
    primary_key: list[str]

    @property
    def insertable_columns(self) -> list[str]:
        return [c.name for c in self.columns if not c.generated]

    def is_nullable(self, column: str) -> bool:
        for col in self.columns:
            if col.name == column:
                return col.nullable
        raise KeyError(f"{self.name}.{column} não existe no schema de produção")


@dataclass
class Schema:
    tables: dict[str, TableSchema]
    # (tabela, coluna, tabela_referenciada, coluna_referenciada)
    foreign_keys: list[tuple[str, str, str, str]] = field(default_factory=list)

    def has_column(self, table: str, column: str) -> bool:
        return table in self.tables and any(c.name == column for c in self.tables[table].columns)

    def user_reference_columns(self) -> list[tuple[str, str]]:
        refs = {(t, c) for t, c, rt, _ in self.foreign_keys if rt == "users"}
        refs.update((t, c) for t, c in USER_SOFT_REFERENCES if self.has_column(t, c))
        return sorted(refs)

    def row_references(self) -> list[tuple[str, str, str, str]]:
        refs = [fk for fk in self.foreign_keys if fk[2] != "users"]
        # Tabela referenciada ausente = nenhuma chave válida: a referência é anulada/derrubada.
        refs.extend(r for r in SOFT_REFERENCES if self.has_column(r[0], r[1]))
        return sorted(set(refs))


def classified_tables() -> dict[str, str]:
    out: dict[str, str] = {}
    groups: list[tuple[str, Iterable[str]]] = [
        ("global", GLOBAL_TABLES),
        ("user", USER_TABLES + ("users",)),
        ("user_child", (c.table for c in USER_CHILD_TABLES)),
        ("template", (TEMPLATES_TABLE,)),
        ("template_child", (c.table for c in TEMPLATE_CHILD_TABLES)),
        ("referenced_only", (c.table for c in REFERENCED_ONLY_TABLES)),
        ("excluded", EXCLUDED_TABLES),
        ("untouched", UNTOUCHED_TABLES),
    ]
    for kind, tables in groups:
        for table in tables:
            if table in out:
                raise AbortError(f"tabela {table} classificada duas vezes ({out[table]} e {kind})")
            out[table] = kind
    return out


def check_classification(prod_tables: Iterable[str]) -> None:
    known = classified_tables()
    unknown = sorted(set(prod_tables) - set(known))
    if unknown:
        raise AbortError(
            "Tabelas de produção sem classificação em scripts/prod_to_dev.py: "
            + ", ".join(unknown)
            + ". Classifique cada uma (global, do usuário, filha, excluída) antes de copiar."
        )


# ---------------------------------------------------------------------------
# Leitura de produção (RDS Data API, SÓ LEITURA)
# ---------------------------------------------------------------------------

_READ_ONLY_RE = re.compile(r"^\s*(SELECT|SHOW)\b", re.IGNORECASE)
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|RENAME|GRANT|REVOKE|LOCK|CALL|SET|LOAD|HANDLER|DO)\b",
    re.IGNORECASE,
)


def _strip_literals(sql: str) -> str:
    return re.sub(r"'(?:[^'\\]|\\.)*'", "''", sql)


def assert_read_only_sql(sql: str) -> None:
    """Toda instrução enviada à produção precisa ser um SELECT/SHOW sem escrita."""
    bare = _strip_literals(sql)
    if not _READ_ONLY_RE.match(bare) or ";" in bare.strip().rstrip(";") or _FORBIDDEN_RE.search(bare):
        raise AbortError(f"SQL recusado para produção (só SELECT/SHOW): {sql[:120]!r}")


def decode_field(fld: dict[str, Any]) -> Any:
    if fld.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "blobValue"):
        if key in fld:
            return fld[key]
    if "booleanValue" in fld:
        return 1 if fld["booleanValue"] else 0  # TINYINT(1) chega como booleanValue
    raise AbortError(f"tipo de campo não suportado na Data API: {sorted(fld)}")


def _aws_error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None) or {}
    return str((response.get("Error") or {}).get("Code") or "")


def is_response_too_large(exc: BaseException) -> bool:
    """Resposta acima do limite de ~1 MB da Data API (medido: UnsupportedResultException,
    "The result exceeds the size limit 1 MB")."""
    if _aws_error_code(exc) == "UnsupportedResultException":
        return True
    text = str(exc).lower()
    return "size limit" in text or "response size" in text


def is_database_resuming(exc: BaseException) -> bool:
    return _aws_error_code(exc) == "DatabaseResumingException" or "resuming" in str(exc).lower()


class DataApiReader:
    """Executa SELECTs na Aurora via Data API e devolve linhas como dicts."""

    def __init__(
        self,
        client: Any,
        cluster_arn: str,
        secret_arn: str,
        database: str,
        *,
        page_size: int = 1000,
        sleep: Callable[[float], None] = time.sleep,
        resume_attempts: int = 12,
    ):
        self.client = client
        self.cluster_arn = cluster_arn
        self.secret_arn = secret_arn
        self.database = database
        self.page_size = page_size
        self.sleep = sleep
        self.resume_attempts = resume_attempts
        self.statements = 0

    def query(self, sql: str) -> list[dict[str, Any]]:
        assert_read_only_sql(sql)
        for attempt in range(self.resume_attempts + 1):
            try:
                self.statements += 1
                resp = self.client.execute_statement(
                    resourceArn=self.cluster_arn,
                    secretArn=self.secret_arn,
                    database=self.database,
                    sql=sql,
                    includeResultMetadata=True,
                )
                break
            except Exception as exc:  # noqa: BLE001 - classificado abaixo
                if is_database_resuming(exc) and attempt < self.resume_attempts:
                    print("  Aurora retomando do auto-pause; aguardando 5s...", file=sys.stderr)
                    self.sleep(5)
                    continue
                raise
        names = [m.get("label") or m.get("name") for m in resp.get("columnMetadata", [])]
        return [
            {name: decode_field(fld) for name, fld in zip(names, record)}
            for record in resp.get("records", [])
        ]

    def fetch(self, table: TableSchema, column: Optional[str] = None, values: Optional[Iterable[Any]] = None) -> list[dict[str, Any]]:
        """Lê a tabela inteira (column=None) ou só `column IN values`, paginando pela PK."""
        if column is None:
            return self._paged(table, "")
        ids = sorted({v for v in (values or []) if v is not None}, key=_sort_key)
        rows: list[dict[str, Any]] = []
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start : chunk_start + 500]
            cond = f"{_q(column)} IN ({', '.join(sql_literal(v) for v in chunk)})"
            rows.extend(self._paged(table, cond))
        return rows

    def _paged(self, table: TableSchema, condition: str) -> list[dict[str, Any]]:
        if not table.primary_key:
            raise AbortError(f"{table.name} não tem chave primária: não há como paginar")
        cols = ", ".join(_q(c) for c in table.insertable_columns)
        order = ", ".join(_q(c) for c in table.primary_key)
        keyset = len(table.primary_key) == 1
        pk = table.primary_key[0]
        rows: list[dict[str, Any]] = []
        last: Any = None
        offset = 0
        page = self.page_size
        while True:
            conds = [condition] if condition else []
            if keyset and last is not None:
                conds.append(f"{_q(pk)} > {sql_literal(last)}")
            where = f" WHERE {' AND '.join(conds)}" if conds else ""
            tail = f" LIMIT {page}" if keyset else f" LIMIT {page} OFFSET {offset}"
            sql = f"SELECT {cols} FROM {_q(table.name)}{where} ORDER BY {order}{tail}"
            try:
                batch = self.query(sql)
            except Exception as exc:  # noqa: BLE001
                if is_response_too_large(exc) and page > 1:
                    page = max(1, page // 2)  # limite de ~1 MB por resposta da Data API
                    continue
                raise
            rows.extend(batch)
            if len(batch) < page:
                return rows
            last = batch[-1][pk]
            offset += len(batch)


def _sort_key(value: Any) -> tuple[int, Any]:
    return (0, value) if isinstance(value, (int, float, Decimal)) else (1, str(value))


def _q(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise AbortError(f"identificador inválido: {identifier!r}")
    return f"`{identifier}`"


def _text(value: Any) -> Any:
    return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else value


def load_schema(reader: Any) -> Schema:
    tables = {
        _text(r["TABLE_NAME"])
        for r in reader.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
        )
    }
    columns: dict[str, list[Column]] = {t: [] for t in tables}
    for r in reader.query(
        "SELECT TABLE_NAME, COLUMN_NAME, IS_NULLABLE, EXTRA FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"
    ):
        t = _text(r["TABLE_NAME"])
        if t in columns:
            extra = str(_text(r["EXTRA"]) or "").upper()
            generated = "VIRTUAL GENERATED" in extra or "STORED GENERATED" in extra
            columns[t].append(Column(_text(r["COLUMN_NAME"]), _text(r["IS_NULLABLE"]) == "YES", generated))
    pks: dict[str, list[str]] = {t: [] for t in tables}
    for r in reader.query(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND CONSTRAINT_NAME = 'PRIMARY' "
        "ORDER BY TABLE_NAME, ORDINAL_POSITION"
    ):
        t = _text(r["TABLE_NAME"])
        if t in pks:
            pks[t].append(_text(r["COLUMN_NAME"]))
    fks = [
        (_text(r["TABLE_NAME"]), _text(r["COLUMN_NAME"]), _text(r["REFERENCED_TABLE_NAME"]), _text(r["REFERENCED_COLUMN_NAME"]))
        for r in reader.query(
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL"
        )
    ]
    return Schema({t: TableSchema(t, columns[t], pks[t]) for t in sorted(tables)}, fks)


# ---------------------------------------------------------------------------
# Seleção, fechamento de FKs e limpeza
# ---------------------------------------------------------------------------


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=dict)
    nulled: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    user_refs_nulled: dict[str, int] = field(default_factory=dict)
    user_refs_placeholder: dict[str, int] = field(default_factory=dict)
    cleaned: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def bump(bucket: dict[str, int], key: str, n: int = 1) -> None:
        bucket[key] = bucket.get(key, 0) + n


def _ids(rows: Iterable[dict[str, Any]], column: str) -> set[Any]:
    return {r[column] for r in rows if r.get(column) is not None}


def _merge_unique(table: TableSchema, *groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for rows in groups:
        for row in rows:
            seen.setdefault(tuple(row[c] for c in table.primary_key), row)
    return list(seen.values())


def build_snapshot(reader: Any, schema: Schema, user_id: int) -> dict[str, list[dict[str, Any]]]:
    """Seleciona em produção tudo o que o DEV precisa para o usuário `user_id`."""
    check_classification(schema.tables)
    tables = schema.tables
    snap: dict[str, list[dict[str, Any]]] = {}

    def present(name: str) -> bool:
        return name in tables

    for name in GLOBAL_TABLES:
        if present(name):
            snap[name] = reader.fetch(tables[name])

    snap["users"] = reader.fetch(tables["users"], "id", [user_id])
    if not snap["users"]:
        raise AbortError(f"usuário {user_id} não existe em produção")

    for name in USER_TABLES:
        if present(name):
            snap[name] = reader.fetch(tables[name], "user_id", [user_id])

    for spec in USER_CHILD_TABLES:
        if present(spec.table):
            parents = snap.get(spec.parent_table, [])
            snap[spec.table] = reader.fetch(tables[spec.table], spec.column, _ids(parents, spec.parent_column))

    templates = tables[TEMPLATES_TABLE]
    referenced: set[Any] = set()
    for t, c in TEMPLATE_REFERENCES:
        referenced |= _ids(snap.get(t, []), c)
    snap[TEMPLATES_TABLE] = _merge_unique(
        templates,
        reader.fetch(templates, "created_by_user_id", [user_id]),
        reader.fetch(templates, "id", referenced),
    )
    for spec in TEMPLATE_CHILD_TABLES:
        if present(spec.table):
            parents = snap.get(spec.parent_table, [])
            snap[spec.table] = reader.fetch(tables[spec.table], spec.column, _ids(parents, spec.parent_column))

    for spec in REFERENCED_ONLY_TABLES:
        if present(spec.table):
            refs = _ids(snap.get(spec.parent_table, []), spec.parent_column)
            snap[spec.table] = reader.fetch(tables[spec.table], spec.column, refs)

    return snap


def close_references(snap: dict[str, list[dict[str, Any]]], schema: Schema, report: Report) -> None:
    """Fechamento de FKs: referência a linha que não veio vira NULL (se a coluna
    aceita) ou derruba a linha que referencia; repete até estabilizar, porque
    derrubar uma linha pode deixar outra órfã."""
    refs = [r for r in schema.row_references() if r[0] in snap]
    changed = True
    while changed:
        changed = False
        for table, column, ref_table, ref_column in refs:
            keys = _ids(snap.get(ref_table, []), ref_column)
            nullable = schema.tables[table].is_nullable(column)
            kept: list[dict[str, Any]] = []
            for row in snap[table]:
                value = row.get(column)
                if value is None or value in keys:
                    kept.append(row)
                elif nullable:
                    row[column] = None
                    report.bump(report.nulled, f"{table}.{column}")
                    kept.append(row)
                else:
                    report.bump(report.dropped, f"{table} (órfã de {ref_table})")
                    changed = True
            snap[table] = kept


def anonymize_user_references(snap: dict[str, list[dict[str, Any]]], schema: Schema, user_id: int, report: Report) -> None:
    """Nenhum outro usuário de produção vai para o DEV: id alheio vira NULL ou o placeholder."""
    if any(row["id"] != user_id for row in snap.get("users", [])):
        raise AbortError("a seleção de users contém outro usuário além do pedido")
    used_placeholder = False
    for table, column in schema.user_reference_columns():
        if table not in snap or table == "users":
            continue
        nullable = schema.tables[table].is_nullable(column)
        for row in snap[table]:
            value = row.get(column)
            if value is None or value == user_id:
                continue
            if nullable:
                row[column] = None
                report.bump(report.user_refs_nulled, f"{table}.{column}")
            else:
                row[column] = PLACEHOLDER_USER_ID
                used_placeholder = True
                report.bump(report.user_refs_placeholder, f"{table}.{column}")
    if used_placeholder:
        columns = set(schema.tables["users"].insertable_columns)
        snap["users"].append({k: v for k, v in PLACEHOLDER_USER.items() if k in columns})


def clean_sensitive(snap: dict[str, list[dict[str, Any]]], user_id: int, report: Report, *, token: Callable[[], str] = lambda: secrets.token_hex(32)) -> None:
    for row in snap.get("users", []):
        if row["id"] != user_id:
            continue
        for column, value in (("intervals_api_key_encrypted", None), ("failed_login_attempts", 0), ("locked_until", None)):
            if column in row and row[column] != value:
                row[column] = value
                report.bump(report.cleaned, f"users.{column}")
    for row in snap.get("devices", []):
        if row.get("token_hash") is not None:
            row["token_hash"] = token()
            report.bump(report.cleaned, "devices.token_hash")
    for table in EXCLUDED_TABLES:
        if snap.get(table):
            raise AbortError(f"{table} não pode ser copiada")


def verify_snapshot(snap: dict[str, list[dict[str, Any]]], schema: Schema, user_id: int) -> None:
    """Última barreira antes de gerar SQL: nenhum id de terceiro sobreviveu."""
    allowed = {user_id, PLACEHOLDER_USER_ID}
    for table, column in schema.user_reference_columns():
        for row in snap.get(table, []):
            if row.get(column) is not None and row[column] not in allowed:
                raise AbortError(f"{table}.{column} ainda referencia outro usuário ({row[column]})")
    for table in USER_TABLES:
        for row in snap.get(table, []):
            if row.get("user_id") != user_id:
                raise AbortError(f"{table} contém linha de outro usuário")
    for row in snap.get("users", []):
        if row["id"] != user_id and row.get("password_hash") != PLACEHOLDER_USER["password_hash"]:
            raise AbortError("hash de senha de terceiro na cópia")


def prepare(reader: Any, schema: Schema, user_id: int) -> tuple[dict[str, list[dict[str, Any]]], Report]:
    report = Report()
    snap = build_snapshot(reader, schema, user_id)
    close_references(snap, schema, report)
    anonymize_user_references(snap, schema, user_id, report)
    clean_sensitive(snap, user_id, report)
    verify_snapshot(snap, schema, user_id)
    report.counts = {t: len(rows) for t, rows in sorted(snap.items())}
    return snap, report


# ---------------------------------------------------------------------------
# Geração do SQL
# ---------------------------------------------------------------------------

_ESCAPES = {"\\": "\\\\", "'": "\\'", "\0": "\\0", "\n": "\\n", "\r": "\\r", "\x1a": "\\Z"}


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "X'" + bytes(value).hex() + "'" if value else "''"
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        value = value.isoformat(sep=" ") if isinstance(value, _dt.datetime) else value.isoformat()
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value)
    return "'" + "".join(_ESCAPES.get(ch, ch) for ch in text) + "'"


def tables_to_clear(schema: Schema) -> list[str]:
    kinds = classified_tables()
    return sorted(t for t in schema.tables if kinds.get(t) not in (None, "untouched"))


def render_sql(snap: dict[str, list[dict[str, Any]]], schema: Schema, *, batch: int = 200, header: str = "") -> list[str]:
    statements = [
        "SET NAMES utf8mb4",
        "SET time_zone = '+00:00'",  # Data API devolve TIMESTAMP em UTC
        "SET FOREIGN_KEY_CHECKS = 0",
        "SET UNIQUE_CHECKS = 0",
    ]
    statements += [f"DELETE FROM {_q(t)}" for t in tables_to_clear(schema)]
    for table in sorted(snap):
        rows = snap[table]
        if not rows:
            continue
        cols = schema.tables[table].insertable_columns
        col_sql = ", ".join(_q(c) for c in cols)
        for start in range(0, len(rows), batch):
            values = ",\n".join(
                "(" + ", ".join(sql_literal(row.get(c)) for c in cols) + ")" for row in rows[start : start + batch]
            )
            statements.append(f"INSERT INTO {_q(table)} ({col_sql}) VALUES\n{values}")
    statements += ["SET UNIQUE_CHECKS = 1", "SET FOREIGN_KEY_CHECKS = 1"]
    if header:
        statements[0] = header + "\n" + statements[0]
    return statements


def write_sql_file(path: Path, statements: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for stmt in statements:
            fh.write(stmt + ";\n")


# ---------------------------------------------------------------------------
# Migrações: produção precisa estar na mesma versão do destino
# ---------------------------------------------------------------------------


def repo_migration_files(directory: Path = MIGRATIONS_DIR) -> set[str]:
    return {p.name for p in directory.iterdir() if re.match(r"^\d{3}_.+\.(sql|py)$", p.name)}


def compare_migrations(prod: dict[str, str], local: dict[str, Optional[str]], local_label: str) -> None:
    """`prod`/`local`: filename -> checksum (None = não comparar checksum)."""
    only_prod = sorted(set(prod) - set(local))
    only_local = sorted(set(local) - set(prod))
    changed = sorted(f for f in set(prod) & set(local) if local[f] is not None and local[f] != prod[f])
    if only_prod or only_local or changed:
        parts = []
        if only_prod:
            parts.append("só em produção: " + ", ".join(only_prod))
        if only_local:
            parts.append(f"só em {local_label}: " + ", ".join(only_local))
        if changed:
            parts.append("checksum diferente: " + ", ".join(changed))
        raise AbortError("Versão de migração diverge entre produção e " + local_label + " (" + "; ".join(parts) + ").")


# ---------------------------------------------------------------------------
# Destino local
# ---------------------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


@dataclass(frozen=True)
class Destination:
    host: str
    port: int
    user: str
    password: str
    database: str


def validate_destination(dest: Destination, dotenv: dict[str, str], environ: dict[str, str]) -> None:
    """Trava: o destino só pode ser o MySQL local do docker compose."""
    host = dest.host.strip().lower()
    for name, value in (("host", dest.host), ("database", dest.database), ("user", dest.user)):
        if "arn:" in value.lower() or "amazonaws.com" in value.lower():
            raise AbortError(f"destino recusado: {name} parece recurso AWS ({value})")
    if host not in LOCAL_HOSTS:
        raise AbortError(f"destino recusado: host {dest.host!r} não é o MySQL local do compose ({', '.join(sorted(LOCAL_HOSTS))})")
    for source, values in (("ambiente", environ), (".env", dotenv)):
        if (values.get("DB_BACKEND") or "").strip().lower() == "dataapi":
            raise AbortError(f"destino recusado: DB_BACKEND=dataapi no {source} (configuração de produção)")
    expected_db = dotenv.get("MYSQL_DATABASE")
    if not expected_db:
        raise AbortError(".env sem MYSQL_DATABASE: não sei qual é o banco de DEV")
    if dest.database != expected_db:
        raise AbortError(f"destino recusado: banco {dest.database!r} não é o do .env de DEV ({expected_db!r})")
    if dest.database.lower() in SYSTEM_DATABASES:
        raise AbortError(f"destino recusado: {dest.database!r} é banco de sistema")


def destination_from_env(dotenv: dict[str, str], host: str) -> Destination:
    password = dotenv.get("MYSQL_ROOT_PASSWORD")
    if not password:
        raise AbortError(".env sem MYSQL_ROOT_PASSWORD (precisa de root para o backup e a carga)")
    return Destination(
        host=host,
        port=int(dotenv.get("MUSCLEFIT_MYSQL_HOST_PORT") or 3306),
        user="root",
        password=password,
        database=dotenv.get("MYSQL_DATABASE", ""),
    )


def compose_exec(dest: Destination, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    cmd = [
        "docker", "compose", "--project-directory", str(REPO_ROOT),
        "exec", "-T", "-e", f"MYSQL_PWD={dest.password}", "mysql", *args,
    ]
    return subprocess.run(cmd, check=True, **kwargs)


def connect_local(dest: Destination) -> Any:
    import pymysql  # só no modo de carga; o dry-run não precisa

    conn = pymysql.connect(
        host=dest.host if dest.host != "mysql" else "127.0.0.1",
        port=dest.port,
        user=dest.user,
        password=dest.password,
        database=dest.database,
        charset="utf8mb4",
        autocommit=False,
    )
    with conn.cursor() as cur:
        try:
            cur.execute("SELECT @@aurora_version")
            raise AbortError("destino recusado: o servidor conectado é uma Aurora")
        except AbortError:
            raise
        except Exception:  # noqa: BLE001 - MySQL comum não tem @@aurora_version
            pass
        cur.execute("SELECT @@server_uuid")
        server_uuid = cur.fetchone()[0]
    container_uuid = compose_exec(
        dest, ["mysql", "-uroot", "-N", "-B", "-e", "SELECT @@server_uuid"], capture_output=True, text=True
    ).stdout.strip()
    if server_uuid != container_uuid:
        raise AbortError("destino recusado: a conexão não é o MySQL do container `mysql` do compose")
    return conn


def local_migrations(conn: Any) -> dict[str, Optional[str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT filename, checksum FROM schema_migration_files")
        return {row[0]: row[1] for row in cur.fetchall()}


def backup_local(dest: Destination, now: _dt.datetime) -> Path:
    BACKUPS_DIR.mkdir(exist_ok=True)
    path = BACKUPS_DIR / f"dev-{now:%Y%m%d-%H%M}.sql"
    with path.open("wb") as fh:
        compose_exec(
            dest,
            ["mysqldump", "-uroot", "--single-transaction", "--routines", "--triggers", "--no-tablespaces", "--set-gtid-purged=OFF", dest.database],
            stdout=fh,
        )
    if path.stat().st_size == 0:
        raise AbortError(f"backup vazio em {path}; nada foi alterado")
    return path


def restore_local(dest: Destination, path: Path) -> None:
    with path.open("rb") as fh:
        compose_exec(dest, ["mysql", "-uroot", dest.database], stdin=fh)


def apply_statements(conn: Any, statements: list[str]) -> None:
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------


def aws_session(profile: str, region: str) -> Any:
    import boto3
    from botocore.exceptions import MissingDependencyException

    session = boto3.Session(profile_name=profile, region_name=region)
    try:
        if session.get_credentials() is None:
            raise AbortError(f"sem credenciais no perfil {profile}: rode `aws login --profile {profile}`")
        return session
    except MissingDependencyException:
        # `aws login` sem botocore[crt]: pede as credenciais temporárias ao próprio AWS CLI.
        out = subprocess.run(
            ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            raise AbortError(f"credenciais do perfil {profile} inválidas: rode `aws login --profile {profile}`")
        creds = json.loads(out.stdout)
        return boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds.get("SessionToken"),
            region_name=region,
        )


def discover_cluster(session: Any, cluster_id: str) -> tuple[str, str, str]:
    """(cluster_arn, secret_arn, database) por DescribeDBClusters - só leitura."""
    clusters = session.client("rds").describe_db_clusters(DBClusterIdentifier=cluster_id)["DBClusters"]
    if not clusters:
        raise AbortError(f"cluster {cluster_id} não encontrado")
    cluster = clusters[0]
    if not cluster.get("HttpEndpointEnabled"):
        raise AbortError(f"cluster {cluster_id} sem Data API habilitada")
    secret_arn = (cluster.get("MasterUserSecret") or {}).get("SecretArn")
    if not secret_arn:
        paginator = session.client("secretsmanager").get_paginator("list_secrets")
        for page in paginator.paginate(Filters=[{"Key": "owning-service", "Values": ["rds"]}]):
            for secret in page.get("SecretList", []):
                if cluster_id in (secret.get("Name") or "") or cluster["DbClusterResourceId"] in (secret.get("Name") or ""):
                    secret_arn = secret["ARN"]
    if not secret_arn:
        raise AbortError("secret do cluster não encontrado")
    return cluster["DBClusterArn"], secret_arn, cluster.get("DatabaseName") or "musclefit"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def print_report(report: Report) -> None:
    print("\nLinhas por tabela:")
    width = max((len(t) for t in report.counts), default=10)
    for table, n in report.counts.items():
        print(f"  {table.ljust(width)}  {n:>7}")
    print(f"  {'TOTAL'.ljust(width)}  {sum(report.counts.values()):>7}")
    for title, bucket in (
        ("Referências órfãs anuladas", report.nulled),
        ("Linhas órfãs descartadas", report.dropped),
        ("Ids de outros usuários anulados", report.user_refs_nulled),
        ("Ids de outros usuários -> placeholder", report.user_refs_placeholder),
        ("Colunas sensíveis limpas", report.cleaned),
    ):
        if bucket:
            print(f"\n{title}:")
            for key, n in sorted(bucket.items()):
                print(f"  {key}: {n}")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Copia os dados de produção de UM usuário para o MySQL local do DEV.")
    p.add_argument("--user-id", type=int, help="usuário de produção a copiar (ex.: 1)")
    p.add_argument("--dry-run", action="store_true", help="só lê produção e grava o SQL; não toca o MySQL local")
    p.add_argument("--yes", action="store_true", help="não pede confirmação antes de substituir os dados do DEV")
    p.add_argument("--out", type=Path, help="arquivo do SQL gerado (padrão: backups/prod-user<N>-AAAAMMDD-HHMM.sql)")
    p.add_argument("--restore", type=Path, help="restaura um backup do DEV gerado por este script e sai")
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--cluster-id", default=DEFAULT_CLUSTER_ID)
    p.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    p.add_argument("--mysql-host", default="127.0.0.1")
    args = p.parse_args(argv)
    if args.restore is None and args.user_id is None:
        p.error("--user-id é obrigatório (ou use --restore)")
    return args


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    now = _dt.datetime.now()

    dest: Optional[Destination] = None
    if not args.dry_run or args.restore:
        dotenv = read_env_file(args.env_file)
        dest = destination_from_env(dotenv, args.mysql_host)
        validate_destination(dest, dotenv, dict(os.environ))

    if args.restore:
        assert dest is not None
        restore_local(dest, args.restore)
        print(f"DEV restaurado a partir de {args.restore}")
        return 0

    session = aws_session(args.profile, args.region)
    cluster_arn, secret_arn, database = discover_cluster(session, args.cluster_id)
    reader = DataApiReader(session.client("rds-data"), cluster_arn, secret_arn, database)
    print(f"Produção (só leitura): {args.cluster_id} / {database} ({args.region})")

    prod_migrations = {
        _text(r["filename"]): _text(r["checksum"])
        for r in reader.query("SELECT filename, checksum FROM schema_migration_files")
    }
    compare_migrations(prod_migrations, {f: None for f in repo_migration_files()}, "o repositório (api/db/migrations)")

    conn = None
    if dest is not None:
        conn = connect_local(dest)
        compare_migrations(prod_migrations, local_migrations(conn), "o MySQL local")

    schema = load_schema(reader)
    snap, report = prepare(reader, schema, args.user_id)
    header = f"-- prod_to_dev.py: usuário {args.user_id}, gerado em {now:%Y-%m-%d %H:%M} (contém dados pessoais; não commitar)"
    statements = render_sql(snap, schema, header=header)
    out = args.out or BACKUPS_DIR / f"prod-user{args.user_id}-{now:%Y%m%d-%H%M}.sql"
    write_sql_file(out, statements)
    print_report(report)
    print(f"\nSQL gerado: {out} ({len(statements)} instruções; {reader.statements} consultas à Data API)")

    if args.dry_run:
        print("Dry-run: MySQL local não foi tocado.")
        return 0

    assert dest is not None and conn is not None
    if not args.yes:
        answer = input(f"Substituir os dados do banco local {dest.database!r} pelos de produção? Digite SIM: ")
        if answer.strip() != "SIM":
            print("Cancelado; nada foi alterado.")
            return 1
    backup = backup_local(dest, now)
    print(f"Backup do DEV: {backup}")
    print(f"  Para restaurar: python scripts/prod_to_dev.py --restore {backup.relative_to(REPO_ROOT).as_posix()}")
    apply_statements(conn, statements)
    conn.close()
    print("Carga concluída no MySQL local.")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except AbortError as exc:
        print(f"ABORTADO: {exc}", file=sys.stderr)
        sys.exit(2)
