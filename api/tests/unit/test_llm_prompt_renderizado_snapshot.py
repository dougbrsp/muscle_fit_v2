"""Snapshot do PROMPT RENDERIZADO de cada feature de texto (IA-11).

Para cada caso congelado de `tests/eval/casos/<feature>.json` (entradas
sinteticas com a estrutura da coleta do DEV - M103 L2), roda a funcao REAL da feature com a rede
mockada e compara o que iria para a OpenAI com a referencia commitada:

- `api/privado/tests/fixtures/llm_prompts/<feature>/<caso>.txt` (repositorio
  PRIVADO, M103 P1): o ESQUELETO do prompt - o
  texto inteiro, com cada bloco JSON grande trocado por
  `<<JSON n linhas, c chars, sha256 ...>>` (diff legivel no PR: e onde se ve
  uma secao mudar de lugar, uma regra mudar de texto, um numero aparecer);
- `fixtures/llm_prompts/index.json`: sha256 do prompt COMPLETO e do corpo
  HTTP inteiro (modelo, `text.format` das estruturadas), `prompt_chars` e
  `prompt_version` (publico: so hashes).

O log do CI publico nao mostra o texto: a falha diz so o tamanho e a 1a linha
diferente (`tests/support/privado.py`); o diff sai localmente com
`LLM_PROMPT_SNAPSHOT_DIFF=1`.

Qualquer mudanca de prompt - texto, ordem, dado, schema, modelo - quebra este
teste. Mudou de proposito? Suba o `PROMPT_VERSION` da feature e regenere:

    cd api && LLM_PROMPT_SNAPSHOT_WRITE=1 python -m pytest tests/unit/test_llm_prompt_renderizado_snapshot.py

e rode o eval pago antes/depois (`docs/ia/EVALS.md`). Para ver o prompt
COMPLETO de cada caso (diff dos blocos JSON): `LLM_PROMPT_SNAPSHOT_DUMP=<pasta>`.

O RAG entra como marcador fixo (`RAG_FIXO`): ele e conteudo versionado em
`rag/markdown` e o recorte e medido a parte (`rag_chars`); este teste trava o
template e os dados. Nenhuma chamada paga.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import pytest

from tests.support import llm_casos, privado

# Esqueletos (texto do prompt): repositorio privado. Index (hashes): publico.
INDEX = Path(__file__).parent / "fixtures" / "llm_prompts" / "index.json"


def _fixtures() -> Path:
    return privado.fixture_privada("llm_prompts")

# Linha unica maior que isto vira referencia por hash no esqueleto (os JSON
# compactos da periodizacao: catalogo da academia, tabela da metodologia).
MAX_LINHA_NO_ESQUELETO = 2000

CASOS = llm_casos.todos_os_casos()


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def esqueleto(prompt: str) -> str:
    """O prompt com cada bloco JSON indentado de topo (`{`/`[` na coluna 0
    ate o `}`/`]` na coluna 0) e cada linha gigante trocados por uma
    referencia com tamanho e hash. Deterministico e sem conhecer a feature."""
    linhas = prompt.split("\n")
    saida: list[str] = []
    i = 0
    while i < len(linhas):
        linha = linhas[i]
        if linha in ("{", "["):
            fecha = "}" if linha == "{" else "]"
            j = i + 1
            while j < len(linhas) and linhas[j] != fecha:
                j += 1
            if j < len(linhas):
                bloco = "\n".join(linhas[i : j + 1])
                saida.append(f"<<JSON {j - i + 1} linhas, {len(bloco)} chars, sha256 {_sha(bloco)[:16]}>>")
                i = j + 1
                continue
        if len(linha) > MAX_LINHA_NO_ESQUELETO:
            saida.append(f"<<LINHA {len(linha)} chars, sha256 {_sha(linha)[:16]}>>")
        else:
            saida.append(linha)
        i += 1
    return "\n".join(saida) + "\n"


class _Resposta:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def capturar(monkeypatch, feature: str, caso: dict) -> dict:
    """Roda o caso com `urlopen` mockado; devolve o corpo HTTP enviado."""
    from app import openai_client

    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    enviados = []
    texto = llm_casos.RESPOSTA_FALSA.get(feature, llm_casos.RESPOSTA_FALSA_PADRAO)

    def _fake(request, timeout=None):
        enviados.append(request)
        return _Resposta({"output_text": texto, "model": "modelo-falso"})

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    llm_casos.executar(feature, caso, monkeypatch, rag="fixo")
    assert len(enviados) == 1, f"{feature}/{caso['id']}: esperava 1 chamada, houve {len(enviados)}"
    assert enviados[0].full_url == "https://api.openai.com/v1/responses"
    return json.loads(enviados[0].data.decode("utf-8"))


def _referencia(feature: str, caso_id: str) -> Path:
    return _fixtures() / feature / f"{caso_id}.txt"


def _ler_index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}


def _gravar(feature: str, caso_id: str, corpo: dict, prompt: str) -> None:
    destino = _referencia(feature, caso_id)
    destino.parent.mkdir(parents=True, exist_ok=True)
    # LF explicito: o repo normaliza para LF (.gitattributes) e o CI e Linux.
    destino.write_text(esqueleto(prompt), encoding="utf-8", newline="\n")
    index = _ler_index()
    index.setdefault(feature, {})[caso_id] = _resumo(corpo, prompt)
    INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _resumo(corpo: dict, prompt: str) -> dict:
    return {
        "body_sha256": _sha(json.dumps(corpo, sort_keys=True, ensure_ascii=False)),
        "model": corpo.get("model"),
        # Visiveis no diff do index (e nao so dentro do `body_sha256`): teto de
        # saida e chave de cache do cliente (`openai_client.LLM_MAX_OUTPUT_TOKENS`,
        # `prompt_cache_key`). `None` = a feature nao manda o parametro.
        "max_output_tokens": corpo.get("max_output_tokens"),
        "prompt_cache_key": corpo.get("prompt_cache_key"),
        # Retencao na OpenAI (IA-17, `openai_client.LLM_STORE_RESPONSES`).
        "store": corpo.get("store"),
        "prompt_chars": len(prompt),
        "prompt_sha256": _sha(prompt),
    }


@pytest.mark.parametrize(("feature", "caso"), CASOS, ids=[f"{f}/{c['id']}" for f, c in CASOS])
def test_prompt_renderizado_igual_ao_snapshot(monkeypatch, feature, caso):
    corpo = capturar(monkeypatch, feature, caso)
    prompt = corpo["input"]
    if os.environ.get("LLM_PROMPT_SNAPSHOT_DUMP"):
        pasta = Path(os.environ["LLM_PROMPT_SNAPSHOT_DUMP"]) / feature
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / f"{caso['id']}.txt").write_text(prompt, encoding="utf-8", newline="\n")
    if os.environ.get("LLM_PROMPT_SNAPSHOT_WRITE"):
        _gravar(feature, caso["id"], corpo, prompt)

    privado.comparar_arquivo_sem_vazar(
        esqueleto(prompt),
        _referencia(feature, caso["id"]),
        f"prompt de {feature}/{caso['id']}",
        regerar="LLM_PROMPT_SNAPSHOT_WRITE",
    )
    esperado = _ler_index()[feature][caso["id"]]
    assert _resumo(corpo, prompt) == esperado


def test_todo_caso_tem_snapshot_e_nao_ha_snapshot_orfao():
    """Toda feature tem de 5 a 10 casos, cada caso tem referencia e nao
    sobra referencia de caso apagado."""
    index = _ler_index()
    for feature in llm_casos.FEATURES:
        ids = {caso["id"] for caso in llm_casos.carregar(feature)}
        assert 5 <= len(ids) <= 10, f"{feature}: {len(ids)} casos"
        arquivos = {p.stem for p in (_fixtures() / feature).glob("*.txt")}
        assert arquivos == ids, f"{feature}: snapshots {sorted(arquivos ^ ids)} sem caso/sem snapshot"
        assert set(index.get(feature, {})) == ids
    assert set(index) == set(llm_casos.FEATURES)


def test_esqueleto_troca_so_os_blocos_json():
    prompt = "Regra 1\nDados:\n{\n  \"a\": {\n    \"b\": 1\n  }\n}\nFim\n" + "x" * (MAX_LINHA_NO_ESQUELETO + 1)
    linhas = esqueleto(prompt).splitlines()
    assert linhas[:2] == ["Regra 1", "Dados:"]
    assert linhas[2].startswith("<<JSON 5 linhas,")
    assert linhas[3] == "Fim"
    assert linhas[4].startswith(f"<<LINHA {MAX_LINHA_NO_ESQUELETO + 1} chars")


def _versao_do_prompt(feature: str) -> str:
    """A constante de versao de cada feature, lida do modulo dono do prompt."""
    from app import (
        cardio_agent_ai,
        exercise_description_ai,
        exercise_set_evaluation_ai,
        exercise_summary_ai,
        insights_prompt,
        nutrition_meal_parse_ai,
        report_ai,
        workout_summary_ai,
    )
    from app.periodization import core as periodization_core

    return {
        "set_evaluation": exercise_set_evaluation_ai.PROMPT_VERSION,
        "exercise_summary": exercise_summary_ai.PROMPT_VERSION,
        "workout_summary": workout_summary_ai.PROMPT_VERSION,
        "insights": insights_prompt.PROMPT_VERSION,
        "workouts_analysis": report_ai.PROMPT_VERSION,
        "cardio_agent": cardio_agent_ai.PROMPT_VERSION,
        "periodization": periodization_core.PROMPT_VERSION,
        "exercise_description": exercise_description_ai.EXEC_DESC_PROMPT_VERSION,
        "meal_parse": nutrition_meal_parse_ai.PROMPT_VERSION,
    }[feature]


@pytest.mark.parametrize("feature", llm_casos.FEATURES)
def test_llm_usage_registra_a_versao_do_prompt(monkeypatch, caplog, feature):
    """IA-11: todo `llm.usage` diz com qual `PROMPT_VERSION` a chamada foi
    feita - so no log; o corpo enviado (travado acima) nao muda."""
    caso = llm_casos.carregar(feature)[0]
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        corpo = capturar(monkeypatch, feature, caso)
    eventos = [
        json.loads(rec.getMessage())
        for rec in caplog.records
        if rec.name == "musclefit.events" and json.loads(rec.getMessage()).get("event") == "llm.usage"
    ]
    assert len(eventos) == 1
    assert eventos[0]["feature"] == feature
    assert eventos[0]["prompt_version"] == _versao_do_prompt(feature)
    assert "prompt_version" not in corpo
