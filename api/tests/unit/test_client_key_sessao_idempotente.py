"""Idempotência por `client_key` na sessão ao vivo (Evolução ZEPP, PR C,
migração 087).

- repetir a chave devolve o MESMO registro com `duplicate: true`, sem gravar
  nada (INSERT, agregados, `log_event`);
- a série repetida devolve os mesmos campos derivados (`next_set_load`,
  status, teto) que a original, lidos do motor com as entradas gravadas;
- chaves diferentes criam; a mesma chave em pais diferentes cria;
- sem chave o comportamento e o SQL são os de antes (a coluna da 087 nem é
  citada);
- corrida: o INSERT bate na UNIQUE, a leitura travante acha o registro do
  outro envio e o devolve; outra violação (set_number) continua subindo;
- validação do campo (tamanho e charset).

Rodar: cd api && python -m pytest tests/unit/test_client_key_sessao_idempotente.py
"""

from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import migrate as m
from app import workout_sessions as ws
from app.domain import effective_intensity as ei
from app.domain.training_goal import SQL_PLAN_EXERCISE_GOAL
from app.engine_decision_context import PairEngineContext
from tests.support.fake_db import NullContext, fake_transaction

USER = 1
SESSION = 10
OUTRA_SESSAO = 11
PASSO = Decimal("2.5")
MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "087_client_keys_sessao_ao_vivo.py"


class _Duplicate(Exception):
    """Mesma mensagem do MySQL que `is_duplicate_key_error` reconhece."""


def _row(sql: str, params: tuple) -> dict:
    """Colunas do INSERT -> valores (`%s` consome o próximo parâmetro; literal fica como está).
    O carimbo do objetivo de treino (`training_goal.SQL_PLAN_EXERCISE_GOAL`, subconsulta com dois
    parâmetros) vira o default: este banco falso não tem ficha de origem."""
    sql = sql.replace(" ".join(SQL_PLAN_EXERCISE_GOAL.split()), "%goal")
    cols_txt, vals_txt = re.search(r"\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", sql).groups()
    queue = list(params)

    def _value(v: str):
        if v.strip() == "%goal":
            queue.pop(0)
            return queue.pop(0)
        return queue.pop(0) if v.strip() == "%s" else int(v)

    values = [_value(v) for v in vals_txt.split(",")]
    assert not queue
    return {"client_key": None, **dict(zip([c.strip() for c in cols_txt.split(",")], values))}


