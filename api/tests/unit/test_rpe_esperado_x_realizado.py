"""#182 parte B: RPE realizado x esperado da serie (dono: effective_intensity)."""

from __future__ import annotations

from decimal import Decimal

from app import analytics, services
from app.domain import effective_intensity as ei
from tests.support.fake_db import fake_transaction
from tests.unit.test_last_session_sets import _Cursor as _LastCursor


def test_abaixo_no_esperado_acima():
    assert ei.rpe_vs_expected(7, 8).status == ei.RPE_VS_BELOW
    assert ei.rpe_vs_expected(8, 8).status == ei.RPE_VS_ON_TARGET
    acima = ei.rpe_vs_expected(9, 8)
    assert acima.status == ei.RPE_VS_ABOVE and acima.delta == 1.0
    assert acima.label == "Acima do esperado"


def test_meio_ponto_e_decimal_contra_float():
    r = ei.rpe_vs_expected(8.5, 9)
    assert r.status == ei.RPE_VS_BELOW and r.delta == -0.5
    assert ei.rpe_vs_expected(Decimal("8.5"), 8.5).status == ei.RPE_VS_ON_TARGET


def test_nulos_devolvem_none():
    assert ei.rpe_vs_expected(None, 8) is None
    assert ei.rpe_vs_expected(8, None) is None


def test_contagem_ignora_series_sem_esperado_ou_realizado():
    rows = [
        {"rpe": 7, "rpe_target_planned": 8},
        {"rpe": 8, "rpe_target_planned": 8},
        {"rpe": 9, "rpe_target_planned": Decimal("8.0")},
        {"rpe": 9, "rpe_target_planned": None},
        {"rpe": None, "rpe_target_planned": 8},
    ]
    assert ei.rpe_vs_expected_counts(rows) == {"below": 1, "on_target": 1, "above": 1, "compared": 3}


def test_serie_de_trabalho_e_legada_comparam_e_nao_trabalho_nao():
    for tipo in ("backoff", "technique", "warmup"):
        assert ei.set_rpe_vs_expected({"set_type": tipo, "rpe": 9, "rpe_target_planned": 8}) is None
    assert ei.set_rpe_vs_expected({"set_type": "working", "rpe": 9, "rpe_target_planned": 8}).status == ei.RPE_VS_ABOVE
    assert ei.set_rpe_vs_expected({"rpe": 7, "rpe_target_planned": 8}).status == ei.RPE_VS_BELOW


def test_contagem_do_dono_ignora_nao_trabalho():
    rows = [
        {"set_type": "working", "rpe": 9, "rpe_target_planned": 8},
        {"set_type": "backoff", "rpe": 9, "rpe_target_planned": 8},
        {"set_type": "technique", "rpe": 7, "rpe_target_planned": 8},
    ]
    assert ei.rpe_vs_expected_counts(rows) == {"below": 0, "on_target": 0, "above": 1, "compared": 1}


def test_detalhe_nao_compara_backoff_mas_mantem_esperado_cru(monkeypatch):
    class _Cur(_LastCursor):
        def execute(self, sql, params=None):
            flat = " ".join(sql.split())
            super().execute(sql, params)
            if flat.startswith("SELECT a.*"):
                self._one = {"id": 9, "user_id": 1, "calories_kcal": None, "calories_source": None}
            elif flat.startswith("SELECT e.*,"):
                self._all = [{"id": 1}]
            elif flat.startswith("SELECT * FROM exercise_sets"):
                self._all = [
                    {"id": 1, "exercise_id": 1, "set_number": 1, "set_type": "backoff", "reps": 10,
                     "rpe": Decimal("9"), "rpe_target_planned": Decimal("8")},
                ]

    monkeypatch.setattr(services, "transaction", fake_transaction(_Cur([])))
    (serie,) = services.activity_detail(9, user_id=1)["exercises"][0]["sets"]
    assert serie["rpe_vs_expected"] is None and serie["rpe_target_planned"] == 8.0


def test_detalhe_do_treino_devolve_esperado_e_comparacao(monkeypatch):
    class _Cur(_LastCursor):
        def execute(self, sql, params=None):
            flat = " ".join(sql.split())
            super().execute(sql, params)
            if flat.startswith("SELECT a.*"):
                self._one = {"id": 9, "user_id": 1, "calories_kcal": None, "calories_source": None}
            elif flat.startswith("SELECT e.*,"):
                self._all = [{"id": 1}]
            elif flat.startswith("SELECT * FROM exercise_sets"):
                self._all = [
                    {"id": 1, "exercise_id": 1, "set_number": 1, "set_type": "working", "reps": 10,
                     "rpe": Decimal("8.5"), "rpe_target_planned": Decimal("9.0")},
                    {"id": 2, "exercise_id": 1, "set_number": 2, "set_type": "working", "reps": 10,
                     "rpe": Decimal("8.5"), "rpe_target_planned": None},
                ]

    monkeypatch.setattr(services, "transaction", fake_transaction(_Cur([])))
    nova, antiga = services.activity_detail(9, user_id=1)["exercises"][0]["sets"]
    assert nova["rpe_target_planned"] == 9.0 and isinstance(nova["rpe_target_planned"], float)
    assert nova["rpe_vs_expected"] == {"status": "below", "label": "Abaixo do esperado", "delta": -0.5}
    assert antiga["rpe_target_planned"] is None and antiga["rpe_vs_expected"] is None


def _linha(activity_id, rpe, planned):
    return {
        "activity_id": activity_id, "local_date": None, "activity_name": "T", "mapping_id": 1,
        "set_number": 1, "set_type": "working", "reps": 10, "weight_kg": 50, "rpe": rpe,
        "measure": "reps", "rpe_target_planned": planned,
    }


def test_historico_tem_contagens_na_sessao_nova_e_null_na_antiga():
    nova, antiga = [
        s["rpe_vs_expected_counts"]
        for s in analytics._build_progression_sessions([_linha(1, 8, 8), _linha(1, 9, 8), _linha(2, 8, None)])
    ]
    assert nova == {"below": 0, "on_target": 1, "above": 1, "compared": 2}
    assert antiga is None
