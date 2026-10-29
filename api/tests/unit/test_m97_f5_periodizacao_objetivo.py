"""M97 F5: geração de Modelo por IA com objetivo por dia (mapa §68).

Pedido do usuário: Modelo híbrido (ex.: 5 dias, 2 de força e 3 de
hipertrofia). Cobre:

- o schema do pedido (`PeriodizationDraftIn.training_goal`/`day_goals`);
- a precedência do dia (`goal_plan`);
- os auto-fixes auditados do validador (`_apply_goal_plan`): override por
  item, isolador do dia de força em hipertrofia, técnica/RPE/reps do objetivo,
  compostos pesados do dia e o piso de exercícios do objetivo do dia;
- o prompt: seção OBJETIVO POR DIA com todo número dos donos; sem objetivo
  não-default, prompt e rascunho são os de sempre;
- o complemento de volume não acrescenta técnica fora do objetivo.
"""

from __future__ import annotations

import copy
import os

import pytest
from pydantic import ValidationError

from app.domain.prescription_defaults import defaults_for
from app.domain.training_goal import DEFAULT_GOAL, GOAL_VALUES, is_default
from app.domain.training_safety import goal_disallowed_techniques, goal_rpe_ceiling
from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn
from app.periodization import context as ctx
from app.periodization import goal_plan, validation
from app.periodization import volume_complement as vc
from app.periodization.limits import (
    day_item_goal,
    heavy_compound_range,
    session_exercises_min,
    slot_session_goal,
)
from tests.support import llm_casos, privado
from tests.support.privado import exigir

DEFAULT = DEFAULT_GOAL.value
OUTRO = next(goal for goal in GOAL_VALUES if not is_default(goal))
TECNICA_FORA = sorted(goal_disallowed_techniques(OUTRO))[0]
COMPOSTO, ISOLADOR = 1, 2


def _pedido(**extra) -> PeriodizationDraftIn:
    base = {"volume_mode": "equipment_series", "gym_id": 1, "days_per_cycle": 2}
    return PeriodizationDraftIn(**{**base, **extra})


def _item(order: int, mapping_id: int, **extra) -> dict:
    return {
        "item_order": order,
        "item_kind": "equipment",
        "muscle_group_id": 10,
        "mapping_id": mapping_id,
        "equipment_id": 900 + mapping_id,
        "sets_count": 3,
        "reps_target": "8-12",
        "rpe_target": "8",
        **extra,
    }


def _template(slots: list[dict], training_goal=None) -> WorkoutPlanTemplateIn:
    return WorkoutPlanTemplateIn(name="Rascunho", volume_mode="equipment_series", slots=slots, training_goal=training_goal)


@pytest.fixture
def flags(monkeypatch):
    lidos: list[set[int]] = []

    def fake(conn, mapping_ids):
        lidos.append(set(mapping_ids))
        return {COMPOSTO: True, ISOLADOR: False}

    monkeypatch.setattr(ctx, "load_compound_flags", fake)
    return lidos


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_pedido_sem_objetivo_e_o_de_sempre():
    pedido = _pedido()
    assert pedido.training_goal is None and pedido.day_goals is None
    assert not goal_plan.has_goal_plan(pedido)


def test_pedido_valida_objetivo_e_um_por_dia():
    assert _pedido(training_goal=OUTRO.upper()).training_goal == OUTRO
    assert _pedido(day_goals=[OUTRO, DEFAULT]).day_goals == [OUTRO, DEFAULT]
    with pytest.raises(ValidationError):
        _pedido(training_goal="resistencia")
    with pytest.raises(ValidationError):
        _pedido(day_goals=[OUTRO])  # 2 dias no ciclo
    with pytest.raises(ValidationError):
        _pedido(day_goals=[OUTRO, ""])


# ---------------------------------------------------------------------------
# Precedência do dia
# ---------------------------------------------------------------------------


def test_objetivo_do_dia_declarado_vence_o_do_modelo():
    pedido = _pedido(training_goal=OUTRO, day_goals=[OUTRO, DEFAULT])
    assert goal_plan.day_goal(pedido, 1) == OUTRO
    assert goal_plan.day_goal(pedido, 2) == DEFAULT
    sem_lista = _pedido(training_goal=OUTRO)
    assert goal_plan.day_goal(sem_lista, 2) == OUTRO
    assert goal_plan.declared_day_goal(_pedido(), 1) is None


