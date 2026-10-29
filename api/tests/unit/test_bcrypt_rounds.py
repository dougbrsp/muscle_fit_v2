"""Guarda do custo do bcrypt (M38).

`app.auth.hash_password` passou a ler `BCRYPT_ROUNDS` do ambiente para que o
processo de TESTE possa hashear barato (`tests/conftest.py` poe 4). O risco que
essa flexibilidade cria e exatamente um: o custo de PRODUCAO cair junto, por
engano - num default reduzido "porque o CI estava lento", ou numa env
`BCRYPT_ROUNDS` vazando para uma Lambda.

Este arquivo fecha os dois caminhos:

1. o DEFAULT, sem env nenhuma, continua 12 - e o hash gerado sem env carrega
   o `$2b$12$` no proprio prefixo (prova no artefato, nao na constante);
2. nenhum arquivo de INFRA/ambiente define `BCRYPT_ROUNDS` - se um PR futuro
   colocar essa env num `infra/*.tf`, no `docker-compose.yml` ou no
   `.env.example`, este teste reprova e obriga a decisao a ser explicita.

Nada aqui toca banco: e unitario de proposito, roda no job `backend unit`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import auth

REPO_ROOT = Path(__file__).resolve().parents[3]

# Onde uma env de runtime pode ser definida para a aplicacao de verdade.
ARQUIVOS_DE_AMBIENTE: tuple[Path, ...] = (
    *sorted((REPO_ROOT / "infra").glob("*.tf")),
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / ".env.example",
)


def test_default_de_producao_continua_12(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `BCRYPT_ROUNDS` no ambiente, o custo e o mesmo de sempre."""
    monkeypatch.delenv(auth.BCRYPT_ROUNDS_ENV, raising=False)
    assert auth.DEFAULT_BCRYPT_ROUNDS == 12
    assert auth.bcrypt_rounds() == 12


def test_hash_sem_env_carrega_o_custo_12_no_proprio_prefixo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prova no artefato: o hash produzido sem env comeca com `$2b$12$`.

    Um default reduzido por engano muda ESTE prefixo, mesmo que a constante
    acima seja "ajustada" junto.
    """
    monkeypatch.delenv(auth.BCRYPT_ROUNDS_ENV, raising=False)
    assert auth.hash_password("senha-de-referencia").startswith("$2b$12$")


def test_o_processo_de_teste_hasheia_barato() -> None:
    """A outra ponta: `tests/conftest.py` colocou um custo baixo, e e ELE que
    vale aqui. Se esta linha cair, o CI voltou a pagar ~0,3 s por hash."""
    assert auth.bcrypt_rounds() <= 6
    assert auth.hash_password("senha-de-teste").startswith("$2b$0")


@pytest.mark.parametrize("valor", ["", "  ", "abc", "3", "32", "-1", "12.5"])
def test_valor_ausente_invalido_ou_fora_da_faixa_cai_no_default(
    monkeypatch: pytest.MonkeyPatch, valor: str
) -> None:
    monkeypatch.setenv(auth.BCRYPT_ROUNDS_ENV, valor)
    assert auth.bcrypt_rounds() == auth.DEFAULT_BCRYPT_ROUNDS


@pytest.mark.parametrize("valor", ["4", "10", "12", "31"])
def test_valor_valido_e_respeitado(monkeypatch: pytest.MonkeyPatch, valor: str) -> None:
    monkeypatch.setenv(auth.BCRYPT_ROUNDS_ENV, valor)
    assert auth.bcrypt_rounds() == int(valor)


def test_verificacao_funciona_entre_custos_diferentes(monkeypatch: pytest.MonkeyPatch) -> None:
    """O custo entra no hash, entao um hash gravado com 12 continua sendo
    verificado por um processo que hasheia com 4 (e vice-versa). E o que
    garante que baixar o custo no CI nao inventa um comportamento que producao
    nao tem."""
    monkeypatch.setenv(auth.BCRYPT_ROUNDS_ENV, "12")
    hash_caro = auth.hash_password("mesma-senha")
    monkeypatch.setenv(auth.BCRYPT_ROUNDS_ENV, "4")
    hash_barato = auth.hash_password("mesma-senha")

    assert auth.verify_password("mesma-senha", hash_caro)
    assert auth.verify_password("mesma-senha", hash_barato)
    assert not auth.verify_password("outra-senha", hash_caro)
    assert not auth.verify_password("outra-senha", hash_barato)


def test_nenhum_arquivo_de_infra_ou_ambiente_define_bcrypt_rounds() -> None:
    """Impacto em producao = nenhum, verificado no repo e nao na promessa.

    `BCRYPT_ROUNDS` so pode existir dentro do processo de teste. Um PR que a
    coloque numa Lambda, no compose ou no `.env.example` reprova aqui."""
    padrao = re.compile(r"\bBCRYPT_ROUNDS\b")
    culpados = [
        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        for path in ARQUIVOS_DE_AMBIENTE
        if path.exists() and padrao.search(path.read_text(encoding="utf-8"))
    ]
    assert culpados == [], (
        "BCRYPT_ROUNDS definida fora do processo de teste - producao mudaria de custo: "
        f"{culpados}"
    )


def test_a_lista_de_arquivos_vigiados_nao_esta_vazia() -> None:
    """Guarda da guarda: se o layout do repo mudar e os globs pararem de casar,
    o teste acima passaria a vigiar NADA em silencio."""
    existentes = [p for p in ARQUIVOS_DE_AMBIENTE if p.exists()]
    assert len(existentes) >= 5
    assert any(p.suffix == ".tf" for p in existentes)
