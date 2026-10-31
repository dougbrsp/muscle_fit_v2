"""Adaptador dos schemas strict (OpenAI) para a saida estruturada da Anthropic.

Cada schema REAL das features cabe nos limites do subconjunto depois da
adaptacao, e `restore` devolve exatamente a forma original."""

from __future__ import annotations

import copy

import pytest

from app import cardio_recommendation_v3, exercise_description_ai, exercise_set_evaluation_ai
from app import nutrition_label_ai, nutrition_meal_parse_ai
from app.llm_anthropic_schema import (
    ITEMS,
    MAX_OPTIONAL_PARAMS,
    MAX_UNION_PARAMS,
    SchemaAdaptationError,
    _counts,
    adapt_schema,
    restore,
)
from app.periodization.output_schema import output_schema

SCHEMAS_REAIS = {
    "set_evaluation": lambda: exercise_set_evaluation_ai.SET_EVALUATION_SCHEMA,
    "meal_parse": nutrition_meal_parse_ai.output_schema,
    "label_photo": nutrition_label_ai.output_schema,
    "exercise_description": lambda: exercise_description_ai.OUTPUT_SCHEMA,
    "cardio_agent": cardio_recommendation_v3.llm_schema,
    "periodization": lambda: output_schema(False),
    "periodization_cardio": lambda: output_schema(True),
}


def _nulavel(n: int) -> dict:
    props = {f"c{i}": {"type": ["integer", "null"]} for i in range(n)}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


@pytest.mark.parametrize("nome", SCHEMAS_REAIS)
def test_schema_real_cabe_nos_limites_e_nao_muda_o_original(nome):
    original = SCHEMAS_REAIS[nome]()
    copia = copy.deepcopy(original)
    adaptado = adapt_schema(original)
    assert original == copia  # o schema da OpenAI nao e tocado
    unioes, opcionais = _counts(adaptado.schema)
    assert unioes <= MAX_UNION_PARAMS and opcionais <= MAX_OPTIONAL_PARAMS
    assert adaptado.removed_keywords == ()


def test_schema_dentro_do_limite_vai_igual():
    schema = _nulavel(MAX_UNION_PARAMS)
    adaptado = adapt_schema(schema)
    assert adaptado.schema == schema and not adaptado.optional_paths


def test_excedente_de_anulaveis_vira_opcional_e_volta_como_none():
    schema = _nulavel(MAX_UNION_PARAMS + 3)
    adaptado = adapt_schema(schema)
    convertidos = sorted(p[0] for p in adaptado.optional_paths)
    assert convertidos == [f"c{i}" for i in range(MAX_UNION_PARAMS, MAX_UNION_PARAMS + 3)]
    for nome in convertidos:
        assert adaptado.schema["properties"][nome] == {"type": "integer"}
        assert nome not in adaptado.schema["required"]
    resposta = {f"c{i}": i for i in range(MAX_UNION_PARAMS)}
    resposta[f"c{MAX_UNION_PARAMS}"] = 7
    devolvido = restore(resposta, schema, adaptado.optional_paths)
    assert list(devolvido) == list(schema["properties"])  # ordem do schema
    assert devolvido[f"c{MAX_UNION_PARAMS}"] == 7
    assert devolvido[f"c{MAX_UNION_PARAMS + 1}"] is None and devolvido[f"c{MAX_UNION_PARAMS + 2}"] is None


def test_restore_dentro_de_listas_e_anyof():
    item = _nulavel(MAX_UNION_PARAMS + 1)
    schema = {
        "type": "object",
        "properties": {"lista": {"type": "array", "items": {"anyOf": [item, {"type": "null"}]}}},
        "required": ["lista"],
        "additionalProperties": False,
    }
    adaptado = adapt_schema(schema)
    assert ("lista", ITEMS, f"c{MAX_UNION_PARAMS}") in adaptado.optional_paths
    devolvido = restore({"lista": [{"c0": 1}, None]}, schema, adaptado.optional_paths)
    assert devolvido["lista"][0][f"c{MAX_UNION_PARAMS}"] is None and devolvido["lista"][1] is None


