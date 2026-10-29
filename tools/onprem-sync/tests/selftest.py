#!/usr/bin/env python3
"""Auto-teste ponta a ponta do sync, SEM tocar a AWS e SEM tocar producao.

O que ele monta:

- um container MySQL efemero, de nome proprio (`onprem-sync-selftest-mysql`),
  criado com `docker run` - NUNCA `docker compose`, que recriaria os
  containers reais do projeto;
- o schema real, extraido do onpremise via `mysqldump --no-data` (leitura);
- um dublê do client `rds-data` (tests/fake_data_api.py) apontando para esse
  container, no lugar do Aurora.

O que ele prova (cenarios abaixo):

1. carga inicial num destino vazio: plano = so INSERTs, e o apply passa na
   verificacao de contagem, checksum, FK orfa e encoding;
2. IDEMPOTENCIA: segunda execucao produz plano vazio (0/0/0);
3. reconciliacao: depois de mexer no destino a mao (linha apagada, linha
   editada com acento, linha intrusa), o plano detecta exatamente 1 insert,
   1 update e 1 delete, e o apply devolve o espelho ao estado correto;
4. AUTO_INCREMENT realinhado com o do onprem;
5. backup + `--restore-from` voltando o destino a um estado anterior.

Uso:
    python tests/selftest.py            # sobe, testa e destroi o container
    python tests/selftest.py --keep     # mantem o container para inspecao
"""

from __future__ import annotations

import argparse
import builtins
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import boto3  # noqa: E402

import sync  # noqa: E402
from fake_data_api import FakeRdsDataClient  # noqa: E402
from syncmod.config import load_config  # noqa: E402
from syncmod.onprem import OnpremReader  # noqa: E402


CONTAINER = "onprem-sync-selftest-mysql"
DB = "musclefit_selftest"
PORT = 13399
ROOT_PW = "selftest"
ONPREM_CONTAINER = "fitness-mysql"
ONPREM_DB = "fitness"
VIEW = "vw_muscle_group_equipment_exercises"

FAKE_SECRET = "arn:aws:secretsmanager:sa-east-1:000000000000:secret:selftest-FAKE"

_failures: list[str] = []


