"""M5 "Esqueci minha senha" - fluxo completo contra MySQL real, com o captcha
e o SES substituidos (o token cru e capturado do "e-mail").

Seguranca provada aqui:
- resposta IDENTICA (status + corpo) para conta existente, inexistente,
  inativa e sem e-mail;
- captcha ausente/invalido -> 400 sem consultar a conta; sem secret -> 503;
- token invalido, expirado e ja usado sao rejeitados com a mesma mensagem;
- uso unico (segunda confirmacao com o mesmo token falha) e a senha nova
  autentica; a antiga nao;
- confirmar revoga TODAS as sessoes ativas e limpa o lockout;
- rate limit por IP (request e confirm) e por conta, identico para conta
  existente e inexistente;
- o token nunca aparece em NENHUMA linha de log.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, captcha, password_reset as pr
from app.auth import MAX_FAILED_ATTEMPTS, hash_password
from app.db import transaction

SENHA_ANTIGA = "SenhaAntiga123"
SENHA_NOVA = "SenhaNova45678"
TOKEN_RE = re.compile(r"redefinir-senha\?token=([A-Za-z0-9_\-]+)")


# A 043 (`password_reset_tokens`) ja vem no schema compartilhado: a fixture
# `mysql_schema` aplica TODAS as migracoes `.py` (M25). Nao ha mais nada para
# este modulo preparar.


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", "")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")
    monkeypatch.setenv(captcha.TURNSTILE_SECRET_ENV, "1x0000000000000000000000000000000AA")
    monkeypatch.setenv(pr.FROM_EMAIL_ENV, "no-reply@musclefit.test")
    monkeypatch.setenv(pr.APP_BASE_URL_ENV, "http://localhost:3000")


@pytest.fixture
def captcha_fake(monkeypatch):
    """Turnstile de mentira: token 'ok' passa, qualquer outro falha; registra
    quantas vezes foi consultado."""
    chamadas: list[str] = []

    def fake(url, fields, timeout):
        chamadas.append(fields["response"])
        return {"success": fields["response"] == "ok"}

    monkeypatch.setattr(captcha, "_http_post_form", fake)
    return chamadas


@pytest.fixture
def emails(monkeypatch):
    """SES de mentira: guarda cada envio para o teste extrair o token."""
    enviados: list[dict] = []

    class Ses:
        def send_email(self, **kwargs):
            enviados.append(kwargs)
            return {"MessageId": "fake"}

    monkeypatch.setattr(pr, "_ses_client", lambda: Ses())
    return enviados


@pytest.fixture
def contas(catalog):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_sessions")
            cur.execute("DELETE FROM password_reset_tokens")
            cur.execute("DELETE FROM password_reset_attempts")
            cur.execute(
                "INSERT INTO users (username, password_hash, email, role, is_active) VALUES (%s, %s, %s, 'regular', 1)",
                ("alvo", hash_password(SENHA_ANTIGA), "Alvo@Example.com"),
            )
            alvo_id = cur.lastrowid
            cur.execute(
                "INSERT INTO users (username, password_hash, email, role, is_active) VALUES (%s, %s, %s, 'regular', 0)",
                ("inativo", hash_password(SENHA_ANTIGA), "inativo@example.com"),
            )
            cur.execute(
                "INSERT INTO users (username, password_hash, email, role, is_active) VALUES (%s, %s, NULL, 'regular', 1)",
                ("sem-email", hash_password(SENHA_ANTIGA)),
            )
    return {"alvo_id": alvo_id}


@pytest.fixture
def client(contas, captcha_fake, emails) -> TestClient:
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(pr.router)
    return TestClient(app, raise_server_exceptions=False)


def pedir(client, *, captcha_token="ok", **ident):
    return client.post("/auth/password-reset/request", json={"captcha_token": captcha_token, **ident})


def confirmar(client, token, senha=SENHA_NOVA):
    return client.post("/auth/password-reset/confirm", json={"token": token, "new_password": senha})


def token_do_email(emails) -> str:
    texto = emails[-1]["Message"]["Body"]["Text"]["Data"]
    return TOKEN_RE.search(texto).group(1)


def login(client, senha):
    return client.post("/auth/login", json={"username": "alvo", "password": senha})


def ler_usuario(user_id):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT failed_login_attempts, locked_until, must_change_password FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()


# ---------------------------------------------------------------------------
# Solicitacao: resposta identica e captcha
# ---------------------------------------------------------------------------


def test_resposta_identica_para_conta_existente_inexistente_inativa_e_sem_email(client, emails):
    respostas = [
        pedir(client, email="alvo@example.com"),
        pedir(client, username="alvo"),
        pedir(client, email="ninguem@example.com"),
        pedir(client, username="nao-existe"),
        pedir(client, username="inativo"),
        pedir(client, username="sem-email"),
    ]
    assert {r.status_code for r in respostas} == {202}
    assert len({r.text for r in respostas}) == 1, "corpos diferentes vazam se a conta existe"
    assert respostas[0].json() == {"status": "accepted", "detail": pr.ACCEPTED_DETAIL}
    # So a conta ativa com e-mail recebeu (2 pedidos: por e-mail e por usuario).
    assert len(emails) == 2
    assert all(e["Destination"] == {"ToAddresses": ["Alvo@Example.com"]} for e in emails)


def test_captcha_ausente_ou_invalido_e_400_sem_consultar_a_conta(client, emails, captcha_fake):
    sem = client.post("/auth/password-reset/request", json={"username": "alvo"})
    assert sem.status_code == 400
    assert sem.json()["detail"] == pr.CAPTCHA_INVALID_DETAIL
    assert captcha_fake == [], "token vazio nao deve nem chegar na Cloudflare"

    ruim = pedir(client, username="alvo", captcha_token="ruim")
    assert ruim.status_code == 400
    assert emails == []
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM password_reset_tokens")
            assert cur.fetchone()["n"] == 0
            cur.execute("SELECT COUNT(*) AS n FROM password_reset_attempts WHERE scope = %s", (pr.SCOPE_REQUEST_ACCOUNT,))
            assert cur.fetchone()["n"] == 0, "sem captcha nao conta por conta..."
            cur.execute("SELECT COUNT(*) AS n FROM password_reset_attempts WHERE scope = %s", (pr.SCOPE_REQUEST_IP,))
            assert cur.fetchone()["n"] == 2, "...mas conta por IP"


def test_sem_secret_do_turnstile_falha_fechado_com_503(client, monkeypatch, emails):
    monkeypatch.delenv(captcha.TURNSTILE_SECRET_ENV)
    resposta = pedir(client, username="alvo")
    assert resposta.status_code == 503
    assert resposta.json()["detail"] == pr.CAPTCHA_UNAVAILABLE_DETAIL
    assert emails == []


def test_sem_identificador_e_400(client):
    assert pedir(client).status_code == 400


# ---------------------------------------------------------------------------
# Confirmacao
# ---------------------------------------------------------------------------


def test_fluxo_completo_troca_a_senha_e_o_token_e_de_uso_unico(client, emails, contas):
    assert pedir(client, email="ALVO@example.com").status_code == 202
    token = token_do_email(emails)

    ok = confirmar(client, token)
    assert ok.status_code == 200, ok.text

    assert login(client, SENHA_ANTIGA).status_code == 401
    assert login(client, SENHA_NOVA).status_code == 200
    assert ler_usuario(contas["alvo_id"])["must_change_password"] == 0

    de_novo = confirmar(client, token, "OutraSenha9999")
    assert de_novo.status_code == 400
    assert de_novo.json()["detail"] == pr.INVALID_TOKEN_DETAIL
    assert login(client, "OutraSenha9999").status_code == 401


def test_token_invalido_e_expirado_sao_rejeitados_com_a_mesma_mensagem(client, emails):
    invalido = confirmar(client, "nao-e-um-token")
    assert invalido.status_code == 400

    pedir(client, username="alvo")
    token = token_do_email(emails)
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE password_reset_tokens SET expires_at = %s", (datetime.utcnow() - timedelta(seconds=1),))
    expirado = confirmar(client, token)
    assert expirado.status_code == 400
    assert expirado.json()["detail"] == invalido.json()["detail"] == pr.INVALID_TOKEN_DETAIL
    assert login(client, SENHA_NOVA).status_code == 401


def test_senha_curta_segue_a_mesma_politica_do_change_password(client, emails):
    pedir(client, username="alvo")
    resposta = confirmar(client, token_do_email(emails), "curta")
    assert resposta.status_code == 422
    # Token continua valido: a rejeicao foi do schema, antes de qualquer escrita.
    assert confirmar(client, token_do_email(emails)).status_code == 200


def test_confirmar_revoga_todas_as_sessoes_ativas(client, emails):
    assert login(client, SENHA_ANTIGA).status_code == 200
    assert client.get("/auth/me").status_code == 200

    pedir(client, username="alvo")
    assert confirmar(client, token_do_email(emails)).status_code == 200

    assert client.get("/auth/me").status_code == 401


def test_conta_bloqueada_pode_pedir_reset_e_confirmar_limpa_o_lockout(client, emails, contas):
    for _ in range(MAX_FAILED_ATTEMPTS):
        login(client, "errada")
    assert ler_usuario(contas["alvo_id"])["locked_until"] is not None
    assert login(client, SENHA_ANTIGA).status_code == 401  # SEG-07: sem 423 distinto

    assert pedir(client, username="alvo").status_code == 202
    assert confirmar(client, token_do_email(emails)).status_code == 200

    linha = ler_usuario(contas["alvo_id"])
    assert linha["locked_until"] is None and linha["failed_login_attempts"] == 0
    assert login(client, SENHA_NOVA).status_code == 200


def test_confirmar_queima_os_outros_tokens_pendentes_do_usuario(client, emails):
    pedir(client, username="alvo")
    primeiro = token_do_email(emails)
    pedir(client, username="alvo")
    segundo = token_do_email(emails)
    assert confirmar(client, segundo).status_code == 200
    assert confirmar(client, primeiro, "OutraSenha9999").status_code == 400


def test_usuario_inativo_com_token_valido_e_rejeitado(client, emails, contas):
    pedir(client, username="alvo")
    token = token_do_email(emails)
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET is_active = 0 WHERE id = %s", (contas["alvo_id"],))
    assert confirmar(client, token).status_code == 400


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


def test_rate_limit_por_conta_e_identico_para_conta_existente_e_inexistente(client, emails):
    for nome in ("alvo", "fantasma"):
        for _ in range(pr.MAX_REQUESTS_PER_ACCOUNT):
            assert pedir(client, username=nome).status_code == 202
        bloqueado = pedir(client, username=nome)
        assert bloqueado.status_code == 429
        assert bloqueado.json()["detail"] == pr.RATE_LIMITED_DETAIL
    assert len(emails) == pr.MAX_REQUESTS_PER_ACCOUNT


def test_rate_limit_por_ip_na_solicitacao_inclui_tentativas_sem_captcha(client):
    for i in range(pr.MAX_REQUESTS_PER_IP):
        # Identificadores diferentes: so o IP e comum. Metade sem captcha.
        resposta = pedir(client, username=f"u{i}", captcha_token="ok" if i % 2 else "ruim")
        assert resposta.status_code in (202, 400)
    assert pedir(client, username="outro").status_code == 429


def test_rate_limit_por_ip_na_confirmacao(client):
    for _ in range(pr.MAX_CONFIRMS_PER_IP):
        assert confirmar(client, "chute").status_code == 400
    assert confirmar(client, "chute").status_code == 429


def test_janela_expirada_libera_de_novo(client):
    for _ in range(pr.MAX_REQUESTS_PER_ACCOUNT):
        pedir(client, username="alvo")
    assert pedir(client, username="alvo").status_code == 429
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE password_reset_attempts SET created_at = %s",
                (datetime.utcnow() - timedelta(minutes=pr.RATE_LIMIT_WINDOW_MINUTES + 1),),
            )
    assert pedir(client, username="alvo").status_code == 202


# ---------------------------------------------------------------------------
# Logs e persistencia
# ---------------------------------------------------------------------------


def test_token_nunca_aparece_em_log_nem_em_claro_no_banco(client, emails, caplog):
    with caplog.at_level(logging.DEBUG):
        pedir(client, username="alvo")
        token = token_do_email(emails)
        confirmar(client, token)
    assert token not in caplog.text
    assert "Alvo@Example.com" not in caplog.text, "e-mail e PII: fora do log"

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT token_hash, used_at, created_ip FROM password_reset_tokens")
            (linha,) = cur.fetchall()
    assert linha["token_hash"] != token and len(linha["token_hash"]) == 64
    assert linha["used_at"] is not None
    assert linha["created_ip"]
    assert any('"event": "password_reset.confirmed"' in r.getMessage() for r in caplog.records)
