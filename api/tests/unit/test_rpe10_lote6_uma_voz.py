"""RPE 10 Lote 6/P9 - "uma voz no Modo Treino" (2026-10-11).

- status por SERIE no motor (`progression_engine.set_intensity_status`), com
  o mesmo nucleo do status da sessao, devolvido no salvamento da serie;
- categorias da IA por serie no vocabulario do motor
  (`domain/set_evaluation_categories.py`);
- resumo dos sinais de recuperacao congelado na ficha
  (`domain/recovery_signal_summary.py`) para o banner e o briefing.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import workout_sessions
from app.domain import effective_intensity as ei
from app.domain import progression_engine as pe
from app.domain import set_evaluation_categories as sec
from app.domain.exercise_briefing import DECISION_LABELS, describe_adjustment
from app.domain.prescription import build_prescription
from app.domain.recovery_signal_summary import RECOVERY_SIGNAL_LABELS, summarize_signals
from app.domain.training_safety import FAILURE_DENIED_BY_PAIR, FAILURE_DENIED_BY_RECOVERY, build_training_safety
from tests.support import progression_fixtures as pf
from tests.support.fake_db import NullContext, fake_transaction

# ---------------------------------------------------------------------------
# 1. Status por serie: mesmo nucleo do status da sessao
# ---------------------------------------------------------------------------

_RPES = (None, 6, 7.5, 8, 9, 10)
_ALVOS = (7, 8, 9, 10)
_ORIGENS = ((True, None), (False, FAILURE_DENIED_BY_PAIR), (False, FAILURE_DENIED_BY_RECOVERY), (False, None))


@pytest.mark.parametrize("rpe", _RPES)
@pytest.mark.parametrize("alvo", _ALVOS)
@pytest.mark.parametrize(("falha", "origem"), _ORIGENS)
def test_serie_unica_tem_o_mesmo_status_da_sessao_de_uma_serie(rpe, alvo, falha, origem):
    valor = None if rpe is None else Decimal(str(rpe))
    sessao = pe.intensity_status_for(valor, alvo, falha, highest=valor, failure_denied_by=origem)
    assert pe.set_intensity_status(valor, alvo, falha, failure_denied_by=origem) == sessao


def test_serie_sem_alvo_so_reconhece_o_evento_do_par():
    assert pe.set_intensity_status(Decimal(10), None, False, failure_denied_by=FAILURE_DENIED_BY_PAIR) == (
        pe.INTENSITY_ABOVE_SAFE_CEILING
    )
    assert pe.set_intensity_status(Decimal(10), None, False, failure_denied_by=FAILURE_DENIED_BY_RECOVERY) is None
    assert pe.set_intensity_status(Decimal(8), None, True) is None


@pytest.mark.parametrize("set_type", ["warmup", "feeler", "rest_pause", "drop_set"])
def test_serie_que_nao_e_de_trabalho_nao_tem_status(set_type):
    intensity = ei.EffectiveIntensity(rpe_target_effective=9, ceiling=9, failure_allowed=False)
    assert ei.set_intensity_reading(set_type, 10, intensity).status is None


def test_leitura_da_serie_informativa_e_de_seguranca():
    recuperacao = ei.EffectiveIntensity(
        rpe_target_effective=9, ceiling=9, failure_allowed=False, failure_denied_by=FAILURE_DENIED_BY_RECOVERY
    )
    leitura = ei.set_intensity_reading("working", 10, recuperacao)
    assert (leitura.status, leitura.safety_event) == (
        pe.INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING,
        pe.SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
    )
    par = ei.EffectiveIntensity(
        rpe_target_effective=9, ceiling=9, failure_allowed=False, failure_denied_by=FAILURE_DENIED_BY_PAIR
    )
    leitura = ei.set_intensity_reading("working", 10, par)
    assert (leitura.status, leitura.safety_event) == (
        pe.INTENSITY_ABOVE_SAFE_CEILING,
        pe.SAFETY_EVENT_RPE_ABOVE_CEILING,
    )
    abaixo = ei.set_intensity_reading("working", 7, par)
    assert (abaixo.status, abaixo.safety_event, abaixo.target) == (pe.INTENSITY_BELOW_TARGET, None, 9)


class _CursorDaSerie(NullContext):
    def __init__(self):
        self.executados: list[str] = []

    def execute(self, sql, params=None):
        self.executados.append(sql)

    def fetchone(self):
        return {"id": 5, "plan_technique": None, "plan_reps_target": "10-12", "prescription_json": None}

    def fetchall(self):
        return []


def test_salvar_serie_devolve_o_status_do_motor_e_os_rotulos(monkeypatch):
    cursor = _CursorDaSerie()
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(workout_sessions, "ensure_valid_codes", lambda codes: None)
    monkeypatch.setattr(workout_sessions, "_get_live_session", lambda cur, sid, uid: {"id": sid})
    monkeypatch.setattr(workout_sessions, "_refresh_exercise_aggregates", lambda cur, eid: None)
    monkeypatch.setattr(workout_sessions, "_refresh_activity_avg_rpe", lambda cur, sid: None)
    monkeypatch.setattr(workout_sessions, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(
        workout_sessions.effective_intensity,
        "resolve_for_logged_exercise",
        lambda conn, eid: ei.EffectiveIntensity(
            rpe_target_effective=9, ceiling=9, failure_allowed=False, failure_denied_by=FAILURE_DENIED_BY_RECOVERY
        ),
    )
    payload = workout_sessions.SessionSetIn(set_number=2, reps=10, weight_kg=Decimal(40), rpe=Decimal(10))
    resposta = workout_sessions.add_session_set(1, 5, payload, identity=SimpleNamespace(user_id=1))
    assert resposta["intensity_status"] == pe.INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING
    # set-eval-v5: o rotulo diz que o status e de ESFORCO; as reps contra a
    # faixa da ficha vem ao lado.
    assert resposta["intensity_status_label"] == "Esforço atingido (falha não permitida)"
    assert resposta["reps_position"] == pe.REPS_IN_RANGE
    assert resposta["reps_position_label"] == "Reps dentro da faixa"
    assert resposta["safety_event"] == pe.SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET
    assert resposta["safety_event_label"] == "RPE acima do alvo do dia"
    # set-eval-v6: 10 reps em RPE 10 -> a regra da proxima serie mantem a carga
    # (a mesma voz: o RPE 10 informativo nao vira reducao).
    assert resposta["next_set_load"]["direction"] == pe.HOLD
    assert resposta["next_set_load"]["load_kg"] == 40.0
    assert any("INSERT INTO exercise_sets" in sql for sql in cursor.executados)


# ---------------------------------------------------------------------------
# 2. Categorias da IA = acoes do motor
# ---------------------------------------------------------------------------


def test_categorias_sao_um_subconjunto_das_acoes_do_motor():
    for code in sec.SET_EVALUATION_CATEGORIES:
        assert code in DECISION_LABELS
        assert sec.SET_EVALUATION_CATEGORY_LABELS[code] == DECISION_LABELS[code]


@pytest.mark.parametrize(
    ("categoria", "sugerida", "executada", "esperada"),
    [
        (pe.INCREASE_LOAD, 40, 40, pe.HOLD),
        (pe.HOLD, 42.5, 40, pe.INCREASE_LOAD),
        (pe.HOLD, 37.5, 40, pe.DECREASE_LOAD),
        (pe.DECREASE_LOAD, None, 40, pe.DECREASE_LOAD),
        (pe.INCREASE_LOAD, 42.5, None, pe.INCREASE_LOAD),
        (pe.INCREASE_LOAD, 42.5, 0, pe.INCREASE_LOAD),
    ],
)
def test_categoria_segue_a_direcao_da_carga(categoria, sugerida, executada, esperada):
    assert sec.category_for_load(categoria, sugerida, executada) == esperada


# ---------------------------------------------------------------------------
# 3. Sinais de recuperacao no snapshot (banner/briefing)
# ---------------------------------------------------------------------------

SINAIS = (
    {
        "code": "EXCESSIVE_REP_DROP",
        "value": True,
        "source_session_at": "2026-10-03 18:22:00",
        "exercises": [{"exercise_id": 9, "exercise_name": "Abdominal crunch", "first_reps": 12, "last_reps": 8}],
    },
    {"code": "PERFORMANCE_DROP", "value": 0.91, "source_session_at": "2026-10-05T07:10:00"},
    {"code": "RPE_ANTICIPATED", "value": True, "source_session_at": None},
)


def test_resumo_dos_sinais_traz_texto_e_data():
    assert summarize_signals(SINAIS) == [
        {
            "code": "EXCESSIVE_REP_DROP",
            "text": "queda de repetições no exercício Abdominal crunch (12 → 8 reps)",
            "source_session_date": "2026-10-03",
        },
        {
            "code": "PERFORMANCE_DROP",
            "text": RECOVERY_SIGNAL_LABELS["PERFORMANCE_DROP"],
            "source_session_date": "2026-10-05",
        },
    ]


def test_prescribe_congela_os_sinais_e_a_ficha_os_expoe():
    contrato = pe.ReadinessContract(status="REDUCED_DOSE", max_rpe=9, allow_failure=False, blocking_signals=SINAIS)
    seguranca = build_training_safety(equipment_max_safe_rpe=10)
    decisao = pe.prescribe(pf.track(), pf.group(), seguranca, contrato)
    assert decisao.evidence[pe.EVIDENCE_RECOVERY_SIGNALS] == summarize_signals(SINAIS)

    snapshot = build_prescription(
        {"sets_count": 3, "reps_target": "10-12", "rpe_target": "9"}, safety=seguranca, decision=decisao.to_dict()
    )
    assert snapshot["recovery_signals"] == summarize_signals(SINAIS)
    ajuste = describe_adjustment(snapshot)
    assert ajuste["recovery_signals"] == summarize_signals(SINAIS)
    assert ajuste["effective_rpe_ceiling"] == snapshot["effective_rpe_ceiling"]


def test_sem_sinais_a_ficha_sai_com_lista_vazia():
    decisao = pe.prescribe(
        pf.track(),
        pf.group(),
        build_training_safety(equipment_max_safe_rpe=10),
    )
    assert pe.EVIDENCE_RECOVERY_SIGNALS not in decisao.evidence
    snapshot = build_prescription({"sets_count": 3}, safety=None, decision=decisao.to_dict())
    assert snapshot["recovery_signals"] == []


def test_ficha_sem_decisao_recebe_os_sinais_do_chamador():
    snapshot = build_prescription({"sets_count": 3}, recovery_signals=summarize_signals(SINAIS[:1]))
    assert [s["code"] for s in snapshot["recovery_signals"]] == ["EXCESSIVE_REP_DROP"]
