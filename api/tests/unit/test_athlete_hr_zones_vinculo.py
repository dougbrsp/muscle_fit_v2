"""De quem sao as zonas do Modelo, por grupo e vinculo ativo, sem identity.role (RBAC lote 5A).

`athlete_hr_zones.resolve_template_zones_subject`: o atleta do Modelo so e o
assunto com `has_active_link`; senao as proprias zonas (inclui admin e usuario).
Treinador = membro do grupo personal_trainer (monkeypatch). Sem banco: cursor roteirizado e `has_active_link` substituido.
Rodar: cd api && python -m pytest tests/unit/test_athlete_hr_zones_vinculo.py
"""

from __future__ import annotations

import pytest

from app import athlete_hr_zones
from app.access import service as access_service
from app.security import Identity

_NAMES = {3: "Ana", 4: "Bia"}
_ASSIGNEES = {10: [3], 11: [], 12: [3, 4]}


class _Cursor:
    def __init__(self):
        self.executed: list[str] = []
        self._all: list = []

    def execute(self, sql, params=None):
        self.executed.append(" ".join(sql.split()))
        self._all = [
            {"user_id": uid, "full_name": _NAMES[uid], "username": _NAMES[uid].lower()}
            for uid in _ASSIGNEES.get(params[0], [])
        ]

    def fetchall(self):
        return self._all


@pytest.fixture
def vinculos(monkeypatch):
    """Treinadores (grupo personal_trainer) e vinculos ativos; registra consultas."""
    ativos: set[tuple[int, int]] = set()
    consultas: list[tuple[int, int]] = []
    treinadores: set[int] = {2}

    def fake(trainer, student):
        consultas.append((trainer, student))
        return (trainer, student) in ativos

    def membro(user_id, group_key):
        assert group_key == "personal_trainer"
        return user_id in treinadores

    monkeypatch.setattr(access_service, "has_active_link", fake)
    monkeypatch.setattr(access_service, "is_group_member", membro)
    return ativos, consultas


def _ident(user_id, role="regular"):
    return Identity(user_id=user_id, role=role, source="session")


def _resolve(cur, user_id, template_id):
    return athlete_hr_zones.resolve_template_zones_subject(cur, _ident(user_id), template_id)


def test_treinador_com_vinculo_ativo_ve_o_atleta_do_template(vinculos):
    vinculos[0].add((2, 3))
    subject = _resolve(_Cursor(), 2, 10)
    assert subject.kind == athlete_hr_zones.SUBJECT_ASSIGNEE
    assert subject.user_id == 3 and subject.name == "Ana"


def test_treinador_sem_vinculo_recebe_none_no_link(vinculos):
    subject = _resolve(_Cursor(), 2, 10)
    assert subject.kind == athlete_hr_zones.SUBJECT_NONE
    assert subject.reason == athlete_hr_zones.REASON_NO_LINK and subject.user_id is None


def test_treinador_atribuido_a_si_mesmo_e_no_link(vinculos, monkeypatch):
    monkeypatch.setitem(_NAMES, 2, "Pt")
    monkeypatch.setitem(_ASSIGNEES, 13, [2])
    subject = _resolve(_Cursor(), 2, 13)
    assert subject.kind == athlete_hr_zones.SUBJECT_NONE
    assert subject.reason == athlete_hr_zones.REASON_NO_LINK
    assert vinculos[1] == []


def test_admin_nao_treinador_mantem_as_proprias(vinculos):
    subject = _resolve(_Cursor(), 1, 10)
    assert subject.kind == athlete_hr_zones.SUBJECT_SELF and subject.user_id == 1
    assert vinculos[1] == []


def test_usuario_comum_ve_as_proprias_mesmo_no_template_de_outro(vinculos):
    subject = _resolve(_Cursor(), 4, 10)
    assert subject.kind == athlete_hr_zones.SUBJECT_SELF and subject.user_id == 4
    assert vinculos[1] == []


def test_nao_treinador_sem_template_e_self_sem_consultar_atribuicao(vinculos):
    cur = _Cursor()
    subject = _resolve(cur, 4, None)
    assert subject.kind == athlete_hr_zones.SUBJECT_SELF and subject.user_id == 4
    assert cur.executed == []


def test_treinador_sem_template_e_none_no_template(vinculos):
    cur = _Cursor()
    subject = _resolve(cur, 2, None)
    assert subject.kind == athlete_hr_zones.SUBJECT_NONE
    assert subject.reason == athlete_hr_zones.REASON_NO_TEMPLATE
    assert cur.executed == [] and vinculos[1] == []


def test_treinador_template_sem_atribuicao_e_no_assignee(vinculos):
    subject = _resolve(_Cursor(), 2, 11)
    assert subject.kind == athlete_hr_zones.SUBJECT_NONE
    assert subject.reason == athlete_hr_zones.REASON_NO_ASSIGNEE


@pytest.mark.parametrize("com_vinculo", [True, False])
def test_treinador_com_dois_atletas_e_multiple_assignees(vinculos, com_vinculo):
    if com_vinculo:
        vinculos[0].add((2, 4))
    subject = _resolve(_Cursor(), 2, 12)
    assert subject.kind == athlete_hr_zones.SUBJECT_NONE
    assert subject.reason == athlete_hr_zones.REASON_MULTIPLE_ASSIGNEES
    assert subject.user_id is None
