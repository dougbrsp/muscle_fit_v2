"""Spike: valida o shim `rds_data_shim.py` contra o cluster Aurora real
(`musclefit-db`, sa-east-1), ja com os dados migrados da Fase 1.

Cobre padroes de acesso a dados reais extraidos de
`fitness-platform/api/app/services.py` (recuperacao muscular) e
`fitness-platform/api/app/analytics.py` (volume/analytics) do backend de
referencia:

1. SELECT simples com WHERE + parametro (padrao de `source_id`, services.py:71-76).
2. SELECT com JOINs + GROUP BY + agregacao (padrao de
   `compute_training_trend`/`compute_rolling_weekly_trend`, analytics.py:212-238,
   265-291), incluindo `DATE_FORMAT(..., '%%Y-%%m-%%d')` e `LIKE '%%muscul%%'`
   - o caso que exercita a traducao `%%` -> `%` do shim.
3. INSERT com retorno de id via `lastrowid` (padrao de `source_id`,
   services.py:78-82) - tabela dedicada `spike_test_table`, criada e
   derrubada por este script, SEM tocar dado real.
4. UPDATE + `rowcount`.
5. Transacao com commit real (2 INSERTs relacionados, como uma
   activity + exercise) e transacao com rollback (garante que nada e
   persistido).
6. Parametro grande (>65536 chars combinados de SQL) via `parameters`
   tipados - reproduz a mitigacao ja usada na migracao de dados da Fase 1
   para colunas JSON grandes (`context_snapshot`/`recommendation_markdown`),
   confirmando que o shim tambem se beneficia dela por default (SQL fica
   pequeno, o valor grande vai em `parameters`, nao inline).

So faz leitura nas tabelas reais (`activities`, `exercises`, `exercise_sets`,
`sources`) - nenhum INSERT/UPDATE/DELETE roda contra elas. Escrita e
exclusivamente em `spike_test_table`.

Uso:
    # gerar credenciais temporarias (ver instrucoes do projeto) e entao:
    python test_spike.py
"""

from __future__ import annotations

import os
import sys
import traceback
from decimal import Decimal

from rds_data_shim import Connection, DataApiError, transaction

RESOURCE_ARN = "arn:aws:rds:sa-east-1:930413537709:cluster:musclefit-db"
SECRET_ARN = (
    "arn:aws:secretsmanager:sa-east-1:930413537709:"
    "secret:rds!cluster-b3317b3e-2be1-41f8-9c18-132e4847112f-Yy88bJ"
)
DATABASE = "musclefit"
REGION = "sa-east-1"

PASS = "OK"
FAIL = "FALHOU"

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        results.append((name, True, ""))
        print(f"[{PASS}] {name}")
    except Exception as exc:  # noqa: BLE001
        results.append((name, False, f"{exc}\n{traceback.format_exc()}"))
        print(f"[{FAIL}] {name}: {exc}")


def new_conn() -> Connection:
    return Connection(RESOURCE_ARN, SECRET_ARN, DATABASE, REGION)


