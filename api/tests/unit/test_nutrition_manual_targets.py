"""Gramas manuais validadas pelo motor (N1, D6; dono `domain/nutrition/manual_targets.py`)."""

from __future__ import annotations

from dataclasses import replace

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


def _check_legado_mlg(proteina, carbo, gordura, **kwargs):
    """Cutting com a base antiga (massa livre de gordura): só metas/planos antigos a carregam (nutrition-v4 usa o peso)."""
    kwargs.setdefault("weight_kg", PESO)
    kwargs.setdefault("ffm_kg", MLG)
    params = replace(st.StrategyParams.defaults_for(S.CUTTING), protein_basis=st.ProteinBasis.FAT_FREE_MASS)
    return mt.check_manual_targets(proteina, carbo, gordura, params, **kwargs)


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
    # Manutenção (base = peso 80 kg): piso 0,8 × 80 e teto 3,5 × 80 valem, um pouco além bloqueia.
    assert _campo(_check(0.8 * PESO, 300, 70), "protein_g").status == R.WARNING
    assert _campo(_check(3.5 * PESO, 300, 70), "protein_g").status == R.WARNING
    assert _campo(_check(3.5 * PESO + 1, 300, 70), "protein_g").status == R.BLOCKED
    # Cutting (v4, base = peso 80 kg): teto 3,5 × 80.
    assert _campo(_check(3.5 * PESO, 200, 55, estrategia=S.CUTTING), "protein_g").status == R.WARNING
    assert _campo(_check(3.5 * PESO + 1, 200, 55, estrategia=S.CUTTING), "protein_g").status == R.BLOCKED
    # Legado (base = MLG 64 kg): teto 3,5 × 64.
    assert _campo(_check_legado_mlg(3.5 * MLG, 200, 55), "protein_g").status == R.WARNING
    assert _campo(_check_legado_mlg(3.5 * MLG + 1, 200, 55), "protein_g").status == R.BLOCKED


def test_teto_de_proteina_usa_a_base_da_estrategia():
    # Manutenção, 70 kg, 40% de gordura (MLG 42 kg): 154 g = 2,2 g/kg do peso (topo da faixa) e 3,67 g/kg da
    # MLG. Dentro da faixa recomendada, não pode bloquear.
    r = _check(154, 300, 70, weight_kg=70.0, ffm_kg=42.0)
    assert _campo(r, "protein_g").status == R.WITHIN_RANGE and r.can_save
    # No cutting (v4) o teto é por peso: 3,6 g/kg de 80 kg = 288 g bloqueia, com ou sem MLG.
    cut = _campo(_check(290, 200, 55, estrategia=S.CUTTING), "protein_g")
    assert cut.codes == (f"{mt.MANUAL_BLOCKED_ABOVE}:protein_g",) and cut.base == "body_weight"
    semmlg = _campo(_check(290, 200, 55, estrategia=S.CUTTING, ffm_kg=None), "protein_g")
    assert semmlg.codes == (f"{mt.MANUAL_BLOCKED_ABOVE}:protein_g",) and semmlg.base == "body_weight"
    # Legado (MLG): 3,6 g/kg de 64 kg = 230,4 g bloqueia (e passaria por peso: 2,88 g/kg).
    legado = _campo(_check_legado_mlg(230.4, 200, 55), "protein_g")
    assert legado.codes == (f"{mt.MANUAL_BLOCKED_ABOVE}:protein_g",) and legado.base == "fat_free_mass"
    # Legado sem MLG: o teto usa o peso e a faixa não é comparada (bases diferentes).
    legado_sem = _campo(_check_legado_mlg(290, 200, 55, ffm_kg=None), "protein_g")
    assert legado_sem.base == "body_weight" and legado_sem.recommended is None


@pytest.mark.parametrize("estrategia", list(S))
@pytest.mark.parametrize("gordura_corporal", [3.0, 10.0, 25.0, 40.0, 60.0])
def test_nada_dentro_da_faixa_recomendada_e_bloqueado(estrategia, gordura_corporal):
    params = st.StrategyParams.defaults_for(estrategia)
    peso = 70.0
    mlg = peso * (1.0 - gordura_corporal / 100.0)
    base = mlg if params.protein_basis == st.ProteinBasis.FAT_FREE_MASS else peso
    piso, teto = st.protein_range_for(estrategia)
    for g_por_kg in (piso + 1e-9, (piso + teto) / 2.0, teto - 1e-9):
        r = mt.check_manual_targets(g_por_kg * base, 3.0 * peso, 70.0, params, weight_kg=peso, ffm_kg=mlg)
        campo = _campo(r, "protein_g")
        assert campo.status == R.WITHIN_RANGE, (estrategia, gordura_corporal, g_por_kg)
        # Valor e faixa na mesma base.
        assert campo.base == ("fat_free_mass" if params.protein_basis == st.ProteinBasis.FAT_FREE_MASS else "body_weight")
        assert campo.recommended == (piso, teto)


