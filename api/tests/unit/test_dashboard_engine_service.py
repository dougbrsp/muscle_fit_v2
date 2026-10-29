"""Unit: a LEITURA do `generation_audit` e as regras puras de
`/dashboard/engine` (F3.1), sem banco.

O que estes testes travam e a fronteira que a tela existe para manter:

- o bloco de auditoria NAO tem forma unica (o Modo A grava `selection` como
  dicionario por grupo, o Modo B nem o grava), e a leitura tem de aceitar as
  duas sem inventar entrada nenhuma;
- ausencia continua ausencia: secao que o motor nao gravou nao vira lista
  vazia com significado de "nada aconteceu";
- um `fallback` que fala do GRUPO nao pode ser colado a um exercicio;
- a coluna PLANEJADO so nomeia exercicio quando o MODELO o nomeou;
- o item coberto por outro slot (o defeito corrigido no PR #211) e
  RECONHECIDO, e nao reclassificado - quem classifica continua sendo o §5.
"""

from __future__ import annotations

from app.intelligence.dashboard import engine_service as es

# Bloco do Modo A (`generation/mode_a.py`), reduzido ao que a tela le.
AUDIT_MODO_A = {
    "template_id": 3,
    "slot_sequence": 1,
    "volume_mode": "percent_budget",
    "engine_version": "v2",
    "llm_used": False,
    "activation": {
        "5": {
            "muscle_group_id": 5,
            "muscle_group_name": "Peitoral",
            "target_weekly": 12.0,
            "target_min": 6.0,
            "target_max": 9.0,
            "result": 7.5,
            "deviation_status": "BALANCED",
            "recovery_status": "READY",
        }
    },
    "selection": {
        "5": [
            {"exercise_id": 10, "rank": 1, "selected": True, "reason": None, "sets_count": 3},
            {"exercise_id": 11, "rank": 2, "selected": False, "reason": "SECONDARY_BUDGET_EXCEEDED"},
        ]
    },
    "safety_clamps": [
        {"exercise_id": 10, "equipment_id": 2, "field": "rpe_target", "from": 9, "to": 8, "reason": "RECOVERY"}
    ],
    "substitutions": [],
    "fallbacks": [
        {"muscle_group_id": 5, "muscle_group_name": "Peitoral", "reason": "ACTIVATION_DEFICIT", "deficit": 1.5},
        {"muscle_group_id": 5, "exercise_id": 10, "reason": "SETS_REDUCED_FOR_SECONDARY_BUDGET", "from": 4, "to": 3},
    ],
}

# Bloco do Modo B: subconjunto - sem `activation`, sem `selection`.
AUDIT_MODO_B = {
    "template_id": 4,
    "slot_sequence": 2,
    "volume_mode": "equipment_series",
    "engine_version": "v2",
    "llm_used": False,
    "safety_clamps": [{"sequence": 0, "exercise_id": 20, "field": "rpe", "from": 10, "to": 9, "reason": "EQUIPMENT_SAFETY"}],
    "substitutions": [
        {
            "kind": "exercise",
            "reason": "EXERCISE_UNAVAILABLE_IN_GYM",
            "from_exercise_id": 21,
            "to_exercise_id": 22,
            "rejected": [],
            "ranking": [],
        }
    ],
    "fallbacks": [],
}


# ---------------------------------------------------------------------------
# Leitura do bloco
# ---------------------------------------------------------------------------


def test_selection_como_dicionario_por_grupo_vira_razao_do_exercicio_escolhido():
    razoes = es.reasons_by_exercise(AUDIT_MODO_A)

    assert [r["kind"].value for r in razoes[10]] == ["selection", "safety_clamp", "fallback"]
    # O candidato RECUSADO nao vira razao do item - ele nao esta na ficha.
    assert 11 not in razoes


def test_o_grupo_do_selection_chega_na_razao_e_e_o_que_liga_ao_orcamento():
    razoes = es.reasons_by_exercise(AUDIT_MODO_A)
    selecao = razoes[10][0]
    assert selecao["data"]["muscle_group_id"] == 5


def test_modo_b_sem_selection_nem_activation_nao_inventa_nada():
    assert es.group_budgets(AUDIT_MODO_B) == []
    assert es.rejections(AUDIT_MODO_B) == []
    razoes = es.reasons_by_exercise(AUDIT_MODO_B)
    assert [r["kind"].value for r in razoes[20]] == ["safety_clamp"]
    # A substituicao fica no exercicio que ENTROU na ficha, nao no que saiu.
    assert [r["kind"].value for r in razoes[22]] == ["substitution"]
    assert 21 not in razoes


