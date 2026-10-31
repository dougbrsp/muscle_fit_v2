"""PR-16: o job da periodizacao ponta a ponta contra MySQL real, com a LLM
MOCKADA (a chave da OpenAI no DEV e vazia - nada aqui pode depender de rede).

O que estes testes protegem, em ordem de importancia:

1. **Zero INSERT em `workout_plan_templates`** (nem em slots/itens) em NENHUM
   cenario - sucesso, erro ou auto-fix. O modelo so nasce no
   `POST /workout-plan-templates` que o usuario dispara depois de editar.
2. Cada etapa da cascata de validacao recusa o que deve recusar, com o codigo
   certo no relatorio, e o que e corrigivel deterministicamente e corrigido
   (clamp de RPE, remocao de tecnica) em vez de recusado.
3. Falha da OpenAI (timeout) vira job `failed` pelo handler real da Lambda.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import lambda_plan_generator, workout_plan_templates
from app.openai_client import OpenAIGenerationError, StructuredResult
from app.periodization import core as periodization_core
from app.periodization.core import PROMPT_VERSION, PeriodizationError, run_periodization_draft
from app.periodization.logs import DAILY_GENERATION_LIMIT
from app.security import Identity, require_admin_or_pt
from tests.support.privado import exigir
from tests.support.sql_capture import WRITE_SQL, recorded_sql

TEMPLATE_TABLES = ("workout_plan_templates", "workout_plan_template_slots", "workout_plan_template_slot_items")


@pytest.fixture(autouse=True)
def modelo_configurado(monkeypatch):
    monkeypatch.setenv(periodization_core.MODEL_ENV_VAR, "gpt-periodizacao-teste")


@pytest.fixture(autouse=True)
def rag_sem_disco(monkeypatch):
    """O prompt nao e o objeto destes testes: o RAG entra como texto fixo (sem
    depender de `rag/markdown` estar no disco da maquina de CI) e o resto do
    contexto continua vindo do banco de verdade."""
    monkeypatch.setattr(
        "app.periodization.context.load_rag_text", lambda *args, **kwargs: "## Arquivo: PROGRESSAO.md"
    )


@pytest.fixture
def academia(db_conn, catalog):
    """Associacoes `gym_equipment` (par exercicio+equipamento) da academia
    principal, e o Rack SO na outra academia - o cenario de "equipamento fora da
    academia" precisa de um par que existe no banco mas nao nesta academia."""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM gym_equipment")
        # Matriz de tecnicas zerada: ausencia de linha = INCOMPATIVEL (D3), que
        # e o estado que estes testes assumem. Outro teste de integracao pode ter
        # deixado marcacoes para os mesmos equipamentos no schema compartilhado.
        cur.execute("DELETE FROM equipment_exercise_technique_overrides")
        cur.execute("DELETE FROM equipment_technique_compatibility")
        for table in TEMPLATE_TABLES[::-1]:
            cur.execute(f"DELETE FROM {table}")
        cur.execute("DELETE FROM template_generation_logs")
        pares = {}
        for equipamento, exercicio in (("Banco reto", "Supino reto"), ("Polia", "Remada baixa")):
            cur.execute(
                "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, %s, %s)",
                (catalog.gym_id, catalog.exercises[exercicio], catalog.equipment[equipamento]),
            )
            pares[equipamento] = cur.lastrowid
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, %s, %s)",
            (catalog.other_gym_id, catalog.exercises["Agachamento"], catalog.equipment["Rack"]),
        )
        # Teto 9 no banco reto: e o que o clamp de RPE tem de respeitar.
        cur.execute(
            "UPDATE equipment SET max_safe_rpe = 9 WHERE id = %s", (catalog.equipment["Banco reto"],)
        )
    db_conn.commit()
    return pares