def test_isolador_do_dia_de_forca_fica_em_hipertrofia():
    assert day_item_goal(OUTRO, True).value == OUTRO
    assert day_item_goal(OUTRO, False).value == DEFAULT
    assert day_item_goal(DEFAULT, True).value == DEFAULT
    assert goal_plan.item_target_goal(OUTRO, "muscle_group", None) == OUTRO
    assert goal_plan.item_target_goal(OUTRO, "cardio", None) is None
    assert goal_plan.override_for(DEFAULT, DEFAULT) is None
    assert goal_plan.override_for(OUTRO, DEFAULT) == OUTRO


# ---------------------------------------------------------------------------
# Validador
# ---------------------------------------------------------------------------


def test_sem_objetivo_nao_default_nada_muda(flags):
    template = _template([{"sequence": 1, "name": "A", "items": [_item(1, COMPOSTO, advanced_technique=TECNICA_FORA)]}])
    antes = template.model_dump()
    report = validation.ValidationReport()
    assert validation._apply_goal_plan(None, template, _pedido(days_per_cycle=1), report) is None
    assert template.model_dump() == antes
    assert report.to_dict() == {"errors": [], "warnings": [], "auto_fixes": []}
    assert flags == []


def test_dia_de_forca_aplica_o_objetivo_item_a_item(flags):
    template = _template(
        [
            {
                "sequence": 1,
                "name": "Pesado",
                "items": [
                    _item(1, COMPOSTO, rpe_target="10", advanced_technique=TECNICA_FORA, advanced_technique_applications=1),
                    _item(2, ISOLADOR, advanced_technique=TECNICA_FORA, advanced_technique_applications=1),
                ],
            },
            {"sequence": 2, "name": "Volume", "items": [_item(1, COMPOSTO, advanced_technique=TECNICA_FORA)]},
        ]
    )
    pedido = _pedido(day_goals=[OUTRO, DEFAULT])
    report = validation.ValidationReport()
    validation._apply_goal_plan(None, template, pedido, report, gym_pairs=[{"mapping_id": 3}])

    pesado, volume = template.slots
    composto, isolador = pesado.items
    perfil = defaults_for(OUTRO)
    assert composto.training_goal == OUTRO
    assert composto.advanced_technique is None
    assert composto.rpe_target == str(goal_rpe_ceiling(OUTRO))
    assert composto.reps_target == perfil.reps_target
    # isolador: override EXPLÍCITO de hipertrofia; técnica e reps preservadas
    assert isolador.training_goal == DEFAULT
    assert isolador.advanced_technique == TECNICA_FORA
    assert isolador.reps_target == "8-12"
    # dia de hipertrofia intocado
    assert volume.items[0].training_goal is None and volume.items[0].advanced_technique == TECNICA_FORA
    assert flags == [{COMPOSTO, ISOLADOR, 3}]

    codigos = [(fix["code"], fix.get("slot"), fix.get("item")) for fix in report.auto_fixes]
    assert (validation.ISOLATION_GOAL_OVERRIDE, 1, 2) in codigos
    assert (validation.TECHNIQUE_REMOVED, 1, 1) in codigos
    assert (validation.RPE_CLAMPED, 1, 1) in codigos
    assert (validation.GOAL_REPS_FIXED, 1, 1) in codigos
    removida = next(f for f in report.auto_fixes if f["code"] == validation.TECHNIQUE_REMOVED)
    assert removida["reason"] == validation.ts.TECHNIQUE_NOT_ALLOWED_FOR_GOAL
    assert removida["training_goal_label"]


