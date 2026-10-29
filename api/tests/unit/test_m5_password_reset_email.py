"""E-mail de redefinicao (M5): URL montada a partir de `APP_BASE_URL`, envio
via SES (boto3 substituido), env ausente -> evento estruturado sem token, e
o token NUNCA aparece em nenhuma linha de log.
"""

from __future__ import annotations

import json
import logging

import pytest

from app import password_reset as pr

TOKEN = "TOKEN-SECRETO-abc123_XYZ"


class SesFalso:
    def __init__(self, erro: Exception | None = None):
        self.erro = erro
        self.envios: list[dict] = []

    def send_email(self, **kwargs):
        if self.erro:
            raise self.erro
        self.envios.append(kwargs)
        return {"MessageId": "fake"}


@pytest.fixture
def ses(monkeypatch):
    cliente = SesFalso()
    monkeypatch.setattr(pr, "_ses_client", lambda: cliente)
    return cliente


@pytest.fixture
def envs(monkeypatch):
    monkeypatch.setenv(pr.FROM_EMAIL_ENV, "no-reply@musclefit.com.br")
    monkeypatch.setenv(pr.APP_BASE_URL_ENV, "https://app.musclefit.com.br/")


def _eventos(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "musclefit.events"]


def test_url_de_redefinicao_segue_o_contrato_do_frontend():
    assert pr.build_reset_url("https://app.musclefit.com.br", "a+b/c=") == (
        "https://app.musclefit.com.br/redefinir-senha?token=a%2Bb%2Fc%3D"
    )
    # Barra final da base nao duplica.
    assert pr.build_reset_url("http://localhost:3000/", "t").startswith("http://localhost:3000/redefinir-senha?")


def test_envio_usa_ses_com_remetente_destinatario_e_link(ses, envs, caplog):
    with caplog.at_level(logging.INFO):
        assert pr.send_reset_email(user_id=7, to_email="doug@example.com", username="doug", raw_token=TOKEN) is True

    (envio,) = ses.envios
    assert envio["Source"] == "no-reply@musclefit.com.br"
    assert envio["Destination"] == {"ToAddresses": ["doug@example.com"]}
    texto = envio["Message"]["Body"]["Text"]["Data"]
    html = envio["Message"]["Body"]["Html"]["Data"]
    assert f"https://app.musclefit.com.br/redefinir-senha?token={TOKEN}" in texto
    assert f"redefinir-senha?token={TOKEN}" in html
    assert "doug" in texto

    assert TOKEN not in caplog.text
    (evento,) = [e for e in _eventos(caplog) if e["event"] == pr.EVENT_EMAIL_SENT]
    assert evento == {"event": pr.EVENT_EMAIL_SENT, "user_id": 7}


@pytest.mark.parametrize("faltando", [pr.FROM_EMAIL_ENV, pr.APP_BASE_URL_ENV])
def test_env_ausente_nao_envia_e_loga_evento_sem_token(ses, envs, monkeypatch, caplog, faltando):
    monkeypatch.delenv(faltando)
    with caplog.at_level(logging.INFO):
        assert pr.send_reset_email(user_id=7, to_email="x@example.com", username="x", raw_token=TOKEN) is False
    assert ses.envios == []
    (evento,) = [e for e in _eventos(caplog) if e["event"] == pr.EVENT_EMAIL_NOT_CONFIGURED]
    assert evento["missing_env"] == [faltando]
    assert TOKEN not in caplog.text


def test_falha_do_ses_nao_propaga_e_loga_so_o_tipo(envs, monkeypatch, caplog):
    cliente = SesFalso(erro=RuntimeError(f"boom {TOKEN}"))
    monkeypatch.setattr(pr, "_ses_client", lambda: cliente)
    with caplog.at_level(logging.INFO):
        assert pr.send_reset_email(user_id=7, to_email="x@example.com", username="x", raw_token=TOKEN) is False
    (evento,) = [e for e in _eventos(caplog) if e["event"] == pr.EVENT_EMAIL_FAILED]
    assert evento == {"event": pr.EVENT_EMAIL_FAILED, "user_id": 7, "reason": "RuntimeError"}
    assert TOKEN not in caplog.text


def test_message_rejected_do_ses_vira_evento_com_o_codigo_e_nao_propaga(envs, monkeypatch, caplog):
    """Identidade nao verificada / sandbox do SES: `ClientError` com
    `MessageRejected`. A rota segue 202 - aqui so garantimos que o envio
    devolve False e loga o codigo, sem token nem e-mail."""
    from botocore.exceptions import ClientError

    erro = ClientError({"Error": {"Code": "MessageRejected", "Message": f"Email address is not verified {TOKEN}"}}, "SendEmail")
    cliente = SesFalso(erro=erro)
    monkeypatch.setattr(pr, "_ses_client", lambda: cliente)
    with caplog.at_level(logging.INFO):
        assert pr.send_reset_email(user_id=7, to_email="x@example.com", username="x", raw_token=TOKEN) is False
    (evento,) = [e for e in _eventos(caplog) if e["event"] == pr.EVENT_EMAIL_FAILED]
    assert evento == {"event": pr.EVENT_EMAIL_FAILED, "user_id": 7, "reason": "ClientError", "error_code": "MessageRejected"}
    assert TOKEN not in caplog.text
    assert "x@example.com" not in caplog.text


def test_bootstrap_de_secrets_conhece_o_turnstile_e_nao_sobrescreve_env_direta(monkeypatch):
    """Contrato do PR de infra: a Lambda recebe TURNSTILE_SECRET_KEY_SECRET_ARN;
    em DEV a env direta existe e o bootstrap nao tenta o Secrets Manager."""
    from app import secrets_bootstrap

    assert ("TURNSTILE_SECRET_KEY", "TURNSTILE_SECRET_KEY_SECRET_ARN") in secrets_bootstrap._SECRETS

    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "1x0000000000000000000000000000000AA")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY_SECRET_ARN", "arn:aws:secretsmanager:sa-east-1:1:secret:x")
    for env_name, _ in secrets_bootstrap._SECRETS:
        if env_name != "TURNSTILE_SECRET_KEY":
            monkeypatch.delenv(env_name, raising=False)
            monkeypatch.delenv(f"{env_name}_SECRET_ARN", raising=False)

    def boto3_proibido(*args, **kwargs):
        raise AssertionError("nao deveria consultar o Secrets Manager com a env direta definida")

    import boto3

    monkeypatch.setattr(boto3, "client", boto3_proibido)
    secrets_bootstrap.load_secrets()
    assert __import__("os").environ["TURNSTILE_SECRET_KEY"] == "1x0000000000000000000000000000000AA"
