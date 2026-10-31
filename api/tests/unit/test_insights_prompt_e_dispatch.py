"""O prompt de `/insights/generate` e a montagem server-side do contexto
(F3.2).

Nenhum teste deste arquivo fala com a OpenAI. A chamada e SEMPRE dublê -
`call_openai` e o unico ponto de saida, e ele e substituido por um espiao que
guarda o prompt. Chamada real custaria dinheiro a cada execucao do CI e
tornaria a suite nao deterministica; alem disso, o que precisa ser verificado
aqui e o que ENTRA no modelo, nao o que sai dele.
"""

from __future__ import annotations

import pytest

from app import insights_ai, insights_core, insights_prompt, openai_client, prompt_store
from app.prompt_user_data import user_data_rule
from tests.support.pacote_inteligencia import overview_sintetico
from app.intelligence.dashboard.intelligence_package import package_from_overview


@pytest.fixture
def pacote():
    return package_from_overview(overview_sintetico(), question="Estou evoluindo no peito?")


# ---------------------------------------------------------------------------
# 1) O prompt vem do template versionado (M103 P2). As tres regras do plano,
#    a exigencia de citar por data e a acao fora do vocabulario sao prosa do
#    template: `api/privado/tests/unit/test_insights_prompt.py`.
# ---------------------------------------------------------------------------


def test_prompt_vem_do_template_da_versao_e_preenche_tudo(pacote):
    prompt = insights_prompt.build_insight_prompt(pacote, "RAG")
    assert prompt_store.load(insights_prompt.PROMPT_FEATURE_DIR, insights_prompt.PROMPT_VERSION)
    assert "{{" not in prompt and "}}" not in prompt
    assert user_data_rule() in prompt
    assert prompt.index(user_data_rule()) < prompt.index(insights_prompt.render_package(pacote))


def test_prompt_declara_janela_confianca_e_versoes(pacote):
    prompt = insights_prompt.build_insight_prompt(pacote, "RAG")
    assert "2026-08-30 a 2026-09-26" in prompt
    assert insights_prompt.PROMPT_VERSION in prompt
    assert pacote["package_version"] in prompt


def test_prompt_leva_o_pacote_e_o_rag_recebido(pacote):
    prompt = insights_prompt.build_insight_prompt(pacote, "CONTEUDO-DO-RAG", question="E o peito?")
    assert "CONTEUDO-DO-RAG" in prompt
    assert '"consistency.rate"' in prompt
    # A pergunta e dado do usuario: vai no fim, delimitada (IA-10).
    assert prompt.rstrip().endswith("<dados_do_usuario>\nE o peito?\n</dados_do_usuario>")


def test_prompt_vive_num_lugar_so():
    """A montagem do prompt nao pode voltar a existir em dois arquivos: era
    assim antes da F3.2 (`insights_ai.build_prompt` + a copia no frontend) e
    foi como o texto divergiu."""
    fonte = (insights_ai.__file__ or "")
    assert fonte
    with open(fonte, encoding="utf-8") as handle:
        texto = handle.read()
    template = prompt_store.load(insights_prompt.PROMPT_FEATURE_DIR, insights_prompt.PROMPT_VERSION)
    linhas = [linha.strip() for linha in template.split("\n") if len(linha.strip()) > 30 and "{{" not in linha]
    assert linhas and not any(linha in texto for linha in linhas), "o texto do prompt voltou para insights_ai.py"
    assert "def build_prompt" not in texto


# ---------------------------------------------------------------------------
# 2) A chave da API saiu da constante de modulo
# ---------------------------------------------------------------------------


def test_chave_da_api_nao_e_constante_de_modulo():
    """`insights_ai.py:11` guardava `OPENAI_API_KEY` numa constante de modulo:
    o valor congelava no import, uma rotacao de segredo so valia no proximo
    cold start e um teste que setasse a variavel nao tinha efeito. Agora a
    leitura e de `openai_client._api_key`, a cada chamada."""
    assert not hasattr(insights_ai, "OPENAI_API_KEY")
    with open(insights_ai.__file__, encoding="utf-8") as handle:
        texto = handle.read()
    atribuicoes = [
        linha
        for linha in texto.splitlines()
        if linha.startswith("OPENAI_API_KEY") or "os.environ.get(\"OPENAI_API_KEY\"" in linha
    ]
    assert not atribuicoes, f"a chave voltou ao corpo do módulo: {atribuicoes}"
    # A copia local da chamada HTTP tambem foi embora: um unico cliente.
    assert "urllib" not in texto


def test_erro_do_cliente_compartilhado_chega_como_erro_de_insight(monkeypatch, pacote):
    def _explode(prompt, model, timeout=90, **_kw):
        raise openai_client.OpenAIGenerationError("OPENAI_API_KEY nao configurada no backend.")

    monkeypatch.setattr(insights_ai, "call_openai_text", _explode)
    with pytest.raises(insights_ai.InsightsGenerationError) as exc:
        insights_ai.generate_insight("pergunta", pacote, "RAG")
    assert "OPENAI_API_KEY" in str(exc.value)