def test_modelo_de_forca_registra_o_override_de_hipertrofia_do_isolador(flags):
    template = _template(
        [{"sequence": 1, "name": "Pesado", "items": [_item(1, COMPOSTO), _item(2, ISOLADOR)]}], training_goal=OUTRO
    )
    report = validation.ValidationReport()
    compound_flags = validation._apply_goal_plan(None, template, _pedido(days_per_cycle=1, training_goal=OUTRO), report)
    composto, isolador = template.slots[0].items
    assert composto.training_goal is None  # herda o Modelo (força)
    assert isolador.training_goal == DEFAULT
    fix = next(f for f in report.auto_fixes if f["code"] == validation.ISOLATION_GOAL_OVERRIDE)
    assert fix["item"] == 2 and fix["override"] == DEFAULT and fix["day_goal"] == OUTRO

    # passada final: idempotente e confere os compostos pesados do dia
    antes = len(report.auto_fixes)
    validation._apply_goal_plan(None, template, _pedido(days_per_cycle=1, training_goal=OUTRO), report, None, compound_flags, final=True)
    assert len(report.auto_fixes) == antes
    minimo, maximo = heavy_compound_range(OUTRO)
    aviso = next(w for w in report.warnings if w["code"] == validation.DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE)
    assert (aviso["compound_count"], aviso["min"], aviso["max"]) == (1, minimo, maximo)


def test_sem_flag_de_composto_todo_item_e_isolador(monkeypatch):
    def quebra(conn, ids):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(ctx, "load_compound_flags", quebra)
    template = _template([{"sequence": 1, "name": "A", "items": [_item(1, COMPOSTO)]}])
    validation._apply_goal_plan(None, template, _pedido(days_per_cycle=1, day_goals=[OUTRO]), validation.ValidationReport())
    assert template.slots[0].items[0].training_goal == DEFAULT  # sem flag = isolador


def test_piso_de_exercicios_e_o_do_objetivo_do_dia():
    itens = [_item(i, COMPOSTO) for i in range(1, session_exercises_min(OUTRO) + 1)]
    template = _template([{"sequence": 1, "name": "Pesado", "items": itens}])
    report = validation.ValidationReport()
    validation._check_methodology(template, report, inputs=None, request=_pedido(days_per_cycle=1, day_goals=[OUTRO]))
    assert not [w for w in report.warnings if w["code"] == validation.SLOT_TOO_FEW_EXERCISES]
    report = validation.ValidationReport()
    validation._check_methodology(template, report, inputs=None, request=_pedido(days_per_cycle=1))
    assert [w for w in report.warnings if w["code"] == validation.SLOT_TOO_FEW_EXERCISES]
    assert slot_session_goal([], None, OUTRO).value == OUTRO


def test_rascunho_leva_o_objetivo_do_modelo_do_pedido():
    from app.periodization.output_schema import PeriodizationOutput

    parsed = PeriodizationOutput(
        template={"name": "X", "volume_mode": "equipment_series", "slots": [{"sequence": 1, "name": "A", "slot_type": "normal", "items": []}]},
        rationale="",
        source_citations=[],
    )
    assert validation._to_template_in(parsed, _pedido(days_per_cycle=1, training_goal=OUTRO)).training_goal == OUTRO
    assert validation._to_template_in(parsed, _pedido(days_per_cycle=1)).training_goal is None


# ---------------------------------------------------------------------------
# Complemento de volume
# ---------------------------------------------------------------------------


def test_complemento_nao_acrescenta_tecnica_fora_do_objetivo():
    engine = vc._Complement.__new__(vc._Complement)
    engine.template = _template([], training_goal=OUTRO)
    herda = WorkoutPlanTemplateIn.model_validate(
        {"name": "x", "volume_mode": "equipment_series", "slots": [{"sequence": 1, "name": "A", "items": [_item(1, COMPOSTO)]}]}
    ).slots[0].items[0]
    assert not engine._goal_allows(herda, TECNICA_FORA)
    herda.training_goal = DEFAULT
    assert engine._goal_allows(herda, TECNICA_FORA)
    assert engine._item_rest(herda) == vc.COMPLEMENT_DEFAULT_REST_SECONDS
    herda.training_goal = None
    assert engine._item_rest(herda) == defaults_for(OUTRO).max_rest_seconds


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def _contexto(caso: str = "modo_b_5_dias_sem_perfil") -> dict:
    entrada = next(c for c in llm_casos.carregar("periodization") if c["id"] == caso)["input"]
    contexto = copy.deepcopy(entrada["context"])
    contexto["rag_text"] = llm_casos.RAG_FIXO
    return contexto


