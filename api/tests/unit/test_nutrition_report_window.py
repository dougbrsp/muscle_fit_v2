"""Resumo da nutrição no relatório Markdown (`domain/nutrition/report_window`, mapa §91) e o coletor
`_collect_nutrition` com um fake de cursor que PROJETA as colunas do SQL real.

Rodar: cd api && python -m pytest tests/unit/test_nutrition_report_window.py
"""

from __future__ import annotations

import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.domain.nutrition import meal_math
from app.domain.nutrition.report_window import window_report
from app.domain.nutrition.weekly_budget import target_macros
from app.intelligence.dashboard.contracts import WindowInfo
from app.intelligence.report import markdown_report_service as svc
from app.intelligence.report.markdown_renderer import render_markdown

HOJE = date(2026, 11, 5)  # quinta
INICIO_PLANO = date(2026, 10, 25)


def _totais(kcal, p=150.0, c=250.0, f=60.0, fib=30.0):
    valores = {
        meal_math.ENERGY_FIELD: kcal,
        meal_math.PROTEIN_FIELD: p,
        meal_math.CARB_FIELD: c,
        meal_math.FAT_FIELD: f,
        meal_math.FIBER_FIELD: fib,
        meal_math.SODIUM_FIELD: 2000.0,
    }
    return meal_math.totals_from_stored(valores, (), None, 1)


def _janela(dias):
    return [date.fromordinal(HOJE.toordinal() - n) for n in range(dias - 1, -1, -1)]


def test_medias_so_dos_dias_registrados_e_balanco_pelo_motor():
    dias = _janela(5)  # 01..05/11; hoje (05) aberto
    meta = target_macros(2400, 160, 260, 70, 30)
    totais = [_totais(2000), None, _totais(2600), None, _totais(9999)]
    r = window_report(
        dias, HOJE, INICIO_PLANO, [meta] * 5, totais, [2500, 2500, 2700, 2500, 2500],
        ["auto_synced", "auto_synced", "auto_fallback", None, None],
    )
    assert (r["window_days"], r["logged_days"], r["no_record_days"], r["open_today"]) == (5, 2, 2, True)
    s = r["summary"]
    assert s["nutrients"]["kcal"] == {"target_avg": 2400.0, "intake_avg": 2300.0}
    assert s["tdee_avg_kcal"] == 2600.0
    assert s["balance_avg_kcal"] == -300.0  # (2000-2500 + 2600-2700) / 2
    assert s["balance_total_kcal"] == -600.0
    assert s["tdee_basis_days"] == {"auto_synced": 1, "auto_fallback": 1}
    # hoje (aberto) fica de fora, mesmo com total
    assert len(r["days"]) == 2


def test_balanco_total_e_a_soma_dos_balancos_diarios():
    dias = _janela(3)
    meta = target_macros(2400, 160, 260, 70, 30)
    r = window_report(dias, HOJE, INICIO_PLANO, [meta] * 3, [_totais(2000), _totais(2600), None], [2500] * 3, [None] * 3)
    assert r["summary"]["balance_total_kcal"] == 100.0 - 500.0
    assert r["summary"]["balance_avg_kcal"] == -200.0


def test_sem_meta_o_dia_entra_no_consumo_mas_nao_na_media_da_meta():
    dias = _janela(3)
    r = window_report(dias, HOJE, INICIO_PLANO, [None] * 3, [_totais(2000), None, None], [None] * 3, [None] * 3)
    assert r["summary"]["nutrients"]["kcal"] == {"target_avg": None, "intake_avg": 2000.0}
    assert r["summary"]["balance_avg_kcal"] is None and r["summary"]["balance_total_kcal"] is None


def test_semanas_segunda_a_domingo_com_ponta_parcial():
    dias = _janela(7)  # 30/10 (sex) .. 05/11 (qui)
    meta = target_macros(2400, 160, 260, 70, 30)
    totais = [_totais(2000)] * 6 + [None]
    r = window_report(dias, HOJE, date(2026, 10, 1), [meta] * 7, totais, [2500] * 7, [None] * 7)
    semanas = r["weeks"]
    assert [w["start"] for w in semanas] == [date(2026, 10, 26), date(2026, 11, 2)]
    assert [(w["days_in_window"], w["logged_days"], w["partial"]) for w in semanas] == [(3, 3, True), (4, 3, True)]


def test_dia_incompleto_nao_entra_nas_medias():
    dias = _janela(2)
    incompleto = meal_math.totals_from_stored({meal_math.ENERGY_FIELD: 500.0}, (meal_math.PROTEIN_FIELD,), None, 1)
    r = window_report(dias, HOJE, INICIO_PLANO, [None] * 2, [incompleto, None], [None] * 2, [None] * 2)
    assert (r["logged_days"], r["incomplete_days"], r["has_records"]) == (0, 1, True)


def test_sem_refeicao_nenhuma_has_records_falso():
    r = window_report(_janela(3), HOJE, INICIO_PLANO, [None] * 3, [None] * 3, [None] * 3, [None] * 3)
    assert r["has_records"] is False and r["summary"]["balance_total_kcal"] is None


# --- coletor com fake de cursor que projeta colunas ---------------------------------------------------------


def _split_colunas(texto: str) -> list[str]:
    partes, nivel, atual = [], 0, ""
    for ch in texto:
        if ch == "(":
            nivel += 1
        elif ch == ")":
            nivel -= 1
        if ch == "," and nivel == 0:
            partes.append(atual.strip())
            atual = ""
        else:
            atual += ch
    partes.append(atual.strip())
    return [re.split(r"\s+AS\s+", p, flags=re.I)[-1].strip() for p in partes]


