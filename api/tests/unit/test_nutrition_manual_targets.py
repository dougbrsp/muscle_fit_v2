"""Gramas manuais validadas pelo motor (N1, D6; dono `domain/nutrition/manual_targets.py`)."""

from __future__ import annotations

import pytest

from app.domain.nutrition import labels as lb
from app.domain.nutrition import manual_targets as mt
from app.domain.nutrition import strategy as st

S = st.Strategy
R = mt.RangeStatus
PESO, MLG = 80.0, 64.0


def _check(proteina, carbo, gordura, estrategia=S.MAINTENANCE, **kwargs):
    kwargs.setdefault("weight_kg", PESO)
    kwargs.setdefault("ffm_kg", MLG)
    return mt.check_manual_targets(proteina, carbo, gordura, st.StrategyParams.defaults_for(estrategia), **kwargs)


def _campo(resultado: mt.ManualTargetsCheck, nome: str) -> mt.FieldCheck:
    return next(f for f in resultado.fields if f.field == nome)


def test_dentro_da_faixa_devolve_kcal_de_atwater_e_pode_salvar():
    r = _check(160, 300, 70)  # 2,0 g/kg; 3,75 g/kg; 70 g = 630 kcal
    assert r.kcal == pytest.approx(160 * 4 + 300 * 4 + 70 * 9)
    assert r.status == R.WITHIN_RANGE and r.can_save and r.macros is not None
    assert all(f.status == R.WITHIN_RANGE for f in r.fields)
    assert _campo(r, "protein_g").metric_value == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("proteina", "campo", "codigo"),
    [(100, "protein_g", mt.MANUAL_BELOW_RANGE), (200, "protein_g", mt.MANUAL_ABOVE_RANGE)],
)
def test_proteina_fora_da_faixa_avisa_e_salva(proteina, campo, codigo):
    r = _check(proteina, 300, 70)  # manutenção 1,6-2,2 g/kg: 1,25 e 2,5
    assert _campo(r, campo).status == R.WARNING
    assert _campo(r, campo).codes == (f"{codigo}:{campo}",)
    assert r.status == R.WARNING and r.can_save


@pytest.mark.parametrize(("proteina", "codigo"), [(50, mt.MANUAL_BLOCKED_BELOW), (300, mt.MANUAL_BLOCKED_ABOVE)])
def test_proteina_absurda_bloqueia(proteina, codigo):
    r = _check(proteina, 300, 70)  # 0,625 g/kg (< 0,8) e 3,75 g/kg (> 3,5)
    assert _campo(r, "protein_g").codes == (f"{codigo}:protein_g",)
    assert r.status == R.BLOCKED and not r.can_save


def test_limites_exatos_de_proteina_nao_bloqueiam():
    assert _campo(_check(0.8 * PESO, 300, 70), "protein_g").status == R.WARNING
    assert _campo(_check(3.5 * PESO, 300, 70), "protein_g").status == R.WARNING


def test_proteina_do_cutting_usa_a_base_de_massa_livre_de_gordura():
    r = _check(2.6 * MLG, 200, 55, estrategia=S.CUTTING)
    assert _campo(r, "protein_g").status == R.WITHIN_RANGE
    assert _campo(r, "protein_g").metric_value == pytest.approx(2.6)


def test_gordura_por_fracao_das_kcal():
    # 160 P + 300 C + 30 G = 640 + 1200 + 270 = 2110 kcal; 270/2110 = 12,8%: abaixo de 20% (aviso), acima de 10%.
    r = _check(160, 300, 30)
    assert _campo(r, "fat_g").status == R.WARNING
    assert _campo(r, "fat_g").codes == (f"{mt.MANUAL_BELOW_RANGE}:fat_g",)
    # 5 g = 45 kcal em 1885: 2,4%, abaixo do bloqueio.
    assert _check(160, 300, 5).status == R.BLOCKED
    # 200 g = 1800 kcal em 3640: 49%: aviso; 330 g: acima de 60%.
    assert _campo(_check(160, 300, 200), "fat_g").status == R.WARNING
    r = _check(160, 100, 330)
    assert _campo(r, "fat_g").codes == (f"{mt.MANUAL_BLOCKED_ABOVE}:fat_g",)


def test_carbo_avisa_abaixo_do_piso_e_acima_de_8_e_bloqueia_acima_de_12():
    assert _campo(_check(160, 40, 80), "carb_g").codes == (f"{mt.MANUAL_BELOW_RANGE}:carb_g",)  # 0,5 g/kg
    assert _campo(_check(160, 700, 70), "carb_g").codes == (f"{mt.MANUAL_ABOVE_RANGE}:carb_g",)  # 8,75 g/kg
    r = _check(160, 1000, 70)  # 12,5 g/kg
    assert _campo(r, "carb_g").codes == (f"{mt.MANUAL_BLOCKED_ABOVE}:carb_g",) and not r.can_save


