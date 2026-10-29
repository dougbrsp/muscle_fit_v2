"""M96 (2026-10-25): veredito ÚNICO no card da série do Modo Treino.

Achado do E2E do Modo Intensivo no DEV (24/10): depois de 12 reps @ RPE 9 o
card mostrava ao mesmo tempo "Esforço abaixo do alvo" (status de esforço,
`progression_engine.set_intensity_status`, alvo 10) e "Repetições e RPE dentro
do esperado" (motivo do MANTER da carga da próxima série,
`next_set_load`, que não olhava o alvo). As duas mensagens agora derivam do
MESMO status:

- o status é cobrado contra o alvo DESTA série: o degrau da rampa planejada
  (`progression_engine.set_effort_target`, o mesmo `planned_ramp_rpe` da carga
  da próxima série) ou, no Modo Intensivo (`rpe_target_source='protocol'`), o
  alvo do protocolo gravado na série (`effective_intensity.with_protocol_target`);
- o motivo do MANTER da carga da próxima série sai desse status
  (`SetFacts.intensity_status`): abaixo do alvo / acima do teto seguro nunca
  dizem "dentro do esperado". A direção (MANTER) não muda.

Rodar: cd api && python -m pytest tests/unit/test_m96_veredito_unico_card_serie.py -q
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import workout_sessions
from app.domain import effective_intensity as ei
from app.domain import next_set_load as nsl
from app.domain import progression_engine as pe
from app.domain.training_safety import FAILURE_DENIED_BY_PAIR, FAILURE_RPE, protocol_target_rpe
from app.engine_decision_context import PairEngineContext
from tests.support.fake_db import NullContext, SharedCursorConn

PASSO = Decimal("2.5")
USER, SESSION, EXERCISE = 1, 10, 5
CARGA = Decimal(40)

# Status de esforço "atingido" (com ou sem falha liberada).
ATINGIDO = {pe.INTENSITY_REACHED, pe.INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING}
MOTIVOS_DENTRO = {nsl.REASON_WITHIN_RULE, nsl.REASON_WITHIN_PLANNED_RAMP}


class _Cursor(NullContext):
    """`{"n": anteriores}` para o COUNT das séries de trabalho anteriores."""

    def __init__(self, anteriores):
        self.anteriores = anteriores
        self.ultimo = ""

    def execute(self, sql, params=None):
        self.ultimo = sql

    def fetchone(self):
        return {"n": self.anteriores} if "COUNT(*)" in self.ultimo else {}

    def fetchall(self):
        return []


@pytest.fixture(autouse=True)
def _sem_io(monkeypatch):
    monkeypatch.setattr(workout_sessions, "load_pair_step_context", lambda c, m, g: PairEngineContext(step_kg=PASSO))
    monkeypatch.setattr(workout_sessions, "load_published_ruleset", lambda conn: nsl.DEFAULT_RULESET)


def _linha(rpe_target):
    return {
        "id": EXERCISE,
        "plan_reps_target": "10-12",
        "prescription_json": None,
        "plan_rpe_target": rpe_target,
        "mapping_id": 11,
        "gym_equipment_id": 2,
    }


def _avaliar(intensidade, *, rpe_target, reps, rpe, anteriores=0, protocol_target=None):
    cursor = _Cursor(anteriores)
    return workout_sessions._evaluate_set(
        SharedCursorConn(cursor),
        cursor,
        EXERCISE,
        _linha(rpe_target),
        intensidade,
        user_id=USER,
        session_id=SESSION,
        set_type="working",
        set_number=anteriores + 1,
        reps=reps,
        weight_kg=CARGA,
        rpe=Decimal(str(rpe)),
        joint_pain=None,
        muscle_pain=None,
        protocol_target=protocol_target,
    )


def _sem_contradicao(leitura, proxima):
    """Motivo "dentro do esperado/da rampa" só com esforço atingido; esforço
    abaixo do alvo/acima do teto nunca vem com esses motivos."""
    if proxima is None:
        return
    if proxima.reason in MOTIVOS_DENTRO:
        assert leitura.status in ATINGIDO, (leitura.status, proxima.reason)
    if leitura.status == pe.INTENSITY_BELOW_TARGET and proxima.direction == pe.HOLD:
        assert proxima.reason == nsl.REASON_HOLD_EFFORT_BELOW_TARGET
    assert "dentro do esperado" not in proxima.reason_label or leitura.status in ATINGIDO


# ---------------------------------------------------------------------------
# Caso do achado: 12 reps @ RPE 9
# ---------------------------------------------------------------------------


def test_normal_alvo_10_doze_reps_rpe_9_abaixo_do_alvo_e_motivo_coerente():
    intensidade = ei.EffectiveIntensity(rpe_target_effective=FAILURE_RPE, ceiling=FAILURE_RPE, failure_allowed=True)
    leitura, proxima = _avaliar(intensidade, rpe_target=str(FAILURE_RPE), reps=12, rpe=9)

    assert leitura.status == pe.INTENSITY_BELOW_TARGET
    assert (proxima.direction, proxima.load_kg) == (pe.HOLD, float(CARGA))  # regra do usuário intacta
    assert proxima.reason == nsl.REASON_HOLD_EFFORT_BELOW_TARGET
    assert "dentro do esperado" not in proxima.reason_label
    _sem_contradicao(leitura, proxima)


def test_normal_alvo_9_doze_reps_rpe_9_atingido_e_dentro_do_esperado():
    intensidade = ei.EffectiveIntensity(rpe_target_effective=9, ceiling=9, failure_allowed=False)
    leitura, proxima = _avaliar(intensidade, rpe_target="9", reps=12, rpe=9)

    assert leitura.status in ATINGIDO
    assert (proxima.direction, proxima.reason) == (pe.HOLD, nsl.REASON_WITHIN_RULE)
    _sem_contradicao(leitura, proxima)


def test_modo_intensivo_alvo_do_protocolo_vale_no_status_e_no_motivo():
    """Prescrição com alvo 9, série com `rpe_target_source='protocol'`: o alvo
    gravado/mostrado é o do protocolo (10 com falha liberada) e o status é
    cobrado contra ELE, não contra o da prescrição."""
    alvo_protocolo = protocol_target_rpe(FAILURE_RPE)
    intensidade = ei.EffectiveIntensity(rpe_target_effective=9, ceiling=FAILURE_RPE, failure_allowed=True)

    leitura, proxima = _avaliar(intensidade, rpe_target="9", reps=12, rpe=9, protocol_target=alvo_protocolo)

    assert leitura.target == alvo_protocolo
    assert leitura.status == pe.INTENSITY_BELOW_TARGET
    assert (proxima.direction, proxima.reason) == (pe.HOLD, nsl.REASON_HOLD_EFFORT_BELOW_TARGET)
    _sem_contradicao(leitura, proxima)


def test_modo_intensivo_rpe_no_alvo_do_protocolo_atingido():
    alvo_protocolo = protocol_target_rpe(FAILURE_RPE)
    intensidade = ei.EffectiveIntensity(rpe_target_effective=9, ceiling=FAILURE_RPE, failure_allowed=True)

    leitura, proxima = _avaliar(intensidade, rpe_target="9", reps=12, rpe=alvo_protocolo, protocol_target=alvo_protocolo)

    assert leitura.status == pe.INTENSITY_REACHED
    assert proxima.reason in MOTIVOS_DENTRO
    _sem_contradicao(leitura, proxima)


def test_protocolo_nao_e_restringido_pela_rampa_da_ficha():
    alvo_protocolo = protocol_target_rpe(FAILURE_RPE)
    intensidade = ei.EffectiveIntensity(rpe_target_effective=FAILURE_RPE, ceiling=FAILURE_RPE, failure_allowed=True)

    leitura, _ = _avaliar(intensidade, rpe_target="8-9-10", reps=12, rpe=9, protocol_target=alvo_protocolo)

    assert leitura.target == alvo_protocolo
    assert leitura.status == pe.INTENSITY_BELOW_TARGET


# ---------------------------------------------------------------------------
# Rampa planejada: o degrau da série é o alvo cobrado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "anteriores, rpe, status, motivo",
    [
        (0, 8, ATINGIDO, nsl.REASON_WITHIN_PLANNED_RAMP),  # série 1, planejada 8
        (1, 9, ATINGIDO, nsl.REASON_WITHIN_RULE),  # série 2, planejada 9
        (2, 9, {pe.INTENSITY_BELOW_TARGET}, nsl.REASON_HOLD_EFFORT_BELOW_TARGET),  # série 3, planejada 10
    ],
)
def test_rampa_8_9_10_doze_reps(anteriores, rpe, status, motivo):
    intensidade = ei.EffectiveIntensity(rpe_target_effective=FAILURE_RPE, ceiling=FAILURE_RPE, failure_allowed=True)
    leitura, proxima = _avaliar(intensidade, rpe_target="8-9-10", reps=12, rpe=rpe, anteriores=anteriores)

    assert leitura.status in status
    assert proxima.reason == motivo
    _sem_contradicao(leitura, proxima)


def test_degrau_so_restringe_o_alvo():
    assert pe.set_effort_target(9, Decimal(10)) == Decimal(9)
    assert pe.set_effort_target(10, Decimal(8)) == Decimal(8)
    assert pe.set_effort_target(10, None) == Decimal(10)
    assert pe.set_effort_target(None, Decimal(8)) is None


# ---------------------------------------------------------------------------
# Teto seguro do par
# ---------------------------------------------------------------------------


def test_rpe_acima_do_teto_seguro_nao_diz_dentro_do_esperado():
    intensidade = ei.EffectiveIntensity(
        rpe_target_effective=9, ceiling=9, failure_allowed=False, failure_denied_by=FAILURE_DENIED_BY_PAIR
    )
    leitura, proxima = _avaliar(intensidade, rpe_target="9", reps=8, rpe=FAILURE_RPE)

    assert leitura.status == pe.INTENSITY_ABOVE_SAFE_CEILING
    assert (proxima.direction, proxima.reason) == (pe.HOLD, nsl.REASON_HOLD_ABOVE_SAFE_CEILING)
    _sem_contradicao(leitura, proxima)


# ---------------------------------------------------------------------------
# Propriedade: nenhuma combinação contradiz
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alvo", [8, 9, FAILURE_RPE])
@pytest.mark.parametrize("rpe_target", [None, "9", "10", "8-9-10"])
@pytest.mark.parametrize("anteriores", [0, 1, 2])
def test_nenhuma_combinacao_contradiz(alvo, rpe_target, anteriores):
    intensidade = ei.EffectiveIntensity(
        rpe_target_effective=alvo, ceiling=FAILURE_RPE, failure_allowed=alvo == FAILURE_RPE
    )
    for reps in (4, 6, 10, 12, 16):
        for rpe in (6, 7, 8, 9, 10):
            leitura, proxima = _avaliar(intensidade, rpe_target=rpe_target, reps=reps, rpe=rpe, anteriores=anteriores)
            _sem_contradicao(leitura, proxima)


# ---------------------------------------------------------------------------
# Compatibilidade: sem status, o motivo de sempre
# ---------------------------------------------------------------------------


def test_sem_status_o_motivo_e_o_de_sempre():
    facts = nsl.SetFacts(reps=12, rpe=9, weight_kg=CARGA, step_kg=PASSO)
    assert nsl.evaluate_ruleset(facts, nsl.DEFAULT_RULESET).reason == nsl.REASON_WITHIN_RULE


def test_regra_que_casou_vence_o_status():
    """Aumentar/reduzir não muda pelo status: só o motivo do MANTER."""
    facts = nsl.SetFacts(reps=16, rpe=9, weight_kg=CARGA, step_kg=PASSO, intensity_status=pe.INTENSITY_BELOW_TARGET)
    resultado = nsl.evaluate_ruleset(facts, nsl.DEFAULT_RULESET)
    assert (resultado.direction, resultado.reason) == (pe.INCREASE_LOAD, nsl.REASON_REPS_ABOVE_LIMIT)
