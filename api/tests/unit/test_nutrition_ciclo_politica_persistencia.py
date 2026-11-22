# ruff: noqa: F811
"""Política do ciclo de carbo (#242) na persistência, nas rotas, no check-in, no dia materializado e no export.

Banco em memória: `tests/support/nutrition_fake.py` (mesmos fakes de `test_nutrition_plano_autogerido_rotas.py`)."""

from __future__ import annotations

import json
import logging

import pytest

from app import nutrition_targets_service as svc
from app.domain import nutrition as nutrition_pkg
from app.nutrition_schema import reset_nutrition_tables_cache
from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import weekly_checkin as wc
from tests.unit.test_nutrition_plano_autogerido_rotas import (  # noqa: F401  (fixtures `db`/`hoje` e pytestmark)
    ALUNO, DIAS_ALTOS, MANUAL, SEGUNDA, _cliente, _codigos, _criar, db, hoje, pytestmark,
)

COMP = cc.CarbCyclePolicy.FAT_COMPENSATION.value


def _put(cliente, plano_id, **corpo):
    return cliente.put(f"/nutrition/plans/{plano_id}", json={"strategy": "maintenance", **corpo})


def _linha(db, **extra):
    return {**db.plans[db.add_plan(ALUNO)], **extra}


# --- gravação e leitura -----------------------------------------------------------------------------------------


def test_padrao_grava_o_mesmo_json_de_antes_e_o_modo_novo_grava_a_chave(db):
    padrao = _criar(_cliente(), cycle_days=DIAS_ALTOS).json()
    assert db.plans[padrao["id"]]["carb_cycling_json"] == DIAS_ALTOS
    assert db.plans[padrao["id"]]["carb_cycle_policy"] == "vary_kcal"  # a coluna recebe sempre o código explícito
    novo = _criar(_cliente(), cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP)
    assert novo.status_code == 201, novo.text
    assert db.plans[novo.json()["id"]]["carb_cycling_json"] == {**DIAS_ALTOS, "policy": COMP}
    assert db.plans[novo.json()["id"]]["carb_cycle_policy"] == COMP
    assert novo.json()["carb_cycle_policy"] == COMP and novo.json()["cycle_days"] == DIAS_ALTOS


def test_leitura_legado_e_padrao_e_json_com_chave_e_modo_novo(db):
    legado = svc.settings_from_row(_linha(db, carb_cycling_json=json.dumps(DIAS_ALTOS)))
    assert legado.carb_cycle_policy == cc.DEFAULT_CARB_CYCLE_POLICY and legado.cycle_days == DIAS_ALTOS
    novo = svc.settings_from_row(_linha(db, carb_cycling_json=json.dumps({**DIAS_ALTOS, "policy": COMP})))
    assert novo.carb_cycle_policy.value == COMP and novo.cycle_days == DIAS_ALTOS


def test_coluna_prevalece_e_nula_cai_na_chave_do_json(db):
    json_comp = json.dumps({**DIAS_ALTOS, "policy": COMP})
    assert svc.settings_from_row(_linha(db, carb_cycling_json=json_comp, carb_cycle_policy="vary_kcal")).carb_cycle_policy == cc.DEFAULT_CARB_CYCLE_POLICY
    assert svc.settings_from_row(_linha(db, carb_cycling_json=json_comp, carb_cycle_policy=None)).carb_cycle_policy.value == COMP
    sem_ciclo = svc.settings_from_row(_linha(db, carb_cycle_policy=COMP))
    assert sem_ciclo.carb_cycle_policy.value == COMP and sem_ciclo.cycle_days is None


@pytest.mark.parametrize("extra", [{"carb_cycle_policy": "xyz"}, {"carb_cycling_json": {**DIAS_ALTOS, "policy": "xyz"}}])
def test_policy_desconhecida_gravada_cai_no_padrao_sem_quebrar_os_dias_e_avisa(db, caplog, extra):
    caplog.set_level(logging.WARNING)
    s = svc.settings_from_row(_linha(db, **{"carb_cycling_json": DIAS_ALTOS, **extra}))
    assert s.carb_cycle_policy == cc.DEFAULT_CARB_CYCLE_POLICY and s.cycle_days == DIAS_ALTOS
    avisos = [r.getMessage() for r in caplog.records if "carb_cycle_policy" in r.getMessage()]
    assert len(avisos) == 1 and f"plan_id={db.next_plan_id - 1}" in avisos[0] and f"user_id={ALUNO}" in avisos[0]
    assert "xyz" not in avisos[0]