class FakeSessionDB(NullContext):
    """Banco mínimo das rotas de sessão: activities, exercises, exercise_sets
    com as UNIQUEs da 001 (`exercise_id, set_number`) e da 087 (chave por pai).

    `hidden`: linhas confirmadas por OUTRA transação depois do nosso snapshot
    (só a leitura travante `FOR UPDATE` e a UNIQUE as enxergam)."""

    def __init__(self, *, status="in_progress"):
        self.sessions = {
            SESSION: {"id": SESSION, "gym_id": 3, "session_status": status, "started_at": _dt.datetime(2026, 10, 18, 7)},
            OUTRA_SESSAO: {"id": OUTRA_SESSAO, "gym_id": 3, "session_status": "in_progress", "started_at": None},
        }
        self.exercises: list[dict] = []
        self.sets: list[dict] = []
        self.hidden_exercises: list[dict] = []
        self.hidden_sets: list[dict] = []
        self.sql: list[str] = []
        self.inserts: list[str] = []
        self._result = None
        self.lastrowid = None
        self._next_id = 100

    # -- cursor -------------------------------------------------------------
    def execute(self, sql, params=None):
        self.sql.append(sql)
        flat = " ".join(sql.split())
        p = tuple(params or ())
        locking = flat.endswith("FOR UPDATE")
        if flat.startswith("SELECT id, gym_id, session_status, started_at FROM activities"):
            row = self.sessions.get(p[0]) if p[1] == USER else None
            self._result = dict(row) if row else None
        elif "COALESCE(MAX(sequence), 0) + 1" in flat:
            self._result = {"seq": 1 + max((e["sequence"] for e in self.exercises if e["activity_id"] == p[0]), default=0)}
        elif flat.startswith("SELECT id, sequence, mapping_id, equipment_id, workout_plan_exercise_id FROM exercises"):
            pool = self.exercises + (self.hidden_exercises if locking else [])
            self._result = next((dict(e) for e in pool if e["activity_id"] == p[0] and e["client_key"] == p[1]), None)
        elif "FROM exercises e LEFT JOIN workout_plan_exercises" in flat:
            ex = next((e for e in self.exercises if e["id"] == p[0] and e["activity_id"] == p[1]), None)
            self._result = (
                {"id": ex["id"], "plan_technique": None, "plan_reps_target": "10-12", "prescription_json": None,
                 "plan_rpe_target": None, "mapping_id": 11, "gym_equipment_id": 2}
                if ex else None
            )
        elif flat.startswith("SELECT set_number, set_type, reps, weight_kg, rpe, muscle_pain"):
            pool = self.sets + (self.hidden_sets if locking else [])
            self._result = next((dict(s) for s in pool if s["exercise_id"] == p[0] and s["client_key"] == p[1]), None)
        elif "COALESCE(MAX(set_number), 0) + 1" in flat:
            self._result = {"n": 1 + max((s["set_number"] for s in self.sets if s["exercise_id"] == p[0]), default=0)}
        elif flat.startswith("INSERT INTO exercises "):
            self.inserts.append(flat)
            row = _row(flat, p)
            for other in self.exercises + self.hidden_exercises:
                if row["client_key"] is not None and (other["activity_id"], other["client_key"]) == (
                    row["activity_id"], row["client_key"]
                ):
                    raise _Duplicate("(1062, \"Duplicate entry for key 'exercises.uq_exercises_activity_client_key'\")")
            self._next_id += 1
            row["id"] = self._next_id
            self.exercises.append(row)
            self.lastrowid = row["id"]
        elif flat.startswith("INSERT INTO exercise_sets "):
            self.inserts.append(flat)
            row = _row(flat, p)
            for other in self.sets + self.hidden_sets:
                if other["exercise_id"] != row["exercise_id"]:
                    continue
                if other["set_number"] == row["set_number"]:
                    raise _Duplicate("(1062, \"Duplicate entry for key 'exercise_sets.uq_exercise_set_number'\")")
                if row["client_key"] is not None and other["client_key"] == row["client_key"]:
                    raise _Duplicate("(1062, \"Duplicate entry for key 'exercise_sets.uq_exercise_sets_exercise_client_key'\")")
            self.sets.append(row)
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return []

    # -- montagem -----------------------------------------------------------
    def add_exercise(self, activity_id=SESSION, client_key=None) -> int:
        self._next_id += 1
        self.exercises.append(
            {"id": self._next_id, "activity_id": activity_id, "sequence": len(self.exercises) + 1, "mapping_id": 11,
             "equipment_id": 2, "workout_plan_exercise_id": None, "client_key": client_key}
        )
        return self._next_id


@pytest.fixture
def db(monkeypatch):
    return _install(monkeypatch, FakeSessionDB())


def _install(monkeypatch, fake):
    calls = {"aggregates": 0, "avg_rpe": 0, "log_event": 0}

    def _count(name):
        def _fn(*_a, **_k):
            calls[name] += 1

        return _fn

    monkeypatch.setattr(ws, "transaction", fake_transaction(fake))
    monkeypatch.setattr(ws, "ensure_valid_codes", lambda codes: None)
    monkeypatch.setattr(ws, "resolve_mapping_id", lambda name, _a, mapping_id, cur=None: mapping_id or 11)
    monkeypatch.setattr(ws, "resolve_equipment_id", lambda cur, mapping_id, gym_id, eq_id, eq_name, user_id=None: 2)
    monkeypatch.setattr(ws, "_refresh_exercise_aggregates", _count("aggregates"))
    monkeypatch.setattr(ws, "_refresh_activity_avg_rpe", _count("avg_rpe"))
    monkeypatch.setattr(ws, "log_event", _count("log_event"))
    monkeypatch.setattr(ws, "load_pair_step_context", lambda conn, mapping_id, gym_equipment_id: PairEngineContext(step_kg=PASSO))
    # Um clamp em toda série: prova que o `log_event` não repete na 2ª vez.
    monkeypatch.setattr(
        ws.effective_intensity,
        "resolve_for_logged_exercise",
        lambda conn, eid: ei.EffectiveIntensity(
            rpe_target_effective=9, ceiling=10, failure_allowed=True,
            clamps=({"field": "rpe_target", "from": 10, "to": 9, "reason": "teste"},),
        ),
    )
    fake.calls = calls
    return fake


def _identity():
    return SimpleNamespace(user_id=USER)


def _add_exercise(session_id=SESSION, **kw):
    return ws.add_session_exercise(session_id, ws.SessionExerciseIn(original_name="Supino reto", **kw), identity=_identity())


def _add_set(exercise_id, session_id=SESSION, **kw):
    base = {"reps": 17, "weight_kg": Decimal(40), "rpe": Decimal(8)}
    return ws.add_session_set(session_id, exercise_id, ws.SessionSetIn(**{**base, **kw}), identity=_identity())