@pytest.fixture
def metas(db_conn, catalog):
    """Perfil de Metas de Ativacao com meta alta em Peitoral - qualquer rascunho
    magro fica abaixo do minimo e tem de gerar warning, nao erro."""
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM training_type_muscle_group_targets")
        cur.execute("DELETE FROM training_types")
        cur.execute(
            "INSERT INTO training_types (name, created_by_user_id) VALUES ('Hipertrofia PR-16', %s)",
            (catalog.user_id,),
        )
        training_type_id = cur.lastrowid
        cur.execute(
            """
            INSERT INTO training_type_muscle_group_targets
                (training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (training_type_id, catalog.muscle_groups["Peitoral"], 12, 16, 20),
        )
    db_conn.commit()
    return training_type_id


def _llm(monkeypatch, output, *, tokens=(1000, 250)):
    """Substitui a UNICA porta de saida para a OpenAI."""
    chamadas = []

    def _fake(prompt, model, schema, *, schema_name, timeout=90, **_kw):
        chamadas.append({"prompt": prompt, "model": model, "timeout": timeout})
        return StructuredResult(data=output, model=model, tokens_in=tokens[0], tokens_out=tokens[1])

    monkeypatch.setattr(periodization_core, "call_openai_structured", _fake)
    return chamadas


def _saida(slots, volume_mode="percent_budget", name="ABC gerado"):
    return {
        "template": {"name": name, "description": None, "volume_mode": volume_mode, "slots": slots},
        "rationale": "Divisao ABC porque ...",
        "source_citations": ["TREINO_ABC.md"],
    }


def _item(**campos):
    base = {
        "item_kind": "muscle_group",
        "muscle_group_id": None,
        "priority_min_pct": None,
        "priority_max_pct": None,
        "prioritize": False,
        "mapping_id": None,
        "equipment_id": None,
        "sets_count": None,
        "reps_target": None,
        "rpe_target": None,
        "advanced_technique": None,
        "advanced_technique_applications": None,
        "advanced_technique_duration_seconds": None,
        "warmup_sets_count": None,
        "warmup_reps": None,
        "warmup_rest_seconds": None,
        "feeler_set_sets_count": None,
        "feeler_set_reps": None,
        "feeler_set_rest_seconds": None,
        "peak_contraction_seconds": None,
    }
    base.update(campos)
    return base


def _slot(sequence, items, slot_type="normal"):
    return {"sequence": sequence, "name": f"Dia {sequence}", "slot_type": slot_type, "weekday": None, "items": items}


def _pedido(catalog, **extra):
    pedido = {
        "rag_sources": [],
        "training_type_id": None,
        "volume_mode": "percent_budget",
        "gym_id": None,
        "days_per_cycle": 2,
        "instructions": None,
    }
    pedido.update(extra)
    return pedido


def _contar_templates(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM workout_plan_templates")
        return int(cur.fetchone()["n"])


def _relatorio_do_erro(exc: PeriodizationError) -> dict:
    return json.loads(exc.detail)["validation_report"]


def _codigos(report, chave) -> list[str]:
    return [entry["code"] for entry in report[chave]]


# ---------------------------------------------------------------------------
# Caminho feliz
# ---------------------------------------------------------------------------


def test_json_valido_gera_rascunho_e_nao_grava_nenhum_template(db_conn, catalog, academia, monkeypatch):
    saida = _saida(
        [
            _slot(1, [_item(muscle_group_id=catalog.muscle_groups["Peitoral"], priority_min_pct=45, priority_max_pct=55)]),
            _slot(2, [_item(muscle_group_id=catalog.muscle_groups["Costas"], priority_min_pct=45, priority_max_pct=55)]),
        ]
    )
    chamadas = _llm(monkeypatch, saida)

    with recorded_sql("pymysql", monkeypatch, None) as sql:
        resultado = run_periodization_draft(_pedido(catalog), catalog.user_id, None)

    assert chamadas[0]["timeout"] == 90
    assert resultado["validation_report"] == {"errors": [], "warnings": [], "auto_fixes": []}
    assert resultado["prompt_version"] == PROMPT_VERSION
    assert resultado["rationale"].startswith("Divisao ABC")
    assert resultado["source_citations"] == ["TREINO_ABC.md"]

    draft = resultado["draft"]
    assert [slot["sequence"] for slot in draft["slots"]] == [1, 2]
    # item_order e do backend, nao da LLM
    assert draft["slots"][0]["items"][0]["item_order"] == 1

    # A prova central do PR: nada foi gravado em template nenhum.
    escritas = [stmt for stmt in sql if WRITE_SQL.match(stmt)]
    assert not [stmt for stmt in escritas if any(table in stmt for table in TEMPLATE_TABLES)], escritas
    assert _contar_templates(db_conn) == 0


def test_rascunho_valido_passa_pelo_save_de_modelo_sem_ajuste(db_conn, catalog, academia, monkeypatch):
    """O rascunho tem de ser aceitavel pelo `POST /workout-plan-templates` como
    esta - senao o usuario do PR-17 receberia um rascunho impossivel de salvar."""
    from app.models import WorkoutPlanTemplateIn

    saida = _saida([_slot(1, [_item(muscle_group_id=catalog.muscle_groups["Peitoral"], priority_min_pct=90, priority_max_pct=100)])])
    _llm(monkeypatch, saida)
    resultado = run_periodization_draft(_pedido(catalog, days_per_cycle=1), catalog.user_id, None)
    workout_plan_templates._validate_template_payload(WorkoutPlanTemplateIn(**resultado["draft"]))
    assert _contar_templates(db_conn) == 0


def _cardio(**campos):
    base = {
        "cardio_mode": "continuous",
        "cardio_warmup_minutes": 5,
        "cardio_duration_minutes": 30,
        "cardio_target_zone": 2,
        "cardio_cooldown_minutes": 5,
        "hiit_low_seconds": None,
        "hiit_high_seconds": None,
        "hiit_low_zone": None,
        "hiit_high_zone": None,
        "hiit_rounds": None,
    }
    base.update(campos)
    return base


def test_m98_c5_cardio_pedido_vai_para_o_fim_do_slot_com_envelope(db_conn, catalog, academia, monkeypatch):
    """M98 C5 ponta a ponta: preferencias pedem cardio -> schema com
    `cardio_items` e secao de cardio no prompt; HIIT na vespera de perna vira
    Normal (envelope do dono), descanso com cardio continua descanso, o cardio
    entra depois da forca e o rascunho passa no save do Modelo. Nada gravado."""
    from app.models import WorkoutPlanTemplateIn

    hiit = _cardio(
        cardio_mode="hiit", cardio_duration_minutes=None, cardio_target_zone=None, hiit_low_seconds=120,
        hiit_high_seconds=120, hiit_low_zone=2, hiit_high_zone=4, hiit_rounds=5,
    )
    saida = _saida(
        [
            {**_slot(1, [_item(muscle_group_id=catalog.muscle_groups["Peitoral"], priority_min_pct=90, priority_max_pct=100)]), "cardio_items": [hiit]},
            {**_slot(2, [_item(muscle_group_id=catalog.muscle_groups["Quadríceps"], priority_min_pct=90, priority_max_pct=100)]), "cardio_items": []},
            {**_slot(3, [], slot_type="descanso"), "cardio_items": [_cardio()]},
        ]
    )
    esquemas = []

    def _fake(prompt, model, schema, *, schema_name, timeout=90, **_kw):
        esquemas.append((prompt, schema))
        return StructuredResult(data=saida, model=model, tokens_in=1000, tokens_out=250)

    monkeypatch.setattr(periodization_core, "call_openai_structured", _fake)
    resultado = run_periodization_draft(
        _pedido(catalog, days_per_cycle=3, instructions="Quero cardio depois do treino e no descanso."),
        catalog.user_id,
        None,
    )

    prompt, schema = esquemas[0]
    exigir("`cardio_items`" in prompt, "secao de cardio no prompt (campo cardio_items)")
    assert "cardio_items" in schema["properties"]["template"]["properties"]["slots"]["items"]["properties"]
    peito, perna, descanso = resultado["draft"]["slots"]
    assert [i["item_kind"] for i in peito["items"]] == ["muscle_group", "cardio"]
    assert peito["items"][-1]["cardio_mode"] == "continuous"
    assert [i["item_kind"] for i in perna["items"]] == ["muscle_group"]
    assert descanso["slot_type"] == "descanso" and [i["item_kind"] for i in descanso["items"]] == ["cardio"]
    assert "CARDIO_ADJUSTED" in _codigos(resultado["validation_report"], "auto_fixes")
    workout_plan_templates._validate_template_payload(WorkoutPlanTemplateIn(**resultado["draft"]))
    assert _contar_templates(db_conn) == 0


def test_m98_c5_sem_pedido_de_cardio_o_schema_e_o_da_v12(db_conn, catalog, academia, monkeypatch):
    saida = _saida([_slot(1, [_item(muscle_group_id=catalog.muscle_groups["Peitoral"], priority_min_pct=90, priority_max_pct=100)])])
    esquemas = []

    def _fake(prompt, model, schema, *, schema_name, timeout=90, **_kw):
        esquemas.append((prompt, schema))
        return StructuredResult(data=saida, model=model, tokens_in=1000, tokens_out=250)

    monkeypatch.setattr(periodization_core, "call_openai_structured", _fake)
    run_periodization_draft(_pedido(catalog, days_per_cycle=1), catalog.user_id, None)
    prompt, schema = esquemas[0]
    exigir("cardio_items" not in prompt, "sem secao de cardio no prompt")
    assert "cardio_items" not in schema["properties"]["template"]["properties"]["slots"]["items"]["properties"]


# ---------------------------------------------------------------------------
# Cascata de validacao: o que precisa ser recusado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "saida_crua, codigo",
    [
        ({"rationale": "sem template"}, "SCHEMA_INVALID"),
        ({"template": {"name": "X", "volume_mode": "inventado", "slots": []}, "rationale": "", "source_citations": []}, "SCHEMA_INVALID"),
        (
            {
                "template": {
                    "name": "X",
                    "description": None,
                    "volume_mode": "percent_budget",
                    "slots": [{"sequence": 1, "name": "D", "slot_type": "normal", "weekday": None, "items": [{"item_kind": "inventado"}]}],
                },
                "rationale": "",
                "source_citations": [],
            },
            "SCHEMA_INVALID",
        ),
    ],
)
def test_saida_fora_do_schema_falha_o_job_com_relatorio(db_conn, catalog, academia, monkeypatch, saida_crua, codigo):
    _llm(monkeypatch, saida_crua)
    with pytest.raises(PeriodizationError) as exc:
        run_periodization_draft(_pedido(catalog), catalog.user_id, None)
    assert _codigos(_relatorio_do_erro(exc.value), "errors") == [codigo]
    assert _contar_templates(db_conn) == 0


def test_sequence_furada_e_renumerada_em_vez_de_reprovar(db_conn, catalog, academia, monkeypatch):
    """Sequencias 1 e 3 em dois slots: a regra "sequence unica de 1 a N"
    continua sendo a do `_validate_template_payload` (nao uma copia), mas agora
    o rascunho chega consertado - a ordem entregue vira 1 e 2 e o conserto fica
    registrado em `auto_fixes`."""
    grupo = catalog.muscle_groups["Peitoral"]
    _llm(monkeypatch, _saida([_slot(1, [_item(muscle_group_id=grupo)]), _slot(3, [_item(muscle_group_id=grupo)])]))
    resultado = run_periodization_draft(_pedido(catalog), catalog.user_id, None)

    report = resultado["validation_report"]
    assert report["errors"] == []
    renumerados = [fix for fix in report["auto_fixes"] if fix["code"] == "SLOT_SEQUENCE_RENUMBERED"]
    assert [(fix["from"], fix["to"]) for fix in renumerados] == [(3, 2)]
    assert [slot["sequence"] for slot in resultado["draft"]["slots"]] == [1, 2]
    assert _contar_templates(db_conn) == 0


def test_slot_de_descanso_com_item_de_grupo_vira_dia_normal(db_conn, catalog, academia, monkeypatch):
    """Descanso com exercicio prescrito: o conteudo manda (apagar o exercicio
    seria jogar fora o trabalho da geracao), o dia vira 'normal' e o conserto
    fica no relatorio."""
    _llm(
        monkeypatch,
        _saida([_slot(1, [_item(muscle_group_id=catalog.muscle_groups["Peitoral"])], slot_type="descanso")]),
    )
    resultado = run_periodization_draft(_pedido(catalog, days_per_cycle=1), catalog.user_id, None)

    report = resultado["validation_report"]
    assert report["errors"] == []
    assert [fix["code"] for fix in report["auto_fixes"]] == ["SLOT_TYPE_FIXED"]
    assert resultado["draft"]["slots"][0]["slot_type"] == "normal"
    assert _contar_templates(db_conn) == 0


def test_grupo_muscular_inexistente_reprova(db_conn, catalog, academia, monkeypatch):
    _llm(monkeypatch, _saida([_slot(1, [_item(muscle_group_id=987654)])]))
    with pytest.raises(PeriodizationError) as exc:
        run_periodization_draft(_pedido(catalog, days_per_cycle=1), catalog.user_id, None)
    report = _relatorio_do_erro(exc.value)
    assert report["errors"][0]["code"] == "MUSCLE_GROUP_NOT_FOUND"
    assert report["errors"][0]["muscle_group_id"] == 987654
    assert _contar_templates(db_conn) == 0


@pytest.mark.parametrize("cenario", ["inexistente", "outra_academia"])
def test_equipamento_fora_da_academia_e_trocado_por_um_par_do_mesmo_grupo(
    db_conn, catalog, academia, monkeypatch, cenario
):
    """Confiabilidade (2026-09-28): par fora da academia deixou de matar o
    rascunho inteiro quando existe substituto do MESMO grupo DENTRO dela. A
    academia tem (Supino reto, Banco reto) em Peitoral, entao o item vira esse
    par e o conserto fica no relatorio - o rascunho continua sem nenhum
    equipamento que a academia nao tenha, que era a garantia original."""
    if cenario == "inexistente":
        mapping_id, equipment_id = 999999, 888888
    else:  # par que existe, mas so na OUTRA academia
        mapping_id, equipment_id = catalog.exercises["Agachamento"], catalog.equipment["Rack"]
    _llm(
        monkeypatch,
        _saida(
            [
                _slot(
                    1,
                    [
                        _item(
                            item_kind="equipment",
                            muscle_group_id=catalog.muscle_groups["Peitoral"],
                            mapping_id=mapping_id,
                            equipment_id=equipment_id,
                            sets_count=3,
                        )
                    ],
                )
            ],
            volume_mode="equipment_series",
        ),
    )
    resultado = run_periodization_draft(
        _pedido(catalog, days_per_cycle=1, volume_mode="equipment_series", gym_id=catalog.gym_id),
        catalog.user_id,
        None,
    )

    report = resultado["validation_report"]
    assert report["errors"] == []
    trocas = [fix for fix in report["auto_fixes"] if fix["code"] == "EQUIPMENT_REPLACED"]
    assert len(trocas) == 1
    assert trocas[0]["from"] == {"mapping_id": mapping_id, "equipment_id": equipment_id}
    assert trocas[0]["to"] == {
        "mapping_id": catalog.exercises["Supino reto"],
        "equipment_id": catalog.equipment["Banco reto"],
    }
    item = resultado["draft"]["slots"][0]["items"][0]
    assert (item["mapping_id"], item["equipment_id"]) == (
        catalog.exercises["Supino reto"],
        catalog.equipment["Banco reto"],
    )
    assert _contar_templates(db_conn) == 0


def test_equipamento_de_grupo_sem_nenhum_par_na_academia_continua_reprovando(
    db_conn, catalog, academia, monkeypatch
):
    """A promessa de seguranca nao e negociada por taxa de sucesso: Quadriceps
    nao tem NENHUM par ativo nesta academia, entao nao ha substituto e o
    rascunho continua recusado com `EQUIPMENT_NOT_IN_GYM`."""
    _llm(
        monkeypatch,
        _saida(
            [
                _slot(
                    1,
                    [
                        _item(
                            item_kind="equipment",
                            muscle_group_id=catalog.muscle_groups["Quadríceps"],
                            mapping_id=catalog.exercises["Agachamento"],
                            equipment_id=catalog.equipment["Rack"],
                            sets_count=3,
                        )
                    ],
                )
            ],
            volume_mode="equipment_series",
        ),
    )
    with pytest.raises(PeriodizationError) as exc:
        run_periodization_draft(
            _pedido(catalog, days_per_cycle=1, volume_mode="equipment_series", gym_id=catalog.gym_id),
            catalog.user_id,
            None,
        )
    report = _relatorio_do_erro(exc.value)
    assert report["errors"][0]["code"] == "EQUIPMENT_NOT_IN_GYM"
    assert report["errors"][0]["equipment_id"] == catalog.equipment["Rack"]
    assert [fix["code"] for fix in report["auto_fixes"] if fix["code"] == "EQUIPMENT_REPLACED"] == []
    assert _contar_templates(db_conn) == 0


@pytest.mark.parametrize("min_pct, max_pct", [(60, 40), (0, 140), (-5, 50)])
def test_percentuais_invalidos_reprovam(db_conn, catalog, academia, monkeypatch, min_pct, max_pct):
    _llm(
        monkeypatch,
        _saida(
            [
                _slot(
                    1,
                    [
                        _item(
                            muscle_group_id=catalog.muscle_groups["Peitoral"],
                            priority_min_pct=min_pct,
                            priority_max_pct=max_pct,
                        )
                    ],
                )
            ]
        ),
    )
    with pytest.raises(PeriodizationError) as exc:
        run_periodization_draft(_pedido(catalog, days_per_cycle=1), catalog.user_id, None)
    assert _codigos(_relatorio_do_erro(exc.value), "errors") == ["TEMPLATE_INVALID"]


def test_volume_mode_diferente_do_pedido_reprova(db_conn, catalog, academia, monkeypatch):
    _llm(
        monkeypatch,
        _saida(
            [_slot(1, [_item(item_kind="equipment", muscle_group_id=catalog.muscle_groups["Peitoral"], mapping_id=catalog.exercises["Supino reto"], equipment_id=catalog.equipment["Banco reto"], sets_count=3)])],
            volume_mode="equipment_series",
        ),
    )
    with pytest.raises(PeriodizationError) as exc:
        run_periodization_draft(_pedido(catalog, days_per_cycle=1), catalog.user_id, None)
    assert "VOLUME_MODE_MISMATCH" in _codigos(_relatorio_do_erro(exc.value), "errors")


# ---------------------------------------------------------------------------
# Warnings e auto-fixes: rascunho continua utilizavel
# ---------------------------------------------------------------------------


def test_meta_incompativel_gera_warning_de_ativacao_e_nao_erro(db_conn, catalog, academia, metas, monkeypatch):
    """Peitoral tem meta minima 12 series/semana e o rascunho entrega 30% dela:
    warning de cobertura, job com sucesso, decisao do humano."""
    _llm(
        monkeypatch,
        _saida(
            [
                _slot(
                    1,
                    [
                        _item(
                            muscle_group_id=catalog.muscle_groups["Peitoral"],
                            priority_min_pct=30,
                            priority_max_pct=30,
                        )
                    ],
                )
            ]
        ),
    )
    resultado = run_periodization_draft(
        _pedido(catalog, days_per_cycle=1, training_type_id=metas), catalog.user_id, None
    )
    warnings = resultado["validation_report"]["warnings"]
    assert [w["code"] for w in warnings] == ["ACTIVATION_COVERAGE"]
    assert warnings[0]["status"] == "UNDER_ALLOCATED"
    assert warnings[0]["muscle_group_id"] == catalog.muscle_groups["Peitoral"]
    assert resultado["validation_report"]["errors"] == []
    assert _contar_templates(db_conn) == 0


def test_ativacao_projetada_do_modo_b_compara_com_a_meta_da_janela(db_conn, catalog, academia, metas, monkeypatch):
    """Modo B tem series de verdade: a projecao usa `preview_activation` (com a
    janela `max(7, ciclo)` do fix do PR-13) e compara com a meta escalada."""
    _llm(
        monkeypatch,
        _saida(
            [
                _slot(
                    1,
                    [
                        _item(
                            item_kind="equipment",
                            muscle_group_id=catalog.muscle_groups["Peitoral"],
                            mapping_id=catalog.exercises["Supino reto"],
                            equipment_id=catalog.equipment["Banco reto"],
                            sets_count=1,
                            reps_target="8-12",
                            rpe_target="8",
                        )
                    ],
                )
            ],
            volume_mode="equipment_series",
        ),
    )
    resultado = run_periodization_draft(
        _pedido(
            catalog,
            days_per_cycle=1,
            volume_mode="equipment_series",
            gym_id=catalog.gym_id,
            training_type_id=metas,
        ),
        catalog.user_id,
        None,
    )
    warning = next(w for w in resultado["validation_report"]["warnings"] if w["code"] == "ACTIVATION_BELOW_MIN")
    assert warning["muscle_group_name"] == "Peitoral"
    # 1 slot repetido nos 7 dias da janela x 1 serie de Supino reto (peso 1.0 em
    # Peitoral) = 7 series projetadas, contra a meta minima semanal de 12.
    assert warning["window_days"] == 7
    assert warning["min"] == pytest.approx(12.0)
    assert warning["projected"] == pytest.approx(7.0)
    assert resultado["validation_report"]["errors"] == []


def test_rpe_acima_do_teto_do_par_e_clampado(db_conn, catalog, academia, monkeypatch):
    _llm(
        monkeypatch,
        _saida(
            [
                _slot(
                    1,
                    [
                        _item(
                            item_kind="equipment",
                            muscle_group_id=catalog.muscle_groups["Peitoral"],
                            mapping_id=catalog.exercises["Supino reto"],
                            equipment_id=catalog.equipment["Banco reto"],
                            sets_count=3,
                            rpe_target="10",
                        )
                    ],
                )
            ],
            volume_mode="equipment_series",
        ),
    )
    resultado = run_periodization_draft(
        _pedido(catalog, days_per_cycle=1, volume_mode="equipment_series", gym_id=catalog.gym_id),
        catalog.user_id,
        None,
    )
    fixes = resultado["validation_report"]["auto_fixes"]
    assert [fix["code"] for fix in fixes] == ["RPE_CLAMPED"]
    assert (fixes[0]["from"], fixes[0]["to"]) == ("10", "9")
    assert fixes[0]["reason"] == "EQUIPMENT_SAFETY"
    assert resultado["draft"]["slots"][0]["items"][0]["rpe_target"] == "9"
    assert resultado["validation_report"]["errors"] == []


@pytest.mark.parametrize("codigo, motivo", [("drop_set", "TECHNIQUE_NOT_COMPATIBLE"), ("inventada", "TECHNIQUE_UNKNOWN")])
def test_tecnica_invalida_no_par_e_removida_deterministicamente(
    db_conn, catalog, academia, monkeypatch, codigo, motivo
):
    """Sem linha na matriz de compatibilidade, tecnica e INCOMPATIVEL (D3); e
    codigo fora do catalogo ativo e desconhecido. Nos dois casos o rascunho
    continua util: remove a tecnica e avisa."""
    _llm(
        monkeypatch,
        _saida(
            [
                _slot(
                    1,
                    [
                        _item(
                            item_kind="equipment",
                            muscle_group_id=catalog.muscle_groups["Peitoral"],
                            mapping_id=catalog.exercises["Supino reto"],
                            equipment_id=catalog.equipment["Banco reto"],
                            sets_count=3,
                            advanced_technique=codigo,
                            advanced_technique_applications=2,
                        )
                    ],
                )
            ],
            volume_mode="equipment_series",
        ),
    )
    resultado = run_periodization_draft(
        _pedido(catalog, days_per_cycle=1, volume_mode="equipment_series", gym_id=catalog.gym_id),
        catalog.user_id,
        None,
    )
    fixes = [fix for fix in resultado["validation_report"]["auto_fixes"] if fix["code"] == "TECHNIQUE_REMOVED"]
    assert fixes and fixes[0]["technique"] == codigo
    assert fixes[0]["reason"] == motivo
    item = resultado["draft"]["slots"][0]["items"][0]
    assert item["advanced_technique"] is None
    assert item["advanced_technique_applications"] is None
    assert resultado["validation_report"]["errors"] == []


# ---------------------------------------------------------------------------
# Falha da OpenAI e auditoria
# ---------------------------------------------------------------------------


def test_timeout_da_openai_vira_job_failed_pelo_handler_da_lambda(db_conn, catalog, academia, monkeypatch):
    def _estoura(*args, **kwargs):
        raise OpenAIGenerationError("Timeout de 90s ao chamar OpenAI.")

    monkeypatch.setattr(periodization_core, "call_openai_structured", _estoura)
    marcados = {}
    monkeypatch.setattr("app.async_jobs.mark_failed", lambda job_id, error: marcados.update(failed=(job_id, error)))
    monkeypatch.setattr(
        "app.async_jobs.mark_done", lambda job_id, result: pytest.fail("job com timeout nao pode virar done")
    )

    monkeypatch.setattr("app.async_jobs.claim_job", lambda job_id: True)  # M81
    lambda_plan_generator.handler(
        {
            "job_id": "job-1",
            "job_kind": "periodization",
            "payload": _pedido(catalog, days_per_cycle=1),
            "user_id": catalog.user_id,
        },
        None,
    )
    job_id, error = marcados["failed"]
    assert job_id == "job-1"
    assert "Timeout de 90s" in error
    assert _contar_templates(db_conn) == 0


def test_job_kind_ausente_continua_sendo_geracao_de_ficha(monkeypatch):
    """Compatibilidade: o evento que `POST /workout-plans/generate` publica nao
    tem `job_kind` e nao pode mudar de caminho."""
    chamados = {}

    def _plan(payload, user_id):
        chamados["plan"] = (payload.template_id, user_id)
        return {"ok": True}

    monkeypatch.setattr(lambda_plan_generator, "dispatch_plan_generation", _plan)
    monkeypatch.setattr("app.async_jobs.mark_done", lambda job_id, result: chamados.setdefault("done", result))
    monkeypatch.setattr("app.async_jobs.claim_job", lambda job_id: True)  # M81
    lambda_plan_generator.handler({"job_id": "j", "payload": {"template_id": 7}, "user_id": 3}, None)
    assert chamados["plan"] == (7, 3)
    assert chamados["done"] == {"ok": True}


def test_auditoria_registra_tokens_status_e_relatorio(db_conn, catalog, academia, monkeypatch):
    from app.periodization.logs import create_pending_log

    pedido = _pedido(catalog, days_per_cycle=1, instructions="Joelho sensivel.")
    with db_conn.cursor() as cur:
        cur.execute("DELETE FROM template_generation_logs")
    db_conn.commit()
    log_id = create_pending_log(db_conn, catalog.user_id, pedido, PROMPT_VERSION)
    db_conn.commit()

    chamadas = _llm(monkeypatch, _saida([_slot(1, [_item(muscle_group_id=catalog.muscle_groups["Peitoral"])])]), tokens=(1500, 400))
    resultado = run_periodization_draft(pedido, catalog.user_id, log_id)
    assert resultado["generation_log_id"] == log_id

    with db_conn.cursor() as cur:
        cur.execute("SELECT * FROM template_generation_logs WHERE id = %s", (log_id,))
        row = cur.fetchone()
    assert row["status"] == "succeeded"
    assert (row["tokens_in"], row["tokens_out"]) == (1500, 400)
    # Custo pelo dono do preco (`ai_usage`, mapa §74), na escala da coluna.
    from app.periodization.logs import estimate_cost_usd

    esperado = estimate_cost_usd(chamadas[0]["model"], 1500, 400)
    assert (row["cost_usd"] is None) == (esperado is None)
    if esperado is not None:
        assert float(row["cost_usd"]) == pytest.approx(esperado)
    assert json.loads(row["validation_report"])["errors"] == []
    # instrucao do usuario fica SO aqui (secao 14)
    assert "Joelho sensivel." in json.loads(row["request_json"])["instructions"]
    assert _contar_templates(db_conn) == 0


def test_relatorio_de_erro_tambem_e_auditado_como_failed(db_conn, catalog, academia, monkeypatch):
    from app.periodization.logs import create_pending_log

    log_id = create_pending_log(db_conn, catalog.user_id, _pedido(catalog), PROMPT_VERSION)
    db_conn.commit()
    _llm(monkeypatch, _saida([_slot(1, [_item(muscle_group_id=987654)])]))
    with pytest.raises(PeriodizationError):
        run_periodization_draft(_pedido(catalog, days_per_cycle=1), catalog.user_id, log_id)

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, validation_report FROM template_generation_logs WHERE id = %s", (log_id,))
        row = cur.fetchone()
    assert row["status"] == "failed"
    assert json.loads(row["validation_report"])["errors"][0]["code"] == "MUSCLE_GROUP_NOT_FOUND"


# ---------------------------------------------------------------------------
# Endpoint (202, 422, 429)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(catalog, monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(workout_plan_templates.router)
    app.dependency_overrides[require_admin_or_pt] = lambda: Identity(
        user_id=catalog.user_id, role="admin", source="session"
    )
    invocacoes: list[dict] = []
    monkeypatch.setattr("app.async_jobs.create_job", lambda job_type, request=None, *, owner_user_id: f"job-{job_type}")
    monkeypatch.setattr(
        "app.async_jobs.invoke_async", lambda env_var, payload: invocacoes.append({"env": env_var, **payload})
    )
    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.invocacoes = invocacoes
    return test_client


def test_endpoint_responde_202_cria_log_pending_e_dispara_a_lambda(db_conn, catalog, academia, client):
    resposta = client.post(
        "/workout-plan-templates/periodization-drafts",
        json={"rag_sources": [], "days_per_cycle": 3, "instructions": "Enfase em costas."},
    )
    assert resposta.status_code == 202
    corpo = resposta.json()
    assert corpo["job_id"] == "job-periodization"
    assert corpo["status"] == "pending"

    invocacao = client.invocacoes[0]
    assert invocacao["env"] == "PLAN_GENERATOR_FUNCTION_NAME"
    assert invocacao["job_kind"] == "periodization"
    assert invocacao["generation_log_id"] == corpo["generation_log_id"]

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, prompt_version FROM template_generation_logs WHERE id = %s", (corpo["generation_log_id"],))
        row = cur.fetchone()
    assert (row["status"], row["prompt_version"]) == ("pending", PROMPT_VERSION)
    assert _contar_templates(db_conn) == 0


def test_fonte_de_rag_inexistente_responde_422_sem_criar_job(db_conn, catalog, academia, client):
    resposta = client.post(
        "/workout-plan-templates/periodization-drafts",
        json={"rag_sources": ["999. Pasta que nao existe"], "days_per_cycle": 3},
    )
    assert resposta.status_code == 422
    assert "Fonte(s) de RAG inexistente(s)" in resposta.json()["detail"]
    assert client.invocacoes == []
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM template_generation_logs")
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize(
    "corpo, trecho",
    [
        ({"volume_mode": "equipment_series"}, "exige uma academia"),
        ({"training_type_id": 999999}, "Tipo de treinamento"),
        ({"volume_mode": "equipment_series", "gym_id": 999999}, "Academia nao encontrada"),
    ],
)
def test_pedido_incoerente_responde_422_antes_de_gastar_token(catalog, academia, client, corpo, trecho):
    resposta = client.post("/workout-plan-templates/periodization-drafts", json={"days_per_cycle": 2, **corpo})
    assert resposta.status_code == 422
    assert trecho in resposta.json()["detail"]
    assert client.invocacoes == []


def test_limite_diario_de_geracoes_responde_429(db_conn, catalog, academia, client):
    for _ in range(DAILY_GENERATION_LIMIT):
        assert client.post("/workout-plan-templates/periodization-drafts", json={"days_per_cycle": 2}).status_code == 202
    resposta = client.post("/workout-plan-templates/periodization-drafts", json={"days_per_cycle": 2})
    assert resposta.status_code == 429
    assert f"{DAILY_GENERATION_LIMIT} geracoes" in resposta.json()["detail"]
    assert len(client.invocacoes) == DAILY_GENERATION_LIMIT
    assert _contar_templates(db_conn) == 0


# ---------------------------------------------------------------------------
# Procedencia: o modelo so nasce no POST explicito, e sabendo de onde veio
# ---------------------------------------------------------------------------


def _corpo_de_modelo(catalog, **extra):
    corpo = {
        "name": "Modelo PR-16",
        "slots": [
            {
                "sequence": 1,
                "name": "Dia 1",
                "items": [
                    {
                        "item_order": 1,
                        "item_kind": "muscle_group",
                        "muscle_group_id": catalog.muscle_groups["Peitoral"],
                    }
                ],
            }
        ],
    }
    corpo.update(extra)
    return corpo


def test_salvar_o_rascunho_marca_origin_periodization_e_fecha_a_auditoria(db_conn, catalog, academia, client):
    """Passo seguinte do fluxo (o PR-17 dispara isto pela UI): o MESMO
    `POST /workout-plan-templates` de sempre, agora informando de qual geracao o
    modelo saiu."""
    from app.periodization.logs import create_pending_log

    log_id = create_pending_log(db_conn, catalog.user_id, _pedido(catalog), PROMPT_VERSION)
    db_conn.commit()

    resposta = client.post("/workout-plan-templates", json=_corpo_de_modelo(catalog, generation_log_id=log_id))
    assert resposta.status_code == 200, resposta.text
    modelo = resposta.json()
    assert modelo["origin"] == "periodization"
    assert modelo["generation_log_id"] == log_id

    with db_conn.cursor() as cur:
        cur.execute("SELECT saved_template_id FROM template_generation_logs WHERE id = %s", (log_id,))
        assert cur.fetchone()["saved_template_id"] == modelo["id"]


def test_modelo_sem_geracao_continua_manual(db_conn, catalog, academia, client):
    resposta = client.post("/workout-plan-templates", json=_corpo_de_modelo(catalog))
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["origin"] == "manual"
    assert resposta.json()["generation_log_id"] is None


def test_geracao_de_outro_usuario_nao_serve_de_procedencia(db_conn, catalog, academia, client):
    from app.periodization.logs import create_pending_log

    with db_conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('outro-pr16', 'x', 'admin')")
        outro_user_id = cur.lastrowid
    log_id = create_pending_log(db_conn, outro_user_id, _pedido(catalog), PROMPT_VERSION)
    db_conn.commit()

    resposta = client.post("/workout-plan-templates", json=_corpo_de_modelo(catalog, generation_log_id=log_id))
    assert resposta.status_code == 422
    assert "geracao automatica deste usuario" in resposta.json()["detail"]
    assert _contar_templates(db_conn) == 0
