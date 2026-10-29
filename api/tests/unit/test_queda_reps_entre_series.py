"""Queda de repeticoes entre series (sinal EXCESSIVE_REP_DROP da recuperacao).

Lote 1/P2 da investigacao RPE 10 (2026-10-11; dono `intelligence/volume_service.py`,
constantes `REP_DROP_MIN_FRACTION` e `REP_DROP_IGNORE_FROM_RPE`; mapa
`docs/motores/FONTES_DA_VERDADE.md` §3.2, guarda `queda_reps_entre_series`):

- so series `working` entram (ajuste/backoff/tecnica na mesma carga, nao);
- a ultima serie de trabalho em RPE >= 9 (perto da falha) nao gera queda;
- RPE abaixo do corte, ou sem RPE, continua sinal;
- o detalhe (reps da 1a e da ultima) vai para o blocking_signal.
"""

from __future__ import annotations

import pytest

from app.intelligence import volume_service as vs
from app.intelligence.volume_service import VolumeService


def _row(n, reps, rpe=None, weight=50, set_type="working"):
    return {"set_number": n, "reps": reps, "rpe": rpe, "weight_kg": weight, "set_type": set_type}


def test_constantes_nomeadas_com_os_valores_decididos():
    assert vs.REP_DROP_MIN_FRACTION == 0.30
    assert vs.REP_DROP_IGNORE_FROM_RPE == 9


@pytest.mark.parametrize(
    "rows,esperado",
    [
        # 12 -> 7 (queda de 42%) com RPE 8 na ultima: sinal
        ([_row(1, 12, 7), _row(2, 10, 8), _row(3, 7, 8)], {"first_reps": 12, "last_reps": 7}),
        # mesma queda sem RPE registrado: continua sinal
        ([_row(1, 12), _row(2, 7)], {"first_reps": 12, "last_reps": 7}),
        # mesma queda com a ultima em RPE 9 ou 10: esperada, nao e sinal
        ([_row(1, 12, 8), _row(2, 7, 9)], None),
        ([_row(1, 12, 9), _row(2, 7, 10)], None),
        # queda de 20%: abaixo do limiar
        ([_row(1, 10, 7), _row(2, 8, 8)], None),
        # carga diferente: nao compara
        ([_row(1, 12, 7, weight=50), _row(2, 7, 8, weight=45)], None),
        # uma serie so
        ([_row(1, 12, 7)], None),
    ],
)
def test_queda_entre_a_primeira_e_a_ultima_de_trabalho(rows, esperado):
    assert VolumeService._rep_drop_from_sets(rows) == esperado
    assert VolumeService._excessive_rep_drop_from_sets(rows) is (esperado is not None)


@pytest.mark.parametrize("tipo", ["backoff", "feeler", "warmup", "rest_pause", "drop_set", "forced_rep"])
def test_so_series_working_entram(tipo):
    """Ajuste/backoff na mesma carga entravam por engano antes do Lote 1."""
    rows = [_row(1, 12, 8), _row(2, 12, 8), _row(3, 5, 7, set_type=tipo)]
    assert VolumeService._rep_drop_from_sets(rows) is None


def test_item_do_historico_leva_o_detalhe_do_exercicio():
    item = {
        "mapping_id": 7,
        "original_name": "Abdominal",
        "load_convention": None,
        "set_rows": [_row(2, 9, 8), _row(1, 15, 7)],
    }
    out = VolumeService._complete_history_item(item)
    assert out["excessive_rep_drop"] is True
    assert out["rep_drop_detail"] == {
        "exercise_id": 7,
        "exercise_name": "Abdominal",
        "first_reps": 15,
        "last_reps": 9,
    }
    item["set_rows"] = [_row(1, 15, 9), _row(2, 9, 10)]
    out = VolumeService._complete_history_item(item)
    assert out["excessive_rep_drop"] is False and out["rep_drop_detail"] is None
