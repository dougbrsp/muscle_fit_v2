"""Auto-fixes ESTRUTURAIS do validador da periodizacao (confiabilidade,
2026-09-28).

A medicao do prompt v3 em DEV deu 9 tentativas e 3 sucessos: o volume melhorou,
mas o modelo passou a violar regra DURA (trio fora do catalogo da academia,
numero de slots diferente do pedido, mais de 3 priorizados no slot) e o job
inteiro virava `failed` - o usuario nao recebia rascunho nenhum, embora a maior
parte do trabalho estivesse boa.

O que este arquivo trava, sem banco e sem rede:

1. troca de par fora da academia pelo par mais proximo do MESMO grupo DENTRO
   dela, com a preferencia documentada (nao repetir no slot > mesmo exercicio >
   mesmo equipamento > primeiro do catalogo) e o `de -> para` auditavel;
2. SEM candidato do grupo na academia: continua ERRO `EQUIPMENT_NOT_IN_GYM` -
   a promessa de seguranca ("nunca prescrever equipamento que a academia nao
   tem") nao e negociada por taxa de sucesso;
3. slots a mais/a menos, `sequence` furado, descanso com exercicio, excesso de
   `prioritize` e `sets_count` invalido viram auto-fix deterministico;
4. depois dos auto-fixes, o MESMO `_validate_template_payload` do save aceita o
   rascunho - que e a definicao de "erro consertado".
"""

from __future__ import annotations

from app.models import PeriodizationDraftIn, WorkoutPlanTemplateIn
from app.periodization import validation
from app.workout_plan_templates import _validate_template_payload

PEITORAL = 52
BICEPS = 5

# Catalogo ATIVO da academia de teste, na ordem do recomendador (ge.id ASC).
ROWS = [
    {
        "mapping_id": 100,
        "equipment_id": 10,
        "muscle_group_id": PEITORAL,
        "exercise_name": "Supino reto",
        "equipment_name": "Barra",
    },
    {
        "mapping_id": 100,
        "equipment_id": 11,
        "muscle_group_id": PEITORAL,
        "exercise_name": "Supino reto",
        "equipment_name": "Halteres",
    },
    {
        "mapping_id": 101,
        "equipment_id": 12,
        "muscle_group_id": PEITORAL,
        "exercise_name": "Crucifixo",
        "equipment_name": "Maquina",
    },
    {
        "mapping_id": 200,
        "equipment_id": 20,
        "muscle_group_id": BICEPS,
        "exercise_name": "Rosca direta",
        "equipment_name": "Barra W",
    },
]


def _eq(order: int, mapping_id: int, equipment_id: int, group: int = PEITORAL, **extra) -> dict:
    item = {
        "item_order": order,
        "item_kind": "equipment",
        "muscle_group_id": group,
        "mapping_id": mapping_id,
        "equipment_id": equipment_id,
        "sets_count": 4,
        "reps_target": "8-12",
        "rpe_target": "8",
    }
    item.update(extra)
    return item


def _slot(seq: int, items: list[dict], slot_type: str = "normal", name: str | None = None) -> dict:
    return {"sequence": seq, "name": name or f"Dia {seq}", "slot_type": slot_type, "items": items}


def _template(slots: list[dict]) -> WorkoutPlanTemplateIn:
    return WorkoutPlanTemplateIn(name="Rascunho", volume_mode="equipment_series", slots=slots)


def _pedido(days: int = 4) -> PeriodizationDraftIn:
    return PeriodizationDraftIn(volume_mode="equipment_series", gym_id=7, days_per_cycle=days)


def _pares(template: WorkoutPlanTemplateIn) -> list[tuple]:
    return [(slot, item) for slot in template.slots for item in slot.items]


# ---------------------------------------------------------------------------
# 1. Troca de par fora da academia
# ---------------------------------------------------------------------------


def test_trio_fora_do_catalogo_vira_troca_pelo_mesmo_exercicio_da_academia():
    """O erro real medido em DEV: mapping_id de uma linha com equipment_id de
    outra. O exercicio que o modelo escolheu e preservado; so o equipamento
    passa a ser um que a academia tem."""
    template = _template([_slot(1, [_eq(1, 100, 99)])])
    report = validation.ValidationReport()
    validation._replace_equipment_pairs(_pares(template), ROWS, _pedido(), report)

    assert report.errors == []
    fix = report.auto_fixes[0]
    assert fix["code"] == validation.EQUIPMENT_REPLACED
    assert (fix["from"], fix["to"]) == (
        {"mapping_id": 100, "equipment_id": 99},
        {"mapping_id": 100, "equipment_id": 10},
    )
    assert (fix["slot"], fix["item"], fix["gym_id"], fix["muscle_group_id"]) == (1, 1, 7, PEITORAL)
    assert (fix["exercise_name"], fix["equipment_name"]) == ("Supino reto", "Barra")
    item = template.slots[0].items[0]
    assert (item.mapping_id, item.equipment_id) == (100, 10)