def test_politica_valida_ou_ausente_nao_avisa(db, caplog):
    caplog.set_level(logging.WARNING)
    svc.settings_from_row(_linha(db, carb_cycling_json=DIAS_ALTOS, carb_cycle_policy=COMP))
    svc.settings_from_row(_linha(db, carb_cycling_json=DIAS_ALTOS))
    assert not [r for r in caplog.records if "carb_cycle_policy" in r.getMessage()]


def test_join_e_split_sao_inversos():
    assert cc.split_stored_cycle(cc.join_stored_cycle(DIAS_ALTOS, cc.CarbCyclePolicy.FAT_COMPENSATION)) == (DIAS_ALTOS, COMP)
    assert cc.split_stored_cycle(DIAS_ALTOS) == (DIAS_ALTOS, None)
    assert cc.join_stored_cycle(None, cc.CarbCyclePolicy.FAT_COMPENSATION) is None


# --- ida e volta: criar, editar -----------------------------------------------------------------------------------


def test_edicao_omitido_herda_null_volta_ao_padrao_explicito_troca(db):
    cliente = _cliente()
    pid = _criar(cliente, cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP).json()["id"]
    herdou = _put(cliente, pid, cycle_days=DIAS_ALTOS)
    assert herdou.status_code == 200 and herdou.json()["carb_cycle_policy"] == COMP
    assert db.plans[pid]["carb_cycling_json"]["policy"] == COMP
    nulo = _put(cliente, pid, cycle_days=DIAS_ALTOS, carb_cycle_policy=None)
    assert nulo.json()["carb_cycle_policy"] == "vary_kcal" and db.plans[pid]["carb_cycling_json"] == DIAS_ALTOS
    assert _put(cliente, pid, cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP).json()["carb_cycle_policy"] == COMP
    padrao = _put(cliente, pid, cycle_days=DIAS_ALTOS, carb_cycle_policy="vary_kcal")
    assert padrao.json()["carb_cycle_policy"] == "vary_kcal" and db.plans[pid]["carb_cycling_json"] == DIAS_ALTOS


def test_desligar_o_ciclo_guarda_a_politica_na_coluna_e_religar_devolve(db):
    cliente = _cliente()
    pid = _criar(cliente, cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP).json()["id"]
    r = _put(cliente, pid)  # omitido, sem ciclo: herda o guardado
    assert r.status_code == 200 and r.json()["carb_cycle_policy"] == COMP and r.json()["cycle_days"] is None
    assert db.plans[pid]["carb_cycling_json"] is None and db.plans[pid]["carb_cycle_policy"] == COMP
    assert _put(cliente, pid, cycle_days=DIAS_ALTOS).json()["carb_cycle_policy"] == COMP  # religar
    assert _put(cliente, pid, carb_cycle_policy=None).json()["carb_cycle_policy"] == "vary_kcal"  # null volta ao padrão
    assert db.plans[pid]["carb_cycle_policy"] == "vary_kcal" and db.plans[pid]["carb_cycling_json"] is None
    assert _put(cliente, pid).json()["carb_cycle_policy"] == "vary_kcal"  # a reserva do JSON não ressuscita nada


def test_ciclo_vazio_ou_so_normal_tambem_herda_a_politica(db):
    cliente = _cliente()
    pid = _criar(cliente, cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP).json()["id"]
    for sem_ciclo in ({}, {"0": "normal"}):
        r = _put(cliente, pid, cycle_days=sem_ciclo)
        assert r.status_code == 200, r.text
        assert r.json()["carb_cycle_policy"] == COMP and db.plans[pid]["carb_cycle_policy"] == COMP
    r = _put(cliente, pid, cycle_days={}, carb_cycle_policy=COMP)
    assert r.status_code == 200 and r.json()["carb_cycle_policy"] == COMP


def test_politica_sem_ciclo_e_aceita_e_valor_desconhecido_e_recusado_com_rotulo(db):
    novo = _criar(_cliente(), carb_cycle_policy=COMP)
    assert novo.status_code == 201, novo.text
    linha = db.plans[novo.json()["id"]]
    assert linha["carb_cycle_policy"] == COMP and linha["carb_cycling_json"] is None
    ruim = _criar(_cliente(), cycle_days=DIAS_ALTOS, carb_cycle_policy="xyz")
    assert ruim.status_code == 400 and "invalid_code:carb_cycle_policy" in _codigos(ruim)
    assert all(i["label"] for i in ruim.json()["detail"]["errors"])


# --- coluna ausente (código novo sem a migração 123) --------------------------------------------------------------


