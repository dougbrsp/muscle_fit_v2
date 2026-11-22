"""#241: serie de trabalho que chega fora de ordem (fila offline) regrava o
`rpe_target_planned` das seguintes na mesma transacao. Dono da composicao:
`effective_intensity.ramp_planned_rpe_for_position`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import workout_sessions as ws
from app.domain import effective_intensity as ei
from app.domain.next_set_load import planned_ramp_rpe
from tests.unit.test_client_key_sessao_idempotente import SESSION, _identity, _install
from tests.unit.test_rpe_esperado_gravado_na_serie import FakeRampDB


class FakeLateDB(FakeRampDB):
    """+ o SELECT da regravacao e o UPDATE do esperado, com as colunas reais."""

    def __init__(self):
        super().__init__()
        self.resync_selects = 0
        self.resync_updates = 0

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        p = tuple(params or ())
        if flat.startswith("SELECT id, set_number, reps_pending, measure, rpe_target_source, rpe_target_planned FROM exercise_sets"):
            self.sql.append(sql)
            self.resync_selects += 1
            rows = [
                {"id": i, **{k: s.get(k) for k in ("set_number", "reps_pending", "measure", "rpe_target_source", "rpe_target_planned")}}
                for i, s in enumerate(self.sets)
                if s["exercise_id"] == p[0] and s["set_type"] in ("", "working")
            ]
            self._rows = sorted(rows, key=lambda r: r["set_number"])
        elif flat == "UPDATE exercise_sets SET rpe_target_planned = %s WHERE id = %s":
            self.sql.append(sql)
            self.resync_updates += 1
            self.sets[p[1]]["rpe_target_planned"] = p[0]
        else:
            super().execute(sql, params)

    def fetchall(self):
        rows, self._rows = getattr(self, "_rows", []), []
        return rows


def _make(monkeypatch, rpe="7-8-9", deload=None):
    fake = _install(monkeypatch, FakeLateDB())
    monkeypatch.setattr(ws, "user_timezone", lambda conn, user_id: "America/Sao_Paulo")
    original = ws._session_exercise_row
    monkeypatch.setattr(
        ws,
        "_session_exercise_row",
        lambda cur, sid, eid: {**original(cur, sid, eid), "plan_rpe_target": rpe, "deload_source": deload},
    )
    return fake


@pytest.fixture
def db(monkeypatch):
    return _make(monkeypatch)


def _post(ex, n, **kw):
    base = {"reps": 10, "weight_kg": Decimal(40), "rpe": Decimal(8), "set_number": n}
    return ws.add_session_set(SESSION, ex, ws.SessionSetIn(**{**base, **kw}), identity=_identity())


def _planned(db):
    return [float(s["rpe_target_planned"]) for s in sorted(db.sets, key=lambda s: s["set_number"])]


@pytest.mark.parametrize("ordem", [(1, 3, 2), (3, 2, 1), (2, 1, 3), (2, 3, 1), (3, 1, 2)])
def test_fora_de_ordem_termina_na_rampa(db, ordem):
    ex = db.add_exercise()
    for n in ordem:
        _post(ex, n)
    assert _planned(db) == [7, 8, 9]


def test_em_ordem_nao_escreve(db):
    ex = db.add_exercise()
    for n in (1, 2, 3):
        _post(ex, n)
    assert _planned(db) == [7, 8, 9] and db.resync_updates == 0


def test_aquecimento_chegando_depois_nao_muda_posicao(db):
    ex = db.add_exercise()
    _post(ex, 2)
    _post(ex, 3)
    _post(ex, 1, set_type="warmup")
    assert [s["rpe_target_planned"] for s in sorted(db.sets, key=lambda s: s["set_number"])] == [None, 7, 8]
    assert db.resync_updates == 0


def test_backoff_posterior_nao_e_regravado_e_nulo_continua_nulo(db):
    ex = db.add_exercise()
    _post(ex, 3)
    _post(ex, 4, set_type="backoff")
    backoff = dict(db.sets[1])
    db.sets[0]["rpe_target_planned"] = None  # importada/sem ficha: nula
    _post(ex, 1)
    assert db.sets[1] == backoff and db.sets[0]["rpe_target_planned"] is None


def test_deload_e_sem_rampa_nao_leem_nem_escrevem(monkeypatch):
    fake = _make(monkeypatch, deload="manual")
    ex = fake.add_exercise()
    for n in (2, 1):
        _post(ex, n)
    assert fake.resync_selects == 0 and fake.resync_updates == 0
    fake = _make(monkeypatch, rpe="8")
    ex = fake.add_exercise()
    for n in (2, 1):
        _post(ex, n)
    assert fake.resync_selects == 0 and fake.resync_updates == 0


def test_repeticao_por_client_key_nao_regrava(db):
    ex = db.add_exercise()
    _post(ex, 1)
    _post(ex, 3, client_key="k3")
    _post(ex, 2)
    antes = db.resync_selects
    again = _post(ex, 3, client_key="k3")
    assert again["duplicate"] is True and _planned(db) == [7, 8, 9] and db.resync_selects == antes


def test_patch_nao_dispara_regravacao(db):
    ex = db.add_exercise()
    _post(ex, 1)
    selects = db.resync_selects
    ws.update_session_set(SESSION, ex, 1, ws.SessionSetPatchIn(reps=12), identity=_identity())
    assert db.resync_selects == selects and db.resync_updates == 0


def test_pendente_conta_posicao_mas_nao_e_regravada(db):
    ex = db.add_exercise()
    _post(ex, 2, reps=None, reps_pending=True, rpe=None)
    before = db.sets[0]["rpe_target_planned"]
    _post(ex, 3)
    _post(ex, 1)
    pend = next(s for s in db.sets if s["set_number"] == 2)
    assert pend["rpe_target_planned"] == before
    assert [float(s["rpe_target_planned"]) for s in db.sets if s["set_number"] == 3] == [9]


def test_rampa_mais_curta_que_as_series_segue_a_regra_do_post(db):
    esperado = [float(ei.ramp_planned_rpe_for_position("7-8-9", 9, i)) for i in range(4)]
    assert esperado == [7, 8, 9, 9]
    ex = db.add_exercise()
    for n in (4, 3, 2, 1):
        _post(ex, n)
    assert _planned(db) == esperado


@pytest.mark.parametrize("eff", [9, 8, 7.5])
def test_dono_igual_ao_post(eff):
    for i in range(4):
        intensity = ei.EffectiveIntensity(rpe_target_effective=eff, ceiling=10, failure_allowed=True)
        reading = ei.set_intensity_reading("working", 8, intensity, planned_rpe=planned_ramp_rpe("7-8-9", i))
        assert ei.ramp_planned_rpe_for_position("7-8-9", eff, i) == ei.planned_set_rpe_target("working", reading.target)
