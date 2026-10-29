"""`equipment_record` - recorde e e1RM do Modo Treino.

Recorde (M76 Lote 3b, decisão 3): a regra é a do dashboard,
`session_records.record_events` — carga direcional ESTRITAMENTE maior que
todas as exposições anteriores da mesma chave exercício + equipamento +
convenção; 1ª exposição não é recorde; empate de carga com mais reps não é
recorde; sem equipamento resolvido não há recorde; reps só informativas.

e1RM: o maior confiável (Epley + RIR, até 12 reps,
`THRESHOLDS["e1rm_max_reliable_reps"]`).

Rodar: cd api && python -m pytest tests/unit/test_equipment_record.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.equipment_record import current_mark, current_record, record_payload, summarize_equipment_history
from app.intelligence.dashboard.metrics_registry import THRESHOLDS
from app.intelligence.muscle_recovery_service import MuscleRecoveryService

EXERCICIO = 500
BANCO = 7
SMITH = 8


def _row(weight, reps, rpe=None):
    return {"weight_kg": weight, "reps": reps, "rpe": rpe}


def _hist(activity_id, max_kg, *, equipment_id=BANCO, convention=None, min_kg=None, exercise_id=EXERCICIO):
    """Linha de `session_records.HISTORY_SQL_TEMPLATE` (uma por exposição)."""
    return {
        "activity_id": activity_id,
        "exercise_row_id": activity_id * 10,
        "exercise_id": exercise_id,
        "local_date": f"2026-09-{activity_id:02d}",
        "started_at": None,
        "position_in_session": 1,
        "equipment_id": equipment_id,
        "load_convention": convention,
        "prescribed_reps_target": None,
        "max_weight_kg": max_kg,
        "min_weight_kg": min_kg if min_kg is not None else max_kg,
    }


def _sets(activity_id, series):
    return [
        {"exercise_row_id": activity_id * 10, "set_number": i + 1, "reps": reps, "weight_kg": kg, "rpe": None}
        for i, (reps, kg) in enumerate(series)
    ]


def _record(history, *, equipment_id=BANCO, convention=None, exclude=None):
    return current_record(
        history, exercise_id=EXERCICIO, equipment_id=equipment_id, load_convention=convention, exclude_activity_id=exclude
    )


# ---------------------------------------------------------------------------
# Recorde: a regra do dashboard
# ---------------------------------------------------------------------------


def test_primeira_exposicao_nao_e_recorde():
    assert _record([_hist(1, 30)]) is None
    # e continua sem recorde enquanto ninguém superar a primeira
    assert _record([_hist(1, 30), _hist(2, 28), _hist(3, 30)]) is None


def test_recorde_e_a_carga_que_superou_estritamente_e_reps_so_informam():
    history = [_hist(1, 30), _hist(2, 32.5), _hist(3, 32.5)]
    event = _record(history)
    assert event is not None and event.exposure.activity_id == 2
    sets = _sets(2, [(8, 32.5), (6, 32.5), (10, 30)]) + _sets(3, [(12, 32.5)])
    # reps = as da série recorde (na carga do recorde, na exposição recorde);
    # as 12 reps a 32,5 da sessão 3 NÃO viram recorde (empate na carga).
    assert record_payload(event, sets) == {"reps": 8, "weight_kg": 32.5, "kind": "record"}


def test_empate_de_carga_com_mais_reps_nao_e_recorde():
    """Antes: (carga, reps) com desempate por reps — 30 x 13 batia 30 x 10."""
    assert _record([_hist(1, 30), _hist(2, 30)]) is None


def test_recorde_vigente_e_o_ultimo_que_superou():
    event = _record([_hist(1, 30), _hist(2, 32.5), _hist(3, 31), _hist(4, 35)])
    assert event.exposure.activity_id == 4
    assert event.load_kg == 35 and event.previous_load_kg == 32.5


def test_outro_equipamento_nao_entra_na_chave():
    history = [_hist(1, 30), _hist(2, 60, equipment_id=SMITH), _hist(3, 32.5)]
    assert _record(history).load_kg == 32.5
    assert _record(history, equipment_id=SMITH) is None  # só uma exposição no Smith


def test_sem_equipamento_resolvido_nao_ha_recorde():
    """Sem `equipment_id` a chave não é comparável quando o exercício tem
    histórico em algum equipamento (antes: equipamentos misturados)."""
    history = [_hist(1, 30, equipment_id=None), _hist(2, 40, equipment_id=None), _hist(3, 35)]
    assert _record(history, equipment_id=None) is None


def test_excecao_do_paragrafo_8_1_historico_inteiro_sem_equipamento():
    history = [_hist(1, 30, equipment_id=None), _hist(2, 40, equipment_id=None)]
    assert _record(history, equipment_id=None).load_kg == 40


def test_convencao_diferente_e_outra_chave():
    history = [_hist(1, 30, convention="per_dumbbell"), _hist(2, 32, convention="total"), _hist(3, 34, convention="total")]
    assert _record(history, convention="total").load_kg == 34
    assert _record(history, convention="per_dumbbell") is None


def test_assistido_recorde_e_a_menor_assistencia():
    history = [_hist(1, 30, convention="assisted"), _hist(2, 20, convention="assisted"), _hist(3, 25, convention="assisted")]
    event = _record(history, convention="assisted")
    assert event.exposure.activity_id == 2
    assert record_payload(event, _sets(2, [(8, 20)])) == {"reps": 8, "weight_kg": 20.0, "kind": "record"}


def test_peso_corporal_nao_tem_recorde_de_carga():
    history = [_hist(1, 10, convention="bodyweight"), _hist(2, 20, convention="bodyweight")]
    assert _record(history, convention="bodyweight") is None


def test_sessao_atual_fica_fora_do_historico():
    history = [_hist(1, 30), _hist(2, 32.5), _hist(3, 60)]
    assert _record(history, exclude=3).load_kg == 32.5
    assert _record(history, exclude="3").load_kg == 32.5


def test_sem_evento_nao_ha_payload():
    assert record_payload(None, []) is None


# ---------------------------------------------------------------------------
# Marca a bater: "Recorde" ou "Melhor carga" (M76 Lote 3d)
# ---------------------------------------------------------------------------


def _mark(history, *, equipment_id=BANCO, convention=None, exclude=None):
    return current_mark(
        history, exercise_id=EXERCICIO, equipment_id=equipment_id, load_convention=convention, exclude_activity_id=exclude
    )


def test_marca_com_recorde_e_o_recorde():
    history = [_hist(1, 30), _hist(2, 32.5)]
    kind, event = _mark(history)
    assert kind == "record"
    assert event.exposure.activity_id == 2
    assert record_payload(event, _sets(2, [(8, 32.5)]), kind) == {"reps": 8, "weight_kg": 32.5, "kind": "record"}


def test_sem_recorde_a_marca_e_a_melhor_carga_da_primeira_exposicao():
    """A 1ª exposição ainda detém a marca: sem recorde, mostra "Melhor carga"
    (a maior carga da chave) com as reps daquela série."""
    history = [_hist(1, 30), _hist(2, 28), _hist(3, 30)]
    kind, best = _mark(history)
    assert kind == "best_load"
    assert best.exposure.activity_id == 1  # empate depois não toma a marca
    payload = record_payload(best, _sets(1, [(10, 30), (12, 25)]) + _sets(3, [(14, 30)]), kind)
    assert payload == {"reps": 10, "weight_kg": 30.0, "kind": "best_load"}


def test_melhor_carga_com_uma_exposicao_so():
    kind, best = _mark([_hist(1, 40)])
    assert kind == "best_load" and best.load_kg == 40


def test_melhor_carga_respeita_a_chave():
    history = [_hist(1, 30), _hist(2, 60, equipment_id=SMITH), _hist(3, 30, convention="per_dumbbell")]
    kind, best = _mark(history)
    assert kind == "best_load" and best.load_kg == 30 and best.exposure.activity_id == 1
    assert _mark(history, equipment_id=SMITH)[1].load_kg == 60


def test_melhor_carga_assistida_e_a_menor_assistencia():
    history = [_hist(1, 30, convention="assisted", min_kg=25), _hist(2, 35, convention="assisted", min_kg=27.5)]
    kind, best = _mark(history, convention="assisted")
    assert kind == "best_load"
    assert best.exposure.activity_id == 1 and best.load_kg == 25
    assert record_payload(best, _sets(1, [(8, 30), (6, 25)]), kind) == {"reps": 6, "weight_kg": 25.0, "kind": "best_load"}


def test_sem_equipamento_resolvido_nao_ha_melhor_carga():
    history = [_hist(1, 30, equipment_id=None), _hist(2, 35)]
    assert _mark(history, equipment_id=None) is None


def test_peso_corporal_sem_carga_nao_tem_marca():
    """Sem "recorde de reps" inventado: `bodyweight` não tem carga."""
    history = [_hist(1, 10, convention="bodyweight"), _hist(2, 20, convention="bodyweight")]
    assert _mark(history, convention="bodyweight") is None


def test_sem_carga_registrada_nao_ha_melhor_carga():
    assert _mark([_hist(1, None)]) is None


def test_sessao_atual_fica_fora_da_melhor_carga():
    kind, best = _mark([_hist(1, 30), _hist(2, 60)], exclude=2)
    assert kind == "best_load" and best.load_kg == 30


# ---------------------------------------------------------------------------
# e1RM (inalterado no Lote 3b)
# ---------------------------------------------------------------------------


def test_sem_historico_nao_ha_e1rm():
    assert summarize_equipment_history([]) == {"e1rm_kg": None, "load_convention": "total"}


def test_e1rm_reusa_a_formula_do_dashboard_e_pega_o_maior_confiavel():
    result = summarize_equipment_history([_row(30, 10, 10), _row(30, 8, 9), _row(32, 6, 10)])
    # Epley com RIR (MuscleRecoveryService.estimated_1rm): 30 x (1 + 10/30) = 40
    assert result["e1rm_kg"] == pytest.approx(40.0)
    assert result["e1rm_kg"] == pytest.approx(MuscleRecoveryService.estimated_1rm(30, 10, 10))


def test_e1rm_de_serie_com_mais_de_12_reps_nao_conta():
    assert THRESHOLDS["e1rm_max_reliable_reps"] == 12
    result = summarize_equipment_history([_row(30, 13, 10), _row(30, 10, 10)])
    assert result["e1rm_kg"] == pytest.approx(40.0)
    assert summarize_equipment_history([_row(30, 15, 10)])["e1rm_kg"] is None


def test_rpe_abaixo_de_10_soma_rir_ao_e1rm():
    assert summarize_equipment_history([_row(100, 5, 8)])["e1rm_kg"] == pytest.approx(100 * (1 + 7 / 30), abs=0.01)


def test_serie_sem_rpe_ou_sem_carga_nao_gera_e1rm():
    assert summarize_equipment_history([_row(40, 8, None)])["e1rm_kg"] is None
    assert summarize_equipment_history([_row(None, 12, 9), _row(0, 15, 10)])["e1rm_kg"] is None


def test_assistido_e_peso_corporal_sem_e1rm():
    assert summarize_equipment_history([_row(30, 10, 9)], "assisted") == {"e1rm_kg": None, "load_convention": "assisted"}
    assert summarize_equipment_history([_row(10, 8, 9)], "bodyweight") == {"e1rm_kg": None, "load_convention": "bodyweight"}


def test_por_halter_mantem_o_numero_registrado():
    result = summarize_equipment_history([_row(20, 10, 10)], "per_dumbbell")
    assert result["e1rm_kg"] == pytest.approx(MuscleRecoveryService.estimated_1rm(20, 10, 10), abs=0.01)


def test_aceita_decimal_e_string_do_driver_e_ignora_reps_invalidas():
    result = summarize_equipment_history(
        [_row(Decimal("24.95"), 10, Decimal("9.5")), _row("30.00", "0", 9), _row(50, None, 9)]
    )
    assert result["e1rm_kg"] == pytest.approx(round(24.95 * (1 + 10.5 / 30), 2))
