"""Testes unitarios de app/equipment_exercises.py (PR-05).

Rodar: cd api && python -m pytest tests/unit/test_equipment_exercises.py
Sem banco: o servico e exercitado com um cursor fake que responde por padrao
de SQL.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from fastapi import HTTPException  # noqa: E402

from app import equipment_exercises as ee
from app import load_conventions  # noqa: E402
from app import gym_equipment_sync as ges  # noqa: E402


# ---------------------------------------------------------------------------
# Logica pura
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [(1, 1), ("42", 42), (" 7 ", 7), (3.0, 3)])
def test_parse_positive_id_accepts(value, expected):
    assert ee.parse_positive_id(value, "equipment_id") == expected


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "", "abc", "1.5", 2.5, [], {}])
def test_parse_positive_id_rejects(value):
    with pytest.raises(ValueError):
        ee.parse_positive_id(value, "equipment_id")


def test_normalize_notes():
    assert ee.normalize_notes(None) is None
    assert ee.normalize_notes("   ") is None
    assert ee.normalize_notes("  banco 30 graus ") == "banco 30 graus"
    with pytest.raises(ValueError):
        ee.normalize_notes(123)
    with pytest.raises(ValueError):
        ee.normalize_notes("x" * (ee.NOTES_MAX_LENGTH + 1))


def test_normalize_link_payload_canonical_and_legacy_field():
    assert ee.normalize_link_payload({"equipment_id": 1, "exercise_id": "2"}) == (1, 2, None, False)
    assert ee.normalize_link_payload({"equipment_id": 1, "exercise_id": 2, "notes": ""}) == (1, 2, None, True)
    assert ee.normalize_link_payload({"equipment_id": 1, "mapping_id": 9}, exercise_field="mapping_id") == (
        1,
        9,
        None,
        False,
    )
    with pytest.raises(ValueError):
        ee.normalize_link_payload({"equipment_id": 1, "mapping_id": 9})  # campo canonico ausente
    with pytest.raises(ValueError):
        ee.normalize_link_payload(["nao", "objeto"])  # type: ignore[arg-type]


def test_decide_mirror():
    assert ee.decide_mirror(False, None, False) == ee.MIRROR_SKIPPED_NO_LEGACY_TABLE
    assert ee.decide_mirror(True, None, False) == ee.MIRROR_OK
    assert ee.decide_mirror(True, "exercise_zepp_mappings", True) == ee.MIRROR_OK
    assert ee.decide_mirror(True, "exercise_zepp_mappings", False) == ee.MIRROR_SKIPPED_FK_TARGET_MISSING


def test_serialize_link_row_casts_flags():
    row = ee.serialize_link_row({"id": 1, "is_active": 1, "is_preferred": 0, "has_photo": 0, "notes": None})
    assert row == {"id": 1, "is_active": True, "is_preferred": False, "has_photo": False, "notes": None}


# ---------------------------------------------------------------------------
# Servico com cursor fake
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.db.executed.append((normalized, tuple(params)))
        self._result = self.db.respond(normalized, tuple(params))

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeDb:
    def __init__(self, *, fk_table="exercise_zepp_mappings", legacy_ids=(10,), legacy_table=True, tem_coluna_045=True, schema="musclefit"):
        self.executed = []
        self.tem_coluna_045 = tem_coluna_045
        self.schema = schema
        self.fk_table = fk_table
        self.legacy_ids = set(legacy_ids)
        self.legacy_table = legacy_table
        self.links = {}  # (equipment_id, exercise_id) -> row
        self.next_id = 100

    def cursor(self):
        return FakeCursor(self)

    def respond(self, sql, params):
        if sql.startswith("SELECT id FROM equipment WHERE"):
            return [{"id": params[0]}] if params[0] != 404 else []
        if sql.startswith("SELECT id FROM exercise_catalog"):
            return [{"id": params[0]}] if params[0] != 404 else []
        if sql.startswith("INSERT INTO equipment_exercises"):
            # O INSERT e montado com as colunas REALMENTE enviadas (ver
            # `upsert_equipment_exercise`), entao o fake le a lista de colunas
            # do proprio SQL em vez de contar os parametros.
            colunas = sql.split("(", 1)[1].split(")", 1)[0].replace(" ", "").split(",")
            valores = dict(zip(colunas, params))
            key = (valores["equipment_id"], valores["exercise_id"])
            row = self.links.get(key)
            if row is None:
                self.next_id += 1
                row = {
                    "id": self.next_id,
                    "equipment_id": key[0],
                    "exercise_id": key[1],
                    "is_preferred": 0,
                    "notes": None,
                    "load_convention": None,
                }
                self.links[key] = row
            row["is_active"] = 1
            for campo in ("notes", "load_convention"):
                if campo in valores:
                    row[campo] = valores[campo]
            return []
        if sql.startswith("UPDATE equipment_exercises SET is_active = 0"):
            row = self.links.get((params[0], params[1]))
            if row:
                row["is_active"] = 0
            return []
        if sql.startswith("SELECT id, equipment_id, exercise_id"):
            row = self.links.get((params[0], params[1]))
            return [dict(row)] if row else []
        if sql.startswith("SELECT equipment_id, exercise_id FROM equipment_exercises WHERE id"):
            return [dict(r) for r in self.links.values() if r["id"] == params[0]]
        if sql.startswith("SELECT DATABASE()"):
            return [{"db": self.schema}]
        if "information_schema.COLUMNS" in sql:
            return [{"n": 1 if self.tem_coluna_045 else 0}]
        if "information_schema.TABLES" in sql:
            return [{"n": 1 if self.legacy_table else 0}]
        if "information_schema.KEY_COLUMN_USAGE" in sql:
            return [{"ref": self.fk_table}] if self.fk_table else []
        if sql.startswith("SELECT 1 AS ok FROM"):
            return [{"ok": 1}] if params[0] in self.legacy_ids else []
        return []

    def statements(self, prefix):
        return [s for s in self.executed if s[0].startswith(prefix)]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    ee.reset_legacy_fk_cache()
    ges.reset_fk_ref_cache()
    # a deteccao de `equipment_exercises.load_convention` (045) tambem e
    # cacheada por processo: sem isto, um teste herdaria a resposta do outro
    load_conventions.reset_load_convention_column_cache()
    synced = []

    def fake_sync(conn, eq_id):
        synced.append(eq_id)
        return []

    monkeypatch.setattr(ee, "sync_gym_equipment_for_equipment", fake_sync)
    return synced


def test_upsert_mirrors_when_fk_target_exists(_isolate):
    db = FakeDb(legacy_ids={10})
    row = ee.upsert_equipment_exercise(db, 1, 10)
    assert row["legacy_mirror"] == ee.MIRROR_OK
    assert row["is_active"] is True
    assert len(db.statements("INSERT INTO equipment_exercise_mappings")) == 1
    assert _isolate == [1]


def test_upsert_skips_mirror_when_fk_target_missing(_isolate):
    db = FakeDb(legacy_ids=set())
    row = ee.upsert_equipment_exercise(db, 1, 55)
    assert row["legacy_mirror"] == ee.MIRROR_SKIPPED_FK_TARGET_MISSING
    assert db.statements("INSERT INTO equipment_exercise_mappings") == []
    assert db.links[(1, 55)]["is_active"] == 1  # escrita canonica feita mesmo assim
    assert _isolate == [1]


def test_upsert_is_idempotent_and_reactivates():
    db = FakeDb()
    first = ee.upsert_equipment_exercise(db, 1, 10)
    ee.deactivate_equipment_exercise(db, first["id"])
    assert db.links[(1, 10)]["is_active"] == 0
    second = ee.upsert_equipment_exercise(db, 1, 10)
    assert second["id"] == first["id"]
    assert second["is_active"] is True
    assert len(db.links) == 1


def test_upsert_preserves_notes_unless_provided():
    db = FakeDb()
    ee.upsert_equipment_exercise(db, 1, 10, "pegada neutra", notes_provided=True)
    assert ee.upsert_equipment_exercise(db, 1, 10)["notes"] == "pegada neutra"
    assert ee.upsert_equipment_exercise(db, 1, 10, None, notes_provided=True)["notes"] is None


def test_normalize_load_convention_field():
    """Campo ausente preserva; vazio/None limpa (NULL = total); invalido e 400."""
    assert ee.normalize_load_convention_field({}) == (None, False)
    assert ee.normalize_load_convention_field({"load_convention": None}) == (None, True)
    assert ee.normalize_load_convention_field({"load_convention": "  "}) == (None, True)
    assert ee.normalize_load_convention_field({"load_convention": " Per_Side "}) == ("per_side", True)
    assert ee.normalize_load_convention_field({"load_convention": "assisted"}) == ("assisted", True)
    with pytest.raises(ValueError):
        ee.normalize_load_convention_field({"load_convention": "por_lado"})
    with pytest.raises(ValueError):
        ee.normalize_load_convention_field({"load_convention": 7})


def test_upsert_preserves_load_convention_unless_provided():
    db = FakeDb()
    ee.upsert_equipment_exercise(db, 1, 10, load_convention="assisted", load_convention_provided=True)
    assert db.links[(1, 10)]["load_convention"] == "assisted"
    # reativacao SEM o campo nao apaga o que ja estava gravado
    ee.upsert_equipment_exercise(db, 1, 10)
    assert db.links[(1, 10)]["load_convention"] == "assisted"
    # enviado vazio limpa (volta a valer como `total`)
    ee.upsert_equipment_exercise(db, 1, 10, load_convention=None, load_convention_provided=True)
    assert db.links[(1, 10)]["load_convention"] is None


def test_sem_a_migracao_045_a_leitura_do_vinculo_continua_funcionando():
    """Codigo publicado ANTES da 045 (ordem de deploy) ou banco num estado de
    schema antigo: a escrita do vinculo nao pode quebrar, e `load_convention`
    volta como `None` - que ja significa `total` (D12)."""
    db = FakeDb(tem_coluna_045=False)
    row = ee.upsert_equipment_exercise(db, 1, 10)
    assert row["is_active"] is True
    assert row["load_convention"] is None
    selects = [sql for sql, _p in db.executed if sql.startswith("SELECT id, equipment_id")]
    assert selects and "NULL AS load_convention" in selects[0]


def test_sem_a_migracao_045_gravar_a_convencao_e_recusado_com_mensagem_clara():
    """A ESCRITA da convencao exige a coluna: quem grava e a tela de admin, e
    la a 045 ja rodou. 503 explicito, nunca um 500 de 'Unknown column'."""
    db = FakeDb(tem_coluna_045=False)
    with pytest.raises(HTTPException) as exc:
        ee.upsert_equipment_exercise(db, 1, 10, load_convention="assisted", load_convention_provided=True)
    assert exc.value.status_code == 503
    assert "045" in str(exc.value.detail)


def test_o_cache_da_coluna_045_e_por_schema_e_nao_vaza():
    """O que quebrou o CI: a suite de integracao cria um schema por arquivo no
    MESMO processo. Um schema ja migrado nao pode ensinar ao outro que a
    coluna existe."""
    migrado = FakeDb(schema="musclefit_run1_test", tem_coluna_045=True)
    ee.upsert_equipment_exercise(migrado, 1, 10)
    select_migrado = [s for s, _p in migrado.executed if s.startswith("SELECT id, equipment_id")][0]
    assert "equipment_exercises.load_convention" in select_migrado

    antigo = FakeDb(schema="musclefit_run2_test", tem_coluna_045=False)
    ee.upsert_equipment_exercise(antigo, 1, 10)
    select_antigo = [s for s, _p in antigo.executed if s.startswith("SELECT id, equipment_id")][0]
    assert "equipment_exercises.load_convention" not in select_antigo
    assert "NULL AS load_convention" in select_antigo


def test_a_ausencia_da_coluna_045_nao_e_memorizada(_isolate):
    """Regressao reproduzida em DEV: o container da API subiu ANTES da 045,
    uma requisicao gravou "nao existe" no cache e o processo passou a recusar a
    escrita (503 citando a 045) mesmo depois de a migracao rodar - so o restart
    recuperava. Aqui o MESMO schema, no MESMO processo, ganha a coluna no meio
    do teste e a chamada seguinte ja tem de enxerga-la, SEM reset."""
    db = FakeDb(schema="musclefit", tem_coluna_045=False)
    with db.cursor() as cur:
        assert load_conventions.has_load_convention_column(cur) is False

    db.tem_coluna_045 = True  # a 045 roda agora, com o processo de pe
    with db.cursor() as cur:
        assert load_conventions.has_load_convention_column(cur) is True
        assert load_conventions.load_convention_select_expr(cur) == "eqex.load_convention"

    # e a escrita da convencao, que antes ficava presa no 503, volta a passar
    row = ee.upsert_equipment_exercise(db, 1, 10, load_convention="per_side", load_convention_provided=True)
    assert row["load_convention"] == "per_side"


def test_a_presenca_da_coluna_045_continua_cacheada_por_schema(_isolate):
    """O lado positivo e permanente (migracoes aditivas): `information_schema`
    e consultada uma unica vez por schema depois que a coluna existe."""
    db = FakeDb(schema="musclefit", tem_coluna_045=True)
    for _ in range(3):
        with db.cursor() as cur:
            assert load_conventions.has_load_convention_column(cur) is True

    consultas = [sql for sql, _p in db.executed if "information_schema.COLUMNS" in sql]
    assert len(consultas) == 1


def test_upsert_404_on_unknown_equipment_or_exercise():
    with pytest.raises(HTTPException) as exc:
        ee.upsert_equipment_exercise(FakeDb(), 404, 10)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        ee.upsert_equipment_exercise(FakeDb(), 1, 404)
    assert exc.value.status_code == 404


def test_deactivate_unknown_id_404():
    with pytest.raises(HTTPException) as exc:
        ee.deactivate_equipment_exercise(FakeDb(), 999)
    assert exc.value.status_code == 404


def test_deactivate_mirrors_legacy_and_syncs(_isolate):
    db = FakeDb()
    link = ee.upsert_equipment_exercise(db, 3, 10)
    ee.deactivate_equipment_exercise(db, link["id"])
    assert len(db.statements("UPDATE equipment_exercise_mappings SET is_active = 0")) == 1
    assert _isolate == [3, 3]


def _deteccoes_de_fk(db):
    return len([s for s in db.executed if "information_schema.KEY_COLUMN_USAGE" in s[0]])


def test_legacy_fk_resolution_is_cached_quando_a_023_ja_rodou():
    """Estado CANONICO (FK ja em `exercise_catalog`): uma deteccao por schema,
    e so. Esse estado e permanente - as migracoes do projeto sao aditivas e
    nada reaponta a FK de volta."""
    db = FakeDb(fk_table="exercise_catalog", legacy_ids={10})
    ee.upsert_equipment_exercise(db, 1, 10)
    ee.upsert_equipment_exercise(db, 2, 10)
    assert _deteccoes_de_fk(db) == 1


def test_a_ausencia_da_023_nunca_e_memorizada(_isolate):
    """M18: com a FK ainda no alvo LEGADO, a deteccao se REPETE a cada
    chamada - e, assim que a migracao roda, a MESMA instancia ja enxerga o
    estado novo. Memorizar esse "ainda nao" prendia o container na resposta
    velha ate reciclar, durante toda a janela de deploy."""
    db = FakeDb(fk_table="exercise_zepp_mappings", legacy_ids={10})
    ee.upsert_equipment_exercise(db, 1, 10)
    ee.upsert_equipment_exercise(db, 2, 10)
    assert _deteccoes_de_fk(db) == 2

    # a 023 roda no meio da vida do processo
    db.fk_table = "exercise_catalog"
    row = ee.upsert_equipment_exercise(db, 3, 10)
    assert row["legacy_mirror"] == ee.MIRROR_OK


def test_a_ausencia_da_tabela_legada_nunca_e_memorizada(_isolate):
    """Mesmo raciocinio para `legacy_exists=False`: a tabela legada pode
    aparecer (schema antigo sendo migrado) sem reciclar o processo."""
    db = FakeDb(legacy_table=False)
    assert ee.upsert_equipment_exercise(db, 1, 10)["legacy_mirror"] == ee.MIRROR_SKIPPED_NO_LEGACY_TABLE
    db.legacy_table = True
    db.fk_table = "exercise_catalog"
    assert ee.upsert_equipment_exercise(db, 2, 10)["legacy_mirror"] == ee.MIRROR_OK


def test_o_cache_da_fk_legada_e_por_schema(_isolate):
    """A suite de integracao cria um schema por ARQUIVO no MESMO processo: o
    estado canonico observado num schema nao pode valer para outro."""
    canonico = FakeDb(fk_table="exercise_catalog", legacy_ids={10}, schema="schema_a")
    ee.upsert_equipment_exercise(canonico, 1, 10)
    assert _deteccoes_de_fk(canonico) == 1

    antigo = FakeDb(fk_table="exercise_zepp_mappings", legacy_ids=set(), schema="schema_b")
    row = ee.upsert_equipment_exercise(antigo, 1, 55)
    assert _deteccoes_de_fk(antigo) == 1, "o schema novo precisa ser detectado, nao herdado"
    assert row["legacy_mirror"] == ee.MIRROR_SKIPPED_FK_TARGET_MISSING


def test_no_legacy_table_skips_mirror():
    db = FakeDb(legacy_table=False)
    row = ee.upsert_equipment_exercise(db, 1, 10)
    assert row["legacy_mirror"] == ee.MIRROR_SKIPPED_NO_LEGACY_TABLE
    link_id = row["id"]
    ee.deactivate_equipment_exercise(db, link_id)
    assert db.statements("UPDATE equipment_exercise_mappings") == []


def test_mirror_error_never_breaks_canonical_write():
    class ExplodingDb(FakeDb):
        def respond(self, sql, params):
            if sql.startswith("SELECT 1 AS ok FROM"):
                raise RuntimeError("boom")
            return super().respond(sql, params)

    db = ExplodingDb()
    row = ee.upsert_equipment_exercise(db, 1, 10)
    assert row["legacy_mirror"] == ee.MIRROR_SKIPPED_ERROR
    assert db.links[(1, 10)]["is_active"] == 1


def test_upsert_and_deactivate_propagate_gym_sync_warnings(monkeypatch):
    warning = {"code": ges.FK_LEGACY_EXCLUDED_CODE, "gym_id": 7, "excluded_count": 1, "excluded_exercise_ids": [55]}
    monkeypatch.setattr(ee, "sync_gym_equipment_for_equipment", lambda conn, eq_id: [warning])
    db = FakeDb()
    row = ee.upsert_equipment_exercise(db, 1, 10)
    assert row["gym_sync_warnings"] == [warning]
    assert ee.deactivate_equipment_exercise(db, row["id"])["gym_sync_warnings"] == [warning]


# ---------------------------------------------------------------------------
# gym_equipment_sync: deteccao da FK de gym_equipment.mapping_id e filtro
# ---------------------------------------------------------------------------


def test_fk_filter_table_pure():
    assert ges.fk_filter_table(None) is None
    assert ges.fk_filter_table("exercise_catalog") is None
    assert ges.fk_filter_table("exercise_zepp_mappings") == "exercise_zepp_mappings"
    with pytest.raises(ValueError):
        ges.fk_filter_table("x`; DROP TABLE y")


def test_build_fk_excluded_warning_dedup_sort_and_truncate():
    warning = ges.build_fk_excluded_warning(3, "exercise_zepp_mappings", [9, 2, 9])
    assert warning == {
        "code": "gym_equipment_sync.fk_legacy_excluded",
        "gym_id": 3,
        "fk_referenced_table": "exercise_zepp_mappings",
        "excluded_count": 2,
        "excluded_exercise_ids": [2, 9],
        "excluded_ids_truncated": False,
    }
    big = ges.build_fk_excluded_warning(3, "t", range(1, ges.MAX_LOGGED_IDS + 6))
    assert big["excluded_count"] == ges.MAX_LOGGED_IDS + 5
    assert len(big["excluded_exercise_ids"]) == ges.MAX_LOGGED_IDS
    assert big["excluded_ids_truncated"] is True


class FakeSyncDb:
    """Simula o que o MySQL faria no sync: `excluded` = exercise_ids ativos da
    academia ausentes da tabela alvo da FK."""

    def __init__(self, *, fk_table, excluded=(), gyms=(1,), schema="musclefit"):
        self.fk_table = fk_table
        self.excluded = list(excluded)
        self.gyms = list(gyms)
        self.schema = schema
        self.executed = []

    def cursor(self):
        return FakeCursor(self)

    def respond(self, sql, params):
        if sql.startswith("SELECT DATABASE()"):
            return [{"db": self.schema}]
        if "information_schema.KEY_COLUMN_USAGE" in sql:
            assert params == ("gym_equipment", "mapping_id")
            return [{"ref": self.fk_table}] if self.fk_table else []
        if sql.startswith("SELECT DISTINCT eem.exercise_id"):
            return [{"exercise_id": value} for value in self.excluded]
        if sql.startswith("SELECT DISTINCT gym_id FROM gym_equipment_catalog"):
            return [{"gym_id": gym} for gym in self.gyms]
        return []

    def inserts(self):
        return [s for s in self.executed if s[0].startswith("INSERT INTO gym_equipment")]


def test_sync_filters_insert_when_fk_points_to_legacy_table(caplog):
    db = FakeSyncDb(fk_table="exercise_zepp_mappings", excluded=[2001, 2002])
    with caplog.at_level("WARNING", logger=ges.logger.name):
        warnings = ges.sync_gym_equipment_for_gym(db, 1)

    (insert_sql, insert_params), = db.inserts()
    assert "AND EXISTS (SELECT 1 FROM `exercise_zepp_mappings` fkt WHERE fkt.id = eem.exercise_id)" in insert_sql
    assert insert_params == (1,)
    assert warnings == [ges.build_fk_excluded_warning(1, "exercise_zepp_mappings", [2001, 2002])]
    record = next(r for r in caplog.records if r.getMessage() == ges.FK_LEGACY_EXCLUDED_CODE)
    assert record.excluded_count == 2
    assert record.excluded_exercise_ids == [2001, 2002]


def test_sync_legacy_fk_without_orphans_filters_but_does_not_warn(caplog):
    db = FakeSyncDb(fk_table="exercise_zepp_mappings", excluded=[])
    with caplog.at_level("WARNING", logger=ges.logger.name):
        assert ges.sync_gym_equipment_for_gym(db, 1) == []
    assert "AND EXISTS" in db.inserts()[0][0]
    assert not [r for r in caplog.records if r.getMessage() == ges.FK_LEGACY_EXCLUDED_CODE]


@pytest.mark.parametrize("fk_table", ["exercise_catalog", None])
def test_sync_no_filter_when_fk_points_to_catalog_or_absent(fk_table):
    db = FakeSyncDb(fk_table=fk_table, excluded=[999])
    assert ges.sync_gym_equipment_for_gym(db, 1) == []
    assert "EXISTS" not in db.inserts()[0][0]
    assert not [s for s in db.executed if s[0].startswith("SELECT DISTINCT eem.exercise_id")]


def test_sync_fk_detection_is_cached_across_gyms_quando_a_023_ja_rodou():
    """Estado CANONICO: uma deteccao por schema, reusada pelas duas academias."""
    db = FakeSyncDb(fk_table="exercise_catalog", excluded=[2001], gyms=[1, 2])
    assert ges.sync_gym_equipment_for_equipment(db, 5) == []
    assert _deteccoes_de_fk(db) == 1
    assert len(db.inserts()) == 2


def test_sync_nunca_memoriza_a_ausencia_da_023():
    """M18, o mais grave dos dois: aqui o caminho e de LEITURA materializada.
    Preso no alvo legado, o sync continuaria EXCLUINDO do `gym_equipment`
    exercicios ja validos no banco - silenciosamente, com um warning que
    parece ruido. Por isso a deteccao nao-canonica se repete a cada academia,
    e a migracao passa a valer sem reciclar o processo."""
    db = FakeSyncDb(fk_table="exercise_zepp_mappings", excluded=[2001], gyms=[1, 2])
    warnings = ges.sync_gym_equipment_for_equipment(db, 5)
    assert [w["gym_id"] for w in warnings] == [1, 2]
    assert _deteccoes_de_fk(db) == 2
    assert len(db.inserts()) == 2

    db.fk_table = "exercise_catalog"
    assert ges.sync_gym_equipment_for_gym(db, 1) == []
    assert "EXISTS" not in db.inserts()[-1][0]


def test_sync_nunca_memoriza_a_ausencia_de_fk():
    """`None` ("nao ha FK") tambem e transitorio e tambem nao e memorizado."""
    db = FakeSyncDb(fk_table=None, gyms=[1, 2])
    ges.sync_gym_equipment_for_equipment(db, 5)
    assert _deteccoes_de_fk(db) == 2


def test_o_cache_da_fk_do_sync_e_por_schema():
    canonico = FakeSyncDb(fk_table="exercise_catalog", schema="schema_a")
    ges.sync_gym_equipment_for_gym(canonico, 1)
    assert _deteccoes_de_fk(canonico) == 1

    antigo = FakeSyncDb(fk_table="exercise_zepp_mappings", excluded=[2001], schema="schema_b")
    warnings = ges.sync_gym_equipment_for_gym(antigo, 1)
    assert _deteccoes_de_fk(antigo) == 1, "o schema novo precisa ser detectado, nao herdado"
    assert [w["fk_referenced_table"] for w in warnings] == ["exercise_zepp_mappings"]
