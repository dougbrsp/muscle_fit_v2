"""Gravação da série com medida (Lote T3 das medidas de série, 2026-10-20).

- `SessionSetIn`/`ExerciseSetIn`: medida ausente = `reps` (o relógio antigo só
  manda reps; nunca deduzir do item da ficha); forma inválida -> 422 com os
  códigos do dono (`set_measures.validate_set_measure`);
- o INSERT grava `measure`/`duration_seconds`; a repetição por `client_key`
  devolve a medida gravada;
- série por tempo/máximo de reps sai com `reps_position` e `next_set_load`
  nulos; o status de esforço pelo RPE continua.

Rodar: cd api && python -m pytest tests/unit/test_workout_sessions_medida.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app import workout_sessions as ws
from app.domain import set_measures as sm
from app.engine_decision_context import next_set_load_for
from app.models import ExerciseSetIn, ExerciseSetUpdateIn
from tests.unit.test_client_key_sessao_idempotente import FakeSessionDB, _identity, _install

SESSION = 10


@pytest.fixture
def db(monkeypatch):
    return _install(monkeypatch, FakeSessionDB())


def _add(exercise_id, **kw):
    return ws.add_session_set(SESSION, exercise_id, ws.SessionSetIn(**kw), identity=_identity())


# ---------------------------------------------------------------------------
# Validação (422)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kw, code",
    [
        ({"measure": "duration", "duration_seconds": 58, "reps": 10}, sm.REPS_NOT_ALLOWED),
        ({"measure": "duration"}, sm.DURATION_REQUIRED),
        ({"measure": "duration", "duration_seconds": 0}, sm.DURATION_OUT_OF_RANGE),
        ({"measure": "max_reps"}, sm.REPS_REQUIRED),
        ({"measure": "max_reps", "reps": 0}, sm.REPS_BELOW_MIN),
        ({"measure": "max_reps", "reps": 15, "duration_seconds": 2}, sm.TIME_WINDOW_OUT_OF_RANGE),
        ({"measure": "reps", "reps": 10, "duration_seconds": 30}, sm.DURATION_NOT_ALLOWED),
        ({"measure": "segundos", "reps": 10}, sm.MEASURE_UNKNOWN),
    ],
)
def test_forma_invalida_vira_422_nos_dois_modelos(kw, code):
    for model, extra in ((ws.SessionSetIn, {}), (ExerciseSetIn, {"set_number": 1})):
        with pytest.raises(ValidationError) as err:
            model(**extra, **kw)
        assert code in str(err.value)


def test_medida_ausente_e_reps_e_formas_validas_passam():
    assert ws.SessionSetIn(reps=10).measure is None
    assert ExerciseSetIn(set_number=1, reps=10).measure is None
    ws.SessionSetIn(measure="duration", duration_seconds=60)
    ws.SessionSetIn(measure="max_reps", reps=15, duration_seconds=30)
    ExerciseSetIn(set_number=1, measure="max_reps", reps=15)


def test_duracao_solta_sem_medida_e_ignorada_como_antes():
    # Cliente antigo (relógio/importador) que mande `duration_seconds` sem medida
    # não ganha 422 (a fila offline travaria): grava reps, sem duração.
    assert ws.SessionSetIn(reps=10, duration_seconds=30).duration_seconds is None
    assert ExerciseSetIn(set_number=1, reps=10, duration_seconds=30).duration_seconds is None


def test_edicao_valida_so_depois_da_mescla():
    # Payload parcial da edição: a medida omitida vem da série gravada.
    assert ExerciseSetUpdateIn(set_number=1, reps=10, duration_seconds=30).duration_seconds == 30


# ---------------------------------------------------------------------------
# Gravação e resposta
# ---------------------------------------------------------------------------


def test_serie_por_tempo_grava_colunas_e_sai_sem_posicao_nem_proxima_carga(db):
    ex = db.add_exercise()
    out = _add(ex, measure="duration", duration_seconds=58, rpe=Decimal(8))
    gravada = db.sets[-1]
    assert gravada["measure"] == "duration" and gravada["duration_seconds"] == 58 and gravada["reps"] is None
    assert out["reps_position"] is None and out["next_set_load"] is None
    # Status de esforço pelo RPE continua (alvo 9 do fake, RPE 8).
    assert out["intensity_status"] is not None


def test_serie_de_maximo_de_reps_sem_posicao_nem_proxima_carga(db):
    ex = db.add_exercise()
    out = _add(ex, measure="max_reps", reps=17, weight_kg=Decimal(40), rpe=Decimal(8), duration_seconds=30)
    gravada = db.sets[-1]
    assert (gravada["measure"], gravada["reps"], gravada["duration_seconds"]) == ("max_reps", 17, 30)
    assert out["reps_position"] is None and out["next_set_load"] is None


def test_serie_sem_medida_grava_reps_e_mantem_a_regra(db):
    ex = db.add_exercise()
    out = _add(ex, reps=17, weight_kg=Decimal(40), rpe=Decimal(8))
    gravada = db.sets[-1]
    assert gravada["measure"] == sm.DEFAULT_MEASURE.value and gravada["duration_seconds"] is None
    # Mesma resposta de antes do T3 (17 reps na faixa 10-12 -> sobe 1 passo).
    assert out["reps_position"] is not None and out["next_set_load"]["load_kg"] == 42.5


def test_repeticao_por_chave_usa_a_medida_gravada(db):
    ex = db.add_exercise()
    primeira = _add(ex, measure="duration", duration_seconds=58, client_key="set-t")
    segunda = _add(ex, measure="duration", duration_seconds=58, client_key="set-t")
    assert segunda["duplicate"] is True and segunda["next_set_load"] is None and segunda["reps_position"] is None
    assert {k: v for k, v in segunda.items() if k != "duplicate"} == {k: v for k, v in primeira.items() if k != "duplicate"}
    assert len(db.sets) == 1


def test_select_da_chave_traz_as_colunas_da_medida(db):
    ex = db.add_exercise()
    _add(ex, measure="duration", duration_seconds=58, client_key="set-t")
    _add(ex, measure="duration", duration_seconds=58, client_key="set-t")
    leitura = next(sql for sql in db.sql if "client_key = %s" in sql and "FROM exercise_sets" in sql)
    assert "measure, duration_seconds" in leitura


def test_dono_da_proxima_carga_e_neutro_fora_de_reps():
    from app.domain.next_set_load import DEFAULT_RULESET, SetFacts, evaluate_ruleset

    fatos = SetFacts(reps=17, weight_kg=Decimal(40), rpe=Decimal(8), step_kg=Decimal("2.5"))
    assert evaluate_ruleset(fatos, DEFAULT_RULESET) is not None
    for medida in ("duration", "max_reps"):
        assert evaluate_ruleset(SetFacts(**{**fatos.__dict__, "measure": medida}), DEFAULT_RULESET) is None


def test_avaliacao_por_ia_aceita_a_medida():
    from app.models import SetEvaluationIn

    assert SetEvaluationIn(exercise_name="Prancha", measure="duration").measure == "duration"


def test_proxima_carga_do_contexto_e_neutra_fora_de_reps():
    valores = {"set_type": "working", "reps": 17, "weight_kg": Decimal(40), "rpe": Decimal(8)}
    assert next_set_load_for(valores, None) is not None
    assert next_set_load_for({**valores, "measure": "max_reps"}, None) is None
    assert next_set_load_for({**valores, "measure": "reps"}, None) is not None