def _amostra(no):
    """Resposta minima valida para o schema ADAPTADO: so os campos obrigatorios."""
    if "anyOf" in no:
        return _amostra(no["anyOf"][0])
    tipo = no.get("type")
    tipo = tipo[0] if isinstance(tipo, list) else tipo
    if "enum" in no:
        return no["enum"][0]
    if tipo == "object":
        return {k: _amostra(v) for k, v in no.get("properties", {}).items() if k in no.get("required", [])}
    if tipo == "array":
        return [_amostra(no["items"])]
    return {"string": "x", "integer": 1, "number": 1.5, "boolean": True, "null": None}[tipo]


def _faltando(dado, no, caminho=()):
    """Chaves obrigatorias do schema ORIGINAL ausentes na resposta devolvida."""
    if "anyOf" in no:
        ramo = next((v for v in no["anyOf"] if "properties" in v or "items" in v), None)
        return _faltando(dado, ramo, caminho) if ramo and dado is not None else []
    if isinstance(dado, dict):
        saida = [caminho + (k,) for k in no.get("required", []) if k not in dado]
        for k, v in no.get("properties", {}).items():
            if k in dado:
                saida += _faltando(dado[k], v, caminho + (k,))
        return saida
    if isinstance(dado, list):
        return [f for item in dado for f in _faltando(item, no["items"], caminho + (ITEMS,))]
    return []


@pytest.mark.parametrize("nome", SCHEMAS_REAIS)
def test_ida_e_volta_devolve_toda_chave_obrigatoria_do_original(nome):
    original = SCHEMAS_REAIS[nome]()
    adaptado = adapt_schema(original)
    resposta = _amostra(adaptado.schema)
    devolvido = restore(resposta, original, adaptado.optional_paths)
    assert _faltando(devolvido, original) == []
    if adaptado.optional_paths:
        assert _faltando(resposta, original)  # sem o restore faltaria chave


