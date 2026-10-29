"""M46 - complemento deterministico de volume do rascunho da Periodizacao.

Cada ramo da regra decidida pelo usuario, no MOTOR REAL da metodologia
(`evaluate_methodology_volume`), com um cadastro de macro-grupos montado aqui
(numeros pequenos e redondos, para a conta caber de cabeca):

- (a) serie a mais ate o ideal de 4 por exercicio;
- (b) exercicio novo do catalogo da academia, preferindo PRIMARY exclusivo,
  sem repetir exercicio do slot, com menos series se 3 passariam da faixa;
- (c) teto de 9 exercicios -> drop set (seguranca permite falha) ou, sem
  tecnica possivel, passar de 4 series;
- limite de 90 min pela estimativa do builder; nunca empurrar grupo para
  ABOVE_RANGE; janela de 7 dias; ordem sessao -> semanal; determinismo;
- `validate_draft` de ponta a ponta: uma leitura da metodologia so, reusada
  pela etapa de avisos, que ja enxerga o rascunho complementado.
"""

from __future__ import annotations

import pytest

from app.domain import training_safety as ts
from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as svc
from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn
from app.periodization import validation
from app.periodization import volume_complement as vc
from app.periodization.time_estimate import estimate_slot_seconds

# ---------------------------------------------------------------------------
# Cenario
# ---------------------------------------------------------------------------

PEITO, TRICEPS, BICEPS, PEITO_SUP, CORE = 1, 2, 3, 4, 9
GRANDES, PEQUENOS = 10, 20

GROUPS = (
    mv.MethodologyGroupInput(id=PEITO, name="Peitoral", volume_class_id=GRANDES),
    mv.MethodologyGroupInput(id=TRICEPS, name="Tríceps", volume_class_id=PEQUENOS),
    mv.MethodologyGroupInput(id=BICEPS, name="Bíceps", volume_class_id=PEQUENOS),
    mv.MethodologyGroupInput(id=PEITO_SUP, name="Peitoral superior", volume_class_id=GRANDES, methodology_parent_group_id=PEITO),
    mv.MethodologyGroupInput(id=CORE, name="Core", volume_class_id=None),  # sem macro-grupo: nunca avaliado
)

# mapping_id -> grupos PRIMARY.
PRIMARY = {
    100: {PEITO}, 110: {PEITO}, 113: {PEITO}, 114: {PEITO}, 115: {PEITO_SUP},
    200: {PEITO},  # candidato exclusivo
    201: {PEITO, TRICEPS},  # candidato que derrama em triceps
    204: {PEITO},
    210: {TRICEPS}, 211: {TRICEPS}, 212: {TRICEPS}, 213: {TRICEPS},
    300: {CORE}, 301: {CORE}, 302: {CORE}, 303: {CORE}, 304: {CORE}, 305: {CORE}, 306: {CORE}, 307: {CORE},
}
# 107 e o mesmo exercicio canonico do 100 em outra linha do catalogo.
CANONICAL = {m: 1000 + m for m in PRIMARY}
CANONICAL[107] = CANONICAL[100]
PRIMARY[107] = {PEITO}

CATALOG = {
    "drop_set": {"activation_set_equivalent": 0.5, "max_equivalent_per_anchor": 1.0, "default_sets_count": None},
    "rest_pause": {"activation_set_equivalent": 0.4, "max_equivalent_per_anchor": 1.0, "default_sets_count": None},
}


def classes(peito=(15, 20), weekly_peito=None, peq=(9, 16), weekly_peq=None, ex_peito=None):
    def spec(cid, name, rng, weekly, ex):
        return mv.VolumeClassSpec(
            id=cid,
            name=name,
            ranges={
                "1x": mv.MethodologyRange(
                    sets_min=rng[0],
                    sets_max=rng[1],
                    sets_source=mv.RangeSource.ADMIN,
                    exercises_min=ex[0] if ex else None,
                    exercises_max=ex[1] if ex else None,
                    exercises_source=mv.RangeSource.ADMIN if ex else None,
                )
            },
            weekly_sets_min=weekly[0] if weekly else None,
            weekly_sets_max=weekly[1] if weekly else None,
            weekly_source=mv.RangeSource.ADMIN if weekly else None,
        )

    return {GRANDES: spec(GRANDES, "Grandes", peito, weekly_peito, ex_peito), PEQUENOS: spec(PEQUENOS, "Pequenos", peq, weekly_peq, None)}


