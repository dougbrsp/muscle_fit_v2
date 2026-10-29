"""Detalhamento diario do relatorio em Markdown (`report/daily_breakdown.py`).

Funcoes puras, sem banco. O ponto central e a INVARIANTE: a soma das linhas
diarias e o total do periodo - por grupo muscular (contra o resumo do
dashboard) e por equipamento (resumo derivado das mesmas linhas).

Rodar: cd api && python -m pytest tests/unit/test_reports_markdown_daily.py
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.intelligence.dashboard import muscle_summary_service as mss
from app.intelligence.report import daily_breakdown as daily
from app.intelligence.report import markdown_report_service as svc

D1, D2, D3 = date(2026, 9, 1), date(2026, 9, 3), date(2026, 9, 5)


def test_convencoes_excluidas_sao_as_mesmas_do_resumo():
    assert daily.EXCLUDED_CONVENTIONS == svc.TONNAGE_EXCLUDED_CONVENTIONS


def test_sql_diario_por_grupo_usa_a_mesma_cadeia_do_resumo():
    # Mesma cadeia JOIN/WHERE (filtro de preparatorias, contribuicao ativa) e
    # mesma unidade de trabalho do `ROLE_TOTALS_SQL`: so muda o GROUP BY.
    assert mss._FROM_REALIZED in mss.DAILY_GROUP_TOTALS_SQL
    assert mss._FROM_REALIZED in mss.ROLE_TOTALS_SQL
    # M97 F4: o estimulo pondera a serie de forca pelo peso do dono do volume.
    assert f"SUM({mss._WORK} * emc.stimulus_weight * {mss._HYP})" in mss.DAILY_GROUP_TOTALS_SQL
    assert f"SUM({mss._WORK} * emc.stimulus_weight * {mss._HYP})" in mss.ROLE_TOTALS_SQL
    assert "GROUP BY a.local_date, emc.muscle_group_id" in mss.DAILY_GROUP_TOTALS_SQL


def test_sql_de_tonelagem_agrupa_por_dia_sem_mudar_a_regra():
    sql = svc.TONNAGE_BY_GROUP_SQL_TEMPLATE.format(load_convention="NULL")
    assert "a.local_date" in sql
    assert "GROUP BY emc.muscle_group_id, load_conv, a.local_date" in sql


def test_sql_de_equipamento_isola_usuario_e_nao_multiplica_serie():
    sql = daily.EQUIPMENT_DAILY_SQL_TEMPLATE.format(load_convention="eqex.load_convention")
    assert "a.user_id = %s" in sql
    assert "exercise_muscle_contributions" not in sql  # serie conta uma vez
    assert "GROUP BY a.local_date, e.id" in sql
    assert "LEFT JOIN gym_equipment ge" in sql  # sem equipamento continua na leitura


# ---------------------------------------------------------------------------
# Tonelagem e grupo: soma diaria = total do periodo
# ---------------------------------------------------------------------------

TONNAGE_ROWS = [
    # (dia, grupo, convencao, tonelagem ponderada, series)
    {"local_date": D1, "muscle_group_id": 1, "load_conv": None, "tonnage_kg": 1200.04, "sets_count": 2},
    {"local_date": D2, "muscle_group_id": 1, "load_conv": "total", "tonnage_kg": 1500.03, "sets_count": 3},
    {"local_date": D2, "muscle_group_id": 2, "load_conv": None, "tonnage_kg": 750.02, "sets_count": 3},
    {"local_date": D3, "muscle_group_id": 3, "load_conv": "assisted", "tonnage_kg": 900, "sets_count": 3},
    {"local_date": D3, "muscle_group_id": 1, "load_conv": "bodyweight", "tonnage_kg": 0, "sets_count": 2},
]


def test_tonelagem_diaria_soma_o_total_do_periodo():
    period = svc.summarize_tonnage(TONNAGE_ROWS, [])["by_group"]
    days = daily.daily_tonnage(TONNAGE_ROWS)
    for group_id, total in period.items():
        summed = sum(b["tonnage_kg"] for (d, g), b in days.items() if g == group_id)
        assert summed == pytest.approx(total["tonnage_kg"], abs=0.051)
        assert sum(b["sets"] for (d, g), b in days.items() if g == group_id) == total["sets"]
        assert sum(b["excluded_sets"] for (d, g), b in days.items() if g == group_id) == total["excluded_sets"]
    # Assistido/peso corporal nunca entram na tonelagem do dia.
    assert days[(D3, 3)] == {"tonnage_kg": 0.0, "sets": 0, "excluded_sets": 3}
    assert days[(D3, 1)]["excluded_sets"] == 2


def _period_groups():
    """O resumo do periodo como o coletor monta (valores do dashboard)."""
    return [
        {"muscle_group_id": 1, "name": "Peitoral", "physical_sets": 7.0, "effective_sets": 6.5,
         "tonnage_kg": 2700.1, "avg_rpe": 8.43},  # 59 / 7, arredondado como o `metric`
        {"muscle_group_id": 2, "name": "Triceps", "physical_sets": 3.0, "effective_sets": 1.5,
         "tonnage_kg": 750.0, "avg_rpe": None},
        {"muscle_group_id": 3, "name": "Costas", "physical_sets": 3.0, "effective_sets": 3.0,
         "tonnage_kg": None, "avg_rpe": 8.0},
        # Grupo so com meta (nenhuma serie): existe no resumo, nao nos dias.
        {"muscle_group_id": 4, "name": "Ombro", "physical_sets": 0.0, "effective_sets": 0.0,
         "tonnage_kg": None, "avg_rpe": None},
    ]


GROUP_ROWS = [
    {"local_date": D1, "muscle_group_id": 1, "physical_sets": 2, "stimulus_sets": 2.0, "rpe_sum": 17, "rpe_n": 2,
     "exercises": 1, "sessions": 1, "technique_rows": 0},
    # D2: 3 series fisicas, 1 delas aplicacao de tecnica (fora do estimulo do SQL).
    {"local_date": D2, "muscle_group_id": 1, "physical_sets": 3, "stimulus_sets": 2.0, "rpe_sum": 25, "rpe_n": 3,
     "exercises": 1, "sessions": 1, "technique_rows": 1},
    {"local_date": D2, "muscle_group_id": 2, "physical_sets": 3, "stimulus_sets": 1.5, "rpe_sum": 0, "rpe_n": 0,
     "exercises": 1, "sessions": 1, "technique_rows": 0},
    {"local_date": D3, "muscle_group_id": 3, "physical_sets": 3, "stimulus_sets": 3.0, "rpe_sum": 24, "rpe_n": 3,
     "exercises": 1, "sessions": 1, "technique_rows": 0},
    {"local_date": D3, "muscle_group_id": 1, "physical_sets": 2, "stimulus_sets": 2.0, "rpe_sum": 17, "rpe_n": 2,
     "exercises": 1, "sessions": 1, "technique_rows": 0},
]
# A aplicacao de D2 vale meia serie no grupo 1 (peso 1,0).
APPLICATIONS = [({"local_date": D2, "muscle_group_id": 1, "stimulus_weight": 1.0}, 0.5)]
NAMES = {1: "Peitoral", 2: "Triceps", 3: "Costas"}


def _group_days():
    return daily.build_group_days(GROUP_ROWS, APPLICATIONS, daily.daily_tonnage(TONNAGE_ROWS), NAMES)


def test_dias_por_grupo_em_ordem_cronologica_com_tecnica_e_tonelagem():
    days = _group_days()
    assert [(d["local_date"], d["name"]) for d in days] == [
        (D1, "Peitoral"), (D2, "Peitoral"), (D2, "Triceps"), (D3, "Costas"), (D3, "Peitoral"),
    ]
    d2 = days[1]
    assert d2["effective_sets"] == 2.5  # 2 de trabalho + fatia 0,5 da aplicacao
    assert d2["avg_rpe"] == pytest.approx(25 / 3)
    assert days[2]["avg_rpe"] is None  # sem RPE registrado: ausencia, nao zero
    # Costas so tem serie assistida: tonelagem ausente, series excluidas contadas.
    assert days[3]["tonnage_kg"] is None and days[3]["tonnage_excluded_sets"] == 3
    # Peitoral em D3 foi peso corporal: sem tonelagem naquele dia.
    assert days[4]["tonnage_kg"] is None


def test_invariante_por_grupo_fecha_com_o_resumo():
    assert daily.check_daily_invariant(_period_groups(), _group_days(), key="muscle_group_id") == []


def test_invariante_por_grupo_acusa_divergencia():
    period = _period_groups()
    period[0]["physical_sets"] = 8.0  # um dia "sumiu" do detalhamento
    problems = daily.check_daily_invariant(period, _group_days(), key="muscle_group_id")
    assert problems and problems[0].startswith("Peitoral: physical_sets")
    # Grupo que aparece nos dias e nao no periodo tambem e divergencia.
    problems = daily.check_daily_invariant(period[1:], _group_days(), key="muscle_group_id")
    assert any("aparece no detalhamento diário" in p for p in problems)


# ---------------------------------------------------------------------------
# Equipamento
# ---------------------------------------------------------------------------


def _eq_row(day, row_id, *, equipment=None, gym=None, exercise="Supino", conv=None, sets=3, work=3, rpe=(24, 3),
            reps=30, tonnage=1800.0, load_max=60.0, load=(180.0, 3)):
    return {
        "local_date": day, "exercise_row_id": row_id, "exercise_id": 1, "exercise_name": exercise,
        "equipment_id": 9 if equipment else None, "equipment_name": equipment, "gym_name": gym, "load_conv": conv,
        "physical_sets": sets, "work_units": work, "technique_rows": sets - work, "rpe_sum": rpe[0], "rpe_n": rpe[1],
        "working_sets": work, "reps_sum": reps, "tonnage_kg": tonnage, "load_max": load_max,
        "load_sum": load[0], "load_n": load[1],
    }


EQUIPMENT_ROWS = [
    _eq_row(D1, 10, equipment="Banco reto", gym="Centro"),
    # Mesmo dia, mesmo equipamento e exercicio em duas linhas (duas sessoes):
    # vira UMA linha diaria. A segunda tem uma aplicacao de tecnica.
    _eq_row(D1, 11, equipment="Banco reto", gym="Centro", sets=4, work=3, rpe=(36, 4), reps=36, tonnage=2160.0,
            load_max=70.0, load=(210.0, 3)),
    _eq_row(D2, 12, equipment="Graviton", exercise="Barra assistida", conv="assisted", reps=23, tonnage=690.0,
            load_max=30.0, load=(90.0, 3)),
    _eq_row(D3, 13, exercise="Flexao", conv=None, reps=45, tonnage=0.0, load_max=None, load=(0.0, 0), rpe=(0, 0)),
    _eq_row(D3, 14, equipment="Banco reto", gym="Centro", reps=24, tonnage=1440.0),
]


def test_equipamento_diario_agrupa_por_dia_equipamento_e_exercicio():
    out = daily.build_equipment(EQUIPMENT_ROWS, {11: 0.5})
    days = out["days"]
    assert [(d["local_date"], d["equipment"], d["exercise"]) for d in days] == [
        (D1, "Banco reto (Centro)", "Supino"),
        (D2, "Graviton", "Barra assistida"),
        (D3, "Banco reto (Centro)", "Supino"),
        (D3, daily.NO_EQUIPMENT, "Flexao"),
    ]
    d1 = days[0]
    assert d1["physical_sets"] == 7 and d1["effective_sets"] == 6.5  # 6 de trabalho + 0,5 da aplicacao
    assert d1["tonnage_kg"] == 3960.0 and d1["reps"] == 66
    assert d1["load_max"] == 70.0 and d1["load_avg"] == pytest.approx(65.0)
    assert d1["avg_rpe"] == pytest.approx(60 / 7)
    # Assistido: sem tonelagem, reps e convencao declaradas; a carga e a gravada.
    graviton = days[1]
    assert graviton["tonnage_kg"] is None and graviton["reps"] == 23
    assert graviton["excluded_conventions"] == ["assisted"] and graviton["load_max"] == 30.0
    # Sem equipamento: entra com nome proprio, sem carga.
    sem = days[3]
    assert sem["load_max"] is None and sem["load_avg"] is None and sem["avg_rpe"] is None


def test_invariante_por_equipamento_soma_diaria_igual_ao_periodo():
    out = daily.build_equipment(EQUIPMENT_ROWS, {11: 0.5})
    assert daily.check_daily_invariant(out["period"], out["days"], key="equipment", label="equipment") == []
    period = {p["equipment"]: p for p in out["period"]}
    banco = period["Banco reto (Centro)"]
    assert banco["days"] == 2 and banco["exercises"] == 1
    assert banco["physical_sets"] == sum(d["physical_sets"] for d in out["days"] if d["equipment"] == banco["equipment"])
    assert banco["tonnage_kg"] == 3960.0 + 1440.0
    assert period["Graviton"]["tonnage_kg"] is None
    # "Sem equipamento" sempre por ultimo no resumo.
    assert out["period"][-1]["equipment"] == daily.NO_EQUIPMENT


def test_equipamento_vazio():
    assert daily.build_equipment([], {}) == {"days": [], "period": []}


# ---------------------------------------------------------------------------
# Recuperacao, corpo e ciclos
# ---------------------------------------------------------------------------


def test_recuperacao_uma_linha_por_data_com_todas_as_fontes():
    days = daily.build_recovery_days(
        [{"local_date": D1, "duration_hours": "7.50", "deep_sleep_hours": None, "rem_sleep_hours": "2.00",
          "regularity_percent": None}],
        [{"local_date": D1, "hrv_ms": "50.0", "resting_hr_bpm": 52, "form_tsb": "-3.5", "readiness_score": None},
         {"local_date": D3, "hrv_ms": None, "resting_hr_bpm": None, "form_tsb": None, "readiness_score": None}],
        [{"local_date": D2, "energy": 4.0, "soreness": 2.0, "stress": None, "mood": 5.0}],
        # Dia de treino sem dor registrada (COALESCE do SQL = 0) nao vira "dor 0".
        [{"local_date": D2, "muscle_pain": 3, "joint_pain": 0}, {"local_date": D3, "muscle_pain": None, "joint_pain": 0}],
    )
    assert [d["local_date"] for d in days] == [D1, D2]  # D3 nao tem nenhum valor
    assert days[0]["sleep_hours"] == 7.5 and days[0]["hrv_ms"] == 50.0 and days[0]["deep_hours"] is None
    assert days[1]["energy"] == 4.0 and days[1]["muscle_pain"] == 3.0 and days[1]["sleep_hours"] is None


def test_medias_do_periodo_saem_das_linhas_diarias():
    rows = [
        {"duration_hours": "7.50", "deep_sleep_hours": "1.50", "rem_sleep_hours": "2.00", "regularity_percent": "80"},
        {"duration_hours": "6.50", "deep_sleep_hours": None, "rem_sleep_hours": "1.00", "regularity_percent": None},
    ]
    stats = daily.sleep_stats(rows)
    assert stats == {
        "days": 2, "avg_duration_hours": 7.0, "duration_days": 2, "avg_deep_hours": 1.5, "deep_days": 1,
        "avg_rem_hours": 1.5, "rem_days": 2, "avg_regularity_percent": 80.0, "regularity_days": 1,
    }
    assert daily.wellness_stats([])["avg_hrv_ms"] is None


def test_corpo_peso_bioimpedancia_e_medidas_por_data():
    out = daily.build_body_days(
        [{"local_date": date(2026, 8, 30), "weight_kg": 81.0, "source": "x"},  # fora da janela (media movel)
         {"local_date": D1, "weight_kg": 80.5, "source": "body_composition"},
         {"local_date": D3, "weight_kg": 80.0, "source": "daily_biometrics"}],
        [{"local_date": D1, "body_fat_percent": "15.0", "muscle_mass_kg": "38.0"},
         {"local_date": D1, "body_fat_percent": "14.8", "muscle_mass_kg": None}],
        [{"local_date": D2, "site": "waist", "value_cm": "84.0"}, {"local_date": D2, "site": "arm_r", "value_cm": "36"}],
        date_from=D1, date_to=D3, site_labels={"waist": "Cintura", "arm_r": "Braço direito"},
    )
    assert [d["local_date"] for d in out["days"]] == [D1, D3]
    assert out["days"][0]["body_fat_percent"] == 14.8  # ultima leitura do dia vence
    assert out["fields"] == ["body_fat_percent"]
    assert [s["label"] for s in out["measurement_sites"]] == ["Cintura", "Braço direito"]
    assert out["measurement_days"] == [{"local_date": D2, "values": {"waist": 84.0, "arm_r": 36.0}}]


def test_linha_do_tempo_de_ciclos_com_modelo_vigente_e_dia_do_modelo():
    segments = [
        SimpleNamespace(date_from=D1, date_to=D2, assignment=None),
        SimpleNamespace(date_from=date(2026, 9, 4), date_to=D3, assignment=SimpleNamespace(template_name="PPL")),
    ]
    sessions = [
        {"activity_id": 1, "local_date": D1, "name": "Livre", "duration_seconds": None, "minutes": None,
         "avg_hr_bpm": None, "max_hr_bpm": None, "slot_sequence": None},
        {"activity_id": 2, "local_date": D3, "name": "Push", "duration_seconds": 3720, "avg_hr_bpm": 120.0,
         "max_hr_bpm": 150.0, "slot_sequence": 1, "slot_name": "Push"},
    ]
    rpe = [{"activity_id": 2, "physical_sets": 18, "rpe_sum": 148.5, "rpe_n": 18}]
    timeline = daily.build_cycle_timeline(sessions, rpe, segments)
    assert timeline[0]["template_name"] is None and timeline[0]["slot"] is None and timeline[0]["avg_rpe"] is None
    assert timeline[1]["template_name"] == "PPL" and timeline[1]["slot"] == "1 — Push"
    assert timeline[1]["minutes"] == 62 and timeline[1]["avg_rpe"] == 8.25 and timeline[1]["physical_sets"] == 18