# ---------------------------------------------------------------------------
# Exercício
# ---------------------------------------------------------------------------


def test_exercicio_repetido_devolve_o_mesmo_id_com_duplicate(db):
    primeiro = _add_exercise(client_key="ex-1")
    segundo = _add_exercise(client_key="ex-1")
    assert primeiro["duplicate"] is False and segundo["duplicate"] is True
    assert segundo["exercise_id"] == primeiro["exercise_id"]
    assert {k: v for k, v in segundo.items() if k != "duplicate"} == {k: v for k, v in primeiro.items() if k != "duplicate"}
    assert len(db.exercises) == 1 and len(db.inserts) == 1


def test_exercicio_chaves_diferentes_e_pais_diferentes_criam(db):
    a = _add_exercise(client_key="ex-1")
    b = _add_exercise(client_key="ex-2")
    c = _add_exercise(session_id=OUTRA_SESSAO, client_key="ex-1")
    assert len({a["exercise_id"], b["exercise_id"], c["exercise_id"]}) == 3
    assert not any(r["duplicate"] for r in (a, b, c))


def test_exercicio_sem_chave_igual_a_hoje(db):
    a = _add_exercise()
    b = _add_exercise()
    assert a["exercise_id"] != b["exercise_id"] and a["client_key"] is None and a["duplicate"] is False
    # Sem chave: o INSERT nem cita a coluna da 087 e não há leitura por chave.
    assert all("client_key" not in sql for sql in db.inserts)
    assert not any("client_key = %s" in sql for sql in db.sql)


def test_exercicio_corrida_na_unique_devolve_o_do_outro_envio(db):
    # O outro envio confirmou depois do nosso snapshot: a 1ª leitura não o vê.
    db.hidden_exercises.append(
        {"id": 555, "activity_id": SESSION, "sequence": 1, "mapping_id": 11, "equipment_id": 2,
         "workout_plan_exercise_id": None, "client_key": "ex-1"}
    )
    out = _add_exercise(client_key="ex-1")
    assert out["exercise_id"] == 555 and out["duplicate"] is True
    assert any(sql.rstrip().endswith("FOR UPDATE") for sql in db.sql)
    assert db.exercises == []


def test_exercicio_em_treino_finalizado(monkeypatch):
    fake = _install(monkeypatch, FakeSessionDB(status="done"))
    existente = fake.add_exercise(client_key="ex-1")
    # Repetição de algo já gravado: devolve, mesmo com o treino encerrado.
    assert _add_exercise(client_key="ex-1")["exercise_id"] == existente
    # Lançamento novo (com ou sem chave): 409 como antes.
    for kw in ({"client_key": "ex-2"}, {}):
        with pytest.raises(HTTPException) as err:
            _add_exercise(**kw)
        assert err.value.status_code == 409


def test_sessao_de_outro_usuario_404_mesmo_com_chave(db):
    with pytest.raises(HTTPException) as err:
        ws.add_session_exercise(SESSION, ws.SessionExerciseIn(original_name="x", client_key="ex-1"),
                                identity=SimpleNamespace(user_id=2))
    assert err.value.status_code == 404


# ---------------------------------------------------------------------------
# Série
# ---------------------------------------------------------------------------


def test_serie_repetida_devolve_a_mesma_resposta_sem_efeitos(db):
    ex = db.add_exercise()
    primeira = _add_set(ex, client_key="set-1")
    efeitos = dict(db.calls)
    segunda = _add_set(ex, client_key="set-1")
    assert primeira["duplicate"] is False and segunda["duplicate"] is True
    assert {k: v for k, v in segunda.items() if k != "duplicate"} == {k: v for k, v in primeira.items() if k != "duplicate"}
    # A resposta repetida traz a carga da próxima série do motor (17 reps -> subir).
    assert segunda["next_set_load"] == primeira["next_set_load"] and segunda["next_set_load"]["load_kg"] == 42.5
    assert len(db.sets) == 1 and len(db.inserts) == 1
    assert db.calls == efeitos == {"aggregates": 1, "avg_rpe": 1, "log_event": 1}


def test_serie_repetida_usa_o_gravado_e_nao_o_payload_novo(db):
    ex = db.add_exercise()
    primeira = _add_set(ex, client_key="set-1")
    # O reenvio traz outro corpo (cliente bugado): vale o que foi gravado.
    segunda = _add_set(ex, client_key="set-1", reps=4, weight_kg=Decimal(80), rpe=Decimal(10))
    assert segunda["duplicate"] is True and segunda["next_set_load"] == primeira["next_set_load"]
    assert len(db.sets) == 1