def inputs(volume_classes=None) -> svc.MethodologyInputs:
    return svc.MethodologyInputs(
        canonical_ids=dict(CANONICAL),
        contributions={m: frozenset(g) for m, g in PRIMARY.items()},
        groups=GROUPS,
        volume_classes=volume_classes if volume_classes is not None else classes(),
    )


def pair(mapping_id: int, group: int, equipment_id: int | None = None) -> dict:
    return {
        "mapping_id": mapping_id,
        "equipment_id": equipment_id or 900 + mapping_id,
        "muscle_group_id": group,
        "exercise_name": f"Exercício {mapping_id}",
        "equipment_name": f"Equipamento {equipment_id or 900 + mapping_id}",
    }


def safety(mapping_id: int, equipment_id: int, *, failure: bool = True) -> ts.TrainingSafety:
    allowed = tuple(ts.TechniquePermission(code, "equipment_default") for code in ("drop_set", "rest_pause"))
    return ts.TrainingSafety(
        exercise_id=mapping_id,
        equipment_id=equipment_id,
        equipment_exercise_id=None,
        max_safe_rpe=10 if failure else 9,
        failure_allowed=failure,
        safety_source=None,
        allowed_techniques=allowed,
        technique_failure_requirements={"drop_set": "requires_failure", "rest_pause": "requires_failure"},
    )


def eq(order: int, mapping_id: int, group: int, sets: int, **extra) -> dict:
    item = {
        "item_order": order,
        "item_kind": "equipment",
        "muscle_group_id": group,
        "mapping_id": mapping_id,
        "equipment_id": 900 + mapping_id,
        "sets_count": sets,
        "reps_target": "8-12",
        "rpe_target": "8",
    }
    item.update(extra)
    return item


def slot(seq: int, items: list[dict], slot_type: str = "normal") -> dict:
    return {"sequence": seq, "name": f"Dia {seq}", "slot_type": slot_type, "items": items}


def week(*training: dict, days: int = 7) -> list[dict]:
    slots = list(training)
    while len(slots) < days:
        slots.append(slot(len(slots) + 1, [], "descanso"))
    return slots


def template(slots: list[dict]) -> WorkoutPlanTemplateIn:
    return WorkoutPlanTemplateIn(name="Rascunho", volume_mode="equipment_series", slots=slots)


def run(tpl, *, gym=(), safeties=None, volume_classes=None):
    safeties = safeties if safeties is not None else {}
    result = vc.complement_volume(tpl, inputs(volume_classes), list(gym), safeties, catalog=CATALOG)
    return result.auto_fixes


def sets_of(tpl, seq: int) -> list[tuple[int, int | None]]:
    s = next(s for s in tpl.slots if s.sequence == seq)
    return [(i.mapping_id, i.sets_count) for i in s.items]


def evaluate(tpl, volume_classes=None):
    data = inputs(volume_classes)
    ev = mv.evaluate_methodology_volume(
        svc.build_slot_inputs(tpl.slots, "equipment_series", data.canonical_ids, CATALOG),
        data.contributions,
        data.groups,
        "equipment_series",
        None,
        data.volume_classes,
    )
    return {g.muscle_group_id: g for g in ev.groups}


# ---------------------------------------------------------------------------
# (a) series ate o ideal de 4
# ---------------------------------------------------------------------------


