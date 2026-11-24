"""POST /workout-plan-templates/redundancy-preview (#288 fatia 2) sem banco real: lote sem N+1, alvo, ordem, filtro,
tabela de perfis ausente, permissao e entrada invalida."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import exercise_redundancy_routes as mod
from app.security import Identity, get_current_identity

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

URL = "/workout-plan-templates/redundancy-preview"
PERFIL = {"source": "plate_loaded", "mechanism": "lever", "trajectory": "guided_arc", "profile": "mid_peak",
          "line": "joint_axis_relative", "direction_changes": "no", "confidence": "high", "status": "reviewed",
          "origin": "admin", "flags_json": "[]"}
EX = {"biomechanical_pattern": "push", "primary_joint": "shoulder", "movement": "press", "body_position": "seated",
      "trajectory": "vertical", "is_unilateral": 0}


class _Cur:
    def __init__(self, db):
        self.db, self.rows = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.db.sql.append(sql)
        if "information_schema" in sql:
            self.rows = [{"n": 1 if self.db.tabela else 0}]
        elif "FROM exercise_catalog" in sql:
            self.rows = [{"id": i, **self.db.catalog[i]} for i in params if i in self.db.catalog]
        elif "equipment_exercises" in sql:
            self.rows = self.db.perfis
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _DB:
    def __init__(self):
        self.sql: list[str] = []
        self.tabela = True
        self.catalog = {10: dict(EX), 11: dict(EX), 12: {**EX, "biomechanical_pattern": "pull"}}
        self.perfis = [{"equipment_id": 1, "exercise_id": 10, **PERFIL}, {"equipment_id": 2, "exercise_id": 11, **PERFIL}]

    def conn(self):
        db = self

        class C:
            def cursor(self):
                return _Cur(db)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return C()


@pytest.fixture
def db(monkeypatch):
    banco = _DB()
    chamadas = []

    def _contrib(cur, ids, group_names=None):
        chamadas.append(set(ids))
        prim = [{"muscle_group_id": 5, "role": "PRIMARY"}, {"muscle_group_id": 6, "role": "SECONDARY"}]
        return {10: prim, 11: prim, 12: [{"muscle_group_id": 9, "role": "PRIMARY"}], 13: [{"muscle_group_id": None, "role": "PRIMARY"}]}

    monkeypatch.setattr(mod, "get_conn", banco.conn)
    monkeypatch.setattr(mod, "resolve_muscle_contributions_with_cursor", _contrib)
    banco.contrib_calls = chamadas
    return banco


def _client(papel="admin"):
    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role=papel, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _itens(*pares):
    return [{"mapping_id": m, **({"equipment_id": e} if e else {})} for m, e in pares]


def test_todos_os_pares_em_lote_sem_n_mais_1_e_ordenados_do_mais_redundante(db):
    r = _client().post(URL, json={"items": _itens((10, 1), (11, 2), (12, 3), (10, 1)),
                                  "min_level": mod.red.LEVEL_ORDER[0]})
    assert r.status_code == 200, r.text
    itens = r.json()["items"]
    assert len(itens) == 6  # 4 itens -> 6 pares
    assert (itens[0]["a_index"], itens[0]["b_index"], itens[0]["level"]) == (0, 3, "near_duplicate")
    assert itens[1]["level"] == "high" and (itens[1]["a_mapping_id"], itens[1]["b_mapping_id"]) == (10, 11)
    niveis = [i["level"] for i in itens]
    assert niveis == sorted(niveis, key=mod.red.level_rank, reverse=True)
    assert niveis[-1] == "low"  # pull x push: grupos primarios diferentes
    # 1 catalogo + 1 resolvedor + 1 checagem de tabela + 1 perfis, independente do numero de itens
    assert len(db.sql) == 3 and db.contrib_calls == [{10, 11, 12}]


def test_alvo_compara_so_com_os_demais(db):
    r = _client().post(URL, json={"items": _itens((10, 1), (11, 2), (12, 3)), "target_index": 2, "min_level": "unknown"})
    pares = [(i["a_index"], i["b_index"]) for i in r.json()["items"]]
    assert sorted(pares) == [(0, 2), (1, 2)]


def test_piso_padrao_do_dono_quando_min_level_omitido(db):
    r = _client().post(URL, json={"items": _itens((10, 1), (11, 2), (12, 3), (10, 1))})
    niveis = [i["level"] for i in r.json()["items"]]
    assert niveis and all(mod.red.at_least(n, mod.red.DISPLAY_MIN_LEVEL) for n in niveis)
    assert len(niveis) < 6  # o par "low" (pull x push) fica fora


def test_min_level_omite_pares_abaixo_e_nivel_invalido_422(db):
    c = _client()
    r = c.post(URL, json={"items": _itens((10, 1), (11, 2), (12, 3)), "min_level": "high"})
    assert [i["level"] for i in r.json()["items"]] == ["high"]
    assert c.post(URL, json={"items": _itens((10, 1)), "min_level": "xx"}).status_code == 422


def test_tabela_de_perfis_ausente_vira_perfil_none_sem_503(db):
    db.tabela = False
    r = _client().post(URL, json={"items": _itens((10, 1), (11, 2)), "min_level": "unknown"})
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["level"] == "low" and "no_reliable_profile" in [x["code"] for x in item["reasons"]]
    assert not any("equipment_exercises" in s for s in db.sql)


def test_item_sem_equipamento_ou_sem_contribuicao_ou_fora_do_catalogo(db):
    db.catalog[13] = dict(EX)
    r = _client().post(URL, json={"items": _itens((10, None), (13, 1), (99, 1)), "min_level": "unknown"})
    itens = r.json()["items"]
    assert [(i["a_index"], i["b_index"]) for i in itens] == [(0, 1)]  # 99 ausente: sem par
    assert itens[0]["level"] == "unknown"  # "Outros" (sem grupo) nao e grupo primario


def test_permissao_e_entrada_invalida(db):
    assert _client("regular").post(URL, json={"items": _itens((10, 1))}).status_code == 403
    c = _client()
    assert c.post(URL, json={"items": []}).status_code == 422
    assert c.post(URL, json={"items": _itens(*[(10, 1)] * 31)}).status_code == 422
    assert c.post(URL, json={"items": _itens((10, 1)), "target_index": 1}).status_code == 422
    assert c.post(URL, json={"items": _itens((10, 1)), "extra": 1}).status_code == 422
    assert c.post(URL, json={"items": [{"mapping_id": "a"}]}).status_code == 422