def test_anyof_com_null_e_enum_com_null():
    schema = {
        "type": "object",
        "properties": {
            **{f"u{i}": {"type": ["string", "null"], "enum": ["a", "b", None]} for i in range(MAX_UNION_PARAMS)},
            "obj": {"anyOf": [{"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                              {"type": "null"}]},
        },
        "additionalProperties": False,
    }
    schema["required"] = list(schema["properties"])
    adaptado = adapt_schema(schema)
    assert adaptado.schema["properties"]["obj"]["type"] == "object"
    assert ("obj",) in adaptado.optional_paths
    adaptado2 = adapt_schema(_nulavel(0) | {"properties": {"e": {"type": ["string", "null"], "enum": ["a", None]}},
                                            "required": ["e"]})
    # Enum com lista de tipos vira anyOf (o servidor recusa a lista + enum);
    # segue sendo UMA uniao e o conjunto de valores e o mesmo.
    assert adaptado2.schema["properties"]["e"] == {"anyOf": [{"type": "string", "enum": ["a"]}, {"type": "null"}]}
    assert _counts(adaptado2.schema) == _counts(_nulavel(0) | {"properties": {"e": {"type": ["string", "null"]}},
                                                              "required": ["e"]})


def _tipos_com_enum(no, caminho=()):
    if isinstance(no, list):
        return [c for v in no for c in _tipos_com_enum(v, caminho)]
    if not isinstance(no, dict):
        return []
    achados = [caminho] if isinstance(no.get("type"), list) and "enum" in no else []
    for chave, valor in no.items():
        achados += _tipos_com_enum(valor, caminho + (chave,))
    return achados


@pytest.mark.parametrize("nome", SCHEMAS_REAIS)
def test_schema_adaptado_nao_tem_enum_com_lista_de_tipos(nome):
    """400 do servidor no eval de 2026-10-30 (`basis` do rotulo): "Enum value
    'g' does not match declared type '['string', 'null']'"."""
    assert _tipos_com_enum(adapt_schema(SCHEMAS_REAIS[nome]()).schema) == []


def test_basis_do_rotulo_vira_anyof_e_volta_igual():
    original = nutrition_label_ai.output_schema()
    adaptado = adapt_schema(original)
    basis = adaptado.schema["properties"]["basis"]
    assert basis["anyOf"][1] == {"type": "null"}
    assert None not in basis["anyOf"][0]["enum"]
    assert set(basis["anyOf"][0]["enum"]) | {None} == set(original["properties"]["basis"]["enum"])
    resposta = _amostra(adaptado.schema) | {"basis": None}
    assert restore(resposta, original, adaptado.optional_paths)["basis"] is None


def test_palavras_fora_do_subconjunto_saem():
    schema = {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 10},
            "t": {"type": "string", "maxLength": 5},
            "l": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 4},
            "k": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "required": ["n", "t", "l", "k"],
        "additionalProperties": False,
    }
    adaptado = adapt_schema(schema)
    assert adaptado.schema["properties"]["n"] == {"type": "integer"}
    assert adaptado.schema["properties"]["t"] == {"type": "string"}
    assert adaptado.schema["properties"]["l"] == {"type": "array", "items": {"type": "string"}}
    assert adaptado.schema["properties"]["k"]["minItems"] == 1
    assert sorted(adaptado.removed_keywords) == ["maxItems", "maxLength", "maximum", "minItems", "minimum"]


def test_schema_que_nao_cabe_levanta():
    # Uniao de dois tipos nao nulos nao vira opcional: passa do limite.
    props = {f"x{i}": {"type": ["integer", "string"]} for i in range(MAX_UNION_PARAMS + 1)}
    schema = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    with pytest.raises(SchemaAdaptationError):
        adapt_schema(schema)


# ---------------------------------------------------------------------------
# `optional_first`: a feature escolhe quais anulaveis viram opcionais
# ---------------------------------------------------------------------------


def test_optional_first_converte_os_indicados_antes_dos_ultimos():
    schema = _nulavel(MAX_UNION_PARAMS + 2)
    adaptado = adapt_schema(schema, [("c0",), ("nao_existe",), ("c3",)])
    assert adaptado.optional_paths == {("c0",), ("c3",)}
    assert "c0" not in adaptado.schema["required"] and f"c{MAX_UNION_PARAMS + 1}" in adaptado.schema["required"]
    resposta = {f"c{i}": i for i in range(1, MAX_UNION_PARAMS + 2) if i != 3}
    devolvido = restore(resposta, schema, adaptado.optional_paths)
    assert list(devolvido) == list(schema["properties"]) and devolvido["c0"] is None and devolvido["c3"] is None


def test_optional_first_maior_que_o_excedente_usa_so_os_primeiros_e_sem_ele_nada_muda():
    schema = _nulavel(MAX_UNION_PARAMS + 1)
    assert adapt_schema(schema, [("c5",), ("c1",)]).optional_paths == {("c5",)}
    assert adapt_schema(schema, ()).schema == adapt_schema(schema).schema


def _periodizacao(com_cardio: bool):
    from app.periodization.output_schema import ANTHROPIC_OPTIONAL_FIRST

    original = output_schema(com_cardio)
    return original, adapt_schema(original, ANTHROPIC_OPTIONAL_FIRST)


def test_periodizacao_sem_cardio_fica_identica_a_de_antes():
    original, adaptado = _periodizacao(False)
    assert adaptado.schema == adapt_schema(original).schema


def _periodizacao_modo_b(com_cardio: bool, refs):
    from app.periodization.output_schema import ANTHROPIC_OPTIONAL_FIRST

    original = output_schema(com_cardio, True, refs)
    return original, adapt_schema(original, ANTHROPIC_OPTIONAL_FIRST)


@pytest.mark.parametrize("com_cardio", [False, True])
def test_periodizacao_modo_b_com_enum_de_refs_cabe_nos_limites_e_volta_inteira(com_cardio):
    """v15: o item do Modo B so tem `catalog_ref` (enum por pedido) no lugar
    do trio. A `enum` de strings nao e uniao: o pedido cabe nos limites, sem
    cardio nem precisa de opcional, e a ida e volta devolve a forma original."""
    from app.periodization.catalog_refs import CATALOG_REF_FIELD, MAX_ENUM_REFS

    refs = [f"E{n}" for n in range(1, MAX_ENUM_REFS + 1)]
    original, adaptado = _periodizacao_modo_b(com_cardio, refs)
    unioes, opcionais = _counts(adaptado.schema)
    assert unioes <= MAX_UNION_PARAMS and opcionais <= MAX_OPTIONAL_PARAMS
    if not com_cardio:
        assert adaptado.optional_paths == frozenset()
    item = adaptado.schema["properties"]["template"]["properties"]["slots"]["items"]["properties"]["items"]["items"]
    assert item["properties"][CATALOG_REF_FIELD] == {"type": "string", "enum": refs}
    assert CATALOG_REF_FIELD in item["required"]
    for campo in ("muscle_group_id", "mapping_id", "equipment_id", "item_kind", "rest_seconds"):
        assert campo not in item["properties"], campo
    for campo in ("sets_count", "reps_target", "rpe_target"):
        assert campo in item["required"], campo
    resposta = _amostra(adaptado.schema)
    devolvido = restore(resposta, original, adaptado.optional_paths)
    assert _faltando(devolvido, original) == []


def test_periodizacao_modo_b_sem_enum_vira_string_livre():
    from app.periodization.catalog_refs import CATALOG_REF_FIELD

    original, _adaptado = _periodizacao_modo_b(False, None)
    item = original["properties"]["template"]["properties"]["slots"]["items"]["properties"]["items"]["items"]
    assert item["properties"][CATALOG_REF_FIELD] == {"type": "string"}


def test_periodizacao_com_cardio_mantem_obrigatorios_do_dono_e_cabe_nos_limites():
    from app.domain import cardio_prescription as cp
    from app.periodization.output_schema import ANTHROPIC_OPTIONAL_FIRST, CARDIO_ITEMS_FIELD

    _original, adaptado = _periodizacao(True)
    unioes, opcionais = _counts(adaptado.schema)
    assert unioes <= MAX_UNION_PARAMS and opcionais <= MAX_OPTIONAL_PARAMS
    assert adaptado.optional_paths == set(ANTHROPIC_OPTIONAL_FIRST)  # cabe exatamente na lista
    slot = adaptado.schema["properties"]["template"]["properties"]["slots"]["items"]
    cardio = slot["properties"][CARDIO_ITEMS_FIELD]["items"]
    exigidos = {campo for campos in cp.REQUIRED_FIELDS.values() for campo in campos}
    for campo in exigidos:
        assert campo in cardio["required"], campo  # o Claude nao pode omitir
        assert "null" in cardio["properties"][campo]["type"]  # o outro modo segue null
    convertidos_cardio = {p[-1] for p in adaptado.optional_paths if CARDIO_ITEMS_FIELD in p}
    assert convertidos_cardio == set(cp.ZERO_WHEN_MISSING_FIELDS)  # "ausente" = 0 para o dono
    # Campos centrais do item de forca seguem obrigatorios.
    item = slot["properties"]["items"]["items"]
    for campo in (
        "muscle_group_id",
        "priority_min_pct",
        "priority_max_pct",
        "mapping_id",
        "equipment_id",
        "sets_count",
        "reps_target",
        "rpe_target",
    ):
        assert campo in item["required"], campo


def test_periodizacao_com_cardio_ida_e_volta_hiit_e_continuo():
    from app.periodization.output_schema import PeriodizationOutput

    original, adaptado = _periodizacao(True)
    resposta = _amostra(adaptado.schema)
    slot = resposta["template"]["slots"][0]
    continuo = dict(slot["cardio_items"][0], cardio_mode="continuous", cardio_duration_minutes=20)
    hiit = dict(slot["cardio_items"][0], cardio_mode="hiit", hiit_rounds=8)
    slot["cardio_items"] = [continuo, hiit]
    devolvido = restore(resposta, original, adaptado.optional_paths)
    assert _faltando(devolvido, original) == []
    volta = devolvido["template"]["slots"][0]["cardio_items"]
    assert volta[0]["cardio_duration_minutes"] == 20 and volta[0]["cardio_warmup_minutes"] is None
    assert volta[1]["hiit_rounds"] == 8 and volta[1]["cardio_cooldown_minutes"] is None
    assert devolvido["template"]["description"] is None
    PeriodizationOutput(**devolvido)  # a forma devolvida passa no espelho pydantic do dono