def test_a_soma_serie_no_exercicio_com_menos_series_ate_o_ideal():
    tpl = template(week(slot(1, [eq(1, 100, PEITO, 4), eq(2, 110, PEITO, 4), eq(3, 113, PEITO, 3), eq(4, 114, PEITO, 3)])))
    fixes = run(tpl)

    assert sets_of(tpl, 1) == [(100, 4), (110, 4), (113, 4), (114, 3)]  # 14 -> 15: so uma serie
    assert [f["code"] for f in fixes] == [vc.VOLUME_SETS_ADDED]
    fix = fixes[0]
    assert (fix["muscle_group_id"], fix["axis"], fix["slot"], fix["item"]) == (PEITO, "session", 1, 3)
    assert (fix["from"], fix["to"], fix["deficit_before"], fix["deficit_after"], fix["min"]) == (3, 4, 1, 0, 15)


def test_a_varias_series_no_mesmo_item_viram_uma_entrada_so():
    tpl = template(week(slot(1, [eq(1, 100, PEITO, 4), eq(2, 110, PEITO, 4), eq(3, 113, PEITO, 4), eq(4, 114, PEITO, 1)])))
    fixes = run(tpl)
    assert sets_of(tpl, 1)[-1] == (114, 3)
    assert [(f["code"], f["from"], f["to"], f["deficit_before"], f["deficit_after"]) for f in fixes] == [
        (vc.VOLUME_SETS_ADDED, 1, 3, 2, 0)
    ]


# ---------------------------------------------------------------------------
# (b) exercicio novo do catalogo da academia
# ---------------------------------------------------------------------------


def test_b_exercicio_novo_prefere_primary_exclusivo_e_nao_repete_o_do_slot():
    tpl = template(week(slot(1, [eq(1, 100, PEITO, 4), eq(2, 110, PEITO, 4), eq(3, 113, PEITO, 4)])))
    gym = [
        pair(100, PEITO, equipment_id=777),  # mesmo exercicio, outro equipamento: repete
        pair(107, PEITO),  # mesmo canonico do 100: repete
        pair(201, PEITO),  # derrama em triceps
        pair(200, PEITO),  # exclusivo
    ]
    fixes = run(tpl, gym=gym)

    assert sets_of(tpl, 1) == [(100, 4), (110, 4), (113, 4), (200, 3)]
    novo = tpl.slots[0].items[-1]
    assert (novo.item_order, novo.equipment_id, novo.reps_target, novo.rpe_target, novo.prioritize) == (4, 1100, "8-12", "8", False)
    assert novo.rest_seconds is None
    assert [f["code"] for f in fixes] == [vc.VOLUME_EXERCISE_ADDED]
    fix = fixes[0]
    assert (fix["mapping_id"], fix["sets"], fix["exclusive_primary"], fix["deficit_before"], fix["deficit_after"]) == (
        200, 3, True, 3, 0,
    )
    assert fix["exercise_name"] == "Exercício 200"


def test_b_usa_menos_series_quando_tres_passariam_da_faixa():
    tpl = template(week(slot(1, [eq(1, 100, PEITO, 4), eq(2, 110, PEITO, 4), eq(3, 113, PEITO, 4)])))
    fixes = run(tpl, gym=[pair(200, PEITO)], volume_classes=classes(peito=(13, 14)))
    assert sets_of(tpl, 1)[-1] == (200, 2)  # 12 + 3 = 15 > 14; 12 + 2 = 14
    assert fixes[0]["sets"] == 2


def test_b_series_extras_no_exercicio_novo_atualizam_a_entrada_dele():
    tpl = template(week(slot(1, [eq(1, 100, PEITO, 4), eq(2, 110, PEITO, 4)])))  # 8, min 15
    fixes = run(tpl, gym=[pair(200, PEITO)])
    # (b) 200 com 3 -> 11; (a) 200 vai a 4 -> 12; o unico par ja esta no slot e,
    # sem seguranca lida, nao ha tecnica: (c) passa de 4 series, um por vez.
    assert sets_of(tpl, 1) == [(100, 5), (110, 5), (200, 5)]
    assert [(f["code"], f["mapping_id"]) for f in fixes] == [
        (vc.VOLUME_EXERCISE_ADDED, 200),
        (vc.VOLUME_SETS_ABOVE_IDEAL, 100),
        (vc.VOLUME_SETS_ABOVE_IDEAL, 110),
        (vc.VOLUME_SETS_ABOVE_IDEAL, 200),
    ]
    assert fixes[0]["sets"] == 4 and fixes[0]["deficit_after"] == 3
    assert (fixes[-1]["from"], fixes[-1]["to"], fixes[-1]["deficit_after"]) == (4, 5, 0)


