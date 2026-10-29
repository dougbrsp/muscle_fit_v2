"""M95b + M95c: nenhum prompt de IA do treino recebe o CODIGO cru de uma tecnica.

- M95c: o bloco do objetivo (`goal_prompt_context.goal_section`) cita as
  tecnicas vetadas pelo objetivo com o NOME do catalogo `advanced_techniques`
  nos quatro prompts (avaliacao de serie, resumo de exercicio, resumo de
  treino, insights); sem catalogo, mencao generica, nunca o codigo.
- M95b: o resumo de exercicio rotula as series pelo `set_labels.set_display_label`
  (nome do catalogo pelo `technique_code` real da linha), com
  `technique_code`/`anchor_set_number` lidos em `services.exercise_summary_context`.

Os nomes abaixo sao de TESTE: o teste prova que o prompt usa o que o catalogo
devolver, sem nenhum nome escrito no codigo de producao.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from decimal import Decimal

import pytest

from app import advanced_techniques
from app import exercise_set_evaluation_ai as set_eval
from app import exercise_summary_ai as exercise_summary
from app import goal_prompt_context as gpc
from app import insights_core
from app import insights_prompt
from app import prompt_store
from app import services
from app import workout_ai_generation_core as core
from app import workout_summary_ai as workout_summary
from app.domain.training_goal import DEFAULT_GOAL, GOAL_VALUES, is_default
from app.domain.training_safety import goal_disallowed_techniques
from app.engine_decision_context import PairEngineContext
from app.set_labels import TECHNIQUE_FALLBACK_LABEL, technique_names
from tests.support import llm_casos

DEFAULT = DEFAULT_GOAL.value
OUTRO = next(goal for goal in GOAL_VALUES if not is_default(goal))

CATALOGO = [
    {"code": "partial_reps", "name": "Parciais Teste", "set_type": "forced_rep", "is_active": True},
    {"code": "forced_reps", "name": "Forçadas Teste", "set_type": "forced_rep", "is_active": True},
    {"code": "drop_set", "name": "Drop Teste", "set_type": "drop_set", "is_active": True},
    {"code": "rest_pause", "name": "Pausa Teste", "set_type": "rest_pause", "is_active": True},
    {"code": "fst7", "name": "Fáscia Teste", "set_type": None, "is_active": True},
    {"code": "gvt", "name": "Volume Alemão Teste", "set_type": None, "is_active": False},
]
NOMES = technique_names(CATALOGO)
VETADAS = sorted(goal_disallowed_techniques(OUTRO))
CODIGO_CRU = re.compile(r"\b(?:" + "|".join(sorted({*NOMES, "forced_rep"})) + r")\b")


def _bloco_objetivo(prompt: str) -> str:
    """O bloco do objetivo presente no prompt (com ou sem os nomes do catalogo),
    sem depender do texto do template (privado, M103 P2)."""
    for nomes in (NOMES, None):
        bloco = gpc.goal_section(OUTRO, nomes)
        if bloco and bloco in prompt:
            return bloco
    pytest.fail("bloco do objetivo ausente do prompt")


def _assert_nomes_do_catalogo(bloco: str) -> None:
    for code in VETADAS:
        assert NOMES[code] in bloco, code
    assert not CODIGO_CRU.search(bloco), bloco


def _assert_sem_codigo(bloco: str) -> None:
    assert not CODIGO_CRU.search(bloco), bloco


# ---------------------------------------------------------------------------
# M95c: o bloco do objetivo nos quatro prompts
# ---------------------------------------------------------------------------


def test_bloco_do_objetivo_sem_catalogo_nunca_traz_o_codigo():
    for nomes in (None, {}):
        texto = gpc.goal_section(OUTRO, nomes)
        _assert_sem_codigo(texto)
        # Mencao generica (prosa do template): privado, M103 P2.
        assert prompt_store.load(gpc.PROMPT_FEATURE_DIR, f"{gpc.GOAL_PROMPT_VERSION}.tecnicas_genericas") in texto


def test_bloco_do_objetivo_com_catalogo_parcial_cita_os_nomes_e_resume_o_resto():
    parcial = {VETADAS[0]: NOMES[VETADAS[0]]}
    texto = gpc.goal_section(OUTRO, parcial)
    assert NOMES[VETADAS[0]] in texto
    assert prompt_store.load(gpc.PROMPT_FEATURE_DIR, f"{gpc.GOAL_PROMPT_VERSION}.tecnicas_outras") in texto
    _assert_sem_codigo(texto)


def _set_eval_prompt(names):
    from app.domain.training_safety import IntensityComposition
    from app.engine_decision_context import EngineDecision, next_set_load_for

    entrada = llm_casos.carregar("set_evaluation")[0]["input"]
    decision = entrada["engine"]["decision"]
    engine = PairEngineContext(
        step_kg=Decimal(entrada["engine"]["step_kg"]),
        decision=EngineDecision(**decision) if decision else None,
        load_convention=entrada["engine"].get("load_convention"),
        training_goal=OUTRO,
    )
    composition = IntensityComposition(**entrada["composition"])
    payload = entrada["payload"]
    return set_eval.build_prompt(
        payload,
        llm_casos.RAG_FIXO,
        engine=engine,
        composition=composition,
        set_reading=llm_casos.leitura_da_serie(payload, composition, engine),
        next_set=next_set_load_for(payload, engine),
        technique_names=names,
    )


def test_avaliacao_de_serie_usa_o_nome_do_catalogo():
    _assert_nomes_do_catalogo(_bloco_objetivo(_set_eval_prompt(NOMES)))
    _assert_sem_codigo(_bloco_objetivo(_set_eval_prompt(None)))


def _exercise_summary_payload(**extra) -> dict:
    payload = {
        "exercise_name": "Supino",
        "sets": [
            {"set_number": 1, "set_type": "warmup", "reps": 12, "weight_kg": 20},
            {"set_number": 2, "set_type": "working", "reps": 10, "weight_kg": 60, "rpe": 9},
            {
                "set_number": 3,
                "set_type": "forced_rep",
                "technique_code": "partial_reps",
                "anchor_set_number": 2,
                "reps": 4,
                "weight_kg": 60,
            },
            {"set_number": 4, "set_type": "working", "technique_code": "drop_set", "anchor_set_number": 2,
             "reps": 8, "weight_kg": 40},
            # Linha legada sem codigo: deriva pelo `set_type`.
            {"set_number": 5, "set_type": "rest_pause", "reps": 3, "weight_kg": 60},
            {"set_number": 6, "set_type": "feeler", "reps": 5, "weight_kg": 30},
        ],
    }
    payload.update(extra)
    return payload


def test_resumo_de_exercicio_usa_o_nome_do_catalogo_no_bloco_do_objetivo():
    prompt = exercise_summary.build_prompt(_exercise_summary_payload(training_goal=OUTRO, technique_names=NOMES))
    _assert_nomes_do_catalogo(_bloco_objetivo(prompt))
    sem = exercise_summary.build_prompt(_exercise_summary_payload(training_goal=OUTRO))
    _assert_sem_codigo(_bloco_objetivo(sem))


def test_resumo_de_treino_usa_o_nome_do_catalogo_no_bloco_do_objetivo():
    activity = {
        "id": 1,
        "name": "Ficha",
        "local_date": "2026-10-25",
        "exercises": [
            {"id": 1, "exercise_name": "Agachamento", "training_goal": OUTRO,
             "sets": [{"set_number": 1, "set_type": "working", "reps": 5, "weight_kg": 100, "rpe": 8}]},
        ],
    }
    _assert_nomes_do_catalogo(_bloco_objetivo(workout_summary.build_prompt(activity, technique_names=NOMES)))
    _assert_sem_codigo(_bloco_objetivo(workout_summary.build_prompt(activity)))


def test_insights_usa_o_nome_do_catalogo_no_bloco_do_objetivo():
    entrada = llm_casos.carregar("insights")[0]["input"]
    package = llm_casos.reviver_pacote(entrada["package"])
    prompt = insights_prompt.build_insight_prompt(
        package, "RAG", question=entrada["question"], training_goals=(DEFAULT, OUTRO), technique_names=NOMES
    )
    _assert_nomes_do_catalogo(_bloco_objetivo(prompt))
    sem = insights_prompt.build_insight_prompt(
        package, "RAG", question=entrada["question"], training_goals=(DEFAULT, OUTRO)
    )
    _assert_sem_codigo(_bloco_objetivo(sem))


# ---------------------------------------------------------------------------
# M95b: series do resumo de exercicio
# ---------------------------------------------------------------------------


def _series(prompt: str) -> str:
    """As linhas das series (`_format_sets`), sem depender do texto do
    template (privado, M103 P2)."""
    trecho = "\n".join(linha for linha in prompt.split("\n") if linha.startswith("- série "))
    assert trecho
    return trecho


def test_resumo_de_exercicio_rotula_as_series_pelo_nome_do_catalogo():
    series = _series(exercise_summary.build_prompt(_exercise_summary_payload(technique_names=NOMES)))
    assert "- série 3 [técnica: Parciais Teste]: 4 reps" in series
    # `working` vinculada (technique_code + ancora) tambem e aplicacao.
    assert "- série 4 [técnica: Drop Teste]: 8 reps" in series
    assert "- série 5 [técnica: Pausa Teste]: 3 reps" in series
    assert "- série 1 [aquecimento]: 12 reps" in series
    assert "- série 2: 10 reps" in series


@pytest.mark.parametrize("nomes", [NOMES, None, {}])
def test_resumo_de_exercicio_nunca_traz_codigo_cru_nas_series(nomes):
    series = _series(exercise_summary.build_prompt(_exercise_summary_payload(technique_names=nomes)))
    assert not CODIGO_CRU.search(series), series
    assert not re.search(r"\[[a-z]+(?:_[a-z]+)*\]", series.replace("[aquecimento]", ""))


def test_resumo_de_exercicio_sem_catalogo_cai_no_rotulo_generico():
    series = _series(exercise_summary.build_prompt(_exercise_summary_payload()))
    assert f"- série 3 [{TECHNIQUE_FALLBACK_LABEL}]" in series


def test_versao_do_resumo_de_exercicio_subiu():
    assert exercise_summary.PROMPT_VERSION == "exercise-summary-v3"


class _Cursor:
    def __init__(self, log):
        self.log = log
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)

    def fetchone(self):
        return {"id": 7, "original_name": "Supino"}

    def fetchall(self):
        return [{"set_number": 1, "set_type": None, "technique_code": "drop_set", "anchor_set_number": 1}]


class _Conn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return _Cursor(self.log)


def test_contexto_do_resumo_de_exercicio_le_tecnica_e_ancora(monkeypatch):
    log: list[str] = []

    @contextmanager
    def fake_transaction():
        yield _Conn(log)

    monkeypatch.setattr(services, "transaction", fake_transaction)
    contexto = services.exercise_summary_context(7, user_id=1)
    sql_series = next(sql for sql in log if "FROM exercise_sets" in sql)
    assert "technique_code" in sql_series and "anchor_set_number" in sql_series
    assert contexto["sets"][0]["technique_code"] == "drop_set"
    assert contexto["sets"][0]["set_type"] == "working"


# ---------------------------------------------------------------------------
# Dispatch: a leitura unica do catalogo chega aos prompts
# ---------------------------------------------------------------------------


def _catalogo(monkeypatch, chamadas=None):
    def fake(include_inactive=False):
        if chamadas is not None:
            chamadas.append(include_inactive)
        return [dict(item) for item in CATALOGO if include_inactive or item["is_active"]]

    monkeypatch.setattr(advanced_techniques, "list_techniques", fake)


def test_nomes_do_catalogo_incluem_inativas_e_falha_vira_vazio(monkeypatch):
    _catalogo(monkeypatch)
    assert advanced_techniques.technique_display_names() == NOMES

    def quebra(include_inactive=False):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(advanced_techniques, "list_techniques", quebra)
    assert advanced_techniques.technique_display_names() == {}


def test_dispatch_do_resumo_de_exercicio_passa_os_nomes(monkeypatch):
    _catalogo(monkeypatch)
    capturado = {}
    monkeypatch.setattr(core, "exercise_summary_context", lambda *a, **k: {"original_name": "Supino", "sets": []})
    monkeypatch.setattr(core, "safe_read", lambda reader, *a, default=None, **k: default)
    monkeypatch.setattr(core, "summarize_exercise", lambda payload, user_id=None: capturado.update(payload) or {})
    core.dispatch_exercise_summary({"exercise_id": 7}, user_id=1)
    assert capturado["technique_names"] == NOMES


def _dispatch_set_eval(monkeypatch, goal):
    recebido = {}
    motor = PairEngineContext(step_kg=Decimal("2.5"), training_goal=goal)

    def fake_evaluate_set(payload, **kwargs):
        recebido.update(kwargs)
        return {"category": "HOLD", "suggested_next_load_kg": None, "suggested_next_reps": None,
                "suggested_next_rpe": None}

    monkeypatch.setattr(core, "evaluate_set", fake_evaluate_set)
    monkeypatch.setattr(core, "_evaluate_set_engine", lambda payload, user_id: motor)
    monkeypatch.setattr(core, "_evaluate_set_logged_intensity", lambda engine: None)
    monkeypatch.setattr(core, "_evaluate_set_fatigue_band", lambda payload, user_id: None)
    monkeypatch.setattr(core, "load_published_ruleset_autocommit", lambda: core.DEFAULT_RULESET)
    monkeypatch.setattr(core, "apply_engine_load_bound", lambda result, *a, **k: {**result, "clamps": []})
    payload = {"exercise_name": "Supino", "set_number": 1, "sets_count": 3, "reps": 5, "weight_kg": 100, "rpe": 8}
    core.dispatch_evaluate_set(payload, user_id=1)
    return recebido


def test_dispatch_da_avaliacao_de_serie_le_o_catalogo_so_fora_do_default(monkeypatch):
    chamadas: list = []
    _catalogo(monkeypatch, chamadas)
    assert _dispatch_set_eval(monkeypatch, OUTRO)["technique_names"] == NOMES
    assert chamadas == [True]
    chamadas.clear()
    assert _dispatch_set_eval(monkeypatch, DEFAULT)["technique_names"] is None
    assert chamadas == []


@pytest.mark.parametrize("goals, esperado", [((DEFAULT, OUTRO), NOMES), ((DEFAULT,), None)])
def test_dispatch_de_insights_passa_os_nomes_so_fora_do_default(monkeypatch, goals, esperado):
    _catalogo(monkeypatch)
    recebido = {}

    class _Window:
        kind = type("K", (), {"value": "last_7d"})()

    contexto = insights_core.WindowGoalContext(training_goals=goals, deload=False)
    monkeypatch.setattr(
        insights_core,
        "build_insights_inputs",
        lambda **k: ({"window": _Window(), "size_bytes": 1}, [], contexto),
    )
    monkeypatch.setattr(insights_core, "select_insights_rag", lambda groups, goals=None: (0, "RAG"))

    def fake_generate(question, package, rag_text, user_id=None, **kwargs):
        recebido.update(kwargs)
        return {"model": "m", "insight": "ok"}

    monkeypatch.setattr(insights_core, "generate_insight", fake_generate)
    insights_core.dispatch_insights({"question": "Como estou?"}, user_id=1)
    assert recebido["technique_names"] == esperado
