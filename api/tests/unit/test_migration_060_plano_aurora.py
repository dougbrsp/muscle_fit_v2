"""Plano da migração 060 (coeficientes das técnicas + backfill do vínculo
série -> técnica -> âncora - Fase 5, PR 2) contra o recorte REAL da Aurora.

Mesmo recorte composto do teste da 059 (032_033 + colunas de
`exercise_sets` da 026), sem leitura nova da AWS. As leituras de dados
(catálogo pendente e contagens) são respondidas pelo teste.

Rodar: cd api && python -m pytest tests/unit/test_migration_060_plano_aurora.py
"""

from __future__ import annotations

import functools
from decimal import Decimal

import pytest

from app import migrate as m
from tests.unit.test_migration_059_plano_aurora import _RecordingDB as _RecordingDB059
from tests.unit.test_migration_059_plano_aurora import _column, _convergido, _sem_catalogo, _snapshot

MIGRATION_060 = m.DEFAULT_MIGRATIONS_DIR / "060_tecnicas_coeficientes_e_backfill.py"
TODOS = ("drop_set", "rest_pause", "forced_reps", "negative_reps", "partial_reps", "cheat_reps", "fst7", "gvt")


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION_060).__globals__


class _DB(_RecordingDB059):
    def __init__(self, snapshot, *, dry_run=False, pendentes=TODOS, tecnica=0, ancora=0):
        super().__init__(snapshot, dry_run=dry_run)
        self.pendentes = pendentes
        self.counts = {"technique_code IS NULL": tecnica, "anchor_set_number IS NULL": ancora}

    def query(self, sql, params=None):
        if sql == _module()["PENDING_CALIBRATION_SQL"]:
            self.queries.append(sql)
            return [{"code": c} for c in self.pendentes]
        return super().query(sql, params)


def _com_059_aplicada() -> dict:
    return _convergido()


def _com_060_aplicada() -> dict:
    snap = _convergido()
    snap["columns"] += [
        dict(_column("max_equivalent_per_anchor", "decimal(4,2)"), t="advanced_techniques"),
        dict(_column("default_sets_count", "int"), t="advanced_techniques"),
    ]
    return snap


def _sqls(statements):
    return [" ".join(sql.split()) for sql, _ in statements]


def _updates(statements):
    return [p for sql, p in statements if sql == _module()["UPDATE_COEFFICIENTS_SQL"]]


def test_coeficientes_da_tabela_do_plano_para_os_8_codigos_do_catalogo():
    c = _module()["COEFFICIENTS"]
    assert set(c) == set(TODOS)
    assert c["drop_set"] == (Decimal("0.50"), Decimal("1.00"), Decimal("1.15"), None)
    assert c["rest_pause"] == (Decimal("0.40"), Decimal("1.00"), Decimal("1.10"), None)
    assert c["negative_reps"][2] == Decimal("1.25")
    assert c["fst7"][3] == 7 and c["gvt"][3] == 10
    assert c["fst7"][:2] == (Decimal("0.00"), Decimal("0.00"))


def test_producao_depois_da_059_planeja_ddl_calibracao_e_backfill():
    db = _DB(_com_059_aplicada(), tecnica=40, ancora=38)
    statements = _module()["plan"](db)
    sqls = _sqls(statements)
    assert sqls[0] == "ALTER TABLE advanced_techniques ADD COLUMN max_equivalent_per_anchor DECIMAL(4,2) NULL"
    assert sqls[1] == "ALTER TABLE advanced_techniques ADD COLUMN default_sets_count INT NULL"
    assert [p[-1] for p in _updates(statements)] == list(TODOS)
    assert sqls[-2].startswith("UPDATE exercise_sets s JOIN exercises e")
    assert sqls[-1].startswith("UPDATE exercise_sets s JOIN ( SELECT a.id AS set_id, MAX(w.set_number)")
    assert len(statements) == 2 + 8 + 2
    assert not db.executed


def test_reexecucao_convergida_nao_escreve_nada():
    db = _DB(_com_060_aplicada(), pendentes=(), tecnica=0, ancora=0)
    assert _module()["plan"](db) == []