# ---------------------------------------------------------------------------
# (c) teto de 9 exercicios
# ---------------------------------------------------------------------------


def _nove_itens(peito_sets=4):
    peito = [eq(i, m, PEITO, peito_sets) for i, m in enumerate((100, 110, 113), start=1)]
    core = [eq(i, m, CORE, 3) for i, m in enumerate((300, 301, 302, 303, 304, 305), start=4)]
    return peito + core


def test_c_slot_com_9_exercicios_recebe_drop_set_em_vez_de_exercicio_novo():
    tpl = template(week(slot(1, _nove_itens())))  # peito 12, faixa 13-20
    seg = {(m, 900 + m): safety(m, 900 + m) for m in (100, 110, 113)}
    fixes = run(tpl, gym=[pair(200, PEITO)], safeties=seg, volume_classes=classes(peito=(13, 20)))

    assert len(tpl.slots[0].items) == 9  # teto respeitado
    primeiro = tpl.slots[0].items[0]
    assert (primeiro.advanced_technique, primeiro.advanced_technique_applications, primeiro.sets_count) == ("drop_set", 2, 4)
    assert [f["code"] for f in fixes] == [vc.VOLUME_TECHNIQUE_ADDED]
    fix = fixes[0]
    assert (fix["technique"], fix["applications"], fix["equivalent"], fix["deficit_before"], fix["deficit_after"]) == (
        "drop_set", 2, 1, 1, 0,
    )


def test_c_sem_tecnica_permitida_passa_de_4_series_e_registra_acima_do_ideal():
    tpl = template(week(slot(1, _nove_itens())))
    seg = {(m, 900 + m): safety(m, 900 + m, failure=False) for m in (100, 110, 113)}  # teto RPE 9: sem falha
    fixes = run(tpl, gym=[pair(200, PEITO)], safeties=seg, volume_classes=classes(peito=(13, 20)))

    assert sets_of(tpl, 1)[:3] == [(100, 5), (110, 4), (113, 4)]
    assert tpl.slots[0].items[0].advanced_technique is None
    assert [(f["code"], f["from"], f["to"], f["ideal"]) for f in fixes] == [(vc.VOLUME_SETS_ABOVE_IDEAL, 4, 5, 4)]


# ---------------------------------------------------------------------------
# Limite de 90 min, faixa e janela
# ---------------------------------------------------------------------------


def test_limite_de_90_min_bloqueia_e_o_deficit_fica_registrado():
    # 4 x (10 reps x 3 s + 600 s) = 2520 s de supino + 48 min de cardio = 5400 s.
    itens = [
        eq(1, 100, PEITO, 4, reps_target="10", rest_seconds=600),
        {"item_order": 2, "item_kind": "cardio", "cardio_duration_minutes": 48, "sets_count": None},
    ]
    tpl = template(week(slot(1, itens)))
    assert estimate_slot_seconds(tpl.slots[0].items) == vc.SLOT_TIME_LIMIT_SECONDS
    seg = {(100, 1000): safety(100, 1000, failure=False)}
    fixes = run(tpl, gym=[pair(200, PEITO)], safeties=seg)

    assert sets_of(tpl, 1) == [(100, 4), (None, None)]
    assert estimate_slot_seconds(tpl.slots[0].items) == vc.SLOT_TIME_LIMIT_SECONDS
    assert [f["code"] for f in fixes] == [vc.VOLUME_GAP_UNRESOLVED]
    gap = fixes[0]
    assert gap["axis"] == "session" and gap["slots"] == [1]
    assert {vc.REASON_TIME_LIMIT, vc.REASON_NO_TECHNIQUE} <= set(gap["reasons"])
    assert (gap["value_before"], gap["value_after"], gap["min"]) == (4, 4, 15)


