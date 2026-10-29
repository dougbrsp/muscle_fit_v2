"""Testes do rewriter puro do seed local (api/scripts/seed_sql_rewrite.py).

Nenhum dado real: todos os valores abaixo sao ficticios.
"""

from __future__ import annotations

import pytest

from scripts.seed_sql_rewrite import (
    SqlTokenizeError,
    parse_insert,
    rewrite_insert_for_schema,
    split_statements,
)

SCHEMA = {
    "gyms": {"id", "name", "notes", "is_active"},
    "equipment": {"id", "name", "notes", "photo_path", "is_active"},
}


def test_nenhuma_coluna_descartada_devolve_statement_original():
    sql = "INSERT INTO `gyms` (`id`, `name`) VALUES (1, 'Academia A')"
    result = rewrite_insert_for_schema(sql, SCHEMA)
    assert result.statement is sql
    assert result.table == "gyms"
    assert result.dropped_columns == ()
    assert result.table_missing is False


def test_tabela_inexistente_e_pulada():
    sql = "INSERT INTO `equipment_types` (`id`, `code`) VALUES (1, 'x')"
    result = rewrite_insert_for_schema(sql, SCHEMA)
    assert result.statement is None
    assert result.table == "equipment_types"
    assert result.table_missing is True


@pytest.mark.parametrize(
    ("columns", "values", "expected"),
    [
        # coluna descartada no inicio
        (
            "`color_palette`, `id`, `name`",
            "('#fff,#000', 1, 'A')",
            "INSERT INTO `gyms` (`id`, `name`) VALUES (1, 'A')",
        ),
        # no meio
        (
            "`id`, `color_palette`, `name`",
            "(1, '{\"a\": [1, 2]}', 'A')",
            "INSERT INTO `gyms` (`id`, `name`) VALUES (1, 'A')",
        ),
        # no fim
        (
            "`id`, `name`, `color_palette`",
            "(1, 'A', NULL)",
            "INSERT INTO `gyms` (`id`, `name`) VALUES (1, 'A')",
        ),
    ],
)
def test_descarta_coluna_no_inicio_meio_fim(columns, values, expected):
    sql = f"INSERT INTO `gyms` ({columns}) VALUES {values}"
    result = rewrite_insert_for_schema(sql, SCHEMA)
    assert result.statement == expected
    assert result.dropped_columns == ("color_palette",)


def test_multiplas_tuplas_e_multiplas_colunas_descartadas():
    sql = (
        "INSERT INTO `equipment` (`id`, `name`, `equipment_type_id`, `notes`, `manufacturer`, `model`) "
        "VALUES (1, 'Leg press', 3, NULL, 'Marca, Ltda', 'X (v2)'),\n"
        "(2, 'Supino', NULL, 'obs; com ponto e virgula', NULL, '')"
    )
    result = rewrite_insert_for_schema(sql, SCHEMA)
    assert result.dropped_columns == ("equipment_type_id", "manufacturer", "model")
    assert result.statement == (
        "INSERT INTO `equipment` (`id`, `name`, `notes`) VALUES "
        "(1, 'Leg press', NULL), (2, 'Supino', 'obs; com ponto e virgula')"
    )