def test_fallback_do_grupo_nao_e_colado_a_nenhum_exercicio():
    plano = es.plan_level_reasons(AUDIT_MODO_A)
    assert [r["code"] for r in plano] == ["ACTIVATION_DEFICIT"]
    # O de item continua no item.
    assert any(r["code"] == "SETS_REDUCED_FOR_SECONDARY_BUDGET" for r in es.reasons_by_exercise(AUDIT_MODO_A)[10])


def test_clamp_preserva_os_campos_do_motor_sem_renomear():
    clamp = [r for r in es.reasons_by_exercise(AUDIT_MODO_A)[10] if r["kind"] is es.EngineReasonKind.SAFETY_CLAMP][0]
    assert clamp["code"] == "RECOVERY"
    assert clamp["data"]["from"] == 9 and clamp["data"]["to"] == 8


def test_orcamento_por_grupo_le_as_chaves_do_group_budget():
    linha = es.group_budgets(AUDIT_MODO_A)[0]
    assert linha["muscle_group_name"] == "Peitoral"
    assert linha["target_min_sets"] == 6.0
    assert linha["target_max_sets"] == 9.0
    assert linha["generated_sets"] == 7.5


def test_orcamento_ausente_e_None_nunca_zero():
    linha = es.group_budgets({"activation": {"5": {"muscle_group_id": 5}}})[0]
    assert linha["target_weekly_sets"] is None
    assert linha["target_max_sets"] is None


def test_rejeicoes_saem_na_ordem_do_motor_e_so_as_nao_escolhidas():
    recusados = es.rejections(AUDIT_MODO_A)
    assert [r["exercise_id"] for r in recusados] == [11]
    assert recusados[0]["reason"] == "SECONDARY_BUDGET_EXCEEDED"


def test_rejeicoes_respeitam_o_teto():
    audit = {"selection": [{"exercise_id": i, "selected": False, "reason": "UNAVAILABLE"} for i in range(200)]}
    assert len(es.rejections(audit, limit=5)) == 5


def test_parse_audit_aceita_string_do_shim_e_dict_do_pymysql():
    assert es.parse_audit('{"volume_mode": "percent_budget"}') == {"volume_mode": "percent_budget"}
    assert es.parse_audit({"volume_mode": "x"}) == {"volume_mode": "x"}
    # JSON quebrado nao pode virar 500: a tela dira "auditoria indisponivel".
    assert es.parse_audit("{nao e json") is None
    assert es.parse_audit(None) is None


# ---------------------------------------------------------------------------
# Coluna PLANEJADO
# ---------------------------------------------------------------------------

TEMPLATE_ROWS = [
    {"template_item_id": 1, "item_kind": "exercise", "exercise_id": 10, "exercise_name": "Supino", "sets_count": 3},
    {"template_item_id": 2, "item_kind": "exercise", "exercise_id": 10, "exercise_name": "Supino", "sets_count": 2},
    {"template_item_id": 3, "item_kind": "muscle_group", "muscle_group_id": 5, "exercise_id": None},
]


def test_planejado_casa_pelo_exercicio_que_o_modelo_nomeou():
    usados: set[int] = set()
    planejado = es.match_planned(exercise_id=10, template_rows=TEMPLATE_ROWS, used_template_items=usados)
    assert planejado["source"] is es.EnginePlannedSource.TEMPLATE_ITEM
    assert planejado["sets_count"] == 3


def test_o_mesmo_pedido_do_modelo_casa_uma_vez_so():
    usados: set[int] = set()
    primeiro = es.match_planned(exercise_id=10, template_rows=TEMPLATE_ROWS, used_template_items=usados)
    segundo = es.match_planned(exercise_id=10, template_rows=TEMPLATE_ROWS, used_template_items=usados)
    assert primeiro["sets_count"] == 3
    assert segundo["sets_count"] == 2
    terceiro = es.match_planned(exercise_id=10, template_rows=TEMPLATE_ROWS, used_template_items=usados)
    assert terceiro["source"] is es.EnginePlannedSource.UNAVAILABLE


def test_item_muscle_group_do_modelo_nao_casa_com_exercicio_nenhum():
    """O Modelo pediu VOLUME, nao aquele exercicio. Casar os dois faria a tela
    afirmar uma escolha que foi do motor."""
    usados: set[int] = set()
    planejado = es.match_planned(exercise_id=99, template_rows=TEMPLATE_ROWS, used_template_items=usados)
    assert planejado["source"] is es.EnginePlannedSource.UNAVAILABLE