def test_nunca_empurra_outro_grupo_para_acima_da_faixa():
    # Triceps ja no maximo (16 = 4 x 4); o unico par do catalogo e o que
    # derrama em triceps -> recusado; sem tecnica, o peito passa de 4 series.
    itens = [eq(1, 100, PEITO, 4), eq(2, 110, PEITO, 4), eq(3, 113, PEITO, 4)] + [
        eq(i, m, TRICEPS, 4) for i, m in enumerate((210, 211, 212, 213), start=4)
    ]
    tpl = template(week(slot(1, itens)))
    fixes = run(tpl, gym=[pair(201, PEITO)])

    assert all(f.get("mapping_id") != 201 for f in fixes)
    assert sum(s for m, s in sets_of(tpl, 1) if m in (210, 211, 212, 213)) == 16
    grupos = evaluate(tpl)
    assert grupos[TRICEPS].direct_sets_axis.status is mv.MethodologyVolumeStatus.WITHIN_RANGE
    assert grupos[PEITO].direct_sets_axis.status is mv.MethodologyVolumeStatus.WITHIN_RANGE
    assert {f["code"] for f in fixes} == {vc.VOLUME_SETS_ABOVE_IDEAL}


def test_nunca_passa_do_maximo_da_faixa_do_proprio_grupo_nem_de_exercicios():
    # Faixa de exercicios 4-4: o exercicio novo deixaria o peito ACIMA no eixo
    # de exercicios (com 3, 2 ou 1 serie) -> recusado; vai para serie acima do ideal.
    faixa = classes(peito=(17, 18), ex_peito=(4, 4))
    tpl = template(week(slot(1, [eq(i, m, PEITO, 4) for i, m in enumerate((100, 110, 113, 114), start=1)])))
    fixes = run(tpl, gym=[pair(200, PEITO)], volume_classes=faixa)
    assert len(tpl.slots[0].items) == 4
    grupos = evaluate(tpl, faixa)
    for axis in (grupos[PEITO].direct_sets_axis, grupos[PEITO].direct_exercises_axis):
        assert axis.status is mv.MethodologyVolumeStatus.WITHIN_RANGE
    assert [(f["code"], f["mapping_id"], f["to"]) for f in fixes] == [(vc.VOLUME_SETS_ABOVE_IDEAL, 100, 5)]


def test_so_mexe_em_slot_dentro_da_janela_de_7_dias():
    # Ciclo de 8 dias: o slot 8 fica fora da janela (1..7) e nao e tocado.
    slots = week(slot(1, [eq(1, 100, PEITO, 3), eq(2, 110, PEITO, 3), eq(3, 113, PEITO, 3), eq(4, 114, PEITO, 3)]), days=7)
    slots.append(slot(8, [eq(1, 100, PEITO, 3), eq(2, 110, PEITO, 3)]))
    tpl = template(slots)
    fixes = run(tpl)
    assert sets_of(tpl, 8) == [(100, 3), (110, 3)]
    assert {f["slot"] for f in fixes} == {1}


def test_grupo_abaixo_da_faixa_sem_slot_na_janela_que_o_treine_nao_ganha_slot_novo():
    # Peito so em uma fatia (peitoral superior) num slot: o bloco e o peito; o
    # complemento usa o slot que ja o treina, nunca um slot que nao treina.
    tpl = template(week(slot(1, [eq(1, 115, PEITO_SUP, 3)]), slot(2, [eq(1, 300, CORE, 3)])))
    fixes = run(tpl, gym=[pair(200, PEITO)])
    assert sets_of(tpl, 2) == [(300, 3)]
    assert {f["slot"] for f in fixes if "slot" in f} == {1}


