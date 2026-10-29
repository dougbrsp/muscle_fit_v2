"""M76 Lote 4c — tempo de treino, defaults de prescrição e tetos com dono;
prompts interpolados (E7, E8, E9, E10).

Trava, sem banco e sem rede:
1. o dono dos defaults (`domain/prescription_defaults.py`) e o que cada
   consumidor herda dele (mesmos números de antes);
2. o dono dos tetos (`periodization/limits.py`): nenhum número mudou e cada
   consumidor lê a constante nomeada;
3. o preview do Modelo devolve o tempo de CADA slot pelo dono do tempo;
4. o prompt da Periodização contém os números VINDOS DA FONTE: mudar a fonte
   muda o texto, e a tabela de produção (só séries, sem faixa de exercícios)
   gera um exemplo coerente com ela.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.domain import prescription_defaults as pd
from app.periodization import context as ctx
from app.periodization import limits
from tests.support.privado import exigir


# ---------------------------------------------------------------------------
# 1. Defaults de prescrição (E8)
# ---------------------------------------------------------------------------


def test_defaults_de_prescricao_sao_os_de_antes():
    assert (pd.DEFAULT_REPS_TARGET, pd.DEFAULT_RPE_TARGET) == ("8-12", "8-9")
    assert (pd.DEFAULT_MIN_REST_SECONDS, pd.DEFAULT_MAX_REST_SECONDS) == (30, 90)
    assert (pd.REST_SECONDS_SANITY_MIN, pd.REST_SECONDS_SANITY_MAX) == (15, 240)
    assert (pd.DEFAULT_SETS_COUNT, pd.MIN_SETS_COUNT) == (3, 1)


def test_ponto_medio_usa_o_round_do_python_como_as_copias_antigas():
    assert pd.midpoint_rest_seconds(30, 90) == 60
    assert pd.default_rest_seconds() == pd.DEFAULT_PRESCRIPTION_REST_SECONDS == 60
    # Arredondamento bancário: o mesmo `round((a + b) / 2)` que estava escrito
    # em 12 lugares; o valor gravado na ficha não muda.
    for lo, hi in ((30, 91), (45, 120), (15, 240), (60, 61)):
        assert pd.midpoint_rest_seconds(lo, hi) == round((lo + hi) / 2)


def test_contrato_da_geracao_herda_o_intervalo_padrao():
    from app.models import WorkoutPlanGenerateIn

    fields = WorkoutPlanGenerateIn.model_fields
    assert fields["min_rest_seconds"].default == pd.DEFAULT_MIN_REST_SECONDS
    assert fields["max_rest_seconds"].default == pd.DEFAULT_MAX_REST_SECONDS


def test_assinaturas_dos_geradores_herdam_o_intervalo_padrao():
    from app import abc_volume_reinforcement, workout_plan_templates_engine
    from app.generation import mode_a
    from app.intelligence import template_ai_selector_v2

    for fn in (
        mode_a.generate_plan_draft_from_template_a,
        workout_plan_templates_engine.build_template_prompt,
        workout_plan_templates_engine.generate_plan_draft_from_template,
        abc_volume_reinforcement._reinforce_target_groups_minimum_sets,
        template_ai_selector_v2.build_v2_selection_prompt,
    ):
        params = inspect.signature(fn).parameters
        assert params["min_rest_seconds"].default == pd.DEFAULT_MIN_REST_SECONDS
        assert params["max_rest_seconds"].default == pd.DEFAULT_MAX_REST_SECONDS
    params = inspect.signature(template_ai_selector_v2.parse_v2_selection_response).parameters
    assert params["default_rest_seconds"].default == pd.default_rest_seconds()


def test_exemplo_de_reps_do_seletor_v2_vem_do_dono():
    from app.intelligence.template_ai_selector_v2 import build_v2_selection_prompt

    prompt = build_v2_selection_prompt({}, [], {})
    exigir(f'"reps_target": "{pd.DEFAULT_REPS_TARGET}"' in prompt, "exemplo de reps do dono no seletor v2")
    exigir("__DEFAULT_REPS_TARGET__" not in prompt and "{{" not in prompt, "nenhum placeholder sobrando")


# ---------------------------------------------------------------------------
# 2. Tetos (E9): nenhum número mudou; consumidores leem o dono
# ---------------------------------------------------------------------------


def test_tetos_nao_mudaram_de_valor():
    assert (limits.SESSION_EXERCISES_MIN, limits.SESSION_EXERCISES_TARGET_MIN, limits.SESSION_EXERCISES_MAX) == (6, 8, 12)
    assert limits.COMPLEMENT_MAX_EXERCISES_PER_SLOT == 9
    assert limits.COMPLEMENT_MAX_SETS_PER_EXERCISE == 6
    assert limits.SLOT_TIME_LIMIT_SECONDS == 90 * 60
    assert limits.COMPLEMENT_DEFAULT_REST_SECONDS == 90
    assert limits.MAX_PRIORITIZED_PER_SLOT == 3
    assert limits.MODE_A_MAX_EXERCISES_PER_GROUP == 3
    assert limits.LEGACY_V2_MAX_SETS_PER_EXERCISE == 6
    assert limits.LEGACY_REINFORCEMENT_MAX_EXERCISES_PER_DAY == 12
    assert limits.LEGACY_REINFORCEMENT_MAX_SETS_PER_EXERCISE == 9
    # "tipicamente 8-9" do prompt = até onde o complemento leva um slot.
    assert limits.SESSION_EXERCISES_TYPICAL_MAX == limits.COMPLEMENT_MAX_EXERCISES_PER_SLOT


def test_consumidores_leem_o_dono_dos_tetos():
    from app import abc_volume_reinforcement as reinf
    from app.generation import mode_a
    from app.intelligence import template_volume_validator as tvv
    from app.periodization import validation
    from app.periodization import volume_complement as vc

    assert vc.MAX_EXERCISES_PER_SLOT is limits.COMPLEMENT_MAX_EXERCISES_PER_SLOT
    assert vc.MAX_SETS_PER_EXERCISE is limits.COMPLEMENT_MAX_SETS_PER_EXERCISE
    assert vc.SLOT_TIME_LIMIT_SECONDS is limits.SLOT_TIME_LIMIT_SECONDS
    assert vc.COMPLEMENT_DEFAULT_REST_SECONDS is limits.COMPLEMENT_DEFAULT_REST_SECONDS
    assert validation.MAX_PRIORITIZED_PER_SLOT is limits.MAX_PRIORITIZED_PER_SLOT
    assert validation.SESSION_EXERCISES_MIN is limits.SESSION_EXERCISES_MIN
    assert ctx.SESSION_EXERCISES_MAX is limits.SESSION_EXERCISES_MAX
    assert mode_a.MAX_EXERCISES_PER_GROUP is limits.MODE_A_MAX_EXERCISES_PER_GROUP
    assert tvv.MAX_SETS_PER_EXERCISE_V2 is limits.LEGACY_V2_MAX_SETS_PER_EXERCISE
    assert reinf.TARGET_MAX_EXERCISES_PER_DAY is limits.LEGACY_REINFORCEMENT_MAX_EXERCISES_PER_DAY
    assert reinf.MAX_SETS_PER_EXERCISE_REINFORCEMENT is limits.LEGACY_REINFORCEMENT_MAX_SETS_PER_EXERCISE


def test_save_do_modelo_recusa_priorizados_acima_do_teto_do_dono():
    import pytest
    from fastapi import HTTPException

    from app.models import WorkoutPlanTemplateIn
    from app.workout_plan_templates import _validate_template_payload

    n = limits.MAX_PRIORITIZED_PER_SLOT + 1
    payload = WorkoutPlanTemplateIn(
        name="t",
        slots=[
            {
                "sequence": 1,
                "name": "A",
                "slot_type": "normal",
                "items": [
                    {"item_kind": "muscle_group", "muscle_group_id": i + 1, "item_order": i + 1, "prioritize": True}
                    for i in range(n)
                ],
            }
        ],
    )
    with pytest.raises(HTTPException) as excinfo:
        _validate_template_payload(payload)
    assert f"no máximo {limits.MAX_PRIORITIZED_PER_SLOT} itens" in excinfo.value.detail


# ---------------------------------------------------------------------------
# 3. Tempo por slot no preview (E7)
# ---------------------------------------------------------------------------


def _slot(sequence, items, slot_type="normal"):
    return SimpleNamespace(sequence=sequence, slot_type=slot_type, items=[SimpleNamespace(**i) for i in items])


def test_preview_devolve_o_tempo_de_cada_slot_pelo_dono():
    from app.periodization.time_estimate import estimate_slot_time
    from app.workout_plan_templates import _slot_time_estimates

    sem_descanso = {"item_kind": "equipment", "sets_count": 4, "reps_target": "8-12", "rest_seconds": None}
    com_descanso = {"item_kind": "equipment", "sets_count": 3, "reps_target": "10", "rest_seconds": 90}
    slots = [_slot(1, [sem_descanso, com_descanso]), _slot(2, [], "descanso")]
    times = _slot_time_estimates(slots)

    assert [t["slot_sequence"] for t in times] == [1, 2]
    primeiro = times[0]
    esperado = estimate_slot_time([sem_descanso, com_descanso])
    assert primeiro["estimated_seconds"] == esperado["total_seconds"] == 120 + (90 + 270)
    assert primeiro["breakdown"] == esperado["breakdown"]
    # Sem descanso conta 0 s e entra na contagem (decisão da Fase 7).
    assert primeiro["items_without_rest"] == 1
    assert primeiro["has_estimated_reps"] is False
    assert times[1]["estimated_seconds"] == 0


# ---------------------------------------------------------------------------
# 4. Prompt da Periodização com números da fonte (E10)
# ---------------------------------------------------------------------------

# Tabela como a de produção em 2026-10-07: o admin cadastrou só séries
# (exercícios null) e Grandes 1x = 16-20, não o 15-20 do seed.
_TABELA_PRODUCAO = [
    {"macro_grupo": "Grandes", "frequency_bucket": "1x", "sets_per_session_min": 16.0, "sets_per_session_max": 20.0,
     "exercises_per_session_min": None, "exercises_per_session_max": None},
    {"macro_grupo": "Pequenos", "frequency_bucket": "1x", "sets_per_session_min": 12.0, "sets_per_session_max": 14.0,
     "exercises_per_session_min": None, "exercises_per_session_max": None},
    {"macro_grupo": "Outros", "frequency_bucket": "1x", "sets_per_session_min": 1.0, "sets_per_session_max": 24.0,
     "exercises_per_session_min": None, "exercises_per_session_max": None},
    {"macro_grupo": "Grandes", "frequency_bucket": "2x", "sets_per_session_min": 8.0, "sets_per_session_max": 10.0,
     "exercises_per_session_min": None, "exercises_per_session_max": None},
]
_TECNICAS = [
    {"code": "drop_set", "activation_set_equivalent": 0.5, "max_equivalent_per_anchor": 1.0},
    {"code": "rest_pause", "activation_set_equivalent": 0.4, "max_equivalent_per_anchor": 1.0},
]


def _regras_b(ranges=_TABELA_PRODUCAO, techniques=_TECNICAS):
    return ctx._mode_rules("equipment_series", {"methodology_ranges": ranges, "techniques": techniques})


# A prosa em volta de cada número (rótulos, exemplos) é travada no privado
# (`api/privado/tests/unit/test_periodization_prompt.py`). Aqui: os NÚMEROS
# saem da fonte e mudam com ela, sem pôr o texto do prompt no log.


def test_exemplo_da_celula_sai_da_tabela_de_producao():
    regras = _regras_b()
    exemplo = ctx.methodology_cell_example(_TABELA_PRODUCAO, 3)
    exigir(bool(exemplo) and exemplo in regras, "exemplo da célula nas regras")
    # Célula 1x de maior piso (Grandes 16-20), sem faixa de exercícios: 2 x 3 = 6.
    for valor in ("Grandes", "16-20", "(6 "):
        exigir(valor in exemplo, f"{valor} no exemplo da célula")
    # O número do seed que contradizia a tabela não aparece.
    exigir("4-5" not in regras and "15-20" not in regras, "sem o número do seed")


def test_exemplo_da_celula_acompanha_a_tabela():
    tabela = [dict(_TABELA_PRODUCAO[0], sets_per_session_min=18.0, sets_per_session_max=24.0,
                   exercises_per_session_min=5, exercises_per_session_max=6)]
    exemplo = ctx.methodology_cell_example(tabela, 3)
    exigir(exemplo in _regras_b(tabela), "exemplo da célula nas regras")
    for valor in ("5-6", "18-24", "(6 "):
        exigir(valor in exemplo, f"{valor} no exemplo da célula")
    exigir(exemplo != ctx.methodology_cell_example(_TABELA_PRODUCAO, 3), "exemplo acompanha a tabela")


def test_tabela_vazia_nao_inventa_exemplo():
    assert ctx.methodology_cell_example([], 3) == ""
    exigir(ctx.methodology_cell_example(_TABELA_PRODUCAO, 3) not in _regras_b([]), "sem exemplo com tabela vazia")


def test_exemplo_de_tecnica_calculado_pelo_catalogo():
    exemplo = ctx.technique_equivalent_example(_TECNICAS, 4)
    exigir(exemplo in _regras_b(), "exemplo de técnica nas regras")
    exigir("rest_pause" in exemplo and "0,8" in exemplo and "4,8" in exemplo, "rest_pause 4 + 0,8 = 4,8")
    # Coeficiente diferente no catálogo -> texto diferente (não há número à mão).
    outro = [{"code": "rest_pause", "activation_set_equivalent": 0.3, "max_equivalent_per_anchor": 1.0}]
    exigir("4,6" in ctx.technique_equivalent_example(outro, 4), "4 + 0,6 = 4,6")
    # Teto do catálogo vale (0,5 x 2 = 1,0, teto 0,75).
    teto = [{"code": "rest_pause", "activation_set_equivalent": 0.5, "max_equivalent_per_anchor": 0.75}]
    exigir("4,75" in ctx.technique_equivalent_example(teto, 4), "4 + 0,75 = 4,75")
    # Sem técnica com equivalente: sem exemplo.
    assert ctx.technique_equivalent_example([{"code": "fst_7", "activation_set_equivalent": 0}], 4) == ""


def test_tetos_e_defaults_no_texto_vem_das_constantes():
    regras = _regras_b()
    for valor in (
        f"{limits.SESSION_EXERCISES_TARGET_MIN}-{limits.SESSION_EXERCISES_TYPICAL_MAX},",
        f"'{pd.DEFAULT_REPS_TARGET}', '{pd.DEFAULT_RPE_TARGET}'",
    ):
        exigir(valor in regras, f"{valor} nas regras do Modo B")
    exigir("{" not in regras, "nenhum placeholder sobrando")


def test_cobertura_do_modo_a_vem_do_dominio():
    from app.intelligence.template_volume_domain import OVER_ALLOCATION_THRESHOLD_PCT, UNDER_ALLOCATION_THRESHOLD_PCT

    regras = ctx._mode_rules("percent_budget")
    exigir(
        f"{UNDER_ALLOCATION_THRESHOLD_PCT} e {OVER_ALLOCATION_THRESHOLD_PCT})" in regras,
        "faixa de cobertura nas regras do Modo A",
    )
    exigir("{" not in regras, "nenhum placeholder sobrando")


def _contexto_priorizados() -> dict:
    return {
        "request": {"volume_mode": "equipment_series", "days_per_cycle": 4, "training_type_id": None,
                    "gym_id": 1, "rag_sources": []},
        "muscle_groups": [],
        "targets": [],
        "methodology_ranges": _TABELA_PRODUCAO,
        "methodology_targets": [],
        "techniques": _TECNICAS,
        "gym_catalog": {},
        "rag_text": "",
        "instructions": "",
    }


def test_limite_de_priorizados_no_prompt_e_no_checklist(monkeypatch):
    contexto = _contexto_priorizados()
    antes = ctx.build_prompt(contexto)
    exigir(antes.endswith(ctx._final_checklist(4)), "checklist com o limite do dono")
    monkeypatch.setattr(ctx, "MAX_PRIORITIZED_PER_SLOT", 99)
    depois = ctx.build_prompt(contexto)
    # O limite entra nas regras duras E no checklist: mudar a fonte muda os dois.
    exigir(depois.count("99") == antes.count("99") + 2, "limite da fonte nas regras e no checklist")


def test_rag_nao_fixa_series_semanais_do_deltoide():
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[3] / "api" / "privado" / "rag" / "markdown"
    for arquivo in (raiz / "HIPERTROFIA.md", raiz / "TIPOS_TREINO" / "1. Full Body" / "FULLBODY.md"):
        texto = arquivo.read_text(encoding="utf-8")
        assert "9 a 10 séries semanais" not in texto
        assert "meta semanal do macro-grupo" in texto
