"""Modo de contagem do volume, Lote 5: geracao de Modelo por IA (Periodizacao).

O que este arquivo trava, sem banco e sem rede (nenhuma chamada a OpenAI):

1. o modo do pedido (`PeriodizationDraftIn.volume_counting_mode`) e validado
   pelo enum do motor e resolvido num ponto so (`request_counting_mode`),
   com o default do motor quando ausente;
2. prompt: a regra de contagem e o item 2 do checklist dependem do modo. Em
   `primary_only` o texto e o de antes (o snapshot renderizado tambem prova:
   `prompt_sha256` dos casos antigos nao mudou). No modo ponderado o modo, os
   papeis e o limiar de sessao do secundario saem do motor, sem numero a mao;
3. contexto: no modo ponderado o catalogo da academia traz
   `volume_contributions` com os pesos do motor (`counted_contributions`);
   em `primary_only` o catalogo sai intocado, sem leitura nova; a TABELA vem
   das faixas do modo;
4. validador e complemento: leem e avaliam no modo do pedido; no ponderado a
   serie num composto conta tambem no secundario, a trava "nenhum grupo passa
   a ABOVE" continua e, em `primary_only`, o resultado e o mesmo de antes
   (paridade com a forma de conjunto da v4).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain import training_safety as ts
from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from app.intelligence.methodology_volume import (
    COUNTED_ROLES_BY_MODE,
    DEFAULT_VOLUME_COUNTING_MODE,
    SECONDARY_SESSION_MIN_SETS,
    ContributionRow,
    VolumeCountingMode,
)
from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn
from app.periodization import context as ctx
from app.periodization import validation
from app.periodization import volume_complement as vc
from app.periodization.core import PROMPT_VERSION
from tests.support.fake_db import CursorFactoryConn, NullContext
from tests.support.privado import exigir

PONDERADO = VolumeCountingMode.PRIMARY_SECONDARY
SO_PRIMARIO = VolumeCountingMode.PRIMARY_ONLY

# ---------------------------------------------------------------------------
# Cenario: puxada (costas PRIMARY, biceps secundario) e rosca (biceps PRIMARY)
# ---------------------------------------------------------------------------

COSTAS, BICEPS, CORE = 1, 3, 9
GRANDES, PEQUENOS = 10, 20
# Peso do biceps na puxada, como viria do cadastro (exercise_muscle_contributions).
PESO_BICEPS_NA_PUXADA = 0.5
PESO_ESTABILIZADOR = 0.3

GROUPS = (
    mv.MethodologyGroupInput(id=COSTAS, name="Costas", volume_class_id=GRANDES),
    mv.MethodologyGroupInput(id=BICEPS, name="Bíceps", volume_class_id=PEQUENOS),
    mv.MethodologyGroupInput(id=CORE, name="Core", volume_class_id=None),
)

PUXADA_A, PUXADA_B, ROSCA, ROSCA_NOVA = 400, 401, 500, 501


def _puxada() -> tuple[ContributionRow, ...]:
    return (
        ContributionRow(COSTAS, "PRIMARY", 1.0),
        ContributionRow(BICEPS, "SECONDARY", PESO_BICEPS_NA_PUXADA),
        ContributionRow(CORE, "STABILIZER", PESO_ESTABILIZADOR),
    )


ROWS = {
    PUXADA_A: _puxada(),
    PUXADA_B: _puxada(),
    ROSCA: (ContributionRow(BICEPS, "PRIMARY", 1.0),),
    ROSCA_NOVA: (ContributionRow(BICEPS, "PRIMARY", 1.0),),
}
CANONICAL = {m: 1000 + m for m in ROWS}


def classes(costas=(6, 9), biceps=(8, 10)):
    def spec(cid, name, rng):
        return mv.VolumeClassSpec(
            id=cid,
            name=name,
            ranges={"1x": mv.MethodologyRange(sets_min=rng[0], sets_max=rng[1], sets_source=mv.RangeSource.ADMIN)},
        )

    return {GRANDES: spec(GRANDES, "Grandes", costas), PEQUENOS: spec(PEQUENOS, "Pequenos", biceps)}


def inputs(mode=PONDERADO, volume_classes=None) -> svc.MethodologyInputs:
    return svc.MethodologyInputs(
        canonical_ids=dict(CANONICAL),
        contributions=dict(ROWS),
        groups=GROUPS,
        volume_classes=volume_classes if volume_classes is not None else classes(),
        counting_mode=VolumeCountingMode(mode),
    )


def eq(order, mapping_id, group, sets):
    return {
        "item_order": order,
        "item_kind": "equipment",
        "muscle_group_id": group,
        "mapping_id": mapping_id,
        "equipment_id": 900 + mapping_id,
        "sets_count": sets,
        "reps_target": "8-12",
        "rpe_target": "8",
    }


def semana(items) -> WorkoutPlanTemplateIn:
    slots = [{"sequence": 1, "name": "Dia 1", "slot_type": "normal", "items": items}]
    slots += [{"sequence": s, "name": "Descanso", "slot_type": "descanso", "items": []} for s in range(2, 8)]
    return WorkoutPlanTemplateIn(name="Rascunho", volume_mode="equipment_series", slots=slots)


def pair(mapping_id, group):
    return {
        "mapping_id": mapping_id,
        "equipment_id": 900 + mapping_id,
        "muscle_group_id": group,
        "exercise_name": f"Exercício {mapping_id}",
        "equipment_name": f"Equipamento {900 + mapping_id}",
    }


def sets_of(tpl):
    return [(i.mapping_id, i.sets_count) for i in tpl.slots[0].items]


def avaliar(tpl, data):
    ev = mv.evaluate_methodology_volume(
        svc.build_slot_inputs(tpl.slots, "equipment_series", data.canonical_ids),
        data.contributions,
        data.groups,
        "equipment_series",
        None,
        data.volume_classes,
        data.counting_mode,
    )
    return {g.muscle_group_id: g for g in ev.groups}


# ---------------------------------------------------------------------------
# 1. Pedido
# ---------------------------------------------------------------------------


def test_pedido_sem_modo_usa_o_default_do_motor():
    pedido = PeriodizationDraftIn(volume_mode="equipment_series", gym_id=1)
    assert pedido.volume_counting_mode is None
    assert ctx.request_counting_mode(pedido) is DEFAULT_VOLUME_COUNTING_MODE


def test_pedido_com_modo_valido_e_o_enum_do_motor():
    pedido = PeriodizationDraftIn(volume_mode="equipment_series", gym_id=1, volume_counting_mode=PONDERADO.value)
    assert ctx.request_counting_mode(pedido) is PONDERADO


def test_pedido_com_modo_invalido_e_recusado():
    with pytest.raises(ValidationError):
        PeriodizationDraftIn(volume_mode="equipment_series", gym_id=1, volume_counting_mode="tudo")


def test_prompt_version_subiu():
    # v13 (M98 C5): cardio no Modelo; sem cardio pedido o texto e o da v12.
    assert PROMPT_VERSION == "periodization-v14"


# ---------------------------------------------------------------------------
# 2. Prompt
# ---------------------------------------------------------------------------


def _contexto(mode=None):
    request = {"volume_mode": "equipment_series", "days_per_cycle": 7, "training_type_id": None, "gym_id": 7, "rag_sources": []}
    if mode is not None:
        request["volume_counting_mode"] = VolumeCountingMode(mode).value
    return {
        "request": request,
        "muscle_groups": [],
        "targets": [],
        "methodology_ranges": [],
        "methodology_targets": [],
        "techniques": [],
        "gym_catalog": {},
        "rag_text": "RAG",
        "instructions": "",
    }


def test_todo_modo_tem_regra_de_contagem_e_item_de_checklist():
    """Cada modo tem o seu fragmento de regra e de item 2 do checklist
    (`api/privado/prompts/periodization/`). O texto do `primary_only` letra
    por letra e travado no privado e pelo snapshot."""
    assert set(ctx._COUNTING_PARTS) == set(VolumeCountingMode)
    for modo in VolumeCountingMode:
        for parte in (".contagem", ".checklist_contagem"):
            assert ctx._counting_rule(modo, parte)
    assert len({ctx._counting_rule(modo, ".contagem") for modo in VolumeCountingMode}) == len(VolumeCountingMode)


def test_contexto_sem_modo_e_com_primary_only_dao_o_mesmo_prompt():
    assert ctx.build_prompt(_contexto()) == ctx.build_prompt(_contexto(SO_PRIMARIO))


def test_ponderado_interpola_modo_papeis_e_limiar_do_motor():
    # Os rotulos de cada valor sao travados no privado; aqui, os valores do
    # motor e os fragmentos do modo, sem por o texto no log.
    prompt = ctx.build_prompt(_contexto(PONDERADO))
    for rotulo, valor in (
        ("modo", f"'{PONDERADO.value}'"),
        ("modo default", f"'{DEFAULT_VOLUME_COUNTING_MODE.value}'"),
        ("papeis", " e ".join(sorted(COUNTED_ROLES_BY_MODE[PONDERADO]))),
        ("limiar de sessao", f"{SECONDARY_SESSION_MIN_SETS} serie"),
        ("campo do catalogo", "`volume_contributions`"),
    ):
        exigir(valor in prompt, f"{rotulo} no prompt ponderado")
    regra = ctx._counting_rule(PONDERADO, ".contagem")
    exigir(regra in prompt, "regra ponderada no prompt")
    # A regra do primario so (EXG-014 "conta por inteiro") nao aparece como regra deste Modelo.
    exigir(ctx._counting_rule(SO_PRIMARIO, ".contagem") not in prompt, "sem a regra do primario")
    # Nada de peso escrito a mao: o peso vem do catalogo, por par.
    exigir("0,5" not in prompt and "0.5" not in prompt, "sem peso escrito a mao")
    # O checklist do modo e o item 2 ponderado.
    exigir(prompt.endswith(ctx._final_checklist(7, PONDERADO)), "checklist ponderado fecha o prompt")
    exigir(ctx._counting_rule(PONDERADO, ".checklist_contagem") in prompt, "item 2 ponderado no checklist")


def test_contexto_antigo_sem_modo_e_primary_only():
    assert ctx.counting_mode_of_context({"request": {}}) is DEFAULT_VOLUME_COUNTING_MODE
    assert ctx.counting_mode_of_context(_contexto(PONDERADO)) is PONDERADO


# ---------------------------------------------------------------------------
# 3. Contexto
# ---------------------------------------------------------------------------


# Grupos do contexto (`load_methodology_catalog`): BICEPS_LONGA e fatia do biceps.
BICEPS_LONGA = 31
GRUPOS_CTX = [
    {"id": COSTAS, "name": "Costas", "methodology_parent_group_id": None},
    {"id": BICEPS, "name": "Bíceps", "methodology_parent_group_id": None},
    {"id": BICEPS_LONGA, "name": "Bíceps cabeça longa", "methodology_parent_group_id": BICEPS},
    {"id": CORE, "name": "Core", "methodology_parent_group_id": None},
]


def test_catalogo_ponderado_junta_fatia_e_pai_num_bloco_so(monkeypatch):
    """Fatia + pai no mesmo bloco viram UMA entrada (id do bloco, maior peso,
    primario se algum for) - a juncao do motor, para a LLM nao somar em dobro."""
    linhas = {
        ROSCA: (
            ContributionRow(BICEPS, "SECONDARY", PESO_BICEPS_NA_PUXADA),
            ContributionRow(BICEPS_LONGA, "PRIMARY", 1.0),
        )
    }
    monkeypatch.setattr(svc, "load_inherited_contributions", lambda cur, ids, mode: ({}, linhas))
    catalogo = {"Bíceps": [{"mapping_id": ROSCA, "equipment_id": 2}]}
    saida = ctx.attach_volume_contributions(CursorFactoryConn(NullContext), catalogo, PONDERADO, GRUPOS_CTX)
    assert saida["Bíceps"][0]["volume_contributions"] == [{"muscle_group_id": BICEPS, "weight": 1.0, "primary": True}]


def test_catalogo_em_primary_only_sai_intocado_e_sem_leitura(monkeypatch):
    monkeypatch.setattr(svc, "load_inherited_contributions", lambda *a, **k: pytest.fail("nao deveria ler"))
    catalogo = {"Costas": [{"mapping_id": PUXADA_A, "equipment_id": 1}]}
    saida = ctx.attach_volume_contributions(None, catalogo, SO_PRIMARIO, GRUPOS_CTX)
    assert saida == {"Costas": [{"mapping_id": PUXADA_A, "equipment_id": 1}]}


def test_catalogo_ponderado_traz_os_pesos_do_motor(monkeypatch):
    pedidos = []

    def fake(cur, mapping_ids, mode):
        pedidos.append((set(mapping_ids), mode))
        return {}, {m: ROWS[m] for m in mapping_ids if m in ROWS}

    monkeypatch.setattr(svc, "load_inherited_contributions", fake)
    catalogo = {
        "Costas": [{"mapping_id": PUXADA_A, "equipment_id": 1}],
        "Bíceps": [{"mapping_id": ROSCA, "equipment_id": 2}, {"mapping_id": 777, "equipment_id": 3}],
    }
    saida = ctx.attach_volume_contributions(CursorFactoryConn(NullContext), catalogo, PONDERADO, GRUPOS_CTX)

    assert pedidos == [({PUXADA_A, ROSCA, 777}, PONDERADO)]  # uma leitura so
    # Estabilizador fora; biceps com o peso do cadastro; primario marcado.
    assert saida["Costas"][0]["volume_contributions"] == [
        {"muscle_group_id": COSTAS, "weight": 1.0, "primary": True},
        {"muscle_group_id": BICEPS, "weight": PESO_BICEPS_NA_PUXADA, "primary": False},
    ]
    assert saida["Bíceps"][0]["volume_contributions"] == [{"muscle_group_id": BICEPS, "weight": 1.0, "primary": True}]
    assert saida["Bíceps"][1]["volume_contributions"] == []  # sem cadastro: o motor nao conta


def test_tabela_da_metodologia_vem_das_faixas_do_modo(monkeypatch):
    modos = []
    monkeypatch.setattr(svc, "load_methodology_groups", lambda cur: ())
    monkeypatch.setattr(svc, "load_volume_classes", lambda cur, mode: modos.append(mode) or {})
    ctx.load_methodology_catalog(CursorFactoryConn(NullContext), PONDERADO)
    ctx.load_methodology_catalog(CursorFactoryConn(NullContext))
    assert modos == [PONDERADO, DEFAULT_VOLUME_COUNTING_MODE]


# ---------------------------------------------------------------------------
# 4. Complemento de volume
# ---------------------------------------------------------------------------


def test_block_contributions_junta_fatia_e_pai_com_o_maior_peso():
    """Regra unica do motor, usada pelo complemento: grupo fora de `block_of`
    nao conta; dois grupos no mesmo bloco ficam com o maior peso e PRIMARY."""
    fatia = 30
    linhas = (
        ContributionRow(BICEPS, "SECONDARY", PESO_BICEPS_NA_PUXADA),
        ContributionRow(fatia, "PRIMARY", 1.0),
        ContributionRow(CORE, "STABILIZER", PESO_ESTABILIZADOR),
    )
    blocos = mv.block_contributions(linhas, PONDERADO, {BICEPS: BICEPS, fatia: BICEPS})
    assert blocos == {BICEPS: mv.GroupContribution(1.0, True)}
    assert mv.block_contributions(linhas, SO_PRIMARIO, {BICEPS: BICEPS}) == {}


def test_ponderado_secundario_da_puxada_ja_conta_e_o_complemento_sobe_a_rosca():
    """Biceps: 2x4 puxada x peso + 2 da rosca = 6 no ponderado (faixa 8-10).
    O complemento soma na rosca (PRIMARY primeiro) ate 8, sem tocar a puxada."""
    tpl = semana([eq(1, PUXADA_A, COSTAS, 4), eq(2, PUXADA_B, COSTAS, 4), eq(3, ROSCA, BICEPS, 2)])
    data = inputs(PONDERADO, classes(costas=(6, 9)))
    antes = avaliar(tpl, data)
    assert antes[BICEPS].direct_sets_axis.value_per_session == 6

    fixes = vc.complement_volume(tpl, data, [], {}).auto_fixes

    assert sets_of(tpl) == [(PUXADA_A, 4), (PUXADA_B, 4), (ROSCA, 4)]
    assert [(f["code"], f["muscle_group_id"], f["from"], f["to"]) for f in fixes] == [
        (vc.VOLUME_SETS_ADDED, BICEPS, 2, 4)
    ]
    depois = avaliar(tpl, data)
    assert depois[BICEPS].direct_sets_axis.status is mv.MethodologyVolumeStatus.WITHIN_RANGE


def test_ponderado_serie_no_composto_conta_no_secundario():
    """Rosca no ideal (4): a serie a mais vai para a puxada e cada uma vale o
    peso do cadastro no biceps (3+3 puxadas = 3 + 4 = 7 -> 8 com duas series)."""
    tpl = semana([eq(1, PUXADA_A, COSTAS, 3), eq(2, PUXADA_B, COSTAS, 3), eq(3, ROSCA, BICEPS, 4)])
    data = inputs(PONDERADO, classes(costas=(6, 9)))

    fixes = vc.complement_volume(tpl, data, [], {}).auto_fixes

    assert sets_of(tpl) == [(PUXADA_A, 4), (PUXADA_B, 4), (ROSCA, 4)]
    assert {(f["code"], f["item"]) for f in fixes} == {(vc.VOLUME_SETS_ADDED, 1), (vc.VOLUME_SETS_ADDED, 2)}
    depois = avaliar(tpl, data)
    assert depois[BICEPS].direct_sets_axis.value_per_session == 8
    assert depois[COSTAS].direct_sets_axis.value_per_session == 8


def test_ponderado_prefere_o_item_em_que_o_grupo_e_primario():
    """Empate em series (puxada A e rosca com 3): a rosca (PRIMARY do biceps)
    recebe a serie antes da puxada, que so soma a fracao."""
    tpl = semana([eq(1, PUXADA_A, COSTAS, 3), eq(2, PUXADA_B, COSTAS, 4), eq(3, ROSCA, BICEPS, 3)])
    data = inputs(PONDERADO, classes(costas=(6, 9)))

    fixes = vc.complement_volume(tpl, data, [], {}).auto_fixes

    assert [f["item"] for f in fixes] == [3, 1]
    assert sets_of(tpl) == [(PUXADA_A, 4), (PUXADA_B, 4), (ROSCA, 4)]


def test_ponderado_trava_nenhum_grupo_passa_a_above_pelo_composto():
    """Costas no teto (6-6): somar na puxada empurraria costas a ABOVE, entao
    o complemento acrescenta um exercicio novo de biceps."""
    tpl = semana([eq(1, PUXADA_A, COSTAS, 3), eq(2, PUXADA_B, COSTAS, 3), eq(3, ROSCA, BICEPS, 4)])
    data = inputs(PONDERADO, classes(costas=(6, 6)))

    fixes = vc.complement_volume(tpl, data, [pair(ROSCA_NOVA, BICEPS)], {}).auto_fixes

    assert sets_of(tpl)[:3] == [(PUXADA_A, 3), (PUXADA_B, 3), (ROSCA, 4)]
    assert [f["code"] for f in fixes] == [vc.VOLUME_EXERCISE_ADDED]
    depois = avaliar(tpl, data)
    assert all(g.direct_sets_axis.status is not mv.MethodologyVolumeStatus.ABOVE_RANGE for g in depois.values())


def test_primary_only_com_linhas_cruas_e_identico_a_forma_de_conjunto():
    """Paridade: em primary_only as linhas do cadastro (com secundario e
    estabilizador) dao o MESMO complemento que o conjunto de PRIMARY da v4."""
    itens = [eq(1, PUXADA_A, COSTAS, 4), eq(2, PUXADA_B, COSTAS, 4), eq(3, ROSCA, BICEPS, 2)]
    gym = [pair(ROSCA_NOVA, BICEPS)]
    tpl_linhas, tpl_conjunto = semana(itens), semana(itens)
    conjunto = svc.MethodologyInputs(
        canonical_ids=dict(CANONICAL),
        contributions={PUXADA_A: frozenset({COSTAS}), PUXADA_B: frozenset({COSTAS}), ROSCA: frozenset({BICEPS}), ROSCA_NOVA: frozenset({BICEPS})},
        groups=GROUPS,
        volume_classes=classes(),
    )
    fixes_linhas = vc.complement_volume(tpl_linhas, inputs(SO_PRIMARIO), gym, {}).auto_fixes
    fixes_conjunto = vc.complement_volume(tpl_conjunto, conjunto, gym, {}).auto_fixes

    assert fixes_linhas == fixes_conjunto
    assert tpl_linhas.model_dump() == tpl_conjunto.model_dump()
    # E o secundario da puxada nao conta: biceps sai de 2.
    assert fixes_linhas[0]["deficit_before"] == 6


# ---------------------------------------------------------------------------
# 5. Validador
# ---------------------------------------------------------------------------


def _avisos(template, data, mode):
    report = validation.ValidationReport()
    validation._check_methodology(template, report, data, mode)
    return {(w["code"], w.get("muscle_group_id")) for w in report.warnings}


def test_check_methodology_avalia_no_modo_do_pedido():
    tpl = semana([eq(1, PUXADA_A, COSTAS, 4), eq(2, PUXADA_B, COSTAS, 4), eq(3, ROSCA, BICEPS, 4)])
    # primary_only: biceps 4 (BELOW 8); ponderado: 4 + 8 x peso = 8 (WITHIN).
    assert (validation.METHODOLOGY_BELOW_RANGE, BICEPS) in _avisos(tpl, inputs(SO_PRIMARIO), SO_PRIMARIO)
    assert (validation.METHODOLOGY_BELOW_RANGE, BICEPS) not in _avisos(tpl, inputs(PONDERADO), PONDERADO)


def test_check_methodology_sem_leituras_le_no_modo(monkeypatch):
    modos = []
    monkeypatch.setattr(svc, "load_methodology_inputs", lambda ids, mode: modos.append(mode) or inputs(mode))
    tpl = semana([eq(1, ROSCA, BICEPS, 4)])
    validation._check_methodology(tpl, validation.ValidationReport(), None, PONDERADO)
    validation._check_methodology(tpl, validation.ValidationReport())
    assert modos == [PONDERADO, DEFAULT_VOLUME_COUNTING_MODE]


def _item_llm(**overrides):
    base = {
        "item_kind": "equipment", "muscle_group_id": None, "mapping_id": None, "equipment_id": None,
        "priority_min_pct": None, "priority_max_pct": None, "sets_count": None, "reps_target": None,
        "rpe_target": None, "prioritize": False, "advanced_technique": None,
        "advanced_technique_applications": None, "advanced_technique_duration_seconds": None,
        "warmup_sets_count": None, "warmup_reps": None, "warmup_rest_seconds": None,
        "feeler_set_sets_count": None, "feeler_set_reps": None, "feeler_set_rest_seconds": None,
        "peak_contraction_seconds": None,
    }
    base.update(overrides)
    return base


def _saida_llm():
    itens = [
        _item_llm(muscle_group_id=g, mapping_id=m, equipment_id=900 + m, sets_count=s, reps_target="8-12", rpe_target="8")
        for m, g, s in ((PUXADA_A, COSTAS, 4), (PUXADA_B, COSTAS, 4), (ROSCA, BICEPS, 4))
    ]
    return {
        "template": {
            "name": "Esboço",
            "description": None,
            "volume_mode": "equipment_series",
            "slots": [
                {"sequence": 1, "name": "Costas e bíceps", "slot_type": "normal", "weekday": None, "items": itens},
                *[{"sequence": s, "name": "Descanso", "slot_type": "descanso", "weekday": None, "items": []} for s in range(2, 8)],
            ],
        },
        "rationale": "teste",
        "source_citations": [],
    }


@pytest.fixture
def validacao_sem_banco(monkeypatch):
    modos = []

    def fake_inputs(mapping_ids, mode):
        modos.append(mode)
        return inputs(mode)

    monkeypatch.setattr(svc, "load_methodology_inputs", fake_inputs)
    monkeypatch.setattr(validation, "_check_references", lambda *a, **k: [])
    monkeypatch.setattr(validation, "_check_template_rules", lambda *a, **k: None)
    monkeypatch.setattr(validation, "_apply_safety_auto_fixes", lambda *a, **k: {})
    monkeypatch.setattr(validation, "_check_activation", lambda *a, **k: None)
    monkeypatch.setattr(ts, "resolve_training_safety_bulk", lambda conn, pairs: {})
    return modos


def test_validate_draft_ponderado_le_no_modo_e_o_rascunho_leva_o_modo(validacao_sem_banco):
    pedido = PeriodizationDraftIn(
        volume_mode="equipment_series", gym_id=7, days_per_cycle=7, volume_counting_mode=PONDERADO.value
    )
    draft, report, _ = validation.validate_draft(object(), pedido, _saida_llm())

    assert validacao_sem_banco == [PONDERADO]  # uma leitura so, no modo do pedido
    assert draft["volume_counting_mode"] == PONDERADO.value
    assert (validation.METHODOLOGY_BELOW_RANGE, BICEPS) not in {
        (w["code"], w.get("muscle_group_id")) for w in report["warnings"]
    }


def test_validate_draft_sem_modo_e_primary_only_e_rascunho_sem_modo(validacao_sem_banco):
    pedido = PeriodizationDraftIn(volume_mode="equipment_series", gym_id=7, days_per_cycle=7)
    draft, report, _ = validation.validate_draft(object(), pedido, _saida_llm())

    assert validacao_sem_banco == [DEFAULT_VOLUME_COUNTING_MODE]
    assert draft["volume_counting_mode"] is None  # o default na criacao, como antes
    assert (validation.METHODOLOGY_BELOW_RANGE, BICEPS) in {
        (w["code"], w.get("muscle_group_id")) for w in report["warnings"]
    }