# ---------------------------------------------------------------------------
# Ordem dos eixos e determinismo
# ---------------------------------------------------------------------------


def test_primeiro_o_eixo_por_sessao_depois_o_semanal():
    tpl = template(week(slot(1, [eq(1, 100, PEITO, 4), eq(2, 110, PEITO, 4), eq(3, 113, PEITO, 4)])))
    fixes = run(tpl, gym=[pair(200, PEITO), pair(204, PEITO)], volume_classes=classes(peito=(15, 24), weekly_peito=(18, 32)))
    assert [(f["code"], f["axis"], f["mapping_id"]) for f in fixes] == [
        (vc.VOLUME_EXERCISE_ADDED, "session", 200),  # 12 -> 15: sessao fechada
        (vc.VOLUME_SETS_ADDED, "weekly", 200),  # 15 -> 16: (a) antes de (b)
        (vc.VOLUME_EXERCISE_ADDED, "weekly", 204),  # 16 -> 19: semana fechada
    ]


def test_mesma_entrada_mesma_saida():
    def montar():
        return template(
            week(
                slot(1, [eq(1, 100, PEITO, 2), eq(2, 210, TRICEPS, 2)]),
                slot(3, [eq(1, 110, PEITO, 3), eq(2, 211, TRICEPS, 3)]),
            )
        )

    gym = [pair(201, PEITO), pair(200, PEITO), pair(212, TRICEPS), pair(204, PEITO)]
    seg = {(m, 900 + m): safety(m, 900 + m) for m in (100, 110, 200, 201, 204, 210, 211, 212)}
    a, b = montar(), montar()
    fixes_a = run(a, gym=gym, safeties=seg, volume_classes=classes(weekly_peito=(18, 32), weekly_peq=(12, 24)))
    fixes_b = run(b, gym=gym, safeties=seg, volume_classes=classes(weekly_peito=(18, 32), weekly_peq=(12, 24)))
    assert fixes_a == fixes_b and fixes_a
    assert a.model_dump() == b.model_dump()


def test_rascunho_sem_deficit_fica_intacto():
    tpl = template(week(slot(1, [eq(i, m, PEITO, 4) for i, m in enumerate((100, 110, 113, 114), start=1)])))
    antes = tpl.model_dump()
    assert run(tpl, gym=[pair(200, PEITO)]) == []
    assert tpl.model_dump() == antes


# ---------------------------------------------------------------------------
# validate_draft de ponta a ponta
# ---------------------------------------------------------------------------


def _item_llm(**overrides) -> dict:
    base = {
        "item_kind": "equipment", "muscle_group_id": None, "priority_min_pct": None, "priority_max_pct": None,
        "prioritize": False, "mapping_id": None, "equipment_id": None, "sets_count": None, "reps_target": None,
        "rpe_target": None, "advanced_technique": None, "advanced_technique_applications": None,
        "advanced_technique_duration_seconds": None, "warmup_sets_count": None, "warmup_reps": None,
        "warmup_rest_seconds": None, "feeler_set_sets_count": None, "feeler_set_reps": None,
        "feeler_set_rest_seconds": None, "peak_contraction_seconds": None,
    }
    base.update(overrides)
    return base


