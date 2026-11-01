"""Escolha guiada pelo motor (prompt `periodization-v15`): a LLM escolhe a
referencia curta do catalogo da academia e o motor (`catalog_refs`) monta o
trio antes de qualquer validacao. Testes puros (sem banco, sem rede):

- indice deterministico, com o grupo da CHAVE do catalogo;
- catalogo do prompt com `ref` e sem os ids do par;
- `enum` do schema dentro do limite dos provedores, string livre acima dele;
- resolucao valida / desconhecida / caixa diferente, sem mutar a entrada;
- paridade do formato gravado: a saida resolvida vira o MESMO
  `WorkoutPlanTemplateIn` que a saida da v14 com o trio copiado certo;
- `run_periodization_draft` manda o schema do Modo B com a `enum` e entrega
  ao validador o trio resolvido, com a contagem no relatorio."""

from __future__ import annotations

import contextlib
import copy

import pytest

from app.models import PeriodizationDraftIn
from app.openai_client import StructuredResult
from app.periodization import catalog_refs as cr
from app.periodization import core
from app.periodization.output_schema import PeriodizationOutput, output_schema
from app.periodization.validation import _to_template_in


def _contexto():
    return {
        "request": {"volume_mode": "equipment_series", "days_per_cycle": 1, "gym_id": 7},
        "muscle_groups": [{"id": 1, "name": "Peitoral"}, {"id": 2, "name": "Costas"}, {"id": 3, "name": "Sem catalogo"}],
        # Ordem de insercao proposital fora da ordem alfabetica.
        "gym_catalog": {
            "Peitoral": [
                {"mapping_id": 10, "equipment_id": 100, "exercise_name": "Supino", "max_safe_rpe": 9},
                {"mapping_id": 11, "equipment_id": 101, "exercise_name": "Crucifixo", "max_safe_rpe": 10},
            ],
            "Costas": [{"mapping_id": 20, "equipment_id": 200, "exercise_name": "Remada", "max_safe_rpe": 9}],
            "Grupo fora do cadastro": [{"mapping_id": 30, "equipment_id": 300}],
        },
    }


def test_indice_e_deterministico_na_ordem_do_json_do_prompt():
    indice = cr.build_index(_contexto())
    # "Costas" < "Peitoral" (sort_keys do prompt); grupo sem cadastro fica de fora.
    assert indice == {
        "E1": cr.CatalogRef("E1", 2, 20, 200),
        "E2": cr.CatalogRef("E2", 1, 10, 100),
        "E3": cr.CatalogRef("E3", 1, 11, 101),
    }
    assert cr.build_index(copy.deepcopy(_contexto())) == indice
    assert cr.build_index({"gym_catalog": {}, "muscle_groups": []}) == {}


def test_catalogo_do_prompt_traz_a_ref_e_tira_os_ids_do_par():
    catalogo = cr.catalog_for_prompt(_contexto())
    assert list(catalogo) == ["Costas", "Peitoral"]
    assert catalogo["Peitoral"][0] == {"ref": "E2", "exercise_name": "Supino", "max_safe_rpe": 9}
    for linhas in catalogo.values():
        for linha in linhas:
            assert "mapping_id" not in linha and "equipment_id" not in linha


def test_enum_respeita_o_limite_dos_provedores():
    indice = cr.build_index(_contexto())
    assert cr.schema_enum(indice) == ["E1", "E2", "E3"]
    assert cr.schema_enum({}) is None
    grande = {f"E{n}": cr.CatalogRef(f"E{n}", 1, n, n) for n in range(1, cr.MAX_ENUM_REFS + 2)}
    assert cr.schema_enum(grande) is None  # acima do teto: string livre
    # O teto e o limiar da OpenAI strict (acima de 250 vale o teto de caracteres).
    assert cr.MAX_ENUM_REFS == 250


def _saida(*refs, extra=None):
    itens = [
        {cr.CATALOG_REF_FIELD: ref, "prioritize": False, "sets_count": 3, "reps_target": "8-12", "rpe_target": "8"}
        for ref in refs
    ]
    return {
        "template": {
            "name": "M",
            "description": None,
            "volume_mode": "equipment_series",
            "slots": [{"sequence": 1, "name": "A", "slot_type": "normal", "weekday": None, "items": itens, **(extra or {})}],
        },
        "rationale": "r",
        "source_citations": [],
    }


def test_resolucao_monta_o_trio_e_conta_desconhecidas_sem_mutar_a_entrada():
    indice = cr.build_index(_contexto())
    crua = _saida("E2", " e1 ", "E99")
    original = copy.deepcopy(crua)
    resolvida, stats = cr.resolve_output(crua, indice)
    assert crua == original
    itens = resolvida["template"]["slots"][0]["items"]
    assert [(i["item_kind"], i["muscle_group_id"], i["mapping_id"], i["equipment_id"]) for i in itens] == [
        ("equipment", 1, 10, 100),
        ("equipment", 2, 20, 200),  # caixa/espaco ignorados
        ("equipment", None, None, None),  # desconhecida: trio vazio, o validador remove
    ]
    assert all(cr.CATALOG_REF_FIELD not in i for i in itens)
    assert stats.to_dict() == {"total": 3, "resolved": 2, "unknown": 1}
    assert stats.unknown == [{"slot": 1, "item": 3, "ref": "E99"}]


