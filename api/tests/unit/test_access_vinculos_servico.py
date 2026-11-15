"""Servico de vinculos treinador-aluno (RBAC lote 5A, ADR 0011). Cursor fake, sem banco."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.access import admin as svc

ACTOR = 1
TRAINER = 20
STUDENT = 30


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._rules: list[tuple[str, Any]] = []
        self._last: list[Any] = []
        self.lastrowid: Any = None
        self.next_lastrowid: Any = None

    def on(self, fragment: str, rows: Any) -> "FakeCursor":
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
        if norm.startswith("INSERT INTO trainer_students"):
            self.lastrowid = self.next_lastrowid

    def fetchone(self) -> Any:
        return self._last[0] if self._last else None

    def fetchall(self) -> list[Any]:
        return list(self._last)

    def sqls(self) -> list[str]:
        return [s for s, _ in self.calls]

    def writes(self) -> list[tuple[str, tuple]]:
        return [(s, p) for s, p in self.calls if s.split()[0] in ("INSERT", "UPDATE", "DELETE")]

    def audits(self) -> list[dict[str, Any]]:
        out = []
        for s, p in self.calls:
            if s.startswith("INSERT INTO access_audit_log"):
                actor, action, ttype, tid, detalhe = p
                out.append({"actor": actor, "action": action, "target_type": ttype, "target_id": tid,
                            "detalhe": json.loads(detalhe)})
        return out


def _link(lid: int = 5, trainer: int = TRAINER, student: int = STUDENT, status: str = "ativo") -> dict[str, Any]:
    return {
        "id": lid, "trainer_user_id": trainer, "student_user_id": student, "status": status,
        "created_by_user_id": ACTOR, "created_at": None, "ended_at": None,
        "trainer_username": "t", "trainer_full_name": "T", "student_username": "s", "student_full_name": "S",
    }


USER_SQL = "SELECT id, is_active FROM users WHERE id = %s"
PT_SQL = "FROM user_access_groups uag JOIN access_groups g"
DUP_SQL = "SELECT id FROM trainer_students WHERE trainer_user_id"
LINK_BY_ID = "WHERE ts.id = %s"


def _cur_create(**kw: Any) -> FakeCursor:
    cur = FakeCursor().on(USER_SQL, [{"id": 1, "is_active": 1}])
    cur.on(PT_SQL, [{"member": 1}]).on(DUP_SQL, []).on(LINK_BY_ID, [_link()])
    cur.next_lastrowid = 5
    return cur


# --- criar -------------------------------------------------------------------------


def test_create_link_grava_e_audita_na_mesma_transacao():
    cur = _cur_create()
    link = svc.create_link(cur, ACTOR, TRAINER, STUDENT)
    assert link["id"] == 5 and link["status"] == "ativo"
    inserts = [w for w in cur.writes() if w[0].startswith("INSERT INTO trainer_students")]
    assert inserts[0][1] == (TRAINER, STUDENT, "ativo", ACTOR)
    [aud] = cur.audits()
    assert aud["action"] == "link.create" and aud["target_type"] == "trainer_link" and aud["target_id"] == 5
    assert aud["actor"] == ACTOR and aud["detalhe"]["antes"] is None
    assert aud["detalhe"]["depois"] == {"trainer_user_id": TRAINER, "student_user_id": STUDENT, "status": "ativo"}


def test_create_link_sem_lastrowid_busca_o_id():
    cur = _cur_create()
    cur.next_lastrowid = None
    original = cur.execute

    def execute(sql, params=()):
        original(sql, params)
        if sql.startswith("SELECT id FROM trainer_students") and "FOR UPDATE" not in sql:
            cur._last = [{"id": 5}]  # fallback pos-INSERT (o SELECT do duplicado leva FOR UPDATE)

    cur.execute = execute  # type: ignore[method-assign]
    assert svc.create_link(cur, ACTOR, TRAINER, STUDENT)["id"] == 5


@pytest.mark.parametrize("trainer,student", [(ACTOR, STUDENT), (TRAINER, ACTOR)])
def test_create_link_recusa_ator_envolvido_sem_tocar_no_banco(trainer, student):
    cur = _cur_create()
    with pytest.raises(svc.LinkActorInvolvedError) as exc:
        svc.create_link(cur, ACTOR, trainer, student)
    assert exc.value.code == "link_actor_involved"
    assert cur.calls == []


def test_create_link_recusa_aluno_igual_ao_treinador():
    cur = _cur_create()
    with pytest.raises(svc.LinkSelfForbiddenError) as exc:
        svc.create_link(cur, ACTOR, TRAINER, TRAINER)
    assert exc.value.code == "link_self_forbidden" and cur.calls == []


@pytest.mark.parametrize("trainer,student", [(0, 1), (True, 3), ("2", 3), (2, None), (-1, 3)])
def test_create_link_recusa_ids_invalidos(trainer, student):
    with pytest.raises(svc.InvalidIdError):
        svc.create_link(_cur_create(), ACTOR, trainer, student)


def test_create_link_usuario_inexistente():
    cur = FakeCursor().on(USER_SQL, [])
    with pytest.raises(svc.UserNotFoundError) as exc:
        svc.create_link(cur, ACTOR, TRAINER, STUDENT)
    assert exc.value.code == "user_not_found" and cur.writes() == []


def test_create_link_usuario_inativo():
    cur = FakeCursor().on(USER_SQL, [{"id": 1, "is_active": 0}])
    with pytest.raises(svc.UserInactiveError) as exc:
        svc.create_link(cur, ACTOR, TRAINER, STUDENT)
    assert exc.value.code == "user_inactive" and cur.writes() == []


def test_create_link_aluno_inativo_recusa_mesmo_com_treinador_valido():
    cur = FakeCursor()
    respostas = iter([[{"id": TRAINER, "is_active": 1}], [{"id": STUDENT, "is_active": 0}]])
    original = cur.execute

    def execute(sql, params=()):
        original(sql, params)
        if sql.startswith("SELECT id, is_active"):
            cur._last = next(respostas)

    cur.execute = execute  # type: ignore[method-assign]
    with pytest.raises(svc.UserInactiveError):
        svc.create_link(cur, ACTOR, TRAINER, STUDENT)


def test_create_link_treinador_fora_do_grupo_personal_trainer():
    cur = FakeCursor().on(USER_SQL, [{"id": 1, "is_active": 1}]).on(PT_SQL, [])
    with pytest.raises(svc.TrainerNotPersonalTrainerError) as exc:
        svc.create_link(cur, ACTOR, TRAINER, STUDENT)
    assert exc.value.code == "trainer_not_personal_trainer"
    assert cur.writes() == []
    sql, params = next((s, p) for s, p in cur.calls if PT_SQL in s)
    assert params == (TRAINER, "personal_trainer")


def test_create_link_duplicado_ativo():
    cur = _cur_create()
    cur._rules = [(DUP_SQL, [{"id": 9}])] + cur._rules
    with pytest.raises(svc.LinkAlreadyActiveError) as exc:
        svc.create_link(cur, ACTOR, TRAINER, STUDENT)
    assert exc.value.code == "link_already_active" and cur.writes() == []


def test_create_link_corrida_unique_vira_duplicado():
    cur = _cur_create()
    cur._rules = [("INSERT INTO trainer_students", Exception("(1062) Duplicate entry '1-2-1'"))] + cur._rules
    with pytest.raises(svc.LinkAlreadyActiveError):
        svc.create_link(cur, ACTOR, TRAINER, STUDENT)
    assert cur.audits() == []


# --- encerrar ----------------------------------------------------------------------


def test_end_link_encerra_e_audita():
    cur = FakeCursor().on(LINK_BY_ID, [_link()])
    link = svc.end_link(cur, ACTOR, 5)
    assert link["status"] == "encerrado"
    assert any(s.endswith("FOR UPDATE") for s in cur.sqls())
    [(sql, params)] = cur.writes()[:1]
    assert sql.startswith("UPDATE trainer_students SET status = %s, ended_at = CURRENT_TIMESTAMP")
    assert params == ("encerrado", 5)
    [aud] = cur.audits()
    assert aud["action"] == "link.end" and aud["target_type"] == "trainer_link" and aud["target_id"] == 5
    assert aud["detalhe"]["antes"]["status"] == "ativo" and aud["detalhe"]["depois"]["status"] == "encerrado"


def test_end_link_inexistente():
    cur = FakeCursor().on(LINK_BY_ID, [])
    with pytest.raises(svc.LinkNotFoundError) as exc:
        svc.end_link(cur, ACTOR, 5)
    assert exc.value.code == "link_not_found" and cur.writes() == []


def test_end_link_ja_encerrado():
    cur = FakeCursor().on(LINK_BY_ID, [_link(status="encerrado")])
    with pytest.raises(svc.LinkAlreadyEndedError) as exc:
        svc.end_link(cur, ACTOR, 5)
    assert exc.value.code == "link_already_ended" and cur.writes() == []


@pytest.mark.parametrize("trainer,student", [(ACTOR, STUDENT), (TRAINER, ACTOR)])
def test_end_link_recusa_ator_envolvido(trainer, student):
    cur = FakeCursor().on(LINK_BY_ID, [_link(trainer=trainer, student=student)])
    with pytest.raises(svc.LinkActorInvolvedError):
        svc.end_link(cur, ACTOR, 5)
    assert cur.writes() == []


def test_end_link_id_invalido():
    with pytest.raises(svc.InvalidIdError):
        svc.end_link(FakeCursor(), ACTOR, 0)


# --- listar ------------------------------------------------------------------------


def test_list_links_sem_filtro():
    cur = FakeCursor().on("FROM trainer_students ts", [_link(), _link(lid=4, status="encerrado")])
    out = svc.list_links(cur)
    assert [x["id"] for x in out] == [5, 4]
    sql, params = cur.calls[0]
    assert "WHERE" not in sql and sql.endswith("ORDER BY ts.id DESC") and params == ()


def test_list_links_com_todos_os_filtros():
    cur = FakeCursor().on("FROM trainer_students ts", [_link()])
    svc.list_links(cur, trainer_user_id=TRAINER, student_user_id=STUDENT, status="ativo")
    sql, params = cur.calls[0]
    assert "ts.trainer_user_id = %s AND ts.student_user_id = %s AND ts.status = %s" in sql
    assert params == (TRAINER, STUDENT, "ativo")


def test_list_links_status_invalido_e_id_invalido():
    with pytest.raises(svc.InvalidLinkDataError) as exc:
        svc.list_links(FakeCursor(), status="pendente")
    assert exc.value.code == "invalid_link_data"
    with pytest.raises(svc.InvalidIdError):
        svc.list_links(FakeCursor(), trainer_user_id=0)


# --- meus alunos -------------------------------------------------------------------


def test_list_my_students_devolve_so_id_username_e_nome():
    cur = FakeCursor().on(PT_SQL, [{"member": 1}]).on(
        "FROM trainer_students ts", [{"id": 30, "username": "s", "full_name": "S"}]
    )
    assert svc.list_my_students(cur, TRAINER) == [{"id": 30, "username": "s", "full_name": "S"}]
    sql, params = cur.calls[-1]
    assert "s.is_active = 1" in sql and params == (TRAINER, "ativo")


def test_list_my_students_treinador_fora_do_grupo_devolve_vazio():
    cur = FakeCursor().on(PT_SQL, [])
    assert svc.list_my_students(cur, TRAINER) == []
    assert all("trainer_students" not in s for s in cur.sqls())


def test_acoes_de_auditoria_de_vinculo_estao_registradas():
    assert {"link.create", "link.end"} <= set(svc.AUDIT_ACTIONS)