def test_validate_draft_complementa_e_a_metodologia_ve_o_resultado_com_uma_leitura_so(monkeypatch):
    pedidos: list[set[int]] = []

    def fake_inputs(mapping_ids, counting_mode=None):
        pedidos.append(set(mapping_ids))
        return inputs()

    gym = [pair(100, PEITO), pair(110, PEITO), pair(113, PEITO), pair(200, PEITO)]
    monkeypatch.setattr(svc, "load_methodology_inputs", fake_inputs)
    monkeypatch.setattr(validation, "_check_references", lambda *a, **k: list(gym))
    monkeypatch.setattr(validation, "_check_template_rules", lambda *a, **k: None)
    monkeypatch.setattr(validation, "_apply_safety_auto_fixes", lambda *a, **k: {})
    monkeypatch.setattr(ts, "resolve_training_safety_bulk", lambda conn, pairs: {})
    saida = {
        "template": {
            "name": "Esboço",
            "description": None,
            "volume_mode": "equipment_series",
            "slots": [
                {
                    "sequence": 1, "name": "Peito", "slot_type": "normal", "weekday": None,
                    "items": [
                        _item_llm(muscle_group_id=PEITO, mapping_id=m, equipment_id=900 + m, sets_count=4, reps_target="8-12", rpe_target="8")
                        for m in (100, 110, 113)
                    ],
                },
                *[{"sequence": s, "name": "Descanso", "slot_type": "descanso", "weekday": None, "items": []} for s in range(2, 8)],
            ],
        },
        "rationale": "teste",
        "source_citations": [],
    }
    pedido = PeriodizationDraftIn(volume_mode="equipment_series", gym_id=7, days_per_cycle=7)
    draft, report, _ = validation.validate_draft(object(), pedido, saida)

    assert report["errors"] == []
    assert [f["code"] for f in report["auto_fixes"]] == ["VOLUME_EXERCISE_ADDED"]
    assert [i["mapping_id"] for i in draft["slots"][0]["items"]] == [100, 110, 113, 200]
    # 15 series: o aviso de metodologia some, porque roda sobre o complementado.
    assert not [w for w in report["warnings"] if w["code"].startswith("METHODOLOGY_")]
    # Uma leitura so, com os candidatos da academia, reusada pela etapa de avisos.
    assert pedidos == [{100, 110, 113, 200}]


def test_validate_draft_com_erro_nao_complementa(monkeypatch):
    monkeypatch.setattr(svc, "load_methodology_inputs", lambda ids, mode=None: inputs())

    def referencias(conn, template, request, report):
        report.error(validation.EQUIPMENT_NOT_IN_GYM, gym_id=7)
        return [pair(200, PEITO)]

    monkeypatch.setattr(validation, "_check_references", referencias)
    monkeypatch.setattr(validation, "_check_template_rules", lambda *a, **k: None)
    monkeypatch.setattr(validation, "_apply_safety_auto_fixes", lambda *a, **k: {})
    saida = {
        "template": {
            "name": "X", "description": None, "volume_mode": "equipment_series",
            "slots": [{"sequence": 1, "name": "A", "slot_type": "normal", "weekday": None, "items": [
                _item_llm(muscle_group_id=PEITO, mapping_id=100, equipment_id=1000, sets_count=3)
            ]}],
        },
        "rationale": "", "source_citations": [],
    }
    _, report, _ = validation.validate_draft(
        object(), PeriodizationDraftIn(volume_mode="equipment_series", gym_id=7, days_per_cycle=1), saida
    )
    assert not [f for f in report["auto_fixes"] if f["code"].startswith("VOLUME_")]


def test_falha_do_complemento_vira_aviso_e_nao_mexe_no_rascunho(monkeypatch):
    monkeypatch.setattr(vc._Complement, "run", lambda self: (_ for _ in ()).throw(RuntimeError("bug")))
    report = validation.ValidationReport()
    tpl = template(week(slot(1, [eq(1, 100, PEITO, 3)])))
    antes = tpl.model_dump()
    monkeypatch.setattr(svc, "load_methodology_inputs", lambda ids, mode=None: inputs())
    validation._apply_volume_complement(object(), tpl, [pair(200, PEITO)], {}, report)
    assert tpl.model_dump() == antes
    assert report.warnings == [
        {"code": "ACTIVATION_COVERAGE", "status": "UNAVAILABLE", "message": "complemento de volume: bug"}
    ]


@pytest.mark.parametrize("codigo", [
    "VOLUME_SETS_ADDED", "VOLUME_EXERCISE_ADDED", "VOLUME_TECHNIQUE_ADDED", "VOLUME_SETS_ABOVE_IDEAL", "VOLUME_GAP_UNRESOLVED",
])
def test_codigos_reexportados_pela_validacao(codigo):
    assert getattr(validation, codigo) == codigo