def test_serie_chaves_diferentes_e_exercicios_diferentes_criam(db):
    ex1, ex2 = db.add_exercise(), db.add_exercise()
    a = _add_set(ex1, client_key="set-1")
    b = _add_set(ex1, client_key="set-2")
    c = _add_set(ex2, client_key="set-1")
    assert [a["set_number"], b["set_number"], c["set_number"]] == [1, 2, 1]
    assert not any(r["duplicate"] for r in (a, b, c)) and len(db.sets) == 3


def test_serie_sem_chave_igual_a_hoje(db):
    ex = db.add_exercise()
    a = _add_set(ex)
    b = _add_set(ex)
    assert (a["set_number"], b["set_number"]) == (1, 2) and a["client_key"] is None and a["duplicate"] is False
    assert all("client_key" not in sql for sql in db.inserts)
    assert not any("client_key = %s" in sql for sql in db.sql)
    assert db.calls == {"aggregates": 2, "avg_rpe": 2, "log_event": 2}


def test_serie_corrida_na_unique_devolve_a_do_outro_envio(db):
    ex = db.add_exercise()
    db.hidden_sets.append(
        {"exercise_id": ex, "set_number": 1, "set_type": "working", "reps": 17, "weight_kg": Decimal(40),
         "rpe": Decimal(8), "muscle_pain": None, "joint_pain": None, "rpe_target_effective": 9,
         "safety_event": None, "technique_code": None, "anchor_set_number": None, "client_key": "set-1"}
    )
    out = _add_set(ex, client_key="set-1", set_number=2)
    assert out["duplicate"] is True and out["set_number"] == 1
    assert out["next_set_load"]["load_kg"] == 42.5
    assert db.sets == [] and db.calls == {"aggregates": 0, "avg_rpe": 0, "log_event": 0}


def test_serie_outra_violacao_unique_continua_subindo(db):
    ex = db.add_exercise()
    _add_set(ex, client_key="set-1", set_number=1)
    with pytest.raises(_Duplicate, match="uq_exercise_set_number"):
        _add_set(ex, client_key="set-2", set_number=1)


def test_serie_em_treino_finalizado(monkeypatch):
    fake = _install(monkeypatch, FakeSessionDB())
    ex = fake.add_exercise()
    primeira = _add_set(ex, client_key="set-1")
    fake.sessions[SESSION]["session_status"] = "done"
    assert _add_set(ex, client_key="set-1")["set_number"] == primeira["set_number"]
    for kw in ({"client_key": "set-2"}, {}):
        with pytest.raises(HTTPException) as err:
            _add_set(ex, **kw)
        assert err.value.status_code == 409


def test_serie_em_exercicio_de_outra_sessao_404(db):
    ex = db.add_exercise(activity_id=OUTRA_SESSAO)
    with pytest.raises(HTTPException) as err:
        _add_set(ex, client_key="set-1")
    assert err.value.status_code == 404


# ---------------------------------------------------------------------------
# Validação do campo e paridade com a 087
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modelo", [ws.SessionExerciseIn, ws.SessionSetIn])
@pytest.mark.parametrize("chave", ["", "a" * (ws.CLIENT_KEY_MAX_LENGTH + 1), "com espaço", "é", "a/b", "x'y", "a;b"])
def test_chave_invalida_e_recusada(modelo, chave):
    extra = {"original_name": "x"} if modelo is ws.SessionExerciseIn else {}
    with pytest.raises(ValidationError):
        modelo(client_key=chave, **extra)


@pytest.mark.parametrize("modelo", [ws.SessionExerciseIn, ws.SessionSetIn])
@pytest.mark.parametrize("chave", ["a" * ws.CLIENT_KEY_MAX_LENGTH, "zepp:1739.4-ab_CD", "0f8c2e1a-9d3b-4c55-8a0e-2b7f6c1d9e04"])
def test_chave_valida_e_aceita(modelo, chave):
    extra = {"original_name": "x"} if modelo is ws.SessionExerciseIn else {}
    assert modelo(client_key=chave, **extra).client_key == chave
    assert modelo(**extra).client_key is None


def test_tamanho_da_chave_igual_ao_da_coluna_da_087():
    modulo = m.load_python_migration(MIGRATION).__globals__
    assert modulo["CLIENT_KEY_MAX_LENGTH"] == ws.CLIENT_KEY_MAX_LENGTH
    # A UNIQUE é por pai real: sessão para o exercício, exercício para a série.
    assert modulo["TARGETS"]["exercises"][1] == ("activity_id", "client_key")
    assert modulo["TARGETS"]["exercise_sets"][1] == ("exercise_id", "client_key")
