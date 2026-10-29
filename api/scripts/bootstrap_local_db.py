#!/usr/bin/env python3
"""Seed OPCIONAL do MySQL local com um dump real de producao (PII).

Por padrao este script NAO faz nada - o schema ja é aplicado automaticamente
pelo MySQL via docker-entrypoint-initdb.d (api/db/migrations/, montado no
docker-compose.yml). Este script só entra em ação quando alguém pede
explicitamente dados de exemplo REAIS (nomes, e-mails, hashes de senha de
usuarios de producao) para testar localmente com uma base parecida com a
real, em vez do banco vazio.

Uso:
    SEED_WITH_PROD_DUMP=1 python api/scripts/bootstrap_local_db.py

Sem a env var (ou com valor diferente de "1"), apenas imprime instruções e
sai com sucesso - seguro de rodar sempre, inclusive em CI/scripts que não
querem seed algum.

ATENCAO: o dump em tools/onprem-sync/backups/<timestamp>/restore.sql contem
dados REAIS de producao (PII: nomes, e-mails, hashes de senha). Nunca habilite
SEED_WITH_PROD_DUMP em um ambiente compartilhado/não confiável.
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Mesmo diretorio deste script (api/scripts/ no host, /app/scripts/ no
# container): sys.path[0] ao rodar `python scripts/bootstrap_local_db.py`.
from seed_sql_rewrite import insert_table_name, rewrite_insert_for_schema, split_statements

def _resolve_backups_dir() -> Path:
    """Localiza tools/onprem-sync/backups tanto rodando no host (script em
    api/scripts/, raiz do repo = 2 niveis acima) quanto dentro do container
    `api` (script em /app/scripts/, com tools/onprem-sync montado direto em
    /app/tools/onprem-sync via docker-compose.yml - so 1 nivel acima). Testa
    os dois layouts e usa o primeiro que existir; sem nenhum dos dois, cai no
    layout de host (mensagem de erro mais util para quem le o path)."""
    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir.parents[1] / "tools" / "onprem-sync" / "backups",  # host: api/scripts/../..
        script_dir.parent / "tools" / "onprem-sync" / "backups",      # container: /app/scripts/../
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


BACKUPS_DIR = _resolve_backups_dir()


def _find_latest_restore_sql() -> Path | None:
    """Localiza o restore.sql mais recente, pelo timestamp no nome do
    diretorio pai (ex.: 20260912T151441Z) - maior timestamp = mais recente.
    Nao depende de mtime do arquivo (que pode ter sido alterado por
    checkout/copia), so do nome do diretorio, que e o proprio timestamp."""
    if not BACKUPS_DIR.is_dir():
        return None

    candidates = sorted(
        (d for d in BACKUPS_DIR.iterdir() if d.is_dir() and (d / "restore.sql").is_file()),
        key=lambda d: d.name,
        reverse=True,
    )
    return candidates[0] / "restore.sql" if candidates else None


def _print_disabled_message() -> None:
    print(
        "[bootstrap_local_db] Nenhum seed aplicado (SEED_WITH_PROD_DUMP nao "
        "esta setada como \"1\"). O schema ja foi criado automaticamente pelo "
        "MySQL a partir de api/db/migrations/ - o banco esta vazio, pronto "
        "para uso.\n"
        "\n"
        "Para popular com um snapshot REAL de producao (contem PII: nomes, "
        "e-mails, hashes de senha - use com cautela, apenas em ambiente "
        "local confiavel):\n"
        "\n"
        "    SEED_WITH_PROD_DUMP=1 python api/scripts/bootstrap_local_db.py\n"
        "\n"
        "(ou `make seed`, se disponivel)."
    )


_OTHER_BUCKET = "(outros statements)"
_DDL_RE = re.compile(r"\A\s*(CREATE|ALTER|DROP|RENAME)\b", re.IGNORECASE)
_DELETE_TABLE_RE = re.compile(r"\A\s*DELETE\s+FROM\s+`?([\w$]+)`?", re.IGNORECASE)
# Mensagens do MySQL podem citar valores de linha (ex.: "Duplicate entry
# 'fulano@x.com' for key ..."): todo trecho entre aspas simples e mascarado
# antes de ir para o log. Nomes de coluna/tabela descartados ja aparecem no
# resumo por outro caminho.
_QUOTED_RE = re.compile(r"'(?:[^'\\]|\\.)*'")


@dataclass
class _TableStats:
    applied: int = 0
    failed: int = 0
    skipped: int = 0
    table_missing: bool = False
    dropped_columns: set[str] = field(default_factory=set)
    errors: Counter = field(default_factory=Counter)  # (errno, mensagem mascarada) -> n


def _redact_error(exc: Exception) -> tuple[object, str]:
    args = getattr(exc, "args", ())
    if len(args) >= 2 and isinstance(args[0], int):
        return args[0], _QUOTED_RE.sub("'?'", str(args[1]))
    return type(exc).__name__, _QUOTED_RE.sub("'?'", str(exc))


def _load_table_columns(cur) -> dict[str, set[str]]:
    """Colunas reais de TODAS as tabelas do banco alvo, numa unica consulta a
    information_schema (cache de sessao; recarregado se o dump trouxer DDL)."""
    cur.execute(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE()"
    )
    columns: dict[str, set[str]] = {}
    for table_name, column_name in cur.fetchall():
        columns.setdefault(str(table_name).lower(), set()).add(str(column_name).lower())
    return columns


def _print_seed_summary(stats: dict[str, _TableStats], insert_ignore: bool) -> None:
    out = sys.stderr
    total_applied = sum(s.applied for s in stats.values())
    total_failed = sum(s.failed for s in stats.values())
    total_skipped = sum(s.skipped for s in stats.values())
    print(
        f"[bootstrap_local_db] Resumo: {total_applied} aplicado(s), {total_failed} "
        f"falhado(s), {total_skipped} pulado(s)"
        f"{' (SEED_INSERT_IGNORE=1)' if insert_ignore else ''}.",
        file=out,
    )
    print(f"  {'tabela':<40} {'aplic.':>7} {'falhas':>7} {'pulados':>7}  colunas descartadas", file=out)
    for table in sorted(stats):
        s = stats[table]
        dropped = ", ".join(sorted(s.dropped_columns)) or "-"
        if s.table_missing:
            dropped = "TABELA INEXISTENTE NO SCHEMA LOCAL"
        print(f"  {table:<40} {s.applied:>7} {s.failed:>7} {s.skipped:>7}  {dropped}", file=out)

    missing = sorted(t for t, s in stats.items() if s.table_missing)
    if missing:
        print(f"[bootstrap_local_db] Tabelas puladas (inexistentes localmente): {', '.join(missing)}", file=out)
    if total_failed:
        print(
            "[bootstrap_local_db] Falhas por tabela/erro (valores entre aspas "
            "mascarados; linhas ja existentes geram 'Duplicate entry' - inofensivo):",
            file=out,
        )
        for table in sorted(stats):
            for (errno, message), count in stats[table].errors.most_common():
                print(f"  - {table}: {count}x [{errno}] {message}", file=out)


def _apply_dump_via_pymysql(dump_path: Path, host: str, port: str, user: str, password: str, database: str) -> int:
    """Aplica o dump via PyMySQL, statement a statement, adaptando cada INSERT
    ao schema local antes de executar.

    Por que NAO usar o cliente `mysql` (mesmo quando existe no PATH): ele
    executa o arquivo cru, sem a reescrita abaixo, e aborta no primeiro erro.

    Drift Aurora <-> local: o dump e gerado da Aurora de producao e insere com
    lista explicita de colunas. Os gaps conhecidos de schema ja sao cobertos
    ANTES do dump: `equipment_types` e
    `equipment.equipment_type_id/manufacturer/model/location` pela migracao
    022 e `activities.workout_plan_id` pela migracao 026
    (`_run_app_migrations()`). O que sobra - colunas que so existem em
    dumps antigos (`gyms.color_palette`, feature descartada), gaps
    novos ainda sem migracao, ou a 022 nao aplicada porque o runner falhou -
    fazia o statement inteiro falhar com "Unknown column", deixando tabelas-pai
    vazias e as filhas orfas. Agora (`scripts/seed_sql_rewrite.py`):
      - colunas do dump inexistentes no schema local sao DESCARTADAS (coluna +
        valor em todas as tuplas) - os demais dados da linha entram;
      - INSERT em tabela inexistente e pulado (contado no resumo);
      - INSERT sem drift e executado com o texto original, sem alteracao.
    A decisao e por consulta ao information_schema, nao por lista fixa: com a
    022 aplicada, as colunas de `equipment` nao sao mais descartadas. Um gap
    novo aparece no resumo como "colunas descartadas" - a correcao e uma
    migracao numerada nova.

    Best-effort: cada statement roda em transacao PROPRIA; falha e contada e
    o seed segue (abortar jogaria fora os dados validos das outras tabelas).

    FOREIGN_KEY_CHECKS=0 e setado explicitamente na sessao ANTES do primeiro
    statement (alem do SET que o proprio dump ja traz no topo) e religado ao
    final: a ordem do dump e alfabetica (filhas antes de pais), entao a
    checagem precisa estar desligada de forma garantida, nao dependente do
    primeiro statement do arquivo ter dado certo. Variavel de SESSAO - vale
    para a conexao unica usada aqui, commit/rollback nao a resetam.

    Reexecucao: o dump faz `DELETE FROM` em cada tabela antes dos INSERTs,
    entao reaplicar num banco parcialmente populado reconstroi as tabelas do
    dump em vez de duplicar. Se ainda assim alguma linha ja existir,
    "Duplicate entry" e contado como falha inofensiva. `SEED_INSERT_IGNORE=1`
    (opt-in) troca INSERT por INSERT IGNORE - silencia duplicatas, mas tambem
    rebaixa a warning erros de conversao/truncamento (linha entra alterada
    sem aviso), por isso NAO e o padrao."""
    import pymysql

    insert_ignore = os.environ.get("SEED_INSERT_IGNORE", "0") == "1"
    statements = split_statements(dump_path.read_text(encoding="utf-8"))

    conn = pymysql.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=database,
        autocommit=False,
        charset="utf8mb4",
    )
    stats: dict[str, _TableStats] = defaultdict(_TableStats)
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            table_columns = _load_table_columns(cur)
            for statement in statements:
                try:
                    result = rewrite_insert_for_schema(
                        statement, table_columns, insert_ignore=insert_ignore
                    )
                except ValueError as exc:  # INSERT malformado (sem dados na mensagem)
                    bucket = stats[insert_table_name(statement) or _OTHER_BUCKET]
                    bucket.failed += 1
                    bucket.errors[("parse", str(exc))] += 1
                    continue

                if result.table is not None:
                    bucket = stats[result.table]
                    bucket.dropped_columns.update(result.dropped_columns)
                    bucket.table_missing = bucket.table_missing or result.table_missing
                else:
                    delete_match = _DELETE_TABLE_RE.match(statement)
                    bucket = stats[delete_match.group(1) if delete_match else _OTHER_BUCKET]

                if result.statement is None:
                    bucket.skipped += 1
                    continue

                try:
                    cur.execute(result.statement)
                    conn.commit()
                    bucket.applied += 1
                except Exception as exc:  # noqa: BLE001 - best-effort, ver docstring
                    conn.rollback()
                    bucket.failed += 1
                    bucket.errors[_redact_error(exc)] += 1
                else:
                    if _DDL_RE.match(statement):
                        table_columns = _load_table_columns(cur)
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    finally:
        conn.close()

    _print_seed_summary(stats, insert_ignore)
    return 0 if any(s.applied for s in stats.values()) else 1


def _run_app_schema_guards() -> None:
    """Roda os guards `_ensure_schema()` do proprio app (ALTER TABLE ADD
    COLUMN idempotentes dos modulos em `app.schema_guards`) ANTES de
    aplicar o dump de producao.

    O dump vem de um banco de producao onde essas colunas ja foram
    adicionadas ha muito tempo (lazy, na primeira chamada ao endpoint
    correspondente - ver comentario no topo dos arquivos citados e no
    cabecalho de docs/local-dev.md). Num schema fresh (so
    api/db/migrations/, sem nenhum endpoint chamado ainda) essas colunas
    ainda nao existem, e os INSERTs do dump falham com "Unknown column".
    Delega a lista de modulos e a logica best-effort (guard que falha so
    avisa e segue) para `app.schema_guards`, compartilhado com o hook de
    startup em api/app/main.py (banco local sem seed algum)."""
    # `app` (o pacote FastAPI) vive um nivel acima deste script tanto no host
    # (api/scripts/../ = api/, onde mora api/app/) quanto dentro do container
    # `api` (/app/scripts/../ = /app/, onde mora /app/app/) - mesmo raciocinio
    # de _resolve_backups_dir() acima.
    app_parent_dir = str(Path(__file__).resolve().parent.parent)
    if app_parent_dir not in sys.path:
        sys.path.insert(0, app_parent_dir)

    from app.schema_guards import run_all_schema_guards

    run_all_schema_guards()


def _run_app_migrations() -> None:
    """Aplica as migracoes versionadas pendentes (`app.migrate`, ex.: 022
    `equipment_types`) ANTES do dump. O init do MySQL
    (docker-entrypoint-initdb.d) so executa os `.sql` 001..021 - migracoes
    `.py` sao ignoradas por ele. Best-effort, como os guards: se falhar, so
    avisa e segue com o seed."""
    app_parent_dir = str(Path(__file__).resolve().parent.parent)
    if app_parent_dir not in sys.path:
        sys.path.insert(0, app_parent_dir)

    try:
        from app.migrate import migrate

        report = migrate()
        applied = ", ".join(item.filename for item in report.applied) or "nenhuma"
        print(f"[bootstrap_local_db] Migracoes aplicadas: {applied}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - best-effort, ver docstring
        print(f"[bootstrap_local_db] AVISO: migracoes nao aplicadas: {exc}", file=sys.stderr)


def main() -> int:
    if os.environ.get("SEED_WITH_PROD_DUMP", "0") != "1":
        _print_disabled_message()
        return 0

    dump_path = _find_latest_restore_sql()
    if dump_path is None:
        print(
            f"[bootstrap_local_db] SEED_WITH_PROD_DUMP=1 mas nenhum restore.sql "
            f"encontrado em {BACKUPS_DIR}. Nada aplicado.",
            file=sys.stderr,
        )
        return 1

    print(
        "[bootstrap_local_db] AVISO: aplicando dump com DADOS REAIS DE PRODUCAO "
        "(PII: nomes, e-mails, hashes de senha de usuarios reais).",
        file=sys.stderr,
    )
    print(f"[bootstrap_local_db] Dump selecionado (mais recente): {dump_path}", file=sys.stderr)

    _run_app_migrations()
    _run_app_schema_guards()

    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    port = os.environ.get("MYSQL_PORT", "3306")
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQL_DATABASE", "fitness")

    print(
        "[bootstrap_local_db] Aplicando via PyMySQL (INSERTs adaptados ao schema local)...",
        file=sys.stderr,
    )
    rc = _apply_dump_via_pymysql(dump_path, host, port, user, password, database)

    if rc == 0:
        print("[bootstrap_local_db] Seed aplicado com sucesso.", file=sys.stderr)
    else:
        print(f"[bootstrap_local_db] Falha ao aplicar seed (codigo {rc}).", file=sys.stderr)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