# ---------------------------------------------------------------------------
# Coluna PLANEJADO do item de exercicio livre (M101d)
# ---------------------------------------------------------------------------

MODO_B_ROWS = [
    {"template_item_id": 11, "item_order": 1, "item_kind": "equipment", "muscle_group_id": 2,
     "exercise_id": 10, "exercise_name": "Supino", "equipment_id": 7, "sets_count": 3},
    {"template_item_id": 12, "item_order": 2, "item_kind": "equipment", "muscle_group_id": 5,
     "muscle_group_name": "Costas", "exercise_id": None, "equipment_id": None, "sets_count": 4,
     "reps_target": "8-10", "rpe_target": "8"},
    {"template_item_id": 13, "item_order": 3, "item_kind": "equipment", "muscle_group_id": 5,
     "muscle_group_name": "Costas", "exercise_id": None, "equipment_id": None, "sets_count": 2,
     "reps_target": "12-15", "rpe_target": "9"},
]
# Grupo PRIMARY dos exercicios que o motor escolheu para os livres.
PRIMARY = {10: 2, 20: 5, 30: 5}


def _plan_row(plan_exercise_id: int, sequence: int, mapping_id: int) -> dict:
    return {"plan_exercise_id": plan_exercise_id, "plan_sequence": sequence, "mapping_id": mapping_id}


def _planejado_da_ficha(plan_rows: list[dict], template_rows: list[dict]) -> dict[int, dict]:
    """A mesma sequencia de `build_engine_plan`: livres re-derivados uma vez,
    depois `match_planned` linha a linha com o mesmo `usados`."""
    livres = es.free_items_by_plan_row(object(), plan_rows, template_rows)
    usados: set[int] = set()
    return {
        row["plan_exercise_id"]: es.match_planned(
            exercise_id=row["mapping_id"],
            template_rows=template_rows,
            used_template_items=usados,
            free_template_item=livres.get(row["plan_exercise_id"]),
        )
        for row in plan_rows
    }


def _primary_fake(monkeypatch, chamadas: list | None = None):
    from app import progression_state as ps

    def fake(_conn, mapping_ids):
        if chamadas is not None:
            chamadas.append(list(mapping_ids))
        return {int(m): PRIMARY[int(m)] for m in mapping_ids if int(m) in PRIMARY}, {}

    monkeypatch.setattr(ps, "load_primary_groups", fake)


def test_item_livre_com_ficha_mostra_o_planejado_do_item(monkeypatch):
    _primary_fake(monkeypatch)
    plan_rows = [_plan_row(101, 1, 10), _plan_row(102, 2, 20)]
    planejado = _planejado_da_ficha(plan_rows, MODO_B_ROWS[:2])
    livre = planejado[102]
    assert livre["source"] is es.EnginePlannedSource.TEMPLATE_ITEM
    assert livre["free_exercise"] is True
    assert (livre["sets_count"], livre["reps_target"], livre["rpe_target"]) == (4, "8-10", "8")
    assert livre["muscle_group_name"] == "Costas" and livre["exercise_id"] is None


def test_dois_livres_do_mesmo_grupo_casam_cada_um_com_o_seu(monkeypatch):
    _primary_fake(monkeypatch)
    plan_rows = [_plan_row(101, 1, 10), _plan_row(102, 2, 20), _plan_row(103, 3, 30)]
    planejado = _planejado_da_ficha(plan_rows, MODO_B_ROWS)
    assert planejado[102]["sets_count"] == 4 and planejado[102]["item_order"] == 2
    assert planejado[103]["sets_count"] == 2 and planejado[103]["item_order"] == 3


def test_dois_livres_do_mesmo_grupo_com_rascunho_reordenado(monkeypatch):
    """Linhas trocadas de posicao: o casamento por grupo do congelamento
    entrega um item a cada linha, nunca o mesmo duas vezes."""
    _primary_fake(monkeypatch)
    plan_rows = [_plan_row(103, 1, 30), _plan_row(101, 2, 10), _plan_row(102, 3, 20)]
    livres = es.free_items_by_plan_row(object(), plan_rows, MODO_B_ROWS)
    assert sorted(item["template_item_id"] for item in livres.values()) == [12, 13]


def test_item_livre_ja_casado_nao_casa_de_novo():
    usados: set[int] = set()
    livre = MODO_B_ROWS[1]
    primeiro = es.match_planned(exercise_id=20, template_rows=MODO_B_ROWS, used_template_items=usados,
                                free_template_item=livre)
    segundo = es.match_planned(exercise_id=30, template_rows=MODO_B_ROWS, used_template_items=usados,
                               free_template_item=livre)
    assert primeiro["source"] is es.EnginePlannedSource.TEMPLATE_ITEM
    assert segundo["source"] is es.EnginePlannedSource.UNAVAILABLE


