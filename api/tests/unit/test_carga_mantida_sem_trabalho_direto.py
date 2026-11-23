"""#274: SKIP por NO_DIRECT_WORK mantem a ultima carga da trilha (sem progressao)."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

from app.domain import progression_engine as pe
from app.domain.training_safety import NO_DIRECT_WORK
from tests.support import progression_fixtures as fx

CONTRACT = pe.ReadinessContract(status=NO_DIRECT_WORK, max_rpe=8, allow_failure=False, volume_multiplier=0.0)


def _skip(track, technique=None):
    return pe.prescribe(
        track,
        fx.group(),
        fx.safety(),
        CONTRACT,
        catalog=fx.CATALOG,
        model_technique=technique,
        model_fixed_sets=True,
    )


def test_trilha_com_carga_mantem_a_carga_no_skip():
    d = _skip(fx.track(load=40))
    assert d.decision == pe.SKIP and d.can_progress is False
    assert d.blocked_by == (pe.BLOCKED_NO_DIRECT_WORK,)
    assert d.suggested_load_kg == 40.0
    assert isinstance(d.suggested_load_kg, float)
    json.dumps(d.to_dict())
    assert d.reason == pe.NO_DIRECT_WORK_REASON


def test_trilha_sem_carga_continua_nula():
    d = _skip(fx.track(load=None))
    assert d.suggested_load_kg is None
    assert d.reason == pe.NO_DIRECT_WORK_NO_LOAD_REASON
    assert "carga mantida" not in d.reason


def test_teto_abaixo_da_carga_nunca_cria_nem_reduz_carga():
    d = _skip(replace(fx.track(load=40), max_load_kg=Decimal("30")))
    assert d.suggested_load_kg == 40.0  # regra do helper: teto so limita aumento
    assert d.evidence[pe.EVIDENCE_EQUIPMENT_LOAD_CEILING] == pe.EQUIPMENT_CEILING_REACHED


def test_teto_acima_da_carga_nao_aumenta():
    d = _skip(replace(fx.track(load=40), max_load_kg=Decimal("100")))
    assert d.suggested_load_kg == 40.0
    assert pe.EVIDENCE_EQUIPMENT_LOAD_CEILING not in d.evidence


def test_gvt_mantem_tecnica_e_carga():
    d = _skip(fx.track(load=40, sets_count=10), "gvt")
    assert d.advanced_technique == "gvt" and d.suggested_sets == 10
    assert d.suggested_load_kg == 40.0


def test_partida_de_outra_academia_e_cortada_no_teto():
    d = _skip(replace(fx.track(load=40), max_load_kg=Decimal("30"), start_from_other_gym=True))
    assert d.suggested_load_kg == 30.0
    assert d.evidence[pe.EVIDENCE_EQUIPMENT_LOAD_CEILING] == pe.EQUIPMENT_CEILING_START_LIMITED
    assert "load_increment_kg" not in d.evidence
