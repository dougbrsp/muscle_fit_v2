"""Regra de contabilização de técnicas avançadas (Fase 5, PR 2) - tabela."""

from __future__ import annotations

import pytest

from app.domain import technique_accounting as ta
from app.domain.technique_accounting import TechniqueApplication as App

# Catálogo como a migração 060 o deixa.
CATALOG = {
    "drop_set": {"activation_set_equivalent": 0.50, "max_equivalent_per_anchor": 1.00, "recovery_cost_factor": 1.15, "set_type": "drop_set"},
    "rest_pause": {"activation_set_equivalent": 0.40, "max_equivalent_per_anchor": 1.00, "recovery_cost_factor": 1.10, "set_type": "rest_pause"},
    "forced_reps": {"activation_set_equivalent": 0.25, "max_equivalent_per_anchor": 0.50, "recovery_cost_factor": 1.20, "set_type": "forced_rep"},
    "negative_reps": {"activation_set_equivalent": 0.25, "max_equivalent_per_anchor": 0.50, "recovery_cost_factor": 1.25, "set_type": "forced_rep"},
    "partial_reps": {"activation_set_equivalent": 0.15, "max_equivalent_per_anchor": 0.50, "recovery_cost_factor": 1.05, "set_type": "forced_rep"},
    "fst7": {"activation_set_equivalent": 0, "max_equivalent_per_anchor": 0, "recovery_cost_factor": 1.00, "default_sets_count": 7, "set_type": "working"},
    "gvt": {"activation_set_equivalent": 0, "max_equivalent_per_anchor": 0, "recovery_cost_factor": 1.00, "default_sets_count": 10, "set_type": "working"},
}

RP = "rest_pause"


def _rp(*reps, rpe=10):
    return [App(RP, reps=r, rpe=rpe) for r in reps]


@pytest.mark.parametrize(
    "caso, apps, anchor_reps, anchor_rpe, reps_target, esperado",
    [
        ("6 reps de uma série de 12 = meia série", _rp(6), 12, 10, None, 0.5),
        ("dois mini-blocos de 6 = uma série", _rp(6, 6), 12, 10, None, 1.0),
        ("6+4+3 = 1,08, o teto reduz a 1,0", _rp(6, 4, 3), 12, 10, None, 1.0),
        ("RPE baixo pondera pelos pontos de esforço", _rp(6, rpe=8), 12, 10, None, 0.5 * 0.60 / 1.05),
        ("RPE >= 9 vale esforço cheio mesmo abaixo da âncora", _rp(6, rpe=9), 12, 10, None, 0.5),
        ("sem âncora: topo da faixa prescrita", _rp(6), None, None, "10-12", 0.5),
        ("âncora sem reps: topo da faixa prescrita", _rp(3), 0, 10, "6", 0.5),
        ("reps forçadas: extras / ref × 1,0", [App("forced_reps", reps=3)], 12, 10, None, 0.25),
        ("parciais: peso 0,5 e teto 0,5", [App("partial_reps", reps=12)], 12, 10, None, 0.5),
        ("sem reps: coeficiente do catálogo × aplicações", [App("drop_set"), App("drop_set")], 12, 10, None, 1.0),
        ("sem referência nenhuma: coeficiente do catálogo", _rp(6), None, None, None, 0.4),
        ("FST-7 não soma equivalente", [App("fst7", reps=10)], 10, 10, None, 0.0),
        ("GVT não soma equivalente", [App("gvt", reps=10)], 10, 10, None, 0.0),
        ("código fora do catálogo vale 0", [App("myo_reps", reps=6)], 12, 10, None, 0.0),
    ],
)
def test_bloco_realizado(caso, apps, anchor_reps, anchor_rpe, reps_target, esperado):
    bloco = ta.realized_block_equivalent(
        apps, anchor_reps=anchor_reps, anchor_rpe=anchor_rpe, reps_target=reps_target, catalog=CATALOG
    )
    assert bloco.total == pytest.approx(esperado), caso


def test_teto_e_por_tecnica_na_mesma_ancora():
    bloco = ta.realized_block_equivalent(
        _rp(6, 6, 6) + [App("forced_reps", reps=12)], anchor_reps=12, anchor_rpe=10, catalog=CATALOG
    )
    assert bloco.by_technique == {RP: 1.0, "forced_reps": 0.5}
    assert bloco.total == pytest.approx(1.5)
    assert bloco.capped


def test_linha_nao_calibrada_usa_teto_padrao_de_uma_serie():
    catalogo = {RP: {"activation_set_equivalent": 0.4, "max_equivalent_per_anchor": None}}
    assert ta.realized_block_equivalent(_rp(12, 12), anchor_reps=12, catalog=catalogo).total == 1.0


@pytest.mark.parametrize(
    "code, aplicacoes, esperado",
    [(RP, 2, 0.8), (RP, 3, 1.0), ("drop_set", 1, 0.5), ("forced_reps", 4, 0.5), ("fst7", 7, 0.0), ("gvt", 10, 0.0), (None, 2, 0.0), (RP, 0, 0.0)],
)
def test_planejado(code, aplicacoes, esperado):
    assert ta.planned_equivalent(code, aplicacoes, CATALOG) == pytest.approx(esperado)


def test_recuperacao_da_tecnica_so_do_catalogo():
    """M76 B5: o fator de técnica é SÓ o do catálogo (060: forçadas 1,20); o
    modelo de recuperação não tem mais cópia própria (forced_rep 1,05)."""
    assert ta.recovery_factor("negative_reps", "forced_rep", CATALOG) == 1.25
    assert ta.recovery_factor("forced_reps", "forced_rep", CATALOG) == 1.20
    # aplicação sem código: o set_type aponta a técnica padrão do catálogo
    assert ta.recovery_factor(None, "forced_rep", CATALOG) == 1.20
    assert ta.recovery_factor(None, "dropset", CATALOG) == 1.15
    # sem catálogo / não calibrado -> neutro, nunca um número paralelo
    assert ta.recovery_factor("negative_reps", "forced_rep", {}) == 1.00
    assert ta.recovery_factor(None, "drop_set") == 1.00
    # série que não é técnica: fator do modelo por set_type
    assert ta.recovery_factor(None, "working") == 1.00
    assert ta.recovery_factor(None, "backoff") == 0.95


@pytest.mark.parametrize(
    "set_type, ficha, esperado",
    [
        ("drop_set", None, "drop_set"),
        ("dropset", None, "drop_set"),
        ("rest_pause", "drop_set", "rest_pause"),
        ("forced_rep", "negative_reps", "negative_reps"),
        ("forced_rep", None, "forced_reps"),
        ("forced_rep", "fst7", "forced_reps"),
        ("working", "rest_pause", None),
        ("warmup", None, None),
    ],
)
def test_derivacao_do_codigo(set_type, ficha, esperado):
    assert ta.derive_technique_code(set_type, ficha, CATALOG) == esperado
    assert ta.derive_technique_code(set_type, ficha) == esperado


def test_derivacao_da_ancora_e_a_ultima_serie_de_trabalho_anterior():
    anteriores = [(1, "warmup"), (2, "working"), (3, "working"), (4, "rest_pause"), (5, "working")]
    assert ta.derive_anchor_set_number(5, anteriores) == 3
    assert ta.derive_anchor_set_number(None, anteriores) == 5
    assert ta.derive_anchor_set_number(2, anteriores) is None
