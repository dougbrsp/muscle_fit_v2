"""Migração 124 (corrige vínculo de equipamento de 23 exercícios antigos, issue #244), banco falso.

Rodar: cd api && python -m pytest tests/unit/test_migration_124_plano.py
"""

from __future__ import annotations

import copy
import functools
import logging

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "124_corrige_vinculo_equipamento_de_exercicios_antigos.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _fixes():
    return _mod()["FIXES"]


def _estado() -> dict:
    ex, links = {}, {}
    for f in _fixes():
        ex_id, act, ex_map, old, new, gym, eq, link_map, ativo = f[:9]
        ex[ex_id] = {"activity_id": act, "mapping_id": ex_map, "equipment_id": old, "original_name": "x"}
        links[new] = {"gym_id": gym, "equipment_id": eq, "mapping_id": link_map, "is_active": ativo}
        links.setdefault(old, {"gym_id": gym, "equipment_id": eq, "mapping_id": 1, "is_active": 1})
    ex[9999] = {"activity_id": 1, "mapping_id": 5, "equipment_id": 7, "original_name": "outro"}
    return {"exercises": ex, "gym_equipment": links}


class FakeDB:
    def __init__(self, estado: dict):
        self.s = estado
        self.executados: list[tuple] = []

    def _novo_ok(self, novo, gym, eq, lmap):
        return self.s["gym_equipment"].get(novo, {}).items() >= {"gym_id": gym, "equipment_id": eq, "mapping_id": lmap}.items()

    def _antigo_ok(self, antigo, gym, eq):
        return self.s["gym_equipment"].get(antigo, {}).items() >= {"gym_id": gym, "equipment_id": eq}.items()

    def query(self, sql, params=None):
        p = params
        if sql == _mod()["CHECK_UP_SQL"]:
            eid, act, emap, atual, novo, gym, eq, lmap, ant, agym, aeq = p
            r = self.s["exercises"].get(eid)
            ok = r and (r["activity_id"], r["mapping_id"], r["equipment_id"]) == (act, emap, atual) and self._novo_ok(novo, gym, eq, lmap) and self._antigo_ok(ant, agym, aeq)
            return [{"ok": 1}] if ok else []
        if sql == _mod()["CHECK_DOWN_SQL"]:
            eid, act, emap, atual, antigo, agym, aeq = p
            r = self.s["exercises"].get(eid)
            ok = r and (r["activity_id"], r["mapping_id"], r["equipment_id"]) == (act, emap, atual) and self._antigo_ok(antigo, agym, aeq)
            return [{"ok": 1}] if ok else []
        raise AssertionError(sql)

    def execute(self, sql, params=None):
        p = params
        self.executados.append((sql, p))
        if sql == _mod()["RELINK_SQL"]:
            novo, eid, act, emap, atual, lnovo, gym, eq, lmap, ant, agym, aeq = p
            assert novo == lnovo
            r = self.s["exercises"].get(eid)
            if r and (r["activity_id"], r["mapping_id"], r["equipment_id"]) == (act, emap, atual) and self._novo_ok(lnovo, gym, eq, lmap) and self._antigo_ok(ant, agym, aeq):
                r["equipment_id"] = novo
                return 1
            return 0
        if sql == _mod()["UNRELINK_SQL"]:
            antigo, eid, act, emap, atual, lant, agym, aeq = p
            r = self.s["exercises"].get(eid)
            if r and (r["activity_id"], r["mapping_id"], r["equipment_id"]) == (act, emap, atual) and self._antigo_ok(lant, agym, aeq):
                r["equipment_id"] = antigo
                return 1
            return 0
        raise AssertionError(sql)


def test_constantes_controle():
    f = _fixes()
    assert len(f) == 23
    assert len({x[0] for x in f}) == 23
    assert all(x[3] != x[4] for x in f)
    assert sum(x[0] for x in f) == 5926
    assert sum(x[4] for x in f) == 6115
    assert sum(1 for x in f if x[8] == 1) == 17 and sum(1 for x in f if x[8] == 0) == 6


def test_tudo_bate_gera_23_updates_com_parametros_certos():
    db = FakeDB(_estado())
    plano = _mod()["plan"](db)
    assert len(plano) == 23
    for (sql, p), f in zip(plano, _fixes()):
        assert sql == _mod()["RELINK_SQL"]
        assert p == (f[4], f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[3], f[5], f[6])
    _mod()["up"](db)
    assert all(db.s["exercises"][f[0]]["equipment_id"] == f[4] for f in _fixes())
    assert db.s["exercises"][9999]["equipment_id"] == 7


def test_ja_aplicada_plano_vazio():
    db = FakeDB(_estado())
    _mod()["up"](db)
    assert _mod()["plan"](db) == []


def test_banco_sem_as_linhas_vazio():
    db = FakeDB({"exercises": {}, "gym_equipment": {}})
    assert _mod()["plan"](db) == []
    _mod()["up"](db)
    assert db.executados == []


def test_exercicio_em_estado_diferente_nao_e_tocado():
    for campo, valor in (("equipment_id", 12345), ("mapping_id", 888), ("activity_id", 888)):
        e = _estado()
        alvo = _fixes()[0][0]
        e["exercises"][alvo][campo] = valor
        db = FakeDB(copy.deepcopy(e))
        assert len(_mod()["plan"](db)) == 22
        _mod()["up"](db)
        assert db.s["exercises"][alvo] == e["exercises"][alvo]