class _Cursor:
    def __init__(self, tabelas):
        self.tabelas, self._linhas = tabelas, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        colunas = _split_colunas(sql.split(" FROM ")[0].replace("SELECT ", "", 1))
        origem = "targets" if "nutrition_daily_targets" in sql else "meals"
        inicio, fim = params[1], params[2]
        self._linhas = [
            {c: linha[c] for c in colunas}  # KeyError = coluna que o SQL real não traria
            for linha in self.tabelas[origem]
            if inicio <= linha["local_date"] <= fim
        ]

    def fetchall(self):
        return list(self._linhas)


class _Conn:
    def __init__(self, tabelas):
        self.tabelas = tabelas

    def cursor(self):
        return _Cursor(self.tabelas)


def _linha_meta(dia, basis='"auto_synced"'):
    return {
        "local_date": dia, "plan_id": 1, "day_kind": "training", "is_training_day": 1, "training_known": 1,
        "phase_kind": "cut", "kcal_target": 2400, "protein_g": 160, "carbs_g": 260, "fat_g": 70, "fiber_g": 30,
        "water_ml": 3000, "bmr_kcal": 1700, "bmr_formula": "x", "tdee_kcal": 2800, "training_kcal": 300,
        "training_kcal_nature": None, "limitations_json": "[]", "inputs_hash": "h", "engine_version": "v",
        "computed_at": datetime(2026, 11, 1), "extra_activity_json": None, "tdee_basis_json": basis, "energy_json": None,
    }


def _linha_refeicao(dia, kcal):
    base = {"id": 1, "local_date": dia, "totals_incomplete_json": "[]", "totals_nature": None, "item_count": 1}
    base.update(
        {
            meal_math.ENERGY_FIELD: kcal, meal_math.PROTEIN_FIELD: 150.0, meal_math.CARB_FIELD: 250.0,
            meal_math.FAT_FIELD: 60.0, meal_math.FIBER_FIELD: 28.0, meal_math.SODIUM_FIELD: 2000.0,
        }
    )
    return base


@pytest.fixture
def coleta(monkeypatch):
    from app import nutrition_schema, nutrition_targets_service
    from app.intelligence.dashboard import time_windows

    monkeypatch.setattr(nutrition_schema, "require_nutrition_tables", lambda cur: None)
    monkeypatch.setattr(nutrition_schema, "require_diary_tables", lambda cur: None)
    plano = SimpleNamespace(plan_id=1, effective_from=INICIO_PLANO, published_at=None)
    config = SimpleNamespace(strategy=SimpleNamespace(value="cut"), goal_weight_kg=None, goal_body_fat_percent=None)
    monkeypatch.setattr(
        nutrition_targets_service, "load_published_plans", lambda cur, uid: SimpleNamespace(refs=(plano,), settings={1: config})
    )
    monkeypatch.setattr(time_windows, "user_today_for", lambda conn, uid: HOJE)

    def _coletar(tabelas, dias=7):
        janela = WindowInfo(
            kind="custom", date_from=_janela(dias)[0], date_to=HOJE, timezone="America/Sao_Paulo", days=dias
        )
        return svc._collect_nutrition(_Conn(tabelas), user_id=7, window=janela)

    return _coletar


def test_coletor_monta_resumo_pelo_dashboard_e_pelo_motor(coleta):
    d1, d2 = date(2026, 11, 3), date(2026, 11, 4)
    tabelas = {
        "targets": [_linha_meta(d1), _linha_meta(d2, basis='"auto_fallback"')],
        "meals": [_linha_refeicao(d1, 2200.0), _linha_refeicao(d2, 2600.0)],
    }
    dados = coleta(tabelas)
    assert dados["available"] is True and dados["has_records"] is True
    assert (dados["window_days"], dados["logged_days"]) == (7, 2)
    s = dados["summary"]
    assert s["nutrients"]["kcal"] == {"target_avg": 2400.0, "intake_avg": 2400.0}
    assert s["tdee_avg_kcal"] == 2800.0
    assert s["balance_total_kcal"] == -800.0
    assert s["tdee_basis_days"] == {"auto_synced": 1, "auto_fallback": 1}
    md = render_markdown({"window": {"date_from": date(2026, 10, 30), "date_to": HOJE, "days": 7}, "nutrition": dados})
    assert "## 7. Nutrição" in md and "Balanço total no período:** -800 kcal" in md


def test_coletor_sem_refeicao_devolve_has_records_falso(coleta):
    dados = coleta({"targets": [], "meals": []})
    assert dados["available"] is True and dados["has_records"] is False
    assert "Sem registros de nutrição no período" in render_markdown({"nutrition": dados})


def test_orquestrador_isola_falha_da_secao_de_nutricao(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("tabelas ausentes")

    for nome in ("_collect_muscles", "_collect_equipment", "_collect_recovery", "_collect_body", "_collect_cardio", "_collect_cycles"):
        monkeypatch.setattr(svc, nome, lambda *a, **k: {"available": True})
    monkeypatch.setattr(svc.daily, "WindowTechniqueSets", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_collect_nutrition", _boom)
    janela = WindowInfo(kind="custom", date_from=date(2026, 10, 30), date_to=HOJE, timezone="America/Sao_Paulo", days=7)
    dados = svc.collect_report_data(object(), user_id=7, window=janela, now_local=datetime(2026, 11, 5, 9, 0))
    assert dados["nutrition"] == {"available": False}
    assert "Nutrição" in dados["quality"]["unavailable_sections"]
    assert "Seção indisponível" in render_markdown(dados)
