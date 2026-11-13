"""Vinculos treinador-aluno (RBAC lote 5A, ADR 0011) contra MySQL 8 real, nos DOIS backends
(PyMySQL de dev e shim da RDS Data API de producao).

Exercita o que os testes unitarios (cursor falso) nao provam:

- INSERT em `trainer_students` e `lastrowid` no shim;
- UNIQUE `uq_ts_active_pair` (coluna gerada `active_flag`): duplicado ativo = 409 e, depois de
  encerrar, o mesmo par pode ser vinculado de novo (historico repetido);
- `has_active_link` verdadeiro/falso: vinculo ativo, encerrado, aluno inativo, treinador
  fora do grupo `personal_trainer`;
- `GET /access/my-students` filtrando pelo proprio usuario.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import access_admin
from app.access.service import has_active_link
from app.security import Identity, get_current_identity

USUARIOS = {
    "admin": ("itg_vinc_admin", "admin"),
    "pt": ("itg_vinc_pt", "personal_trainer"),
    "aluno": ("itg_vinc_aluno", "regular"),
    "aluno2": ("itg_vinc_aluno2", "regular"),
}
NOMES = tuple(nome for nome, _ in USUARIOS.values())


def _marcas(n: int) -> str:
    return ", ".join(["%s"] * n)


def _limpar(raw, ids) -> None:
    if not ids:
        return
    with raw.cursor() as cur:
        marcas = _marcas(len(ids))
        cur.execute(f"DELETE FROM access_audit_log WHERE actor_user_id IN ({marcas})", tuple(ids))
        cur.execute(
            f"DELETE FROM trainer_students WHERE trainer_user_id IN ({marcas}) OR student_user_id IN ({marcas})",
            tuple(ids) + tuple(ids),
        )
        cur.execute(f"DELETE FROM user_access_groups WHERE user_id IN ({marcas})", tuple(ids))


@pytest.fixture
def usuarios(raw, conceder_grupo):
    with raw.cursor() as cur:
        cur.execute(f"SELECT id FROM users WHERE username IN ({_marcas(len(NOMES))})", NOMES)
        antigos = [r["id"] for r in cur.fetchall()]
    _limpar(raw, antigos)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({_marcas(len(NOMES))})", NOMES)
        for chave, (nome, papel) in USUARIOS.items():
            cur.execute(
                "INSERT INTO users (username, full_name, password_hash, role) VALUES (%s, %s, 'x', %s)",
                (nome, nome.upper(), papel),
            )
            ids[chave] = cur.lastrowid
            conceder_grupo(cur, ids[chave], papel)
    raw.commit()
    yield ids
    _limpar(raw, list(ids.values()))
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({_marcas(len(NOMES))})", NOMES)
    raw.commit()


@pytest.fixture
def state(usuarios) -> dict:
    return {"identity": Identity(user_id=usuarios["admin"], role="admin", source="session")}


@pytest.fixture
def client(db_backend, usuarios, state, fake_rds) -> TestClient:
    app = FastAPI()
    app.include_router(access_admin.router)
    app.dependency_overrides[get_current_identity] = lambda: state["identity"]
    yield TestClient(app, raise_server_exceptions=False)
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _criar(client, usuarios, trainer="pt", student="aluno"):
    return client.post(
        "/access/links",
        json={"trainer_user_id": usuarios[trainer], "student_user_id": usuarios[student]},
    )


def _linhas(raw, ids) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, trainer_user_id, student_user_id, status, created_by_user_id, ended_at "
            f"FROM trainer_students WHERE trainer_user_id IN ({_marcas(len(ids))}) ORDER BY id",
            tuple(ids),
        )
        return list(cur.fetchall())


def _auditoria(raw, link_id: int) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT action, actor_user_id, target_type, detalhe FROM access_audit_log "
            "WHERE target_type = 'trainer_link' AND target_id = %s ORDER BY id",
            (link_id,),
        )
        return list(cur.fetchall())


def test_criar_vinculo_grava_linha_e_auditoria(client, raw, usuarios):
    r = _criar(client, usuarios)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ativo" and body["trainer_user_id"] == usuarios["pt"]
    assert body["student_user_id"] == usuarios["aluno"] and body["created_by_user_id"] == usuarios["admin"]
    [linha] = _linhas(raw, [usuarios["pt"]])
    assert linha["id"] == body["id"] and linha["status"] == "ativo" and linha["ended_at"] is None
    [aud] = _auditoria(raw, body["id"])
    assert aud["action"] == "link.create" and aud["actor_user_id"] == usuarios["admin"]


def test_criar_duplicado_ativo_e_409_e_nao_grava(client, raw, usuarios):
    assert _criar(client, usuarios).status_code == 201
    r = _criar(client, usuarios)
    assert r.status_code == 409 and r.json()["error"] == "link_already_active"
    assert len(_linhas(raw, [usuarios["pt"]])) == 1


def test_recusas_de_negocio(client, raw, usuarios, state):
    # treinador sem o grupo personal_trainer
    r = _criar(client, usuarios, trainer="aluno2", student="aluno")
    assert r.status_code == 422 and r.json()["error"] == "trainer_not_personal_trainer"
    # aluno igual ao treinador
    r = _criar(client, usuarios, trainer="pt", student="pt")
    assert r.status_code == 422 and r.json()["error"] == "link_self_forbidden"
    # usuario inexistente
    r = client.post("/access/links", json={"trainer_user_id": usuarios["pt"], "student_user_id": 2_000_000_000})
    assert r.status_code == 404 and r.json()["error"] == "user_not_found"
    # aluno inativo
    with raw.cursor() as cur:
        cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (usuarios["aluno2"],))
    raw.commit()
    r = _criar(client, usuarios, trainer="pt", student="aluno2")
    assert r.status_code == 409 and r.json()["error"] == "user_inactive"
    # ator envolvido: o treinador nao cria nem encerra vinculo proprio
    state["identity"] = Identity(user_id=usuarios["pt"], role="admin", source="master_token")
    r = _criar(client, usuarios)
    assert r.status_code == 409 and r.json()["error"] == "link_actor_involved"
    assert _linhas(raw, [usuarios["pt"]]) == []


def test_encerrar_vinculo_e_recriar_o_mesmo_par(client, raw, usuarios):
    primeiro = _criar(client, usuarios).json()["id"]
    r = client.delete(f"/access/links/{primeiro}")
    assert r.status_code == 200 and r.json()["status"] == "encerrado"
    [linha] = _linhas(raw, [usuarios["pt"]])
    assert linha["status"] == "encerrado" and linha["ended_at"] is not None
    assert [a["action"] for a in _auditoria(raw, primeiro)] == ["link.create", "link.end"]
    # encerrar de novo e inexistente
    r = client.delete(f"/access/links/{primeiro}")
    assert r.status_code == 409 and r.json()["error"] == "link_already_ended"
    assert client.delete("/access/links/2000000000").status_code == 404
    # o par volta a ser vinculavel (UNIQUE so cobre o ativo) e o historico fica
    segundo = _criar(client, usuarios)
    assert segundo.status_code == 201 and segundo.json()["id"] != primeiro
    assert [x["status"] for x in _linhas(raw, [usuarios["pt"]])] == ["encerrado", "ativo"]


def test_listar_com_filtros(client, usuarios):
    a = _criar(client, usuarios, student="aluno").json()["id"]
    b = _criar(client, usuarios, student="aluno2").json()["id"]
    client.delete(f"/access/links/{b}")
    todos = client.get(f"/access/links?trainer_user_id={usuarios['pt']}").json()
    assert [x["id"] for x in todos] == [b, a]
    assert todos[0]["student_username"] == "itg_vinc_aluno2" and todos[0]["trainer_username"] == "itg_vinc_pt"
    ativos = client.get(f"/access/links?trainer_user_id={usuarios['pt']}&status=ativo").json()
    assert [x["id"] for x in ativos] == [a]
    por_aluno = client.get(f"/access/links?student_user_id={usuarios['aluno2']}").json()
    assert [x["id"] for x in por_aluno] == [b]
    assert client.get("/access/links?status=pendente").status_code == 422


def test_has_active_link_verdadeiro_e_falso(client, raw, usuarios):
    pt, aluno, aluno2 = usuarios["pt"], usuarios["aluno"], usuarios["aluno2"]
    assert has_active_link(pt, aluno) is False  # sem vinculo
    link = _criar(client, usuarios).json()["id"]
    assert has_active_link(pt, aluno) is True
    assert has_active_link(pt, aluno2) is False  # outro aluno
    assert has_active_link(aluno, pt) is False  # direcao inversa

    # aluno inativo: o vinculo deixa de valer
    with raw.cursor() as cur:
        cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (aluno,))
    raw.commit()
    assert has_active_link(pt, aluno) is False
    with raw.cursor() as cur:
        cur.execute("UPDATE users SET is_active = 1 WHERE id = %s", (aluno,))
    raw.commit()
    assert has_active_link(pt, aluno) is True

    # treinador fora do grupo personal_trainer: o vinculo deixa de valer (sem ser encerrado)
    with raw.cursor() as cur:
        cur.execute(
            "DELETE FROM user_access_groups WHERE user_id = %s AND group_id = "
            "(SELECT id FROM access_groups WHERE group_key = 'personal_trainer')",
            (pt,),
        )
    raw.commit()
    assert has_active_link(pt, aluno) is False
    assert _linhas(raw, [pt])[0]["status"] == "ativo"

    # encerrado: falso mesmo se o grupo voltar
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO user_access_groups (user_id, group_id) "
            "SELECT %s, id FROM access_groups WHERE group_key = 'personal_trainer'",
            (pt,),
        )
    raw.commit()
    assert has_active_link(pt, aluno) is True
    assert client.delete(f"/access/links/{link}").status_code == 200
    assert has_active_link(pt, aluno) is False


def test_my_students_devolve_so_os_alunos_ativos_do_proprio_treinador(client, raw, usuarios, state):
    _criar(client, usuarios, student="aluno")
    outro = _criar(client, usuarios, student="aluno2").json()["id"]
    state["identity"] = Identity(user_id=usuarios["pt"], role="personal_trainer", source="session")
    r = client.get("/access/my-students")
    assert r.status_code == 200, r.text
    assert [(x["id"], x["username"], x["full_name"]) for x in r.json()] == [
        (usuarios["aluno"], "itg_vinc_aluno", "ITG_VINC_ALUNO"),
        (usuarios["aluno2"], "itg_vinc_aluno2", "ITG_VINC_ALUNO2"),
    ]
    assert set(r.json()[0]) == {"id", "username", "full_name"}

    # vinculo encerrado sai da lista
    state["identity"] = Identity(user_id=usuarios["admin"], role="admin", source="session")
    assert client.delete(f"/access/links/{outro}").status_code == 200
    state["identity"] = Identity(user_id=usuarios["pt"], role="personal_trainer", source="session")
    assert [x["id"] for x in client.get("/access/my-students").json()] == [usuarios["aluno"]]

    # quem nao e treinador (ou nao tem alunos) recebe lista vazia, nao 403
    state["identity"] = Identity(user_id=usuarios["aluno"], role="regular", source="session")
    r = client.get("/access/my-students")
    assert r.status_code == 200 and r.json() == []

    # dispositivo e recusado
    state["identity"] = Identity(user_id=usuarios["pt"], role="personal_trainer", source="device", device_id=3)
    assert client.get("/access/my-students").status_code == 403
