"""Comparação plano vigente × rascunho (#344): dono `domain/nutrition/plan_comparison` e a rota
`POST /nutrition/plans/{id}/comparison` (mesma permissão e mesmo dono da preview, nada gravado).

Banco em memória: `tests/support/nutrition_fake.py` (+ a SQL do vigente, só leitura)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import plan_comparison as pc
from app.security import Identity, get_current_identity
from tests.support.nutrition_fake import FakeCursor, FakeNutritionDB

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

HOJE = date(2026, 10, 17)
ALUNO = 2
TERCEIRO = 9


# ---------------------------------------------------------------------------
# Dono (Python puro)
# ---------------------------------------------------------------------------


def _dia(local_date: str, kcal=2000.0, protein_g=160.0, carbs_g=200.0, fat_g=60.0, day_kind="rest", treino=False):
    return {"local_date": local_date, "kcal": kcal, "protein_g": protein_g, "carbs_g": carbs_g, "fat_g": fat_g,
            "day_kind": day_kind, "is_training_day": treino, "carb_cycle_policy": "none"}


def _previa(today="2026-10-17", week=(), target=None, blocking=()):
    return {"today": today, "target": target or _dia(today), "week": list(week), "blocking": list(blocking)}


def test_diferenca_absoluta_e_percentual_com_arredondamento_do_dono():
    v = pc.compare_value(2000.0, 2150.0)
    assert v == {"current": 2000.0, "draft": 2150.0, "diff": 150.0, "diff_percent": 7.5}
    assert pc.compare_value(3.0, 2.0)["diff_percent"] == round(-100 / 3, pc.PERCENT_DECIMALS)
    assert pc.compare_value(10.04, 10.0)["diff"] == 0.0  # -0.04 arredonda para 0 (sem -0.0)


@pytest.mark.parametrize(("atual", "rascunho"), [(0, 50.0), (None, 50.0), (50.0, None)])
def test_percentual_none_com_base_zero_ou_ausente(atual, rascunho):
    v = pc.compare_value(atual, rascunho)
    assert v["diff_percent"] is None
    assert v["diff"] == (50.0 if atual == 0 else None)


def test_differs_por_valor_e_por_contexto():
    igual = pc.compare_day(_dia("2026-10-17"), _dia("2026-10-17"))
    assert igual["differs"] is False
    assert pc.compare_day(_dia("2026-10-17"), _dia("2026-10-17", fat_g=70.0))["differs"] is True
    contexto = pc.compare_day(_dia("2026-10-17"), _dia("2026-10-17", day_kind="training", treino=True))
    assert contexto["differs"] is True
    assert contexto["current_context"]["day_kind"] == "rest" and contexto["draft_context"]["day_kind"] == "training"
    assert pc.compare_day(_dia("2026-10-17", kcal=None), _dia("2026-10-17", kcal=1.0))["differs"] is True


def test_datas_nao_coincidentes_nunca_sao_pareadas():
    atual = _previa(week=[_dia("2026-10-15"), _dia("2026-10-16"), _dia("2026-10-17")])
    rascunho = _previa(week=[_dia("2026-10-17", kcal=2100.0), _dia("2026-10-18")])
    comp = pc.compare_previews(atual, rascunho)
    assert [d["local_date"] for d in comp["days"]] == ["2026-10-17"]
    assert comp["days"][0]["values"]["kcal"]["diff"] == 100.0
    assert comp["only_current"] == ["2026-10-15", "2026-10-16"]
    assert comp["only_draft"] == ["2026-10-18"]


def test_status_do_dono():
    p = _previa()
    assert pc.build_comparison(None, p, has_current=False)["status"] == "no_current"
    assert pc.build_comparison(p, p, has_current=True, same_plan=True)["status"] == "same_plan"
    ok = pc.build_comparison(p, p, has_current=True)
    assert ok["status"] == "ok" and ok["reasons"] == [] and ok["comparison"]["target"]["differs"] is False
    falha = pc.build_comparison(p, None, has_current=True, draft_error="nutrition_subject_incomplete")
    assert falha == {"status": "partial", "reasons": ["draft_failed:nutrition_subject_incomplete"], "comparison": None}
    bloqueio = pc.build_comparison(_previa(blocking=[{"code": "x"}]), p, has_current=True)
    assert bloqueio["status"] == "partial" and bloqueio["reasons"] == [pc.REASON_CURRENT_BLOCKING]
    assert bloqueio["comparison"] is not None
    outra_data = pc.build_comparison(_previa(today="2026-10-16"), p, has_current=True)
    assert outra_data["reasons"] == [pc.REASON_REFERENCE_MISMATCH] and outra_data["comparison"] is None


# ---------------------------------------------------------------------------
# Rota
# ---------------------------------------------------------------------------


@pytest.fixture
def db(monkeypatch):
    banco = FakeNutritionDB()
    banco.add_weight(ALUNO, HOJE - timedelta(days=3), 80)
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    chamadas = []
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: chamadas.append(user_id) or HOJE)
    banco.today_calls = chamadas
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    original = FakeCursor.execute

    def execute(self, sql, params=None):
        if sql == plans_mod._CURRENT_ID_SQL:
            p = tuple(params)
            return self._result([{"id": r["id"]} for r in self.db.plans.values()
                                 if r["user_id"] == p[0] and r["status"] == p[1] and r["superseded_at"] is None])
        return original(self, sql, params)

    monkeypatch.setattr(FakeCursor, "execute", execute)
    return banco


def _client(role="admin", user_id=1) -> TestClient:
    app = FastAPI()
    app.include_router(plans_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _comparar(cliente, plano_id):
    return cliente.post(f"/nutrition/plans/{plano_id}/comparison")


def test_compara_com_o_vigente_no_mesmo_hoje_e_mesma_semana_sem_gravar(db, monkeypatch):
    vigente = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10))
    rascunho = db.add_plan(ALUNO, protein_g_per_kg=2.4)
    dias_pedidos = []
    original = plans_mod._week_and_past
    monkeypatch.setattr(plans_mod, "_week_and_past",
                        lambda cur, aluno, settings, today: dias_pedidos.append(today) or original(cur, aluno, settings, today))
    db.writes.clear()
    resposta = _comparar(_client(), rascunho)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert db.writes == [] and db.targets == {}
    assert db.today_calls == [ALUNO] and dias_pedidos == [HOJE, HOJE]  # um "hoje" só para os dois lados
    assert corpo["status"] == "ok" and corpo["reasons"] == []
    assert corpo["current"]["plan"]["id"] == vigente and corpo["draft"]["plan"]["id"] == rascunho
    assert corpo["current"]["today"] == corpo["draft"]["today"] == corpo["today"] == HOJE.isoformat()
    comp = corpo["comparison"]
    assert comp["only_current"] == [] and comp["only_draft"] == [] and len(comp["days"]) == 7
    proteina = comp["target"]["values"]["protein_g"]
    assert proteina["current"] == corpo["current"]["target"]["protein_g"]
    assert proteina["draft"] == corpo["draft"]["target"]["protein_g"]
    assert proteina["diff"] == pytest.approx(proteina["draft"] - proteina["current"], abs=0.05) and proteina["diff"] > 0
    assert comp["target"]["differs"] is True


def test_sem_vigente(db):
    rascunho = db.add_plan(ALUNO)
    corpo = _comparar(_client(), rascunho).json()
    assert corpo["status"] == "no_current" and corpo["current"] is None and corpo["comparison"] is None
    assert corpo["draft"]["plan"]["id"] == rascunho


def test_o_pedido_e_o_proprio_vigente(db):
    vigente = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10))
    corpo = _comparar(_client(), vigente).json()
    assert corpo["status"] == "same_plan" and corpo["comparison"] is None
    assert corpo["current"]["plan"]["id"] == corpo["draft"]["plan"]["id"] == vigente


def test_falha_de_um_lado_vira_partial_com_o_lado_disponivel(db, monkeypatch):
    vigente = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10))
    rascunho = db.add_plan(ALUNO)
    original = plans_mod._plan_preview

    def previa(conn, linha, aluno, usuario, hoje):
        if int(linha["id"]) == rascunho:
            raise plans_mod._error(422, "nutrition_subject_incomplete", "x")
        return original(conn, linha, aluno, usuario, hoje)

    monkeypatch.setattr(plans_mod, "_plan_preview", previa)
    corpo = _comparar(_client(), rascunho).json()
    assert corpo["status"] == "partial" and corpo["reasons"] == ["draft_failed:nutrition_subject_incomplete"]
    assert corpo["draft"] is None and corpo["current"]["plan"]["id"] == vigente and corpo["comparison"] is None


def test_permissao_alheio_e_404_como_na_preview(db):
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10))
    rascunho = db.add_plan(ALUNO)
    resposta = _comparar(_client("regular", user_id=TERCEIRO), rascunho)
    assert resposta.status_code == 404 and resposta.json()["detail"]["error"] == "plan_not_found"
    assert _comparar(_client("regular", user_id=ALUNO), rascunho).status_code == 200
    assert _comparar(_client(), 12345).status_code == 404


def _com_previa_falhando(monkeypatch, falhas):
    """`falhas`: {plano_id: (status, codigo)}; os demais usam a prévia real."""
    original = plans_mod._plan_preview

    def previa(conn, linha, aluno, usuario, hoje):
        if int(linha["id"]) in falhas:
            raise plans_mod._error(falhas[int(linha["id"])][0], falhas[int(linha["id"])][1], "x")
        return original(conn, linha, aluno, usuario, hoje)

    monkeypatch.setattr(plans_mod, "_plan_preview", previa)


def test_falha_dos_dois_lados_vira_partial_sem_comparacao(db, monkeypatch):
    vigente = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10))
    rascunho = db.add_plan(ALUNO)
    _com_previa_falhando(monkeypatch, {vigente: (422, "nutrition_subject_incomplete"), rascunho: (422, "nutrition_subject_incomplete")})
    corpo = _comparar(_client(), rascunho).json()
    assert corpo["status"] == "partial" and corpo["comparison"] is None
    assert corpo["reasons"] == ["current_failed:nutrition_subject_incomplete", "draft_failed:nutrition_subject_incomplete"]
    assert corpo["current"] is None and corpo["draft"] is None


def test_vigente_com_blocking_e_rascunho_ok(db, monkeypatch):
    db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10))
    rascunho = db.add_plan(ALUNO)
    original = plans_mod._plan_preview

    def previa(conn, linha, aluno, usuario, hoje):
        corpo = original(conn, linha, aluno, usuario, hoje)
        if int(linha["id"]) != rascunho:
            corpo = {**corpo, "blocking": [{"code": "x"}]}
        return corpo

    monkeypatch.setattr(plans_mod, "_plan_preview", previa)
    corpo = _comparar(_client(), rascunho).json()
    assert corpo["status"] == "partial" and corpo["reasons"] == [pc.REASON_CURRENT_BLOCKING]
    assert corpo["comparison"] is not None and corpo["draft"]["plan"]["id"] == rascunho


@pytest.mark.parametrize("lado", ["draft", "current"])
def test_5xx_de_um_lado_propaga_como_5xx(db, monkeypatch, lado):
    vigente = db.add_plan(ALUNO, status="published", effective_from=HOJE - timedelta(days=10))
    rascunho = db.add_plan(ALUNO)
    _com_previa_falhando(monkeypatch, {(rascunho if lado == "draft" else vigente): (503, "nutrition_not_ready")})
    resposta = _comparar(_client(), rascunho)
    assert resposta.status_code == 503 and resposta.json()["detail"]["error"] == "nutrition_not_ready"
