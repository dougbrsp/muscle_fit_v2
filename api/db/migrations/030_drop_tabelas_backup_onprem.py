"""030 - remove as tabelas de backup ad-hoc herdadas do onprem.

Decisao do usuario (2026-09-14): "Remova as tabelas de backup do on-prem em
producao." As duas tabelas existem SO na Aurora de producao (criadas a mao no
onprem e copiadas no dump inicial); nao existem em dev nem em banco novo de CI
e nao sao lidas nem escritas pela aplicacao:

- `exercise_mappings_backup_20260714` (46 linhas, 21 colunas, utf8mb4_unicode_ci);
- `exercises_backup_before_mapping_sync` (42 linhas, 31 colunas).

Conteudo exportado antes do PR (JSON, fora do repositorio). DROP TABLE e
irreversivel: a unica volta e o export ou o PITR do cluster.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements; `up(db)`
executa o plano. Sem `DROP TABLE IF EXISTS` as cegas: o dry-run lista
exatamente o que sera aplicado. Antes do primeiro DROP o plano FALHA (nada e
aplicado) se:

- o nome existir, mas nao for BASE TABLE (ex.: view com o mesmo nome);
- houver FK saindo de uma das tabelas ou apontando para ela, vinda de qualquer
  tabela do schema (o DROP falharia ou deixaria de proteger dado);
- houver view do schema que referencie uma delas (ficaria invalida).

Planos esperados: Aurora -> 2 statements (os dois DROP); dev/CI -> 0 (so a
leitura de `information_schema.TABLES`); reexecucao -> 0.
Data API: sem PREPARE/SET/multi-statement; um statement por chamada.
Reexecutavel: se falhar entre os dois DROP, o plano seguinte so ve a restante.
"""

from __future__ import annotations

BACKUP_TABLES = ("exercise_mappings_backup_20260714", "exercises_backup_before_mapping_sync")


class DropBackupError(Exception):
    """Estado inesperado; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise DropBackupError(f"030: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _in_clause(values) -> str:
    return ", ".join(["%s"] * len(values))


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    found = {
        _text(row["TABLE_NAME"]): _text(row["TABLE_TYPE"])
        for row in db.query(
            "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in_clause(BACKUP_TABLES)})",
            BACKUP_TABLES,
        )
    }
    for name, table_type in sorted(found.items()):
        if table_type != "BASE TABLE":
            _fail(f"{name} existe como {table_type}, esperado BASE TABLE")
    targets = [name for name in BACKUP_TABLES if name in found]
    if not targets:
        return []

    fks = db.query(
        "SELECT TABLE_NAME, CONSTRAINT_NAME, REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL "
        f"AND (TABLE_NAME IN ({_in_clause(targets)}) OR REFERENCED_TABLE_NAME IN ({_in_clause(targets)}))",
        (*targets, *targets),
    )
    if fks:
        found_fks = sorted(
            {f"{_text(r['CONSTRAINT_NAME'])} ({_text(r['TABLE_NAME'])} -> {_text(r['REFERENCED_TABLE_NAME'])})" for r in fks}
        )
        _fail(f"FK envolvendo tabela de backup: {', '.join(found_fks)}")

    views = db.query(
        "SELECT VIEW_NAME, TABLE_NAME FROM information_schema.VIEW_TABLE_USAGE "
        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in_clause(targets)})",
        tuple(targets),
    )
    if views:
        found_views = sorted({f"{_text(r['VIEW_NAME'])} -> {_text(r['TABLE_NAME'])}" for r in views})
        _fail(f"view referencia tabela de backup: {', '.join(found_views)}")

    return [(f"DROP TABLE {name}", None) for name in targets]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
