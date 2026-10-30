"""Contrato infra x codigo do provedor LLM por feature (infra/llm_providers.tf).

- a lista de features publicada pelo Terraform e a de `llm_anthropic.LLM_FEATURES`;
- o padrao publicado e `openai` (nada muda sem PR que ligue a feature);
- o ARN e a leitura do secret `musclefit/claude-api-key` vao SO para as 3
  Lambdas que ja leem a chave da OpenAI; engines, intervals-sync e migrations
  ficam sem;
- o secret entra por `import` (ja existe), sem `_version` nem `description`;
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


def test_padrao_publicado_e_openai_sem_modelo_nem_fallback():
    tf = _tf("llm_providers.tf")
    assert 'lookup(var.llm_provider_by_feature, f, "openai")' in tf
    for variavel in ("llm_provider_by_feature", "anthropic_model_by_feature", "anthropic_effort_by_feature"):
        assert re.search(rf'variable "{variavel}" \{{.*?default\s+= \{{\}}', tf, re.S), variavel
    assert re.search(r'variable "llm_fallback_provider" \{.*?default\s+= ""', tf, re.S)
    assert llm_anthropic.DEFAULT_PROVIDER == "openai"


def test_so_as_tres_lambdas_de_ia_leem_a_chave_da_anthropic():
    for nome in ("lambda_api_core.tf", "lambda_ai_generators.tf", "lambda_workout_ai.tf"):
        tf = _tf(nome)
        assert "aws_secretsmanager_secret.anthropic_api_key.arn" in tf, nome
        assert "local.llm_provider_env" in tf, nome
    for nome in ("lambda_engines.tf", "lambda_intervals_sync.tf", "lambda_migrations.tf"):
        tf = _tf(nome)
        assert "anthropic_api_key" not in tf and "llm_provider_env" not in tf, nome


def test_secret_importado_sem_version_nem_descricao():
    tf = _tf("secrets.tf")
    assert f'id = "{ARN}"' in tf
    recurso = re.search(r'resource "aws_secretsmanager_secret" "anthropic_api_key" \{(.*?)\n\}', tf, re.S).group(1)
    assert 'name = "musclefit/claude-api-key"' in recurso and "description" not in recurso
    assert 'aws_secretsmanager_secret_version" "anthropic_api_key"' not in tf


def test_dev_cria_o_mesmo_secret_no_localstack():
    bootstrap = (REPO / "localstack" / "init" / "ready.d" / "00-bootstrap.sh").read_text(encoding="utf-8")
    assert 'create_or_update_secret "musclefit/claude-api-key"' in bootstrap
    assert "ANTHROPIC_API_KEY=" in (REPO / ".env.example").read_text(encoding="utf-8")