def test_carbo_do_bulking_usa_o_piso_do_plano():
    # 2,5 g/kg está acima do piso geral (1,0), mas abaixo do 3,0 do bulking.
    assert _campo(_check(160, 200, 80, estrategia=S.BULKING), "carb_g").status == R.WARNING
    assert _campo(_check(160, 200, 80, estrategia=S.MAINTENANCE), "carb_g").status == R.WITHIN_RANGE


def test_energia_absurda_bloqueia_nos_dois_extremos():
    assert _campo(_check(60, 50, 20), "kcal").codes == (f"{mt.MANUAL_BLOCKED_BELOW}:kcal",)  # 620 kcal
    assert _campo(_check(300, 1000, 330), "kcal").codes == (f"{mt.MANUAL_BLOCKED_ABOVE}:kcal",)  # 8170 kcal
    r = _check(0, 0, 0)
    assert r.kcal == 0.0 and not r.can_save


def test_avisos_de_energia_contra_tmb_e_contra_a_meta():
    r = _check(160, 300, 70, bmr_kcal=2500.0)  # 2470 kcal < 2500
    assert f"{mt.MANUAL_BELOW_BMR}:kcal" in _campo(r, "kcal").codes
    assert r.can_save and r.status == R.WARNING
    r = _check(160, 300, 70, target_kcal=2000.0)  # 2470 vs 2000: +23%
    assert f"{mt.MANUAL_DIFFERS_FROM_TARGET}:kcal" in _campo(r, "kcal").codes
    assert _check(160, 300, 70, target_kcal=2450.0).status == R.WITHIN_RANGE


@pytest.mark.parametrize("ruim", [-1, "abc", None, float("nan"), float("inf")])
def test_valor_invalido_bloqueia_sem_kcal(ruim):
    r = _check(ruim, 300, 70)
    assert r.kcal is None and r.macros is None and not r.can_save
    assert _campo(r, "protein_g").codes == (f"{mt.MANUAL_INVALID_VALUE}:protein_g",)
    assert _campo(r, "carb_g").status == R.WITHIN_RANGE


def test_sem_peso_nao_inventa_g_por_kg():
    r = _check(160, 300, 70, weight_kg=None, ffm_kg=None)
    assert _campo(r, "protein_g").metric_value is None and _campo(r, "carb_g").metric_value is None
    assert set(r.limitations) == {f"{mt.LIMIT_MISSING_BASE}:protein_g", f"{mt.LIMIT_MISSING_BASE}:carb_g"}
    assert r.can_save


def test_to_dict_e_rotulos_de_todos_os_codigos():
    r = _check(100, 40, 5, bmr_kcal=2500.0, target_kcal=2000.0, weight_kg=None)
    dados = r.to_dict()
    assert dados["status"] == "blocked" and dados["can_save"] is False
    codigos = [c for f in r.fields for c in f.codes] + list(r.limitations)
    assert codigos
    for codigo in codigos:
        assert lb.limitation_label(codigo) != codigo


def test_todo_codigo_emitido_tem_rotulo_com_o_campo():
    for codigo in (
        mt.MANUAL_INVALID_VALUE,
        mt.MANUAL_BELOW_RANGE,
        mt.MANUAL_ABOVE_RANGE,
        mt.MANUAL_BLOCKED_BELOW,
        mt.MANUAL_BLOCKED_ABOVE,
        mt.MANUAL_BELOW_BMR,
        mt.MANUAL_DIFFERS_FROM_TARGET,
        mt.LIMIT_MISSING_BASE,
    ):
        texto = lb.limitation_label(f"{codigo}:carb_g")
        assert texto.startswith("Carboidrato (g)")


def test_limites_de_bloqueio_estao_fora_das_faixas_de_aviso():
    piso, teto = st.protein_range_for(S.CUTTING)
    assert mt.PROTEIN_BLOCK_MIN_G_PER_KG < piso and mt.PROTEIN_BLOCK_MAX_G_PER_KG > teto
    for estrategia in S:
        gordura_piso, gordura_teto = st.fat_share_range_for(estrategia)
        assert mt.FAT_BLOCK_MIN_KCAL_SHARE < gordura_piso and mt.FAT_BLOCK_MAX_KCAL_SHARE > gordura_teto
    assert mt.CARB_WARN_MAX_G_PER_KG < mt.CARB_BLOCK_MAX_G_PER_KG
