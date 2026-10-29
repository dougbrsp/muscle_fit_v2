"""Duas regras do validador da Periodizacao (eval pago de 2026-10-16, prompt v11):

1. `MUSCLE_GROUP_RELABELED`: par ATIVO da academia rotulado com outro grupo
   (pai/irmao do bloco) volta para o grupo sob o qual o catalogo o lista - sem
   mudar nenhum numero da metodologia;
2. `VOLUME_REDUCED`: depois do acrescimo, grupo ACIMA da faixa de series perde
   series (piso = minimo por exercicio da metodologia, nunca remove item),
   sem empurrar nenhum outro eixo para baixo/acima da faixa, priorizados por
   ultimo e respeitando a intencao do motor.

Casos sinteticos no MOTOR REAL (`evaluate_methodology_volume`) + as respostas
REAIS (4 casos do Modo B x 5 rodadas) com as leituras do DEV congeladas
(`tests/support/periodizacao_respostas_reais.py`). Nenhuma chamada a OpenAI.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from app.models import WorkoutPlanTemplateIn
from app.periodization import validation
from app.periodization import volume_complement as vc
from tests.support import periodizacao_respostas_reais as real

# ---------------------------------------------------------------------------
# Cenario sintetico
# ---------------------------------------------------------------------------

PEITO, TRICEPS, COSTAS, BICEPS, PEITO_INF = 1, 2, 3, 4, 5
GRANDES, PEQUENOS = 10, 20
FLOOR = vc._sets_floor_for_reduction()

GROUPS = (
    mv.MethodologyGroupInput(id=PEITO, name="Peitoral", volume_class_id=GRANDES),
    mv.MethodologyGroupInput(id=TRICEPS, name="Tríceps", volume_class_id=PEQUENOS),
    mv.MethodologyGroupInput(id=COSTAS, name="Costas", volume_class_id=GRANDES),
    mv.MethodologyGroupInput(id=BICEPS, name="Bíceps", volume_class_id=PEQUENOS),
    mv.MethodologyGroupInput(id=PEITO_INF, name="Peitoral inferior", volume_class_id=GRANDES, methodology_parent_group_id=PEITO),
)

PRIMARY = {100: {PEITO}, 110: {PEITO}, 113: {PEITO}, 114: {PEITO}, 115: {PEITO_INF}, 201: {PEITO, TRICEPS}, 210: {TRICEPS}, 211: {TRICEPS}}


def classes(peito=(9, 12), peq=(6, 8)):
    def spec(cid, name, rng):
        return mv.VolumeClassSpec(
            id=cid,
            name=name,
            ranges={"1x": mv.MethodologyRange(sets_min=rng[0], sets_max=rng[1], sets_source=mv.RangeSource.ADMIN)},
        )

    return {GRANDES: spec(GRANDES, "Grandes", peito), PEQUENOS: spec(PEQUENOS, "Pequenos", peq)}


def inputs(volume_classes=None, contributions=None, mode=mv.VolumeCountingMode.PRIMARY_ONLY) -> svc.MethodologyInputs:
    contrib = contributions if contributions is not None else {m: frozenset(g) for m, g in PRIMARY.items()}
    return svc.MethodologyInputs(
        canonical_ids={m: 1000 + m for m in contrib},
        contributions=contrib,
        groups=GROUPS,
        volume_classes=volume_classes if volume_classes is not None else classes(),
        counting_mode=mode,
    )


def eq(order: int, mapping_id: int, group: int, sets: int, **extra) -> dict:
    item = {
        "item_order": order, "item_kind": "equipment", "muscle_group_id": group, "mapping_id": mapping_id,
        "equipment_id": 900 + mapping_id, "sets_count": sets, "reps_target": "8-12", "rpe_target": "8",
    }
    item.update(extra)
    return item


def week(items: list[dict]) -> WorkoutPlanTemplateIn:
    slots = [{"sequence": 1, "name": "Dia 1", "slot_type": "normal", "items": items}]
    slots += [{"sequence": s, "name": f"Descanso {s}", "slot_type": "descanso", "items": []} for s in range(2, 8)]
    return WorkoutPlanTemplateIn(name="Rascunho", volume_mode="equipment_series", slots=slots)


def run(tpl, data=None, intent=None):
    return vc.complement_volume(tpl, data or inputs(), [], {}, catalog={}, intent=intent).auto_fixes


def evaluate(tpl, data=None):
    data = data or inputs()
    ev = mv.evaluate_methodology_volume(
        svc.build_slot_inputs(tpl.slots, "equipment_series", data.canonical_ids, {}),
        data.contributions, data.groups, "equipment_series", None, data.volume_classes, data.counting_mode,
    )
    return {g.muscle_group_id: g for g in ev.groups}


def sets_of(tpl) -> list[tuple[int, int]]:
    return [(i.mapping_id, i.sets_count) for i in tpl.slots[0].items]


# ---------------------------------------------------------------------------
# Regra 1 - MUSCLE_GROUP_RELABELED
# ---------------------------------------------------------------------------


NAMES = {g.id: g.name for g in GROUPS}


def _row(mapping_id, equipment_id, group):
    return {"mapping_id": mapping_id, "equipment_id": equipment_id, "muscle_group_id": group, "muscle_group_name": NAMES[group],
            "exercise_name": f"Exercício {mapping_id}", "equipment_name": f"Equipamento {equipment_id}"}


def test_relabel_par_ativo_com_grupo_pai_volta_para_o_grupo_do_catalogo():
    tpl = week([eq(1, 115, PEITO, 4), eq(2, 100, PEITO, 4)])
    s = tpl.slots[0]
    report = validation.ValidationReport()
    rows = [_row(115, 1015, PEITO_INF), _row(100, 1000, PEITO)]
    validation._replace_equipment_pairs([(s, i) for i in s.items], rows, type("R", (), {"gym_id": 7})(), report, NAMES)

    assert [i.muscle_group_id for i in s.items] == [PEITO_INF, PEITO]
    assert report.errors == [] and report.warnings == []
    assert report.auto_fixes == [{
        "code": "MUSCLE_GROUP_RELABELED", "slot": 1, "slot_name": "Dia 1", "item": 1, "mapping_id": 115,
        "equipment_id": 1015, "exercise_name": "Exercício 115", "equipment_name": "Equipamento 1015",
        "from": PEITO, "to": PEITO_INF, "from_name": "Peitoral", "to_name": "Peitoral inferior",
    }]


def test_relabel_nao_mexe_em_par_fora_da_academia_que_segue_a_troca_de_equipamento():
    tpl = week([eq(1, 100, PEITO, 4, equipment_id=555)])
    s = tpl.slots[0]
    report = validation.ValidationReport()
    validation._replace_equipment_pairs([(s, i) for i in s.items], [_row(100, 1000, PEITO)], type("R", (), {"gym_id": 7})(), report)
    assert [f["code"] for f in report.auto_fixes] == ["EQUIPMENT_REPLACED"]
    assert s.items[0].muscle_group_id == PEITO


def test_relabel_nao_muda_numero_da_metodologia_no_modo_b():
    antes = week([eq(1, 115, PEITO, 4), eq(2, 100, PEITO, 4)])
    depois = antes.model_copy(deep=True)
    depois.slots[0].items[0].muscle_group_id = PEITO_INF
    assert evaluate(antes) == evaluate(depois)


def test_codigos_reexportados():
    assert validation.MUSCLE_GROUP_RELABELED == "MUSCLE_GROUP_RELABELED"
    assert validation.VOLUME_REDUCED == vc.VOLUME_REDUCED == "VOLUME_REDUCED"


# ---------------------------------------------------------------------------
# Regra 2 - VOLUME_REDUCED (sintetico)
# ---------------------------------------------------------------------------


def test_corte_tira_series_do_item_com_mais_series_ate_o_teto():
    tpl = week([eq(1, 100, PEITO, 6), eq(2, 110, PEITO, 5), eq(3, 113, PEITO, 4)])  # 15, faixa 9-12
    fixes = run(tpl)

    assert sets_of(tpl) == [(100, 4), (110, 4), (113, 4)]
    assert evaluate(tpl)[PEITO].direct_sets_axis.status is mv.MethodologyVolumeStatus.WITHIN_RANGE
    assert [(f["code"], f["item"], f["from"], f["to"]) for f in fixes] == [
        (vc.VOLUME_REDUCED, 1, 6, 4), (vc.VOLUME_REDUCED, 2, 5, 4),
    ]
    assert (fixes[0]["axis"], fixes[0]["max"], fixes[0]["excess_before"], fixes[0]["min_sets"]) == ("session", 12, 3, FLOOR)
    assert fixes[-1]["excess_after"] == 0


def test_corte_respeita_o_piso_e_nunca_remove_item():
    tpl = week([eq(i, m, PEITO, FLOOR + 1) for i, m in enumerate((100, 110, 113, 114), start=1)] + [eq(5, 115, PEITO_INF, FLOOR)])
    fixes = run(tpl, data=inputs(classes(peito=(5, 6))))

    assert sets_of(tpl) == [(100, FLOOR), (110, FLOOR), (113, FLOOR), (114, FLOOR), (115, FLOOR)]
    assert len([f for f in fixes if f["code"] == vc.VOLUME_REDUCED]) == 4
    assert evaluate(tpl, inputs(classes(peito=(5, 6))))[PEITO].direct_sets_axis.status is mv.MethodologyVolumeStatus.ABOVE_RANGE


def test_trava_corte_nao_derruba_outro_grupo_de_within_para_below():
    # Peito 15 (9-12) acima; o unico item cortavel e o 201 (peito+triceps), e
    # triceps esta no minimo (6): cortar o 201 o poria abaixo.
    tpl = week([eq(1, 201, PEITO, 6), eq(2, 100, PEITO, FLOOR), eq(3, 110, PEITO, FLOOR), eq(4, 113, PEITO, FLOOR)])
    fixes = run(tpl)
    assert sets_of(tpl)[0] == (201, 6)
    assert not [f for f in fixes if f["code"] == vc.VOLUME_REDUCED]


def test_priorizado_so_e_cortado_sem_alternativa():
    tpl = week([eq(1, 100, PEITO, 5, prioritize=True), eq(2, 110, PEITO, 5), eq(3, 113, PEITO, 3)])  # 13 -> 12
    run(tpl)
    assert sets_of(tpl) == [(100, 5), (110, 4), (113, 3)]

    # 14 -> 12: o nao priorizado desce ate o piso; so entao o priorizado cede.
    tpl = week([eq(1, 100, PEITO, 7, prioritize=True), eq(2, 110, PEITO, FLOOR + 1), eq(3, 113, PEITO, FLOOR)])
    fixes = run(tpl)
    assert sets_of(tpl) == [(100, 6), (110, FLOOR), (113, FLOOR)]
    assert [(f["item"], f["prioritized"]) for f in fixes] == [(2, False), (1, True)]


def test_intencao_que_espera_o_excesso_nao_corta():
    tpl = week([eq(1, 100, PEITO, 6), eq(2, 110, PEITO, 6), eq(3, 113, PEITO, 4)])
    intent = mv.MethodologyIntent(kind="specialization", target_group_ids=frozenset({PEITO}))
    assert run(tpl, intent=intent) == []
    assert sets_of(tpl) == [(100, 6), (110, 6), (113, 4)]


def test_corte_termina_e_e_idempotente():
    tpl = week([eq(i, m, PEITO, 6) for i, m in enumerate((100, 110, 113, 114), start=1)])  # 24 contra 12
    run(tpl)
    assert sets_of(tpl) == [(100, FLOOR), (110, FLOOR), (113, FLOOR), (114, FLOOR)]
    again = tpl.model_copy(deep=True)
    assert run(again) == [] and sets_of(again) == sets_of(tpl)


def test_corte_nao_desfaz_o_acrescimo():
    # Triceps 3 (6-8) abaixo: o acrescimo soma no 210. Peito acima nao corta
    # o que o acrescimo tocou (e o acrescimo nunca toca item de grupo acima).
    tpl = week([eq(1, 100, PEITO, 7), eq(2, 110, PEITO, 7), eq(3, 210, TRICEPS, 3)])
    fixes = run(tpl)
    codes = [f["code"] for f in fixes]
    assert codes.index(vc.VOLUME_SETS_ADDED) < codes.index(vc.VOLUME_REDUCED)
    added = {(f["slot"], f["item"]) for f in fixes if f["code"] != vc.VOLUME_REDUCED}
    cut = {(f["slot"], f["item"]) for f in fixes if f["code"] == vc.VOLUME_REDUCED}
    assert not added & cut
    g = evaluate(tpl)
    assert g[PEITO].direct_sets_axis.status is mv.MethodologyVolumeStatus.WITHIN_RANGE
    assert g[TRICEPS].direct_sets_axis.status is mv.MethodologyVolumeStatus.WITHIN_RANGE


def test_ponderado_secundario_so_e_cortado_se_nao_quebrar_o_primario():
    # Remada (300): PRIMARY costas, SECONDARY biceps 0,5. Biceps acima (rosca no
    # piso + fracao da remada); costas no minimo da faixa -> a remada nao corta.
    contrib = {
        300: (mv.ContributionRow(COSTAS, "PRIMARY", 1.0), mv.ContributionRow(BICEPS, "SECONDARY", 0.5)),
        310: (mv.ContributionRow(COSTAS, "PRIMARY", 1.0), mv.ContributionRow(BICEPS, "SECONDARY", 0.5)),
        400: (mv.ContributionRow(BICEPS, "PRIMARY", 1.0),),
        401: (mv.ContributionRow(BICEPS, "PRIMARY", 1.0),),
    }
    data = inputs(classes(peito=(10, 12), peq=(6, 8)), contrib, mv.VolumeCountingMode.PRIMARY_SECONDARY)
    tpl = week([eq(1, 300, COSTAS, 5), eq(2, 310, COSTAS, 5), eq(3, 400, BICEPS, FLOOR), eq(4, 401, BICEPS, FLOOR)])
    before = evaluate(tpl, data)
    assert before[BICEPS].direct_sets_axis.status is mv.MethodologyVolumeStatus.ABOVE_RANGE  # 6 + 5 = 11
    run(tpl, data=data)
    assert sets_of(tpl) == [(300, 5), (310, 5), (400, FLOOR), (401, FLOOR)]

    # Com folga em costas (faixa 8-12), a remada pode ceder ate costas no minimo.
    data = inputs(classes(peito=(8, 12), peq=(6, 8)), contrib, mv.VolumeCountingMode.PRIMARY_SECONDARY)
    tpl = week([eq(1, 300, COSTAS, 5), eq(2, 310, COSTAS, 5), eq(3, 400, BICEPS, FLOOR), eq(4, 401, BICEPS, FLOOR)])
    fixes = run(tpl, data=data)
    after = evaluate(tpl, data)
    assert after[COSTAS].direct_sets_axis.status is mv.MethodologyVolumeStatus.WITHIN_RANGE
    assert sum(s for _m, s in sets_of(tpl)[:2]) == 8
    assert all(not f["is_primary"] for f in fixes)


# ---------------------------------------------------------------------------
# Respostas reais (4 casos do Modo B x 5 rodadas)
# ---------------------------------------------------------------------------

REAL = [(c, r) for c in real.CASES_MODO_B for r in real.ROUNDS]


_RUNS: dict = {}


def _run(case_id, round_, monkeypatch, *, relabel=True, reduce=True):
    """`validate_draft` e deterministico: cada combinacao roda uma vez so."""
    key = (case_id, round_, relabel, reduce)
    if key not in _RUNS:
        _RUNS[key] = _run_once(case_id, round_, monkeypatch, relabel=relabel, reduce=reduce)
    return _RUNS[key]


def _run_once(case_id, round_, monkeypatch, *, relabel, reduce):
    with monkeypatch.context() as mp:
        if not relabel:
            mp.setattr(validation, "_relabel_muscle_group", lambda *a, **k: None)
        if not reduce:
            mp.setattr(vc._Complement, "_reduce_volume", lambda self: None)
        return real.run_case(case_id, mp, round_)


def _axes_state(groups):
    return {
        (gid, name): (vc._deficit(axis), vc._excess(axis))
        for gid, g in groups.items()
        for name, axis in vc._axes(g).items()
    }


def test_campos_opcionais_com_minimo_1_vem_do_schema():
    from app.models import WorkoutPlanTemplateSlotItemIn

    campos = validation.OPTIONAL_POSITIVE_ITEM_FIELDS
    assert {"warmup_sets_count", "warmup_reps", "feeler_set_sets_count", "feeler_set_reps"} <= set(campos)
    # sets_count tem conserto proprio (SETS_COUNT_FIXED); rest_seconds e sempre zerado.
    assert "sets_count" not in campos and "rest_seconds" not in campos
    for name in campos:
        with pytest.raises(ValidationError):
            WorkoutPlanTemplateSlotItemIn(item_order=1, item_kind="equipment", **{name: 0})


def test_real_zero_em_campo_opcional_vira_null_e_o_rascunho_passa(monkeypatch):
    """Rodada 3 do caso de 4 dias: a LLM mandou 0 (e nao null) em warmup/feeler.
    Antes o `WorkoutPlanTemplateIn` recusava e o `ValidationError` escapava de
    `validate_draft` (job em excecao depois da geracao paga)."""
    draft, report, _ = _run("modo_b_4_dias_instrucoes", 3, monkeypatch)
    assert report["errors"] == []
    zerados = [f for f in report["auto_fixes"] if f["code"] == "OPTIONAL_ZERO_NULLED"]
    assert zerados and {"warmup_sets_count", "warmup_reps", "feeler_set_sets_count", "feeler_set_reps"} <= {
        name for f in zerados for name in f["fields"]
    }
    for s in draft["slots"]:
        for i in s["items"]:
            assert all(i[name] != 0 for name in validation.OPTIONAL_POSITIVE_ITEM_FIELDS)


def test_validation_error_depois_do_schema_vira_erro_do_relatorio(monkeypatch):
    """Valor sem conserto deterministico (negativo) nao escapa como excecao:
    vira `SCHEMA_INVALID` no relatorio, sem rascunho."""
    resp = real.response("modo_b_4_dias_instrucoes", 0)
    resp["template"]["slots"][0]["items"][0]["warmup_reps"] = -2
    real.patch_db(monkeypatch)
    draft, report, extras = validation.validate_draft(real._Conn(), real.request("modo_b_4_dias_instrucoes"), resp)
    assert draft is None
    assert [e["code"] for e in report["errors"]] == ["SCHEMA_INVALID"]
    assert "warmup_reps" in report["errors"][0]["message"]
    assert extras["rationale"] == resp["rationale"]


def test_corte_tem_teto_rigido_de_avaliacoes_e_orcamento_proprio(monkeypatch):
    tpl = week([eq(i, m, PEITO, 6) for i, m in enumerate((100, 110, 113, 114), start=1)])  # 24 contra 12
    monkeypatch.setattr(vc, "COMPLEMENT_REDUCTION_MAX_EVALUATIONS", 3)
    monkeypatch.setattr(vc, "MAX_STEPS", 0)  # acrescimo sem orcamento nenhum: o corte roda igual
    calls = []
    original = vc._Complement._measure

    def counted(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(vc._Complement, "_measure", counted)
    fixes = run(tpl)
    assert len(calls) == 1 + 3  # leitura inicial + 3 avaliacoes do corte
    assert sum(f["from"] - f["to"] for f in fixes if f["code"] == vc.VOLUME_REDUCED) == 3
    assert evaluate(tpl)[PEITO].direct_sets_axis.status is mv.MethodologyVolumeStatus.ABOVE_RANGE


@pytest.mark.parametrize("case_id,round_", REAL)
def test_real_rotulo_fica_o_do_catalogo_e_nao_muda_numero(case_id, round_, monkeypatch):
    draft, report, groups = _run(case_id, round_, monkeypatch)
    listed = {(p["mapping_id"], p["equipment_id"]): p["muscle_group_id"] for p in real.gym_pairs(real.request(case_id).gym_id)}
    for s in draft["slots"]:
        for i in s["items"]:
            assert i["muscle_group_id"] == listed[(i["mapping_id"], i["equipment_id"])]

    sem_draft, sem_report, sem_groups = _run(case_id, round_, monkeypatch, relabel=False)
    assert groups == sem_groups
    assert report["warnings"] == sem_report["warnings"]
    assert [f for f in report["auto_fixes"] if f["code"] != "MUSCLE_GROUP_RELABELED"] == sem_report["auto_fixes"]


@pytest.mark.parametrize("case_id,round_", REAL)
def test_real_corte_so_reduz_excesso_e_nao_cria_deficit(case_id, round_, monkeypatch):
    draft, report, groups = _run(case_id, round_, monkeypatch)
    _sem_draft, _sem_report, sem_groups = _run(case_id, round_, monkeypatch, reduce=False)

    depois, antes = _axes_state(groups), _axes_state(sem_groups)
    for key in antes:
        assert depois[key][0] <= antes[key][0] + 1e-6, key  # nenhum eixo mais abaixo
        assert depois[key][1] <= antes[key][1] + 1e-6, key  # nenhum eixo mais acima
    cuts = [f for f in report["auto_fixes"] if f["code"] == "VOLUME_REDUCED"]
    assert all(f["to"] >= f["min_sets"] == FLOOR and f["to"] < f["from"] for f in cuts)
    assert [len(s["items"]) for s in draft["slots"]] == [len(s["items"]) for s in _sem_draft["slots"]]


def test_real_impacto_medido(monkeypatch):
    """A medicao do PR (5 rodadas): o corte so tira ABOVE e nunca soma BELOW."""
    totals = {"antes": {}, "depois": {}}
    for case_id, round_ in REAL:
        for label, reduce in (("antes", False), ("depois", True)):
            _d, _r, groups = _run(case_id, round_, monkeypatch, reduce=reduce)
            for k, v in real.status_counts(groups).items():
                totals[label][k] = totals[label].get(k, 0) + v
    # Os numeros da tabela do FONTES_DA_VERDADE §43 (20 respostas).
    assert totals["antes"] == {"session_above": 65, "session_below": 33, "weekly_above": 70, "weekly_below": 32}
    assert totals["depois"] == {"session_above": 28, "session_below": 33, "weekly_above": 34, "weekly_below": 32}


def test_real_auto_fixes_estruturais_cobrem_7_slots_e_priorizados_acima_do_teto(monkeypatch):
    """Nas 5 rodadas: 1x 7 slots no ponderado (pedido 6) e 3x mais de 3
    priorizados no slot - os auto-fixes existentes consertam, sem erro."""
    from app.periodization.limits import MAX_PRIORITIZED_PER_SLOT

    draft, report, _ = _run("modo_b_6_dias_abc_ponderado", 1, monkeypatch)
    assert len(real.response("modo_b_6_dias_abc_ponderado", 1)["template"]["slots"]) == 7
    assert len(draft["slots"]) == 6 and report["errors"] == []
    assert [f["code"] for f in report["auto_fixes"]].count("SLOT_REMOVED") == 1

    for case_id, round_ in (("modo_b_6_dias_abc", 2), ("modo_b_6_dias_abc_ponderado", 4), ("modo_b_5_dias_sem_perfil", 2)):
        draft, report, _ = _run(case_id, round_, monkeypatch)
        assert report["errors"] == []
        assert any(f["code"] == "PRIORITIZE_UNMARKED" for f in report["auto_fixes"])
        assert all(sum(1 for i in s["items"] if i["prioritize"]) <= MAX_PRIORITIZED_PER_SLOT for s in draft["slots"])
