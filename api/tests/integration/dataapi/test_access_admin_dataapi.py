"""Gestao de grupos e acessos (RBAC lote 3A, ADR 0011) contra MySQL 8 real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de producao).

Exercita o que os testes unitarios (cursor falso) nao provam:

- `lastrowid` do INSERT em `access_groups` (e o fallback por SELECT) no shim;
- INSERT de varias linhas em `access_group_grants` (placeholders repetidos);
- coluna JSON `detalhe` de `access_audit_log` gravada como texto e lida de volta;
- `SELECT ... FOR UPDATE` dentro de `transaction()`;
- CASCADE das concessoes ao excluir o grupo;
- `resolve_permissions` unindo o grupo personalizado ao grupo `usuario` do papel.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import access_admin
from app.access.service import resolve_permissions
from app.security import Identity, get_current_identity

PREFIXO = "itg_acc_"
USUARIOS = ("admin_acessos", "aluno_acessos")
GROUP_KEY = PREFIXO + "grupo_a"

GRANTS_1 = [
    {"resource": "modelos.treino", "action": "ver", "scope": "todos"},
    {"resource": "modelos.metas_ativacao", "action": "ver", "scope": "proprios"},
    {"resource": "relatorios", "action": "editar", "scope": "proprios"},
]
GRANTS_2 = [
    {"resource": "modelos.treino", "action": "editar", "scope": "alunos"},
    {"resource": "modelos.tipos_treino", "action": "ver", "scope": "proprios"},
]


def _limpar(raw, ids) -> None:
    with raw.cursor() as cur:
        if ids:
            marcas = ", ".join(["%s"] * len(ids))
            cur.execute(f"DELETE FROM access_audit_log WHERE actor_user_id IN ({marcas})", tuple(ids))
            cur.execute(f"DELETE FROM user_access_groups WHERE user_id IN ({marcas})", tuple(ids))
        cur.execute(
            "DELETE FROM access_audit_log WHERE target_type = 'group' AND target_id IN "
            "(SELECT id FROM access_groups WHERE group_key LIKE %s)",
            (PREFIXO + "%",),
        )
        cur.execute(
            "DELETE FROM user_access_groups WHERE group_id IN "
            "(SELECT id FROM access_groups WHERE group_key LIKE %s)",
            (PREFIXO + "%",),
        )
        cur.execute("DELETE FROM access_groups WHERE is_system = 0 AND group_key LIKE %s", (PREFIXO + "%",))


@pytest.fixture
def usuarios(raw, conceder_grupo):
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        cur.execute(f"SELECT id FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
        antigos = [r["id"] for r in cur.fetchall()]
    _limpar(raw, antigos)
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
        for nome, papel in zip(USUARIOS, ("admin", "regular")):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel))
            ids[nome.split("_")[0]] = cur.lastrowid
            conceder_grupo(cur, ids[nome.split("_")[0]], papel)
    raw.commit()
    yield ids
    _limpar(raw, list(ids.values()))
    with raw.cursor() as cur:
        cur.execute(f"DELETE FROM users WHERE username IN ({', '.join(['%s'] * len(USUARIOS))})", USUARIOS)
    raw.commit()


@pytest.fixture
def client(db_backend, usuarios, fake_rds) -> TestClient:
    app = FastAPI()
    app.include_router(access_admin.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=usuarios["admin"], role="admin", source="session"
    )
    yield TestClient(app, raise_server_exceptions=False)
    if db_backend == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _auditoria(raw, group_id: int, action: str | None = None) -> list[dict]:
    sql = "SELECT action, actor_user_id, target_type, target_id, detalhe FROM access_audit_log WHERE target_id = %s"
    params: list = [group_id]
    if action:
        sql += " AND action = %s"
        params.append(action)
    with raw.cursor() as cur:
        cur.execute(sql + " ORDER BY id", tuple(params))
        return list(cur.fetchall())


def _grants_no_banco(raw, group_id: int) -> list[tuple]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT resource, action, scope FROM access_group_grants WHERE group_id = %s ORDER BY resource, action",
            (group_id,),
        )
        return [(r["resource"], r["action"], r["scope"]) for r in cur.fetchall()]


def _tuplas(grants: list[dict]) -> list[tuple]:
    return sorted((g["resource"], g["action"], g["scope"]) for g in grants)


def _criar_grupo(client, raw) -> int:
    r = client.post("/access/groups", json={"group_key": GROUP_KEY, "name": "Grupo A", "description": "Teste"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["group_key"] == GROUP_KEY and body["is_system"] is False and body["member_count"] == 0
    assert isinstance(body["id"], int) and body["id"] > 0
    return body["id"]


def test_fluxo_completo_de_grupo_personalizado(client, raw, usuarios):
    aluno = usuarios["aluno"]

    # 2. criacao: lastrowid + linha real + auditoria group.create
    gid = _criar_grupo(client, raw)
    with raw.cursor() as cur:
        cur.execute("SELECT group_key, name, description, is_system FROM access_groups WHERE id = %s", (gid,))
        linha = cur.fetchone()
    assert linha == {"group_key": GROUP_KEY, "name": "Grupo A", "description": "Teste", "is_system": 0}
    audit = _auditoria(raw, gid, "group.create")
    assert len(audit) == 1 and audit[0]["actor_user_id"] == usuarios["admin"]
    detalhe = json.loads(audit[0]["detalhe"])
    assert detalhe["antes"] is None and detalhe["depois"]["group_key"] == GROUP_KEY

    # chave repetida -> 409
    dup = client.post("/access/groups", json={"group_key": GROUP_KEY, "name": "Outro"})
    assert dup.status_code == 409 and dup.json()["error"] == "group_key_taken"

    # 3. concessoes: INSERT multilinha e substituicao completa
    r = client.put(f"/access/groups/{gid}/grants", json={"grants": GRANTS_1})
    assert r.status_code == 200, r.text
    assert _grants_no_banco(raw, gid) == _tuplas(GRANTS_1)
    aud = _auditoria(raw, gid, "group.grants.replace")
    assert len(aud) == 1
    d1 = json.loads(aud[0]["detalhe"])
    assert d1["antes"] == [] and _tuplas(d1["depois"]) == _tuplas(GRANTS_1)

    r = client.put(f"/access/groups/{gid}/grants", json={"grants": GRANTS_2})
    assert r.status_code == 200, r.text
    assert _grants_no_banco(raw, gid) == _tuplas(GRANTS_2)  # as antigas sumiram
    aud = _auditoria(raw, gid, "group.grants.replace")
    assert len(aud) == 2
    d2 = json.loads(aud[1]["detalhe"])
    assert _tuplas(d2["antes"]) == _tuplas(GRANTS_1) and _tuplas(d2["depois"]) == _tuplas(GRANTS_2)

    # recurso de Administracao nao e concedivel
    bad = client.put(
        f"/access/groups/{gid}/grants",
        json={"grants": [{"resource": "admin.grupos", "action": "ver", "scope": "todos"}]},
    )
    assert bad.status_code == 422 and bad.json()["error"] == "resource_not_grantable"
    assert _grants_no_banco(raw, gid) == _tuplas(GRANTS_2)  # rollback: nada mudou

    # 10. listagem
    lista = client.get("/access/groups")
    assert lista.status_code == 200
    meu = next(g for g in lista.json() if g["id"] == gid)
    assert meu["member_count"] == 0 and meu["editable"] is True
    assert _tuplas(meu["grants"]) == _tuplas(GRANTS_2)
    usuario_sistema = next(g for g in lista.json() if g["group_key"] == "usuario")
    assert usuario_sistema["is_system"] is True and usuario_sistema["member_count"] >= 1

    # 4. membro: 201, depois 200 idempotente sem nova auditoria
    antes = resolve_permissions(Identity(user_id=aluno, role="regular", source="session"))
    # o grupo `usuario` so tem `modelos.treino`/ver (proprios); editar e tipos_treino faltam
    assert "editar" not in antes.grants.get("modelos.treino", {})
    assert "modelos.tipos_treino" not in antes.grants
    r = client.post(f"/access/groups/{gid}/members", json={"user_id": aluno})
    assert r.status_code == 201 and r.json()["added"] is True
    assert len(_auditoria(raw, gid, "member.add")) == 1
    r = client.post(f"/access/groups/{gid}/members", json={"user_id": aluno})
    assert r.status_code == 200 and r.json()["already_member"] is True
    assert len(_auditoria(raw, gid, "member.add")) == 1

    efetivas = resolve_permissions(Identity(user_id=aluno, role="regular", source="session"))
    assert efetivas.grants["modelos.treino"]["editar"] == "alunos"
    assert efetivas.grants["modelos.treino"]["ver"] == "proprios"  # uniao: `ver` vem do `usuario`
    assert efetivas.grants["modelos.tipos_treino"]["ver"] == "proprios"
    assert "usuario" in efetivas.group_keys and GROUP_KEY in efetivas.group_keys
    assert efetivas.grants["dashboards"]["ver"] == "proprios"  # uniao com o grupo `usuario`

    membros = client.get(f"/access/groups/{gid}/members")
    assert membros.status_code == 200 and [m["user_id"] for m in membros.json()] == [aluno]
    assert client.get("/access/groups").json() and next(
        g for g in client.get("/access/groups").json() if g["id"] == gid
    )["member_count"] == 1

    # 5. excluir com membro -> 409
    r = client.delete(f"/access/groups/{gid}")
    assert r.status_code == 409 and r.json()["error"] == "group_has_members"
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM access_groups WHERE id = %s", (gid,))
        assert cur.fetchone()["n"] == 1

    # 6. remover membro: 200 e concessoes somem
    r = client.delete(f"/access/groups/{gid}/members/{aluno}")
    assert r.status_code == 200 and r.json()["removed"] is True
    assert len(_auditoria(raw, gid, "member.remove")) == 1
    depois = resolve_permissions(Identity(user_id=aluno, role="regular", source="session"))
    assert "editar" not in depois.grants.get("modelos.treino", {})
    assert "modelos.tipos_treino" not in depois.grants and GROUP_KEY not in depois.group_keys
    assert depois.grants["modelos.treino"]["ver"] == "proprios"  # o do `usuario` permanece
    r = client.delete(f"/access/groups/{gid}/members/{aluno}")
    assert r.status_code == 404 and r.json()["error"] == "membership_not_found"

    # 7. excluir vazio: 204 e CASCADE
    r = client.delete(f"/access/groups/{gid}")
    assert r.status_code == 204 and r.content == b""
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM access_groups WHERE id = %s", (gid,))
        assert cur.fetchone()["n"] == 0
    assert _grants_no_banco(raw, gid) == []
    assert len(_auditoria(raw, gid, "group.delete")) == 1

    # 9. trilha: mais recente primeiro, detalhe como objeto
    r = client.get("/access/audit", params={"group_id": gid})
    assert r.status_code == 200
    entradas = r.json()
    assert [e["action"] for e in entradas] == [
        "group.delete",
        "member.remove",
        "member.add",
        "group.grants.replace",
        "group.grants.replace",
        "group.create",
    ]
    assert all(isinstance(e["detalhe"], dict) for e in entradas)
    assert entradas[0]["detalhe"]["antes"]["group_key"] == GROUP_KEY and entradas[0]["detalhe"]["depois"] is None
    assert entradas[0]["actor_user_id"] == usuarios["admin"]
    assert isinstance(entradas[0]["created_at"], str)
    pagina = client.get("/access/audit", params={"group_id": gid, "limit": 2, "offset": 1})
    assert [e["action"] for e in pagina.json()] == ["member.remove", "member.add"]


def test_grupo_de_sistema_usuario_e_imutavel(client, raw, usuarios):
    with raw.cursor() as cur:
        cur.execute("SELECT id FROM access_groups WHERE group_key = 'usuario' AND is_system = 1")
        sistema = cur.fetchone()["id"]
        cur.execute("SELECT COUNT(*) AS n FROM access_group_grants WHERE group_id = %s", (sistema,))
        antes = cur.fetchone()["n"]

    r = client.put(f"/access/groups/{sistema}/grants", json={"grants": GRANTS_1})
    assert r.status_code == 409 and r.json()["error"] == "group_system_immutable"
    r = client.post(f"/access/groups/{sistema}/members", json={"user_id": usuarios["aluno"]})
    assert r.status_code == 409 and r.json()["error"] == "group_membership_by_role"
    r = client.delete(f"/access/groups/{sistema}")
    assert r.status_code == 409 and r.json()["error"] == "group_system_immutable"

    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM access_group_grants WHERE group_id = %s", (sistema,))
        assert cur.fetchone()["n"] == antes
    assert _auditoria(raw, sistema) == []


def test_recusas_de_membro_e_dispositivo(client, raw, usuarios):
    gid = _criar_grupo(client, raw)
    # o admin nao altera o proprio vinculo
    r = client.post(f"/access/groups/{gid}/members", json={"user_id": usuarios["admin"]})
    assert r.status_code == 409 and r.json()["error"] == "self_membership_forbidden"
    # usuario inexistente
    r = client.post(f"/access/groups/{gid}/members", json={"user_id": 999999999})
    assert r.status_code == 404 and r.json()["error"] == "user_not_found"
    # grupo inexistente
    assert client.get("/access/groups/999999999").status_code == 404
    assert _auditoria(raw, gid, "member.add") == []


def test_atualizar_grupo_grava_antes_e_depois(client, raw, usuarios):
    gid = _criar_grupo(client, raw)
    r = client.put(f"/access/groups/{gid}", json={"name": "Grupo A2", "description": None})
    assert r.status_code == 200 and r.json()["name"] == "Grupo A2"
    with raw.cursor() as cur:
        cur.execute("SELECT name, description FROM access_groups WHERE id = %s", (gid,))
        assert cur.fetchone() == {"name": "Grupo A2", "description": None}
    d = json.loads(_auditoria(raw, gid, "group.update")[0]["detalhe"])
    assert d["antes"]["name"] == "Grupo A" and d["depois"]["name"] == "Grupo A2"
