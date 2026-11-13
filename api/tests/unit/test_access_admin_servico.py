"""Servico de grupos personalizados e auditoria (RBAC, ADR 0011, lote 3A).

Cursor fake: registra o SQL e devolve linhas (dicts que projetam as colunas do
SELECT) conforme regras por trecho de SQL. Sem banco.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app import access
from app.access import admin as svc
from app.access.catalog import ACTIONS, RESOURCES, SCOPES

ACTOR = 1
OTHER = 7


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._rules: list[tuple[str, Any]] = []
        self._last: list[Any] = []
        self.lastrowid: Any = None
        self.next_lastrowid: Any = None

    def on(self, fragment: str, rows: Any) -> "FakeCursor":
        """`rows`: lista de linhas, ou excecao a levantar no execute."""
        self._rules.append((fragment, rows))
        return self

    def execute(self, sql: str, params: tuple = ()) -> None:
        norm = " ".join(sql.split())
        self.calls.append((norm, tuple(params or ())))
        self._last = []
        for fragment, rows in self._rules:
            if fragment in norm:
                if isinstance(rows, BaseException):
                    raise rows
                self._last = list(rows)
                break
        if norm.startswith("INSERT INTO access_groups"):
            self.lastrowid = self.next_lastrowid

    def fetchone(self) -> Any:
        return self._last[0] if self._last else None

    def fetchall(self) -> list[Any]:
        return list(self._last)

    # --- ajudas de asserção
    def sqls(self) -> list[str]:
        return [s for s, _ in self.calls]

    def writes(self) -> list[tuple[str, tuple]]:
        return [(s, p) for s, p in self.calls if s.split()[0] in ("INSERT", "UPDATE", "DELETE")]

    def audits(self) -> list[dict[str, Any]]:
        out = []
        for s, p in self.calls:
            if s.startswith("INSERT INTO access_audit_log"):
                actor, action, ttype, tid, detalhe = p
                out.append(
                    {"actor": actor, "action": action, "target_type": ttype, "target_id": tid,
                     "detalhe": None if detalhe is None else json.loads(detalhe), "raw": detalhe}
                )
        return out


def _group_row(gid: int = 10, key: str = "mentoria", system: int = 0, name: str = "Mentoria",
               description: Any = "Alunos da mentoria") -> dict[str, Any]:
    return {"id": gid, "group_key": key, "name": name, "description": description, "is_system": system}


GROUP_SELECT = "FROM access_groups WHERE id = %s"


def _cur_with_group(**kw: Any) -> FakeCursor:
    return FakeCursor().on(GROUP_SELECT, [_group_row(**kw)])


def _assert_locked(cur: FakeCursor) -> None:
    locks = [s for s in cur.sqls() if GROUP_SELECT in s]
    assert locks and locks[0].endswith("FOR UPDATE")


# --- catálogo -------------------------------------------------------------


def test_catalog_view_traz_recursos_acoes_e_escopos():
    view = svc.catalog_view()
    assert view["actions"] == list(ACTIONS)
    assert view["scopes"] == list(SCOPES)
    assert {r["key"] for r in view["resources"]} == {r.key for r in RESOURCES}
    assert set(view["resources"][0]) == {"key", "label", "domain", "grantable", "warning"}


def test_catalog_view_grantable_coerente_com_validate_grants():
    by_key = {r["key"]: r for r in svc.catalog_view()["resources"]}
    for key, res in by_key.items():
        if key.startswith("admin."):
            assert res["grantable"] is False
            with pytest.raises(svc.ResourceNotGrantableError):
                svc.validate_grants([{"resource": key, "action": "ver", "scope": "proprios"}])
        else:
            assert res["grantable"] is True
            svc.validate_grants([{"resource": key, "action": "ver", "scope": "proprios"}])


def test_catalog_view_avisos():
    view = svc.catalog_view()
    by_key = {r["key"]: r for r in view["resources"]}
    assert by_key["ia.cards_exercicio"]["warning"]
    assert "custo" in by_key["ia.cards_exercicio"]["warning"]
    assert [k for k, r in by_key.items() if r["warning"]] == ["ia.cards_exercicio"]
    assert view["scope_warnings"]["todos"]
    assert set(view["scope_warnings"]) == {"todos"}


# --- leitura ---------------------------------------------------------------


def test_list_groups_agrupa_concessoes_e_marca_editavel():
    cur = (
        FakeCursor()
        .on("AS member_count FROM access_groups g", [
            {**_group_row(1, "admin", 1, "Administrador", None), "member_count": 2},
            {**_group_row(10), "member_count": 0},
        ])
        .on("SELECT group_id, resource, action, scope FROM access_group_grants", [
            {"group_id": 10, "resource": "cardio", "action": "ver", "scope": "proprios"},
            {"group_id": 99, "resource": "cardio", "action": "ver", "scope": "todos"},
        ])
    )
    groups = svc.list_groups(cur)
    assert [g["id"] for g in groups] == [1, 10]
    assert groups[0]["is_system"] is True and groups[0]["editable"] is False
    assert groups[0]["member_count"] == 2 and groups[0]["grants"] == []
    assert groups[1]["editable"] is True
    assert groups[1]["grants"] == [{"resource": "cardio", "action": "ver", "scope": "proprios"}]
    assert cur.writes() == []


def test_list_groups_vazio_nao_consulta_concessoes():
    cur = FakeCursor()
    assert svc.list_groups(cur) == []
    assert len(cur.calls) == 1


def test_get_group_sem_lock_e_inexistente():
    cur = (
        _cur_with_group()
        .on("COUNT(*) AS n FROM user_access_groups", [{"n": 3}])
        .on("FROM access_group_grants WHERE group_id", [{"resource": "cardio", "action": "ver", "scope": "proprios"}])
    )
    group = svc.get_group(cur, 10)
    assert group["member_count"] == 3 and group["grants"][0]["resource"] == "cardio"
    assert not any("FOR UPDATE" in s for s in cur.sqls())
    with pytest.raises(svc.GroupNotFoundError) as exc:
        svc.get_group(FakeCursor(), 5)
    assert exc.value.code == "group_not_found"


@pytest.mark.parametrize("bad", [None, 0, -1, True, "3", 1.0])
def test_ids_invalidos(bad):
    with pytest.raises(svc.InvalidIdError) as exc:
        svc.get_group(FakeCursor(), bad)
    assert exc.value.code == "invalid_id"


# --- create ------------------------------------------------------------------


def test_create_group_insere_e_audita():
    cur = FakeCursor()
    cur.next_lastrowid = 42
    out = svc.create_group(cur, ACTOR, "mentoria_2", "  Mentoria  ", "  Turma ")
    assert out["id"] == 42 and out["name"] == "Mentoria" and out["description"] == "Turma"
    assert out["is_system"] is False and out["editable"] is True and out["grants"] == []
    inserts = [w for w in cur.writes() if w[0].startswith("INSERT INTO access_groups")]
    assert inserts == [(
        "INSERT INTO access_groups (group_key, name, description, is_system) VALUES (%s, %s, %s, 0)",
        ("mentoria_2", "Mentoria", "Turma"),
    )]
    [audit] = cur.audits()
    assert audit["action"] == "group.create" and audit["target_type"] == "group" and audit["target_id"] == 42
    assert audit["actor"] == ACTOR
    assert audit["detalhe"] == {"antes": None, "depois": {"group_key": "mentoria_2", "name": "Mentoria",
                                                          "description": "Turma"}}


def test_create_group_sem_lastrowid_relê_o_id():
    cur = FakeCursor()
    calls = {"n": 0}
    original = cur.fetchone

    def fetchone():
        if cur.calls[-1][0].startswith("SELECT id FROM access_groups WHERE group_key"):
            calls["n"] += 1
            return None if calls["n"] == 1 else {"id": 77}
        return original()

    cur.fetchone = fetchone  # type: ignore[method-assign]
    out = svc.create_group(cur, ACTOR, "abc", "Abc")
    assert out["id"] == 77 and cur.audits()[0]["target_id"] == 77


def test_create_group_descricao_unicode_sem_escape():
    cur = FakeCursor()
    cur.next_lastrowid = 5
    svc.create_group(cur, ACTOR, "nutricao_x", "Nutrição", "Descrição")
    assert "Nutrição" in cur.audits()[0]["raw"]


def test_create_group_chave_repetida_antes_do_insert():
    cur = FakeCursor().on("SELECT id FROM access_groups WHERE group_key", [{"id": 3}])
    with pytest.raises(svc.GroupKeyTakenError) as exc:
        svc.create_group(cur, ACTOR, "mentoria", "Mentoria")
    assert exc.value.code == "group_key_taken"
    assert cur.writes() == []


def test_create_group_chave_repetida_na_corrida_vira_group_key_taken():
    cur = FakeCursor().on("INSERT INTO access_groups", RuntimeError("Duplicate entry 'x' for key 'uq'"))
    with pytest.raises(svc.GroupKeyTakenError):
        svc.create_group(cur, ACTOR, "mentoria", "Mentoria")
    assert cur.audits() == []


def test_create_group_outro_erro_do_insert_propaga():
    cur = FakeCursor().on("INSERT INTO access_groups", RuntimeError("Lock wait timeout"))
    with pytest.raises(RuntimeError):
        svc.create_group(cur, ACTOR, "mentoria", "Mentoria")


@pytest.mark.parametrize("key", ["ab", "1abc", "Abc", "a-bc", "a" * 65, "", None, "abc "])
def test_create_group_chave_invalida(key):
    cur = FakeCursor()
    with pytest.raises(svc.InvalidGroupDataError) as exc:
        svc.create_group(cur, ACTOR, key, "Nome")
    assert exc.value.code == "invalid_group_data" and cur.calls == []


def test_create_group_chave_limites_validos():
    for key in ("abc", "a" * 64, "a1_b"):
        cur = FakeCursor()
        cur.next_lastrowid = 1
        svc.create_group(cur, ACTOR, key, "N")


@pytest.mark.parametrize("name", ["", "   ", None, "x" * 121, 5])
def test_create_group_nome_invalido(name):
    with pytest.raises(svc.InvalidGroupDataError):
        svc.create_group(FakeCursor(), ACTOR, "abc", name)


def test_create_group_descricao_limite():
    with pytest.raises(svc.InvalidGroupDataError):
        svc.create_group(FakeCursor(), ACTOR, "abc", "N", "d" * 501)
    with pytest.raises(svc.InvalidGroupDataError):
        svc.create_group(FakeCursor(), ACTOR, "abc", "N", 3)
    cur = FakeCursor()
    cur.next_lastrowid = 1
    assert svc.create_group(cur, ACTOR, "abc", "x" * 120, "d" * 500)["description"] == "d" * 500
    cur = FakeCursor()
    cur.next_lastrowid = 1
    assert svc.create_group(cur, ACTOR, "abc", "N", "   ")["description"] is None


# --- update ------------------------------------------------------------------


def test_update_group_trava_atualiza_e_audita_antes_depois():
    cur = _cur_with_group()
    out = svc.update_group(cur, ACTOR, 10, "Mentoria VIP", None)
    _assert_locked(cur)
    assert ("UPDATE access_groups SET name = %s, description = %s WHERE id = %s",
            ("Mentoria VIP", None, 10)) in cur.writes()
    assert out["name"] == "Mentoria VIP"
    [audit] = cur.audits()
    assert audit["action"] == "group.update" and audit["target_id"] == 10
    assert audit["detalhe"]["antes"] == {"group_key": "mentoria", "name": "Mentoria",
                                         "description": "Alunos da mentoria"}
    assert audit["detalhe"]["depois"] == {"group_key": "mentoria", "name": "Mentoria VIP", "description": None}


def test_update_group_sistema_imutavel():
    cur = _cur_with_group(gid=1, key="usuario", system=1)
    with pytest.raises(svc.GroupSystemImmutableError) as exc:
        svc.update_group(cur, ACTOR, 1, "X")
    assert exc.value.code == "group_system_immutable" and cur.writes() == []


def test_update_group_inexistente():
    with pytest.raises(svc.GroupNotFoundError):
        svc.update_group(FakeCursor(), ACTOR, 10, "X")


def test_free_e_editavel():
    cur = _cur_with_group(gid=4, key="free", system=0, name="Free")
    svc.update_group(cur, ACTOR, 4, "Gratuito")
    assert cur.audits()[0]["action"] == "group.update"


# --- grants ------------------------------------------------------------------


def test_replace_grants_delete_insert_e_audita():
    cur = _cur_with_group().on(
        "FROM access_group_grants WHERE group_id",
        [{"resource": "cardio", "action": "ver", "scope": "proprios"}],
    )
    novos = [
        {"resource": "relatorios", "action": "ver", "scope": "todos"},
        ("cardio", "editar", "alunos"),
    ]
    out = svc.replace_grants(cur, ACTOR, 10, novos)
    _assert_locked(cur)
    writes = cur.writes()
    assert writes[0] == ("DELETE FROM access_group_grants WHERE group_id = %s", (10,))
    assert writes[1] == (
        "INSERT INTO access_group_grants (group_id, resource, action, scope) VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)",
        (10, "cardio", "editar", "alunos", 10, "relatorios", "ver", "todos"),
    )
    depois = [{"resource": "cardio", "action": "editar", "scope": "alunos"},
              {"resource": "relatorios", "action": "ver", "scope": "todos"}]
    assert out["grants"] == depois
    [audit] = cur.audits()
    assert audit["action"] == "group.grants.replace" and audit["target_id"] == 10
    assert audit["detalhe"] == {"antes": [{"resource": "cardio", "action": "ver", "scope": "proprios"}],
                                "depois": depois}


def test_replace_grants_vazio_so_apaga():
    cur = _cur_with_group()
    svc.replace_grants(cur, ACTOR, 10, [])
    writes = [w for w in cur.writes() if not w[0].startswith("INSERT INTO access_audit_log")]
    assert writes == [("DELETE FROM access_group_grants WHERE group_id = %s", (10,))]
    assert cur.audits()[0]["detalhe"] == {"antes": [], "depois": []}


@pytest.mark.parametrize("grants", [
    [{"resource": "nao.existe", "action": "ver", "scope": "todos"}],
    [{"resource": "cardio", "action": "apagar", "scope": "todos"}],
    [{"resource": "cardio", "action": "ver", "scope": "geral"}],
    [{"resource": "cardio", "action": "ver"}],
    [("cardio", "ver")],
    ["cardio"],
    [{"resource": "cardio", "action": "ver", "scope": "todos"},
     {"resource": "cardio", "action": "ver", "scope": "proprios"}],
    "cardio",
    None,
])
def test_replace_grants_invalido(grants):
    cur = _cur_with_group()
    with pytest.raises(svc.InvalidGrantError) as exc:
        svc.replace_grants(cur, ACTOR, 10, grants)
    assert exc.value.code == "invalid_grant" and cur.writes() == []


@pytest.mark.parametrize("resource", [r.key for r in RESOURCES if r.domain == "Administração"])
def test_replace_grants_dominio_admin_nao_concedivel(resource):
    cur = _cur_with_group()
    with pytest.raises(svc.ResourceNotGrantableError) as exc:
        svc.replace_grants(cur, ACTOR, 10, [{"resource": resource, "action": "ver", "scope": "todos"}])
    assert exc.value.code == "resource_not_grantable" and cur.writes() == []


def test_admin_grupos_e_do_dominio_admin():
    assert any(r.key == "admin.grupos" and r.domain == "Administração" for r in RESOURCES)


def test_replace_grants_aceita_todo_recurso_nao_admin():
    grants = [{"resource": r.key, "action": "ver", "scope": "proprios"} for r in RESOURCES
              if r.domain != "Administração"]
    cur = _cur_with_group()
    out = svc.replace_grants(cur, ACTOR, 10, grants)
    assert len(out["grants"]) == len(grants)


def test_replace_grants_sistema_imutavel():
    cur = _cur_with_group(gid=2, key="personal_trainer", system=1)
    with pytest.raises(svc.GroupSystemImmutableError):
        svc.replace_grants(cur, ACTOR, 2, [])
    assert cur.writes() == []


# --- delete ------------------------------------------------------------------


def test_delete_group_sem_membros_audita_snapshot():
    cur = (
        _cur_with_group()
        .on("COUNT(*) AS n FROM user_access_groups", [{"n": 0}])
        .on("FROM access_group_grants WHERE group_id", [{"resource": "cardio", "action": "ver", "scope": "todos"}])
    )
    svc.delete_group(cur, ACTOR, 10)
    _assert_locked(cur)
    assert ("DELETE FROM access_groups WHERE id = %s", (10,)) in cur.writes()
    [audit] = cur.audits()
    assert audit["action"] == "group.delete" and audit["target_id"] == 10
    assert audit["detalhe"] == {
        "antes": {"group_key": "mentoria", "name": "Mentoria", "description": "Alunos da mentoria",
                  "grants": [{"resource": "cardio", "action": "ver", "scope": "todos"}]},
        "depois": None,
    }


def test_delete_group_com_membros():
    cur = _cur_with_group().on("COUNT(*) AS n FROM user_access_groups", [{"n": 2}])
    with pytest.raises(svc.GroupHasMembersError) as exc:
        svc.delete_group(cur, ACTOR, 10)
    assert exc.value.code == "group_has_members" and cur.writes() == []


def test_delete_group_sistema_e_inexistente():
    with pytest.raises(svc.GroupSystemImmutableError):
        svc.delete_group(_cur_with_group(gid=1, key="admin", system=1), ACTOR, 1)
    with pytest.raises(svc.GroupNotFoundError):
        svc.delete_group(FakeCursor(), ACTOR, 1)


# --- membros -----------------------------------------------------------------


def test_list_members_projeta_colunas():
    cur = _cur_with_group().on("FROM user_access_groups uag JOIN users u", [{
        "user_id": OTHER, "username": "ana", "full_name": "Ana", "role": "regular", "is_active": 1,
        "created_at": "2026-11-12 10:00:00", "created_by_user_id": ACTOR,
    }])
    [m] = svc.list_members(cur, 10)
    assert m == {"user_id": OTHER, "username": "ana", "full_name": "Ana", "role": "regular", "is_active": True,
                 "created_at": "2026-11-12 10:00:00", "created_by_user_id": ACTOR}
    with pytest.raises(svc.GroupNotFoundError):
        svc.list_members(FakeCursor(), 10)


def _member_cur(*, member: bool, user_exists: bool = True, **kw: Any) -> FakeCursor:
    cur = _cur_with_group(**kw)
    if user_exists:
        cur.on("SELECT id FROM users WHERE id = %s", [{"id": OTHER}])
    if member:
        cur.on("SELECT 1 AS member FROM user_access_groups", [{"member": 1}])
    return cur


def test_add_member_insere_e_audita():
    cur = _member_cur(member=False)
    out = svc.add_member(cur, ACTOR, 10, OTHER)
    _assert_locked(cur)
    assert out == {"group_id": 10, "user_id": OTHER, "added": True, "already_member": False}
    assert ("INSERT INTO user_access_groups (user_id, group_id, created_by_user_id) VALUES (%s, %s, %s)",
            (OTHER, 10, ACTOR)) in cur.writes()
    [audit] = cur.audits()
    assert audit["action"] == "member.add" and audit["target_type"] == "group" and audit["target_id"] == 10
    assert audit["detalhe"] == {"user_id": OTHER, "antes": {"membro": False}, "depois": {"membro": True}}


def test_add_member_ja_membro_idempotente_sem_auditoria():
    cur = _member_cur(member=True)
    out = svc.add_member(cur, ACTOR, 10, OTHER)
    assert out["added"] is False and out["already_member"] is True
    assert cur.writes() == [] and cur.audits() == []


@pytest.mark.parametrize("fn", [svc.add_member, svc.remove_member])
def test_membro_grupo_sistema_segue_papel(fn):
    cur = _member_cur(member=True, gid=1, key="usuario", system=1)
    with pytest.raises(svc.GroupMembershipByRoleError) as exc:
        fn(cur, ACTOR, 1, OTHER)
    assert exc.value.code == "group_membership_by_role" and cur.writes() == []


@pytest.mark.parametrize("fn", [svc.add_member, svc.remove_member])
def test_membro_usuario_inexistente(fn):
    cur = _member_cur(member=False, user_exists=False)
    with pytest.raises(svc.UserNotFoundError) as exc:
        fn(cur, ACTOR, 10, OTHER)
    assert exc.value.code == "user_not_found" and cur.writes() == []


@pytest.mark.parametrize("fn", [svc.add_member, svc.remove_member])
def test_membro_proprio_vinculo_proibido(fn):
    cur = _member_cur(member=True)
    with pytest.raises(svc.SelfMembershipForbiddenError) as exc:
        fn(cur, ACTOR, 10, ACTOR)
    assert exc.value.code == "self_membership_forbidden" and cur.writes() == []


@pytest.mark.parametrize("fn", [svc.add_member, svc.remove_member])
def test_membro_grupo_inexistente(fn):
    with pytest.raises(svc.GroupNotFoundError):
        fn(FakeCursor(), ACTOR, 10, OTHER)


def test_remove_member_apaga_e_audita():
    cur = _member_cur(member=True)
    out = svc.remove_member(cur, ACTOR, 10, OTHER)
    _assert_locked(cur)
    assert out == {"group_id": 10, "user_id": OTHER, "removed": True}
    assert ("DELETE FROM user_access_groups WHERE user_id = %s AND group_id = %s", (OTHER, 10)) in cur.writes()
    [audit] = cur.audits()
    assert audit["action"] == "member.remove" and audit["target_id"] == 10
    assert audit["detalhe"] == {"user_id": OTHER, "antes": {"membro": True}, "depois": {"membro": False}}


def test_remove_member_nao_membro():
    cur = _member_cur(member=False)
    with pytest.raises(svc.MembershipNotFoundError) as exc:
        svc.remove_member(cur, ACTOR, 10, OTHER)
    assert exc.value.code == "membership_not_found" and cur.writes() == []


# --- auditoria -----------------------------------------------------------------


def test_list_audit_filtra_ordena_e_desserializa():
    cur = FakeCursor().on("FROM access_audit_log", [
        {"id": 2, "actor_user_id": ACTOR, "action": "member.add", "target_type": "group", "target_id": 10,
         "detalhe": json.dumps({"user_id": OTHER}), "created_at": "t2"},
        {"id": 1, "actor_user_id": None, "action": "group.create", "target_type": "group", "target_id": None,
         "detalhe": None, "created_at": "t1"},
    ])
    rows = svc.list_audit(cur, group_id=10, limit=20, offset=40)
    sql, params = cur.calls[-1]
    assert "WHERE target_type = %s AND target_id = %s" in sql
    assert sql.endswith("ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s")
    assert params == ("group", 10, 20, 40)
    assert rows[0]["detalhe"] == {"user_id": OTHER} and rows[1]["detalhe"] is None
    assert rows[1]["actor_user_id"] is None


def test_list_audit_sem_filtro_e_detalhe_dict():
    cur = FakeCursor().on("FROM access_audit_log", [
        {"id": 1, "actor_user_id": 1, "action": "group.delete", "target_type": "group", "target_id": 3,
         "detalhe": {"antes": {}}, "created_at": "t"},
    ])
    rows = svc.list_audit(cur)
    sql, params = cur.calls[-1]
    assert "WHERE" not in sql and params == (50, 0)
    assert rows[0]["detalhe"] == {"antes": {}}


@pytest.mark.parametrize("limit,offset", [(0, 0), (201, 0), (True, 0), ("10", 0), (10, -1)])
def test_list_audit_paginacao_invalida(limit, offset):
    with pytest.raises(svc.InvalidPaginationError):
        svc.list_audit(FakeCursor(), limit=limit, offset=offset)


def test_list_audit_limites_validos():
    for limit in (1, 200):
        svc.list_audit(FakeCursor(), limit=limit)


def test_audit_rejeita_acao_desconhecida():
    with pytest.raises(ValueError):
        svc._audit(FakeCursor(), ACTOR, "group.hack", "group", 1, {})


def test_hierarquia_e_codigos_exportados():
    codes = {
        svc.GroupNotFoundError: "group_not_found",
        svc.GroupSystemImmutableError: "group_system_immutable",
        svc.GroupMembershipByRoleError: "group_membership_by_role",
        svc.GroupKeyTakenError: "group_key_taken",
        svc.GroupHasMembersError: "group_has_members",
        svc.InvalidGrantError: "invalid_grant",
        svc.ResourceNotGrantableError: "resource_not_grantable",
        svc.UserNotFoundError: "user_not_found",
        svc.SelfMembershipForbiddenError: "self_membership_forbidden",
        svc.MembershipNotFoundError: "membership_not_found",
    }
    for cls, code in codes.items():
        assert issubclass(cls, svc.AccessAdminError) and cls.code == code
        assert getattr(access, cls.__name__) is cls
    assert access.AccessAdminError is svc.AccessAdminError
