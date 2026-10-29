"""M52 - wellness do Intervals.icu mais velho que a idade maxima e ignorado.

Sem banco e sem rede: a linha de `intervals_wellness` chega por um duble de
`get_latest_intervals_wellness`.

Cobre:
- a regra pura (`wellness_freshness`): limite inclusivo de 3 dias, data
  ilegivel = velha, ausencia de linha nao e "velha", "hoje" no fuso do dono;
- recuperacao: dado velho -> fator NEUTRO (os mesmos numeros de "sem
  Intervals"); dado fresco -> os MESMOS numeros do modelo vigente
  (`fullbody-no-reps-v1`) antes do M52;
- payload de transparencia: `stale`, `max_age_days`, data do ultimo wellness;
- recovery-signals: o aviso chega no contrato da tela;
- prompt de geracao: secao omitida com dado velho, mantida com dado fresco.

Rodar: cd api && python -m pytest tests/unit/test_m52_wellness_idade_maxima.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app import workout_plan_ai
from app.intelligence import wellness_freshness
from app.intelligence.base import IntelligenceServiceBase
from app.intelligence.dashboard import recovery_signals_service, time_windows
from app.intelligence.dashboard.contracts import GlobalReadinessAdjustmentOut
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from tests.support.privado import exigir

HOJE = date(2026, 10, 3)


def _row(dias_atras: int, tsb: float | None = -15.0, tz: str | None = None) -> dict:
    return {
        "user_id": 7,
        "wellness_date": (HOJE - timedelta(days=dias_atras)).isoformat(),
        "form_tsb": tsb,
        "fitness_ctl": 50.0,
        "fatigue_atl": 65.0,
        "ramp_rate": 1.2,
        "readiness_score": None,
        "daily_effort_load": 40,
        "user_timezone": tz,
    }


@pytest.fixture
def hoje_fixo(monkeypatch):
    """"Hoje" do usuario congelado em HOJE (meio-dia em Sao Paulo)."""
    agora_utc = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)

    class _Datetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return agora_utc if tz is None else agora_utc.astimezone(tz)

    monkeypatch.setattr(time_windows, "datetime", _Datetime)
    return HOJE


# ---------------------------------------------------------------------------
# Regra pura
# ---------------------------------------------------------------------------


def test_idade_maxima_e_tres_dias_e_mora_num_lugar_so():
    assert wellness_freshness.WELLNESS_MAX_AGE_DAYS == 3


@pytest.mark.parametrize(
    ("dias_atras", "velho"),
    [(0, False), (1, False), (3, False), (4, True), (30, True)],
)
def test_limite_inclusivo(dias_atras, velho):
    data = HOJE - timedelta(days=dias_atras)
    assert wellness_freshness.is_stale(data.isoformat(), HOJE) is velho
    assert wellness_freshness.is_stale(data, HOJE) is velho


def test_data_futura_nao_e_velha_e_ilegivel_e():
    assert wellness_freshness.wellness_age_days("2026-10-05", HOJE) == 0
    assert wellness_freshness.is_stale("2026-10-05", HOJE) is False
    assert wellness_freshness.is_stale("lixo", HOJE) is True
    assert wellness_freshness.is_stale(None, HOJE) is True


def test_sem_linha_nao_e_velho():
    assert wellness_freshness.is_row_stale(None) is False
    assert wellness_freshness.is_row_stale({}) is False


def test_hoje_e_o_do_fuso_do_dono(monkeypatch):
    # 03/10 23:30 em Sao Paulo = 04/10 02:30 UTC = 04/10 11:30 em Tokyo.
    agora_utc = datetime(2026, 10, 4, 2, 30, tzinfo=timezone.utc)

    class _Datetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return agora_utc if tz is None else agora_utc.astimezone(tz)

    monkeypatch.setattr(time_windows, "datetime", _Datetime)
    linha = {"wellness_date": "2026-09-30"}
    # Sao Paulo (default, inclusive fuso ausente): 3 dias -> fresco.
    assert wellness_freshness.is_row_stale({**linha, "user_timezone": None}) is False
    assert wellness_freshness.is_row_stale({**linha, "user_timezone": "America/Sao_Paulo"}) is False
    # Tokyo ja esta em 04/10: 4 dias -> velho.
    assert wellness_freshness.is_row_stale({**linha, "user_timezone": "Asia/Tokyo"}) is True


# ---------------------------------------------------------------------------
# Recuperacao
# ---------------------------------------------------------------------------


def _servico(monkeypatch, linha):
    service = MuscleRecoveryService()
    chamadas = []

    def fake_latest(user_id=None):
        chamadas.append(user_id)
        return dict(linha) if linha is not None else None

    monkeypatch.setattr(service, "get_latest_intervals_wellness", fake_latest)
    return service, chamadas


def test_wellness_velho_vira_fator_neutro(monkeypatch, hoje_fixo):
    service, _ = _servico(monkeypatch, _row(4))
    tsb, wellness = service._get_global_tsb(user_id=7)
    assert tsb is None
    assert wellness["wellness_date"] == "2026-09-29"
    # Neutro = exatamente o caminho "sem Intervals": horas inalteradas.
    assert service.apply_global_readiness_factor(40.0, tsb, 18.0) == 40.0


def test_wellness_fresco_aplica_o_mesmo_tsb_de_antes(monkeypatch, hoje_fixo):
    service, _ = _servico(monkeypatch, _row(3))
    tsb, _ = service._get_global_tsb(user_id=7)
    assert tsb == -15.0
    # Formula vigente intocada: 1 - (-15)/150 = 1,10.
    assert service.compute_global_readiness_factor(tsb) == pytest.approx(1.10)
    assert service.apply_global_readiness_factor(40.0, tsb, 18.0) == pytest.approx(44.0)


def test_memo_continua_uma_leitura_por_instancia(monkeypatch, hoje_fixo):
    service, chamadas = _servico(monkeypatch, _row(10))
    service._get_global_tsb(user_id=7)
    service.get_global_readiness_adjustment(user_id=7)
    assert chamadas == [7]


def test_ajuste_fresco_expoe_flag_falsa(monkeypatch, hoje_fixo):
    service, _ = _servico(monkeypatch, _row(1))
    assert service.get_global_readiness_adjustment(user_id=7) == {
        "form_tsb": -15.0,
        "factor": 1.1,
        "wellness_date": "2026-10-02",
        "stale": False,
        "max_age_days": 3,
    }


def test_ajuste_velho_declara_stale_com_fator_neutro(monkeypatch, hoje_fixo):
    service, _ = _servico(monkeypatch, _row(30, tsb=-25.0))
    assert service.get_global_readiness_adjustment(user_id=7) == {
        "form_tsb": -25.0,
        "factor": 1.0,
        "wellness_date": "2026-09-03",
        "stale": True,
        "max_age_days": 3,
    }


def test_ajuste_velho_sem_tsb_ainda_avisa(monkeypatch, hoje_fixo):
    service, _ = _servico(monkeypatch, _row(9, tsb=None))
    ajuste = service.get_global_readiness_adjustment(user_id=7)
    assert ajuste["stale"] is True and ajuste["form_tsb"] is None and ajuste["factor"] == 1.0


def test_sem_wellness_continua_none(monkeypatch, hoje_fixo):
    service, _ = _servico(monkeypatch, None)
    assert service._get_global_tsb(user_id=7) == (None, None)
    assert service.get_global_readiness_adjustment(user_id=7) is None


def test_fresco_sem_tsb_continua_none(monkeypatch, hoje_fixo):
    service, _ = _servico(monkeypatch, _row(0, tsb=None))
    assert service.get_global_readiness_adjustment(user_id=7) is None


def _overview_com(monkeypatch, linha):
    """Overview completo sobre linhas prontas (sem banco): e o caminho que a
    geracao de ficha, o Modo Treino e o hub usam."""
    service, _ = _servico(monkeypatch, linha)
    monkeypatch.setattr(service, "_get_personal_coefficient", lambda *a, **k: 1.0)
    linhas = [
        {
            "muscle_group_id": 1,
            "muscle_group": "Peito",
            "session_dose": 6.0,
            "dose_last_72h": 6.0,
            "session_participation": 1.0,
            "hours_since_trained": 20.0,
            "days_since_trained": 1,
            "performance_ratio": 1.0,
        }
    ]
    return service.get_recovery_overview(user_id=7, overview_rows=[dict(r) for r in linhas])


def test_overview_com_dado_velho_e_identico_ao_sem_intervals(monkeypatch, hoje_fixo):
    assert _overview_com(monkeypatch, _row(4)) == _overview_com(monkeypatch, None)


def test_overview_com_dado_fresco_estica_a_recuperacao(monkeypatch, hoje_fixo):
    fresco = _overview_com(monkeypatch, _row(0))[0]
    sem = _overview_com(monkeypatch, None)[0]
    assert fresco["estimated_recovery_hours"] > sem["estimated_recovery_hours"]


# ---------------------------------------------------------------------------
# Contrato da tela (recovery-signals)
# ---------------------------------------------------------------------------


def test_adjustment_out_velho_traz_flag_e_texto_proprio():
    out = recovery_signals_service._adjustment_out(
        {"form_tsb": -25.0, "factor": 1.0, "wellness_date": "2026-09-03", "stale": True, "max_age_days": 3}
    )
    GlobalReadinessAdjustmentOut.model_validate(out)
    assert out["stale"] is True and out["max_age_days"] == 3
    assert out["wellness_date"] == date(2026, 9, 3)
    assert "mais de 3 dias" in out["description"]


def test_adjustment_out_legado_sem_flag_e_fresco():
    out = recovery_signals_service._adjustment_out({"form_tsb": -12.5, "factor": 1.083, "wellness_date": "2026-09-21"})
    GlobalReadinessAdjustmentOut.model_validate(out)
    assert out["stale"] is False and out["max_age_days"] is None


# ---------------------------------------------------------------------------
# Prompt de geracao de ficha
# ---------------------------------------------------------------------------


def _prompt_latest(monkeypatch, linha):
    monkeypatch.setattr(
        IntelligenceServiceBase, "get_latest_intervals_wellness", lambda self, user_id=None: linha
    )
    return workout_plan_ai.get_latest_intervals_wellness(user_id=7)


def test_prompt_omite_secao_com_dado_velho(monkeypatch, hoje_fixo):
    wellness = _prompt_latest(monkeypatch, _row(4))
    assert wellness["stale"] is True
    assert workout_plan_ai.format_intervals_wellness_section(wellness) == ""


def test_prompt_mantem_secao_com_dado_fresco(monkeypatch, hoje_fixo):
    wellness = _prompt_latest(monkeypatch, _row(2))
    assert wellness["stale"] is False
    secao = workout_plan_ai.format_intervals_wellness_section(wellness)
    # `exigir`: a falha nao poe a secao (prosa do repositorio privado) no log publico.
    exigir(secao.startswith("\n\n") and len(secao) > 2, "secao presente")
    exigir("wellness_date=2026-10-01" in secao, "data do registro")
    exigir("Forma (TSB): -15.0" in secao, "forma (TSB)")


def test_prompt_sem_dado_mantem_texto_de_ausencia(monkeypatch, hoje_fixo):
    assert _prompt_latest(monkeypatch, None) is None
    ausencia = workout_plan_ai._intervals_text(".sem_dados", {})
    exigir(workout_plan_ai.format_intervals_wellness_section(None) == "\n\n" + ausencia, "texto de ausencia")


def test_prompt_falha_de_leitura_nao_quebra_geracao(monkeypatch, hoje_fixo):
    def boom(self, user_id=None):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(IntelligenceServiceBase, "get_latest_intervals_wellness", boom)
    assert workout_plan_ai.get_latest_intervals_wellness(user_id=7) is None