def log(msg: str = "") -> None:
    print(msg, flush=True)


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FALHOU"
    log(f"  [{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def run(cmd: list[str], input_bytes: bytes | None = None, check_rc: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, input=input_bytes, capture_output=True)
    if check_rc and proc.returncode != 0:
        raise RuntimeError(
            f"Comando falhou ({proc.returncode}): {' '.join(cmd[:4])}...\n"
            f"{proc.stderr.decode('utf-8', 'replace')[:2000]}"
        )
    return proc


# ---------------------------------------------------------------------------
# Container efemero
# ---------------------------------------------------------------------------

def destroy_container() -> None:
    run(["docker", "rm", "-f", CONTAINER], check_rc=False)


def start_container() -> None:
    destroy_container()
    log(f"  subindo container efemero {CONTAINER} (docker run, nao compose)...")
    run([
        "docker", "run", "-d", "--name", CONTAINER,
        "-e", f"MYSQL_ROOT_PASSWORD={ROOT_PW}",
        "-e", f"MYSQL_DATABASE={DB}",
        "-p", f"{PORT}:3306",
        "mysql:8.4",
        "--character-set-server=utf8mb4",
        "--collation-server=utf8mb4_0900_ai_ci",
    ])

    import pymysql

    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            conn = pymysql.connect(host="127.0.0.1", port=PORT, user="root",
                                   password=ROOT_PW, database=DB, charset="utf8mb4")
            conn.close()
            log("  container pronto.")
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("Container de teste nao ficou pronto em 180s.")


def load_schema_from_onprem() -> None:
    log("  extraindo DDL do onpremise (mysqldump --no-data, somente leitura)...")
    dump = run([
        "docker", "exec", ONPREM_CONTAINER, "sh", "-c",
        f'exec mysqldump --default-character-set=utf8mb4 --no-data --skip-triggers '
        f'--no-tablespaces --set-gtid-purged=OFF --ignore-table={ONPREM_DB}.{VIEW} '
        f'-uroot -p"$MYSQL_ROOT_PASSWORD" {ONPREM_DB}',
    ]).stdout

    ddl = b"SET FOREIGN_KEY_CHECKS=0;\n" + dump + b"\nSET FOREIGN_KEY_CHECKS=1;\n"
    run([
        "docker", "exec", "-i", CONTAINER, "sh", "-c",
        f'exec mysql --default-character-set=utf8mb4 -uroot -p{ROOT_PW} {DB}',
    ], input_bytes=ddl)
    log("  schema carregado no destino de teste.")


def target_sql(sql: str) -> list[tuple]:
    import pymysql

    conn = pymysql.connect(host="127.0.0.1", port=PORT, user="root", password=ROOT_PW,
                           database=DB, charset="utf8mb4", autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall()) if cur.description else []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ambiente do sync
# ---------------------------------------------------------------------------

class _FakeSts:
    @staticmethod
    def get_caller_identity() -> dict[str, str]:
        return {"Arn": "arn:aws:iam::000000000000:user/selftest", "Account": "000000000000"}


class _FakeSession:
    """Sessao boto3 de mentira: so sabe devolver o duble do `rds-data`."""

    available_profiles: list[str] = ["selftest"]

    def __init__(self, fake: FakeRdsDataClient) -> None:
        self._fake = fake

    def client(self, service_name: str, *a, **kw):
        if service_name == "rds-data":
            return self._fake
        if service_name == "sts":
            return _FakeSts()
        raise AssertionError(
            f"O auto-teste nao pode usar o servico AWS '{service_name}'. "
            "Isto indica que o codigo tentou falar com a AWS de verdade."
        )


def install_fake_aws() -> FakeRdsDataClient:
    fake = FakeRdsDataClient(host="127.0.0.1", port=PORT, user="root", password=ROOT_PW)
    session = _FakeSession(fake)

    # A ferramenta cria clients por Session (para honrar o perfil nomeado), e
    # nao por boto3.client direto. Os dois caminhos sao interceptados, para que
    # uma regressao que volte a usar boto3.client tambem seja pega aqui.
    boto3.Session = lambda *a, **kw: session  # type: ignore[assignment]
    boto3.client = session.client  # type: ignore[assignment]
    return fake


def make_config(tmp: Path) -> Path:
    base = json.loads((TOOL_DIR / "sync_config.json").read_text(encoding="utf-8"))
    base["aurora"]["database"] = DB
    base["aurora"]["secret_arn"] = FAKE_SECRET  # evita chamar rds:DescribeDBClusters
    base["aurora"]["profile"] = None  # a sessao e o duble; perfil real nao se aplica
    path = tmp / "selftest_config.json"
    path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class AutoConfirm:
    """Responde a confirmacao interativa, exercitando o codigo real do prompt."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self._original_input = builtins.input
        self._original_isatty = sys.stdin.isatty

    def __enter__(self) -> "AutoConfirm":
        builtins.input = lambda prompt="": (log(f"  {prompt}{self.answers[0]}"), self.answers.pop(0))[1]
        sys.stdin.isatty = lambda: True  # type: ignore[method-assign]
        return self

    def __exit__(self, *a) -> None:
        builtins.input = self._original_input
        sys.stdin.isatty = self._original_isatty  # type: ignore[method-assign]


def run_sync(argv: list[str], answers: list[str] | None = None) -> tuple[int, str]:
    import contextlib
    import io as _io

    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        if answers is not None:
            with AutoConfirm(answers):
                code = sync.main(argv)
        else:
            code = sync.main(argv)
    return code, buf.getvalue()


# ---------------------------------------------------------------------------
# Cenarios
# ---------------------------------------------------------------------------

def scenario_initial_load(cfg_path: Path, backup_dir: Path) -> None:
    log()
    log("CENARIO 1 - carga inicial em destino vazio")
    code, output = run_sync(["--config", str(cfg_path)])
    check("dry-run termina com sucesso", code == 0)
    check("dry-run nao escreve nada", "Nenhuma escrita foi feita" in output)
    check("plano tem inserts e nenhum delete", "0 deletes" in output and " 0 inserts," not in output)

    log()
    log("  aplicando...")
    code, output = run_sync(
        ["--config", str(cfg_path), "--apply", "--backup-dir", str(backup_dir)],
        answers=[f"ESPELHAR {DB}"],
    )
    check("apply termina OK e verificacao passa", code == 0, output[-3000:])
    check("relatorio final diz espelho exato", "TUDO OK" in output)
    check("nenhuma FK orfa", "0 com vinculo quebrado" in output)
    check("encoding sem divergencia", "nenhuma divergencia" in output)
    check("backup foi gravado antes da escrita", "BACKUP DO ESTADO ATUAL DO AURORA" in output)


def scenario_idempotency(cfg_path: Path) -> None:
    log()
    log("CENARIO 2 - idempotencia (criterio de aceitacao principal)")
    code, output = run_sync(["--config", str(cfg_path)])
    check("segunda execucao termina OK", code == 0)
    check("plano vazio na segunda execucao", "PLANO VAZIO" in output, output[-2000:])
    check("totais zerados", "TOTAL: 0 inserts, 0 updates, 0 deletes" in output, output[-2000:])


def scenario_reconciliation(cfg_path: Path, backup_dir: Path) -> None:
    log()
    log("CENARIO 3 - reconciliacao (delete, update acentuado e linha intrusa)")

    target_sql("DELETE FROM exercise_sets WHERE id = (SELECT * FROM (SELECT MIN(id) FROM exercise_sets) x)")
    target_sql("UPDATE muscle_groups SET name = 'CORROMPIDO SEM ACENTO' WHERE id = 1")
    target_sql(
        "INSERT INTO muscle_groups (id, name) VALUES (9999, 'Grupo que so existe no destino')"
    )

    code, output = run_sync(["--config", str(cfg_path)])
    check("plano detecta 1 insert, 1 update e 1 delete", "TOTAL: 1 inserts, 1 updates, 1 deletes" in output,
          output[-2500:])

    code, output = run_sync(
        ["--config", str(cfg_path), "--apply", "--backup-dir", str(backup_dir)],
        answers=[f"ESPELHAR {DB}"],
    )
    check("apply reconcilia e verifica OK", code == 0 and "TUDO OK" in output, output[-3000:])

    restored = target_sql("SELECT name, HEX(name), OCTET_LENGTH(name), CHAR_LENGTH(name) FROM muscle_groups WHERE id = 1")
    onprem = subprocess.run([
        "docker", "exec", ONPREM_CONTAINER, "sh", "-c",
        'exec mysql --default-character-set=utf8mb4 --raw -N -B -uroot -p"$MYSQL_ROOT_PASSWORD" fitness '
        '-e "SELECT HEX(name), OCTET_LENGTH(name), CHAR_LENGTH(name) FROM muscle_groups WHERE id=1"',
    ], capture_output=True).stdout.decode("utf-8").strip().split("\t")
    check(
        "acentuacao restaurada byte a byte (HEX identico ao onprem)",
        restored and str(restored[0][1]) == onprem[0],
        f"destino={restored[0][1] if restored else None} onprem={onprem[0]}",
    )
    check("nenhuma linha intrusa sobrou", target_sql("SELECT COUNT(*) FROM muscle_groups WHERE id=9999")[0][0] == 0)

    code, output = run_sync(["--config", str(cfg_path)])
    check("plano volta a ser vazio apos reconciliacao", "PLANO VAZIO" in output)


def scenario_auto_increment(cfg_path: Path) -> None:
    log()
    log("CENARIO 4 - AUTO_INCREMENT realinhado com o onprem")
    tables = ["activities", "exercise_sets", "intervals_activities", "manual_effort_metrics"]
    rows = target_sql(
        "SELECT TABLE_NAME, AUTO_INCREMENT FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA='{DB}' AND TABLE_NAME IN ({', '.join(repr(t) for t in tables)})"
    )
    got = {r[0]: r[1] for r in rows}

    # Alvo esperado = max(AUTO_INCREMENT declarado no onprem, MAX(id)+1),
    # porque o InnoDB nunca aceita contador abaixo de MAX(id)+1.
    union = " UNION ALL ".join(
        f"SELECT '{t}' AS t, GREATEST(IFNULL((SELECT AUTO_INCREMENT FROM information_schema.TABLES "
        # Sem crases no nome da tabela: este SQL vai dentro de `-e "..."` no
        # `sh -c`, e a crase viraria substituicao de comando do shell.
        f"WHERE TABLE_SCHEMA='{ONPREM_DB}' AND TABLE_NAME='{t}'), 1), IFNULL(MAX(id),0)+1) AS ai FROM {t}"
        for t in tables
    )
    raw = subprocess.run([
        "docker", "exec", ONPREM_CONTAINER, "sh", "-c",
        f'exec mysql --raw -N -B -uroot -p"$MYSQL_ROOT_PASSWORD" {ONPREM_DB} -e "{union}"',
    ], capture_output=True).stdout.decode("utf-8").strip().splitlines()
    expected = {line.split("\t")[0]: int(line.split("\t")[1]) for line in raw if line.strip()}

    for table, value in expected.items():
        check(f"AUTO_INCREMENT de {table} = {value} (alvo efetivo do onprem)",
              got.get(table) == value, f"veio {got.get(table)}")


def scenario_backup_restore(cfg_path: Path, backup_dir: Path) -> None:
    log()
    log("CENARIO 5 - backup e restauracao")
    code, output = run_sync(["--config", str(cfg_path), "--backup-only", "--backup-dir", str(backup_dir)])
    check("--backup-only termina OK", code == 0)
    check("--backup-only nao escreve", "Nada foi escrito no Aurora" in output)

    snapshots = sorted(p for p in backup_dir.iterdir() if p.is_dir())
    latest = snapshots[-1]
    check("backup tem manifest, data/ e restore.sql",
          (latest / "manifest.json").is_file() and (latest / "data").is_dir() and (latest / "restore.sql").is_file())

    manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
    backed_up = sum(t["row_count"] for t in manifest["tables"].values())

    # Total conferido contra o onprem AO VIVO, nunca contra numero fixo: este
    # banco e producao e recebe sessoes de treino novas o tempo todo.
    cfg = load_config(cfg_path)
    reader = OnpremReader(cfg.onprem, time_zone=cfg.time_zone)
    schema = reader.load_schema()
    scope = [t for t in schema.names if t not in cfg.excluded_table_names]
    expected_total = sum(reader.count_rows(scope).values())
    check(f"manifest conta as mesmas {expected_total} linhas do onprem",
          backed_up == expected_total, f"backup={backed_up} onprem={expected_total}")

    target_sql("DELETE FROM gyms WHERE id = 11")
    code, output = run_sync(
        ["--config", str(cfg_path), "--restore-from", str(latest)],
        answers=[f"RESTAURAR {DB}"],
    )
    check("--restore-from termina OK", code == 0, output[-2000:])
    hex_row = target_sql("SELECT HEX(name) FROM gyms WHERE id = 11")
    check("linha restaurada com os MESMOS bytes (inclusive mojibake de origem)",
          bool(hex_row) and hex_row[0][0] == "47617669C383C2B56573",
          f"veio {hex_row[0][0] if hex_row else None}")


def scenario_guards(cfg_path: Path) -> None:
    log()
    log("CENARIO 6 - salvaguardas")
    from syncmod.onprem import OnpremError, _assert_read_only

    for sql in ["DELETE FROM users", "UPDATE users SET role='admin'", "DROP TABLE users", "TRUNCATE users"]:
        try:
            _assert_read_only(sql)
            check(f"onprem recusa escrita: {sql[:20]}", False, "nao levantou erro")
        except OnpremError:
            check(f"onprem recusa escrita: {sql[:20]}", True)

    code = None
    try:
        with AutoConfirm(["sim"]):
            code, _ = run_sync(["--config", str(cfg_path), "--apply"]), None
    except SystemExit as exc:
        check("confirmacao errada aborta o apply", "Confirmacao nao conferiu" in str(exc), str(exc))
    else:
        check("confirmacao errada aborta o apply", False, f"nao abortou (code={code})")


def scenario_legacy_tables(cfg_path: Path) -> None:
    log()
    log("CENARIO 7 - tabelas legadas (equipment_exercise_mappings)")
    cfg = load_config(cfg_path)
    legacy = "equipment_exercise_mappings"

    check("config marca a tabela como legada", legacy in cfg.legacy_table_names)
    check("legada NAO e excluida (continua sincronizada enquanto o espelho existir)",
          legacy not in cfg.excluded_table_names)
    check("canonica equipment_exercises no escopo esperado e nao excluida",
          "equipment_exercises" not in cfg.excluded_table_names
          and any("equipment_exercises" in tier for tier in sync.EXPECTED_TIERS))

    expected = [t for tier in sync.EXPECTED_TIERS for t in tier]
    sem_legada = [t for t in expected if t != legacy]
    check("tabela legada dropada nao gera aviso na comparacao humana",
          sync.compare_with_expected(sem_legada, sem_legada, cfg.legacy_table_names) == [])
    check("tabela nao legada ausente continua gerando aviso",
          sync.compare_with_expected(sem_legada, [t for t in sem_legada if t != "equipment_exercises"],
                                     cfg.legacy_table_names) != [])

    code, output = run_sync(["--config", str(cfg_path), "--onprem-only",
                             "--tables", f"{legacy},equipment_exercises"])
    check("--onprem-only sinaliza a tabela legada no relatorio",
          code == 0 and f"LEGADA (no escopo): {legacy}" in output, f"code={code}")

    conflict = json.loads(cfg_path.read_text(encoding="utf-8"))
    conflict["excluded_tables"][legacy] = "conflito proposital"
    conflict_path = cfg_path.with_name("selftest_conflict.json")
    conflict_path.write_text(json.dumps(conflict, ensure_ascii=False), encoding="utf-8")
    try:
        load_config(conflict_path)
        check("config recusa tabela legada e excluida ao mesmo tempo", False, "nao levantou erro")
    except ValueError:
        check("config recusa tabela legada e excluida ao mesmo tempo", True)


BACKUP_TABLES_030 = ("exercise_mappings_backup_20260714", "exercises_backup_before_mapping_sync")


def scenario_backup_tables_dropped_on_target(cfg_path: Path) -> None:
    log()
    log("CENARIO 8 - tabelas de backup do onprem ausentes no destino (migracao 030)")
    cfg = load_config(cfg_path)
    for table in BACKUP_TABLES_030:
        check(f"{table} continua excluida por config", table in cfg.excluded_table_names)
        check(f"{table} fora de collation_exceptions", table not in cfg.collation_exceptions)

    for table in BACKUP_TABLES_030:
        target_sql(f"DROP TABLE IF EXISTS `{table}`")
    code, output = run_sync(["--config", str(cfg_path)])
    check("dry-run termina OK sem as tabelas de backup no destino", code == 0, output[-2000:])
    # A mensagem de abort do preflight (o motivo em excluded_tables cita o mesmo texto entre aspas).
    check("preflight nao acusa tabela ausente",
          "Tabelas ausentes no Aurora (schema fora de paridade" not in output
          and "tabela(s) com o mesmo conjunto de colunas nos dois lados" in output, output[-2000:])
    check("plano continua vazio", "PLANO VAZIO" in output, output[-2000:])


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="mantem o container de teste no fim")
    args = parser.parse_args()

    log("=" * 78)
    log("AUTO-TESTE tools/onprem-sync (nenhuma credencial AWS, nenhum acesso ao Aurora)")
    log("=" * 78)

    start_container()
    load_schema_from_onprem()
    install_fake_aws()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_path = make_config(tmp_path)
        backup_dir = tmp_path / "backups"

        try:
            scenario_initial_load(cfg_path, backup_dir)
            scenario_idempotency(cfg_path)
            scenario_reconciliation(cfg_path, backup_dir)
            scenario_auto_increment(cfg_path)
            scenario_backup_restore(cfg_path, backup_dir)
            scenario_guards(cfg_path)
            scenario_legacy_tables(cfg_path)
            scenario_backup_tables_dropped_on_target(cfg_path)
        finally:
            if not args.keep:
                destroy_container()
            else:
                log(f"\n  container {CONTAINER} mantido (porta {PORT}).")

    log()
    log("=" * 78)
    if _failures:
        log(f"RESULTADO: {len(_failures)} verificacao(oes) FALHOU/FALHARAM:")
        for f in _failures:
            log(f"  - {f}")
        return 1
    log("RESULTADO: todas as verificacoes passaram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
