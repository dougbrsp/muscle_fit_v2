"""Arquivar plano de nutrição (migração 105): só oculta, reversível, só plano SUBSTITUÍDO.

Rascunho (tem DELETE) e vigente nunca arquivam (409 `plan_not_archivable`); a lista padrão esconde os
arquivados e `include_archived=true` traz todos; as metas por dia (`load_published_plans`) continuam lendo
o plano arquivado; dono/admin/PT operam, usuário comum em plano alheio recebe 404.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition.plan import is_archivable_plan
from tests.unit.test_nutrition_plans_routes import ALUNO, _client, db  # noqa: F401 (fixture reaproveitada)

ANTES = datetime(2026, 9, 1, 12, 0, 0)


@pytest.fixture
def cenario(db):  # noqa: F811
    rascunho = db.add_plan(ALUNO)
    vigente = db.add_plan(ALUNO, status="published", published_at=ANTES)
    substituido = db.add_plan(ALUNO, status="published", published_at=ANTES, superseded_at=db.now)
    return db, rascunho, vigente, substituido


def test_regra_do_dono_so_substituido_e_arquivavel():
    assert is_archivable_plan("draft", None) is False
    assert is_archivable_plan("published", None) is False
    assert is_archivable_plan("published", ANTES) is True


def test_substituido_arquiva_e_some_da_lista_padrao(cenario):
    banco, _, _, substituido = cenario
    admin = _client("admin")
    resposta = admin.post(f"/nutrition/plans/{substituido}/archive")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["archived"] is True and corpo["archived_at"] and corpo["can_archive"] is False
    ids = [p["id"] for p in admin.get(f"/nutrition/plans?user_id={ALUNO}").json()["items"]]
    assert substituido not in ids and len(ids) == 2


def test_include_archived_traz_todos_com_flags(cenario):
    banco, rascunho, vigente, substituido = cenario
    admin = _client("admin")
    admin.post(f"/nutrition/plans/{substituido}/archive")
    itens = {p["id"]: p for p in admin.get(f"/nutrition/plans?user_id={ALUNO}&include_archived=true").json()["items"]}
    assert set(itens) == {rascunho, vigente, substituido}
    assert itens[substituido]["archived"] is True and itens[substituido]["can_archive"] is False
    assert itens[vigente]["archived"] is False and itens[vigente]["can_archive"] is False
    assert itens[rascunho]["can_archive"] is False


def test_can_archive_exposto_no_substituido_nao_arquivado(cenario):
    _, _, _, substituido = cenario
    itens = {p["id"]: p for p in _client("admin").get(f"/nutrition/plans?user_id={ALUNO}").json()["items"]}
    assert itens[substituido]["can_archive"] is True


@pytest.mark.parametrize("qual", ["rascunho", "vigente"])
def test_rascunho_e_vigente_nao_arquivam(cenario, qual):
    banco, rascunho, vigente, _ = cenario
    alvo = {"rascunho": rascunho, "vigente": vigente}[qual]
    resposta = _client("admin").post(f"/nutrition/plans/{alvo}/archive")
    assert resposta.status_code == 409
    assert resposta.json()["detail"]["error"] == "plan_not_archivable"
    assert banco.plans[alvo]["archived_at"] is None


def test_arquivar_e_idempotente_e_desarquivar_reverte(cenario):
    banco, _, _, substituido = cenario
    admin = _client("admin")
    admin.post(f"/nutrition/plans/{substituido}/archive")
    primeira = banco.plans[substituido]["archived_at"]
    assert admin.post(f"/nutrition/plans/{substituido}/archive").status_code == 200
    assert banco.plans[substituido]["archived_at"] == primeira
    voltou = admin.post(f"/nutrition/plans/{substituido}/unarchive")
    assert voltou.status_code == 200 and voltou.json()["archived"] is False and voltou.json()["can_archive"] is True
    assert admin.post(f"/nutrition/plans/{substituido}/unarchive").status_code == 200
    ids = [p["id"] for p in admin.get(f"/nutrition/plans?user_id={ALUNO}").json()["items"]]
    assert substituido in ids


def test_plano_inexistente_404(cenario):
    assert _client("admin").post("/nutrition/plans/9999/archive").status_code == 404
    assert _client("admin").post("/nutrition/plans/9999/unarchive").status_code == 404


def test_dono_arquiva_o_proprio_e_usuario_comum_nao_alcanca_o_alheio(cenario):
    banco, _, _, substituido = cenario
    outro = _client("user", user_id=3)
    for rota in ("archive", "unarchive"):
        resposta = outro.post(f"/nutrition/plans/{substituido}/{rota}")
        assert resposta.status_code == 404 and resposta.json()["detail"]["error"] == "plan_not_found"
    assert banco.plans[substituido]["archived_at"] is None
    dono = _client("user", user_id=ALUNO)
    assert dono.post(f"/nutrition/plans/{substituido}/archive").status_code == 200


def test_pt_e_admin_operam_o_plano_do_aluno(cenario):
    _, _, _, substituido = cenario
    assert _client("personal_trainer", user_id=5).post(f"/nutrition/plans/{substituido}/archive").status_code == 200


def test_arquivado_continua_nas_metas_por_dia(cenario):
    """As metas leem `_PUBLISHED_PLANS_SQL`, que não filtra `archived_at`: o histórico de metas não muda."""
    assert "archived_at" not in svc._PUBLISHED_PLANS_SQL
    assert "archived_at IS NULL" not in plans_mod._BY_USER_SQL


def _corrida(monkeypatch, banco, plano_id, *, outro_arquivou: bool):
    """O UPDATE de arquivar afeta 0 linhas porque outra chamada chegou antes (arquivou ou o plano mudou)."""
    from tests.support.nutrition_fake import FakeCursor

    original = FakeCursor.execute

    def execute(self, sql, params=None):
        if sql == plans_mod._ARCHIVE_SQL:
            if outro_arquivou:
                banco.plans[plano_id]["archived_at"] = banco.now
            self.rowcount = 0
            return None
        return original(self, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", execute)


def test_corrida_ja_arquivado_por_outra_chamada_e_200_idempotente(cenario, monkeypatch):
    banco, _, _, substituido = cenario
    _corrida(monkeypatch, banco, substituido, outro_arquivou=True)
    resposta = _client("admin").post(f"/nutrition/plans/{substituido}/archive")
    assert resposta.status_code == 200 and resposta.json()["archived"] is True


def test_corrida_sem_arquivar_continua_409(cenario, monkeypatch):
    banco, _, _, substituido = cenario
    _corrida(monkeypatch, banco, substituido, outro_arquivou=False)
    resposta = _client("admin").post(f"/nutrition/plans/{substituido}/archive")
    assert resposta.status_code == 409 and resposta.json()["detail"]["error"] == "plan_not_archivable"