def test_calibracao_so_toca_linhas_ainda_nao_calibradas():
    db = _DB(_com_060_aplicada(), pendentes=("gvt", "tecnica_nova"))
    statements = _module()["plan"](db)
    assert [p[-1] for p in _updates(statements)] == ["gvt"]
    assert all("IS NULL" in sql for sql, _ in statements if sql == _module()["UPDATE_COEFFICIENTS_SQL"])


def test_backfill_so_quando_ha_pendencia():
    db = _DB(_com_060_aplicada(), pendentes=(), tecnica=3, ancora=0)
    sqls = _sqls(_module()["plan"](db))
    assert len(sqls) == 1 and "SET s.technique_code" in sqls[0]


def test_backfill_e_idempotente_e_so_toca_aplicacoes_de_tecnica():
    g = _module()
    tecnica = " ".join(g["BACKFILL_TECHNIQUE_SQL"].split())
    ancora = " ".join(g["BACKFILL_ANCHOR_SQL"].split())
    assert "WHERE s.technique_code IS NULL" in tecnica
    assert "a.anchor_set_number IS NULL" in ancora
    for sql in (tecnica, ancora):
        assert "'rest_pause'" in sql and "'drop_set'" in sql and "'forced_rep'" in sql and "'dropset'" in sql
    # técnica da ficha só vale se o catálogo grava o mesmo set_type; senão, padrão.
    assert "t.set_type = CASE" in tecnica and "ELSE 'forced_reps'" in tecnica
    # âncora: última série de trabalho ANTERIOR do mesmo exercício.
    assert "w.set_number < a.set_number" in ancora and "MAX(w.set_number)" in ancora
    assert "GROUP BY a.id" in ancora


def test_dry_run_com_a_059_pendente_planeja_tudo_sem_contar():
    db = _DB(_snapshot(), dry_run=True)
    statements = _module()["plan"](db)
    assert len(statements) == 2 + 8 + 2
    assert not any("COUNT(*)" in q for q in db.queries)


def test_execucao_real_sem_a_059_falha_antes_de_escrever():
    Erro = _module()["TechniqueCoefficientsError"]
    db = _DB(_snapshot())
    with pytest.raises(Erro, match="059"):
        _module()["up"](db)
    assert not db.executed


def test_coluna_em_outro_formato_falha():
    Erro = _module()["TechniqueCoefficientsError"]
    snap = _com_059_aplicada()
    snap["columns"].append(dict(_column("max_equivalent_per_anchor", "decimal(5,2)"), t="advanced_techniques"))
    with pytest.raises(Erro, match="decimal"):
        _module()["plan"](_DB(snap))


def test_sem_prepare_set_ou_multi_statement():
    sqls = " ".join(v for k, v in _module().items() if k.endswith("_SQL")).upper()
    for proibido in ("PREPARE", "SET @", ";"):
        assert proibido not in sqls


def test_down_restaura_coeficientes_e_remove_colunas():
    db = _DB(_com_060_aplicada())
    statements = _module()["plan_down"](db)
    sqls = _sqls(statements)
    assert sqls[-2:] == [
        "ALTER TABLE advanced_techniques DROP COLUMN max_equivalent_per_anchor",
        "ALTER TABLE advanced_techniques DROP COLUMN default_sets_count",
    ]
    assert len(statements) == 8 + 2
    _module()["down"](db)
    assert len(db.executed) == 10


def test_dry_run_do_lote_com_a_031_pendente_planeja_colunas_e_coeficientes():
    db = _DB(_sem_catalogo(), dry_run=True, pendentes=())
    statements = _module()["plan"](db)
    # catálogo ainda por criar: 2 ADD COLUMN + os 8 coeficientes + 2 backfills sem contagem
    assert len(statements) == 2 + 8 + 2
    assert _module()["PENDING_CALIBRATION_SQL"] not in db.queries
    assert not any("COUNT(*)" in q for q in db.queries)
    assert not db.executed


def test_execucao_real_sem_a_031_falha_antes_de_escrever():
    Erro = _module()["TechniqueCoefficientsError"]
    db = _DB(_sem_catalogo())
    with pytest.raises(Erro, match="031"):
        _module()["up"](db)
    assert not db.executed
