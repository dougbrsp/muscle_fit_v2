"""Migração 117 (corrige equipamento do Crucifixo de agosto, issue #137), banco falso.

O falso aplica cada statement como o MySQL aplicaria, lendo os guardas dos
parâmetros. Cobre: estado errado conhecido, reaplicação, banco sem as linhas,
linha em estado diferente, vínculo 77 diferente, usuário diferente e `down`.

Rodar: cd api && python -m pytest tests/unit/test_migration_117_plano.py
"""

from __future__ import annotations

import copy
import functools

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "117_corrige_equipamento_crucifixo_agosto.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _estado_errado() -> dict:
    return {
        "exercises": {
            267: {"activity_id": 146, "mapping_id": 4, "equipment_id": 416, "original_name": "Voador (Pec Deck)"},
            355: {"activity_id": 174, "mapping_id": 4, "equipment_id": 488, "original_name": "Crucifixo (HumanSport)"},
            937: {"activity_id": 300, "mapping_id": 4, "equipment_id": 417, "original_name": "Crucifixo"},
        },
        "activities": {146: 1, 174: 1, 300: 1},
        "gym_equipment": {77: {"gym_id": 1, "mapping_id": 4, "equipment_id": 30}},
        "tracks": {
            1547: {"user_id": 1, "exercise_id": 4, "equipment_id": 53, "working_load_kg": "31.50"},
            1548: {"user_id": 1, "exercise_id": 4, "equipment_id": 53, "working_load_kg": "31.50"},
            1544: {"user_id": 1, "exercise_id": 4, "equipment_id": 30, "working_load_kg": "45.00"},
        },
    }


class FakeDB:
    def __init__(self, estado: dict):
        self.s = estado

    def query(self, sql, params=None):
        raise AssertionError("a migração não lê: os guardas estão no SQL")

    def execute(self, sql, params=None):
        s, p = self.s, params
        if sql.startswith("UPDATE exercises e JOIN"):
            novo, eid, act, mapping, atual, user, link, gym, lmap, leq = p
            row = s["exercises"].get(eid)
            gl = s["gym_equipment"].get(link)
            if (
                row and row["activity_id"] == act and row["mapping_id"] == mapping and row["equipment_id"] == atual
                and s["activities"].get(act) == user
                and gl == {"gym_id": gym, "mapping_id": lmap, "equipment_id": leq}
            ):
                row["equipment_id"] = novo
                return 1
            return 0
        if sql.startswith("UPDATE exercises SET"):
            novo, eid, act, mapping, atual = p
            row = s["exercises"].get(eid)
            if row and row["activity_id"] == act and row["mapping_id"] == mapping and row["equipment_id"] == atual:
                row["equipment_id"] = novo
                return 1
            return 0
        if sql.startswith("DELETE FROM progression_tracks"):
            tid, user, ex, eq, carga = p
            row = s["tracks"].get(tid)
            if row == {"user_id": user, "exercise_id": ex, "equipment_id": eq, "working_load_kg": carga}:
                del s["tracks"][tid]
                return 1
            return 0
        raise AssertionError(f"statement inesperado: {sql}")


def test_estado_errado_e_corrigido_sem_tocar_o_resto():
    antes = _estado_errado()
    db = FakeDB(copy.deepcopy(antes))
    _mod()["up"](db)
    assert db.s["exercises"][267]["equipment_id"] == 77
    assert db.s["exercises"][355]["equipment_id"] == 77
    assert db.s["exercises"][937] == antes["exercises"][937]  # execução real de 12/11 intacta
    assert db.s["exercises"][267]["original_name"] == "Voador (Pec Deck)"
    assert set(db.s["tracks"]) == {1544}  # Pec Deck preservado, 1547 e 1548 descartadas


def test_reaplicar_nao_muda_nada():
    db = FakeDB(_estado_errado())
    _mod()["up"](db)
    depois = copy.deepcopy(db.s)
    _mod()["up"](db)
    assert db.s == depois


def test_banco_sem_as_linhas_e_noop():
    vazio = {"exercises": {}, "activities": {}, "gym_equipment": {}, "tracks": {}}
    db = FakeDB(copy.deepcopy(vazio))
    _mod()["up"](db)
    assert db.s == vazio


def test_linha_em_estado_diferente_nao_e_tocada():
    estado = _estado_errado()
    estado["exercises"][267]["equipment_id"] = 99  # outro vínculo que o medido
    estado["exercises"][355]["mapping_id"] = 5
    estado["tracks"][1547]["working_load_kg"] = "40.00"
    estado["tracks"][1548]["user_id"] = 2
    db = FakeDB(copy.deepcopy(estado))
    _mod()["up"](db)
    assert db.s == estado


def test_sem_o_vinculo_77_correto_nao_reatribui():
    for ajuste in ({"equipment_id": 31}, {"gym_id": 2}, {"mapping_id": 5}):
        estado = _estado_errado()
        estado["gym_equipment"][77].update(ajuste)
        db = FakeDB(copy.deepcopy(estado))
        _mod()["up"](db)
        assert db.s["exercises"] == estado["exercises"]


def test_activity_de_outro_usuario_nao_e_tocada():
    estado = _estado_errado()
    estado["activities"][146] = 2
    db = FakeDB(copy.deepcopy(estado))
    _mod()["up"](db)
    assert db.s["exercises"][267]["equipment_id"] == 416
    assert db.s["exercises"][355]["equipment_id"] == 77


def test_down_devolve_so_o_vinculo():
    db = FakeDB(_estado_errado())
    _mod()["up"](db)
    _mod()["down"](db)
    assert db.s["exercises"][267]["equipment_id"] == 416
    assert db.s["exercises"][355]["equipment_id"] == 488
    assert 1547 not in db.s["tracks"]  # trilhas não são recriadas
    _mod()["down"](db)  # idempotente
    assert db.s["exercises"][267]["equipment_id"] == 416


def test_sql_sem_dois_pontos():
    for sql, _ in _mod()["plan"]() + _mod()["plan_down"]():
        assert ":" not in sql
