"""Chave do USDA FoodData Central (nutricao, Lote L3b): contrato entre a infra
e `app/secrets_bootstrap.py`.

- Em producao a api-core recebe so `USDA_API_KEY_SECRET_ARN`
  (infra/lambda_api_core.tf) e o bootstrap hidrata `USDA_API_KEY` no cold start.
- Em DEV o `.env` define `USDA_API_KEY` direto e o ARN nao existe: no-op.
- Sem ARN, ou com o secret ilegivel, o cold start NAO quebra: a variavel fica
  ausente e quem consome responde 503 (plano de nutricao, secao 8.1). Secret
  ilegivel deixa um aviso `secrets_bootstrap.load_failed` sem o valor.
- So a api-core recebe o ARN; nenhum `.tf` carrega a chave em texto.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pytest

from app import secrets_bootstrap

REPO = Path(__file__).resolve().parents[3]
ARN = "arn:aws:secretsmanager:sa-east-1:1:secret:musclefit/usda-api-key-AbC123"
VALOR = "chave-falsa-de-teste-9f3a"


class ClienteFalso:
    def __init__(self, valor: str | None = None, erro: Exception | None = None):
        self.valor = valor
        self.erro = erro
        self.pedidos: list[str] = []

    def get_secret_value(self, SecretId: str):  # noqa: N803 - assinatura do boto3
        self.pedidos.append(SecretId)
        if self.erro:
            raise self.erro
        return {"SecretString": self.valor}


@pytest.fixture
def so_usda(monkeypatch):
    """Isola o teste nos pares do USDA: nenhum outro secret pendente.

    setenv antes do delenv: `delenv` de variavel ausente nao registra nada, e
    o que `load_secrets()` grava direto em os.environ vazaria para outros
    testes. Com o setenv o monkeypatch guarda "ausente" e apaga no fim."""
    for env_name, arn_env in secrets_bootstrap._SECRETS:
        for nome in (env_name, arn_env):
            monkeypatch.setenv(nome, "")
            monkeypatch.delenv(nome)


def _boto3_com(monkeypatch, cliente: ClienteFalso | None):
    import boto3

    def fabrica(servico, **kwargs):
        if cliente is None:
            raise AssertionError("nao deveria consultar o Secrets Manager")
        assert servico == "secretsmanager"
        return cliente

    monkeypatch.setattr(boto3, "client", fabrica)


def test_bootstrap_mapeia_a_chave_do_usda():
    assert ("USDA_API_KEY", "USDA_API_KEY_SECRET_ARN") in secrets_bootstrap._SECRETS


def test_sem_arn_e_no_op_e_nao_quebra_o_cold_start(monkeypatch, so_usda):
    _boto3_com(monkeypatch, None)
    secrets_bootstrap.load_secrets()
    assert "USDA_API_KEY" not in os.environ


def _avisos(caplog) -> list[dict]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "musclefit.events" and r.levelno == logging.WARNING
    ]


def test_com_arn_hidrata_a_chave_sem_aviso_e_sem_logar_o_valor(monkeypatch, so_usda, caplog):
    cliente = ClienteFalso(valor=VALOR)
    _boto3_com(monkeypatch, cliente)
    monkeypatch.setenv("USDA_API_KEY_SECRET_ARN", ARN)
    with caplog.at_level(logging.DEBUG):
        secrets_bootstrap.load_secrets()
    assert cliente.pedidos == [ARN]
    assert os.environ["USDA_API_KEY"] == VALOR
    assert _avisos(caplog) == []
    assert VALOR not in caplog.text


@pytest.mark.parametrize("codigo", ["AccessDeniedException", "ResourceNotFoundException"])
def test_secret_ilegivel_avisa_e_segue_sem_a_chave(monkeypatch, so_usda, caplog, codigo):
    """O aviso sai em WARNING (o cold start roda antes do configure_logging, e
    um INFO seria descartado) com o NOME da env do ARN e o codigo AWS; a
    mensagem da excecao - aqui carregando um valor de mentira - nunca entra."""
    from botocore.exceptions import ClientError

    erro = ClientError({"Error": {"Code": codigo, "Message": f"vazou {VALOR} {ARN}"}}, "GetSecretValue")
    cliente = ClienteFalso(erro=erro)
    _boto3_com(monkeypatch, cliente)
    monkeypatch.setenv("USDA_API_KEY_SECRET_ARN", ARN)
    with caplog.at_level(logging.WARNING):
        secrets_bootstrap.load_secrets()
    assert cliente.pedidos == [ARN]
    assert "USDA_API_KEY" not in os.environ
    assert _avisos(caplog) == [
        {
            "event": secrets_bootstrap.EVENT_SECRET_LOAD_FAILED,
            "arn_env": "USDA_API_KEY_SECRET_ARN",
            "env": "USDA_API_KEY",
            "error_class": "ClientError",
            "error_code": codigo,
        }
    ]
    assert VALOR not in caplog.text
    assert ARN not in caplog.text


def test_erro_sem_codigo_aws_avisa_so_a_classe(monkeypatch, so_usda, caplog):
    cliente = ClienteFalso(erro=TimeoutError(f"vazou {VALOR}"))
    _boto3_com(monkeypatch, cliente)
    monkeypatch.setenv("USDA_API_KEY_SECRET_ARN", ARN)
    with caplog.at_level(logging.WARNING):
        secrets_bootstrap.load_secrets()
    assert "USDA_API_KEY" not in os.environ
    (aviso,) = _avisos(caplog)
    assert aviso["error_class"] == "TimeoutError"
    assert "error_code" not in aviso
    assert VALOR not in caplog.text


def test_secret_sem_secret_string_avisa_e_segue(monkeypatch, so_usda, caplog):
    cliente = ClienteFalso(valor=None)
    _boto3_com(monkeypatch, cliente)
    monkeypatch.setenv("USDA_API_KEY_SECRET_ARN", ARN)
    with caplog.at_level(logging.WARNING):
        secrets_bootstrap.load_secrets()
    assert "USDA_API_KEY" not in os.environ
    (aviso,) = _avisos(caplog)
    assert aviso["arn_env"] == "USDA_API_KEY_SECRET_ARN"
    assert aviso["error_class"] == "EmptySecretString"


def test_env_direta_de_dev_vence_e_nao_consulta_o_secrets_manager(monkeypatch, so_usda):
    _boto3_com(monkeypatch, None)
    monkeypatch.setenv("USDA_API_KEY", "chave-do-env-de-dev")
    monkeypatch.setenv("USDA_API_KEY_SECRET_ARN", ARN)
    secrets_bootstrap.load_secrets()
    assert os.environ["USDA_API_KEY"] == "chave-do-env-de-dev"


def test_so_a_api_core_recebe_o_arn_e_nenhum_tf_tem_a_chave_em_texto():
    tfs = sorted((REPO / "infra").glob("*.tf"))
    assert tfs, "diretorio infra/ sem .tf - caminho do repo mudou?"
    atribui_arn = re.compile(r"^\s*USDA_API_KEY_SECRET_ARN\s*=", re.M)
    com_arn = [p.name for p in tfs if atribui_arn.search(p.read_text(encoding="utf-8"))]
    assert com_arn == ["lambda_api_core.tf"]

    api_core = (REPO / "infra" / "lambda_api_core.tf").read_text(encoding="utf-8")
    # Env da Lambda + GetSecretValue da role de execucao.
    assert api_core.count("aws_secretsmanager_secret.usda_api_key.arn") == 2

    chave_em_texto = re.compile(r"^\s*USDA_API_KEY\s*=", re.M)
    assert not [p.name for p in tfs if chave_em_texto.search(p.read_text(encoding="utf-8"))]
