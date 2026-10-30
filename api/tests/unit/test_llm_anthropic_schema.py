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
    assert adaptado2.schema["properties"]["e"]["enum"] == ["a", None]  # dentro do limite: intacto


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
