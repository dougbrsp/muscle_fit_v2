"""Filtro de selecao dos schemas descartaveis - CODIGO DE SEGURANCA.

Um falso positivo aqui apaga o banco de desenvolvimento (`fitness`) ou o de
producao (`musclefit`). Estes testes existem para que qualquer mexida no
regex/denylist quebre alto e cedo.
"""

from __future__ import annotations

import pytest

from tests.support import schema_lifecycle as sl

STEM = "musclefit"

# Nomes que a suite realmente cria (fixtures de tests/integration/**).
DESCARTAVEIS = [
    "musclefit_test",
    "musclefit_fk023_test",
    "musclefit_fk023_dataapi_test",
    "musclefit_p028_test",
    "musclefit_r029_test",
    "musclefit_grupob027_test",
    "musclefit_run0123456789_test",
    "musclefit_run0123456789_p038_test",
    # Schemas criados por quem roda a suite com `TEST_MYSQL_DATABASE` proprio
    # (ex.: `TEST_MYSQL_DATABASE=musclefit_divida_test`): a varredura da
    # execucao default (stem `musclefit`) TEM que alcancar esses tambem.
    "musclefit_divida_test",
    "musclefit_divida_aliases_test",
    "musclefit_pr18_test",
]

# Nomes legitimos, parecidos de proposito. Nenhum pode ser selecionado.
INTOCAVEIS = [
    "fitness",  # banco do DEV local
    "musclefit",  # banco de producao
    "mysql",
    "information_schema",
    "performance_schema",
    "sys",
    "musclefit_prod",
    "musclefit_prod_backup",
    "fitness_test_backup",
    "fitness_2026",
    "musclefittest",
    "musclefit_testes",
    "musclefit_test_backup",
    "xmusclefit_test",
    "backup_musclefit_test",
    "musclefit-test",
]


@pytest.mark.parametrize("name", DESCARTAVEIS)
def test_schema_de_teste_e_selecionado(name: str) -> None:
    assert sl.is_disposable_test_schema(name, stem=STEM) is True


@pytest.mark.parametrize("name", INTOCAVEIS)
def test_schema_legitimo_nunca_e_selecionado(name: str) -> None:
    assert sl.is_disposable_test_schema(name, stem=STEM) is False


def test_denylist_vence_ate_se_o_stem_for_o_proprio_banco_de_dev() -> None:
    """Mesmo com TEST_MYSQL_DATABASE=fitness_test, `fitness` esta protegido."""
    assert sl.is_disposable_test_schema("fitness", stem="fitness") is False
    assert sl.is_disposable_test_schema("fitness_test", stem="fitness") is True


def test_drop_schemas_recusa_nome_fora_do_padrao() -> None:
    conn = _FakeConn(["fitness"])
    with pytest.raises(ValueError, match="fora do padrao"):
        sl.drop_schemas(conn, ["fitness"], stem=STEM)
    assert conn.dropped == []


def test_nome_de_schema_e_unico_por_execucao() -> None:
    token = sl.new_run_token()
    database = sl.run_scoped_database("musclefit_test", token)
    assert database == f"musclefit_{token}_test"
    assert sl.is_disposable_test_schema(database, stem=STEM)
    assert sl.run_token_of(database, stem=STEM) == token
    # ... e se propaga para os schemas auxiliares derivados pelas fixtures.
    derivado = f"{database[: -len('_test')]}_p028_test"
    assert sl.run_token_of(derivado, stem=STEM) == token
    assert sl.new_run_token() != token


def test_schema_legado_nao_tem_token_de_execucao() -> None:
    assert sl.run_token_of("musclefit_fk023_test", stem=STEM) is None
    assert sl.run_token_of("musclefit_test", stem=STEM) is None


def test_run_scoped_database_recusa_token_arbitrario() -> None:
    with pytest.raises(ValueError):
        sl.run_scoped_database("musclefit_test", "../fitness")


# ---------------------------------------------------------------------------
# Varredura de orfaos contra um MySQL de mentira (sem banco real).
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn
        self._rows: list[tuple] = []

    def execute(self, sql: str, args=None) -> None:
        text = " ".join(sql.split())
        if text.upper().startswith("SHOW DATABASES"):
            self._rows = [(name,) for name in self._conn.schemas]
        elif "information_schema.processlist" in text:
            self._rows = [(1,)] if args[0] in self._conn.em_uso else []
        elif "IS_USED_LOCK" in text.upper():
            held = args[0] in self._conn.locks
            self._rows = [(42 if held else None,)]
        elif "GET_LOCK" in text.upper():
            self._conn.locks.add(args[0])
            self._rows = [(1,)]
        elif text.upper().startswith("DROP DATABASE"):
            name = text.split("`")[1]
            self._conn.dropped.append(name)
            self._conn.schemas = [s for s in self._conn.schemas if s != name]
        else:  # pragma: no cover - comando inesperado e bug do teste
            raise AssertionError(f"SQL inesperado: {text}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc) -> None:
        return None


class _FakeConn:
    """MySQL de mentira: SHOW DATABASES, DROP DATABASE e locks nomeados."""

    def __init__(
        self,
        schemas: list[str],
        locks: set[str] | None = None,
        em_uso: set[str] | None = None,
    ) -> None:
        self.schemas = list(schemas)
        self.locks: set[str] = set(locks or ())
        self.em_uso: set[str] = set(em_uso or ())
        self.dropped: list[str] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)


