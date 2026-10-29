"""Fix M97 (2026-10-26): carga de FORÇA por %1RM e normalização do item ao
objetivo na geração da ficha (mapa §66.5).

Decisões do usuário:
A. item de força com e1RM confiável e atual do par = faixa
   `STRENGTH_LOAD_PCT_1RM_MIN`–`STRENGTH_LOAD_PCT_1RM_MAX` do e1RM, no passo do
   equipamento, com um valor sugerido (dono `progression_engine`); sem e1RM, a
   progressão dupla da F2 decide;
B. o Modelo guarda o que foi digitado; a GERAÇÃO ajusta descanso, reps, RPE,
   séries e técnica fora do perfil do objetivo (dono `prescription_defaults`),
   com aviso auditado no snapshot e no relatório.

Hipertrofia sai byte a byte igual (mesmos objetos, nenhuma leitura nova, nenhum
campo novo no snapshot).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain import prescription_defaults as pd
from app.domain import progression_engine as pe
from app.domain import training_safety as ts
from app.domain.prescription import WARNING_GOAL_NORMALIZED, WARNING_MODEL_TECHNIQUE_INVALID, build_prescription
from app.generation import deterministic_prescription as dp
from app.intelligence.dashboard import exposure_service as xs
from tests.support import progression_fixtures as fx

S = "strength"
H = "hypertrophy"
NOW = datetime(2026, 10, 26, 18, 0)
PEITO, PERNA, BICEPS = 10, 11, 12
SUPINO, AGACHAMENTO, ROSCA = 101, 102, 103
BANCO, RACK, POLIA = 201, 202, 203


# ---------------------------------------------------------------------------
# A. Faixa de carga por %1RM (dono `progression_engine`)
# ---------------------------------------------------------------------------


def test_constantes_e_regime_por_objetivo():
    assert (pe.STRENGTH_LOAD_PCT_1RM_MIN, pe.STRENGTH_LOAD_PCT_1RM_MAX) == (80, 85)
    assert pe.ladder_for(S).load_pct_1rm == (pe.STRENGTH_LOAD_PCT_1RM_MIN, pe.STRENGTH_LOAD_PCT_1RM_MAX)
    assert pe.HYPERTROPHY_LADDER.load_pct_1rm is None
    assert pe.uses_e1rm_load(S) and not pe.uses_e1rm_load(H) and not pe.uses_e1rm_load(None)


@pytest.mark.parametrize(
    ("e1rm", "passo", "faixa", "sugerido"),
    [
        (100, 2.5, (80.0, 85.0), 82.5),  # ponto medio 82,5 cabe no passo
        (100, 5, (80.0, 85.0), 80.0),  # 82,5 para BAIXO no passo de 5
        (123.33, 2.5, (100.0, 102.5), 100.0),  # piso para CIMA, teto para BAIXO
        (101, 1, (81.0, 85.0), 83.0),
        (20, 10, (10.0, 10.0), 10.0),  # passo nao cabe: multiplo logo abaixo, nunca acima do teto
    ],
)
def test_faixa_no_passo_do_equipamento(e1rm, passo, faixa, sugerido):
    faixa_carga = pe.e1rm_load_range(e1rm, passo, S)
    assert (faixa_carga.load_min_kg, faixa_carga.load_max_kg) == faixa
    assert faixa_carga.suggested_kg == sugerido
    assert faixa_carga.load_min_kg <= faixa_carga.suggested_kg <= faixa_carga.load_max_kg
    assert faixa_carga.load_max_kg <= e1rm * pe.STRENGTH_LOAD_PCT_1RM_MAX / 100
    assert (faixa_carga.pct_min, faixa_carga.pct_max) == (80.0, 85.0)


@pytest.mark.parametrize("valor", [None, 0, -5, "abc", True])
def test_sem_e1rm_valido_nao_ha_faixa(valor):
    assert pe.e1rm_load_range(valor, 2.5, S) is None


def test_hipertrofia_nao_tem_faixa():
    assert pe.e1rm_load_range(100, 2.5, H) is None and pe.e1rm_load_range(100, 2.5) is None


def _strength_track(**extra):
    base = fx.track(
        exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=95, is_compound=True, load_increment_kg=2.5
    )
    return replace(base, training_goal=S, **extra)


def _safety(exercise_id=SUPINO, equipment_id=BANCO):
    return fx.safety(exercise_id=exercise_id, equipment_id=equipment_id, max_safe_rpe=10)


def test_prescribe_forca_com_e1rm_usa_a_faixa_e_decide_pela_carga_da_trilha():
    grupo = pe.new_group_state(PEITO, NOW, S)
    decisao = pe.prescribe(_strength_track(), grupo, _safety(), now=NOW, current_e1rm_kg=123.33)
    assert decisao.suggested_load_kg == 100.0
    assert decisao.decision == pe.INCREASE_LOAD  # trilha em 95 kg
    assert decisao.evidence[pe.EVIDENCE_LOAD_REGIME] == pe.LOAD_REGIME_PCT_1RM
    assert decisao.evidence[pe.EVIDENCE_STRENGTH_E1RM_LOAD] == {
        "e1rm_kg": 123.33,
        "pct_min": 80.0,
        "pct_max": 85.0,
        "load_min_kg": 100.0,
        "load_max_kg": 102.5,
        "suggested_kg": 100.0,
        "step_kg": 2.5,
    }
    assert "80-85% do e1RM 123.33 kg" in decisao.reason
    # e1RM menor que o da trilha: reduz
    menor = pe.prescribe(_strength_track(), grupo, _safety(), now=NOW, current_e1rm_kg=100)
    assert (menor.suggested_load_kg, menor.decision) == (82.5, pe.DECREASE_LOAD)
    # mesma carga: mantem
    igual = pe.prescribe(_strength_track(working_load_kg=Decimal("82.5")), grupo, _safety(), now=NOW, current_e1rm_kg=100)
    assert (igual.suggested_load_kg, igual.decision) == (82.5, pe.HOLD)


def test_prescribe_forca_sem_e1rm_e_a_progressao_dupla_marcada():
    grupo = pe.new_group_state(PEITO, NOW, S)
    decisao = pe.prescribe(_strength_track(), grupo, _safety(), now=NOW)
    assert decisao.suggested_load_kg == 95.0
    assert decisao.evidence[pe.EVIDENCE_LOAD_REGIME] == pe.LOAD_REGIME_DOUBLE_PROGRESSION
    assert pe.EVIDENCE_STRENGTH_E1RM_LOAD not in decisao.evidence


def test_prescribe_forca_sem_carga_aplicavel_nao_usa_e1rm():
    grupo = pe.new_group_state(PEITO, NOW, S)
    track = _strength_track(load_status=pe.LOAD_NOT_APPLICABLE)
    decisao = pe.prescribe(track, grupo, _safety(), now=NOW, current_e1rm_kg=123.33)
    assert decisao.evidence[pe.EVIDENCE_LOAD_REGIME] == pe.LOAD_REGIME_DOUBLE_PROGRESSION


def test_prescribe_hipertrofia_ignora_o_e1rm_byte_a_byte():
    grupo = pe.new_group_state(PEITO, NOW)
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=40)
    sem = pe.prescribe(track, grupo, _safety(), now=NOW)
    com = pe.prescribe(track, grupo, _safety(), now=NOW, current_e1rm_kg=123.33)
    assert sem.to_dict() == com.to_dict()
    assert pe.EVIDENCE_LOAD_REGIME not in sem.evidence


# ---------------------------------------------------------------------------
# e1RM atual do par (leitura: `exposure_service`)
# ---------------------------------------------------------------------------


def test_e1rm_atual_e_o_confiavel_mais_recente_do_mesmo_equipamento():
    exposicoes = [
        {"equipment_id": BANCO, "_reliable_e1rm": 110.0},
        {"equipment_id": BANCO, "_reliable_e1rm": 120.0},
        {"equipment_id": BANCO, "_reliable_e1rm": None},  # mais recente sem e1RM confiavel: pula
        {"equipment_id": RACK, "_reliable_e1rm": 150.0},  # outro equipamento: fora
    ]
    assert xs.current_e1rm_from_exposures(exposicoes, BANCO) == 120.0
    assert xs.current_e1rm_from_exposures(exposicoes, POLIA) is None
    assert xs.current_e1rm_from_exposures(exposicoes, None) is None


def test_e1rm_atual_por_par_le_todos_os_objetivos_sem_contexto(monkeypatch):
    chamadas = []

    def fake_between(conn, exercise_id, **kwargs):
        chamadas.append((exercise_id, kwargs))
        return [{"equipment_id": BANCO, "_reliable_e1rm": 123.33}]

    monkeypatch.setattr(xs, "get_exposures_between", fake_between)
    resultado = xs.current_e1rm_by_pair(object(), 1, [(SUPINO, BANCO), (SUPINO, RACK), (ROSCA, None)], today=date(2026, 10, 26))
    assert resultado == {(SUPINO, BANCO): 123.33}
    assert len(chamadas) == 1
    exercicio, kwargs = chamadas[0]
    assert exercicio == SUPINO and kwargs["all_goals"] is True and kwargs["with_fatigue_context"] is False
    assert (kwargs["date_to"] - kwargs["date_from"]).days == xs.CURRENT_E1RM_LOOKBACK_DAYS


def test_geracao_so_le_e1rm_para_objetivo_com_regime_pct(monkeypatch):
    lidos = []
    monkeypatch.setattr(
        xs, "current_e1rm_by_pair", lambda conn, user_id, pairs, today: lidos.append(list(pairs)) or {}
    )
    hipertrofia = [dp.PairRequest(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)]
    assert dp.current_e1rm_for_requests(object(), 1, hipertrofia, NOW) == {}
    assert lidos == []  # hipertrofia: nenhuma leitura nova
    forca = hipertrofia + [dp.PairRequest(exercise_id=AGACHAMENTO, equipment_id=RACK, muscle_group_id=PERNA, training_goal=S)]
    dp.current_e1rm_for_requests(object(), 1, forca, NOW)
    assert lidos == [[(AGACHAMENTO, RACK)]]


def test_falha_na_leitura_do_e1rm_nao_derruba_a_geracao(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("data api")

    monkeypatch.setattr(xs, "current_e1rm_by_pair", boom)
    pedidos = [dp.PairRequest(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, training_goal=S)]
    assert dp.current_e1rm_for_requests(object(), 1, pedidos, NOW) == {}


# ---------------------------------------------------------------------------
# B. Normalização do item ao objetivo (dono `prescription_defaults`)
# ---------------------------------------------------------------------------

ITEM_TESTE_ZEPP = {
    "rest_seconds": 10,
    "reps_target": "6-12",
    "rpe_target": "9-10",
    "sets_count": 10,
    "advanced_technique": "gvt",
    "advanced_technique_applications": 2,
    "advanced_technique_duration_seconds": 20,
}


def test_hipertrofia_nao_normaliza_e_devolve_o_mesmo_objeto():
    item, ajustes = pd.normalize_item_for_goal(ITEM_TESTE_ZEPP, H)
    assert item is ITEM_TESTE_ZEPP and ajustes == ()
    assert pd.normalize_item_for_goal(ITEM_TESTE_ZEPP, None)[0] is ITEM_TESTE_ZEPP


def test_forca_normaliza_cada_campo_com_o_perfil_dos_donos():
    item, ajustes = pd.normalize_item_for_goal(ITEM_TESTE_ZEPP, S)
    perfil = pd.defaults_for(S)
    assert item["rest_seconds"] == perfil.min_rest_seconds
    assert item["reps_target"] == perfil.reps_target
    assert item["rpe_target"] == str(ts.goal_rpe_ceiling(S))
    assert item["sets_count"] == pe.STRENGTH_MAX_SETS_COUNT
    assert item["advanced_technique"] is None
    assert item["advanced_technique_applications"] is None and item["advanced_technique_duration_seconds"] is None
    assert [ajuste.field for ajuste in ajustes] == [
        pd.ADJUSTMENT_REST_SECONDS,
        pd.ADJUSTMENT_REPS_TARGET,
        pd.ADJUSTMENT_RPE_TARGET,
        pd.ADJUSTMENT_SETS_COUNT,
        pd.ADJUSTMENT_TECHNIQUE,
    ]
    assert ajustes[0].to_dict()["from"] == 10 and ajustes[0].to_dict()["to"] == perfil.min_rest_seconds
    # o item de origem (o Modelo) nao muda
    assert ITEM_TESTE_ZEPP["rest_seconds"] == 10 and ITEM_TESTE_ZEPP["advanced_technique"] == "gvt"


@pytest.mark.parametrize(
    ("descanso", "esperado"),
    [(15, pd.STRENGTH_MIN_REST_SECONDS), (600, pd.STRENGTH_MAX_REST_SECONDS), (240, 240), (None, None)],
)
def test_descanso_de_forca_e_cortado_na_faixa(descanso, esperado):
    item, _ = pd.normalize_item_for_goal({"rest_seconds": descanso}, S)
    assert item.get("rest_seconds") == esperado


def test_item_de_forca_dentro_do_perfil_nao_gera_ajuste():
    item = {"rest_seconds": 240, "reps_target": "3-5", "rpe_target": "8", "sets_count": 3, "advanced_technique": None}
    normalizado, ajustes = pd.normalize_item_for_goal(item, S)
    assert ajustes == () and normalizado == item


def test_reps_fora_de_repeticoes_nao_sao_normalizadas():
    item, ajustes = pd.normalize_item_for_goal({"reps_target": "12", "measure": "duration"}, S)
    assert item["reps_target"] == "12" and ajustes == ()


def test_texto_do_relatorio_interpola_os_numeros_do_perfil():
    _, ajustes = pd.normalize_item_for_goal(ITEM_TESTE_ZEPP, S)
    texto = pd.goal_adjustments_text("Supino", ajustes)
    perfil = pd.defaults_for(S)
    assert texto.startswith("Supino: ajustado ao objetivo na geração")
    assert f"{perfil.min_rest_seconds}-{perfil.max_rest_seconds} s" in texto
    assert "O modelo continua salvo" in texto
    assert pd.goal_adjustments_text("Supino", ()) is None


# ---------------------------------------------------------------------------
# Snapshot (dono `prescription`)
# ---------------------------------------------------------------------------


def test_snapshot_de_hipertrofia_nao_ganha_campo_novo():
    snapshot = build_prescription({"mapping_id": SUPINO, "rpe_target": "8"}, safety=_safety())
    assert "strength_load" not in snapshot and "goal_adjustments" not in snapshot
    assert WARNING_GOAL_NORMALIZED not in snapshot["warnings"]


def test_snapshot_de_forca_leva_a_faixa_e_os_ajustes():
    decisao = pe.prescribe(_strength_track(), pe.new_group_state(PEITO, NOW, S), _safety(), now=NOW, current_e1rm_kg=100)
    _, ajustes = pd.normalize_item_for_goal(ITEM_TESTE_ZEPP, S)
    snapshot = build_prescription(
        {"mapping_id": SUPINO, "training_goal": S, "rpe_target": "9"},
        safety=_safety(),
        decision=decisao.to_dict(),
        goal_adjustments=[ajuste.to_dict() for ajuste in ajustes],
    )
    assert snapshot["strength_load"]["suggested_kg"] == 82.5
    assert len(snapshot["goal_adjustments"]) == 5
    assert WARNING_GOAL_NORMALIZED in snapshot["warnings"]


# ---------------------------------------------------------------------------
# Ponta a ponta: o Modelo "teste zepp" (DEV, template 32) gerado e congelado
# pelo Modo B, com o motor real (`prescribe_pairs` -> `prescribe`).
# ---------------------------------------------------------------------------

MODELO_TESTE_ZEPP = {
    "id": 32,
    "name": "teste zepp",
    "training_goal": S,
    "template_type": "full_body",
    "description": None,
}
ITENS_TESTE_ZEPP = [
    # com e1RM confiavel: carga pela faixa de 80-85%
    {
        "item_kind": "equipment",
        "item_order": 1,
        "muscle_group_id": PEITO,
        "mapping_id": SUPINO,
        "equipment_id": BANCO,
        "sets_count": 4,
        "rest_seconds": 10,
        "reps_target": "10",
        "rpe_target": "9-10",
        "advanced_technique": "rest_pause",
        "advanced_technique_applications": 2,
        "training_goal": None,
    },
    # sem e1RM: progressao dupla (carga da trilha)
    {
        "item_kind": "equipment",
        "item_order": 2,
        "muscle_group_id": PERNA,
        "mapping_id": AGACHAMENTO,
        "equipment_id": RACK,
        "sets_count": 10,
        "rest_seconds": 60,
        "reps_target": "6-12",
        "rpe_target": "10",
        "advanced_technique": "gvt",
        "training_goal": None,
    },
    # override de HIPERTROFIA no Modelo de forca: intocado
    {
        "item_kind": "equipment",
        "item_order": 3,
        "muscle_group_id": BICEPS,
        "mapping_id": ROSCA,
        "equipment_id": POLIA,
        "sets_count": 2,
        "rest_seconds": 45,
        "reps_target": "10-12",
        "rpe_target": "9",
        "advanced_technique": "drop_set",
        "training_goal": H,
    },
]
E1RM_ATUAL = {(SUPINO, BANCO): 123.33}
NOMES = {SUPINO: "Supino", AGACHAMENTO: "Agachamento", ROSCA: "Rosca", PEITO: "Peito", PERNA: "Perna", BICEPS: "Bíceps"}


def _trilhas():
    supino = replace(_strength_track(), sets_count=4)
    agachamento = replace(
        fx.track(exercise_id=AGACHAMENTO, equipment_id=RACK, muscle_group_id=PERNA, load=80, is_compound=True),
        training_goal=S,
    )
    rosca = fx.track(exercise_id=ROSCA, equipment_id=POLIA, muscle_group_id=BICEPS, load=20)
    return {track.key: track for track in (supino, agachamento, rosca)}


@pytest.fixture
def motor_real(monkeypatch):
    """`prescribe_pairs` com o motor real e as leituras do banco trocadas."""
    from app import progression_state as ps
    from app.intelligence.dashboard import time_windows

    lidos: list = []
    monkeypatch.setattr(ps, "load_technique_catalog", lambda conn: fx.CATALOG)
    monkeypatch.setattr(
        ps,
        "load_primary_groups",
        lambda conn, ids: ({SUPINO: PEITO, AGACHAMENTO: PERNA, ROSCA: BICEPS}, {SUPINO: 1.0, AGACHAMENTO: 1.0, ROSCA: 1.0}),
    )
    monkeypatch.setattr(ps, "load_exercise_flags", lambda conn, ids: {SUPINO: True, AGACHAMENTO: True, ROSCA: False})
    monkeypatch.setattr(ps, "load_laboral_ids", lambda conn, ids: frozenset())
    monkeypatch.setattr(ps, "load_equipment_steps", lambda conn, ids: {BANCO: Decimal("2.5"), RACK: Decimal("5")})
    monkeypatch.setattr(ps, "load_user_flags", lambda conn, user_id: False)
    monkeypatch.setattr(ps, "load_all_group_states", lambda conn, user_id: {})
    monkeypatch.setattr(ps, "load_tracks", lambda conn, user_id: _trilhas())
    monkeypatch.setattr(
        dp,
        "resolve_training_safety_bulk",
        lambda conn, pairs: {pair: _safety(*pair) for pair in pairs},
    )
    monkeypatch.setattr(dp, "planned_fatigue_contexts", lambda conn, requests: list(requests))
    monkeypatch.setattr(time_windows, "user_timezone", lambda conn, user_id: None)
    monkeypatch.setattr(time_windows, "user_now", lambda tz: NOW)

    def fake_e1rm(conn, user_id, pairs, today):
        lidos.append(sorted(pairs))
        return {pair: value for pair, value in E1RM_ATUAL.items() if pair in set(pairs)}

    monkeypatch.setattr(xs, "current_e1rm_by_pair", fake_e1rm)
    return lidos


def _gerar_rascunho(monkeypatch):
    import app.generation as generation
    from app import workout_plan_ai
    from app.generation import mode_b
    from app.intelligence import volume_service

    @contextmanager
    def fake_transaction():
        yield object()

    monkeypatch.setattr(generation, "deterministic_enabled", lambda: True)
    monkeypatch.setattr(mode_b, "transaction", fake_transaction)
    monkeypatch.setattr(workout_plan_ai, "gather_ficha_context", lambda gym_id, user_id: {"gyms": [], "muscle_recovery": []})
    monkeypatch.setattr(workout_plan_ai, "resolve_gym_name", lambda gym_id, gym_name, gyms: gym_name)
    monkeypatch.setattr(workout_plan_ai, "project_recovery_for_date", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        mode_b,
        "resolve_item_equipment",
        lambda conn, item, gym_id, user_id=None, exclude_exercise_ids=(): mode_b.ResolvedEquipment(
            mapping_id=int(item["mapping_id"]),
            equipment_id=int(item["equipment_id"]),
            equipment_name=f"eq {item['equipment_id']}",
            preferred_equipment_id=int(item["equipment_id"]) + 1000,
        ),
    )
    monkeypatch.setattr(mode_b, "_load_names", lambda conn, table, ids: {i: NOMES.get(i) for i in ids})
    monkeypatch.setattr(mode_b, "_load_amazfit_names", lambda conn, ids: {})
    monkeypatch.setattr(mode_b, "load_laboral_defaults", lambda conn, ids: {})
    monkeypatch.setattr(
        volume_service.VolumeService, "get_muscle_activation_for_exercises", lambda self, payload: []
    )
    return mode_b.generate_plan_draft_from_equipment_template(
        template=MODELO_TESTE_ZEPP,
        slot={"id": 7, "name": "A", "sequence": 1, "slot_type": "treino", "items": [dict(i) for i in ITENS_TESTE_ZEPP]},
        gym_id=None,
        gym_name=None,
        plan_date="2026-10-27",
        user_id=1,
        min_rest_seconds=pd.DEFAULT_MIN_REST_SECONDS,
        max_rest_seconds=pd.DEFAULT_MAX_REST_SECONDS,
    )


def test_teste_zepp_rascunho_do_modo_b(monkeypatch, motor_real):
    rascunho = _gerar_rascunho(monkeypatch)
    supino, agachamento, rosca = rascunho["exercises"]
    perfil = pd.defaults_for(S)
    teto_rpe = ts.goal_rpe_ceiling(S)

    for linha in (supino, agachamento):
        assert linha["training_goal"] == S
        assert perfil.min_rest_seconds <= linha["rest_seconds"] <= perfil.max_rest_seconds
        assert linha["reps_target"] == perfil.reps_target
        assert int(str(linha["rpe_target"]).split("-")[-1]) <= teto_rpe
        assert linha["sets_count"] <= pe.STRENGTH_MAX_SETS_COUNT
        assert linha["advanced_technique"] is None and linha["advanced_technique_applications"] is None
    # carga pela faixa do e1RM (123,33 kg -> 100-102,5 kg, sugerido 100)
    assert supino["suggested_load_kg"] == 100.0
    # sem e1RM: a carga da trilha (progressao dupla)
    assert agachamento["suggested_load_kg"] == 80.0
    # hipertrofia (override do item): tudo como o Modelo pediu
    assert (rosca["rest_seconds"], rosca["sets_count"], rosca["advanced_technique"]) == (45, 2, "drop_set")
    assert rosca["training_goal"] == H
    # e1RM lido so dos pares de forca
    assert motor_real == [[(SUPINO, BANCO), (AGACHAMENTO, RACK)]]

    avisos = rascunho["raw_payload"]["volume_planning"]["warnings"]
    assert any(aviso.startswith("Supino: ajustado ao objetivo na geração") for aviso in avisos)
    assert any(aviso.startswith("Agachamento: ajustado ao objetivo na geração") for aviso in avisos)
    assert not any(aviso.startswith("Rosca: ajustado") for aviso in avisos)
    # a tecnica de forca sai pelo ajuste ao objetivo, nao como "tecnica invalida do equipamento"
    assert not any("não é válida para este equipamento" in aviso for aviso in avisos)
    auditoria = rascunho["raw_payload"]["generation_audit"]["goal_adjustments"]
    assert [entrada["exercise_id"] for entrada in auditoria] == [SUPINO, AGACHAMENTO]
    # o Modelo nao mudou
    assert ITENS_TESTE_ZEPP[0]["rest_seconds"] == 10 and ITENS_TESTE_ZEPP[1]["sets_count"] == 10


def test_teste_zepp_congelamento_do_modo_b(monkeypatch, motor_real):
    import app.generation as generation
    from app.generation import freeze_mode_b

    monkeypatch.setattr(generation, "deterministic_enabled", lambda: True)
    monkeypatch.setattr(
        freeze_mode_b,
        "_plan_context",
        lambda conn, plan_id: {
            "id": plan_id,
            "user_id": 1,
            "plan_date": date(2026, 10, 27),
            "plan_time": None,
            "template_id": 32,
            "items": [dict(item) for item in ITENS_TESTE_ZEPP],
        },
    )
    monkeypatch.setattr(freeze_mode_b, "equipment_by_gym_equipment", lambda conn, ids: {})
    monkeypatch.setattr(freeze_mode_b, "projected_readiness", lambda user_id, plan_date, plan_time: {})
    monkeypatch.setattr(
        freeze_mode_b,
        "coherent_equipment_id",
        lambda conn, by_gym, row, plan_id: int(row["preferred_equipment_id"]) - 1000,
    )
    # as linhas que o cliente salvou a partir do rascunho (ja normalizado)
    linhas = [
        {
            "workout_plan_exercise_id": 900 + index,
            "sequence": index,
            "mapping_id": item["mapping_id"],
            "preferred_equipment_id": item["equipment_id"] + 1000,
            "sets_count": min(item["sets_count"], pe.STRENGTH_MAX_SETS_COUNT) if index < 3 else item["sets_count"],
            "reps_target": "3-5" if index < 3 else item["reps_target"],
            "rpe_target": "8" if index < 3 else item["rpe_target"],
            "training_goal": S if index < 3 else H,
            "measure": None,
            "load_target": None,
        }
        for index, item in enumerate(ITENS_TESTE_ZEPP, start=1)
    ]
    monkeypatch.setattr(
        freeze_mode_b,
        "match_rows_to_model_items",
        lambda conn, rows, items: {row["workout_plan_exercise_id"]: dict(item) for row, item in zip(rows, items)},
    )
    snapshots = freeze_mode_b.freeze_mode_b(
        object(), 55, linhas, user_id=1, safety_by_row={}, source="PLAN_CREATED", frozen_at=NOW
    )
    supino, agachamento, rosca = (snapshots[900 + index] for index in (1, 2, 3))

    assert supino["strength_load"] == {
        "e1rm_kg": 123.33,
        "pct_min": 80.0,
        "pct_max": 85.0,
        "load_min_kg": 100.0,
        "load_max_kg": 102.5,
        "suggested_kg": 100.0,
        "step_kg": 2.5,
    }
    assert supino["suggested_load_kg"] == 100.0
    assert "strength_load" not in agachamento and agachamento["suggested_load_kg"] == 80.0
    for snapshot in (supino, agachamento):
        assert snapshot["training_goal"] == S
        assert snapshot["reps_target"] == "3-5"
        assert snapshot["effective_rpe_ceiling"] <= ts.goal_rpe_ceiling(S)
        assert snapshot["sets_count"] <= pe.STRENGTH_MAX_SETS_COUNT
        assert snapshot["advanced_technique"] is None
        assert WARNING_GOAL_NORMALIZED in snapshot["warnings"]
        assert WARNING_MODEL_TECHNIQUE_INVALID not in snapshot["warnings"]
        campos = {ajuste["field"] for ajuste in snapshot["goal_adjustments"]}
        assert {pd.ADJUSTMENT_REST_SECONDS, pd.ADJUSTMENT_REPS_TARGET, pd.ADJUSTMENT_RPE_TARGET} <= campos
        assert pd.ADJUSTMENT_TECHNIQUE in campos
    assert pd.ADJUSTMENT_SETS_COUNT in {ajuste["field"] for ajuste in agachamento["goal_adjustments"]}
    # hipertrofia: snapshot sem campo novo, tecnica do Modelo mantida
    assert "goal_adjustments" not in rosca and "strength_load" not in rosca
    assert rosca["advanced_technique"] == "drop_set"
    assert WARNING_GOAL_NORMALIZED not in rosca["warnings"]


# ---------------------------------------------------------------------------
# Decisões do Tech Lead (2026-10-26): deload de força pela faixa %1RM e a
# sugestão do dashboard com `goal=strength`.
# ---------------------------------------------------------------------------


def test_deload_de_forca_parte_do_sugerido_da_faixa_quando_ha_e1rm():
    from app.domain import deload

    faixa = pe.e1rm_load_range(123.33, 2.5, S).to_dict()  # sugerido 100 kg
    com_faixa = deload.item_deload_prescription(
        track_working_load_kg=80,
        snapshot={"training_goal": S, "strength_load": faixa, "sets_count": 3, "reps_target": "3-5"},
        step_kg=2.5,
    )
    assert com_faixa.working_load_kg == faixa["suggested_kg"]
    assert com_faixa.load_kg == pe.round_to_equipment_step(
        Decimal(str(faixa["suggested_kg"])) * deload.DELOAD_LOAD_FACTOR, 2.5
    )
    # sem e1RM (snapshot sem faixa): a carga da trilha, como antes
    sem_faixa = deload.item_deload_prescription(
        track_working_load_kg=80, snapshot={"training_goal": S, "sets_count": 3}, step_kg=2.5
    )
    assert sem_faixa.working_load_kg == 80
    # hipertrofia (nunca tem faixa): identica a de antes
    hipertrofia = deload.item_deload_prescription(
        track_working_load_kg=40, snapshot={"sets_count": 3, "reps_target": "8-12"}, step_kg=2.5
    )
    assert hipertrofia.working_load_kg == 40


@pytest.mark.parametrize("bloco", [None, {}, {"suggested_kg": None}, {"suggested_kg": 0}, "x"])
def test_sugerido_da_faixa_tolerante(bloco):
    assert pe.e1rm_load_suggested_kg(bloco) is None


def test_sugestao_do_dashboard_com_goal_forca_sai_com_a_faixa(motor_real):
    from app.domain import prescription_service as svc

    forca = svc.suggest_for_pair(object(), 1, SUPINO, BANCO, readiness={}, logs=[], now=NOW, training_goal=S)
    assert forca.suggested_load_kg == 100.0
    assert forca.strength_load == pe.e1rm_load_range(123.33, 2.5, S).to_dict()
    hipertrofia = svc.suggest_for_pair(object(), 1, ROSCA, POLIA, readiness={}, logs=[], now=NOW)
    assert hipertrofia.strength_load is None


def test_rota_da_sugestao_repassa_a_faixa_so_quando_existe(monkeypatch):
    from app import services
    from app.domain import prescription_service as svc
    from tests.support.fake_db import CursorFactoryConn

    class _Cursor:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            return None

        def fetchone(self):
            return {"id": SUPINO}

    @contextmanager
    def fake_transaction():
        yield CursorFactoryConn(_Cursor, None)

    faixa = pe.e1rm_load_range(123.33, 2.5, S).to_dict()

    def fake_suggest(conn, user_id, exercise_id, equipment_id=None, **kwargs):
        evidence = {pe.EVIDENCE_STRENGTH_E1RM_LOAD: faixa} if kwargs.get("training_goal") == S else {}
        return svc.PairSuggestion(
            exercise_id=SUPINO,
            equipment_id=BANCO,
            decision={"decision": pe.HOLD, "evidence": evidence, "suggested_load_kg": 100.0},
            suggested_load_kg=100.0,
            load_source=svc.LOAD_SOURCE_ENGINE,
            load_increment_kg=2.5,
        )

    monkeypatch.setattr(services, "transaction", fake_transaction)
    monkeypatch.setattr(services, "_resolve_canonical_exercise_id", lambda mapping_id, cur: SUPINO)
    monkeypatch.setattr(services, "list_exercise_logs", lambda limit, user_id, only_mapping_id: [])
    monkeypatch.setattr(svc, "suggest_for_pair", fake_suggest)

    assert services.suggested_progression_for_mapping(SUPINO, user_id=1, training_goal=S)["strength_load"] == faixa
    assert "strength_load" not in services.suggested_progression_for_mapping(SUPINO, user_id=1)