def test_troca_nao_repete_par_que_o_slot_ja_usa():
    """Preferencia 1: nao criar exercicio duplicado no mesmo dia."""
    template = _template([_slot(1, [_eq(1, 100, 10), _eq(2, 100, 99)])])
    report = validation.ValidationReport()
    validation._replace_equipment_pairs(_pares(template), ROWS, _pedido(), report)

    assert [(i.mapping_id, i.equipment_id) for i in template.slots[0].items] == [(100, 10), (100, 11)]
    assert report.errors == []


def test_troca_preserva_o_equipamento_quando_o_exercicio_nao_existe_na_academia():
    """Preferencia 3: exercicio desconhecido, equipamento conhecido -> fica o
    equipamento e muda o exercicio, dentro do mesmo grupo."""
    template = _template([_slot(1, [_eq(1, 999, 12)])])
    report = validation.ValidationReport()
    validation._replace_equipment_pairs(_pares(template), ROWS, _pedido(), report)

    assert [(i.mapping_id, i.equipment_id) for i in template.slots[0].items] == [(101, 12)]


def test_troca_respeita_o_grupo_muscular_do_item():
    """Nunca troca peito por biceps: o candidato sai do grupo do PROPRIO item."""
    template = _template([_slot(1, [_eq(1, 555, 555, group=BICEPS)])])
    report = validation.ValidationReport()
    validation._replace_equipment_pairs(_pares(template), ROWS, _pedido(), report)

    assert [(i.mapping_id, i.equipment_id) for i in template.slots[0].items] == [(200, 20)]


def test_sem_candidato_do_grupo_na_academia_continua_erro():
    """A promessa de seguranca do validador nao e negociada: sem par do grupo
    dentro da academia, o rascunho e recusado como sempre foi."""
    template = _template([_slot(1, [_eq(1, 777, 777, group=999)])])
    report = validation.ValidationReport()
    validation._replace_equipment_pairs(_pares(template), ROWS, _pedido(), report)

    assert report.auto_fixes == []
    assert report.errors == [
        {
            "code": validation.EQUIPMENT_NOT_IN_GYM,
            "gym_id": 7,
            "mapping_id": 777,
            "equipment_id": 777,
            "muscle_group_id": 999,
        }
    ]


def test_par_valido_nao_e_tocado():
    template = _template([_slot(1, [_eq(1, 101, 12)])])
    report = validation.ValidationReport()
    validation._replace_equipment_pairs(_pares(template), ROWS, _pedido(), report)

    assert (report.errors, report.auto_fixes) == ([], [])
    assert [(i.mapping_id, i.equipment_id) for i in template.slots[0].items] == [(101, 12)]


# ---------------------------------------------------------------------------
# 2. Estrutura dos slots
# ---------------------------------------------------------------------------


def test_slot_a_mais_sai_pelo_descanso_do_fim():
    """9 slots para um ciclo de 8 (a falha medida em DEV): sai o descanso, nunca
    um dia de treino, enquanto houver descanso."""
    slots = [_slot(seq, [_eq(1, 100, 10)]) for seq in range(1, 4)]
    slots.append(_slot(4, [], slot_type="descanso"))
    slots.append(_slot(5, [_eq(1, 101, 12)]))
    report = validation.ValidationReport()
    template = _template(slots)
    validation._apply_structural_auto_fixes(template, _pedido(days=4), report)

    assert [(s.sequence, s.slot_type) for s in template.slots] == [
        (1, "normal"),
        (2, "normal"),
        (3, "normal"),
        (4, "normal"),
    ]
    removido = [f for f in report.auto_fixes if f["code"] == validation.SLOT_REMOVED]
    assert [(f["slot"], f["slot_type"]) for f in removido] == [(4, "descanso")]


def test_slot_a_menos_e_completado_com_descanso_vazio():
    report = validation.ValidationReport()
    template = _template([_slot(1, [_eq(1, 100, 10)])])
    validation._apply_structural_auto_fixes(template, _pedido(days=3), report)

    assert [(s.sequence, s.slot_type, len(s.items)) for s in template.slots] == [
        (1, "normal", 1),
        (2, "descanso", 0),
        (3, "descanso", 0),
    ]
    assert [f["slot"] for f in report.auto_fixes if f["code"] == validation.SLOT_ADDED] == [2, 3]


def test_sequence_duplicado_ou_furado_e_renumerado_pela_posicao():
    report = validation.ValidationReport()
    template = _template([_slot(1, []), _slot(1, []), _slot(5, [])])
    validation._apply_structural_auto_fixes(template, _pedido(days=3), report)

    assert [s.sequence for s in template.slots] == [1, 2, 3]
    renumerados = [f for f in report.auto_fixes if f["code"] == validation.SLOT_SEQUENCE_RENUMBERED]
    assert [(f["from"], f["to"]) for f in renumerados] == [(1, 2), (5, 3)]


