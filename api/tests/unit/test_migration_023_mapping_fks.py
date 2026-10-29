"""Unit da migracao 023 (reaponta FKs de mapping_id para exercise_catalog) com
um fake de schema que imita as regras do MySQL relevantes aqui: FK exige tipo
identico (ER 3780) e coluna presa a FK nao pode ser MODIFY-ada.

Rodar: cd api && python -m pytest tests/unit/test_migration_023_mapping_fks.py
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Optional

import pytest

from app import migrate as m

REPO_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"
FILENAME = "023_mapping_fks_exercise_catalog.py"

GE, EEM = "gym_equipment", "equipment_exercise_mappings"


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _col(col_type: str, nullable: str = "NO", default: Any = None, extra: str = "", comment: str = "") -> dict:
    return {"column_type": col_type, "is_nullable": nullable, "column_default": default, "extra": extra, "column_comment": comment}


def _fk(name: str, ref: str, delete: str = "CASCADE", update: str = "NO ACTION") -> dict:
    return {"constraint_name": name, "referenced_table": ref, "referenced_column": "id", "delete_rule": delete, "update_rule": update}


class Schema:
    def __init__(self) -> None:
        self.columns: dict[str, dict[str, dict]] = {"exercise_catalog": {"id": _col("bigint unsigned")}}
        self.fks: dict[str, list[dict]] = {}
        self.indexes: dict[str, dict[str, str]] = {}  # tabela -> indice -> 1a coluna
        self.orphans: dict[str, int] = {}
        self.ddl: list[str] = []

    def add_table(self, table: str, col_type: str, fks: list[dict], **col_kwargs: Any) -> None:
        self.columns[table] = {"id": _col("bigint"), "mapping_id": _col(col_type, **col_kwargs)}
        self.fks[table] = fks
        self.indexes[table] = {"PRIMARY": "id", f"idx_{'gym_equipment' if table == GE else 'eem'}_mapping": "mapping_id"}


def legacy_local() -> Schema:
    """Estado do 001_schema.sql."""
    s = Schema()
    s.add_table(GE, "bigint", [_fk("fk_gym_equipment_mapping", "exercise_zepp_mappings")])
    s.add_table(EEM, "bigint", [_fk("fk_eem_mapping", "exercise_zepp_mappings")])
    return s


def aurora_like() -> Schema:
    """Schema documentado da Aurora: tipos iguais e FK canonica."""
    s = Schema()
    s.add_table(GE, "bigint unsigned", [_fk("gym_equipment_ibfk_2", "exercise_catalog")])
    s.add_table(EEM, "bigint unsigned", [_fk("equipment_exercise_mappings_ibfk_2", "exercise_catalog")])
    return s


class FakeCursor:
    def __init__(self, s: Schema):
        self.s = s
        self._rows: list[dict] = []
        self.rowcount = 0

    def execute(self, sql: str, params: Any = None) -> None:
        s, q, p = self.s, _norm(sql), list(params or [])
        self._rows = []

        def count(n: int) -> None:
            self._rows = [{"n": n}]

        if q == "SELECT 1 AS n":
            count(1)
        elif "FROM information_schema.TABLES" in q:
            count(int(p[0] in s.columns))
        elif "FROM information_schema.COLUMNS" in q and "COLUMN_TYPE" in q:
            info = s.columns.get(p[0], {}).get(p[1])
            self._rows = [dict(info)] if info else []
        elif "FROM information_schema.COLUMNS" in q:
            count(int(p[1] in s.columns.get(p[0], {})))
        elif "REFERENTIAL_CONSTRAINTS" in q:
            self._rows = sorted((dict(fk) for fk in s.fks.get(p[0], [])), key=lambda fk: fk["constraint_name"])
        elif "FROM information_schema.STATISTICS" in q and "SEQ_IN_INDEX = 1" in q:
            count(int(p[1] in s.indexes.get(p[0], {}).values()))
        elif "FROM information_schema.STATISTICS" in q:
            count(int(p[1] in s.indexes.get(p[0], {})))
        elif "NOT EXISTS (SELECT 1 FROM exercise_catalog" in q:
            count(s.orphans.get(re.match(r"SELECT COUNT\(\*\) AS n FROM (\w+) t", q).group(1), 0))
        else:
            self._ddl(q)

    def _ddl(self, q: str) -> None:
        s = self.s
        s.ddl.append(q)
        if mt := re.fullmatch(r"ALTER TABLE (\w+) DROP FOREIGN KEY `(\w+)`", q):
            before = len(s.fks[mt.group(1)])
            s.fks[mt.group(1)] = [fk for fk in s.fks[mt.group(1)] if fk["constraint_name"] != mt.group(2)]
            assert len(s.fks[mt.group(1)]) == before - 1, f"FK inexistente: {q}"
        elif mt := re.fullmatch(r"ALTER TABLE (\w+) MODIFY COLUMN mapping_id BIGINT UNSIGNED (NULL|NOT NULL)", q):
            if s.fks[mt.group(1)]:
                raise RuntimeError("(1832) Cannot change column 'mapping_id': used in a foreign key constraint")
            s.columns[mt.group(1)]["mapping_id"] = _col("bigint unsigned", "YES" if mt.group(2) == "NULL" else "NO")
        elif mt := re.fullmatch(r"ALTER TABLE (\w+) ADD INDEX (\w+) \(mapping_id\)", q):
            s.indexes[mt.group(1)][mt.group(2)] = "mapping_id"
        elif mt := re.fullmatch(
            r"ALTER TABLE (\w+) ADD CONSTRAINT (\w+) FOREIGN KEY \(mapping_id\) REFERENCES (\w+) \(id\) "
            r"ON DELETE (CASCADE|SET NULL|RESTRICT|NO ACTION) ON UPDATE (CASCADE|SET NULL|RESTRICT|NO ACTION)",
            q,
        ):
            table, name, ref, delete, update = mt.groups()
            if s.columns[table]["mapping_id"]["column_type"] != s.columns[ref]["id"]["column_type"]:
                raise RuntimeError("(3780) Referencing column 'mapping_id' and referenced column 'id' are incompatible.")
            if any(fk["constraint_name"] == name for fks in s.fks.values() for fk in fks):
                raise RuntimeError("(1826) Duplicate foreign key constraint name")
            s.fks[table].append(_fk(name, ref, delete, update))
        else:
            raise AssertionError(f"SQL inesperado no fake: {q}")

    def fetchone(self) -> Optional[dict]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return list(self._rows)


class FakeConn:
    def __init__(self, s: Schema):
        self.s = s

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.s)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def run_023(s: Schema, dry_run: bool = False) -> list[str]:
    up = m.load_python_migration(REPO_MIGRATIONS_DIR / FILENAME)
    db = m.MigrationDB(FakeConn(s), dry_run=dry_run)
    up(db)
    return db.statements


def refs(s: Schema, table: str) -> list[tuple[str, str, str, str]]:
    return [(fk["constraint_name"], fk["referenced_table"], fk["delete_rule"], fk["update_rule"]) for fk in s.fks[table]]


LOCAL_PLAN = [
    "ALTER TABLE gym_equipment DROP FOREIGN KEY `fk_gym_equipment_mapping`",
    "ALTER TABLE gym_equipment MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL",
    "ALTER TABLE gym_equipment ADD CONSTRAINT fk_gym_equipment_mapping_catalog FOREIGN KEY (mapping_id) "
    "REFERENCES exercise_catalog (id) ON DELETE CASCADE ON UPDATE NO ACTION",
    "ALTER TABLE equipment_exercise_mappings DROP FOREIGN KEY `fk_eem_mapping`",
    "ALTER TABLE equipment_exercise_mappings MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL",
    "ALTER TABLE equipment_exercise_mappings ADD CONSTRAINT fk_eem_mapping_catalog FOREIGN KEY (mapping_id) "
    "REFERENCES exercise_catalog (id) ON DELETE CASCADE ON UPDATE NO ACTION",
]


# ---------------------------------------------------------------------------


def test_fk_legada_local_converte_tipo_e_reaponta_e_reexecucao_e_noop() -> None:
    s = legacy_local()
    assert run_023(s) == LOCAL_PLAN
    assert s.ddl == LOCAL_PLAN
    assert refs(s, GE) == [("fk_gym_equipment_mapping_catalog", "exercise_catalog", "CASCADE", "NO ACTION")]
    assert refs(s, EEM) == [("fk_eem_mapping_catalog", "exercise_catalog", "CASCADE", "NO ACTION")]
    assert s.columns[GE]["mapping_id"]["column_type"] == "bigint unsigned"

    s.ddl.clear()
    assert run_023(s) == []
    assert s.ddl == []


def test_fk_ja_canonica_e_noop() -> None:
    s = aurora_like()
    assert run_023(s) == []
    assert s.ddl == []


def test_tipos_compativeis_adiciona_antes_de_remover_e_preserva_regras() -> None:
    s = Schema()
    s.add_table(GE, "bigint(20) unsigned", [_fk("ge_ibfk_x", "exercise_zepp_mappings", "RESTRICT", "CASCADE")])
    s.columns["exercise_catalog"]["id"] = _col("bigint(20) unsigned")
    s.add_table(EEM, "bigint(20) unsigned", [_fk("eem_ibfk_x", "exercise_zepp_mappings", "SET NULL", "NO ACTION")], nullable="YES")
    assert run_023(s) == [
        "ALTER TABLE gym_equipment ADD CONSTRAINT fk_gym_equipment_mapping_catalog FOREIGN KEY (mapping_id) "
        "REFERENCES exercise_catalog (id) ON DELETE RESTRICT ON UPDATE CASCADE",
        "ALTER TABLE gym_equipment DROP FOREIGN KEY `ge_ibfk_x`",
        "ALTER TABLE equipment_exercise_mappings ADD CONSTRAINT fk_eem_mapping_catalog FOREIGN KEY (mapping_id) "
        "REFERENCES exercise_catalog (id) ON DELETE SET NULL ON UPDATE NO ACTION",
        "ALTER TABLE equipment_exercise_mappings DROP FOREIGN KEY `eem_ibfk_x`",
    ]
    assert refs(s, GE) == [("fk_gym_equipment_mapping_catalog", "exercise_catalog", "RESTRICT", "CASCADE")]
    assert refs(s, EEM) == [("fk_eem_mapping_catalog", "exercise_catalog", "SET NULL", "NO ACTION")]


def test_orfaos_falham_antes_de_qualquer_ddl_inclusive_da_outra_tabela() -> None:
    s = legacy_local()
    s.orphans[EEM] = 3
    with pytest.raises(Exception, match=r"equipment_exercise_mappings\.mapping_id tem 3 linha\(s\)") as info:
        run_023(s)
    assert "Nenhum DDL executado" in str(info.value)
    assert s.ddl == []  # nem o DDL de gym_equipment (planejado antes) rodou
    assert refs(s, GE) == [("fk_gym_equipment_mapping", "exercise_zepp_mappings", "CASCADE", "NO ACTION")]


def test_orfaos_pelo_runner_viram_migration_execution_error_sem_registrar(tmp_path: Path) -> None:
    shutil.copy2(REPO_MIGRATIONS_DIR / FILENAME, tmp_path / FILENAME)
    s = legacy_local()
    s.orphans[GE] = 1
    with pytest.raises(m.MigrationExecutionError, match=rf"{FILENAME}: 023: gym_equipment\.mapping_id tem 1 linha"):
        m.migrate(conn_factory=lambda: FakeConn(s), migrations_dir=tmp_path, dry_run=True)
    assert s.ddl == []


@pytest.mark.parametrize(
    ("child", "parent"),
    [("int", "bigint unsigned"), ("bigint unsigned", "bigint"), ("int unsigned", "bigint unsigned")],
)
def test_tipo_incompativel_diferente_do_conhecido_para_sem_alterar_coluna(child: str, parent: str) -> None:
    s = legacy_local()
    s.columns[GE]["mapping_id"] = _col(child)
    s.columns["exercise_catalog"]["id"] = _col(parent)
    if parent == "bigint":
        s.columns[EEM]["mapping_id"] = _col("bigint")
    with pytest.raises(Exception, match="tipo incompativel para FK"):
        run_023(s)
    assert s.ddl == []


def test_conversao_recusada_se_modify_descartaria_atributos() -> None:
    s = legacy_local()
    s.columns[EEM]["mapping_id"] = _col("bigint", comment="id do exercicio")
    with pytest.raises(Exception, match="column_comment"):
        run_023(s)
    assert s.ddl == []


def test_retomada_apos_drop_usa_regras_do_001_e_apos_add_so_remove_legada() -> None:
    s = legacy_local()
    s.fks[GE] = []  # parou depois do DROP (e antes do MODIFY)
    s.fks[EEM].append(_fk("fk_eem_mapping_catalog", "exercise_catalog"))  # parou entre ADD e DROP
    s.columns[EEM]["mapping_id"] = _col("bigint unsigned")
    assert run_023(s) == [
        "ALTER TABLE gym_equipment MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL",
        "ALTER TABLE gym_equipment ADD CONSTRAINT fk_gym_equipment_mapping_catalog FOREIGN KEY (mapping_id) "
        "REFERENCES exercise_catalog (id) ON DELETE CASCADE ON UPDATE NO ACTION",
        "ALTER TABLE equipment_exercise_mappings DROP FOREIGN KEY `fk_eem_mapping`",
    ]
    assert run_023(s) == []


def test_cria_indice_quando_nao_ha_indice_comecando_por_mapping_id() -> None:
    s = legacy_local()
    s.indexes[GE] = {"PRIMARY": "id", "uq_gym_equipment_gym_mapping_equipment": "gym_id"}
    statements = run_023(s)
    assert statements[:3] == [
        "ALTER TABLE gym_equipment DROP FOREIGN KEY `fk_gym_equipment_mapping`",
        "ALTER TABLE gym_equipment MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL",
        "ALTER TABLE gym_equipment ADD INDEX idx_gym_equipment_mapping (mapping_id)",
    ]


def test_regras_divergentes_entre_fks_legadas_falham() -> None:
    s = legacy_local()
    s.fks[GE].append(_fk("fk_outra", "exercise_zepp_mappings", "RESTRICT"))
    with pytest.raises(Exception, match="regras ON DELETE/ON UPDATE diferentes"):
        run_023(s)
    assert s.ddl == []


def test_tabela_ausente_e_ignorada() -> None:
    s = legacy_local()
    del s.columns[EEM]
    assert run_023(s) == LOCAL_PLAN[:3]


def test_dry_run_pelo_runner_lista_statements_sem_executar(tmp_path: Path) -> None:
    shutil.copy2(REPO_MIGRATIONS_DIR / FILENAME, tmp_path / FILENAME)
    s = legacy_local()
    report = m.migrate(conn_factory=lambda: FakeConn(s), migrations_dir=tmp_path, dry_run=True)
    assert [a.filename for a in report.applied] == [FILENAME]
    assert report.applied[0].statements == LOCAL_PLAN
    assert s.ddl == []
    assert refs(s, GE) == [("fk_gym_equipment_mapping", "exercise_zepp_mappings", "CASCADE", "NO ACTION")]
