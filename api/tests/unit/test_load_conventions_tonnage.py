"""M58: regra UNICA de tonelagem por convencao de carga
(`app/load_conventions.py`), usada pelo relatorio Markdown e pelo dashboard.

Convencao do dominio: a carga e gravada SEMPRE como total. NULL/`total` (e
qualquer outra convencao que carrega o peso movido) entram na tonelagem;
`assisted` e `bodyweight` ficam fora.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.intelligence.report import daily_breakdown as daily
from app.intelligence.report import markdown_report_service as svc
from app.load_conventions import (
    TONNAGE_EXCLUDED_CONVENTIONS,
    LoadConvention,
    counts_in_tonnage,
    equipment_exercises_join_sql,
    format_tonnage,
    sql_counts_in_tonnage,
)
from app.domain.set_measures import sql_is_rep_based


def test_conjunto_excluido_e_o_do_dominio():
    assert TONNAGE_EXCLUDED_CONVENTIONS == {LoadConvention.ASSISTED, LoadConvention.BODYWEIGHT}


def test_relatorio_e_detalhamento_usam_o_mesmo_objeto():
    """Uma regra so: os nomes antigos sao o MESMO conjunto, nao uma copia."""
    assert svc.TONNAGE_EXCLUDED_CONVENTIONS is TONNAGE_EXCLUDED_CONVENTIONS
    assert daily.EXCLUDED_CONVENTIONS is TONNAGE_EXCLUDED_CONVENTIONS


@pytest.mark.parametrize("convention", list(LoadConvention))
def test_counts_in_tonnage(convention):
    assert counts_in_tonnage(convention) is (convention not in TONNAGE_EXCLUDED_CONVENTIONS)


@pytest.mark.parametrize(
    "valor,conta",
    [
        (None, True),
        ("total", True),
        ("per_side", True),
        ("per_dumbbell", True),
        ("bodyweight_plus", True),
        ("assisted", False),
        ("bodyweight", False),
        ("", True),  # ENUM invalido vira '' no MySQL; Python o le como total
    ],
)
def test_predicado_sql_espelha_o_python(valor, conta):
    """O predicado roda num banco de verdade (SQLite em memoria; so usa
    IS NULL / NOT IN, iguais no MySQL) e da a mesma resposta que o Python."""
    conn = sqlite3.connect(":memory:")
    try:
        # Lote T3: a tabela ganha a medida da serie (089); `reps` = como antes.
        conn.execute("CREATE TABLE eqex (load_convention TEXT, measure TEXT NOT NULL DEFAULT 'reps')")
        conn.execute("INSERT INTO eqex (load_convention) VALUES (?)", (valor,))
        predicado = sql_counts_in_tonnage('eqex.load_convention', measure='eqex.measure')
        (resultado,) = conn.execute(f"SELECT CASE WHEN {predicado} THEN 1 ELSE 0 END FROM eqex").fetchone()
    finally:
        conn.close()
    assert bool(resultado) is conta


def test_sem_coluna_045_o_predicado_e_sempre_verdadeiro():
    # Lote T3: sem a 045 sobra so o predicado da medida (serie de reps).
    rep_based = sql_is_rep_based("es.measure")
    assert sql_counts_in_tonnage("NULL") == rep_based
    assert sql_counts_in_tonnage(" null ") == rep_based


def test_predicado_e_uma_expressao_so():
    sql = sql_counts_in_tonnage("eqex.load_convention")
    assert ";" not in sql
    assert sql == (
        "((eqex.load_convention IS NULL OR eqex.load_convention NOT IN ('assisted', 'bodyweight'))"
        " AND (es.measure IS NULL OR es.measure = 'reps'))"
    )


def test_join_usa_o_equipamento_canonico_e_o_par_ativo():
    sql = equipment_exercises_join_sql(canonical_expr="COALESCE(elm.canonical_exercise_id, e.mapping_id)")
    assert sql.startswith("LEFT JOIN equipment_exercises eqex")
    assert "eqex.equipment_id = ge.equipment_id" in sql
    assert "eqex.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)" in sql
    assert "eqex.is_active = 1" in sql


# ---------------------------------------------------------------------------
# Exibicao da tonelagem (2026-10-10): pronta em kg para tela e prompt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor, esperado",
    [
        (16285.0, "16.285 kg"),
        ("12084.56", "12.085 kg"),
        (999.5, "1.000 kg"),
        (1234567.4, "1.234.567 kg"),
        (0, "0 kg"),
        (None, "0 kg"),
        ("x", "0 kg"),
        (float("nan"), "0 kg"),
    ],
)
def test_format_tonnage_kg_inteiro_com_milhar_pt_br(valor, esperado):
    assert format_tonnage(valor) == esperado


def test_prompt_do_resumo_de_treino_recebe_a_tonelagem_pronta():
    """O eval de 2026-10-10 viu "16,3 toneladas" a partir de `16285.00 kg`
    cru: o prompt passa a citar o valor ja formatado pelo dono e proibe
    converter unidade."""
    from app.workout_summary_ai import build_prompt

    prompt = build_prompt({"id": 1, "total_volume_kg": 16285.0, "exercises": []})
    # Rotulo e "nao converta unidades" (prosa do template): privado, M103 P2
    # (`api/privado/tests/unit/test_workout_summary_prompt.py`).
    assert f": {format_tonnage(16285.0)}\n" in prompt
    assert "16285" not in prompt
