"""Compatibilidade do bcrypt 5 com senhas longas (SEG-16).

O bcrypt so usa os 72 primeiros bytes da senha. Ate o bcrypt 4.2.1 a lib
truncava em silencio; o 5.0 lanca ValueError. A API aceita ate 256 caracteres,
entao `app.auth` trunca nos mesmos 72 bytes antes de chamar a lib.

Os hashes abaixo foram gerados com bcrypt==4.2.1 (a versao que estava em
producao), custo 4, para provar que quem ja tem senha cadastrada continua
entrando depois da troca de versao.
"""

from __future__ import annotations

import bcrypt

from app import auth

# 111 bytes em UTF-8; o corte de 72 bytes cai NO MEIO de um "ç" (2 bytes),
# exatamente como o bcrypt 4 cortava.
SENHA_LONGA = "Senh@Longa-" + "ç" * 40 + "x" * 20
HASH_LONGA_BCRYPT_4 = "$2b$04$IjYwNpKdTc7WsVR8/6xtBOdiA8eWMeAfn.2qgujtTtLPF6Rst/BMG"
SENHA_CURTA = "Curta#2026"
HASH_CURTA_BCRYPT_4 = "$2b$04$VGmh/9xtzmtGKlvoSf5i9OQNl7MgbKVWS9XmVszypeuXUq4SVYvoy"


def test_fixture_tem_mais_de_72_bytes() -> None:
    assert len(SENHA_LONGA.encode("utf-8")) == 111 > auth.BCRYPT_MAX_PASSWORD_BYTES


def test_hash_de_senha_longa_nao_lanca_e_verifica() -> None:
    h = auth.hash_password(SENHA_LONGA)
    assert auth.verify_password(SENHA_LONGA, h)
    assert not auth.verify_password("outra senha qualquer", h)


def test_senha_no_limite_de_256_caracteres() -> None:
    senha = "á" * 256  # 512 bytes, o maximo aceito pelo request_models
    h = auth.hash_password(senha)
    assert auth.verify_password(senha, h)


def test_hash_antigo_de_senha_longa_continua_valido() -> None:
    assert auth.verify_password(SENHA_LONGA, HASH_LONGA_BCRYPT_4)


def test_hash_antigo_de_senha_curta_continua_valido() -> None:
    assert auth.verify_password(SENHA_CURTA, HASH_CURTA_BCRYPT_4)
    assert not auth.verify_password(SENHA_CURTA + "!", HASH_CURTA_BCRYPT_4)


def test_semantica_de_truncamento_igual_ao_bcrypt_4() -> None:
    """Igual ao bcrypt 4: so os 72 primeiros bytes contam (sem regressao nem
    endurecimento silencioso de quem ja tinha senha longa)."""
    mesmos_72 = SENHA_LONGA[:-5] + "zzzzz"
    assert auth.verify_password(mesmos_72, HASH_LONGA_BCRYPT_4)


def test_hash_novo_e_bcrypt_padrao() -> None:
    h = auth.hash_password(SENHA_CURTA)
    assert h.startswith("$2b$")
    assert bcrypt.checkpw(SENHA_CURTA.encode("utf-8"), h.encode("utf-8"))


def test_hash_invalido_continua_retornando_false() -> None:
    assert not auth.verify_password(SENHA_CURTA, "nao-e-um-hash")