def test_modelo_e_lido_a_cada_chamada(monkeypatch, pacote):
    pedidos = []

    def _responde(prompt, model, timeout=90, **_kw):
        pedidos.append(model)
        return openai_client.TextResult(text="ok", model=model, requested_model=model)

    monkeypatch.setattr(insights_ai, "call_openai_text", _responde)
    monkeypatch.setenv("OPENAI_INSIGHTS_MODEL", "modelo-de-teste")
    resultado = insights_ai.generate_insight("pergunta", pacote, "RAG")
    assert pedidos == ["modelo-de-teste"] and resultado["model"] == "modelo-de-teste"
    assert resultado["prompt_version"] == insights_prompt.PROMPT_VERSION


# ---------------------------------------------------------------------------
# 3) `dispatch_insights` monta o contexto no servidor
# ---------------------------------------------------------------------------


class _Espiao:
    """Dublê de `call_openai`: guarda o prompt e devolve texto fixo."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt, model, timeout=90, **_kw):
        self.prompts.append(prompt)
        return openai_client.TextResult(text="resposta do modelo", model=model, requested_model=model)


@pytest.fixture
def cenario(monkeypatch):
    espiao = _Espiao()
    monkeypatch.setattr(insights_ai, "call_openai_text", espiao)

    chamadas: dict[str, object] = {}

    def _pacote(*, user_id, question, window, date_from=None, date_to=None):
        chamadas["user_id"] = user_id
        chamadas["question"] = question
        chamadas["window"] = window
        pacote = package_from_overview(overview_sintetico(), question=question)
        from app.intelligence.dashboard.intelligence_package import rag_target_groups

        # M97 F5: o contexto de objetivo/deload da janela vem junto (default =
        # hipertrofia sem deload, o prompt de sempre).
        return pacote, rag_target_groups(pacote), insights_core.WindowGoalContext()

    monkeypatch.setattr(insights_core, "build_insights_inputs", _pacote)

    rag: dict[str, object] = {}

    def _rag(groups, rag_folder=None, has_training_type=False, training_goal=None):
        rag["groups"] = list(groups)
        rag["has_training_type"] = has_training_type
        return "RAG-FILTRADO"

    monkeypatch.setattr(insights_core, "select_rag_text", _rag)
    return espiao, chamadas, rag


def test_dispatch_monta_o_pacote_no_servidor(cenario):
    espiao, chamadas, rag = cenario
    resultado = insights_core.dispatch_insights(
        {"question": "Como está meu peito?", "window": "90d"}, user_id=7
    )

    assert resultado["insight"] == "resposta do modelo"
    assert chamadas == {"user_id": 7, "question": "Como está meu peito?", "window": "90d"}
    assert espiao.prompts, "nenhuma chamada foi feita"
    assert "RAG-FILTRADO" in espiao.prompts[0]


def test_dispatch_filtra_o_rag_pelos_grupos_do_pacote(cenario):
    _, _, rag = cenario
    insights_core.dispatch_insights({"question": "?"}, user_id=7)
    assert rag["groups"] == ["Perna", "Costas", "Peito"]
    # `has_training_type=True` evita o fallback recursivo de TIPOS_TREINO, que
    # traria de volta, por outro caminho, o dump que esta entrega remove.
    assert rag["has_training_type"] is True


def test_janela_default_e_a_da_visao_geral(cenario):
    _, chamadas, _ = cenario
    insights_core.dispatch_insights({"question": "?"}, user_id=7)
    assert chamadas["window"] == insights_core.DEFAULT_WINDOW == "28d"


def test_contexto_do_cliente_antigo_e_ignorado_sem_422(cenario, monkeypatch):
    """Janela de compatibilidade do deploy: frontend e API nao sobem no mesmo
    instante. Um 422 aqui transformaria uma defasagem de minutos numa tela
    quebrada - entao o campo e ignorado, e CONTADO."""
    eventos: list[tuple] = []
    monkeypatch.setattr(
        insights_core, "log_event", lambda nome, **kwargs: eventos.append((nome, kwargs))
    )
    espiao, _, _ = cenario

    resultado = insights_core.dispatch_insights(
        {"question": "?", "context": "{json enorme do cliente antigo}"}, user_id=7
    )

    assert resultado["insight"] == "resposta do modelo"
    registrados = [e for e in eventos if e[0] == insights_core.EVENT_LEGACY_CONTEXT]
    assert registrados, "o campo legado foi ignorado em silêncio"
    assert registrados[0][1]["chars"] == len("{json enorme do cliente antigo}")
    # E o mais importante: o texto do cliente NAO entrou no prompt.
    assert "cliente antigo" not in espiao.prompts[0]


def test_pergunta_vazia_e_400(cenario):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        insights_core.dispatch_insights({"question": "   "}, user_id=7)
    assert exc.value.status_code == 400


def test_resposta_mantem_o_shape_antigo(cenario):
    resultado = insights_core.dispatch_insights({"question": "?"}, user_id=7)
    assert set(resultado) == {"model", "insight"}
