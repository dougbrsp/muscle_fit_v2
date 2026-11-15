"""N2, rodada 1 de revisão: pisos do ciclo (menor e maior dia), prioridade × estratégia × ciclo, soma semanal
nos limites, bordas dos bloqueios por macro, publicação no meio da semana, projeção com gramas manuais,
`expected_updated_at`, limite de rascunhos, plano do personal editado pelo dono e "puxar do Modelo" parcial."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import labels as lb
from app.domain.nutrition import macros as mc
from app.domain.nutrition import manual_targets as mt
from app.domain.nutrition import plan as pl
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import LIMIT_MANUAL_TARGETS_IGNORE_PHASE
from app.domain.nutrition.phases import PhaseKind, ProjectionStatus
from app.security import Identity, get_current_identity
from tests.support.nutrition_fake import FakeNutritionDB

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

SEGUNDA = date(2026, 10, 12)
QUARTA = date(2026, 10, 14)
ALUNO = 2
S = st.Strategy
D = cc.DayKind
SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)
COM_MLG = replace(SUJEITO, body_fat_percent=15.0)
LEVE = Subject(sex=Sex.FEMALE, age_years=30, height_cm=160, weight_kg=50, activity_level=ActivityLevel.SEDENTARY)
TREINO = {0: 400.0, 2: 650.0, 4: 300.0}


def _plano(estrategia=S.MAINTENANCE, **extra) -> PlanParams:
    return PlanParams(strategy_params=st.StrategyParams.defaults_for(estrategia), **extra)


def _semana(plano, sujeito=COM_MLG):
    metas = []
    for i in range(7):
        dia = SEGUNDA + timedelta(days=i)
        ctx = DayContext(day=dia, is_training_day=True, training_kcal_reported=TREINO[i]) if i in TREINO else DayContext(day=dia)
        metas.append(compute_daily_target(sujeito, plano, ctx))
    return metas


def _soma(metas, campo):
    return sum(getattr(m.macros, campo) for m in metas)


# ---------------------------------------------------------------------------
# Prioridade × estratégia, e prioridade + ciclo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("estrategia", [S.CUTTING, S.MAINTENANCE, S.BULKING])
@pytest.mark.parametrize("prioridade", list(st.MacroPriority))
def test_prioridade_por_estrategia_segue_o_dono_e_a_faixa_de_gordura(estrategia, prioridade):
    plano = _plano(estrategia, macro_priority=prioridade)
    meta = compute_daily_target(COM_MLG, plano, DayContext(day=SEGUNDA))
    base_mlg = 68.0
    esperado = mc.macro_split_for_priority(meta.kcal_target.kcal, plano.strategy_params, prioridade, 80, base_mlg)
    assert (meta.macros.protein_g, meta.macros.carb_g, meta.macros.fat_g) == pytest.approx(
        (esperado.protein_g, esperado.carb_g, esperado.fat_g)
    )
    piso, teto = st.fat_share_range_for(estrategia)
    fracao = meta.macros.fat_g * 9 / meta.macros.kcal
    if prioridade != st.MacroPriority.BALANCED:
        assert piso - 1e-9 <= fracao <= teto + 1e-9


@pytest.mark.parametrize("estrategia", [S.MAINTENANCE, S.BULKING])
@pytest.mark.parametrize("prioridade", [st.MacroPriority.FIXED_PROTEIN, st.MacroPriority.HIGH_CARB, st.MacroPriority.HIGH_FAT])
def test_prioridade_com_ciclo_preserva_a_soma_e_a_prioridade(estrategia, prioridade):
    kinds = (D.HIGH, D.NORMAL, D.LOW, D.NORMAL, D.HIGH, D.LOW, D.NORMAL)
    sem = _semana(_plano(estrategia, macro_priority=prioridade))
    com = _semana(_plano(estrategia, macro_priority=prioridade, cycle_kinds=kinds))
    assert _soma(com, "carb_g") == pytest.approx(_soma(sem, "carb_g"))
    # O ciclo move kcal entre os dias e a gordura acompanha a kcal final do dia na MESMA fração que a prioridade
    # aplicou (fix da gordura percentual): a proteína não muda, a fração do dia fica a do plano e a soma semanal de
    # gordura e de kcal se preserva.
    for a, b in zip(sem, com):
        assert a.macros.protein_g == b.macros.protein_g
        assert b.macros.fat_g * 9 / b.macros.kcal == pytest.approx(a.macros.fat_g * 9 / a.macros.kcal)
    assert _soma(com, "fat_g") == pytest.approx(_soma(sem, "fat_g"))
    assert _soma(com, "kcal") == pytest.approx(_soma(sem, "kcal"))


# ---------------------------------------------------------------------------
# Soma semanal nos limites do ciclo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kinds",
    [
        (D.HIGH, D.NORMAL, D.LOW, D.NORMAL, D.HIGH, D.LOW, D.NORMAL),
        (D.HIGH, D.LOW, D.LOW, D.LOW, D.LOW, D.LOW, D.LOW),
        (D.HIGH, D.HIGH, D.HIGH, D.HIGH, D.HIGH, D.HIGH, D.LOW),
        (D.NORMAL,) * 7,
        (D.HIGH, D.NORMAL, D.NORMAL, D.NORMAL, D.NORMAL, D.NORMAL, D.NORMAL),
        (D.LOW, D.NORMAL, D.NORMAL, D.NORMAL, D.NORMAL, D.NORMAL, D.NORMAL),
    ],
)
@pytest.mark.parametrize("estrategia", [S.MAINTENANCE, S.BULKING])
def test_soma_semanal_preservada_nos_limites_do_ciclo(kinds, estrategia):
    sem = _semana(_plano(estrategia))
    com = _semana(_plano(estrategia, cycle_kinds=kinds))
    assert _soma(com, "carb_g") == pytest.approx(_soma(sem, "carb_g"))
    assert _soma(com, "kcal") == pytest.approx(_soma(sem, "kcal"))


# ---------------------------------------------------------------------------
# Bordas da faixa e do bloqueio por macro (maiores e menores valores exatos)
# ---------------------------------------------------------------------------


def _check(p, c, f):
    return mt.check_manual_targets(p, c, f, st.StrategyParams.defaults_for(S.MAINTENANCE), 80, None)


def _status(check, campo):
    return next(x for x in check.fields if x.field == campo).status


def test_borda_do_bloqueio_da_proteina_no_piso_e_no_teto():
    assert _status(_check(80 * mt.PROTEIN_BLOCK_MIN_G_PER_KG, 250, 70), "protein_g") != mt.RangeStatus.BLOCKED
    assert _status(_check(80 * mt.PROTEIN_BLOCK_MIN_G_PER_KG - 0.1, 250, 70), "protein_g") == mt.RangeStatus.BLOCKED
    assert _status(_check(80 * mt.PROTEIN_BLOCK_MAX_G_PER_KG, 250, 70), "protein_g") != mt.RangeStatus.BLOCKED
    assert _status(_check(80 * mt.PROTEIN_BLOCK_MAX_G_PER_KG + 0.1, 250, 70), "protein_g") == mt.RangeStatus.BLOCKED


def test_borda_da_faixa_recomendada_da_proteina_e_aviso_nao_bloqueio():
    piso, teto = st.protein_range_for(S.MAINTENANCE)
    assert _status(_check(80 * piso, 250, 70), "protein_g") == mt.RangeStatus.WITHIN_RANGE
    assert _status(_check(80 * teto, 250, 70), "protein_g") == mt.RangeStatus.WITHIN_RANGE
    assert _status(_check(80 * piso - 1, 250, 70), "protein_g") == mt.RangeStatus.WARNING
    assert _status(_check(80 * teto + 1, 250, 70), "protein_g") == mt.RangeStatus.WARNING


def test_borda_do_carbo_aviso_e_bloqueio():
    assert _status(_check(150, 80 * mt.CARB_WARN_MAX_G_PER_KG, 70), "carb_g") == mt.RangeStatus.WITHIN_RANGE
    assert _status(_check(150, 80 * mt.CARB_WARN_MAX_G_PER_KG + 1, 70), "carb_g") == mt.RangeStatus.WARNING
    assert _status(_check(150, 80 * mt.CARB_BLOCK_MAX_G_PER_KG, 70), "carb_g") == mt.RangeStatus.WARNING
    assert _status(_check(150, 80 * mt.CARB_BLOCK_MAX_G_PER_KG + 0.5, 70), "carb_g") == mt.RangeStatus.BLOCKED


def test_borda_da_gordura_pela_fracao_das_kcal():
    base = 150 * 4 + 250 * 4  # 1.600 kcal sem gordura
    piso, teto = mt.FAT_BLOCK_MIN_KCAL_SHARE, mt.FAT_BLOCK_MAX_KCAL_SHARE
    g_piso = piso * base / (9 * (1 - piso))
    g_teto = teto * base / (9 * (1 - teto))
    assert _status(_check(150, 250, g_piso + 0.1), "fat_g") != mt.RangeStatus.BLOCKED
    assert _status(_check(150, 250, g_piso - 0.1), "fat_g") == mt.RangeStatus.BLOCKED
    assert _status(_check(150, 250, g_teto - 0.1), "fat_g") != mt.RangeStatus.BLOCKED
    assert _status(_check(150, 250, g_teto + 0.1), "fat_g") == mt.RangeStatus.BLOCKED


# ---------------------------------------------------------------------------
# Pisos do ciclo (menor e maior dia)
# ---------------------------------------------------------------------------


def _cfg(manual, dias, estrategia="maintenance"):
    r = pl.build_plan_settings(strategy=estrategia, manual_targets=manual, cycle_days=dias)
    assert r.ok, r.errors
    return r.settings


UM_ALTO_SEIS_BAIXOS = {0: "high", 1: "low", 2: "low", 3: "low", 4: "low", 5: "low", 6: "low"}


def test_ciclo_com_gramas_perto_do_piso_de_800_kcal_bloqueia():
    # 805 kcal por dia nas gramas (acima do piso de 800 sem ciclo), mas o dia baixo do ciclo cai para ~770.
    cfg = _cfg({"protein_g": 45, "carb_g": 100, "fat_g": 25}, UM_ALTO_SEIS_BAIXOS)
    assert pl.check_plan_manual_targets(cfg, LEVE) is not None
    assert pl.manual_save_errors(pl.check_plan_manual_targets(cfg, LEVE), LEVE) == ()  # as gramas sozinhas passam
    erros, _avisos = pl.check_cycle_floors(cfg, LEVE)
    assert erros == (pl.ERR_CYCLE_KCAL_BELOW_MIN,)


def test_ciclo_longe_do_piso_passa_e_abaixo_da_tmb_so_avisa():
    cfg = _cfg({"protein_g": 45, "carb_g": 150, "fat_g": 25}, {0: "high", 1: "low", 2: "high", 3: "low", 4: "high", 5: "low", 6: "low"})
    erros, avisos = pl.check_cycle_floors(cfg, LEVE)
    assert erros == () and avisos == (pl.WARN_CYCLE_KCAL_BELOW_BMR,)


def test_ciclo_sem_gramas_manuais_tambem_e_conferido_e_sem_ciclo_nao_confere():
    automatico = _cfg(None, UM_ALTO_SEIS_BAIXOS)
    erros, _ = pl.check_cycle_floors(automatico, SUJEITO)
    assert erros == ()  # o automático da manutenção fica bem acima de 800 kcal
    assert pl.check_cycle_floors(_cfg(None, None), SUJEITO) == ((), ())


def test_dia_alto_acima_do_teto_de_energia_bloqueia():
    cfg = _cfg({"protein_g": 250, "carb_g": 800, "fat_g": 380}, {0: "high", 1: "low", 2: "low", 3: "low", 4: "low", 5: "low", 6: "low"})
    # 7.620 kcal por dia nas gramas (abaixo do teto), mas o dia alto do ciclo passa de 8.000.
    erros, _ = pl.check_cycle_floors(cfg, replace(SUJEITO, weight_kg=100))
    assert pl.ERR_CYCLE_KCAL_ABOVE_MAX in erros


def test_codigos_novos_tem_rotulo():
    for codigo in (
        pl.ERR_CYCLE_KCAL_BELOW_MIN, pl.ERR_CYCLE_KCAL_ABOVE_MAX, pl.WARN_CYCLE_KCAL_BELOW_BMR, pl.WARN_CYCLE_PARTIAL_WEEK,
        pl.REASON_MANUAL_AGAINST_STRATEGY, pl.REASON_MANUAL_MISSING_TDEE, pl.NOTE_TEMPLATE_PARTIAL_WEEKDAYS, pl.ERR_DRAFT_LIMIT,
        LIMIT_MANUAL_TARGETS_IGNORE_PHASE,
    ):
        assert lb.limitation_label(codigo) != codigo, codigo


# ---------------------------------------------------------------------------
# Projeção com gramas manuais (motor)
# ---------------------------------------------------------------------------


GORDO = replace(SUJEITO, weight_kg=90.0, body_fat_percent=25.0)


def _proj(estrategia, kcal_manual_gramas, tdee=2800, **alvo):
    proteina, gordura = 180.0, 70.0
    carbo = (kcal_manual_gramas - proteina * 4 - gordura * 9) / 4
    cfg = pl.build_plan_settings(
        strategy=estrategia, manual_targets={"protein_g": proteina, "carb_g": carbo, "fat_g": gordura}, **alvo
    ).settings
    return pl.project_plan_goal(cfg, GORDO, tdee, SEGUNDA)


def test_projecao_cutting_com_gramas_manuais_deriva_o_ritmo_do_deficit():
    p = _proj("cutting", 2300, goal_weight_kg=80.0)
    assert p.status == ProjectionStatus.PROJECTED and p.weekly_rate_share and p.weeks > 0
    maior_deficit = _proj("cutting", 2000, goal_weight_kg=80.0)
    assert maior_deficit.weekly_rate_share > p.weekly_rate_share and maior_deficit.weeks < p.weeks


def test_projecao_cutting_com_superavit_manual_e_incoerente_com_motivo():
    p = _proj("cutting", 3100, goal_weight_kg=80.0)
    assert p.status == ProjectionStatus.INCOHERENT and p.reasons == (pl.REASON_MANUAL_AGAINST_STRATEGY,)
    assert _proj("cutting", 2800, goal_weight_kg=80.0).status == ProjectionStatus.INCOHERENT  # saldo zero também


def test_projecao_bulking_com_gramas_manuais_deriva_o_ritmo_do_superavit():
    p = _proj("bulking", 3200, goal_weight_kg=95.0)
    assert p.status == ProjectionStatus.PROJECTED and p.weeks > 0
    assert _proj("bulking", 2500, goal_weight_kg=95.0).status == ProjectionStatus.INCOHERENT


def test_projecao_com_gramas_manuais_sem_tdee_e_dados_insuficientes_com_motivo():
    p = _proj("cutting", 2300, tdee=None, goal_weight_kg=80.0)
    assert p.status == ProjectionStatus.INSUFFICIENT_DATA and p.reasons == (pl.REASON_MANUAL_MISSING_TDEE,)


def test_projecao_sem_gramas_manuais_continua_a_de_sempre():
    cfg = pl.build_plan_settings(strategy="cutting", goal_weight_kg=80.0).settings
    p = pl.project_plan_goal(cfg, GORDO, 2800, SEGUNDA)
    assert p.status == ProjectionStatus.PROJECTED and p.weekly_rate_share == pytest.approx(st.CUT_WEEKLY_RATE_SHARE_DEFAULT)


# ---------------------------------------------------------------------------
# Meta diária: delta real e fase com gramas manuais
# ---------------------------------------------------------------------------


def test_delta_real_considera_as_gramas_manuais():
    manual = compute_daily_target(SUJEITO, _plano(manual_macros=(150.0, 200.0, 60.0)), DayContext(day=SEGUNDA))
    esperado = (150 * 4 + 200 * 4 + 60 * 9) - manual.tdee.kcal
    assert manual.kcal_delta == pytest.approx(esperado)
    assert manual.kcal_delta != manual.kcal_target.delta_kcal
    assert compute_daily_target(SUJEITO, _plano(), DayContext(day=SEGUNDA)).kcal_delta == pytest.approx(0.0)


@pytest.mark.parametrize("fase", [PhaseKind.DIET_BREAK, PhaseKind.REFEED])
def test_gramas_manuais_em_pausa_ou_refeed_avisam_que_a_fase_nao_altera(fase):
    meta = compute_daily_target(COM_MLG, _plano(S.CUTTING, manual_macros=(180.0, 250.0, 60.0)), DayContext(day=SEGUNDA, phase_kind=fase))
    assert LIMIT_MANUAL_TARGETS_IGNORE_PHASE in meta.limitations
    assert (meta.macros.protein_g, meta.macros.carb_g, meta.macros.fat_g) == (180.0, 250.0, 60.0)
    normal = compute_daily_target(COM_MLG, _plano(S.CUTTING, manual_macros=(180.0, 250.0, 60.0)), DayContext(day=SEGUNDA))
    assert LIMIT_MANUAL_TARGETS_IGNORE_PHASE not in normal.limitations


# ---------------------------------------------------------------------------
# "Puxar do Modelo": só com dia da semana em TODOS os slots
# ---------------------------------------------------------------------------


def test_modelo_com_dia_da_semana_so_em_alguns_slots_nao_propoe_e_explica():
    parcial = [(1, True), (2, False), (None, True), (4, False)]
    assert pl.propose_cycle_from_template(parcial) is None and pl.training_weekdays_from_template(parcial) is None
    assert pl.template_weekdays_note(parcial) == pl.NOTE_TEMPLATE_PARTIAL_WEEKDAYS
    assert pl.template_weekdays_note([(None, True), (None, False)]) == pl.NOTE_TEMPLATE_WITHOUT_WEEKDAYS
    assert pl.template_weekdays_note([(1, True), (2, False)]) is None
    assert pl.template_weekdays_note([(9, True), (2, False)]) == pl.NOTE_TEMPLATE_PARTIAL_WEEKDAYS  # dia inválido não conta


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------


@pytest.fixture
def hoje():
    return {"dia": SEGUNDA}


@pytest.fixture
def db(monkeypatch, hoje):
    banco = FakeNutritionDB()
    banco.add_weight(ALUNO, SEGUNDA - timedelta(days=3), 80)
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: hoje["dia"])
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    monkeypatch.setattr(plans_mod, "resolve_active_template_id", lambda uid: next(iter(banco.templates), None))
    return banco


def _cliente(role="regular", user_id=ALUNO):
    app = FastAPI()
    app.include_router(plans_mod.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


DIAS = {"0": "high", "1": "low", "2": "high", "3": "low", "4": "high", "5": "low", "6": "low"}


def _criar(cliente, **corpo):
    return cliente.post("/nutrition/plans", json={"strategy": "maintenance", **corpo})


def _codigos(resposta):
    return [item["code"] for item in resposta.json()["detail"]["errors"]]


def test_publicar_no_meio_da_semana_avisa_sem_compensar_e_a_previa_mostra_so_os_dias_que_acontecem(db, hoje):
    hoje["dia"] = QUARTA
    cliente = _cliente()
    plano = _criar(cliente, cycle_days=DIAS).json()
    previa = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()
    assert "cycle_partial_week" in [a["code"] for a in previa["warnings"]]
    assert previa["publish_days"] == [(QUARTA + timedelta(days=i)).isoformat() for i in range(5)]  # qua..dom
    resposta = cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None})
    assert resposta.status_code == 200
    assert sorted(dia for (_u, dia) in db.targets) == [QUARTA + timedelta(days=i) for i in range(5)]
    # Sem compensação: cada dia é o do ciclo sobre a base, a média da semana inteira NÃO é refeita.
    tipos = {dia.weekday(): linha["day_kind"] for (_u, dia), linha in db.targets.items()}
    assert tipos == {2: "high", 3: "low", 4: "high", 5: "low", 6: "low"}


def test_publicar_na_segunda_ou_sem_ciclo_nao_avisa_semana_parcial(db, hoje):
    cliente = _cliente()
    com = _criar(cliente, cycle_days=DIAS).json()
    sem = _criar(cliente).json()
    assert "cycle_partial_week" not in [a["code"] for a in cliente.post(f"/nutrition/plans/{com['id']}/preview").json()["warnings"]]
    hoje["dia"] = QUARTA
    assert "cycle_partial_week" not in [a["code"] for a in cliente.post(f"/nutrition/plans/{sem['id']}/preview").json()["warnings"]]
    assert len(cliente.post(f"/nutrition/plans/{com['id']}/preview").json()["week"]) == 7


def test_pisos_do_ciclo_valem_ao_salvar_editar_e_publicar(db, hoje):
    db.composition.clear()
    db.add_weight(ALUNO, SEGUNDA - timedelta(days=3), 50)
    db.profiles[ALUNO] = {**db.profiles[ALUNO], "sex": "F", "height_cm": Decimal("160.0"), "activity_level": "sedentary"}
    cliente = _cliente()
    ruim = _criar(cliente, manual_targets={"protein_g": 45, "carb_g": 100, "fat_g": 25}, cycle_days=UM_ALTO_SEIS_BAIXOS_STR)
    assert ruim.status_code == 400 and "cycle_kcal_below_minimum" in _codigos(ruim) and db.plans == {}
    bom = _criar(cliente, manual_targets={"protein_g": 45, "carb_g": 150, "fat_g": 25}, cycle_days=DIAS)
    assert bom.status_code == 201, bom.text
    assert "cycle_kcal_below_bmr" in [a["code"] for a in bom.json()["warnings"]]  # abaixo da TMB só avisa
    editar = cliente.put(f"/nutrition/plans/{bom.json()['id']}", json={
        "strategy": "maintenance", "manual_targets": {"protein_g": 45, "carb_g": 100, "fat_g": 25}, "cycle_days": UM_ALTO_SEIS_BAIXOS_STR})
    assert editar.status_code == 400 and "cycle_kcal_below_minimum" in _codigos(editar)


UM_ALTO_SEIS_BAIXOS_STR = {str(k): v for k, v in UM_ALTO_SEIS_BAIXOS.items()}


def test_preview_projeta_com_gramas_manuais_e_expoe_o_ritmo_pronto(db, hoje):
    db.composition.clear()
    db.add_weight(ALUNO, SEGUNDA - timedelta(days=3), 90, body_fat_percent=Decimal("25"), fat_free_mass_kg=Decimal("67.5"))
    cliente = _cliente()
    plano = _criar(cliente, strategy="cutting", goal_weight_kg=80, manual_targets={"protein_g": 200, "carb_g": 150, "fat_g": 60}).json()
    dados = cliente.post(f"/nutrition/plans/{plano['id']}/preview").json()
    proj = dados["projection"]
    assert proj["status"] == "projected"
    assert proj["weekly_rate_percent"] == pytest.approx(proj["weekly_rate_share"] * 100)
    assert dados["target"]["kcal_delta"] == pytest.approx(dados["target"]["kcal"] - dados["target"]["tdee"]["kcal"])
    contra = _criar(cliente, strategy="cutting", goal_weight_kg=80, manual_targets={"protein_g": 200, "carb_g": 500, "fat_g": 100}).json()
    proj2 = cliente.post(f"/nutrition/plans/{contra['id']}/preview").json()["projection"]
    assert proj2["status"] == "incoherent_with_strategy" and proj2["weekly_rate_percent"] is None
    assert proj2["reason_items"][0]["code"] == "projection_manual_against_strategy"


def test_publicacao_confere_a_versao_do_rascunho(db):
    cliente = _cliente()
    plano = _criar(cliente).json()
    velha = plano["updated_at"]
    editado = cliente.put(f"/nutrition/plans/{plano['id']}", json={"strategy": "maintenance", "protein_g_per_kg": 1.8}).json()
    assert editado["updated_at"] != velha
    antiga = cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None, "expected_updated_at": velha})
    assert antiga.status_code == 409 and antiga.json()["detail"]["error"] == "draft_changed"
    assert db.plans[plano["id"]]["status"] == "draft"
    certa = cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None, "expected_updated_at": editado["updated_at"]})
    assert certa.status_code == 200
    assert _criar(cliente).status_code == 201  # e o cliente sem a versão segue aceito
    novo = _criar(cliente).json()
    assert cliente.post(f"/nutrition/plans/{novo['id']}/publish", json={"expected_current_id": plano["id"]}).status_code == 200


def test_limite_de_rascunhos_por_usuario(db, monkeypatch):
    monkeypatch.setattr(plans_mod, "MAX_DRAFTS_PER_USER", 3)
    cliente = _cliente()
    for _ in range(3):
        assert _criar(cliente).status_code == 201
    estouro = _criar(cliente)
    assert estouro.status_code == 409 and estouro.json()["detail"]["error"] == "draft_limit"
    assert estouro.json()["detail"]["max_drafts"] == 3 and estouro.json()["detail"]["message"]
    # Outro usuário não é afetado, e apagar libera.
    assert pl.MAX_DRAFTS_PER_USER == 20
    primeiro = min(db.plans)
    assert cliente.delete(f"/nutrition/plans/{primeiro}").status_code == 200
    assert _criar(cliente).status_code == 201


def test_rascunho_publicado_nao_conta_no_limite(db, monkeypatch):
    monkeypatch.setattr(plans_mod, "MAX_DRAFTS_PER_USER", 1)
    cliente = _cliente()
    plano = _criar(cliente).json()
    assert cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None}).status_code == 200
    assert _criar(cliente).status_code == 201


def test_listagem_de_planos_e_limitada(db, monkeypatch):
    monkeypatch.setattr(plans_mod, "PLAN_LIST_MAX", 2)
    cliente = _cliente()
    for _ in range(4):
        _criar(cliente)
    assert len(cliente.get("/nutrition/plans").json()["items"]) == 2
    assert pl.PLAN_LIST_MAX == 100


def test_rascunho_criado_pelo_personal_e_do_dono_que_edita_publica_e_apaga(db):
    personal = _cliente("personal_trainer", user_id=1)
    criado = personal.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance"}).json()
    assert criado["created_by"] == 1 and criado["user_id"] == ALUNO
    dono = _cliente("regular", user_id=ALUNO)
    assert [p["id"] for p in dono.get("/nutrition/plans").json()["items"]] == [criado["id"]]
    assert dono.put(f"/nutrition/plans/{criado['id']}", json={"strategy": "maintenance", "protein_g_per_kg": 1.9}).status_code == 200
    assert dono.post(f"/nutrition/plans/{criado['id']}/publish", json={"expected_current_id": None}).status_code == 200
    outro = personal.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance"}).json()
    assert dono.delete(f"/nutrition/plans/{outro['id']}").status_code == 200
    # Um terceiro comum continua sem alcançar nada, nem pelo corpo do POST.
    terceiro = _cliente("regular", user_id=9)
    assert terceiro.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance"}).status_code == 403


def test_modelo_parcial_e_modelo_que_treina_todo_dia_nao_apagam_o_que_o_usuario_marcou(db):
    db.templates[7] = {"name": "Parcial", "slots": [(1, "normal"), (None, "descanso"), (3, "normal")]}
    dados = _cliente().get("/nutrition/cycle-days/from-template").json()
    assert dados["cycle_days"] is None and [n["code"] for n in dados["notes"]] == ["template_partial_weekdays"]
    db.templates[7] = {"name": "Full", "slots": [(i, "normal") for i in range(1, 8)]}
    dados = _cliente().get("/nutrition/cycle-days/from-template").json()
    assert dados["cycle_days"] is None and dados["cycles"] is False  # null: o front mantém o que estava marcado


def test_sql_dos_slots_junta_so_a_versao_vigente_do_mesmo_modelo():
    sql = plans_mod._TEMPLATE_SLOTS_SQL
    assert "s.template_id = v.current_template_id" in sql and "v.current_template_id = %s" in sql