# ---------------------------------------------------------------------------
# 1. SELECT simples com WHERE + parametro (padrao services.py:source_id)
# ---------------------------------------------------------------------------
def test_select_simple():
    conn = new_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, type FROM sources WHERE name = %s", ("intervals.icu",))
        row = cur.fetchone()
        # Pode nao existir - o que importa e o shim rodar e devolver dict ou None.
        assert row is None or isinstance(row, dict), f"esperado dict ou None, veio {type(row)}"
        if row is not None:
            assert "id" in row and "name" in row
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. SELECT com JOINs + GROUP BY + DATE_FORMAT com %% + LIKE com %%
#    (padrao analytics.py:compute_training_trend / compute_rolling_weekly_trend)
# ---------------------------------------------------------------------------
def test_select_join_groupby_percent_escape():
    conn = new_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                DATE_FORMAT(a.local_date, '%%Y-%%m-%%d') AS period,
                COUNT(DISTINCT a.id) AS workouts,
                COUNT(es.id) AS sets_count,
                COALESCE(SUM(COALESCE(es.reps, 0)), 0) AS reps_total,
                COALESCE(SUM(COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0)), 0) AS volume_kg,
                ROUND(AVG(es.rpe), 2) AS avg_rpe
            FROM activities a
            LEFT JOIN exercises e ON e.activity_id = a.id
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id
            WHERE a.local_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
              AND (
                LOWER(a.activity_type) LIKE '%%muscul%%'
                OR LOWER(a.activity_type) LIKE '%%strength%%'
              )
            GROUP BY period
            ORDER BY period
            LIMIT 10
            """,
            (365,),
        )
        rows = cur.fetchall()
        assert isinstance(rows, list)
        for r in rows:
            assert set(r.keys()) >= {"period", "workouts", "sets_count", "reps_total", "volume_kg", "avg_rpe"}
            assert isinstance(r["period"], str) and len(r["period"]) == 10  # 'YYYY-MM-DD'
        print(f"    -> {len(rows)} linhas agregadas por dia (ultimos 365 dias, treino de forca)")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. INSERT com lastrowid (tabela dedicada spike_test_table)
# ---------------------------------------------------------------------------
def _create_test_table(conn: Connection) -> None:
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS spike_test_table")
    cur.execute(
        """
        CREATE TABLE spike_test_table (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            weight_kg DECIMAL(6,2) NULL,
            note MEDIUMTEXT NULL,
            created_at DATETIME NOT NULL
        )
        """
    )
    conn.commit()


def _drop_test_table(conn: Connection) -> None:
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS spike_test_table")
    conn.commit()


def test_insert_lastrowid():
    import datetime

    conn = new_conn()
    try:
        _create_test_table(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO spike_test_table (name, weight_kg, note, created_at) VALUES (%s, %s, %s, %s)",
            ("spike-row-1", Decimal("82.35"), None, datetime.datetime(2026, 9, 3, 10, 0, 0)),
        )
        first_id = cur.lastrowid
        assert isinstance(first_id, int) and first_id > 0, f"lastrowid invalido: {first_id!r}"

        cur.execute(
            "INSERT INTO spike_test_table (name, weight_kg, note, created_at) VALUES (%s, %s, %s, %s)",
            ("spike-row-2", None, "sem peso", datetime.datetime(2026, 9, 3, 10, 5, 0)),
        )
        second_id = cur.lastrowid
        assert second_id == first_id + 1, f"esperado id sequencial {first_id + 1}, veio {second_id}"

        cur.execute("SELECT id, name, weight_kg, note FROM spike_test_table ORDER BY id")
        rows = cur.fetchall()
        assert len(rows) == 2
        assert rows[0]["name"] == "spike-row-1"
        assert rows[1]["note"] == "sem peso"
        assert rows[1]["weight_kg"] is None
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. UPDATE + rowcount
# ---------------------------------------------------------------------------
def test_update_rowcount():
    conn = new_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE spike_test_table SET weight_kg = %s WHERE name = %s",
            (Decimal("90.00"), "spike-row-2"),
        )
        assert cur.rowcount == 1, f"esperado rowcount 1, veio {cur.rowcount}"

        cur.execute("SELECT weight_kg FROM spike_test_table WHERE name = %s", ("spike-row-2",))
        row = cur.fetchone()
        assert row is not None
        assert Decimal(row["weight_kg"]) == Decimal("90.00")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5a. Transacao com commit real (2 INSERTs relacionados, como activity+exercise)
# ---------------------------------------------------------------------------
def test_transaction_commit():
    with transaction(RESOURCE_ARN, SECRET_ARN, DATABASE, REGION) as conn:
        cur = conn.cursor()
        import datetime

        cur.execute(
            "INSERT INTO spike_test_table (name, weight_kg, note, created_at) VALUES (%s, %s, %s, %s)",
            ("spike-parent", None, "parent", datetime.datetime(2026, 9, 3, 11, 0, 0)),
        )
        parent_id = cur.lastrowid
        cur.execute(
            "INSERT INTO spike_test_table (name, weight_kg, note, created_at) VALUES (%s, %s, %s, %s)",
            (f"spike-child-of-{parent_id}", Decimal("10.5"), None, datetime.datetime(2026, 9, 3, 11, 1, 0)),
        )

    # nova conexao/transacao para confirmar persistencia
    conn2 = new_conn()
    try:
        cur2 = conn2.cursor()
        cur2.execute("SELECT COUNT(*) AS n FROM spike_test_table WHERE name LIKE %s", ("spike-child-of-%",))
        row = cur2.fetchone()
        assert row["n"] == 1, f"esperado 1 linha child persistida apos commit, veio {row['n']}"
        conn2.commit()
    finally:
        conn2.close()


# ---------------------------------------------------------------------------
# 5b. Transacao com rollback (garante que nada fica persistido)
# ---------------------------------------------------------------------------
def test_transaction_rollback():
    class _Boom(Exception):
        pass

    try:
        with transaction(RESOURCE_ARN, SECRET_ARN, DATABASE, REGION) as conn:
            cur = conn.cursor()
            import datetime

            cur.execute(
                "INSERT INTO spike_test_table (name, weight_kg, note, created_at) VALUES (%s, %s, %s, %s)",
                ("spike-should-not-persist", None, None, datetime.datetime(2026, 9, 3, 12, 0, 0)),
            )
            raise _Boom("forcando rollback")
    except _Boom:
        pass

    conn2 = new_conn()
    try:
        cur2 = conn2.cursor()
        cur2.execute(
            "SELECT COUNT(*) AS n FROM spike_test_table WHERE name = %s",
            ("spike-should-not-persist",),
        )
        row = cur2.fetchone()
        assert row["n"] == 0, f"esperado 0 linhas apos rollback, veio {row['n']}"
        conn2.commit()
    finally:
        conn2.close()


# ---------------------------------------------------------------------------
# 6. Parametro grande via `parameters` tipados (evita limite de 65536 chars
#    de texto SQL literal do execute_statement - mitigacao ja usada na
#    migracao de dados da Fase 1 para context_snapshot/recommendation_markdown)
# ---------------------------------------------------------------------------
def test_large_json_param_avoids_statement_limit():
    import datetime
    import json

    big_payload = json.dumps({"blob": "x" * 80_000, "kind": "spike-large-json"})
    assert len(big_payload) > 65_536, "payload de teste precisa exceder o limite do Data API para o teste fazer sentido"

    conn = new_conn()
    try:
        cur = conn.cursor()
        # O SQL literal (template) permanece pequeno - o valor grande vai
        # inteiramente dentro de `parameters`, fora da string de SQL, entao
        # o limite de 65536 chars (que se aplica ao TEXTO do statement, nao
        # ao valor do parametro) nao e atingido.
        cur.execute(
            "INSERT INTO spike_test_table (name, weight_kg, note, created_at) VALUES (%s, %s, %s, %s)",
            ("spike-large-json", None, big_payload, datetime.datetime(2026, 9, 3, 13, 0, 0)),
        )
        cur.execute("SELECT note FROM spike_test_table WHERE name = %s", ("spike-large-json",))
        row = cur.fetchone()
        assert row is not None
        assert len(row["note"]) == len(big_payload)
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    check("1. SELECT simples com WHERE + parametro", test_select_simple)
    check("2. SELECT JOINs + GROUP BY + DATE_FORMAT/LIKE com %%", test_select_join_groupby_percent_escape)
    check("3. INSERT com lastrowid", test_insert_lastrowid)
    check("4. UPDATE + rowcount", test_update_rowcount)
    check("5a. Transacao com commit (2 INSERTs relacionados)", test_transaction_commit)
    check("5b. Transacao com rollback (nada persiste)", test_transaction_rollback)
    check("6. Parametro grande (>65536 chars) via `parameters` tipados", test_large_json_param_avoids_statement_limit)

    # cleanup final, sempre - nunca deixar spike_test_table pra tras
    try:
        conn = new_conn()
        _drop_test_table(conn)
        conn.close()
        print("[cleanup] spike_test_table derrubada")
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] falhou ao derrubar spike_test_table: {exc}")

    failed = [r for r in results if not r[1]]
    print("\n--- resumo ---")
    for name, ok, _ in results:
        print(f"{PASS if ok else FAIL}: {name}")
    if failed:
        print(f"\n{len(failed)}/{len(results)} testes falharam")
        return 1
    print(f"\ntodos os {len(results)} testes passaram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