def test_coluna_ausente_leitura_e_escrita_seguem_pela_chave_do_json(db):
    cliente = _cliente()
    pid = _criar(cliente, cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP).json()["id"]
    db.plans[pid].pop("carb_cycle_policy")
    db.plan_policy_column = False
    reset_nutrition_tables_cache()  # o cache positivo é global: sem limpar, uma coluna "já vista" esconderia a ausência
    resp = cliente.get("/nutrition/plans")
    assert resp.status_code == 200, resp.text
    lido = next(i for i in resp.json()["items"] if i["id"] == pid)
    assert lido["carb_cycle_policy"] == COMP  # reserva
    novo = _criar(cliente, cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP)
    assert novo.status_code == 201 and db.plans[novo.json()["id"]]["carb_cycle_policy"] is None
    assert db.plans[novo.json()["id"]]["carb_cycling_json"] == {**DIAS_ALTOS, "policy": COMP}
    assert _put(cliente, pid, cycle_days=DIAS_ALTOS).json()["carb_cycle_policy"] == COMP
    assert db.plans[pid]["carb_cycling_json"]["policy"] == COMP and "carb_cycle_policy" not in db.plans[pid]
    # Metas publicadas: o leitor do diário também usa a variante sem a coluna.
    assert svc.load_published_plans(db.conn().cursor(), ALUNO).refs == ()


# --- viabilidade: prévia e salvar ---------------------------------------------------------------------------------


def test_combinacao_inviavel_400_rotulado_e_viavel_salva(db):
    cliente = _cliente()
    viavel = _criar(cliente, cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP, manual_targets=MANUAL)
    assert viavel.status_code == 201, viavel.text
    inviavel = {"cycle_days": DIAS_ALTOS, "carb_cycle_policy": COMP, "manual_targets": {"protein_g": 160, "carb_g": 280, "fat_g": 45}}
    salvar = _criar(cliente, **inviavel)
    assert salvar.status_code == 400, salvar.text
    assert salvar.json()["detail"]["error"] == "invalid_plan" and all(i["label"] for i in salvar.json()["detail"]["errors"])
    previa = cliente.post("/nutrition/plan-preview", json={"strategy": "maintenance", **inviavel})
    assert previa.status_code == 200 and previa.json()["can_publish"] is False
    assert previa.json()["blocking"] and all(i["label"] for i in previa.json()["blocking"])


# --- check-in ----------------------------------------------------------------------------------------------------


def test_checkin_preserva_a_politica(db):
    s = svc.settings_from_row(_linha(db, carb_cycling_json={**DIAS_ALTOS, "policy": COMP}))
    assert wc._builder_args(s)["cycle_policy"] == COMP
    aplicado = wc.apply_changes(s, {})
    assert aplicado.settings.carb_cycle_policy.value == COMP and aplicado.settings.cycle_days == DIAS_ALTOS


# --- dia materializado ------------------------------------------------------------------------------------------


def test_dia_materializado_e_previa_trazem_a_politica_e_dia_antigo_e_vary_kcal(db, hoje):
    hoje["dia"] = SEGUNDA
    cliente = _cliente()
    plano = _criar(cliente, cycle_days=DIAS_ALTOS, carb_cycle_policy=COMP).json()
    publicado = cliente.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None}).json()
    dias = publicado["targets"]
    assert dias and all(d["carb_cycle_policy"] == COMP and d["day_kind"] in ("high", "low", "normal") for d in dias)
    # Dia antigo, sem a chave no JSON gravado.
    linha = db.targets[(ALUNO, SEGUNDA)]
    linha["inputs_json"] = {k: v for k, v in linha["inputs_json"].items() if k != "carb_cycle_policy"}
    cur = db.conn().cursor()
    cur.execute(svc._TARGETS_SQL, (ALUNO, SEGUNDA, SEGUNDA))
    assert svc.target_row_public(cur.fetchone(), SEGUNDA)["carb_cycle_policy"] == "vary_kcal"
    previa = cliente.post("/nutrition/plan-preview", json={"strategy": "maintenance", "cycle_days": DIAS_ALTOS, "carb_cycle_policy": COMP}).json()
    assert all(d["carb_cycle_policy"] == COMP for d in previa["week"]) and previa["target"]["carb_cycle_policy"] == COMP


# --- export ----------------------------------------------------------------------------------------------------


def test_export_traz_politicas_padrao_rotulos_e_dicas():
    e = nutrition_pkg.export_for_frontend()
    assert e["carb_cycle_policies"] == ["vary_kcal", COMP] and e["default_carb_cycle_policy"] == "vary_kcal"
    assert set(e["carb_cycle_policy_labels"]) == set(e["carb_cycle_policy_hints"]) == set(e["carb_cycle_policies"])
    assert all(e["carb_cycle_policy_labels"].values()) and all(e["carb_cycle_policy_hints"].values())