# ---------------------------------------------------------------------------
# Decisoes de 2026-10-05: teto de 6 series e descanso padrao de 90 s
# ---------------------------------------------------------------------------


def _slot_cheio_com_um_exercicio(sets: int) -> list[dict]:
    """Triceps com UM exercicio + 8 de core: 9 exercicios (teto), sem par
    novo possivel e sem seguranca lida (sem tecnica) - o caso do replay do
    log 11 (Lombar/Panturrilha/Abdomen iam a 11 series)."""
    core = [eq(i, m, CORE, 3) for i, m in enumerate((300, 301, 302, 303, 304, 305, 306, 307), start=2)]
    return [eq(1, 210, TRICEPS, sets)] + core


def test_teto_de_6_series_por_exercicio_e_o_resto_vira_gap():
    tpl = template(week(slot(1, _slot_cheio_com_um_exercicio(4))))  # triceps 4, faixa 9-16
    fixes = run(tpl, gym=[pair(211, TRICEPS)])

    assert sets_of(tpl, 1)[0] == (210, vc.MAX_SETS_PER_EXERCISE)
    assert [f["code"] for f in fixes] == [vc.VOLUME_SETS_ABOVE_IDEAL, vc.VOLUME_GAP_UNRESOLVED]
    acima, gap = fixes
    assert (acima["from"], acima["to"], acima["ideal"], acima["max_sets"]) == (4, 6, 4, 6)
    assert (gap["value_before"], gap["value_after"], gap["deficit_after"]) == (4, 6, 3)
    assert {vc.REASON_EXERCISE_CAP, vc.REASON_NO_TECHNIQUE, vc.REASON_MAX_SETS_PER_EXERCISE} <= set(gap["reasons"])


def test_exercicio_que_a_ia_entregou_com_mais_de_6_nao_e_cortado_nem_ganha_series():
    tpl = template(week(slot(1, _slot_cheio_com_um_exercicio(8))))  # 8 contra o minimo 9
    fixes = run(tpl, gym=[pair(211, TRICEPS)])
    assert sets_of(tpl, 1)[0] == (210, 8)
    assert [f["code"] for f in fixes] == [vc.VOLUME_GAP_UNRESOLVED]
    assert vc.REASON_MAX_SETS_PER_EXERCISE in fixes[0]["reasons"]


def test_descanso_padrao_de_90s_barra_acrescimo_que_caberia_com_o_0s_da_tela():
    # Peito 4 x 4 series de 10 reps sem descanso + 58 min de cardio. Na conta
    # do complemento (90 s por serie sem descanso): 16 x 120 + 3480 = 5400 s,
    # no limite - qualquer serie a mais estoura. Na tela (0 s) o slot tem so
    # 66 min e um exercicio novo caberia.
    itens = [eq(i, m, PEITO, 4, reps_target="10") for i, m in enumerate((100, 110, 113, 114), start=1)] + [
        {"item_order": 5, "item_kind": "cardio", "cardio_duration_minutes": 58, "sets_count": None}
    ]
    tpl = template(week(slot(1, itens)))
    assert estimate_slot_seconds(tpl.slots[0].items, vc.COMPLEMENT_DEFAULT_REST_SECONDS) == vc.SLOT_TIME_LIMIT_SECONDS
    assert estimate_slot_seconds(tpl.slots[0].items) == 66 * 60

    fixes = run(tpl, gym=[pair(200, PEITO)], volume_classes=classes(peito=(17, 20)))

    assert len(tpl.slots[0].items) == 5 and all(s == 4 for m, s in sets_of(tpl, 1) if m)
    assert [f["code"] for f in fixes] == [vc.VOLUME_GAP_UNRESOLVED]
    assert vc.REASON_TIME_LIMIT in fixes[0]["reasons"]


def test_constantes_das_decisoes():
    assert vc.COMPLEMENT_DEFAULT_REST_SECONDS == 90
    assert vc.MAX_SETS_PER_EXERCISE == 6
