"""Teto de carga do equipamento, rodada 1 de revisao: dono unico
`progression_engine.cap_load_to_ceiling`, semantica "o teto nunca aumenta e
nunca reduz sozinho", regime de forca, nota de limite, aumento pendente,
sugestao do par, plano e rotas (401, isolamento A/B, 422).

Rodar: cd api && python -m pytest tests/unit/test_teto_carga_equipamento_rodada1.py
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import equipment_load_ceiling as elc
from app.domain import next_set_load as nsl
from app.domain import prescription_service as svc
from app.domain import progression_engine as pe
from app.security import Identity, get_current_identity
from tests.support import progression_fixtures as fx
from tests.support.fake_db import NullContext, fake_transaction

NOW = datetime(2026, 10, 26, 18, 0)
pytestmark = pytest.mark.usefixtures("permissoes_por_papel")
S = "strength"
H = "hypertrophy"
SUPINO, BANCO, PEITO = 101, 201, 10
SAFETY = fx.safety(max_safe_rpe=10)
D = Decimal


# ---------------------------------------------------------------------------
# G2: a funcao unica do dono
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("teto", [None, 0, -5])
def test_cap_sem_teto_e_identidade(teto):
    assert pe.cap_load_to_ceiling(D("120"), teto) == D("120")
    assert pe.cap_load_to_ceiling(D("120"), teto, current=D("100")) == D("120")


def test_cap_sem_valor_devolve_none():
    assert pe.cap_load_to_ceiling(None, 100) is None


def test_cap_sem_atual_e_o_minimo_simples():
    assert pe.cap_load_to_ceiling(D("120"), 100) == D("100")
    assert pe.cap_load_to_ceiling(D("80"), 100) == D("80")


def test_cap_com_atual_so_limita_aumento():
    assert pe.cap_load_to_ceiling(D("120"), 100, current=D("90")) == D("100")  # sobe so ate o teto
    assert pe.cap_load_to_ceiling(D("120"), 100, current=D("100")) == D("100")  # no teto: nao sobe
    assert pe.cap_load_to_ceiling(D("120"), 80, current=D("90")) == D("90")  # teto abaixo: nem sobe nem reduz
    assert pe.cap_load_to_ceiling(D("85"), 80, current=D("90")) == D("85")  # reducao nao e tocada


# ---------------------------------------------------------------------------
# P2-1 / P2-2 / P3-9: motor da proxima sessao
# ---------------------------------------------------------------------------


def _forca(teto=None, load=95, step=2.5):
    base = fx.track(
        exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=load, is_compound=True, load_increment_kg=step
    )
    return replace(base, training_goal=S, max_load_kg=None if teto is None else D(str(teto)))


def test_forca_com_teto_abaixo_da_carga_de_trabalho_nao_aumenta_nem_reduz():
    grupo = pe.new_group_state(PEITO, NOW, S)
    # e1RM 123,33 pede 100 kg (> 95 da trilha): sem teto, INCREASE.
    sem = pe.prescribe(_forca(), grupo, SAFETY, now=NOW, current_e1rm_kg=123.33)
    assert (sem.decision, sem.suggested_load_kg) == (pe.INCREASE_LOAD, 100.0)
    com = pe.prescribe(_forca(teto=90), grupo, SAFETY, now=NOW, current_e1rm_kg=123.33)
    assert com.decision == pe.HOLD
    assert com.suggested_load_kg == 95.0  # nem o aumento, nem reducao ate o teto
    assert com.evidence[pe.EVIDENCE_EQUIPMENT_LOAD_CEILING] == pe.EQUIPMENT_CEILING_REACHED
    # e1RM menor pede reducao: o teto nao a desfaz
    menor = pe.prescribe(_forca(teto=90), grupo, SAFETY, now=NOW, current_e1rm_kg=100)
    assert (menor.decision, menor.suggested_load_kg) == (pe.DECREASE_LOAD, 82.5)


def test_forca_sobe_ate_o_teto_com_nota_de_limite():
    grupo = pe.new_group_state(PEITO, NOW, S)
    d = pe.prescribe(_forca(teto=97.5, step=5), grupo, SAFETY, now=NOW, current_e1rm_kg=125)
    assert d.decision == pe.INCREASE_LOAD
    assert d.suggested_load_kg == 97.5
    assert d.evidence[pe.EVIDENCE_EQUIPMENT_LOAD_CEILING] == pe.EQUIPMENT_CEILING_LIMITED
    assert pe.EQUIPMENT_LOAD_CEILING_LIMITED_NOTE in d.reason
    assert pe.EQUIPMENT_LOAD_CEILING_NOTE not in d.reason


def test_hipertrofia_sobe_ate_o_teto_95_passo_5_teto_97_5():
    track = replace(_forca(teto=97.5, step=5), training_goal=H, pending_load_kg=D("100"), pending_load_since=NOW)
    d = pe.prescribe(track, fx.group(phase=pe.F2), SAFETY, now=NOW)
    assert (d.decision, d.suggested_load_kg) == (pe.INCREASE_LOAD, 97.5)
    assert d.evidence[pe.EVIDENCE_EQUIPMENT_LOAD_CEILING] == pe.EQUIPMENT_CEILING_LIMITED
    assert pe.EQUIPMENT_LOAD_CEILING_LIMITED_NOTE in d.reason


def test_aumento_pendente_acima_do_teto_real_ate_o_teto_e_aplicado_nao_expira():
    base = replace(fx.track(load=40), pending_load_kg=D("45"), pending_load_since=NOW)
    com_teto = replace(base, max_load_kg=D("42.5"))
    depois = NOW + timedelta(days=1)
    # aumento REAL ate o teto (42,5): aplicado (conta para o plato), nao EXPIRED
    assert pe.track_pending_load_status(com_teto, D("42.5"), now=depois) == pe.PENDING_LOAD_APPLIED
    # ainda nao executou o teto: segue pendente
    assert pe.track_pending_load_status(com_teto, D("40"), now=depois) is None
    # sem teto, o mesmo 42,5 nao cumpre o alvo 45 e segue pendente
    assert pe.track_pending_load_status(base, D("42.5"), now=depois) is None


def test_aumento_pendente_com_teto_ja_atingido_e_cancelado_sem_marca_de_plato():
    from app.domain import progression_evaluator as ev
    from app.domain.training_history import PrescribedSpec

    base = replace(fx.track(load=40), pending_load_kg=D("45"), pending_load_since=NOW)
    no_teto = replace(base, max_load_kg=D("40"))
    depois = NOW + timedelta(days=1)
    assert pe.track_pending_load_status(no_teto, D("40"), now=depois) == pe.PENDING_LOAD_CANCELED_BY_CEILING
    # a decisao tambem nao expira: cancelado antes do prazo e depois dele
    muito_depois = NOW + timedelta(days=pe.GROUP_EFFECTIVATION_DAYS + 5)
    assert pe.track_pending_load_status(no_teto, D("40"), now=muito_depois) == pe.PENDING_LOAD_CANCELED_BY_CEILING
    # origem plato: o cancelamento limpa o pendente e NAO grava `plateau_load_applied_at`
    plato = replace(no_teto, pending_load_source=pe.PENDING_LOAD_SOURCE_PLATEAU)
    execucao = fx.execution(
        set_list=fx.sets(reps=[10, 10, 10], rpe=8), prescribed=PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=8)
    )
    novo = ev.update_track_execution(plato, execucao, now=depois, reps_min=10, group=fx.group(phase=pe.F2))
    assert novo.pending_load_kg is None and novo.pending_load_source is None
    assert novo.plateau_load_applied_at is None


def test_incremento_efetivo_do_caminho_next_load_reflete_o_limite():
    grupo = replace(fx.group(phase=pe.F2), pending_action=pe.INCREASE_LOAD, pending_payload={})
    track = replace(fx.track(load=95), max_load_kg=D("97"), load_increment_kg=D("5"))
    d = pe.prescribe(track, grupo, SAFETY, now=NOW)
    assert d.decision == pe.INCREASE_LOAD and d.suggested_load_kg == 97.0
    assert d.evidence["load_increment_kg"] == 2.0
    assert "Carga +2" in d.reason


# ---------------------------------------------------------------------------
# Sugestao do par (GET /exercises/suggested-progression)
# ---------------------------------------------------------------------------


def _motor_sem_banco(monkeypatch, tracks, groups, tetos):
    from app import progression_state as ps
    from app.generation import deterministic_prescription as dp

    monkeypatch.setattr(ps, "load_tracks", lambda conn, user_id: {t.key: t for t in tracks})
    monkeypatch.setattr(ps, "load_all_group_states", lambda conn, user_id: {g.key: g for g in groups})
    monkeypatch.setattr(ps, "load_technique_catalog", lambda conn: {})
    monkeypatch.setattr(ps, "load_primary_groups", lambda conn, ids: ({SUPINO: PEITO}, {SUPINO: 1.0}))
    monkeypatch.setattr(ps, "load_exercise_flags", lambda conn, ids: {SUPINO: True})
    monkeypatch.setattr(ps, "load_laboral_ids", lambda conn, ids: frozenset())
    monkeypatch.setattr(ps, "load_equipment_steps", lambda conn, ids: {BANCO: D("2.5")})
    monkeypatch.setattr(ps, "load_user_flags", lambda conn, user_id: False)
    monkeypatch.setattr(ps, "load_user_load_ceilings", lambda conn, user_id: dict(tetos))
    monkeypatch.setattr(
        dp,
        "resolve_training_safety_bulk",
        lambda conn, pairs: {pair: fx.safety(exercise_id=pair[0], equipment_id=pair[1]) for pair in pairs},
    )


def _trilha_com_aumento_pendente():
    track = replace(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=60, is_compound=True),
        training_goal=H,
        id=1,
        last_executed_at=datetime(2026, 10, 20),
        pending_load_kg=D("62.5"),
        pending_load_since=datetime(2026, 10, 24),
    )
    group = replace(fx.group(muscle_group_id=PEITO, phase=pe.F2, reps=pe.PROGRESSED_REPS), training_goal=H)
    return [track], [group]


def test_sugestao_do_par_com_teto_no_teto_nao_sobe_e_explica(monkeypatch):
    tracks, groups = _trilha_com_aumento_pendente()
    _motor_sem_banco(monkeypatch, tracks, groups, {(SUPINO, BANCO): D("60")})
    com = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[], now=NOW)
    assert com.suggested_load_kg is not None and com.suggested_load_kg <= 60
    assert com.max_load_kg == 60.0
    assert "Carga máxima do equipamento atingida" in svc.progression_note(com)
    assert com.decision["decision"] != pe.INCREASE_LOAD


def test_sugestao_do_par_sem_teto_e_o_resultado_antigo(monkeypatch):
    tracks, groups = _trilha_com_aumento_pendente()
    _motor_sem_banco(monkeypatch, tracks, groups, {})
    sem = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[], now=NOW)
    assert sem.max_load_kg is None
    assert sem.suggested_load_kg == 62.5 and sem.decision["decision"] == pe.INCREASE_LOAD
    assert pe.EVIDENCE_EQUIPMENT_LOAD_CEILING not in (sem.decision.get("evidence") or {})


def test_payload_do_suggested_progression_expoe_max_load_kg(monkeypatch):
    from app import services

    tracks, groups = _trilha_com_aumento_pendente()
    _motor_sem_banco(monkeypatch, tracks, groups, {(SUPINO, BANCO): D("60")})
    com = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[], now=NOW)
    payload = services._suggested_progression_payload(com)
    assert payload["max_load_kg"] == 60.0 and payload["suggested_load_kg"] <= 60.0
    assert "Carga máxima do equipamento atingida" in payload["progression_note"]
    _motor_sem_banco(monkeypatch, tracks, groups, {})
    sem = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[], now=NOW)
    assert services._suggested_progression_payload(sem)["max_load_kg"] is None


def test_calibracao_pela_ultima_execucao_nao_reduz_por_causa_do_teto(monkeypatch):
    # trilha sem decisao de carga: calibra pela ultima execucao (100 kg) com teto 90 -> mantem 100.
    track = replace(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=None, is_compound=True),
        training_goal=H,
    )
    group = replace(fx.group(muscle_group_id=PEITO, phase=pe.F2, reps=pe.PROGRESSED_REPS), training_goal=H)
    _motor_sem_banco(monkeypatch, [track], [group], {(SUPINO, BANCO): D("90")})
    ultima = svc.LastExecution(local_date="2026-10-20", equipment_id=BANCO, last_load_kg=100.0, last_reps=10, working_load_kg=100.0)
    monkeypatch.setattr(svc, "last_execution_from_logs", lambda logs, exercise_id, equipment_id=None: ultima)
    r = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[{"x": 1}], now=NOW)
    if r.load_source == svc.LOAD_SOURCE_LAST_EXECUTION:
        assert r.suggested_load_kg == 100.0


# ---------------------------------------------------------------------------
# Plano congelado: max_load_kg e load_within_ceiling prontos do dono
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("teto", "atual", "esperado"),
    [
        (90, 80, 90.0),  # ancora 100 e AUMENTO sobre 80: sobe so ate o teto
        (90, 100, 100.0),  # ja trabalha em 100: ancora nao e aumento, nao reduz
        (80, 85, 85.0),  # teto abaixo da carga atual: nao reduz (limita a ancora a carga atual)
        (90, None, 100.0),  # sem carga atual conhecida: na duvida, a propria ancora
    ],
)
def test_plano_expoe_teto_e_carga_limitada_so_em_aumento(monkeypatch, teto, atual, esperado):
    from app import services

    monkeypatch.setattr(services, "_safety_by_exercise_row", lambda conn, exercises: {})
    monkeypatch.setattr(services, "_load_steps_by_row", lambda conn, exercises: {})
    par = (D(str(teto)), None if atual is None else D(str(atual)))
    monkeypatch.setattr(services, "_ceilings_by_row", lambda conn, user_id, exercises: {7: par})
    exercicios = [
        {"workout_plan_exercise_id": 7, "load_target": "100kg", "prescription_json": None},
        {"workout_plan_exercise_id": 8, "load_target": "100kg", "prescription_json": None},
    ]
    services._expose_frozen_prescriptions(object(), exercicios, user_id=1)
    com, sem = exercicios
    assert (com["max_load_kg"], com["load_within_ceiling"]) == (float(teto), esperado)
    assert (sem["max_load_kg"], sem["load_within_ceiling"]) == (None, None)


def test_ceilings_by_row_canonicaliza_o_mapping_id_legado(monkeypatch):
    from app import progression_state as ps
    from app import services

    class Cur(NullContext):
        def execute(self, sql, params=None):
            self._sql = sql

        def fetchall(self):
            if "FROM gym_equipment" in self._sql:
                return [{"id": 9, "equipment_id": 33}]
            return [{"old_exercise_id": 50, "canonical_exercise_id": 5}, {"old_exercise_id": 51, "canonical_exercise_id": 5}]

    class Conn:
        def cursor(self):
            return Cur()

    monkeypatch.setattr(elc, "load_user_ceilings", lambda conn, user_id: {(5, 33): D("90")})
    track = replace(fx.track(exercise_id=5, equipment_id=33, load=80), last_executed_at=NOW)
    monkeypatch.setattr(ps, "load_tracks", lambda conn, user_id: {track.key: track})
    linhas = [
        {"workout_plan_exercise_id": i, "mapping_id": m, "preferred_equipment_id": 9}
        for i, m in ((1, 50), (2, 51), (3, 5), (4, 77))
    ]
    resultado = services._ceilings_by_row(Conn(), 1, linhas)
    assert resultado == {1: (D("90"), D("80")), 2: (D("90"), D("80")), 3: (D("90"), D("80"))}


# ---------------------------------------------------------------------------
# P2-1: par canonico x canonico no SQL real (sqlite com o mesmo SQL do modulo)
# ---------------------------------------------------------------------------


class _SqliteCursor:
    def __init__(self, conn):
        self._cur = conn.cursor()

    def execute(self, sql, params=None):
        self._cur.execute(sql.replace("%s", "?"), tuple(params or ()))

    def fetchone(self):
        row = self._cur.fetchone()
        return None if row is None else dict(zip([c[0] for c in self._cur.description], row))


def _banco(mapping_do_ge):
    import sqlite3

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(
        f"""
        CREATE TABLE gym_equipment (id INTEGER, equipment_id INTEGER, mapping_id INTEGER);
        CREATE TABLE exercise_legacy_id_map (old_exercise_id INTEGER, canonical_exercise_id INTEGER);
        CREATE TABLE equipment_exercises (id INTEGER, equipment_id INTEGER, exercise_id INTEGER, is_active INTEGER);
        CREATE TABLE {elc.CEILINGS_TABLE} (user_id INTEGER, equipment_exercise_id INTEGER, max_load_kg REAL);
        INSERT INTO exercise_legacy_id_map VALUES (50, 5), (51, 5);
        INSERT INTO gym_equipment VALUES (9, 33, {mapping_do_ge});
        INSERT INTO equipment_exercises VALUES (7, 33, 5, 1);
        INSERT INTO {elc.CEILINGS_TABLE} VALUES (1, 7, 90.0);
        """
    )
    return _SqliteCursor(conn)


@pytest.mark.parametrize("mapping_do_ge", [5, 51])  # ge.mapping_id = C, ou L2 -> C
@pytest.mark.parametrize("pedido", [50, 5, 51])  # id legado L, canonico C, outro legado L2
def test_par_resolve_com_ids_legado_e_canonico(mapping_do_ge, pedido):
    cur = _banco(mapping_do_ge)
    assert elc.resolve_pair_link(cur, pedido, 9) == 7
    assert elc.pair_ceiling(cur, 1, pedido, 9) == D("90.0")
    assert elc.pair_ceiling(cur, 2, pedido, 9) is None  # outro usuario nao ve


def test_par_de_outro_exercicio_nao_resolve():
    cur = _banco(5)
    assert elc.resolve_pair_link(cur, 99, 9) is None
    assert elc.pair_ceiling(cur, 1, 99, 9) is None


def test_put_com_id_legado_grava_no_vinculo_canonico(monkeypatch):
    cur = _banco(5)

    class Tx(NullContext):
        rowcount = 1

        def cursor(self):
            return self

        def execute(self, sql, params=None):
            if "INSERT INTO" in sql:
                self.inserido = params
                return None
            return cur.execute(sql, params)

        def fetchone(self):
            return cur.fetchone()

    tx = Tx()
    monkeypatch.setattr(elc, "transaction", fake_transaction(tx))
    app = FastAPI()
    app.include_router(elc.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role="regular", source="session")
    r = TestClient(app).put(
        "/me/equipment-load-ceiling", json={"mapping_id": 50, "gym_equipment_id": 9, "max_load_kg": 100}
    )
    assert r.status_code == 200
    assert tx.inserido[:2] == (1, 7)


# ---------------------------------------------------------------------------
# next_set_load: magnitude percentual limitada pelo teto
# ---------------------------------------------------------------------------


def test_regra_percentual_e_limitada_pelo_teto():
    conjunto = nsl.ruleset_from_dict(
        {
            "reps_floor": 6,
            "reps_ceiling": 12,
            "rules": [
                {
                    "set_position": "any",
                    "fatigue": "any",
                    "reps": {"op": ">", "value": "ceiling"},
                    "rpe": None,
                    "respect_planned_ramp": False,
                    "action": "INCREASE_LOAD",
                    "magnitude": {"kind": "percent", "value": 25},
                }
            ],
        }
    )
    args = dict(set_type="working", reps=13, weight_kg=40, rpe=8, step_kg=D("2.5"), ruleset=conjunto)
    sem = nsl.suggest_next_set_load(**args)
    assert sem.direction == nsl.INCREASE_LOAD and sem.load_kg == 50.0
    com = nsl.suggest_next_set_load(**args, max_load_kg=45)
    assert (com.direction, com.load_kg) == (nsl.INCREASE_LOAD, 45.0)
    no_teto = nsl.suggest_next_set_load(**args, max_load_kg=40)
    assert (no_teto.direction, no_teto.load_kg) == (nsl.HOLD, 40.0)


# ---------------------------------------------------------------------------
# Rotas: 401, isolamento A/B, 422
# ---------------------------------------------------------------------------


class _Store(NullContext):
    """Banco em memoria: (user, link) -> kg. Roteiriza por trecho do SQL."""

    def __init__(self):
        self.rows: dict[tuple[int, int], Any] = {}
        self.rowcount = 0
        self._row: dict | None = None

    def execute(self, sql, params=None):
        self.rowcount = 0
        self._row = None
        if "SELECT eqex.id AS link_id" in sql:
            self._row = {"link_id": 7}
        elif "INSERT INTO" in sql:
            user, link, value = params
            self.rows[(user, link)] = value
            self.rowcount = 1
        elif sql.startswith("DELETE FROM"):
            self.rowcount = 1 if self.rows.pop((params[0], params[1]), None) is not None else 0
        elif f"JOIN {elc.CEILINGS_TABLE} c" in sql:
            user = params[0]
            value = self.rows.get((user, 7))
            self._row = None if value is None else {"max_load_kg": value}

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


def _app(monkeypatch, store, user_id):
    monkeypatch.setattr(elc, "transaction", fake_transaction(store))
    app = FastAPI()
    app.include_router(elc.router)
    if user_id is not None:
        app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


BODY = {"mapping_id": 5, "gym_equipment_id": 9, "max_load_kg": 100}
QS = "?mapping_id=5&gym_equipment_id=9"


def test_sem_credencial_as_tres_rotas_respondem_401(monkeypatch):
    client = _app(monkeypatch, _Store(), None)
    assert client.get("/me/equipment-load-ceiling" + QS).status_code == 401
    assert client.put("/me/equipment-load-ceiling", json=BODY).status_code == 401
    assert client.delete("/me/equipment-load-ceiling" + QS).status_code == 401


def test_usuario_a_grava_e_b_nao_ve(monkeypatch):
    store = _Store()
    a = _app(monkeypatch, store, 1)
    b = _app(monkeypatch, store, 2)
    assert a.put("/me/equipment-load-ceiling", json=BODY).status_code == 200
    assert a.get("/me/equipment-load-ceiling" + QS).json()["max_load_kg"] == 100.0
    assert b.get("/me/equipment-load-ceiling" + QS).json()["max_load_kg"] is None
    # B desmarcar nao apaga o de A
    assert b.delete("/me/equipment-load-ceiling" + QS).json()["deleted"] is False
    assert a.get("/me/equipment-load-ceiling" + QS).json()["max_load_kg"] == 100.0


@pytest.mark.parametrize("valor", [0, -1, 1e9, "abc", 9999.99, None])
def test_put_valor_invalido_422(monkeypatch, valor):
    r = _app(monkeypatch, _Store(), 1).put("/me/equipment-load-ceiling", json={**BODY, "max_load_kg": valor})
    assert r.status_code == 422


def test_put_no_maximo_aceito_nao_estoura_a_coluna(monkeypatch):
    store = _Store()
    r = _app(monkeypatch, store, 1).put("/me/equipment-load-ceiling", json={**BODY, "max_load_kg": 9999.98})
    assert r.status_code == 200
    assert store.rows[(1, 7)] <= D("9999.99")


def test_vinculo_confere_o_exercicio_do_equipamento():
    sql_executado: list[str] = []

    class Cur(NullContext):
        def execute(self, sql, params=None):
            sql_executado.append(sql)

        def fetchone(self):
            return None

    assert elc.resolve_pair_link(Cur(), 5, 9) is None
    assert "m2.canonical_exercise_id" in sql_executado[0]  # canonico x canonico
