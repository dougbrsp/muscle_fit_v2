"""Criptografia simetrica (Fernet) para segredos gravados em repouso no MySQL.

Uso atual (Parte 2 da migracao multiusuario): `users.intervals_api_key_encrypted`
- a chave de API do Intervals.icu de cada usuario nunca e gravada em texto
puro; `app/users.py` chama `encrypt_secret` antes de persistir e
`app/intervals_integration.py` chama `decrypt_secret` sempre que precisa
falar de verdade com o Intervals.icu em nome de um usuario.

A chave simetrica (`SECRET_ENCRYPTION_KEY`) vem de variavel de ambiente,
nunca de codigo/config versionado. Mesmo padrao "lazy" ja usado no resto do
projeto (ex.: `app/db.py`, que so le `os.environ` dentro de funcao, nunca no
import do modulo) - assim importar este modulo nunca falha (o que quebraria
a coleta de testes/outros modulos que soh usam funcoes nao relacionadas a
Intervals), mas a PRIMEIRA tentativa real de criptografar/descriptografar
sem a env var configurada falha alto e claro, nunca silenciosamente.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

_ENV_VAR = "SECRET_ENCRYPTION_KEY"

_fernet_instance: Fernet | None = None
_fernet_key_used: str | None = None


def _get_fernet() -> Fernet:
    global _fernet_instance, _fernet_key_used

    key = os.environ.get(_ENV_VAR, "")
    if not key:
        raise RuntimeError(
            f"{_ENV_VAR} nao configurada no servidor. Gere uma chave com "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` e defina "
            f"{_ENV_VAR} no .env da Fitness API antes de gravar/ler segredos "
            "(ex.: chave do Intervals.icu)."
        )

    # Recria a instancia apenas se a chave mudou (permite trocar a env var em
    # testes via monkeypatch/patch.dict sem reiniciar o processo).
    if _fernet_instance is None or _fernet_key_used != key:
        try:
            _fernet_instance = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except Exception as exc:
            raise RuntimeError(
                f"{_ENV_VAR} invalida: nao e uma chave Fernet valida (deve ser "
                "uma string base64 urlsafe de 32 bytes, gerada por "
                "Fernet.generate_key())."
            ) from exc
        _fernet_key_used = key

    return _fernet_instance


def encrypt_secret(raw: str) -> str:
    """Criptografa `raw` (ex.: API key em texto puro) e devolve um token
    Fernet (string, seguro para gravar em coluna VARCHAR)."""
    if raw is None:
        raise ValueError("encrypt_secret: valor não pode ser None.")

    fernet = _get_fernet()
    return fernet.encrypt(raw.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted: str) -> str:
    """Descriptografa um token Fernet gravado por `encrypt_secret`, devolvendo
    o segredo em texto puro. Levanta `ValueError` se `encrypted` não for um
    token Fernet válido (ex.: chave errada, dado corrompido, ou texto puro
    de antes da criptografia real existir - ver migracao defensiva em
    `app/users.py::_migrate_plaintext_intervals_keys`)."""
    if not encrypted:
        raise ValueError("decrypt_secret: valor vazio.")

    fernet = _get_fernet()
    try:
        return fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("decrypt_secret: token Fernet inválido (chave errada ou dado não criptografado).") from exc


def is_encrypted(value: str) -> bool:
    """Tenta descriptografar `value` só para checar se está no formato
    Fernet, sem levantar exceção. Usado pela migração defensiva que detecta
    linhas antigas gravadas em texto puro (antes desta entrega)."""
    if not value:
        return False
    try:
        decrypt_secret(value)
        return True
    except ValueError:
        return False