def test_strings_com_escapes_virgulas_parenteses_e_json():
    tricky = [
        r"'it\'s, (tricky)'",  # aspa escapada com barra + virgula + parenteses
        "'it''s ) , ('",  # aspa dobrada
        r"'barra no fim \\'",  # barra escapada imediatamente antes do fechamento
        r"'{\"k\": \"v, (x)\", \"l\": [1, 2]}'",  # JSON com aspas escapadas
        "'linha1\\nlinha2'",
        "_binary 'ab,c'",
        "0x1F2E",
        "-12.5e3",
        "NULL",
        "''",
        "CONVERT('a,b' USING utf8mb4)",
    ]
    columns = [f"`c{i}`" for i in range(len(tricky))] + ["`drop_me`"]
    sql = (
        f"INSERT INTO `t` ({', '.join(columns)}) VALUES "
        f"({', '.join(tricky)}, 'x'), ({', '.join(tricky)}, NULL)"
    )
    parsed = parse_insert(sql)
    assert parsed is not None
    assert parsed.rows == (tuple(tricky) + ("'x'",), tuple(tricky) + ("NULL",))

    schema = {"t": {f"c{i}" for i in range(len(tricky))}}
    result = rewrite_insert_for_schema(sql, schema)
    assert result.dropped_columns == ("drop_me",)
    reparsed = parse_insert(result.statement)
    assert reparsed.rows == (tuple(tricky), tuple(tricky))


def test_comparacao_de_colunas_case_insensitive_e_sem_crases():
    sql = "insert into gyms (ID, Name, Color_Palette) values (1, 'A', 'p')"
    result = rewrite_insert_for_schema(sql, SCHEMA)
    assert result.dropped_columns == ("Color_Palette",)
    assert result.statement == "INSERT INTO `gyms` (`ID`, `Name`) VALUES (1, 'A')"


def test_todas_as_colunas_inexistentes_nao_gera_statement():
    sql = "INSERT INTO `gyms` (`color_palette`) VALUES ('p')"
    result = rewrite_insert_for_schema(sql, SCHEMA)
    assert result.statement is None
    assert result.table_missing is False
    assert result.dropped_columns == ("color_palette",)


def test_insert_ignore_opcional():
    sql = "INSERT INTO `gyms` (`id`, `name`) VALUES (1, 'A')"
    result = rewrite_insert_for_schema(sql, SCHEMA, insert_ignore=True)
    assert result.statement == "INSERT IGNORE INTO `gyms` (`id`, `name`) VALUES (1, 'A')"
    already = "INSERT IGNORE INTO `gyms` (`id`) VALUES (1)"
    assert rewrite_insert_for_schema(already, SCHEMA, insert_ignore=True).statement is already


def test_statements_nao_insert_passam_inalterados():
    for sql in ("SET FOREIGN_KEY_CHECKS = 0", "DELETE FROM `gyms`", "COMMIT"):
        result = rewrite_insert_for_schema(sql, SCHEMA)
        assert result.statement is sql
        assert result.table is None


def test_insert_com_sufixo_nao_suportado_passa_inalterado():
    sql = "INSERT INTO `gyms` (`id`, `x`) VALUES (1, 2) ON DUPLICATE KEY UPDATE `id` = 1"
    result = rewrite_insert_for_schema(sql, SCHEMA)
    assert result.statement is sql
    assert result.table == "gyms"


def test_tupla_com_numero_de_valores_errado_levanta_erro():
    with pytest.raises(ValueError):
        parse_insert("INSERT INTO `gyms` (`id`, `name`) VALUES (1)")


def test_split_statements_respeita_strings_e_remove_comentarios():
    script = (
        "-- cabecalho; com ponto e virgula\n"
        "SET FOREIGN_KEY_CHECKS = 0;\n"
        "INSERT INTO `gyms` (`id`, `notes`) VALUES (1, 'a;\nb -- nao e comentario');\n"
        "/* bloco; */ DELETE FROM `gyms`;\n"
        "# outro comentario\n"
        "COMMIT;\n"
    )
    assert split_statements(script) == [
        "SET FOREIGN_KEY_CHECKS = 0",
        "INSERT INTO `gyms` (`id`, `notes`) VALUES (1, 'a;\nb -- nao e comentario')",
        "DELETE FROM `gyms`",
        "COMMIT",
    ]


def test_string_nao_terminada_levanta_erro_sem_conteudo():
    with pytest.raises(SqlTokenizeError) as exc_info:
        split_statements("INSERT INTO `gyms` (`id`) VALUES ('segredo")
    assert "segredo" not in str(exc_info.value)
