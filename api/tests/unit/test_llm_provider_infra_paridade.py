"""Contrato infra x codigo do provedor LLM por feature (infra/llm_providers.tf).

- a lista de features publicada pelo Terraform e a de `llm_anthropic.LLM_FEATURES`;
- so as 6 features da virada vao para o Claude Haiku, com fallback por
  feature; o resto segue `openai`;
- a sugestao de metas (N6) le as envs de `meal_parse`, sem env propria, e nenhuma
  feature existente muda de provedor nem de modelo;
- o ARN e a leitura do secret `musclefit/claude-api-key` vao SO para as 3
  Lambdas que ja leem a chave da OpenAI; engines, intervals-sync e migrations
  ficam sem;
- o secret ja esta no state (import aplicado em 2026-10-31, bloco removido),
  sem `_version` nem `description`;
- o DEV (LocalStack) cria o mesmo secret."""

from __future__ import annotations

import os
import re
from pathlib import Path

from app import llm_anthropic

REPO = Path(__file__).resolve().parents[3]
INFRA = REPO / "infra"
ARN = "arn:aws:secretsmanager:sa-east-1:930413537709:secret:musclefit/claude-api-key-SFXRNu"


def _tf(nome: str) -> str:
    return (INFRA / nome).read_text(encoding="utf-8")


def test_features_do_terraform_sao_as_do_codigo():
    tf = _tf("llm_providers.tf")
    bloco = re.search(r"llm_features = \[(.*?)\]", tf, re.S).group(1)
    assert tuple(re.findall(r'"([a-z_]+)"', bloco)) == llm_anthropic.LLM_FEATURES


# Virada de 2026-10-31 (decisao do usuario; eval em
# docs/ia/EVAL_ANTHROPIC_2026-10-30.md): so estas 6, no Haiku, com o esforco
# vencedor e fallback por feature para a OpenAI.
VIRADA_HAIKU = {
    "cardio_agent": "medium",
    "label_photo": "medium",
    "meal_photo": "medium",
    "insights": "medium",
    "meal_parse": "medium",
    "exercise_description": "medium",
    "workout_summary": "low",
    # Prompt periodization-v15 (docs/ia/EVAL_PERIODIZACAO_V15_2026-10-31.md):
    # Haiku sem raciocinio, esforco low.
    "periodization": "low",
}


def _mapa_padrao(tf: str, variavel: str) -> dict[str, str]:
    if re.search(rf'variable "{variavel}" \{{[^}}]*?default\s*=\s*\{{\}}', tf):
        return {}
    bloco = re.search(rf'variable "{variavel}" \{{.*?default = \{{(.*?)\n  \}}', tf, re.S).group(1)
    return dict(re.findall(r'^\s*([a-z_]+)\s*=\s*"([^"]*)"', bloco, re.M))


def test_padrao_publicado_liga_so_as_da_virada_no_haiku_com_fallback_por_feature():
    tf = _tf("llm_providers.tf")
    assert 'lookup(var.llm_provider_by_feature, f, "openai")' in tf
    provedores = _mapa_padrao(tf, "llm_provider_by_feature")
    assert provedores == {f: "anthropic" for f in VIRADA_HAIKU}
    assert _mapa_padrao(tf, "anthropic_model_by_feature") == {f: llm_anthropic.ANTHROPIC_MODEL_HAIKU for f in VIRADA_HAIKU}
    assert _mapa_padrao(tf, "anthropic_effort_by_feature") == VIRADA_HAIKU
    assert _mapa_padrao(tf, "llm_fallback_provider_by_feature") == {f: "openai" for f in VIRADA_HAIKU}
    assert set(VIRADA_HAIKU) <= set(llm_anthropic.LLM_FEATURES)
    # As que seguem na OpenAI nao recebem fallback: o global continua vazio.
    assert re.search(r'variable "llm_fallback_provider" \{.*?default\s+= ""', tf, re.S)
    assert '"LLM_FALLBACK_PROVIDER_${upper(f)}"' in tf
    assert llm_anthropic.DEFAULT_PROVIDER == "openai"


def test_so_as_tres_lambdas_de_ia_leem_a_chave_da_anthropic():
    for nome in ("lambda_api_core.tf", "lambda_ai_generators.tf", "lambda_workout_ai.tf"):
        tf = _tf(nome)
        assert "aws_secretsmanager_secret.anthropic_api_key.arn" in tf, nome
        assert "local.llm_provider_env" in tf, nome
    for nome in ("lambda_engines.tf", "lambda_intervals_sync.tf", "lambda_migrations.tf"):
        tf = _tf(nome)
        assert "anthropic_api_key" not in tf and "llm_provider_env" not in tf, nome