def test_descanso_com_exercicio_vira_dia_normal():
    report = validation.ValidationReport()
    template = _template([_slot(1, [_eq(1, 100, 10)], slot_type="descanso")])
    validation._apply_structural_auto_fixes(template, _pedido(days=1), report)

    assert template.slots[0].slot_type == "normal"
    assert [f["code"] for f in report.auto_fixes] == [validation.SLOT_TYPE_FIXED]


def test_excesso_de_prioritize_desmarca_do_quarto_em_diante():
    """A outra falha medida em DEV. Os tres primeiros itens continuam
    priorizados; a ordem decide, nada e removido."""
    itens = [_eq(i, 100 + i, 10 + i, prioritize=True) for i in range(1, 6)]
    report = validation.ValidationReport()
    template = _template([_slot(1, itens)])
    validation._apply_structural_auto_fixes(template, _pedido(days=1), report)

    assert [i.prioritize for i in template.slots[0].items] == [True, True, True, False, False]
    desmarcados = [f for f in report.auto_fixes if f["code"] == validation.PRIORITIZE_UNMARKED]
    assert [(f["item"], f["max"], f["received"]) for f in desmarcados] == [(4, 3, 5), (5, 3, 5)]


def test_sets_count_invalido_cai_no_minimo_da_metodologia():
    from app.intelligence.methodology_volume import _SETS_PER_EXERCISE_MIN

    report = validation.ValidationReport()
    template = _template([_slot(1, [_eq(1, 100, 10, sets_count=0), _eq(2, 101, 12, sets_count=None)])])
    validation._apply_structural_auto_fixes(template, _pedido(days=1), report)

    assert [i.sets_count for i in template.slots[0].items] == [_SETS_PER_EXERCISE_MIN] * 2
    assert [f["to"] for f in report.auto_fixes if f["code"] == validation.SETS_COUNT_FIXED] == [
        _SETS_PER_EXERCISE_MIN
    ] * 2


def test_rascunho_valido_nao_recebe_auto_fix_nenhum():
    report = validation.ValidationReport()
    template = _template([_slot(1, [_eq(1, 100, 10)]), _slot(2, [], slot_type="descanso")])
    validation._apply_structural_auto_fixes(template, _pedido(days=2), report)

    assert report.auto_fixes == []


# ---------------------------------------------------------------------------
# 3. Depois do conserto, o save aceita
# ---------------------------------------------------------------------------


def test_depois_dos_auto_fixes_o_validador_do_save_aceita_o_rascunho():
    """Todas as violacoes juntas: 3 slots para um ciclo de 2, sequence furado,
    descanso com exercicio, 5 priorizados e sets_count zerado."""
    slots = [
        _slot(3, [_eq(i, 100 + i, 10 + i, prioritize=True) for i in range(1, 6)]),
        _slot(3, [_eq(1, 100, 10, sets_count=0)], slot_type="descanso"),
        _slot(9, [], slot_type="descanso"),
    ]
    template = _template(slots)
    report = validation.ValidationReport()
    validation._apply_structural_auto_fixes(template, _pedido(days=2), report)

    _validate_template_payload(template)  # nao levanta: o save aceitaria
    assert len(template.slots) == 2
    assert {f["code"] for f in report.auto_fixes} == {
        validation.SLOT_REMOVED,
        validation.SLOT_SEQUENCE_RENUMBERED,
        validation.SLOT_TYPE_FIXED,
        validation.PRIORITIZE_UNMARKED,
        validation.SETS_COUNT_FIXED,
    }


def test_item_inutilizavel_sai_sozinho_e_o_resto_do_dia_fica_de_pe():
    """A terceira falha medida em DEV: um item de equipamento sem o trio
    derrubava o rascunho inteiro. Agora sai so ele, com item_order renumerado."""
    slots = [
        _slot(
            1,
            [
                _eq(1, 100, 10),
                dict(_eq(2, 101, 12), mapping_id=None),
                {"item_order": 3, "item_kind": "muscle_group", "muscle_group_id": PEITORAL},
                _eq(4, 100, 11),
            ],
        )
    ]
    template = _template(slots)
    report = validation.ValidationReport()
    validation._apply_structural_auto_fixes(template, _pedido(days=1), report)

    itens = template.slots[0].items
    assert [(i.item_order, i.mapping_id, i.equipment_id) for i in itens] == [(1, 100, 10), (2, 100, 11)]
    removidos = [f for f in report.auto_fixes if f["code"] == validation.ITEM_REMOVED]
    assert [(f["item"], f["reason"]) for f in removidos] == [
        (2, "trio incompleto"),
        (3, "item de grupo muscular no modo de equipamento"),
    ]
    _validate_template_payload(template)  # nao levanta
