"""`app/crypto_utils.py` (Fernet) - segredos gravados em repouso no MySQL
(`users.intervals_api_key_encrypted`).

O ponto central e a COMPATIBILIDADE COM O QUE JA ESTA GRAVADO: `TOKEN_LEGADO`
foi cifrado com cryptography 44.0.0 (versao de producao antes do SEG-13) e
precisa continuar legivel em qualquer versao futura da biblioteca - se este
teste quebrar numa atualizacao, as chaves do Intervals.icu de producao
deixariam de ser decifradas.

Rodar: cd api && python -m pytest tests/unit/test_crypto_utils.py
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app import crypto_utils

# Chave de TESTE (nunca usada fora daqui) e token gerado com ela por
# `encrypt_secret` sob cryptography 44.0.0, em 2026-10-09.
CHAVE_TESTE = "q3Zf0P8bV2m1oWkzJx7cR5yT9uLhG4sNdA6eB0iKfQo="
SEGREDO = "intervals-api-key-0123456789abcdef"
TOKEN_LEGADO = (
    "gAAAAABqyQY9OHFT92EUCHsRQh3MkFiAW8zyzcQaDHODbm-S4xu_BzhlHXrsOMTCCxquL6YaBkpMMZ9Ooorugdk5oa9kQ78Fg7aD1V15QCp-"
    "Aa-qk0EOX1B8WMW3xVZpux2X1D05rtwE"
)


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", CHAVE_TESTE)


def test_token_gravado_pela_versao_anterior_continua_legivel():
    assert crypto_utils.decrypt_secret(TOKEN_LEGADO) == SEGREDO
    assert crypto_utils.is_encrypted(TOKEN_LEGADO)


def test_round_trip_na_versao_atual():
    token = crypto_utils.encrypt_secret(SEGREDO)
    assert token != SEGREDO
    assert crypto_utils.decrypt_secret(token) == SEGREDO


def test_chave_errada_vira_value_error(monkeypatch):
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        crypto_utils.decrypt_secret(TOKEN_LEGADO)
    assert not crypto_utils.is_encrypted(TOKEN_LEGADO)


def test_texto_puro_nao_e_token():
    assert not crypto_utils.is_encrypted(SEGREDO)


def test_sem_chave_falha_alto(monkeypatch):
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError):
        crypto_utils.encrypt_secret(SEGREDO)
