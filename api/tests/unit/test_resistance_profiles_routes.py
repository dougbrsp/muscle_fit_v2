"""Rotas `app/resistance_profiles.py` (#288) sem banco real: vocabulario, lista com filtros e sem N+1, leitura, PUT do
admin (validacao e transicao pelo dono), historico, cobertura, RBAC e 503 sem a migracao 126."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import resistance_profiles as mod
from app.domain import resistance_profile as dono
from app.security import Identity, get_current_identity
from tests.support.resistance_profiles_fake import FakeProfilesDB, _Duplicate

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

ADMIN, USUARIO = 1, 2
CORPO = {"source": "plate_loaded", "mechanism": "lever", "trajectory": "guided_arc", "profile": "mid_peak",
         "line": "joint_axis_relative", "flags": ["friction"], "confidence": "medium", "status": "reviewed"}


@pytest.fixture
def db(monkeypatch):
    banco = FakeProfilesDB()
    banco.add_pair(100, 1, 10)
    banco.add_pair(101, 1, 11)
    banco.add_pair(102, 2, 10)
    banco.add_pair(103, 2, 11, active=False)
    mod.reset_tables_cache()
    monkeypatch.setattr(mod, "get_conn", banco.conn)
    monkeypatch.setattr(mod, "is_duplicate_key_error", lambda e: isinstance(e, _Duplicate))
    yield banco
    mod.reset_tables_cache()


def _client(papel: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(mod.router)
    uid = ADMIN if papel == "admin" else USUARIO
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=uid, role=papel, source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_vocabulario_e_o_export_do_dono(db):
    r = _client("regular").get("/resistance-profiles/vocabulary")
    assert r.status_code == 200 and r.json() == dono.export_vocabulary()
    assert r.json()["default_status"] == dono.ADMIN_DEFAULT_STATUS
    j = r.json()
    assert j["actions"] == {"approve": dono.APPROVE_STATUS, "reject": dono.REJECT_STATUS}
    assert j["suggest_max_pairs"] == dono.SUGGEST_MAX_PAIRS and j["pending_statuses"] == ["suggested"]
    assert "human_review_statuses" in j


def test_lista_so_pares_ativos_com_ausente_como_desconhecido_e_sem_n_mais_1(db):
    db.add_profile(100, **{k: v for k, v in CORPO.items() if k not in ("flags", "status")}, status="verified",
                   origin="admin", flags_json='["friction"]')
    r = _client("regular").get("/resistance-profiles")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["total"] == 3 and corpo["limit"] == mod.LIST_DEFAULT_LIMIT
    ids = [i["equipment_exercise_id"] for i in corpo["items"]]
    assert ids == [100, 101, 102]  # Leg press/Agachamento, Leg press/Crucifixo, Polia/Agachamento; 103 inativo fora
    com, sem = corpo["items"][0], corpo["items"][1]
    assert com["has_profile"] and com["status"] == "verified" and com["reliable"] is True
    assert com["flags"] == ["friction"] and com["signature"] == dono.signature(com)
    assert com["signature_label"] == dono.signature_label(com)
    assert not sem["has_profile"] and sem["source"] == "unknown" and sem["status"] is None and sem["reliable"] is False
    assert db.statements == 3  # tabelas + count + lista, independente do numero de pares


def test_filtros_e_paginacao(db):
    db.add_profile(100, status="reviewed", origin="admin", confidence="high")
    db.add_profile(101, status="suggested", confidence="low")
    c = _client("regular")

    def ids(**q):
        r = c.get("/resistance-profiles", params=q)
        assert r.status_code == 200, r.text
        return [i["equipment_exercise_id"] for i in r.json()["items"]]

    assert ids(status="reviewed") == [100]
    assert ids(confidence="low") == [101]
    assert ids(pending="true") == [101, 102]  # sugerido + sem perfil
    assert ids(equipment_id=2) == [102]
    assert ids(exercise_id=11) == [101]
    assert ids(limit=1, offset=1) == [101]
    assert c.get("/resistance-profiles", params={"limit": 1}).json()["total"] == 3


@pytest.mark.parametrize("q", [{"status": "nope"}, {"confidence": "nope"}, {"limit": mod.LIST_MAX_LIMIT + 1},
                               {"limit": 0}, {"offset": -1}])
def test_filtro_invalido_422(db, q):
    assert _client("regular").get("/resistance-profiles", params=q).status_code == 422


def test_filtro_invalido_do_dono_traz_code_e_field(db):
    d = _client("regular").get("/resistance-profiles", params={"status": "nope"}).json()["detail"]
    assert (d["code"], d["field"]) == ("invalid_code", "status")


def test_get_par_com_e_sem_perfil_e_404(db):
    c = _client("regular")
    assert c.get("/resistance-profiles/100").json()["has_profile"] is False
    assert c.get("/resistance-profiles/103").status_code == 404  # inativo
    assert c.get("/resistance-profiles/999").status_code == 404


def test_put_cria_valida_pelo_dono_carimba_revisor_e_grava_historico(db):
    r = _client().put("/resistance-profiles/100", json={**CORPO, "reason": "conferido na academia"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["status"] == "reviewed" and corpo["origin"] == "admin" and corpo["reliable"] is True
    assert corpo["reviewed_by"] == ADMIN and corpo["reviewed_at"] is not None
    assert corpo["direction_changes"] == "unknown" and corpo["vocabulary_version"] == dono.RESISTANCE_VOCABULARY_VERSION
    assert corpo["signature"].startswith("plate_loaded:lever:guided_arc:mid_peak:joint_axis_relative")
    (h,) = db.history
    assert h["before_json"] is None and h["actor_user_id"] == ADMIN and h["origin"] == "admin"
    assert h["reason"] == "conferido na academia"
    assert json.loads(h["after_json"])["flags"] == ["friction"]


def test_put_atualiza_guarda_antes_e_depois_e_suggested_nao_carimba_revisor(db):
    db.add_profile(100, source="cable", status="verified", origin="admin", reviewed_by=9)
    r = _client().put("/resistance-profiles/100", json={**CORPO, "status": "suggested"})
    assert r.status_code == 200, r.text
    assert r.json()["reviewed_by"] is None and r.json()["reviewed_at"] is None
    assert len(db.profiles) == 1
    (h,) = db.history
    antes, depois = json.loads(h["before_json"]), json.loads(h["after_json"])
    assert antes["source"] == "cable" and antes["status"] == "verified"
    assert depois["source"] == "plate_loaded" and depois["status"] == "suggested"


@pytest.mark.parametrize("corpo,code,field", [
    ({**CORPO, "mechanism": "xyz"}, "invalid_code", "mechanism"),
    ({**CORPO, "flags": ["xyz"]}, "invalid_flag", "flags"),
    ({**CORPO, "status": "xyz"}, "invalid_code", "status"),
    ({**CORPO, "confidence": "xyz"}, "invalid_code", "confidence"),
])
def test_put_erro_tipado_do_dono_vira_422_sem_gravar(db, corpo, code, field):
    r = _client().put("/resistance-profiles/100", json=corpo)
    assert r.status_code == 422
    assert (r.json()["detail"]["code"], r.json()["detail"]["field"]) == (code, field)
    assert not db.profiles and not db.history


@pytest.mark.parametrize("corpo", [{**CORPO, "origin": "ai"}, {**CORPO, "extra": 1},
                                   {k: v for k, v in CORPO.items() if k != "status"},
                                   {**CORPO, "flags": "friction"}])
def test_put_corpo_estrito_422(db, corpo):
    assert _client().put("/resistance-profiles/100", json=corpo).status_code == 422


def test_put_transicao_recusada_pelo_dono_422_sem_gravar(db, monkeypatch):
    db.add_profile(100, status="suggested")
    monkeypatch.setattr(dono, "can_transition", lambda *a: False)
    r = _client().put("/resistance-profiles/100", json=CORPO)
    assert r.status_code == 422 and r.json()["detail"] == {"error": "invalid_transition", "code": "invalid_transition",
                                                           "field": "status"}
    assert db.profiles[1]["status"] == "suggested" and not db.history


def test_put_par_inexistente_ou_inativo_404(db):
    assert _client().put("/resistance-profiles/103", json=CORPO).status_code == 404
    assert _client().put("/resistance-profiles/999", json=CORPO).status_code == 404
    assert not db.profiles


def test_put_so_admin(db):
    assert _client("regular").put("/resistance-profiles/100", json=CORPO).status_code == 403
    assert not db.profiles


def test_put_corrida_de_insert_vira_409_e_desfaz(db):
    db.inject_duplicate_on_insert = True
    assert _client().put("/resistance-profiles/100", json=CORPO).status_code == 409
    assert not db.profiles and not db.history


def test_put_deadlock_1213_no_insert_vira_409(db):
    db.inject_deadlock_on_insert = True
    assert _client().put("/resistance-profiles/100", json=CORPO).status_code == 409
    assert not db.profiles and not db.history


def test_put_admin_zera_model_version(db):
    db.add_pair(104, 1, 10)
    db.add_profile(104, model_version="haiku-x")
    assert _client().put("/resistance-profiles/104", json=CORPO).json()["model_version"] is None


def test_cobertura_conta_desconhecido_pelo_criterio_do_dono(db):
    db.add_profile(100, direction_changes="single")  # so direction_changes preenchido: nao e desconhecido
    db.add_profile(101)  # tudo unknown
    assert _client().get("/resistance-profiles/coverage").json()["unknown"] == 2  # 101 + 102 (sem perfil)


def test_historico_mais_novo_primeiro_e_vazio_sem_perfil(db):
    c = _client()
    assert c.get("/resistance-profiles/100/history").json() == {"items": []}
    c.put("/resistance-profiles/100", json=CORPO)
    c.put("/resistance-profiles/100", json={**CORPO, "status": "verified"})
    itens = c.get("/resistance-profiles/100/history").json()["items"]
    assert [i["after"]["status"] for i in itens] == ["verified", "reviewed"]
    assert itens[0]["before"]["status"] == "reviewed" and itens[1]["before"] is None
    assert c.get("/resistance-profiles/103/history").status_code == 404


def test_cobertura_conta_pares_ativos_status_e_desconhecidos(db):
    db.add_profile(100, source="cable", status="reviewed", origin="admin")
    db.add_profile(101)  # todos unknown, sugerido
    r = _client("regular").get("/resistance-profiles/coverage")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "vocabulary_version": dono.RESISTANCE_VOCABULARY_VERSION, "active_pairs": 3, "with_profile": 2,
        "without_profile": 1, "by_status": {"suggested": 1, "reviewed": 1, "verified": 0, "rejected": 0},
        "unknown": 2,
    }


def test_sem_as_tabelas_503_citando_a_126_em_todas_as_rotas(db):
    db.tables = False
    c = _client()
    respostas = [c.get("/resistance-profiles"), c.get("/resistance-profiles/coverage"),
                 c.get("/resistance-profiles/100"), c.get("/resistance-profiles/100/history"),
                 c.put("/resistance-profiles/100", json=CORPO)]
    for r in respostas:
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["migration"] == "126" and "126" in r.json()["detail"]["message"]
    assert c.get("/resistance-profiles/vocabulary").status_code == 200  # nao precisa de banco


def test_regular_le_a_cobertura(db):
    assert _client("regular").get("/resistance-profiles/coverage").status_code == 200


# --- POST /suggest (job assincrono de sugestao por IA) ---


@pytest.fixture
def despacho(monkeypatch):
    chamadas: dict = {"jobs": [], "invokes": []}
    monkeypatch.setattr(mod.async_jobs, "create_job",
                        lambda tipo, request=None, *, owner_user_id: chamadas["jobs"].append((tipo, request, owner_user_id)) or "job-1")
    monkeypatch.setattr(mod.async_jobs, "invoke_async", lambda env, payload: chamadas["invokes"].append((env, payload)))
    return chamadas


def test_suggest_com_ids_cria_job_para_a_lambda_de_ia(db, despacho):
    r = _client().post("/resistance-profiles/suggest", json={"equipment_exercise_ids": [100, 101, 100]})
    assert r.status_code == 202 and r.json() == {"job_id": "job-1", "pairs": 2}
    assert despacho["jobs"] == [(dono.SUGGEST_JOB_TYPE, {"payload": {"equipment_exercise_ids": [100, 101]}, "user_id": ADMIN}, ADMIN)]
    env, evento = despacho["invokes"][0]
    assert env == "WORKOUT_AI_FUNCTION_NAME"
    assert evento["job_type"] == dono.SUGGEST_JOB_TYPE and evento["payload"] == {"equipment_exercise_ids": [100, 101]}


def test_suggest_sem_ids_escolhe_pendentes_sem_revisado_sem_perfil_primeiro(db, despacho):
    db.add_profile(100, status="reviewed", origin="admin")
    db.add_profile(101, status="suggested")
    r = _client().post("/resistance-profiles/suggest", json={})
    assert r.status_code == 202
    assert despacho["invokes"][0][1]["payload"]["equipment_exercise_ids"] == [102, 101]  # 103 inativo, 100 revisado


def test_suggest_limit_corta_a_escolha(db, despacho):
    _client().post("/resistance-profiles/suggest", json={"limit": 1})
    assert despacho["invokes"][0][1]["payload"]["equipment_exercise_ids"] == [100]


def test_suggest_sem_pendentes_nao_cria_job(db, despacho):
    for pid in (100, 101, 102):
        db.add_profile(pid, status="verified", origin="admin")
    r = _client().post("/resistance-profiles/suggest", json={})
    assert r.status_code == 202 and r.json() == {"job_id": None, "pairs": 0}
    assert not despacho["jobs"] and not despacho["invokes"]


@pytest.mark.parametrize("corpo", [
    {"limit": dono.SUGGEST_MAX_PAIRS + 1}, {"limit": 0}, {"limit": True}, {"equipment_exercise_ids": []},
    {"equipment_exercise_ids": list(range(1, dono.SUGGEST_MAX_PAIRS + 2))}, {"equipment_exercise_ids": [0]},
    {"equipment_exercise_ids": ["a"]}, {"equipment_exercise_ids": [True]}, {"extra": 1},
])
def test_suggest_corpo_estrito_e_teto_422(db, despacho, corpo):
    assert _client().post("/resistance-profiles/suggest", json=corpo).status_code == 422
    assert not despacho["jobs"]


def test_suggest_so_admin_do_catalogo(db, despacho):
    assert _client("regular").post("/resistance-profiles/suggest", json={}).status_code == 403
    assert not despacho["jobs"]


def test_suggest_falha_de_despacho_503(db, monkeypatch):
    monkeypatch.setattr(mod.async_jobs, "create_job", lambda *a, **k: "job-1")

    def _falha(*a, **k):
        raise RuntimeError("lambda fora")

    monkeypatch.setattr(mod.async_jobs, "invoke_async", _falha)
    r = _client().post("/resistance-profiles/suggest", json={"equipment_exercise_ids": [100]})
    assert r.status_code == 503 and r.json()["detail"]["error"] == "suggest_dispatch_failed"


def test_suggest_sem_as_tabelas_503(db, despacho):
    db.tables = False
    assert _client().post("/resistance-profiles/suggest", json={}).status_code == 503
