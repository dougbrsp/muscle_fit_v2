"""#182: o RPE esperado DA SERIE e gravado no POST (`rpe_target_planned`), com o
valor que a ficha mostra (`charged_rpe_targets_by_set`). Dono:
`effective_intensity.planned_set_rpe_target`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import workout_sessions as ws
from app.domain import effective_intensity as ei
from app.domain.next_set_load import planned_ramp_rpe
from app.exercise_set_update_merge import merge_sets_for_update
from tests.unit.test_client_key_sessao_idempotente import SESSION, _identity, _install
from tests.unit.test_modo_intensivo import FakeIntensiveDB


def _target(set_type, rpe_text, index, effective):
    """O que a serie cobra: o mesmo caminho do POST (`set_intensity_reading`)."""
    intensity = ei.EffectiveIntensity(rpe_target_effective=effective, ceiling=10, failure_allowed=True)
    reading = ei.set_intensity_reading(set_type, 8, intensity, planned_rpe=planned_ramp_rpe(rpe_text, index))
    return ei.planned_set_rpe_target(set_type, reading.target)


@pytest.mark.parametrize("effective", [9, 8, 7])
def test_dono_igual_ao_que_a_ficha_mostra(effective):
    tela = ei.charged_rpe_targets_by_set("7-8-9", 3, effective)
    gravado = [_target("working", "7-8-9", i, effective) for i in range(3)]
    assert [float(v) for v in gravado] == tela
    assert gravado[0] == 7 and gravado[2] == min(9, effective)


def test_dono_sem_rampa_e_o_alvo_efetivo():
    assert _target("working", None, 0, 9) == 9
    assert [float(_target("working", None, i, 8)) for i in range(2)] == ei.charged_rpe_targets_by_set(None, 2, 8)


@pytest.mark.parametrize("set_type", ["warmup", "feeler"])
def test_dono_preparatoria_e_nula(set_type):
    assert _target(set_type, "7-8-9", 0, 9) is None


def test_dono_sem_ficha_e_sem_alvo_e_nulo():
    assert ei.planned_set_rpe_target("working", None) is None


class FakeRampDB(FakeIntensiveDB):
    """+ a contagem ordinal das series de trabalho anteriores (a rampa)."""

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT COUNT(*) AS n FROM exercise_sets WHERE exercise_id = %s AND set_number <"):
            p = tuple(params)
            self.sql.append(sql)
            self._result = {
                "n": sum(
                    1
                    for s in self.sets
                    if s["exercise_id"] == p[0] and s["set_number"] < p[1] and s["set_type"] in ("", "working")
                )
            }
            return
        super().execute(sql, params)


@pytest.fixture
def db(monkeypatch):
    fake = _install(monkeypatch, FakeRampDB())
    monkeypatch.setattr(ws, "user_timezone", lambda conn, user_id: "America/Sao_Paulo")
    original = ws._session_exercise_row
    monkeypatch.setattr(
        ws,
        "_session_exercise_row",
        lambda cur, sid, eid: {**original(cur, sid, eid), "plan_rpe_target": "7-8-9", "deload_source": None},
    )
    return fake


def _post(exercise_id, **kw):
    base = {"reps": 10, "weight_kg": Decimal(40), "rpe": Decimal(8)}
    return ws.add_session_set(SESSION, exercise_id, ws.SessionSetIn(**{**base, **kw}), identity=_identity())


def test_post_grava_a_rampa_e_devolve_o_campo(db):
    ex = db.add_exercise()
    outs = [_post(ex), _post(ex), _post(ex)]
    assert [s["rpe_target_planned"] for s in db.sets] == [7, 8, 9]
    assert [o["rpe_target_planned"] for o in outs] == [7, 8, 9]
    # o alvo do exercicio nao muda: 9 (intensidade do fake) em todas
    assert [s["rpe_target_effective"] for s in db.sets] == [9, 9, 9]


def test_post_preparatoria_grava_nulo(db):
    ex = db.add_exercise()
    out = _post(ex, set_type="warmup")
    assert db.sets[0]["rpe_target_planned"] is None and out["rpe_target_planned"] is None
    assert db.sets[0]["rpe_target_effective"] is None


def test_repeticao_por_client_key_devolve_o_gravado(db):
    ex = db.add_exercise()
    _post(ex, client_key="k1")
    db.sets[0]["rpe_target_planned"] = Decimal("6.5")  # gravado, diferente do que a rampa daria hoje
    again = _post(ex, client_key="k1")
    assert again["duplicate"] is True and again["rpe_target_planned"] == 6.5
    assert len(db.sets) == 1


def test_patch_nao_altera_o_esperado(db):
    ex = db.add_exercise()
    _post(ex)
    db.sets[0]["rpe_target_planned"] = Decimal("7.0")
    out = ws.update_session_set(SESSION, ex, 1, ws.SessionSetPatchIn(reps=12), identity=_identity())
    assert db.sets[0]["rpe_target_planned"] == Decimal("7.0")
    assert out["rpe_target_planned"] == 7.0


def test_edicao_pelo_registro_preserva_a_serie_existente_e_deixa_a_nova_nula():
    stored = [{"set_number": 1, "set_type": "working", "rpe_target_planned": Decimal("7.5")}]
    merged = merge_sets_for_update([{"set_number": 1, "reps": 10}, {"set_number": 2, "reps": 10}], stored)
    assert [m["rpe_target_planned"] for m in merged] == [Decimal("7.5"), None]
