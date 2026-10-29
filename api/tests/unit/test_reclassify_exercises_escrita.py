"""Unit (sem banco): `reclassify_exercises` so escreve quando o vinculo muda
e pagina a leitura; `reclassify_after_catalog_change` nao derruba a escrita
do catalogo; plano de indice da migracao 024."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from app import exercise_mapping_canonical as emc
from app import migrate as m

CATALOG = [
    {"id": 10, "exercise_name": "Supino reto", "keywords": None, "primary_group": "Peitoral", "secondary_group": None,
     "primary_muscles": None, "secondary_muscles": None},
    {"id": 20, "exercise_name": "Puxada alta", "keywords": None, "primary_group": "Costas", "secondary_group": None,
     "primary_muscles": None, "secondary_muscles": None},
]


class FakeDb:
    def __init__(self, exercises):
        self.exercises = [dict(row) for row in exercises]
        self.exercise_queries: list[tuple] = []
        self.updates: list[tuple] = []

    def query(self, sql, params=()):
        if "FROM `exercise_catalog`" in sql:
            return [dict(row) for row in CATALOG]
        if "FROM `exercise_aliases`" in sql or "FROM `exercise_external_mappings`" in sql:
            return []
        assert "FROM `exercises`" in sql, sql
        self.exercise_queries.append((sql, tuple(params)))
        args = list(params)
        limit = args.pop()
        rows = sorted(self.exercises, key=lambda row: -row["id"])
        if "mapping_id IS NULL" in sql:
            rows = [row for row in rows if row["mapping_id"] is None]
        if "id < %s" in sql:
            rows = [row for row in rows if row["id"] < args[0]]
        return [dict(row) for row in rows[:limit]]

    def execute(self, sql, params=()):
        assert sql == "UPDATE `exercises` SET mapping_id=%s WHERE id=%s", sql
        self.updates.append(tuple(params))
        target, exercise_id = params
        for row in self.exercises:
            if row["id"] == exercise_id:
                row["mapping_id"] = target
        return 1


def _run(db, **kwargs):
    return emc.reclassify_exercises(query_fn=db.query, execute_fn=db.execute, **kwargs)


def _exercises():
    return [
        {"id": 1, "original_name": "Supino reto", "mapping_id": None},  # casa -> grava
        {"id": 2, "original_name": "Rosca XYZ", "mapping_id": None},  # sem correspondencia -> NAO grava
        {"id": 3, "original_name": "Puxada alta", "mapping_id": 20},  # ja correto
        {"id": 4, "original_name": "Coisa estranha", "mapping_id": 10},  # vinculo sem correspondencia
    ]


def test_only_missing_so_grava_o_que_casa():
    db = FakeDb(_exercises())
    result = _run(db, limit=100, only_missing=True)
    assert db.updates == [(10, 1)]
    assert (result["processed"], result["updated"], result["unresolved"], result["written"]) == (2, 1, 1, 1)
    assert result["unresolved_examples"] == [{"id": 2, "original_name": "Rosca XYZ"}]


def test_segunda_execucao_nao_emite_update():
    db = FakeDb(_exercises())
    _run(db, limit=100, only_missing=True)
    db.updates.clear()
    result = _run(db, limit=100, only_missing=True)
    assert db.updates == [] and result["written"] == 0


def test_reclassificacao_completa_so_grava_o_que_muda():
    db = FakeDb(_exercises())
    result = _run(db, limit=100, only_missing=False)
    # 1: NULL -> 10; 4: 10 -> NULL (mesma regra de antes); 2 e 3 sem mudanca.
    assert sorted(db.updates, key=lambda u: u[1]) == [(10, 1), (None, 4)]
    assert (result["processed"], result["updated"], result["unresolved"], result["written"]) == (4, 2, 2, 2)


def test_pagina_por_id_e_respeita_limite():
    db = FakeDb([{"id": i, "original_name": "Rosca XYZ", "mapping_id": None} for i in range(1, 2501)])
    result = _run(db, limit=2200, only_missing=True, page_size=1000)
    assert result["processed"] == 2200 and db.updates == []
    # ids 2500..1501, depois 1500..501 (cursor = ultimo id lido), depois 500..301.
    assert [params for _, params in db.exercise_queries] == [(1000,), (1501, 1000), (501, 200)]


class ScopedFakeDb(FakeDb):
    """Leitura do escopo `names`: a query traz `link_unresolved` (vinculo
    nulo ou que nao resolve para exercise_catalog ativo)."""

    def __init__(self, exercises, unresolved_ids):
        super().__init__(exercises)
        self.unresolved_ids = set(unresolved_ids)

    def query(self, sql, params=()):
        if "link_unresolved" not in sql:
            return super().query(sql, params)
        self.exercise_queries.append((sql, tuple(params)))
        args = list(params)
        limit = args.pop()
        rows = sorted(self.exercises, key=lambda row: -row["id"])
        if "e.id < %s" in sql:
            rows = [row for row in rows if row["id"] < args[0]]
        return [
            {**row, "link_unresolved": 1 if row["mapping_id"] is None or row["id"] in self.unresolved_ids else 0}
            for row in rows[:limit]
        ]


def test_escopo_por_nome_so_toca_sem_vinculo_e_o_nome():
    exercises = _exercises() + [
        {"id": 5, "original_name": "supino  RETO!", "mapping_id": 20},  # nome do alvo (normalizado): move
        {"id": 6, "original_name": "Supino reto", "mapping_id": 20},  # mesmo nome, vinculo anterior: move
        {"id": 7, "original_name": "Puxada alta", "mapping_id": 10},  # vinculo valido de outro nome: intocado
    ]
    db = ScopedFakeDb(exercises, unresolved_ids=[])
    result = _run(db, limit=100, names=["Supino Reto"])
    # 1 (sem vinculo, casa), 5 e 6 (mesmo nome normalizado); 4 e 7 tem vinculo valido.
    assert sorted(db.updates, key=lambda u: u[1]) == [(10, 1), (10, 5), (10, 6)]
    assert result["processed"] == 4  # 1, 2 (sem vinculo), 5, 6
    assert all("link_unresolved" in sql for sql, _ in db.exercise_queries)


def test_escopo_por_nome_inclui_vinculo_que_nao_resolve_sem_apagar():
    exercises = _exercises() + [{"id": 8, "original_name": "Puxada alta", "mapping_id": 99}]  # 99: catalogo inativo
    db = ScopedFakeDb(exercises, unresolved_ids=[4, 8])
    result = _run(db, limit=100, names=["Nada a ver"])
    # 8: vinculo que nao resolve e casa -> corrige; 4: nao casa -> mantem (nunca vira NULL).
    assert sorted(db.updates, key=lambda u: u[1]) == [(10, 1), (20, 8)]
    assert result["processed"] == 4  # 1, 2, 4, 8


def test_associate_reclassifica_so_o_escopo_do_alias(monkeypatch):
    calls = []
    responses = iter([[{"id": 10, "name": "Supino reto"}], [], [], []])
    monkeypatch.setattr(emc, "query", lambda sql, params=(): next(responses))
    monkeypatch.setattr(emc, "execute", lambda sql, params=(): 1)
    monkeypatch.setattr(emc, "reclassify_exercises", lambda **kwargs: calls.append(kwargs) or {"written": 0})
    result = emc.canonical_associate(emc.CanonicalAssociationRequest(alias_name=" Triceps Curl ", target_mapping_id=10))
    assert result["ok"] is True
    # keep_linked: apelidos duplicados sao aceitos (2026-09-14); vinculos validos nao sao reprocessados
    assert calls == [{"limit": emc.ASSOCIATE_RECLASSIFY_LIMIT, "names": ["Triceps Curl"], "keep_linked": True}]


def test_hook_do_catalogo_nao_propaga_falha(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(emc, "reclassify_exercises", boom)
    assert emc.reclassify_after_catalog_change("teste") is None


def test_hook_do_catalogo_reprocessa_so_os_sem_vinculo(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return {"processed": 3, "updated": 1, "unresolved": 2, "written": 1, "examples": []}

    monkeypatch.setattr(emc, "reclassify_exercises", fake)
    assert emc.reclassify_after_catalog_change("teste") == {"processed": 3, "updated": 1, "unresolved": 2, "written": 1}
    assert calls == [{"limit": emc.MISSING_RECLASSIFY_LIMIT, "only_missing": True}]


# -- migracao 024: plano do indice ---------------------------------------------


class FakeSchema:
    def __init__(self, *, column=True, leading_index=True, index_name_taken=False):
        self.column = column
        self.leading_index = leading_index
        self.index_name_taken = index_name_taken

    def table_exists(self, table):
        return True

    def column_exists(self, table, column):
        return self.column

    def index_exists(self, table, index):
        return self.index_name_taken

    def query_one(self, sql, params=None):
        assert "information_schema.STATISTICS" in sql
        return {"n": 1 if self.leading_index else 0}


@pytest.fixture(scope="module")
def migration_024():
    path = m.DEFAULT_MIGRATIONS_DIR / "024_exercises_mapping_id_leitura_sem_escrita.py"
    spec = importlib.util.spec_from_file_location("migration_024_unit", path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # sem __pycache__ em api/db/migrations
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def test_024_indice_existente_zero_statements(migration_024):
    assert migration_024.plan_index(FakeSchema()) == []


def test_024_cria_indice_se_faltar(migration_024):
    assert migration_024.plan_index(FakeSchema(leading_index=False)) == [
        "ALTER TABLE exercises ADD INDEX idx_exercises_mapping_id (mapping_id)"
    ]


def test_024_falha_sem_coluna(migration_024):
    with pytest.raises(migration_024.ExercisesMappingIdMigrationError):
        migration_024.plan_index(FakeSchema(column=False))


def test_024_falha_com_nome_de_indice_ocupado(migration_024):
    with pytest.raises(migration_024.ExercisesMappingIdMigrationError):
        migration_024.plan_index(FakeSchema(leading_index=False, index_name_taken=True))
