"""Dono e visibilidade dos Modelos de treino (ADR 0011, lote 6A; migracao 116).

Controle de acesso, nao regra de motor. Cobre:
- a decisao pura `template_access_decision` (tabela);
- `authorize_template` com cursor falso;
- as rotas por HTTP com o resolvedor real de permissoes por papel
  (`permissoes_por_papel`): PT dono, PT em publico alheio (le, nao escreve),
  PT em privado alheio (404), admin em tudo, usuario negado;
- listagem (filtro publico + proprios), criar com `visibility`, duplicar
  (privado por padrao), atribuicao com e sem vinculo, `users-lookup` por escopo.

Rodar: cd api && python -m pytest tests/unit/test_modelos_dono_visibilidade.py -q
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.access import service as access_service
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

ADMIN, USUARIO, PT, OUTRO_PT, ALUNO, ESTRANHO = 1, 7, 8, 9, 50, 60
PAPEIS = {ADMIN: "admin", USUARIO: "regular", PT: "personal_trainer", OUTRO_PT: "personal_trainer"}

# id -> (dono, visibilidade)
PROPRIO_PRIVADO, PUBLICO_ALHEIO, PRIVADO_ALHEIO, ORFAO_PUBLICO = 10, 20, 30, 40
PROPRIO_PUBLICO = 11
MODELOS = {
    PROPRIO_PRIVADO: (PT, "privado"),
    PROPRIO_PUBLICO: (PT, "publico"),
    PUBLICO_ALHEIO: (OUTRO_PT, "publico"),
    PRIVADO_ALHEIO: (OUTRO_PT, "privado"),
    ORFAO_PUBLICO: (None, "publico"),
}
GUARD_SQL = "SELECT id, created_by_user_id, visibility FROM workout_plan_templates"


# ---------------------------------------------------------------------------
# Banco falso
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, db: "FakeDB"):
        self.db = db
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0
        self.lastrowid = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        p = tuple(params or ())
        self.db.sql.append((flat, p))
        self._rows, self.rowcount = [], 0
        if flat.startswith(GUARD_SQL):
            self.db.guard_calls += 1
            dono_vis = MODELOS.get(int(p[0]))
            if dono_vis:
                self._rows = [{"id": int(p[0]), "created_by_user_id": dono_vis[0], "visibility": dono_vis[1]}]
        elif flat.startswith("SELECT id, template_type, volume_counting_mode, training_goal, visibility"):
            dono_vis = MODELOS.get(int(p[0]))
            self._rows = [{"id": int(p[0]), "template_type": None, "volume_counting_mode": None,
                           "training_goal": None, "visibility": dono_vis[1]}] if dono_vis else []
        elif flat.startswith("UPDATE workout_plan_templates SET name"):
            self.db.writes.append(("update", p))
            self.rowcount = 1
        elif flat.startswith("SELECT id FROM workout_plan_templates WHERE id"):
            self._rows = [{"id": int(p[0])}] if int(p[0]) in MODELOS else []
        elif flat.startswith("UPDATE workout_plan_templates SET is_active"):
            self.db.writes.append(("status", p))
            self.rowcount = 1
        elif flat.startswith("INSERT INTO workout_plan_templates"):
            self.db.writes.append(("insert", p))
            self.lastrowid = 99
            self.rowcount = 1
        elif "FROM workout_plan_template_assignments a" in flat:
            self._rows = [dict(r) for r in self.db.assignments]
        elif flat.startswith("SELECT t.id"):
            self._rows = []
        elif "FROM users" in flat or "FROM trainer_students" in flat:
            self._rows = [{"id": ALUNO, "username": "aluno", "full_name": "Aluno"}]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self):
        self.sql: list[tuple[str, tuple]] = []
        self.writes: list[tuple[str, tuple]] = []
        self.guard_calls = 0
        self.assignments: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def transaction(self):
        db = self

        class Conn:
            def cursor(self):
                return FakeCursor(db)

        yield Conn()


@pytest.fixture
def db(monkeypatch):
    banco = FakeDB()
    monkeypatch.setattr(wpt, "transaction", banco.transaction)
    # Leitura do Modelo e selos: fora do escopo (o guard e o que se testa).
    monkeypatch.setattr(wpt, "_get_template", lambda template_id, **_: {"id": template_id, "assignments": []})
    monkeypatch.setattr(wpt, "_with_draft_flags", lambda t: t)
    monkeypatch.setattr(wpt, "_attach_safety_warnings", lambda t: t)
    monkeypatch.setattr(wpt.template_drafts, "draft_stamps", lambda cur, ids: {})
    vinculos = {(PT, ALUNO)}
    monkeypatch.setattr(access_service, "has_active_link", lambda t, s: (int(t), int(s)) in vinculos)
    monkeypatch.setattr(
        access_service, "active_student_ids", lambda t, ids: {int(i) for i in ids if (int(t), int(i)) in vinculos}
    )
    return banco


def _client(uid: int) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    ident = Identity(user_id=uid, role=PAPEIS.get(uid, "regular"), source="session")
    app.dependency_overrides[get_current_identity] = lambda: ident
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Decisao pura
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope, dono, vis, for_write, esperado",
    [
        (None, PT, "publico", False, 403),
        ("todos", OUTRO_PT, "privado", True, 200),
        ("todos", None, "privado", True, 200),
        ("alunos", PT, "privado", True, 200),
        ("alunos", PT, "publico", True, 200),
        ("alunos", OUTRO_PT, "publico", False, 200),
        ("alunos", OUTRO_PT, "publico", True, 403),
        ("alunos", OUTRO_PT, "privado", False, 404),
        ("alunos", OUTRO_PT, "privado", True, 404),
        ("alunos", OUTRO_PT, "desconhecida", False, 404),
        ("alunos", OUTRO_PT, None, False, 200),
        ("alunos", None, "publico", False, 200),
        ("alunos", None, "publico", True, 403),
        ("alunos", None, "privado", False, 404),
        ("proprios", PT, "privado", False, 200),
        ("proprios", OUTRO_PT, "publico", True, 403),
    ],
)
def test_tabela_de_template_access_decision(scope, dono, vis, for_write, esperado):
    assert wpt.template_access_decision(scope, PT, dono, vis, for_write=for_write) == esperado


def test_constantes_de_visibilidade():
    assert wpt.VISIBILITIES == ("publico", "privado")
    assert wpt.DUPLICATE_DEFAULT_VISIBILITY == "privado"
    assert wpt._resolve_visibility(None, "publico") == "publico"
    assert wpt._resolve_visibility("privado", "publico") == "privado"
    assert wpt._resolve_visibility("xpto", "privado") == "privado"


# ---------------------------------------------------------------------------
# authorize_template com cursor falso
# ---------------------------------------------------------------------------


def _perms(uid):
    return access_service.resolve_permissions(Identity(user_id=uid, role=PAPEIS[uid], source="session"))


def _ident(uid):
    return Identity(user_id=uid, role=PAPEIS.get(uid, "regular"), source="session")


def test_authorize_template_admin_nao_consulta_o_banco():
    db = FakeDB()
    with db.transaction() as conn:
        assert wpt.authorize_template(conn.cursor(), _ident(ADMIN), _perms(ADMIN), PRIVADO_ALHEIO, "editar",
                                      for_write=True) is None
    assert db.guard_calls == 0


def test_authorize_template_pt():
    db = FakeDB()
    with db.transaction() as conn:
        cur = conn.cursor()
        perms = _perms(PT)
        row = wpt.authorize_template(cur, _ident(PT), perms, PROPRIO_PRIVADO, "editar", for_write=True)
        assert row["created_by_user_id"] == PT
        assert wpt.authorize_template(cur, _ident(PT), perms, PUBLICO_ALHEIO, "ver", for_write=False)["id"] == 20
        with pytest.raises(HTTPException) as exc:
            wpt.authorize_template(cur, _ident(PT), perms, PUBLICO_ALHEIO, "editar", for_write=True)
        assert exc.value.status_code == 403 and exc.value.detail == wpt.TEMPLATE_WRITE_FORBIDDEN
        for tid in (PRIVADO_ALHEIO, 999):
            with pytest.raises(HTTPException) as exc:
                wpt.authorize_template(cur, _ident(PT), perms, tid, "ver", for_write=False)
            assert exc.value.status_code == 404 and exc.value.detail == wpt.TEMPLATE_NOT_FOUND


def test_authorize_template_usuario_sem_editar_e_403():
    db = FakeDB()
    with db.transaction() as conn:
        with pytest.raises(HTTPException) as exc:
            wpt.authorize_template(conn.cursor(), _ident(USUARIO), _perms(USUARIO), PROPRIO_PRIVADO, "editar",
                                   for_write=True)
    assert exc.value.status_code == 403 and exc.value.detail == FORBIDDEN_DETAIL
    assert db.guard_calls == 0


# ---------------------------------------------------------------------------
# Rotas por HTTP
# ---------------------------------------------------------------------------


def test_pt_dono_le_e_escreve(db):
    c = _client(PT)
    assert c.get(f"/workout-plan-templates/{PROPRIO_PRIVADO}").status_code == 200
    assert c.patch(f"/workout-plan-templates/{PROPRIO_PRIVADO}/status", json={"is_active": False}).status_code == 200
    assert db.writes == [("status", (False, PROPRIO_PRIVADO))]


def test_pt_publico_alheio_le_mas_nao_escreve(db):
    c = _client(PT)
    assert c.get(f"/workout-plan-templates/{PUBLICO_ALHEIO}").status_code == 200
    resp = c.patch(f"/workout-plan-templates/{PUBLICO_ALHEIO}/status", json={"is_active": False})
    assert resp.status_code == 403 and resp.json()["detail"] == wpt.TEMPLATE_WRITE_FORBIDDEN
    assert c.delete(f"/workout-plan-templates/{PUBLICO_ALHEIO}").status_code == 403
    assert c.put(f"/workout-plan-templates/{PUBLICO_ALHEIO}/methodology-intent",
                 json={"intent": "standard"}).status_code == 403
    assert db.writes == []


def test_pt_privado_alheio_e_404_em_leitura_e_escrita(db):
    c = _client(PT)
    for resp in (
        c.get(f"/workout-plan-templates/{PRIVADO_ALHEIO}"),
        c.get(f"/workout-plan-templates/{PRIVADO_ALHEIO}/versions"),
        c.patch(f"/workout-plan-templates/{PRIVADO_ALHEIO}/status", json={"is_active": True}),
        c.post(f"/workout-plan-templates/{PRIVADO_ALHEIO}/duplicate"),
    ):
        assert resp.status_code == 404 and resp.json()["detail"] == wpt.TEMPLATE_NOT_FOUND
    assert db.writes == []


def test_admin_le_e_escreve_qualquer_modelo_sem_consultar_o_dono(db):
    c = _client(ADMIN)
    assert c.get(f"/workout-plan-templates/{PRIVADO_ALHEIO}").status_code == 200
    assert c.patch(f"/workout-plan-templates/{PRIVADO_ALHEIO}/status", json={"is_active": True}).status_code == 200
    assert db.guard_calls == 0


@pytest.mark.parametrize("metodo, caminho", [
    ("GET", "/workout-plan-templates"),
    ("GET", "/workout-plan-templates/users-lookup"),
    ("GET", f"/workout-plan-templates/{PROPRIO_PRIVADO}"),
    ("PATCH", f"/workout-plan-templates/{PROPRIO_PRIVADO}/status"),
    ("POST", f"/workout-plan-templates/{PUBLICO_ALHEIO}/duplicate"),
])
def test_usuario_e_negado_antes_de_tocar_o_banco(db, metodo, caminho):
    resp = _client(USUARIO).request(metodo, caminho, json={"is_active": True} if metodo == "PATCH" else None)
    assert resp.status_code == 403 and resp.json()["detail"] == FORBIDDEN_DETAIL
    assert db.sql == []


def test_listagem_do_pt_filtra_publico_e_proprios_e_a_do_admin_nao(db):
    assert _client(PT).get("/workout-plan-templates").status_code == 200
    sql, params = next((s, p) for s, p in db.sql if s.startswith("SELECT t.id"))
    assert "(t.visibility = %s OR t.created_by_user_id = %s)" in sql
    assert params[:2] == ("publico", PT)
    db.sql.clear()
    assert _client(ADMIN).get("/workout-plan-templates").status_code == 200
    sql, params = next((s, p) for s, p in db.sql if s.startswith("SELECT t.id"))
    assert "(t.visibility = %s OR" not in sql and PT not in params


def _cria(monkeypatch, uid, corpo):
    for nome in ("_validate_template_payload", "_validate_technique_codes", "_enforce_template_safety"):
        monkeypatch.setattr(wpt, nome, lambda *a, **k: None)
    monkeypatch.setattr(wpt, "clear_sets_only_technique_fields", lambda payload: None)
    monkeypatch.setattr(wpt, "_resolve_origin", lambda cur, payload, user_id: ("manual", None))
    monkeypatch.setattr(wpt, "_write_version", lambda *a, **k: None)
    for nome in ("_attach_methodology_volume_warnings", "_attach_volume_coverage_warnings"):
        monkeypatch.setattr(wpt, nome, lambda t: t)
    return _client(uid).post("/workout-plan-templates", json=corpo)


@pytest.mark.parametrize("vis, esperado", [(None, "publico"), ("privado", "privado"), ("publico", "publico")])
def test_criar_grava_o_ator_como_dono_e_a_visibilidade(db, monkeypatch, vis, esperado):
    corpo = {"name": "M", "slots": [{"sequence": 1, "name": "A", "items": []}]}
    if vis:
        corpo["visibility"] = vis
    assert _cria(monkeypatch, PT, corpo).status_code == 200
    (_, params), = [w for w in db.writes if w[0] == "insert"]
    assert params[6] == PT and params[-1] == esperado


def test_criar_com_visibilidade_invalida_e_422(db, monkeypatch):
    corpo = {"name": "M", "slots": [{"sequence": 1, "name": "A", "items": []}], "visibility": "secreto"}
    assert _cria(monkeypatch, PT, corpo).status_code == 422
    assert db.writes == []


def test_put_de_visibilidade_so_dono_ou_admin(db):
    corpo = {"name": "M", "slots": [{"sequence": 1, "name": "A", "items": []}], "visibility": "privado"}
    resp = _client(PT).put(f"/workout-plan-templates/{PUBLICO_ALHEIO}", json=corpo)
    assert resp.status_code == 403 and resp.json()["detail"] == wpt.TEMPLATE_WRITE_FORBIDDEN
    assert db.writes == []


def test_duplicar_publico_alheio_passa_pelo_guard(db, monkeypatch):
    lidos = []
    monkeypatch.setattr(wpt, "_get_template", lambda template_id, **_: lidos.append(template_id))
    resp = _client(PT).post(f"/workout-plan-templates/{PUBLICO_ALHEIO}/duplicate")
    # Passou do guard (leitura) e chegou ao handler, que releu o original.
    assert lidos == [PUBLICO_ALHEIO] and resp.status_code == 404


def test_atribuicao_com_e_sem_vinculo(db):
    pt = wpt.TemplateViewer(_ident(PT), "alunos")
    wpt._authorize_assignment_target(pt, PT)  # a si mesmo
    wpt._authorize_assignment_target(pt, ALUNO)  # aluno vinculado
    with pytest.raises(HTTPException) as exc:
        wpt._authorize_assignment_target(pt, ESTRANHO)
    assert exc.value.status_code == 403 and exc.value.detail == FORBIDDEN_DETAIL
    wpt._authorize_assignment_target(wpt.TemplateViewer(_ident(ADMIN), "todos"), ESTRANHO)


def test_atribuir_e_desatribuir_sem_vinculo_e_403_por_http(db):
    c = _client(PT)
    resp = c.post(f"/workout-plan-templates/{PUBLICO_ALHEIO}/assignments", json={"user_id": ESTRANHO})
    assert resp.status_code == 403 and resp.json()["detail"] == FORBIDDEN_DETAIL
    resp = c.delete(f"/workout-plan-templates/{PROPRIO_PRIVADO}/assignments/{ESTRANHO}")
    assert resp.status_code == 403
    assert db.writes == []


def test_lista_de_atribuicoes_do_pt_so_tem_ele_e_seus_alunos(db):
    db.assignments = [{"id": i, "user_id": u} for i, u in enumerate((PT, ALUNO, ESTRANHO), start=1)]
    resp = _client(PT).get(f"/workout-plan-templates/{PUBLICO_ALHEIO}/assignments")
    assert resp.status_code == 200
    assert [r["user_id"] for r in resp.json()["items"]] == [PT, ALUNO]
    resp = _client(ADMIN).get(f"/workout-plan-templates/{PUBLICO_ALHEIO}/assignments")
    assert [r["user_id"] for r in resp.json()["items"]] == [PT, ALUNO, ESTRANHO]


def test_users_lookup_por_escopo(db):
    resp = _client(PT).get("/workout-plan-templates/users-lookup", params={"search": "al"})
    assert resp.status_code == 200 and resp.json()["items"][0]["id"] == ALUNO
    sql, params = db.sql[-1]
    assert sql.startswith(" ".join(access_service.ACTIVE_STUDENTS_LOOKUP_SQL.split()))
    assert "ts.status = 'ativo'" in sql and "s.is_active = 1" in sql and "personal_trainer" in sql
    assert params == (PT, "%al%", "%al%")
    db.sql.clear()
    assert _client(ADMIN).get("/workout-plan-templates/users-lookup").status_code == 200
    sql, params = db.sql[-1]
    assert sql.startswith("SELECT id, username, full_name FROM users WHERE is_active = 1") and params == ()


# ---------------------------------------------------------------------------
# Correcoes da revisao do 6A
# ---------------------------------------------------------------------------

CORPO = {"name": "M", "slots": [{"sequence": 1, "name": "A", "items": []}]}
TRES_ATRIBUICOES = [{"id": i, "user_id": u} for i, u in enumerate((PT, ALUNO, ESTRANHO), start=1)]


@pytest.fixture
def escrita(db, monkeypatch):
    """PUT/PATCH chegam ao fim com o Modelo relido trazendo 3 atribuicoes
    (a do PT, a do aluno dele e a de um aluno de outro treinador)."""
    for nome in ("_validate_template_payload", "_validate_technique_codes", "_enforce_template_safety"):
        monkeypatch.setattr(wpt, nome, lambda *a, **k: None)
    monkeypatch.setattr(wpt, "clear_sets_only_technique_fields", lambda payload: None)
    monkeypatch.setattr(wpt, "_write_version", lambda *a, **k: None)
    monkeypatch.setattr(wpt.template_drafts, "discard_draft", lambda cur, tid: False)
    for nome in ("_attach_methodology_volume_warnings", "_attach_volume_coverage_warnings"):
        monkeypatch.setattr(wpt, nome, lambda t: t)
    monkeypatch.setattr(
        wpt, "_get_template", lambda template_id, **_: {"id": template_id, "assignments": list(TRES_ATRIBUICOES)}
    )
    return db


def _vis_gravada(db):
    (_, params), = [w for w in db.writes if w[0] == "update"]
    return params[-2]


def test_put_do_dono_pt_em_publico_nao_mostra_alunos_de_outro_treinador(escrita):
    resp = _client(PT).put(f"/workout-plan-templates/{PROPRIO_PUBLICO}", json=CORPO)
    assert resp.status_code == 200, resp.text
    assert [a["user_id"] for a in resp.json()["assignments"]] == [PT, ALUNO]


def test_patch_status_e_get_filtram_atribuicoes_do_pt_e_nao_as_do_admin(escrita):
    resp = _client(PT).patch(f"/workout-plan-templates/{PROPRIO_PUBLICO}/status", json={"is_active": False})
    assert resp.status_code == 200 and [a["user_id"] for a in resp.json()["assignments"]] == [PT, ALUNO]
    resp = _client(PT).get(f"/workout-plan-templates/{PUBLICO_ALHEIO}")
    assert [a["user_id"] for a in resp.json()["assignments"]] == [PT, ALUNO]
    resp = _client(ADMIN).patch(f"/workout-plan-templates/{PROPRIO_PUBLICO}/status", json={"is_active": True})
    assert [a["user_id"] for a in resp.json()["assignments"]] == [PT, ALUNO, ESTRANHO]


def test_present_e_o_helper_unico_de_filtro():
    sem_filtro = {"id": 1, "assignments": [{"user_id": ESTRANHO}]}
    assert wpt._present(sem_filtro, wpt.TemplateViewer(_ident(ADMIN), "todos")) is sem_filtro
    assert wpt._present(None, wpt.TemplateViewer(_ident(PT), "alunos")) is None


@pytest.mark.parametrize("de, para", [("privado", "publico"), ("publico", "privado")])
def test_put_do_dono_troca_a_visibilidade(escrita, de, para):
    tid = PROPRIO_PRIVADO if de == "privado" else PROPRIO_PUBLICO
    resp = _client(PT).put(f"/workout-plan-templates/{tid}", json=dict(CORPO, visibility=para))
    assert resp.status_code == 200, resp.text
    assert _vis_gravada(escrita) == para


def test_put_sem_visibilidade_mantem_a_atual(escrita):
    assert _client(PT).put(f"/workout-plan-templates/{PROPRIO_PRIVADO}", json=CORPO).status_code == 200
    assert _vis_gravada(escrita) == "privado"


def test_put_de_visibilidade_por_nao_dono_e_admin(escrita):
    c = _client(PT)
    resp = c.put(f"/workout-plan-templates/{PRIVADO_ALHEIO}", json=dict(CORPO, visibility="publico"))
    assert resp.status_code == 404 and resp.json()["detail"] == wpt.TEMPLATE_NOT_FOUND
    resp = c.put(f"/workout-plan-templates/{PUBLICO_ALHEIO}", json=dict(CORPO, visibility="privado"))
    assert resp.status_code == 403
    assert escrita.writes == []
    resp = _client(ADMIN).put(f"/workout-plan-templates/{PRIVADO_ALHEIO}", json=dict(CORPO, visibility="publico"))
    assert resp.status_code == 200, resp.text
    assert _vis_gravada(escrita) == "publico"


# --- GET /hr-zones?template_id= ---------------------------------------------


@pytest.fixture
def zonas(db, monkeypatch):
    from app import athlete_hr_zones

    sujeitos = []

    def _resolve(cur, identity, template_id):
        sujeitos.append((int(identity.user_id), template_id))
        return athlete_hr_zones.ZonesSubject(kind=athlete_hr_zones.SUBJECT_SELF, user_id=int(identity.user_id))

    monkeypatch.setattr(athlete_hr_zones, "resolve_template_zones_subject", _resolve)
    monkeypatch.setattr(athlete_hr_zones, "load_athlete_zones", lambda cur, uid: None)
    monkeypatch.setattr(athlete_hr_zones, "zones_payload", lambda athlete: {"zones": []})
    return sujeitos


def _client_me(uid: int) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.me_router)
    ident = Identity(user_id=uid, role=PAPEIS.get(uid, "regular"), source="session")
    app.dependency_overrides[get_current_identity] = lambda: ident
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("tid", [PRIVADO_ALHEIO, 999])
def test_hr_zones_pt_privado_alheio_ou_inexistente_e_404(db, zonas, tid):
    resp = _client_me(PT).get("/workout-plan-templates/hr-zones", params={"template_id": tid})
    assert resp.status_code == 404 and resp.json()["detail"] == wpt.TEMPLATE_NOT_FOUND
    assert zonas == []


@pytest.mark.parametrize("tid", [PROPRIO_PRIVADO, PUBLICO_ALHEIO])
def test_hr_zones_pt_proprio_ou_publico_resolve_o_sujeito(db, zonas, tid):
    resp = _client_me(PT).get("/workout-plan-templates/hr-zones", params={"template_id": tid})
    assert resp.status_code == 200, resp.text
    assert zonas == [(PT, tid)] and db.guard_calls == 1


def test_hr_zones_pt_sem_template_id_nao_consulta_o_dono(db, zonas):
    assert _client_me(PT).get("/workout-plan-templates/hr-zones").status_code == 200
    assert db.guard_calls == 0


@pytest.mark.parametrize("uid", [ALUNO, ADMIN])
def test_hr_zones_aluno_e_admin_inalterados_mesmo_em_privado_alheio(db, zonas, uid):
    # O aluno usa a rota com o Modelo atribuido a ele (nem dono, nem publico).
    resp = _client_me(uid).get("/workout-plan-templates/hr-zones", params={"template_id": PRIVADO_ALHEIO})
    assert resp.status_code == 200, resp.text
    assert zonas == [(uid, PRIVADO_ALHEIO)] and db.guard_calls == 0


# --- POST /methodology-volume-preview -----------------------------------------


@pytest.fixture
def preview(db, monkeypatch):
    from app.intelligence import methodology_volume_service as mvs

    monkeypatch.setattr(mvs, "evaluate_slots", lambda *a, **k: "avaliado")
    monkeypatch.setattr(mvs, "evaluation_to_payload", lambda ev: {"ok": ev})
    monkeypatch.setattr(wpt, "resolve_version_now", lambda tid: {"volume_counting_mode": "primary_only"})
    return db


def _preview_body(tid=None):
    body = {"slots": [], "volume_mode": "percent_budget"}
    if tid is not None:
        body["template_id"] = tid
    return body


@pytest.mark.parametrize("tid", [PRIVADO_ALHEIO, 999])
def test_preview_pt_com_modelo_privado_alheio_ou_inexistente_e_404(preview, tid):
    resp = _client(PT).post("/workout-plan-templates/methodology-volume-preview", json=_preview_body(tid))
    assert resp.status_code == 404 and resp.json()["detail"] == wpt.TEMPLATE_NOT_FOUND


@pytest.mark.parametrize("uid, tid, consultas", [
    (PT, PUBLICO_ALHEIO, 1), (PT, PROPRIO_PRIVADO, 1), (PT, None, 0), (ADMIN, PRIVADO_ALHEIO, 0),
])
def test_preview_legivel_segue(preview, uid, tid, consultas):
    resp = _client(uid).post("/workout-plan-templates/methodology-volume-preview", json=_preview_body(tid))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": "avaliado"} and preview.guard_calls == consultas