def test_secret_sem_import_sem_version_nem_descricao():
    tf = _tf("secrets.tf")
    # O import rodou no deploy de 2026-10-31 (promocao #14); o bloco saiu e o
    # recurso continua no state. Voltar com o bloco nao quebraria nada, mas
    # nao ha motivo: o ARN so entra nas Lambdas pela referencia ao recurso.
    assert "to = aws_secretsmanager_secret.anthropic_api_key" not in tf
    recurso = re.search(r'resource "aws_secretsmanager_secret" "anthropic_api_key" \{(.*?)\n\}', tf, re.S).group(1)
    assert 'name = "musclefit/claude-api-key"' in recurso and "description" not in recurso
    assert 'aws_secretsmanager_secret_version" "anthropic_api_key"' not in tf


def test_dev_cria_o_mesmo_secret_no_localstack():
    bootstrap = (REPO / "localstack" / "init" / "ready.d" / "00-bootstrap.sh").read_text(encoding="utf-8")
    assert 'create_or_update_secret "musclefit/claude-api-key"' in bootstrap
    assert "ANTHROPIC_API_KEY=" in (REPO / ".env.example").read_text(encoding="utf-8")


# Limite da AWS: 4 KB para TODAS as envs de uma Lambda. Medido em producao
# (leitura, 2026-10-31) antes da virada: a api-core, a maior, tinha 1718 bytes
# (chave + valor) sem nenhuma env de LLM. O bloco de LLM tem de caber no que
# sobra com folga; a proxima feature ligada deve colapsar as envs (por
# exemplo, um JSON unico) se este orcamento estourar. Desde a foto do prato
# (nutricao L10), `LLM_PROVIDER_<FEATURE>=openai` nao e mais publicado: e o padrao
# do codigo e so gastava orcamento.
API_CORE_ENV_BYTES_SEM_LLM = 1718
LAMBDA_ENV_LIMIT_BYTES = 4096
LLM_ENV_BUDGET_BYTES = 1400


def test_bloco_de_envs_de_llm_cabe_no_limite_de_4kb_da_lambda():
    tf = _tf("llm_providers.tf")
    provedores = _mapa_padrao(tf, "llm_provider_by_feature")
    assert llm_anthropic.DEFAULT_PROVIDER == llm_anthropic.PROVIDER_OPENAI  # o que nao e publicado
    envs = {
        f"LLM_PROVIDER_{f}".upper(): provedores[f]
        for f in llm_anthropic.LLM_FEATURES
        if provedores.get(f, llm_anthropic.DEFAULT_PROVIDER) != llm_anthropic.DEFAULT_PROVIDER
    }
    for variavel, prefixo in (
        ("anthropic_model_by_feature", "ANTHROPIC_MODEL_"),
        ("anthropic_effort_by_feature", "ANTHROPIC_EFFORT_"),
        ("anthropic_thinking_by_feature", "ANTHROPIC_THINKING_"),
        ("llm_fallback_provider_by_feature", "LLM_FALLBACK_PROVIDER_"),
    ):
        envs.update({f"{prefixo}{f.upper()}": v for f, v in _mapa_padrao(tf, variavel).items()})
    envs["ANTHROPIC_API_KEY_SECRET_ARN"] = ARN
    tamanho = sum(len(k) + len(v) for k, v in envs.items())
    assert tamanho <= LLM_ENV_BUDGET_BYTES, tamanho
    assert API_CORE_ENV_BYTES_SEM_LLM + LLM_ENV_BUDGET_BYTES < LAMBDA_ENV_LIMIT_BYTES


def _envs_publicadas(tf: str) -> dict[str, str]:
    """As envs de LLM que `local.llm_provider_env` publica com os padroes do Terraform (sem o ARN)."""
    provedores = _mapa_padrao(tf, "llm_provider_by_feature")
    features = re.findall(r'"([a-z_]+)"', re.search(r"llm_features = \[(.*?)\]", tf, re.S).group(1))
    envs = {f"LLM_PROVIDER_{f.upper()}": provedores[f] for f in features if provedores.get(f, "openai") != "openai"}
    for variavel, prefixo in (
        ("anthropic_model_by_feature", "ANTHROPIC_MODEL_"),
        ("anthropic_effort_by_feature", "ANTHROPIC_EFFORT_"),
        ("anthropic_thinking_by_feature", "ANTHROPIC_THINKING_"),
        ("llm_fallback_provider_by_feature", "LLM_FALLBACK_PROVIDER_"),
    ):
        envs.update({f"{prefixo}{f.upper()}": v for f, v in _mapa_padrao(tf, variavel).items() if f in features})
    return envs


