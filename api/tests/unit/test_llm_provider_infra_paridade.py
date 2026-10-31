"""Contrato infra x codigo do provedor LLM por feature (infra/llm_providers.tf).

- a lista de features publicada pelo Terraform e a de `llm_anthropic.LLM_FEATURES`;
- so as 6 features da virada vao para o Claude Haiku, com fallback por
  feature; o resto segue `openai`;
- o ARN e a leitura do secret `musclefit/claude-api-key` vao SO para as 3
  Lambdas que ja leem a chave da OpenAI; engines, intervals-sync e migrations
  ficam sem;
- o secret ja esta no state (import aplicado em 2026-10-31, bloco removido),
  sem `_version` nem `description`;
- o DEV (LocalStack) cria o mesmo secret."""

from __future__ import annotations

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
    "insights": "medium",
    "meal_parse": "medium",
    "exercise_description": "medium",
    "workout_summary": "low",
}


def _mapa_padrao(tf: str, variavel: str) -> dict[str, str]:
    if re.search(rf'variable "{variavel}" \{{[^}}]*?default\s*=\s*\{{\}}', tf):
        return {}
    bloco = re.search(rf'variable "{variavel}" \{{.*?default = \{{(.*?)\n  \}}', tf, re.S).group(1)
    return dict(re.findall(r'^\s*([a-z_]+)\s*=\s*"([^"]*)"', bloco, re.M))


def test_padrao_publicado_liga_so_as_6_da_virada_no_haiku_com_fallback_por_feature():
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
# exemplo, um JSON unico) se este orcamento estourar.
API_CORE_ENV_BYTES_SEM_LLM = 1718
LAMBDA_ENV_LIMIT_BYTES = 4096
LLM_ENV_BUDGET_BYTES = 1400


def test_bloco_de_envs_de_llm_cabe_no_limite_de_4kb_da_lambda():
    tf = _tf("llm_providers.tf")
    provedores = _mapa_padrao(tf, "llm_provider_by_feature")
    envs = {f"LLM_PROVIDER_{f.upper()}": provedores.get(f, "openai") for f in llm_anthropic.LLM_FEATURES}
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