def test_varredura_derruba_orfaos_e_preserva_bancos_de_verdade() -> None:
    schemas = INTOCAVEIS + DESCARTAVEIS
    conn = _FakeConn(schemas)

    dropped = sl.sweep_orphan_schemas(conn, stem=STEM, keep_token=None)

    assert sorted(dropped) == sorted(DESCARTAVEIS)
    assert "fitness" not in dropped
    assert "musclefit" not in dropped
    for name in INTOCAVEIS:
        assert name in conn.schemas


def test_varredura_preserva_os_schemas_da_execucao_corrente() -> None:
    token = "run0123456789"
    meus = [f"musclefit_{token}_test", f"musclefit_{token}_p028_test"]
    conn = _FakeConn(["fitness", "musclefit_fk023_test", *meus])

    dropped = sl.sweep_orphan_schemas(conn, stem=STEM, keep_token=token)

    assert dropped == ["musclefit_fk023_test"]
    for name in meus:
        assert name in conn.schemas


def test_varredura_nao_derruba_schema_de_execucao_paralela_viva() -> None:
    """Outra suite, rodando agora, ainda segura o lock nomeado do token dela."""
    outro_token = "runabcdef0123"
    outra = f"musclefit_{outro_token}_p028_test"
    conn = _FakeConn(
        ["fitness", outra, "musclefit_fk023_test"],
        locks={sl.run_lock_name(outro_token)},
    )

    dropped = sl.sweep_orphan_schemas(conn, stem=STEM, keep_token="run9999999999")

    assert dropped == ["musclefit_fk023_test"]
    assert outra in conn.schemas


def test_varredura_derruba_schema_de_execucao_que_morreu_sem_teardown() -> None:
    """Sem lock segurado (processo morto a kill -9), o schema e orfao."""
    morta = "musclefit_runabcdef0123_p028_test"
    conn = _FakeConn(["fitness", morta])

    assert sl.sweep_orphan_schemas(conn, stem=STEM, keep_token="run9999999999") == [morta]
    assert conn.schemas == ["fitness"]


def test_lock_de_execucao_marca_a_suite_como_viva() -> None:
    conn = _FakeConn([])
    token = sl.new_run_token()
    assert sl.run_is_alive(conn, token) is False
    assert sl.acquire_run_lock(conn, token) is True
    assert sl.run_is_alive(conn, token) is True


def test_drop_run_schemas_so_pega_os_da_execucao() -> None:
    token = "run0123456789"
    conn = _FakeConn(
        [
            "fitness",
            "musclefit",
            "musclefit_fk023_test",
            f"musclefit_{token}_test",
            f"musclefit_{token}_p028_test",
            "musclefit_runabcdef0123_test",
        ]
    )

    dropped = sl.drop_run_schemas(conn, stem=STEM, token=token)

    assert sorted(dropped) == [f"musclefit_{token}_p028_test", f"musclefit_{token}_test"]
    assert "fitness" in conn.schemas
    assert "musclefit_fk023_test" in conn.schemas
    assert "musclefit_runabcdef0123_test" in conn.schemas


def test_varredura_poupa_schema_legado_com_conexao_aberta() -> None:
    """Schema legado (sem token) em uso agora: provavelmente uma suite antiga
    rodando em paralelo, nao um orfao."""
    conn = _FakeConn(
        ["fitness", "musclefit_test", "musclefit_fk023_test"],
        em_uso={"musclefit_test"},
    )

    dropped = sl.sweep_orphan_schemas(conn, stem=STEM, keep_token=None)

    assert dropped == ["musclefit_fk023_test"]
    assert "musclefit_test" in conn.schemas


def test_protecao_e_por_igualdade_exata_nao_por_prefixo() -> None:
    """`musclefit` (producao) e `fitness` (DEV) sao protegidos por igualdade
    exata. Se a protecao fosse por prefixo, `musclefit_divida_test` e
    `musclefit_pr18_test` - schemas de teste legitimos, criados por quem roda a
    suite com `TEST_MYSQL_DATABASE` proprio - nunca seriam varridos, e o
    acumulo continuaria."""
    assert "musclefit" in sl.PROTECTED_SCHEMAS
    assert "fitness" in sl.PROTECTED_SCHEMAS
    for descartavel in ("musclefit_divida_test", "musclefit_divida_aliases_test", "musclefit_pr18_test"):
        assert descartavel not in sl.PROTECTED_SCHEMAS
        assert sl.is_disposable_test_schema(descartavel, stem=STEM) is True
    # ... e o inverso: prefixo `musclefit` sem sufixo `_test` nunca e alvo.
    for intocavel in ("musclefit", "musclefit_prod", "musclefit_divida", "fitness"):
        assert sl.is_disposable_test_schema(intocavel, stem=STEM) is False


def test_varredura_alcanca_schema_de_outro_TEST_MYSQL_DATABASE() -> None:
    """Cenario real: dois agentes rodando a suite na mesma instancia, um com
    `TEST_MYSQL_DATABASE=musclefit_divida_test`. Terminada aquela execucao (sem
    conexao aberta e sem lock), a varredura da execucao default recolhe os
    schemas dela."""
    conn = _FakeConn(
        ["fitness", "musclefit", "musclefit_divida_test", "musclefit_divida_aliases_test"]
    )

    dropped = sl.sweep_orphan_schemas(conn, stem=STEM, keep_token="run0123456789")

    assert sorted(dropped) == ["musclefit_divida_aliases_test", "musclefit_divida_test"]
    assert sorted(conn.schemas) == ["fitness", "musclefit"]