# O que cada feature usa em producao ANTES do N6 (provedor, modelo, esforco, raciocinio, fallback), escrito por
# extenso de proposito: e a prova de que a sugestao de metas nao muda nenhuma feature existente.
HAIKU = llm_anthropic.ANTHROPIC_MODEL_HAIKU
SONNET = llm_anthropic.ANTHROPIC_MODEL_SONNET
CONFIG_EM_PRODUCAO_ANTES_DO_N6 = {
    "set_evaluation": ("openai", HAIKU, "low", "adaptive", None),
    "exercise_summary": ("openai", HAIKU, "low", "adaptive", None),
    "workout_summary": ("anthropic", HAIKU, "low", "adaptive", "openai"),
    "exercise_description": ("anthropic", HAIKU, "medium", "adaptive", "openai"),
    "insights": ("anthropic", HAIKU, "medium", "adaptive", "openai"),
    "workouts_analysis": ("openai", SONNET, "medium", "adaptive", None),
    "cardio_agent": ("anthropic", HAIKU, "medium", "adaptive", "openai"),
    "periodization": ("anthropic", HAIKU, "low", "disabled", "openai"),
    "meal_parse": ("anthropic", HAIKU, "medium", "adaptive", "openai"),
    "label_photo": ("anthropic", HAIKU, "medium", "adaptive", "openai"),
    "meal_photo": ("anthropic", HAIKU, "medium", "adaptive", "openai"),
    "workout_plan": ("openai", SONNET, "medium", "adaptive", None),
}


def _config(feature: str) -> tuple:
    return (
        llm_anthropic.provider_for(feature),
        llm_anthropic.anthropic_model_for(feature),
        llm_anthropic.anthropic_effort_for(feature),
        llm_anthropic.anthropic_thinking_for(feature),
        llm_anthropic.fallback_provider_for(feature),
    )


def test_n6_nao_muda_provedor_nem_modelo_de_nenhuma_feature_existente(monkeypatch):
    """Roda o codigo com as envs que o Terraform publica. Toda feature antiga fica como estava."""
    for nome in list(os.environ):
        if nome.startswith(("LLM_", "ANTHROPIC_")):
            monkeypatch.delenv(nome)
    for nome, valor in _envs_publicadas(_tf("llm_providers.tf")).items():
        monkeypatch.setenv(nome, valor)
    assert {f: _config(f) for f in CONFIG_EM_PRODUCAO_ANTES_DO_N6} == CONFIG_EM_PRODUCAO_ANTES_DO_N6
    assert set(llm_anthropic.LLM_FEATURES) == {*CONFIG_EM_PRODUCAO_ANTES_DO_N6, "plan_suggestion"}


def test_sugestao_de_metas_le_a_config_de_meal_parse_sem_env_propria(monkeypatch):
    tf = _tf("llm_providers.tf")
    envs = _envs_publicadas(tf)
    # Nenhuma env nova: o bloco de LLM continua do mesmo tamanho.
    assert not [nome for nome in envs if "PLAN_SUGGESTION" in nome]
    for nome, valor in envs.items():
        monkeypatch.setenv(nome, valor)
    assert llm_anthropic.FEATURE_CONFIG_SOURCE == {"plan_suggestion": "meal_parse"}
    assert _config("plan_suggestion") == _config("meal_parse") == ("anthropic", HAIKU, "medium", "adaptive", "openai")
    # So ela le env de outra feature; as demais usam o proprio nome.
    for feature in CONFIG_EM_PRODUCAO_ANTES_DO_N6:
        assert llm_anthropic.feature_env(llm_anthropic.ENV_PROVIDER_PREFIX, feature) == f"LLM_PROVIDER_{feature.upper()}"


def test_raciocinio_publicado_so_com_valores_e_esforcos_que_a_api_aceita():
    tf = _tf("llm_providers.tf")
    assert '"ANTHROPIC_THINKING_${upper(f)}"' in tf
    raciocinio = _mapa_padrao(tf, "anthropic_thinking_by_feature")
    esforcos = _mapa_padrao(tf, "anthropic_effort_by_feature")
    assert set(raciocinio) <= set(llm_anthropic.LLM_FEATURES)
    for feature, modo in raciocinio.items():
        assert modo in llm_anthropic.THINKING_MODES, feature
        if modo == llm_anthropic.THINKING_DISABLED:
            esforco = esforcos.get(feature, llm_anthropic.DEFAULT_ANTHROPIC_EFFORT[feature])
            assert esforco in llm_anthropic.EFFORTS_WITHOUT_THINKING, feature
