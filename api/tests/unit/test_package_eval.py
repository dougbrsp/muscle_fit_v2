"""Avaliacao DETERMINISTICA do pacote de inteligencia (F3.2) - roda no CI.

Metade (a) do conjunto de avaliacao de `tests/eval/` (ver o README de la). O
conjunto de perguntas e o MESMO (`tests/eval/questions.json`); so este arquivo
mora em `tests/unit/`, porque e ele que precisa rodar em todo PR.

A pergunta que este arquivo responde e anterior a "a IA respondeu bem?": ela
e **"a IA tinha como responder bem?"**. Para cada pergunta do conjunto
versionado (`questions.json`), o pacote montado precisa conter as metricas, os
baselines e as evidencias que uma boa resposta citaria - e nada de outro
usuario.

Isso e o que torna a avaliacao com LLM (a outra metade, fora do CI)
interpretavel: quando ela falhar, ja se sabe que o insumo estava la, entao o
problema e do prompt ou do modelo - nao do pacote. Sem esta metade, toda
regressao pareceria "o modelo piorou".

Nao ha chamada a LLM aqui. Nao ha banco aqui (o cenario e o overview sintetico
de `tests/support/pacote_inteligencia.py`) - a afirmacao de isolamento contra
dado real de outro usuario vive em
`tests/integration/dataapi/test_dashboard_intelligence_package.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.insights_prompt import PROMPT_VERSION
from app.intelligence.dashboard.intelligence_package import (
    PACKAGE_MAX_BYTES,
    PACKAGE_VERSION,
    measure_bytes,
    package_from_overview,
    rag_target_groups,
)
from tests.support.pacote_inteligencia import overview_sintetico

# O conjunto vive em `tests/eval/` junto da metade com LLM (mesma fonte de
# verdade para as duas). Este ARQUIVO, porem, mora em `tests/unit/`: o CI roda
# `python -m pytest tests/unit` e `tests/integration` explicitamente, e um
# teste em `tests/eval/` simplesmente nao seria coletado - a metade que deveria
# rodar em todo PR nao rodaria em nenhum.
DATASET = Path(__file__).resolve().parents[1] / "eval" / "questions.json"


def carregar() -> dict:
    return json.loads(DATASET.read_text(encoding="utf-8"))


CONJUNTO = carregar()
PERGUNTAS = CONJUNTO["questions"]


def _pacote(pergunta: dict) -> dict:
    return package_from_overview(overview_sintetico(), question=pergunta["question"])


def test_conjunto_declara_as_versoes_com_que_foi_escrito():
    """Expectativa escrita contra outro prompt/pacote nao e expectativa - e
    ruido. Se o prompt mudar sem o conjunto ser revisto, este teste reprova e
    obriga a decisao explicita."""
    assert CONJUNTO["prompt_version"] == PROMPT_VERSION
    assert CONJUNTO["package_version"] == PACKAGE_VERSION


def test_ids_sao_unicos():
    ids = [p["id"] for p in PERGUNTAS]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_pacote_tem_as_metricas_que_a_resposta_precisaria(pergunta):
    pacote = _pacote(pergunta)
    presentes = {m["key"] for m in pacote["metrics"]}
    faltando = sorted(set(pergunta["expect_metric_keys"]) - presentes)
    assert not faltando, (
        f"'{pergunta['question']}' não teria como ser respondida: faltam {faltando} no pacote"
    )


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_pacote_tem_os_baselines_que_a_comparacao_precisaria(pergunta):
    pacote = _pacote(pergunta)
    presentes = {b["key"] for b in pacote["baselines"]}
    faltando = sorted(set(pergunta.get("expect_baseline_keys") or []) - presentes)
    assert not faltando, f"'{pergunta['question']}': faltam baselines {faltando}"


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_pacote_tem_a_evidencia_com_data(pergunta):
    pacote = _pacote(pergunta)
    esperados = set(pergunta.get("expect_evidence_codes") or [])
    if not esperados:
        pytest.skip("pergunta sem evidência obrigatória neste cenário")
    codigos = {e["insight_code"] for e in pacote["evidence"]}
    assert esperados <= codigos, f"faltam evidências {sorted(esperados - codigos)}"
    for item in pacote["evidence"]:
        if item["insight_code"] in esperados:
            assert item["observed_at"] is not None, "o prompt exige citar a data"
            # Agregado de janela tem data de janela, e DECLARA isso. O modelo
            # precisa saber qual das duas formas de citacao usar.
            assert item["observed_scope"] in ("point", "window")
            if item["observed_scope"] == "window":
                assert item["observed_from"] is not None


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_metrica_nao_calculavel_continua_nula(pergunta):
    """"Não há dado" e um estado, nao um zero. A pergunta sobre cintura existe
    no conjunto exatamente para travar isso: se `waist_cm` virar 0, o modelo
    responderia "sua cintura está em 0 cm" com toda a confianca do mundo."""
    nulas = pergunta.get("expect_metric_values_null") or []
    if not nulas:
        pytest.skip("cenário sem métrica declaradamente ausente")
    por_chave = {m["key"]: m for m in _pacote(pergunta)["metrics"]}
    for chave in nulas:
        assert por_chave[chave]["value"] is None


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_contexto_esperado_esta_no_pacote(pergunta):
    tipos = set(pergunta.get("expect_context_event_types") or [])
    if not tipos:
        pytest.skip("cenário sem evento de contexto obrigatório")
    presentes = {e["event_type"] for e in _pacote(pergunta)["context_events"]}
    assert tipos <= presentes


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_pacote_cabe_no_teto(pergunta):
    pacote = _pacote(pergunta)
    assert measure_bytes(pacote) <= PACKAGE_MAX_BYTES
    assert pacote["size_bytes"] <= PACKAGE_MAX_BYTES


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_grupos_do_pacote_filtram_o_rag(pergunta):
    pacote = _pacote(pergunta)
    grupos = set(rag_target_groups(pacote))
    esperados = set(pergunta.get("expect_muscle_groups") or [])
    assert esperados <= grupos
    assert grupos, "pacote sem grupo nenhum faria o RAG voltar ao dump completo"


@pytest.mark.parametrize("pergunta", PERGUNTAS, ids=lambda p: p["id"])
def test_pacote_nao_carrega_identificador_de_usuario(pergunta):
    """O pacote vai inteiro para dentro de um prompt. Ele nao deve levar id de
    usuario, e-mail nem nome - nada que sirva para o modelo (ou um log de
    prompt) correlacionar pessoas. O dono do pacote e decidido no servidor,
    pela identidade da requisicao, e nao precisa viajar dentro dele."""
    texto = json.dumps(_pacote(pergunta), default=str, ensure_ascii=False).lower()
    for proibido in ("user_id", "\"email\"", "@", "password"):
        assert proibido not in texto, f"o pacote carrega {proibido!r}"
