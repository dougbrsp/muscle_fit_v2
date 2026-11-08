"""Sombra do gasto adaptativo (`app/nutrition_energy_service.py`) e o bloco `adaptive_energy` de `GET /nutrition/dashboard`.

Só leitura: nada é gravado, nenhuma meta muda. Cobre o serviço (horizonte, dia confirmado, força sem dupla contagem,
TMB gravada, SQL sem `raw_json`, escopo por usuário, isolamento de falha, contagem de SELECTs), a rota (bloco presente,
indisponível sem dados, falha isolada, usuário comum × admin vendo outro), os rótulos e a prévia do plano (`tdee_breakdown`,
`extra_activity`, `tdee_basis`). Banco em memória: `tests/support/nutrition_fake.py` (projeta só as colunas da SQL)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import nutrition_adjustments as adj
from app import nutrition_dashboard as dash_mod
from app import nutrition_days as days_mod
from app import nutrition_energy_service as svc_energy
from app import nutrition_meals as meals_mod
from app import nutrition_plans as plans_mod
from app import nutrition_targets_service as svc
from app.domain.nutrition import activity_energy as ae
from app.load_conventions import _CURRENT_SCHEMA_SQL
from app.domain.nutrition import adaptive_energy as adaptive
from app.domain.nutrition import labels as nutrition_labels
from app.domain.nutrition.subject import Reading, SubjectSeries
from app.security import Identity, get_current_identity
from tests.support.nutrition_dashboard_fake import FakeDashboardDB

HOJE = date(2026, 11, 5)  # quinta-feira
ALUNO, OUTRO, ADMIN = 2, 4, 1
FUSO = "America/Sao_Paulo"
# 09:00 UTC = 06:00 em São Paulo: depois de 05:00 do dia 05, confirma o dia 04; o dia 05 (hoje) segue aberto.
SYNC_UTC = datetime(2026, 11, 5, 9, 0)
INICIO_FORCA = datetime(2026, 11, 4, 18, 0)
MACROS = {"energy_kcal": 2400.0, "protein_g": 150.0, "carbohydrates_g": 280.0, "total_fat_g": 80.0, "fiber_g": 30.0}


def _refeicao(db, user_id: int, dia: date, **valores) -> None:
    linha = {c: Decimal(str(MACROS.get(c, 0))) for c in meals_mod.NUTRIENTS}
    linha.update({c: Decimal(str(v)) for c, v in valores.items()})
    ident = db._novo_id()
    linha.update(id=ident, user_id=user_id, local_date=dia, totals_incomplete_json="[]", totals_nature="derived",
                 item_count=1)
    db.meals[ident] = linha


def _historico(db, user_id: int = ALUNO, dias: int = 30) -> None:
    """`dias` dias fechados com refeição completa e pesagem diária com %G (peso levemente em queda)."""
    for i in range(1, dias + 1):
        dia = HOJE - timedelta(days=i)
        _refeicao(db, user_id, dia)
        db.add_body(user_id, dia, weight=80.0 - (dias - i) * 0.02, body_fat=20.0)


@pytest.fixture
def db(monkeypatch):
    banco = FakeDashboardDB()
    for modulo in (dash_mod, meals_mod, days_mod):
        monkeypatch.setattr(modulo, "get_conn", banco.conn)
    for modulo in (meals_mod, dash_mod, days_mod):
        monkeypatch.setattr(modulo, "require_diary_tables", lambda cur: None)
    for modulo in (dash_mod, days_mod):
        monkeypatch.setattr(modulo, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(dash_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(dash_mod, "_timezone_for", lambda conn, user_id: FUSO)
    monkeypatch.setattr(days_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(plans_mod, "get_conn", banco.conn)
    monkeypatch.setattr(plans_mod, "_today_for", lambda conn, user_id: HOJE)
    monkeypatch.setattr(plans_mod, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(svc, "has_body_measurements_table", lambda cur: True)
    return banco


def _client(role: str = "regular", user_id: int = ALUNO) -> TestClient:
    app = FastAPI()
    for modulo in (dash_mod, meals_mod, days_mod, plans_mod):
        app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _series(db, user_id: int = ALUNO) -> SubjectSeries:
    entradas = svc.load_user_inputs(db.conn().cursor(), user_id, HOJE, HOJE, HOJE)
    return entradas.series


def _bloco(db, user_id: int = ALUNO) -> dict:
    return svc_energy.shadow_block(db.conn().cursor(), user_id, HOJE, _series(db, user_id), FUSO)


# ---------------------------------------------------------------------------
# Serviço
# ---------------------------------------------------------------------------


def test_o_sql_de_refeicoes_e_o_mesmo_do_dashboard_e_o_de_atividades_nao_traz_raw_json_nem_vinculo():
    assert svc_energy.MEAL_TOTALS_SQL == dash_mod.MEAL_TOTALS_SQL
    assert "raw_json" not in svc_energy.SHADOW_ACTIVITIES_SQL
    assert "activity_intervals_link" not in svc_energy.SHADOW_ACTIVITIES_SQL
    assert "user_id = %s" in svc_energy.SHADOW_ACTIVITIES_SQL and "user_id = %s" in svc_energy.INTERVALS_SYNC_SQL
    assert "user_id = %s" in svc_energy.TARGET_BMR_SQL and "inputs_json" not in svc_energy.TARGET_BMR_SQL


def test_horizonte_e_o_do_motor_terminando_hoje():
    dias = svc_energy.horizon_days(HOJE)
    assert len(dias) == adaptive.REPLAY_HISTORY_DAYS and dias[-1] == HOJE and dias[0] == HOJE - timedelta(days=89)


def test_sem_nenhum_dado_a_sombra_fica_indisponivel_com_motivo_e_rotulo(db):
    bloco = _bloco(db)
    assert bloco["available"] is False and bloco["applied"] is False and bloco["state"] is None
    assert bloco["unavailable_reason"] == nutrition_labels.ADAPTIVE_UNAVAILABLE_NO_DATA
    assert bloco["unavailable_reason_label"] and bloco["days"] == []


def test_bloco_com_historico_e_atividades_normaliza_confirma_e_nao_conta_a_forca_duas_vezes(db):
    _historico(db)
    # Treino do app (`activities`) vinculado a UMA atividade do Intervals: só a do Intervals pode contar.
    db.add_activity(ALUNO, HOJE - timedelta(days=1), kcal=428.0, intervals_kcal=428.0)
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=1), "WeightTraining", 428.0, start=INICIO_FORCA, name="Musculação",
                         elapsed=3600, synced_at=SYNC_UTC)
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=20), "Run", 300.0, start=datetime(2026, 10, 16, 7, 0),
                         name="Corrida", synced_at=SYNC_UTC)
    db.writes.clear()
    bloco = _bloco(db)
    assert db.writes == []  # só leitura
    assert bloco["available"] is True and bloco["applied"] is False
    assert bloco["algorithm_version"] == adaptive.ALGORITHM_VERSION and bloco["unavailable_reason"] is None
    estado = bloco["state"]
    assert estado["date"] == HOJE.isoformat() and estado["confidence_level"] in {"low", "medium", "high"}
    assert "weight_slope_kg_per_week" in estado and estado["valid_days"] >= 14
    dias = bloco["days"]
    assert [d["day"] for d in dias] == [(HOJE - timedelta(days=i)).isoformat() for i in range(7)]  # mais recente primeiro
    hoje, ontem = dias[0], dias[1]
    assert hoje["confirmed"] is False and hoje["active_kcal"] is None  # dia aberto: pendente, nunca zero
    assert ontem["confirmed"] is True and ontem["counted_activities"] == 1
    atividade = ontem["activities"][0]
    assert atividade["kind"] == "strength" and atividade["calorie_mode"] == "unknown"
    assert atividade["flags"] == ["reduced_precision"] and ontem["reduced_precision"] is True
    assert atividade["reported_kcal"] == 428.0 and 0 < atividade["active_kcal"] < 428.0  # descontou o repouso
    assert ontem["active_kcal"] == atividade["active_kcal"]
    assert dias[2]["confirmed"] is True and dias[2]["active_kcal"] == 0.0  # descanso confirmado
    json.dumps(bloco)  # serializável


def test_usa_so_as_linhas_do_aluno_alvo(db):
    _historico(db)
    db.add_intervals_row(OUTRO, HOJE - timedelta(days=1), "WeightTraining", 999.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    bloco = _bloco(db)
    ontem = bloco["days"][1]
    assert ontem["confirmed"] is False and ontem["activities"] == []  # sem sync nem atividade do ALUNO
    outro = _bloco(db, OUTRO)
    assert outro["days"][1]["counted_activities"] == 1 if outro["available"] else True


def _sem_registro_do_sync(consultas: list[str]) -> list[str]:
    """Tira as consultas da 107 e da 108 (tabelas, schema, execuções do sync, fechamentos e correções): o núcleo não muda."""
    return [c for c in consultas if c != svc_energy.SYNC_RUNS_SQL and "information_schema" not in c
            and c != _CURRENT_SCHEMA_SQL and c not in (adj.CLOSURES_SQL, adj.CORRECTIONS_SQL)]


def test_sem_atividade_importada_o_dia_nao_e_confirmado_e_so_ha_tres_selects(db):
    _historico(db)
    consultas: list[str] = []
    cursor = db.conn().cursor()
    original = cursor.execute
    cursor.execute = lambda sql, params=None: (consultas.append(sql), original(sql, params))[1]  # type: ignore[method-assign]
    bloco = svc_energy.shadow_block(cursor, ALUNO, HOJE, _series(db), FUSO)
    # refeições, TMB gravada e prova de sincronização (sem atividades, sem 4ª consulta); fora a leitura das execuções do sync
    assert len(_sem_registro_do_sync(consultas)) == 3
    assert sum(1 for c in consultas if c == svc_energy.SYNC_RUNS_SQL) == 1
    # Lote G (108): 1 SELECT de fechamentos e 1 de correções por carga do histórico
    assert [consultas.count(adj.CLOSURES_SQL), consultas.count(adj.CORRECTIONS_SQL)] == [1, 1]
    assert all(not d["confirmed"] for d in bloco["days"])
    assert svc_energy.SHADOW_ACTIVITIES_SQL not in consultas


def test_com_atividades_sao_quatro_selects(db):
    _historico(db)
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=1), "Run", 200.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    consultas: list[str] = []
    cursor = db.conn().cursor()
    original = cursor.execute
    cursor.execute = lambda sql, params=None: (consultas.append(sql), original(sql, params))[1]  # type: ignore[method-assign]
    svc_energy.shadow_block(cursor, ALUNO, HOJE, _series(db), FUSO)
    assert len(_sem_registro_do_sync(consultas)) == 4


def test_tmb_gravada_na_meta_do_dia_tem_prioridade_sobre_a_do_sujeito():
    series = SubjectSeries(sex="male", birth_date=date(1990, 1, 1), height_cm=180,
                           weight=(Reading(80.0, HOJE - timedelta(days=60)),))
    gravada = svc_energy.ShadowRows(target_bmr={HOJE: 2500.0})
    estado, _ = svc_energy.compute_shadow(gravada, series, HOJE, FUSO)
    assert estado is not None and estado.bmr_kcal == 2500.0
    sem_gravada, _ = svc_energy.compute_shadow(svc_energy.ShadowRows(), series, HOJE, FUSO)
    assert sem_gravada is not None and sem_gravada.bmr_kcal not in (None, 2500.0)  # a do sujeito do dia (Mifflin)


def test_raw_activity_usa_decorrido_com_reserva_no_movimento_e_classifica_pelo_dono():
    linha = {"id": 7, "external_id": "i7", "activity_date": date(2026, 11, 3), "start_time": datetime(2026, 11, 3, 23, 30),
             "name": "Treino", "sport_type": "WeightTraining", "moving_time_seconds": 1500, "elapsed_time_seconds": 3600,
             "calories_kcal": Decimal("428.00")}
    bruta = svc_energy.raw_activity_of(linha)
    assert bruta.duration_seconds == 3600 and bruta.kind == "strength" and bruta.calories_kcal == 428.0
    assert bruta.start == datetime(2026, 11, 3, 23, 30) and bruta.calorie_mode == "unknown" and bruta.external_id == "i7"
    sem_decorrido = svc_energy.raw_activity_of({**linha, "elapsed_time_seconds": None})
    assert sem_decorrido.duration_seconds == 1500
    sem_hora = svc_energy.raw_activity_of({**linha, "start_time": None})
    assert sem_hora.start == datetime(2026, 11, 3, 0, 0)
    assert svc_energy.raw_activity_of({**linha, "activity_date": None}) is None


def test_falha_na_sombra_vira_indisponivel_e_o_log_so_traz_o_tipo_do_erro(db, monkeypatch, caplog):
    def quebra(*args, **kwargs):
        raise RuntimeError("senha=segredo valor=123")

    monkeypatch.setattr(svc_energy, "load_shadow_rows", quebra)
    with caplog.at_level(logging.WARNING):
        bloco = _bloco(db)
    assert bloco["available"] is False and bloco["unavailable_reason"] == nutrition_labels.ADAPTIVE_UNAVAILABLE_ERROR
    assert bloco["state"] is None and bloco["days"] == []
    assert "RuntimeError" in caplog.text and "segredo" not in caplog.text
    assert "at=test_nutrition_energy_service.py:" in caplog.text  # o ponto do erro, sem a mensagem


# ---------------------------------------------------------------------------
# Rótulos (todo código do dono tem texto pt-BR)
# ---------------------------------------------------------------------------


def test_todo_codigo_do_dono_tem_rotulo():
    from app.domain.nutrition import weight_trend

    razoes = {v for k, v in vars(adaptive).items() if k.startswith("REASON_") and isinstance(v, str)}
    marcas = {v for k, v in vars(adaptive).items() if k.startswith("FLAG_") and isinstance(v, str)} | {
        v for k, v in vars(weight_trend).items() if k.startswith("FLAG_") and isinstance(v, str)
    }
    assert razoes == set(nutrition_labels.SUSPENSION_REASON_LABELS)
    assert marcas == set(nutrition_labels.DATA_QUALITY_FLAG_LABELS)
    assert set(nutrition_labels.CONFIDENCE_LEVEL_LABELS) == set(adaptive.ConfidenceLevel)
    assert set(nutrition_labels.CALORIE_MODE_LABELS) == set(ae.CalorieMode)
    assert set(nutrition_labels.ACTIVITY_FLAG_LABELS) == set(ae.ActivityFlag)
    todos = [*nutrition_labels.SUSPENSION_REASON_LABELS.values(), *nutrition_labels.DATA_QUALITY_FLAG_LABELS.values(),
             *nutrition_labels.ACTIVITY_FLAG_LABELS.values(), *nutrition_labels.CALORIE_MODE_LABELS.values(),
             *nutrition_labels.CONFIDENCE_LEVEL_LABELS.values(), *nutrition_labels.ADAPTIVE_UNAVAILABLE_LABELS.values()]
    assert all(texto.strip() for texto in todos)


def test_export_para_o_frontend_traz_os_rotulos_por_grupo_e_codigo():
    from app.domain import nutrition

    rotulos = nutrition.export_for_frontend()["adaptive_energy_labels"]
    assert set(rotulos) == {"confidence_level", "calorie_mode", "activity_flag", "data_quality_flag",
                            "suspension_reason", "unavailable_reason", "day_sync_status", "day_confirmation_source",
                            "completeness_source", "activity_correction_action", "activity_correction_energy_kind",
                            "report_adaptive_basis"}
    assert rotulos["confidence_level"]["high"] == "Alta"
    assert set(rotulos["activity_flag"]) == {f.value for f in ae.ActivityFlag}
    assert set(rotulos["calorie_mode"]) == {m.value for m in ae.CalorieMode}
    assert set(rotulos["unavailable_reason"]) == {svc_energy.UNAVAILABLE_NO_DATA, svc_energy.UNAVAILABLE_ERROR}
    assert all(texto.strip() for grupo in rotulos.values() for texto in grupo.values())


# ---------------------------------------------------------------------------
# Rota
# ---------------------------------------------------------------------------


def test_dashboard_traz_o_bloco_adaptive_energy_e_mantem_o_adaptive_tdee(db):
    _historico(db)
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=1), "WeightTraining", 428.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    resposta = _client().get("/nutrition/dashboard")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["adaptive_tdee"]["applied"] is False  # o bloco antigo continua como estava
    bloco = corpo["adaptive_energy"]
    assert set(bloco) == {"available", "applied", "algorithm_version", "unavailable_reason", "unavailable_reason_label",
                          "state", "days"}
    assert bloco["available"] is True and bloco["applied"] is False and len(bloco["days"]) == 7
    assert {"adaptive_tdee", "confidence", "confidence_level", "suspension_reasons", "data_quality_flags",
            "weight_slope_kg_per_week", "trend_weight_kg"} <= set(bloco["state"])
    # aproximação do tamanho do bloco para o "Impacto em produção"
    assert len(resposta.content) < 200_000 and len(json.dumps(bloco)) < 20_000


def test_dashboard_sem_dados_responde_200_com_sombra_indisponivel(db):
    resposta = _client().get("/nutrition/dashboard")
    assert resposta.status_code == 200
    bloco = resposta.json()["adaptive_energy"]
    assert bloco["available"] is False and bloco["unavailable_reason"] == "no_data" and bloco["state"] is None


def test_falha_na_sombra_nao_derruba_o_dashboard(db, monkeypatch):
    _historico(db)

    def quebra(*args, **kwargs):
        raise RuntimeError("banco indisponivel")

    monkeypatch.setattr(svc_energy, "load_shadow_rows", quebra)
    resposta = _client().get("/nutrition/dashboard")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["adaptive_energy"]["available"] is False and corpo["adaptive_energy"]["unavailable_reason"] == "error"
    assert corpo["adaptive_tdee"]["applied"] is False and len(corpo["days"]) > 0


def test_usuario_comum_e_admin_vendo_outro_cada_um_com_os_proprios_dados(db):
    _historico(db, ALUNO)
    _historico(db, OUTRO, dias=5)
    db.add_intervals_row(ALUNO, HOJE - timedelta(days=1), "Run", 200.0, start=INICIO_FORCA, synced_at=SYNC_UTC)
    proprio = _client(user_id=ALUNO).get("/nutrition/dashboard").json()["adaptive_energy"]
    assert proprio["available"] and proprio["days"][1]["counted_activities"] == 1
    admin = _client(role="admin", user_id=ADMIN).get(f"/nutrition/dashboard?user_id={ALUNO}")
    assert admin.status_code == 200 and admin.json()["user"]["id"] == ALUNO
    assert admin.json()["adaptive_energy"]["days"][1]["counted_activities"] == 1
    do_outro = _client(user_id=OUTRO).get("/nutrition/dashboard").json()["adaptive_energy"]
    assert do_outro["days"] == [] or do_outro["days"][1]["counted_activities"] == 0  # nada do ALUNO vaza
    assert _client(user_id=OUTRO).get(f"/nutrition/dashboard?user_id={ALUNO}").status_code == 403


# ---------------------------------------------------------------------------
# Prévia do plano: composição do gasto (valores que o cálculo já tem)
# ---------------------------------------------------------------------------


def test_previa_traz_tdee_breakdown_extra_activity_e_tdee_basis_no_modo_automatico(db):
    db.add_weight(ALUNO, HOJE - timedelta(days=3), 80)
    admin = _client("admin", ADMIN)
    rascunho = admin.post("/nutrition/plans", json={"user_id": ALUNO, "strategy": "maintenance",
                                                    "tdee_mode": "auto_intervals", "activity_level": "light"}).json()
    corpo = admin.post(f"/nutrition/plans/{rascunho['id']}/preview")
    assert corpo.status_code == 200, corpo.text
    alvo = corpo.json()["target"]
    assert alvo["tdee_basis"] in {"auto_synced", "auto_fallback"}
    assert set(alvo["extra_activity"]) == {"kcal", "kcal_nature", "items", "gross_kcal", "resting_discount_kcal"}
    assert "base_kcal" not in alvo["tdee"]  # nada novo no dict que vai para `inputs_json`
    quebra = alvo["tdee_breakdown"]
    assert quebra["parts"][0]["code"] == "base"
    assert sum(parte["kcal"] for parte in quebra["parts"]) == quebra["total_kcal"]