def test_field_check_nao_mistura_bases_no_bloqueio_do_piso():
    # Cutting v4: piso e faixa na mesma base (peso), então a faixa acompanha o valor.
    novo = _campo(_check(20, 200, 55, estrategia=S.CUTTING), "protein_g")
    assert novo.base == "body_weight" and novo.recommended == st.CUT_PROTEIN_G_PER_KG_RANGE
    # Legado com 20 g de proteína: o piso é sobre o peso (0,25 g/kg), a faixa é em MLG: sem faixa no resultado.
    campo = _campo(_check_legado_mlg(20, 200, 55), "protein_g")
    assert campo.codes == (f"{mt.MANUAL_BLOCKED_BELOW}:protein_g",)
    assert campo.base == "body_weight" and campo.metric_value == pytest.approx(0.25) and campo.recommended is None
    assert campo.to_dict()["base"] == "body_weight"


def test_bloqueio_de_proteina_usa_peso_no_piso():
    # 50 g de 80 kg: 0,625 g/kg bloqueia mesmo sendo 0,78 g/kg da MLG.
    assert _campo(_check(50, 300, 70), "protein_g").codes == (f"{mt.MANUAL_BLOCKED_BELOW}:protein_g",)
    # No cutting sem MLG, o piso e o teto são sobre o peso.
    assert _campo(_check(50, 200, 55, estrategia=S.CUTTING, ffm_kg=None), "protein_g").status == R.BLOCKED  # 0,625 g/kg
    assert _campo(_check(300, 200, 55, estrategia=S.CUTTING, ffm_kg=None), "protein_g").status == R.BLOCKED  # 3,75 g/kg


def test_zero_de_proteina_no_cutting_sem_mlg_bloqueia_pelo_peso():
    r = _check(0, 200, 55, estrategia=S.CUTTING, ffm_kg=None)
    assert _campo(r, "protein_g").codes == (f"{mt.MANUAL_BLOCKED_BELOW}:protein_g",) and not r.can_save


def test_sem_nenhuma_base_o_campo_nunca_sai_dentro_da_faixa():
    r = _check(0, 900, 70, estrategia=S.CUTTING, weight_kg=None, ffm_kg=None)
    for campo in ("protein_g", "carb_g"):
        assert _campo(r, campo).status == R.WARNING
        assert _campo(r, campo).codes == (f"{mt.MISSING_BASE}:{campo}",)
    assert r.status == R.WARNING and r.can_save
    assert lb.limitation_label(f"{mt.MISSING_BASE}:protein_g").startswith("Prote")
    # Legado com peso mas sem %G: a faixa da proteína (por MLG) não é conferida, e 0 g ainda bloqueia pelo piso.
    sem_mlg = _check_legado_mlg(160, 200, 55, ffm_kg=None)
    assert _campo(sem_mlg, "protein_g").status == R.WARNING and _campo(sem_mlg, "carb_g").status != R.WARNING


@pytest.mark.parametrize("enorme", [1e308, 5001, 1e30, "1e308"])
def test_gramas_enormes_sao_invalidas_e_o_resultado_serializa(enorme):
    import json

    r = _check(enorme, 300, 70)
    assert r.kcal is None and not r.can_save
    assert _campo(r, "protein_g").codes == (f"{mt.MANUAL_INVALID_VALUE}:protein_g",)
    json.dumps(r.to_dict(), allow_nan=False)


def test_texto_numerico_e_aceito_como_numero():
    # Contrato de `non_negative_number`: "160" vale 160 (a rota recebe JSON, mas o motor não presume o tipo).
    assert _check("160", "300", "70").kcal == pytest.approx(_check(160, 300, 70).kcal)


def test_proteina_do_cutting_usa_a_base_de_peso_total():
    r = _check(2.6 * PESO, 200, 55, estrategia=S.CUTTING)
    assert _campo(r, "protein_g").status == R.WITHIN_RANGE
    assert _campo(r, "protein_g").metric_value == pytest.approx(2.6)
    assert _campo(r, "protein_g").base == "body_weight"
    # Legado: a base antiga (MLG) continua lendo.
    legado = _check_legado_mlg(2.6 * MLG, 200, 55)
    assert _campo(legado, "protein_g").status == R.WITHIN_RANGE
    assert _campo(legado, "protein_g").metric_value == pytest.approx(2.6)


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
    assert _campo(r, "protein_g").status == R.WARNING
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
        mt.MISSING_BASE,
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