def test_prompt_sem_plano_de_objetivo_e_o_de_sempre(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ctx, "goal_plan_section", lambda *a, **k: chamadas.append(a) or "")
    ctx.build_prompt(_contexto())
    assert chamadas == []


# A prosa da secao (rotulos dos numeros, Modo A) e travada no privado
# (`api/privado/tests/unit/test_periodization_prompt.py`). Aqui: os VALORES
# dos donos estao na secao, sem por o texto no log.
def test_secao_de_objetivo_por_dia_interpola_os_donos():
    pedido = _pedido(days_per_cycle=5, day_goals=[OUTRO, DEFAULT, OUTRO, DEFAULT, DEFAULT])
    texto = ctx.goal_plan_section(goal_plan.plan_for_prompt(pedido), mode_b=True)
    perfil = defaults_for(OUTRO)
    isolador = defaults_for(DEFAULT)
    minimo, maximo = heavy_compound_range(OUTRO)
    for rotulo, valor in (
        ("compostos minimo", minimo),
        ("compostos maximo", maximo),
        ("reps do objetivo", perfil.reps_target),
        ("rpe do objetivo", perfil.rpe_target),
        ("series do objetivo", perfil.sets_count),
        ("reps do isolador", isolador.reps_target),
        ("descanso minimo", perfil.min_rest_seconds),
        ("descanso maximo", perfil.max_rest_seconds),
        ("exercicios minimo", session_exercises_min(OUTRO)),
        *((f"tecnica {code}", code) for code in goal_disallowed_techniques(OUTRO)),
    ):
        exigir(f"{valor}" in texto, f"{rotulo} na secao de objetivo")
    regras_dia = [ctx._goal_day_rules(goal, True) for goal in (OUTRO,)]
    exigir(all(regra in texto for regra in regras_dia), "regras do objetivo nao-default na secao")
    exigir(ctx._goal_day_rules(DEFAULT, True) not in texto, "sem regras do objetivo default")


def test_secao_do_modo_a_deixa_a_escolha_para_o_motor():
    pedido = PeriodizationDraftIn(volume_mode="percent_budget", days_per_cycle=2, day_goals=[OUTRO, DEFAULT])
    texto = ctx.goal_plan_section(goal_plan.plan_for_prompt(pedido), mode_b=False)
    exigir(ctx._goal_day_rules(OUTRO, False) in texto, "regras do Modo A na secao")
    exigir(ctx._goal_day_rules(OUTRO, True) not in texto, "sem as regras do Modo B na secao do Modo A")


def test_snapshot_prompt_hibrido_modo_b():
    contexto = _contexto()
    pedido = _pedido(days_per_cycle=5, gym_id=47, day_goals=[OUTRO, DEFAULT, OUTRO, DEFAULT, DEFAULT])
    contexto["goal_plan"] = goal_plan.plan_for_prompt(pedido)
    prompt = ctx.build_prompt(contexto)
    exigir(ctx.goal_plan_section(contexto["goal_plan"], True) in prompt, "secao de objetivo no prompt")
    destino = privado.fixture_privada("llm_prompts_objetivo", "periodization__hibrido_modo_b.txt")
    if os.environ.get("LLM_PROMPT_SNAPSHOT_WRITE"):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(prompt + "\n", encoding="utf-8", newline="\n")
    privado.comparar_arquivo_sem_vazar(
        prompt + "\n", destino, "prompt periodization__hibrido_modo_b", regerar="LLM_PROMPT_SNAPSHOT_WRITE"
    )


def test_catalogo_ganha_flag_de_composto_so_com_plano(monkeypatch):
    class _Cursor:
        def __init__(self):
            self.sql = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params):
            self.sql = (sql, params)

        def fetchall(self):
            return [{"id": COMPOSTO, "is_compound": 1}, {"id": ISOLADOR, "is_compound": 0}]

    class _Conn:
        def cursor(self):
            return _Cursor()

    catalogo = {"Peito": [{"mapping_id": COMPOSTO}, {"mapping_id": ISOLADOR}, {"mapping_id": 99}]}
    marcado = ctx._attach_compound_flags(_Conn(), catalogo)
    assert [i["is_compound"] for i in marcado["Peito"]] == [True, False, False]
    assert ctx.load_compound_flags(_Conn(), []) == {}