def test_saida_do_modo_a_passa_intocada():
    crua = {"template": {"slots": [{"sequence": 1, "items": [{"item_kind": "muscle_group", "muscle_group_id": 1}]}]}}
    resolvida, stats = cr.resolve_output(crua, {})
    assert resolvida == crua and stats.total == 0
    assert cr.attach_to_report({"errors": []}, stats) == {"errors": []}


def test_relatorio_ganha_contagem_e_auto_fix_auditavel():
    _resolvida, stats = cr.resolve_output(_saida("E1", "X9"), cr.build_index(_contexto()))
    relatorio = cr.attach_to_report({"errors": [], "warnings": [], "auto_fixes": []}, stats)
    assert relatorio[cr.REPORT_KEY] == {"total": 2, "resolved": 1, "unknown": 1}
    assert relatorio["auto_fixes"] == [{"code": cr.CATALOG_REF_UNKNOWN, "slot": 1, "item": 2, "ref": "X9"}]


def test_formato_gravado_igual_ao_da_v14():
    """Paridade: a saida v15 resolvida e a saida v14 com o trio certo viram o
    MESMO `WorkoutPlanTemplateIn` (o que vai ao banco e ao frontend)."""
    pedido = PeriodizationDraftIn(volume_mode="equipment_series", gym_id=7, days_per_cycle=1)
    resolvida, _stats = cr.resolve_output(_saida("E2", "E1"), cr.build_index(_contexto()))
    v14 = copy.deepcopy(_saida())
    v14["template"]["slots"][0]["items"] = [
        {
            "item_kind": "equipment",
            "muscle_group_id": grupo,
            "priority_min_pct": None,
            "priority_max_pct": None,
            "prioritize": False,
            "mapping_id": mapping,
            "equipment_id": equipamento,
            "sets_count": 3,
            "reps_target": "8-12",
            "rpe_target": "8",
            "rest_seconds": 90,  # a v14 deixava a LLM mandar; o validador zera
        }
        for grupo, mapping, equipamento in ((1, 10, 100), (2, 20, 200))
    ]
    novo = _to_template_in(PeriodizationOutput(**resolvida), pedido).model_dump(mode="json")
    antigo = _to_template_in(PeriodizationOutput(**v14), pedido).model_dump(mode="json")
    assert novo == antigo


def _objetos(no):
    if isinstance(no, dict):
        if no.get("type") == "object":
            yield no
        for valor in no.values():
            yield from _objetos(valor)
    elif isinstance(no, list):
        for valor in no:
            yield from _objetos(valor)


@pytest.mark.parametrize("com_cardio", [False, True])
@pytest.mark.parametrize("refs", [["E1", "E2"], None])
def test_schema_do_modo_b_segue_o_strict_da_openai(com_cardio, refs):
    schema = output_schema(com_cardio, True, refs)
    for objeto in _objetos(schema):
        assert objeto["additionalProperties"] is False
        assert set(objeto["required"]) == set(objeto["properties"])


def test_run_manda_enum_e_entrega_o_trio_resolvido_ao_validador(monkeypatch):
    contexto = _contexto()
    chamadas: list[dict] = []
    validados: list[dict] = []

    def _fake(prompt, model, schema, **kwargs):
        chamadas.append(schema)
        return StructuredResult(data=_saida("E3", "E77"), model=model, tokens_in=1, tokens_out=1)

    def _validar(conn, request, data, **_kw):
        validados.append(data)
        return data, {"errors": [], "warnings": [], "auto_fixes": []}, {"rationale": "", "source_citations": []}

    monkeypatch.setenv(core.MODEL_ENV_VAR, "gpt-x")
    monkeypatch.setattr(core, "call_openai_structured", _fake)
    monkeypatch.setattr(core, "transaction", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(core, "build_context", lambda conn, request, user_id: contexto)
    monkeypatch.setattr(core, "build_prompt", lambda context: "prompt")
    monkeypatch.setattr(core, "build_prompt_cache_prefix", lambda context: None)
    monkeypatch.setattr(core, "finish_log", lambda *a, **k: None)
    monkeypatch.setattr(core, "validate_draft", _validar)
    resultado = core.run_periodization_draft(
        {"volume_mode": "equipment_series", "gym_id": 7, "days_per_cycle": 1}, user_id=None
    )
    item = chamadas[0]["properties"]["template"]["properties"]["slots"]["items"]["properties"]["items"]["items"]
    assert item["properties"][cr.CATALOG_REF_FIELD]["enum"] == ["E1", "E2", "E3"]
    itens = validados[0]["template"]["slots"][0]["items"]
    assert (itens[0]["muscle_group_id"], itens[0]["mapping_id"], itens[0]["equipment_id"]) == (1, 11, 101)
    assert itens[1]["mapping_id"] is None
    assert resultado["validation_report"][cr.REPORT_KEY] == {"total": 2, "resolved": 1, "unknown": 1}
    assert resultado["prompt_version"] == "periodization-v15"