def test_vinculo_novo_incorreto_ou_ausente_nao_toca_so_aquela_linha():
    alvo = _fixes()[0]
    for ajuste in ({"gym_id": 77}, {"equipment_id": 77}, {"mapping_id": 77}, None):
        e = _estado()
        if ajuste is None:
            del e["gym_equipment"][alvo[4]]
        else:
            e["gym_equipment"][alvo[4]].update(ajuste)
        db = FakeDB(copy.deepcopy(e))
        _mod()["up"](db)
        assert db.s["exercises"][alvo[0]]["equipment_id"] == alvo[3]
        feitos = sum(1 for f in _fixes() if db.s["exercises"][f[0]]["equipment_id"] == f[4])
        assert feitos == 22  # vinculo novo da primeira tupla (86) e exclusivo dela


def test_vinculo_novo_inativo_toca():
    e = _estado()
    inativos = [f for f in _fixes() if f[8] == 0]
    assert inativos
    db = FakeDB(e)
    _mod()["up"](db)
    assert all(db.s["exercises"][f[0]]["equipment_id"] == f[4] for f in inativos)


def test_down_devolve_so_onde_o_novo_esta():
    db = FakeDB(_estado())
    _mod()["up"](db)
    db.s["exercises"][_fixes()[0][0]]["equipment_id"] = 4242  # mudou depois
    _mod()["down"](db)
    assert db.s["exercises"][_fixes()[0][0]]["equipment_id"] == 4242
    assert all(db.s["exercises"][f[0]]["equipment_id"] == f[3] for f in _fixes()[1:])
    _mod()["down"](db)  # idempotente
    assert all(db.s["exercises"][f[0]]["equipment_id"] == f[3] for f in _fixes()[1:])


def test_down_sem_vinculo_antigo_nao_toca():
    db = FakeDB(_estado())
    _mod()["up"](db)
    alvo = _fixes()[0]
    del db.s["gym_equipment"][alvo[3]]
    _mod()["down"](db)
    assert db.s["exercises"][alvo[0]]["equipment_id"] == alvo[4]


def test_sql_sem_dois_pontos():
    for sql, _ in _mod()["plan"]() + _mod()["plan_down"]():
        assert ":" not in sql


def _ALVO_EXCLUSIVO():
    """Tupla do exercicio 104: vinculo antigo (81) so dela (outros sao compartilhados)."""
    return next(f for f in _fixes() if f[0] == 104)


def _um_alvo_pulado(ajuste):
    """Vinculo antigo da 1a tupla alterado (ou ausente): so ela e pulada."""
    alvo = _ALVO_EXCLUSIVO()
    e = _estado()
    if ajuste is None:
        del e["gym_equipment"][alvo[3]]
    else:
        e["gym_equipment"][alvo[3]].update(ajuste)
    db = FakeDB(e)
    _mod()["up"](db)
    assert db.s["exercises"][alvo[0]]["equipment_id"] == alvo[3]
    assert sum(1 for f in _fixes() if db.s["exercises"][f[0]]["equipment_id"] == f[4]) == 22


def test_vinculo_antigo_de_outro_aparelho_pula():
    _um_alvo_pulado({"equipment_id": 999})


def test_vinculo_antigo_de_outra_academia_pula():
    _um_alvo_pulado({"gym_id": 77})


def test_vinculo_antigo_ausente_pula():
    _um_alvo_pulado(None)


def test_down_vinculo_antigo_de_outro_aparelho_nao_devolve():
    db = FakeDB(_estado())
    _mod()["up"](db)
    alvo = _ALVO_EXCLUSIVO()
    db.s["gym_equipment"][alvo[3]]["equipment_id"] = 999
    _mod()["down"](db)
    assert db.s["exercises"][alvo[0]]["equipment_id"] == alvo[4]
    assert all(db.s["exercises"][f[0]]["equipment_id"] == f[3] for f in [f for f in _fixes() if f[0] != 104])


def test_placeholders_batem_com_parametros():
    m_ = _mod()
    f = _ALVO_EXCLUSIVO()
    for sql, n in (
        (m_["RELINK_SQL"], len(m_["_up_params"](f))),
        (m_["CHECK_UP_SQL"], len(m_["_up_params"](f)) - 1),
        (m_["UNRELINK_SQL"], len(m_["_down_params"](f))),
        (m_["CHECK_DOWN_SQL"], len(m_["_down_params"](f)) - 1),
    ):
        assert sql.count("%s") == n


def test_log_das_contagens(caplog):
    e = _estado()
    alvo = _ALVO_EXCLUSIVO()
    e["gym_equipment"][alvo[3]]["equipment_id"] = 999
    db = FakeDB(e)
    with caplog.at_level(logging.INFO, logger="fitness.migrate"):
        _mod()["up"](db)
    assert "migracao 124: plano=22 aplicadas=22 puladas=1 de 23" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="fitness.migrate"):
        _mod()["down"](db)
    assert "migracao 124 down: plano=22 aplicadas=22 puladas=1 de 23" in caplog.text
