"""M76 Lote 2a - recuperação com uma regra só (B2, B3, B5, B7, B8, B11).

Mapa: `docs/motores/FONTES_DA_VERDADE.md` §10. Sem banco.

Rodar: cd api && python -m pytest tests/unit/test_m76_lote2a_recuperacao_regra_unica.py
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from app.domain import progression_engine
from app.generation import freeze_mode_b
from app.generation.mode_b import sets_for_recovery
from app.intelligence import muscle_recovery_service as mrs_module
from app.intelligence.dashboard import recovery_signals_service
from app.intelligence.muscle_recovery_service import MuscleRecoveryService as MRS

# ---------------------------------------------------------------------------
# B2 - dose reduzida: ceil(S × 0,75), mínimo 1, em todo lugar (decisão 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sets, esperado", [(1, 1), (2, 2), (3, 3), (4, 3), (5, 4), (6, 5), (8, 6)])
def test_reduced_sets_bordas(sets, esperado):
    assert MRS.reduced_sets(sets) == esperado
    assert MRS.reduced_sets(sets, "REDUCED_DOSE") == esperado


def test_reduced_sets_por_estado_e_entradas_vazias():
    assert MRS.reduced_sets(4, "READY") == 4
    assert MRS.reduced_sets(4, None) == 4  # grupo sem dose registrada = READY
    assert MRS.reduced_sets(4, "NO_DIRECT_WORK") == 0
    for vazio in (0, None, -2, "x"):
        assert MRS.reduced_sets(vazio) == 0


def test_recomendacao_da_dose_reduzida_expressa_a_regra_e_nao_1_2_series():
    rec = MRS.recommendation_for("REDUCED_DOSE")
    assert rec["direct_sets_min"] is None and rec["direct_sets_max"] is None
    assert rec["volume_multiplier"] == mrs_module.REDUCED_DOSE_MULTIPLIER
    assert rec["minimum_sets"] == mrs_module.REDUCED_DOSE_MIN_SETS
    assert "75%" in rec["sets_rule"] and "mínimo 1" in rec["sets_rule"]

    policy = MRS.reduction_policy()
    assert policy == {
        "status": "REDUCED_DOSE",
        "volume_multiplier": 0.75,
        "minimum_sets": 1,
        "rounding": "ceil",
        "sets_rule": rec["sets_rule"],
        "maximum_rpe": 9,
        "allow_failure_on_last_set": False,
        "allow_progression": False,
    }


def test_regra_do_prompt_e_interpolada_da_politica():
    texto = MRS.reduced_dose_prompt_rule()
    policy = MRS.reduction_policy()
    assert policy["sets_rule"] in texto
    assert f"rpe_target máximo {policy['maximum_rpe']}" in texto
    assert "sem falha" in texto and "sem sinalizar progressão" in texto
    assert "1-2" not in texto and "8-9" not in texto


def test_motor_de_progressao_nao_tem_mais_multiplicador_proprio():
    assert not hasattr(progression_engine, "REDUCED_DOSE_MULTIPLIER")
    for sets in range(1, 9):
        assert progression_engine._reduced_sets(sets, "REDUCED_DOSE") == MRS.reduced_sets(sets)


def test_volume_multiplier_for_e_o_da_tabela():
    assert MRS.volume_multiplier_for("READY") == 1.0
    assert MRS.volume_multiplier_for("REDUCED_DOSE") == 0.75
    assert MRS.volume_multiplier_for("NO_DIRECT_WORK") == 0.0


# ---------------------------------------------------------------------------
# B2 - Modo B passa a REDUZIR séries (antes só avisava)
# ---------------------------------------------------------------------------


def _prescricao(suggested_sets):
    return SimpleNamespace(decision=SimpleNamespace(suggested_sets=suggested_sets))


@pytest.mark.parametrize("modelo, esperado", [(2, 2), (3, 3), (4, 3), (5, 4)])
def test_modo_b_reduz_series_do_modelo_em_reduced_dose_sem_prescricao(modelo, esperado):
    assert sets_for_recovery(modelo, None, "REDUCED_DOSE") == esperado


def test_modo_b_usa_as_series_que_o_motor_ja_reduziu():
    # O motor (prescribe) devolve ceil(S × 0,75): é o mesmo número que o
    # congelamento grava no snapshot.
    assert sets_for_recovery(4, _prescricao(3), "REDUCED_DOSE") == 3
    assert sets_for_recovery(6, _prescricao(5), "REDUCED_DOSE") == 5


def test_modo_b_mantem_o_modelo_fora_da_dose_reduzida():
    assert sets_for_recovery(4, _prescricao(4), "READY") == 4
    assert sets_for_recovery(4, None, "READY") == 4
    # NO_DIRECT_WORK: o motor devolve SKIP sem séries; o Modo B não remove o
    # item (aviso), então mantém o Modelo.
    assert sets_for_recovery(4, _prescricao(None), "NO_DIRECT_WORK") == 4
    assert sets_for_recovery(4, None, "NO_DIRECT_WORK") == 4
    assert sets_for_recovery(None, None, "REDUCED_DOSE") is None


@pytest.mark.parametrize("modelo", [1, 2, 3, 4, 5, 6, 8])
def test_congelamento_nao_reduz_duas_vezes(modelo):
    rascunho = sets_for_recovery(modelo, None, "REDUCED_DOSE")
    partida = freeze_mode_b.base_sets_for_freeze(rascunho, modelo)
    assert partida == modelo
    assert MRS.reduced_sets(partida, "REDUCED_DOSE") == rascunho


def test_congelamento_respeita_edicao_do_usuario():
    assert freeze_mode_b.base_sets_for_freeze(2, 5) == 2  # editou para 2 (≠ 5 e ≠ 4)
    assert freeze_mode_b.base_sets_for_freeze(4, 4) == 4
    assert freeze_mode_b.base_sets_for_freeze(None, 4) == 4
    assert freeze_mode_b.base_sets_for_freeze(3, None) == 3


# ---------------------------------------------------------------------------
# B3 - janela única e sinais que persistem (decisão 2)
# ---------------------------------------------------------------------------


def test_janela_unica_e_default_do_overview():
    params = inspect.signature(MRS.get_recovery_overview).parameters
    assert params["days"].default is None and params["limit"].default is None
    # 28 = a maior das janelas antigas: com 14, a tela de 28 dias perdia sinais
    # de sessões de 15-28 dias atrás (contra a decisão 2).
    assert mrs_module.RECOVERY_LOOKBACK_DAYS == 28
    assert MRS.SIGNALS_POLICY == "signals_persist_until_superseded"


def test_overview_sem_days_usa_a_janela_do_modulo(monkeypatch):
    chamadas = []

    class _Volume:
        _overview_cache = None

        def get_muscle_group_overview(self, **kwargs):
            chamadas.append(kwargs)
            return []

    service = MRS()
    service.volume_service = _Volume()
    monkeypatch.setattr(service, "_get_global_tsb", lambda user_id=None: (None, None))
    service.get_recovery_overview(user_id=None)
    assert chamadas[0]["days"] == mrs_module.RECOVERY_LOOKBACK_DAYS
    assert chamadas[0]["limit"] == mrs_module.RECOVERY_GROUP_LIMIT
    monkeypatch.setattr(mrs_module, "RECOVERY_LOOKBACK_DAYS", 40)
    service.get_recovery_overview(user_id=None)
    assert chamadas[1]["days"] == 40


def test_congelamento_usa_a_janela_do_modelo(monkeypatch):
    chamadas = []

    def fake_overview(self, **kwargs):
        chamadas.append(kwargs)
        return []

    monkeypatch.setattr(MRS, "get_recovery_overview", fake_overview)
    monkeypatch.setattr(MRS, "user_now", lambda self, user_id: datetime(2026, 10, 7, 6, 0))
    freeze_mode_b.projected_readiness(1, date(2026, 10, 8), time(6, 0))
    assert chamadas and "days" not in chamadas[0] and "limit" not in chamadas[0]


def test_dashboards_usam_a_janela_do_modelo(monkeypatch):
    chamadas = []

    def fake_overview(self, **kwargs):
        chamadas.append(kwargs)
        return []

    monkeypatch.setattr(MRS, "get_recovery_overview", fake_overview)
    monkeypatch.setattr(MRS, "get_global_readiness_adjustment", lambda self, user_id=None: None)
    recovery_signals_service._readiness_items(1)
    from app.intelligence.dashboard import muscle_summary_service

    muscle_summary_service._readiness_by_group(1)
    assert chamadas == [{"user_id": 1}, {"user_id": 1}]


# ---------------------------------------------------------------------------
# B5 - fator de técnica só do catálogo
# ---------------------------------------------------------------------------


def test_modelo_nao_tem_fator_proprio_de_tecnica():
    assert set(MRS._SET_TYPE_FACTORS) == {"warmup", "working", "backoff", "feeler"}
    for tecnica in ("drop_set", "dropset", "rest_pause", "forced_rep"):
        assert MRS.set_type_factor(tecnica) == 1.0


# ---------------------------------------------------------------------------
# B7 - descanso entre dias consecutivos usa a dose da SESSÃO
# ---------------------------------------------------------------------------


class _VolumeStub:
    def __init__(self, history):
        self._history = history

    def get_muscle_group_overview(self, days, limit, user_id):
        return [{"muscle_group_id": 1, "muscle_group": "Peitoral", "activity_dose_history": self._history}]

    def _user_now(self, user_id):
        return datetime(2026, 10, 7, 12, 0)


def _entrada(activity_id, quando: datetime, dose: float):
    return {
        "activity_id": activity_id,
        "trained_at": quando.isoformat(),
        "local_date": quando.date().isoformat(),
        "dose": dose,
        "participation": 1.0,
    }


def test_dias_consecutivos_usam_a_dose_da_sessao_mais_recente(monkeypatch):
    d1 = datetime(2026, 10, 5, 7, 0)
    history = [
        _entrada(1, d1, 6.0),  # sessão pesada de manhã
        _entrada(2, d1.replace(hour=19), 1.0),  # sessão leve à noite (a mais recente do dia)
        _entrada(3, datetime(2026, 10, 6, 19, 0), 2.0),
    ]
    service = MRS()
    service.volume_service = _VolumeStub(history)
    monkeypatch.setattr(service, "_get_global_tsb", lambda user_id=None: (None, None))
    monkeypatch.setattr(service, "_get_personal_coefficient", lambda group_id, user_id=None: 1.0)

    [par] = service.get_consecutive_day_recovery_checks(days=7, user_id=1)

    assert par["elapsed_hours"] == 24.0  # 19h de D1 até 19h de D2
    # Dose da sessão das 19h (1,0), não a soma do dia (7,0).
    assert par["required_hours"] == pytest.approx(MRS.calculate_recovery_hours(1.0))
    assert par["required_hours"] < MRS.calculate_recovery_hours(7.0)
    assert par["insufficient_recovery"] is False


# ---------------------------------------------------------------------------
# B8 - dor só pelo papel PRIMARY
# ---------------------------------------------------------------------------


def test_dor_diaria_so_conta_papel_primary():
    sql = recovery_signals_service.PAIN_DAILY_SQL
    assert "c.role = 'PRIMARY'" in sql
    assert "exercise_legacy_id_map" in sql
    # A dor passa pelo filtro; o dia de treino (`sessions`) não.
    assert "THEN e.muscle_pain END" in sql
    assert "THEN COALESCE(e.joint_pain, 0) ELSE 0 END" in sql
    assert "COUNT(DISTINCT a.id)" in sql and "AS sessions" in sql


# ---------------------------------------------------------------------------
# B11 - prompt não manda a IA ajustar pelo TSB
# ---------------------------------------------------------------------------


def test_prompt_de_wellness_diz_que_o_tsb_ja_foi_aplicado():
    from app.workout_plan_ai import format_intervals_wellness_section

    from tests.support.privado import exigir

    # A redacao ("JA aplicou", "nao ajuste") e conferida no privado
    # (`api/privado/tests/unit/test_intervals_wellness_prompt.py`).
    texto = format_intervals_wellness_section({"wellness_date": "2026-10-06", "form_tsb": -12, "stale": False})
    exigir("para ajustar volume/intensidade" not in texto, "sem a instrucao antiga de ajustar pelo TSB")


def test_rag_hab_004_nao_exige_72h():
    from pathlib import Path

    rag = (Path(__file__).resolve().parents[3] / "api" / "privado" / "rag" / "markdown" / "HABILIDADES_TREINO.md").read_text(encoding="utf-8")
    assert "exige 72 horas de descanso" not in rag
    assert "TETO do modelo" in rag


def test_teto_de_recuperacao_e_constante_nomeada():
    assert mrs_module.RECOVERY_MAX_HOURS == 72.0
    assert MRS.calculate_recovery_hours(50.0) == mrs_module.RECOVERY_MAX_HOURS
    assert timedelta(hours=mrs_module.RECOVERY_MAX_HOURS).days == 3
