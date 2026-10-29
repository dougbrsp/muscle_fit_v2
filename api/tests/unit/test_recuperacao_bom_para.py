"""Coluna "Bom para" da prontidão por grupo (2026-10-17).

Dono do instante: `intelligence/recovery_projection.ready_forecast` (a mesma
projeção que a ficha usa para a data futura). O fuso é de
`dashboard/time_windows.user_wall_to_instant`. A tela só formata.

O que cada bloco protege:

1. **Treino recente**: o instante é o PRIMEIRO minuto em que a projeção
   classifica o grupo READY - igual a uma varredura minuto a minuto, READY
   nele e não READY um minuto antes.
2. **Já pronto / sem treino**: sem instante, com o rótulo pronto do dono.
3. **Sinal que só uma nova sessão limpa** (dor, queda de desempenho): sem
   previsão, mesmo com o tempo de recuperação cumprido.
4. **Virada de fuso**: a API devolve o instante com o deslocamento do fuso do
   usuário; o dia é o da parede do usuário, não o de UTC.
5. **Contrato**: `RecoveryGroupOut` aceita os dois campos e serializa o fuso.

Rodar: cd api && python -m pytest tests/unit/test_recuperacao_bom_para.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.intelligence.dashboard import recovery_signals_service as service
from app.intelligence.dashboard.contracts import RecoverySignalsResponse, WindowInfo, WindowKind
from app.intelligence.dashboard.time_windows import user_wall_to_instant
from app.intelligence.muscle_recovery_service import READY_MIN_ADJUSTED_READINESS
from app.intelligence.recovery_projection import (
    READY_NOW_LABEL,
    READY_SEARCH_HORIZON_HOURS,
    ReadyForecast,
    project_recovery_state,
    ready_forecast,
)

AGORA = datetime(2026, 10, 17, 22, 0)  # sábado, 22h na parede do usuário


def _item(**over):
    """Grupo treinado há 10h, 44h estimadas, densidade baixa, sem sinal:
    não READY agora só por tempo."""
    base = {
        "muscle_group": "Peitoral",
        "muscle_group_id": 1,
        "session_dose": 5.0,
        "dose_last_72h": 5.0,
        "estimated_recovery_hours": 44.0,
        "hours_since_trained": 10.0,
        "performance_ratio": None,
        "excessive_rep_drop": False,
        "muscle_pain": None,
        "joint_pain": False,
        "readiness_status": "NO_DIRECT_WORK",
    }
    base.update(over)
    return base


def _status(item, alvo: datetime) -> str:
    return project_recovery_state(item, AGORA, alvo)["projected_readiness_status"]


def _varredura(item) -> datetime | None:
    """Referência ingênua: primeiro minuto READY, um a um."""
    for minuto in range(int(READY_SEARCH_HORIZON_HOURS * 60) + 1):
        alvo = AGORA + timedelta(minutes=minuto)
        if _status(item, alvo) == "READY":
            return alvo
    return None


# ---------------------------------------------------------------------------
# 1) Treino recente
# ---------------------------------------------------------------------------


def test_treino_recente_da_o_primeiro_minuto_ready_da_projecao():
    item = _item()
    previsao = ready_forecast(item, AGORA)

    assert previsao.label is None
    assert previsao.ready_at == _varredura(item)
    assert _status(item, previsao.ready_at) == "READY"
    assert _status(item, previsao.ready_at - timedelta(minutes=1)) != "READY"


def test_instante_sai_em_minuto_cheio_arredondado_para_cima():
    """O "agora" tem segundos; a tela mostra hh:mm. O instante sobe para o
    minuto cheio seguinte (continua READY) em vez de cair antes do READY."""
    agora = AGORA.replace(second=37, microsecond=500_000)
    item = _item()
    previsao = ready_forecast(item, agora)

    assert previsao.ready_at.second == 0 and previsao.ready_at.microsecond == 0
    ready = project_recovery_state(item, agora, previsao.ready_at)["projected_readiness_status"]
    antes = project_recovery_state(item, agora, previsao.ready_at - timedelta(minutes=1))["projected_readiness_status"]
    assert ready == "READY"
    assert antes != "READY"


def test_pronto_chega_antes_dos_100_por_cento_de_tempo():
    """READY vale a partir da prontidão ajustada mínima, não de 100% de tempo:
    "agora + remaining_hours" daria um instante mais tarde do que a coluna
    "Estado" vai mostrar."""
    item = _item(dose_last_72h=0.0)
    previsao = ready_forecast(item, AGORA)

    horas_ate_100 = 44.0 - 10.0
    assert previsao.ready_at < AGORA + timedelta(hours=horas_ate_100)
    decorridas = 10.0 + (previsao.ready_at - AGORA).total_seconds() / 3600
    assert decorridas / 44.0 * 100 == pytest.approx(READY_MIN_ADJUSTED_READINESS, abs=0.1)


def test_densidade_alta_adia_o_pronto():
    leve = ready_forecast(_item(dose_last_72h=0.0), AGORA)
    densa = ready_forecast(_item(dose_last_72h=12.0), AGORA)

    assert densa.ready_at > leve.ready_at
    assert densa.ready_at == _varredura(_item(dose_last_72h=12.0))


def test_dose_reduzida_por_tempo_tambem_tem_previsao():
    item = _item(hours_since_trained=28.0, readiness_status="REDUCED_DOSE")
    previsao = ready_forecast(item, AGORA)

    assert previsao.ready_at == _varredura(item)
    assert previsao.ready_at.date() == date(2026, 10, 18)  # passa da meia-noite da parede


# ---------------------------------------------------------------------------
# 2) Já pronto / sem treino
# ---------------------------------------------------------------------------


def test_ja_pronto_nao_tem_instante_e_usa_o_rotulo_do_dono():
    previsao = ready_forecast(_item(hours_since_trained=40.0, readiness_status="READY"), AGORA)
    assert previsao == ReadyForecast(ready_at=None, label=READY_NOW_LABEL)


def test_ready_com_tempo_restante_continua_pronto():
    """O modelo já liberou o grupo (READY) mesmo antes dos 100% de tempo: a
    coluna diz "Pronto", não uma data futura que contradiria o "Estado"."""
    item = _item(hours_since_trained=36.0, readiness_status="READY")
    assert ready_forecast(item, AGORA) == ReadyForecast(ready_at=None, label=READY_NOW_LABEL)


def test_grupo_sem_treino_na_janela_e_pronto():
    item = _item(session_dose=0.0, estimated_recovery_hours=0.0, hours_since_trained=None, readiness_status="READY")
    assert ready_forecast(item, AGORA) == ReadyForecast(ready_at=None, label=READY_NOW_LABEL)


def test_status_ausente_e_tratado_como_o_overview_trata():
    # `_group_out` cai em READY quando o status não vem; a previsão também.
    item = _item()
    del item["readiness_status"]
    assert ready_forecast(item, AGORA).label == READY_NOW_LABEL


# ---------------------------------------------------------------------------
# 3) Sinal que só uma nova sessão limpa
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sinal",
    [
        {"joint_pain": True},
        {"muscle_pain": 5},
        {"performance_ratio": 0.92},
        {"excessive_rep_drop": True},
    ],
    ids=["dor_articular", "dor_muscular", "queda_desempenho", "queda_reps"],
)
def test_sinal_persistente_nao_tem_previsao(sinal):
    item = _item(hours_since_trained=60.0, **sinal)  # tempo já cumprido
    assert ready_forecast(item, AGORA) == ReadyForecast(ready_at=None, label=None)


# ---------------------------------------------------------------------------
# 4) Virada de fuso
# ---------------------------------------------------------------------------


def test_instante_leva_o_fuso_do_usuario_e_o_dia_e_o_da_parede():
    parede = datetime(2026, 10, 18, 22, 30)
    instante = user_wall_to_instant(parede, "America/Sao_Paulo")

    assert instante.isoformat() == "2026-10-18T22:30:00-03:00"
    # Em UTC já é o dia seguinte: quem lesse sem fuso erraria o dia.
    assert instante.astimezone(timezone.utc).date() == date(2026, 10, 19)


def test_outro_fuso_muda_so_o_deslocamento():
    parede = datetime(2026, 10, 18, 22, 30)
    assert user_wall_to_instant(parede, "America/Manaus").isoformat() == "2026-10-18T22:30:00-04:00"


def test_fuso_invalido_cai_no_default_e_aware_passa_direto():
    parede = datetime(2026, 10, 18, 9, 0)
    assert user_wall_to_instant(parede, "Nao/Existe").isoformat() == "2026-10-18T09:00:00-03:00"
    ja_aware = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)
    assert user_wall_to_instant(ja_aware, "America/Sao_Paulo") is ja_aware


# ---------------------------------------------------------------------------
# 5) Contrato do endpoint
# ---------------------------------------------------------------------------


def _window(fuso: str) -> WindowInfo:
    return WindowInfo(
        kind=WindowKind.ROLLING_7D,
        date_from=date(2026, 10, 11),
        date_to=date(2026, 10, 17),
        timezone=fuso,
        days=7,
    )


class _ConnVazia:
    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        return None

    def fetchall(self):
        return []


@pytest.mark.parametrize("fuso", ["America/Sao_Paulo", "America/Manaus"])
def test_endpoint_devolve_bom_para_com_o_fuso_da_janela(monkeypatch, fuso):
    itens = [
        _item(muscle_group="Peitoral", muscle_group_id=1),
        _item(muscle_group="Costas", muscle_group_id=2, hours_since_trained=40.0, readiness_status="READY"),
        _item(muscle_group="Quadríceps", muscle_group_id=3, hours_since_trained=60.0, joint_pain=True),
    ]
    agoras_recebidos = []

    def readiness_items(user_id, now_local=None):
        agoras_recebidos.append(now_local)
        return list(itens), None

    monkeypatch.setattr(service, "_readiness_items", readiness_items)
    monkeypatch.setattr(service, "user_now", lambda nome: AGORA)
    monkeypatch.setattr(
        service, "load_context_events", lambda conn, *, user_id, window: ([], True, [])
    )

    payload = service.build_recovery_signals(_ConnVazia(), user_id=7, window=_window(fuso))
    resposta = RecoverySignalsResponse.model_validate(payload)
    grupos = {grupo.muscle_group: grupo for grupo in resposta.groups}
    # Um "agora" só: o overview mede as horas com o mesmo instante do "Bom para".
    assert agoras_recebidos == [AGORA]

    esperado = user_wall_to_instant(_varredura(itens[0]), fuso)
    assert grupos["Peitoral"].ready_at == esperado
    assert grupos["Peitoral"].ready_label is None
    assert grupos["Costas"].ready_at is None and grupos["Costas"].ready_label == READY_NOW_LABEL
    assert grupos["Quadríceps"].ready_at is None and grupos["Quadríceps"].ready_label is None

    json = resposta.model_dump(mode="json")
    peitoral = next(g for g in json["groups"] if g["muscle_group"] == "Peitoral")
    deslocamento = "-03:00" if fuso == "America/Sao_Paulo" else "-04:00"
    assert peitoral["ready_at"].endswith(deslocamento)
    # A recomendação continua no payload (o cartão do celular ainda a mostra).
    assert "recommendation" in peitoral


def test_readiness_items_repassa_o_agora_ao_overview(monkeypatch):
    from app.intelligence.muscle_recovery_service import MuscleRecoveryService

    chamadas = []

    def overview(self, **kwargs):
        chamadas.append(kwargs)
        return []

    monkeypatch.setattr(MuscleRecoveryService, "get_recovery_overview", overview)
    monkeypatch.setattr(MuscleRecoveryService, "get_global_readiness_adjustment", lambda self, user_id=None: None)

    service._readiness_items(7, now_local=AGORA)
    assert chamadas == [{"user_id": 7, "now_local": AGORA}]


def test_falha_na_previsao_nao_derruba_a_tela(monkeypatch):
    """Mesma regra de `_readiness_items`: a falha vira "sem previsão"."""

    def quebra(item, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "ready_forecast", quebra)
    saida = service._group_out(_item(), now_local=AGORA, timezone_name="America/Sao_Paulo")
    assert saida["ready_at"] is None and saida["ready_label"] is None


def test_sessao_sem_hora_do_treino_nao_tem_previsao():
    """O retrato mediu pelos dias; a projeção partiria de zero hora e
    atrasaria o instante. Sem o dado, sem previsão."""
    item = _item(hours_since_trained=None, days_since_trained=1)
    assert ready_forecast(item, AGORA) == ReadyForecast(ready_at=None, label=None)


def test_contrato_recusa_bom_para_sem_fuso():
    from pydantic import ValidationError

    from app.intelligence.dashboard.contracts import RecoveryGroupOut

    metrica = {"value": 1.0, "unit": "h", "nature": "INFERRED", "metric_version": "v", "data_quality": "HIGH"}
    base = {
        "muscle_group": "Peitoral",
        "readiness_status": "REDUCED_DOSE",
        "recovery_pct": metrica,
        "estimated_recovery_hours": metrica,
        "remaining_hours": metrica,
        "hours_since_last_stimulus": metrica,
        "session_dose": metrica,
    }
    RecoveryGroupOut.model_validate({**base, "ready_at": "2026-10-18T14:00:00-03:00"})
    with pytest.raises(ValidationError):
        RecoveryGroupOut.model_validate({**base, "ready_at": "2026-10-18T14:00:00"})