def test_item_livre_sem_ficha_segue_no_orcamento_do_grupo(monkeypatch):
    """Linha cujo exercicio nao e do grupo do livre: nada casa, e o chamador
    segue para o orcamento do grupo, como hoje."""
    _primary_fake(monkeypatch)
    plan_rows = [_plan_row(101, 1, 10)]
    assert es.free_items_by_plan_row(object(), plan_rows, MODO_B_ROWS) == {}
    planejado = _planejado_da_ficha([_plan_row(104, 2, 99)], MODO_B_ROWS)
    assert planejado[104]["source"] is es.EnginePlannedSource.UNAVAILABLE


def test_itens_com_par_e_modo_a_nao_mudam_nem_leem_o_banco(monkeypatch):
    chamadas: list = []
    _primary_fake(monkeypatch, chamadas)
    plan_rows = [_plan_row(101, 1, 10), _plan_row(102, 2, 10), _plan_row(103, 3, 99)]
    assert es.free_items_by_plan_row(object(), plan_rows, TEMPLATE_ROWS) == {}
    assert es.free_items_by_plan_row(object(), plan_rows, MODO_B_ROWS[:1]) == {}
    assert chamadas == []
    planejado = _planejado_da_ficha(plan_rows, TEMPLATE_ROWS)
    assert planejado[101]["sets_count"] == 3 and planejado[101]["free_exercise"] is False
    assert planejado[102]["sets_count"] == 2
    assert planejado[103]["source"] is es.EnginePlannedSource.UNAVAILABLE


def test_linha_com_par_substituida_nao_vira_item_livre(monkeypatch):
    """M6: exercicio trocado na posicao de um item COM par casa no congelamento,
    mas o PLANEJADO dele segue como hoje (so o livre e novo)."""
    _primary_fake(monkeypatch)
    plan_rows = [_plan_row(101, 1, 40)]
    PRIMARY[40] = 2
    try:
        assert es.free_items_by_plan_row(object(), plan_rows, MODO_B_ROWS) == {}
    finally:
        PRIMARY.pop(40)


# ---------------------------------------------------------------------------
# Item coberto por outro slot (PR #211)
# ---------------------------------------------------------------------------


def test_item_coberto_por_outro_slot_e_reconhecido():
    """Atividade 230 do PR #211: a execucao do item 941 foi gravada no slot do
    943, e o 941 ficou sem serie nenhuma."""
    plan_rows = [
        {"plan_exercise_id": 941, "mapping_id": 12},
        {"plan_exercise_id": 943, "mapping_id": 15},
    ]
    performed_rows = [{"workout_plan_exercise_id": 943, "exercise_id": 12}]

    assert es.covered_by_another_slot(plan_rows, performed_rows) == {941}


def test_sem_substituicao_nenhum_item_e_dado_por_coberto():
    plan_rows = [{"plan_exercise_id": 1, "mapping_id": 12}, {"plan_exercise_id": 2, "mapping_id": 15}]
    performed_rows = [{"workout_plan_exercise_id": 1, "exercise_id": 12}]

    assert es.covered_by_another_slot(plan_rows, performed_rows) == set()


def test_item_com_serie_propria_nunca_e_dado_por_coberto():
    plan_rows = [{"plan_exercise_id": 941, "mapping_id": 12}, {"plan_exercise_id": 943, "mapping_id": 15}]
    performed_rows = [
        {"workout_plan_exercise_id": 943, "exercise_id": 12},
        {"workout_plan_exercise_id": 941, "exercise_id": 12},
    ]

    assert es.covered_by_another_slot(plan_rows, performed_rows) == set()


# ---------------------------------------------------------------------------
# Limitacoes: a frase muda com o ESTADO
# ---------------------------------------------------------------------------


def test_as_frases_de_limitacao_sao_todas_diferentes():
    frases = {
        es.AUDIT_FROM_RAW_PAYLOAD_LIMITATION,
        es.AUDIT_UNAVAILABLE_LIMITATION,
        es.AUDIT_DRAFT_NOT_SAVED_LIMITATION,
        es.NO_TEMPLATE_LIMITATION,
        es.BUDGET_MODE_LIMITATION,
        es.NO_PROGRESSION_DECISION_LIMITATION,
        es.NOT_EXECUTED_LIMITATION,
        es.COVERED_BY_OTHER_SLOT_LIMITATION,
        es.NO_RUNS_IN_WINDOW_LIMITATION,
    }
    assert len(frases) == 9
