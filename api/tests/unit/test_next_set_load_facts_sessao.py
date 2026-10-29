"""Os FATOS que os chamadores passam ao conjunto de regras (R1, 2026-10-20):
índice da série entre as `working` do exercício, faixa de fadiga prévia, teto
efetivo e conjunto — no salvamento da série (`workout_sessions._evaluate_set`)
e na IA por série (`workout_ai_generation_core.dispatch_evaluate_set`).
Nenhuma regra aqui: só o encanamento até `next_set_load_for`.

R2 (2026-10-20): o conjunto vem do loader (`next_set_load_rules`, publicado
ou o padrão) e a faixa de fadiga SÓ é lida quando alguma regra dele a exige
(`next_set_load.ruleset_uses_fatigue`): com o padrão, nenhuma leitura.

Rodar: cd api && python -m pytest tests/unit/test_next_set_load_facts_sessao.py
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app import workout_ai_generation_core as core
from app import workout_sessions
from app.domain import effective_intensity as ei
from app.domain import next_set_load as nsl
from app.domain import progression_engine as pe
from app.domain.prior_fatigue import PriorFatigue, PriorFatigueBand
from app.engine_decision_context import PairEngineContext
from app.models import SetEvaluationIn
from tests.support.fake_db import NullContext, SharedCursorConn

PASSO = Decimal("2.5")
USER, SESSION, EXERCISE = 1, 10, 5
INTENSIDADE = ei.EffectiveIntensity(rpe_target_effective=9, ceiling=9, failure_allowed=False)
LINHA = {"id": EXERCISE, "plan_reps_target": "10-12", "prescription_json": None, "plan_rpe_target": None, "mapping_id": 11, "gym_equipment_id": 2}


# Conjunto publicado de mentira que EXIGE fadiga (a 1ª regra do padrão só para
# músculo descansado), versão 3.
CONJUNTO_COM_FADIGA = replace(
    nsl.DEFAULT_RULESET,
    rules=(replace(nsl.DEFAULT_RULESET.rules[0], fatigue=nsl.FatigueCondition.RESTED),) + nsl.DEFAULT_RULESET.rules[1:],
    version=3,
    source=nsl.RULESET_SOURCE_PUBLISHED,
)


def _prior(band):
    return PriorFatigue(muscle_group_id=1, muscle_group_name="Peito", prior_sets=2.0, level=0.2, band=band)


class _Cursor(NullContext):
    """Devolve `{"n": anteriores}` para o COUNT das séries de trabalho."""

    def __init__(self, anteriores):
        self.anteriores = anteriores
        self.executados: list[str] = []

    def execute(self, sql, params=None):
        self.executados.append(sql)

    def fetchone(self):
        return {"n": self.anteriores} if self.executados and "COUNT(*)" in self.executados[-1] else dict(LINHA)

    def fetchall(self):
        return []


def _preparar(monkeypatch, *, anteriores=2, fadiga=None, falha=None, conjunto=CONJUNTO_COM_FADIGA):
    cursor = _Cursor(anteriores)
    conn = SharedCursorConn(cursor)
    recebido: dict = {}
    leituras: list = []
    conjuntos_lidos: list = []

    def fake_loader(conn_):
        conjuntos_lidos.append(conn_)
        if isinstance(conjunto, Exception):
            raise conjunto
        return conjunto

    def fake_next_set_load_for(values, context, **kwargs):
        recebido.update(kwargs)
        recebido["values"] = dict(values)
        return None

    def fake_reader(conn_, user_id, activity_ids, **kwargs):
        leituras.append((conn_, user_id, list(activity_ids)))
        if falha is not None:
            raise falha
        return fadiga or {}

    monkeypatch.setattr(workout_sessions, "next_set_load_for", fake_next_set_load_for)
    monkeypatch.setattr(workout_sessions, "load_session_prior_fatigue", fake_reader)
    monkeypatch.setattr(workout_sessions, "load_pair_step_context", lambda c, m, g: PairEngineContext(step_kg=PASSO))
    monkeypatch.setattr(workout_sessions, "load_published_ruleset", fake_loader)
    recebido["_conjuntos_lidos"] = conjuntos_lidos
    return conn, cursor, recebido, leituras


def _avaliar(conn, cursor, **kwargs):
    base = dict(
        user_id=USER,
        session_id=SESSION,
        set_type="working",
        set_number=4,
        reps=10,
        weight_kg=Decimal(20),
        rpe=Decimal(8),
        joint_pain=None,
        muscle_pain=None,
    )
    return workout_sessions._evaluate_set(conn, cursor, EXERCISE, dict(LINHA), INTENSIDADE, **{**base, **kwargs})


# ---------------------------------------------------------------------------
# Salvamento da série
# ---------------------------------------------------------------------------


def test_salvamento_passa_indice_faixa_teto_e_conjunto_publicado(monkeypatch):
    conn, cursor, recebido, leituras = _preparar(
        monkeypatch, anteriores=2, fadiga={EXERCISE: _prior(PriorFatigueBand.PRE_FATIGUED)}
    )
    _avaliar(conn, cursor)
    assert recebido["working_set_index"] == 3  # 2 séries de trabalho antes -> esta é a 3ª
    assert recebido["fatigue_band"] is PriorFatigueBand.PRE_FATIGUED
    assert recebido["rpe_ceiling"] == INTENSIDADE.ceiling
    assert recebido["ruleset"] is CONJUNTO_COM_FADIGA
    assert recebido["planned_rpe"] is None
    assert recebido["_conjuntos_lidos"] == [conn]  # o conjunto na conexão da série
    assert leituras == [(conn, USER, [SESSION])]
    assert sum("COUNT(*)" in sql for sql in cursor.executados) == 1


def test_conjunto_padrao_nao_le_a_fadiga(monkeypatch):
    """O padrão não tem regra de fadiga: a leitura da fadiga prévia (3 SELECTs
    por série no R1) sai do caminho."""
    assert not nsl.ruleset_uses_fatigue(nsl.DEFAULT_RULESET)
    conn, cursor, recebido, leituras = _preparar(
        monkeypatch, fadiga={EXERCISE: _prior(PriorFatigueBand.RESTED)}, conjunto=nsl.DEFAULT_RULESET
    )
    _avaliar(conn, cursor)
    assert leituras == []
    assert recebido["fatigue_band"] is None and recebido["working_set_index"] == 3
    assert recebido["ruleset"] is nsl.DEFAULT_RULESET


def test_falha_do_loader_vira_conjunto_padrao(monkeypatch):
    conn, cursor, recebido, leituras = _preparar(monkeypatch, conjunto=RuntimeError("tabela ausente"))
    _avaliar(conn, cursor)
    assert recebido["ruleset"] is nsl.DEFAULT_RULESET and leituras == []


def test_salvamento_conta_as_series_de_trabalho_mesmo_sem_rampa(monkeypatch):
    conn, cursor, recebido, _ = _preparar(monkeypatch, anteriores=0)
    _avaliar(conn, cursor)
    assert recebido["working_set_index"] == 1 and recebido["planned_rpe"] is None
    assert any("COUNT(*)" in sql for sql in cursor.executados)


def test_salvamento_le_a_rampa_e_o_indice_da_mesma_contagem(monkeypatch):
    conn, cursor, recebido, _ = _preparar(monkeypatch, anteriores=1)
    linha = {**LINHA, "prescription_json": {"rpe_target": "8-9-10"}}
    workout_sessions._evaluate_set(
        conn, cursor, EXERCISE, linha, INTENSIDADE,
        user_id=USER, session_id=SESSION, set_type="working", set_number=3,
        reps=12, weight_kg=Decimal(20), rpe=Decimal(8), joint_pain=None, muscle_pain=None,
    )
    assert recebido["working_set_index"] == 2 and recebido["planned_rpe"] == Decimal(9)


def test_falha_na_leitura_da_fadiga_vira_faixa_desconhecida(monkeypatch):
    conn, cursor, recebido, leituras = _preparar(monkeypatch, falha=RuntimeError("banco fora"))
    _avaliar(conn, cursor)
    assert recebido["fatigue_band"] is None and recebido["working_set_index"] == 3
    assert len(leituras) == 1


def test_exercicio_fora_da_leitura_vira_faixa_desconhecida(monkeypatch):
    conn, cursor, recebido, _ = _preparar(monkeypatch, fadiga={999: _prior(PriorFatigueBand.RESTED)})
    _avaliar(conn, cursor)
    assert recebido["fatigue_band"] is None


def test_serie_que_nao_e_de_trabalho_nao_le_nada(monkeypatch):
    conn, cursor, recebido, leituras = _preparar(monkeypatch, fadiga={EXERCISE: _prior(PriorFatigueBand.RESTED)})
    _avaliar(conn, cursor, set_type="drop_set")
    assert recebido["working_set_index"] is None and recebido["fatigue_band"] is None
    assert leituras == [] and not any("COUNT(*)" in sql for sql in cursor.executados)
    assert recebido["_conjuntos_lidos"] == [] and recebido["ruleset"] is nsl.DEFAULT_RULESET


def test_salvamento_de_verdade_devolve_a_sugestao_com_os_campos_novos(monkeypatch):
    """Sem dublê do dono: a faixa lida entra no `NextSetLoad` do padrão (todas
    as regras são "qualquer fadiga": o resultado é o de sempre, com os campos
    aditivos)."""
    cursor = _Cursor(0)
    conn = SharedCursorConn(cursor)
    monkeypatch.setattr(workout_sessions, "load_session_prior_fatigue", lambda c, u, ids: {EXERCISE: _prior(PriorFatigueBand.RESTED)})
    monkeypatch.setattr(workout_sessions, "load_pair_step_context", lambda c, m, g: PairEngineContext(step_kg=PASSO))
    # Loader de verdade sobre o cursor falso: nenhuma linha publicada -> padrão (v0).
    _, proxima = _avaliar(conn, cursor, reps=16)
    assert (proxima.direction, proxima.load_kg, proxima.rule_position, proxima.ruleset_version) == (pe.INCREASE_LOAD, 22.5, 1, 0)
    assert proxima.to_dict()["magnitude"] == {"kind": "steps", "value": 1}
    assert any("current_published = 1" in sql for sql in cursor.executados)


def test_salvamento_de_verdade_com_conjunto_publicado_devolve_a_versao(monkeypatch):
    cursor = _Cursor(0)
    conn = SharedCursorConn(cursor)
    monkeypatch.setattr(workout_sessions, "load_session_prior_fatigue", lambda c, u, ids: {EXERCISE: _prior(PriorFatigueBand.RESTED)})
    monkeypatch.setattr(workout_sessions, "load_pair_step_context", lambda c, m, g: PairEngineContext(step_kg=PASSO))
    monkeypatch.setattr(workout_sessions, "load_published_ruleset", lambda c: CONJUNTO_COM_FADIGA)
    _, proxima = _avaliar(conn, cursor, reps=16)
    assert (proxima.direction, proxima.rule_position, proxima.ruleset_version) == (pe.INCREASE_LOAD, 1, 3)


# ---------------------------------------------------------------------------
# IA por série
# ---------------------------------------------------------------------------


class _Transacao:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def _preparar_ia(monkeypatch, *, fadiga=None, falha=None):
    leituras: list = []
    conn = object()

    def fake_reader(conn_, user_id, activity_ids, **kwargs):
        leituras.append((conn_, user_id, list(activity_ids)))
        if falha is not None:
            raise falha
        return fadiga or {}

    monkeypatch.setattr(core, "transaction", lambda: _Transacao(conn))
    monkeypatch.setattr(core, "load_session_prior_fatigue", fake_reader)
    return conn, leituras


@pytest.mark.parametrize(
    "payload",
    [{}, {"session_id": SESSION}, {"exercise_id": EXERCISE}, {"session_id": None, "exercise_id": EXERCISE}],
)
def test_ia_sem_sessao_ou_exercicio_nao_le_e_fica_sem_faixa(monkeypatch, payload):
    _, leituras = _preparar_ia(monkeypatch, fadiga={EXERCISE: _prior(PriorFatigueBand.RESTED)})
    assert core._evaluate_set_fatigue_band(payload, USER) is None
    assert leituras == []


def test_ia_le_a_faixa_com_ownership_pelo_mesmo_leitor(monkeypatch):
    conn, leituras = _preparar_ia(monkeypatch, fadiga={EXERCISE: _prior(PriorFatigueBand.HEAVILY_FATIGUED)})
    assert core._evaluate_set_fatigue_band({"session_id": SESSION, "exercise_id": EXERCISE}, USER) is PriorFatigueBand.HEAVILY_FATIGUED
    assert leituras == [(conn, USER, [SESSION])]
    # exercício de outra sessão/usuário não aparece na leitura -> sem faixa
    assert core._evaluate_set_fatigue_band({"session_id": SESSION, "exercise_id": 999}, USER) is None
    # sem usuário (job sem identidade) não há como garantir ownership
    assert core._evaluate_set_fatigue_band({"session_id": SESSION, "exercise_id": EXERCISE}, None) is None


def test_ia_falha_de_leitura_vira_faixa_desconhecida(monkeypatch):
    _preparar_ia(monkeypatch, falha=RuntimeError("banco fora"))
    assert core._evaluate_set_fatigue_band({"session_id": SESSION, "exercise_id": EXERCISE}, USER) is None


@pytest.mark.parametrize(
    ("conjunto", "faixa_esperada", "leituras_esperadas"),
    [
        (CONJUNTO_COM_FADIGA, PriorFatigueBand.PRE_FATIGUED, 1),
        (nsl.DEFAULT_RULESET, None, 0),  # padrão sem regra de fadiga: não lê
        (RuntimeError("banco fora"), None, 0),  # falha do loader -> padrão
    ],
)
def test_dispatch_passa_indice_faixa_teto_e_conjunto(monkeypatch, conjunto, faixa_esperada, leituras_esperadas):
    _, leituras = _preparar_ia(monkeypatch, fadiga={EXERCISE: _prior(PriorFatigueBand.PRE_FATIGUED)})

    def fake_loader():
        if isinstance(conjunto, Exception):
            raise conjunto
        return conjunto

    monkeypatch.setattr(core, "load_published_ruleset_autocommit", fake_loader)
    monkeypatch.setattr(core.effective_intensity, "resolve_pair_safety", lambda conn, e, q: None)
    monkeypatch.setattr(core, "_evaluate_set_engine", lambda payload, user_id: PairEngineContext(step_kg=PASSO))
    recebido: dict = {}

    def fake_next_set_load_for(values, context, **kwargs):
        recebido.update(kwargs)
        return None

    monkeypatch.setattr(core, "next_set_load_for", fake_next_set_load_for)
    monkeypatch.setattr(
        core,
        "evaluate_set",
        lambda payload, **kwargs: {"model": "m", "category": "HOLD", "note": "", "raw_text": "", "suggested_next_reps": None, "suggested_next_load_kg": None, "suggested_next_rpe": None},
    )
    anteriores = [{"set_type": "warmup"}, {"set_type": "working"}, {"set_type": None}]
    core.dispatch_evaluate_set(
        {
            "exercise_name": "Supino",
            "reps": 10,
            "weight_kg": 40,
            "rpe": 8,
            "previous_sets_this_session": anteriores,
            "session_id": SESSION,
            "exercise_id": EXERCISE,
        },
        USER,
    )
    assert recebido["working_set_index"] == 3  # 2 séries de trabalho no payload -> esta é a 3ª
    assert recebido["fatigue_band"] is faixa_esperada
    assert len(leituras) == leituras_esperadas
    assert recebido["rpe_ceiling"] == 9  # par desconhecido: teto conservador D3
    assert recebido["ruleset"] is (conjunto if isinstance(conjunto, nsl.RuleSet) else nsl.DEFAULT_RULESET)


def test_payload_da_ia_aceita_sessao_e_exercicio_opcionais():
    assert SetEvaluationIn(exercise_name="x").session_id is None
    assert SetEvaluationIn(exercise_name="x").exercise_id is None
    corpo = SetEvaluationIn(exercise_name="x", session_id=SESSION, exercise_id=EXERCISE).model_dump(mode="json")
    assert (corpo["session_id"], corpo["exercise_id"]) == (SESSION, EXERCISE)
    with pytest.raises(ValidationError):
        SetEvaluationIn(exercise_name="x", session_id=0)
    with pytest.raises(ValidationError):
        SetEvaluationIn(exercise_name="x", exercise_id=-1)
