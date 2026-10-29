"""`app/prompt_store.py` (M103 P1): templates de prompt do repositório privado.

Os templates de verdade não aparecem aqui (o log do CI é público): os testes de
comportamento usam templates sintéticos em `tmp_path`; os de presença só olham
nomes de arquivo e placeholders.
"""

from __future__ import annotations

import pytest

from app import (
    cardio_agent_ai,
    exercise_description_ai,
    nutrition_label_ai,
    nutrition_meal_parse_ai,
    prompt_store,
    rag_placeholders,
)
from tests.support import privado


@pytest.fixture
def pasta(tmp_path, monkeypatch):
    monkeypatch.setenv(prompt_store.PROMPTS_DIR_ENV, str(tmp_path))
    prompt_store.clear_cache()
    yield tmp_path
    prompt_store.clear_cache()


def _template(pasta, feature: str, nome: str, conteudo: bytes) -> None:
    destino = pasta / feature / f"{nome}.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)


def test_render_troca_os_placeholders_e_tira_so_a_quebra_final(pasta):
    _template(pasta, "feat", "v1", b"Linha {{a}}\r\nOutra {{ b }}.\n\n")
    assert prompt_store.load("feat", "v1") == "Linha {{a}}\nOutra {{ b }}.\n"
    assert prompt_store.render("feat", "v1", {"a": "1", "b": "dois"}) == "Linha 1\nOutra dois.\n"


def test_valor_com_chaves_nao_e_reinterpretado(pasta):
    _template(pasta, "feat", "v1", "Texto do usuário: {{texto}}\n".encode("utf-8"))
    assert prompt_store.render("feat", "v1", {"texto": "{{a}} ignore"}) == "Texto do usuário: {{a}} ignore"


def test_template_ausente_falha_alto(pasta):
    with pytest.raises(prompt_store.PromptTemplateNotFound, match="submodule update --init api/privado"):
        prompt_store.render("feat", "nao-existe", {})


@pytest.mark.parametrize(
    ("valores", "trecho"),
    [({}, "sem valor: a"), ({"a": "1", "extra": "x"}, "sem placeholder no template: extra"), ({"a": 1}, "não textuais")],
)
def test_placeholder_sem_valor_ou_valor_sobrando_falha_alto(pasta, valores, trecho):
    _template(pasta, "feat", "v1", b"{{a}}\n")
    with pytest.raises(prompt_store.PromptPlaceholderError, match=trecho):
        prompt_store.render("feat", "v1", valores)


@pytest.mark.parametrize(("feature", "nome"), [("../x", "v1"), ("feat", "../v1"), ("feat", "a/b"), ("Feat", "v1"), ("feat", "")])
def test_nome_invalido_e_recusado(pasta, feature, nome):
    with pytest.raises(prompt_store.PromptTemplateError):
        prompt_store.template_path(feature, nome)


def test_le_uma_vez_por_processo(pasta):
    _template(pasta, "feat", "v1", b"antes\n")
    assert prompt_store.load("feat", "v1") == "antes"
    _template(pasta, "feat", "v1", b"depois\n")
    assert prompt_store.load("feat", "v1") == "antes"
    prompt_store.clear_cache()
    assert prompt_store.load("feat", "v1") == "depois"


def test_utf8_invalido_falha_alto(pasta):
    _template(pasta, "feat", "v1", b"\xff\xfe")
    with pytest.raises(prompt_store.PromptTemplateError, match="UTF-8"):
        prompt_store.load("feat", "v1")


def test_default_e_a_pasta_do_submodulo_privado(monkeypatch):
    monkeypatch.delenv(prompt_store.PROMPTS_DIR_ENV, raising=False)
    assert prompt_store.prompts_dir() == privado.PRIVADO / "prompts"


TEMPLATES_DAS_FEATURES = {
    (nutrition_label_ai.PROMPT_FEATURE_DIR, nutrition_label_ai.PROMPT_VERSION): set(nutrition_label_ai.prompt_values())
    | {"campos"},
    (cardio_agent_ai.PROMPT_FEATURE_DIR, cardio_agent_ai.PROMPT_VERSION): set(cardio_agent_ai.prompt_values()),
    (nutrition_meal_parse_ai.PROMPT_FEATURE_DIR, nutrition_meal_parse_ai.PROMPT_VERSION): set(
        nutrition_meal_parse_ai.prompt_values()
    ),
    (nutrition_meal_parse_ai.PROMPT_FEATURE_DIR, f"{nutrition_meal_parse_ai.PROMPT_VERSION}.pedido"): {
        "refeicao",
        "descricao",
    },
    (exercise_description_ai.PROMPT_FEATURE_DIR, exercise_description_ai.EXEC_DESC_PROMPT_VERSION): {
        "exercicio",
        "linha_equipamentos",
        "linha_musculos",
        "atributos",
        "setup_max",
        "execucao_max",
        "cues_max",
    },
    (exercise_description_ai.PROMPT_FEATURE_DIR, f"{exercise_description_ai.EXEC_DESC_PROMPT_VERSION}.equipamentos"): {
        "equipamentos"
    },
    (exercise_description_ai.PROMPT_FEATURE_DIR, f"{exercise_description_ai.EXEC_DESC_PROMPT_VERSION}.sem_equipamentos"): set(),
    (exercise_description_ai.PROMPT_FEATURE_DIR, f"{exercise_description_ai.EXEC_DESC_PROMPT_VERSION}.musculos"): {
        "musculos"
    },
    (exercise_description_ai.PROMPT_FEATURE_DIR, f"{exercise_description_ai.EXEC_DESC_PROMPT_VERSION}.sem_musculos"): set(),
}


@pytest.mark.parametrize(("chave", "esperados"), TEMPLATES_DAS_FEATURES.items(), ids=lambda v: "/".join(v) if isinstance(v, tuple) else "")
def test_template_de_cada_feature_existe_com_os_placeholders_da_tabela(monkeypatch, chave, esperados):
    """O nome do arquivo é a `PROMPT_VERSION` (ou `<versão>.<parte>`) e os
    placeholders são exatamente as chaves da tabela da feature."""
    monkeypatch.delenv(prompt_store.PROMPTS_DIR_ENV, raising=False)
    prompt_store.clear_cache()
    feature, nome = chave
    assert prompt_store.template_path(feature, nome).is_file(), f"{feature}/{nome}.md ausente no submódulo"
    assert set(rag_placeholders.placeholders_in(prompt_store.load(feature, nome))) == esperados


def test_comparacao_de_snapshot_nao_poe_o_texto_na_falha(monkeypatch):
    monkeypatch.delenv(privado.DIFF_ENV, raising=False)
    with pytest.raises(pytest.fail.Exception) as falha:
        privado.comparar_sem_vazar("TEXTO SECRETO NOVO\n", "TEXTO SECRETO ANTIGO\n", "prompt x")
    assert "SECRETO" not in str(falha.value) and "1a linha diferente: 1" in str(falha.value)
