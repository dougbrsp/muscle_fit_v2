"""Fase 5, PR 4 - técnica avançada no volume REALIZADO, na aderência e na
recuperação.

O que cada bloco protege:

1. `realized_set_units` (a unidade de cada linha) - tabela: trabalho = 1,
   aquecimento/ajuste = 0, aplicação = fatia do bloco (soma das fatias =
   equivalente do bloco, com teto), legado sem código/âncora derivado.
2. Catálogo preguiçoso: leitura sem técnica não lê o catálogo; fator de
   recuperação por técnica.
3. Overview de recuperação (`VolumeService`): diretas/estímulo com a fatia,
   ponderadas pelo `stimulus_weight`; dose com o fator da técnica; queda de
   reps só entre séries de trabalho.
4. Agregados por exercício (`apply_application_units`) e tendência semanal.
5. Novo Dashboard (`muscle_summary_service._build_items`): mesma contagem, e
   UMA leitura a mais só quando a janela tem técnica - nunca por grupo.
6. Aderência: completude só com séries de trabalho; "técnica aplicada X/N".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.domain import technique_accounting as ta
from app.domain.technique_accounting import LazyCatalog
from app.intelligence import technique_volume as tv
from app.intelligence.dashboard import adherence_service as ad
from app.intelligence.dashboard import muscle_summary_service as ms
from app.intelligence.dashboard.contracts import PlanItemOut, TargetSource, WindowInfo, WindowKind
from app.intelligence.muscle_recovery_service import MuscleRecoveryService as MRS
from app.intelligence.volume_service import VolumeService

# Catálogo como a migração 060 o deixa (só as colunas que a regra lê).
CATALOG = {
    "drop_set": {"activation_set_equivalent": 0.50, "max_equivalent_per_anchor": 1.00, "recovery_cost_factor": 1.15, "set_type": "drop_set"},
    "rest_pause": {"activation_set_equivalent": 0.40, "max_equivalent_per_anchor": 1.00, "recovery_cost_factor": 1.10, "set_type": "rest_pause"},
    "forced_reps": {"activation_set_equivalent": 0.25, "max_equivalent_per_anchor": 0.50, "recovery_cost_factor": 1.20, "set_type": "forced_rep"},
    "partial_reps": {"activation_set_equivalent": 0.15, "max_equivalent_per_anchor": 0.50, "recovery_cost_factor": 1.05, "set_type": "forced_rep"},
    "fst7": {"activation_set_equivalent": 0, "max_equivalent_per_anchor": 0, "recovery_cost_factor": 1.00, "default_sets_count": 7, "set_type": "working"},
}


def _catalog() -> LazyCatalog:
    return LazyCatalog(lambda: CATALOG)


def _s(n, set_type="working", reps=10, rpe=10, code=None, anchor=None, exercise=1):
    return {
        "exercise_id": exercise,
        "set_number": n,
        "set_type": set_type,
        "reps": reps,
        "rpe": rpe,
        "technique_code": code,
        "anchor_set_number": anchor,
    }


# ---------------------------------------------------------------------------
# 1) Unidade de cada linha
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caso, rows, esperado",
    [
        ("só trabalho", [_s(1), _s(2), _s(3)], [1, 1, 1]),
        ("aquecimento e ajuste valem 0", [_s(1, "warmup"), _s(2, "feeler"), _s(3)], [0, 0, 1]),
        ("backoff e legado sem tipo são trabalho", [_s(1, "backoff"), _s(2, None)], [1, 1]),
        (
            "rest-pause 5+5 numa âncora de 10 = uma série",
            [_s(1), _s(2), _s(3, "rest_pause", 5, code="rest_pause", anchor=2), _s(4, "rest_pause", 5, code="rest_pause", anchor=2)],
            [1, 1, 0.5, 0.5],
        ),
        (
            "6+4+3 numa âncora de 12: o teto 1,0 é repartido na proporção",
            [_s(1, reps=12), _s(2, "rest_pause", 6, code="rest_pause", anchor=1), _s(3, "rest_pause", 4, code="rest_pause", anchor=1), _s(4, "rest_pause", 3, code="rest_pause", anchor=1)],
            [1, 6 / 13, 4 / 13, 3 / 13],
        ),
        (
            "legado sem código nem âncora: deriva drop_set e a última série de trabalho anterior",
            [_s(1), _s(2), _s(3, "dropset", 5), _s(4, "drop_set", 5)],
            [1, 1, 0.5, 0.5],
        ),
        (
            "reps forçadas: extras / ref × 1,0, teto 0,5",
            [_s(1, reps=10), _s(2, "forced_rep", 3, code="forced_reps", anchor=1)],
            [1, 0.3],
        ),
        (
            "parciais pela técnica gravada: peso 0,5",
            [_s(1, reps=10), _s(2, "forced_rep", 4, code="partial_reps", anchor=1)],
            [1, 0.2],
        ),
        (
            "sem reps: coeficiente do catálogo por aplicação",
            [_s(1), _s(2, "drop_set", None, code="drop_set", anchor=1)],
            [1, 0.5],
        ),
        (
            "aplicação sem âncora e sem reps de referência: coeficiente",
            [_s(1, "rest_pause", 6, code="rest_pause")],
            [0.4],
        ),
        ("FST-7: séries de trabalho comuns, sem extra", [_s(1, code="fst7"), _s(2, code="fst7")], [1, 1]),
        (
            "o bloco não atravessa exercícios",
            [_s(1, exercise=1), _s(1, exercise=2, reps=20), _s(2, "rest_pause", 5, code="rest_pause", anchor=1, exercise=1)],
            [1, 1, 0.5],
        ),
    ],
)
def test_unidade_de_cada_linha(caso, rows, esperado):
    assert ta.realized_set_units(rows, catalog=CATALOG) == pytest.approx(esperado), caso


def test_soma_das_fatias_e_o_equivalente_do_bloco():
    rows = [_s(1, reps=12, rpe=9), *[_s(n, "rest_pause", r, rpe=8, code="rest_pause", anchor=1) for n, r in ((2, 6), (3, 5), (4, 4))]]
    bloco = ta.realized_block_equivalent(
        [ta.TechniqueApplication("rest_pause", reps=r, rpe=8) for r in (6, 5, 4)],
        anchor_reps=12,
        anchor_rpe=9,
        catalog=CATALOG,
    )
    assert sum(ta.realized_set_units(rows, catalog=CATALOG)[1:]) == pytest.approx(bloco.total)


# ---------------------------------------------------------------------------
# 2) Catálogo preguiçoso e fator de recuperação
# ---------------------------------------------------------------------------


def test_leitura_sem_tecnica_nao_le_o_catalogo():
    catalog = LazyCatalog(lambda: pytest.fail("catálogo lido sem aplicação de técnica"))
    assert ta.realized_set_units([_s(1), _s(2, "warmup")], catalog=catalog) == [1, 0]
    assert ta.realized_recovery_factor(_s(1), catalog) == 1.0
    assert not catalog.loaded


def test_catalogo_lido_uma_vez_por_requisicao():
    chamadas = []
    catalog = LazyCatalog(lambda: chamadas.append(1) or CATALOG)
    rows = [_s(1), _s(2, "rest_pause", 5, code="rest_pause", anchor=1)]
    ta.realized_set_units(rows, catalog=catalog)
    ta.realized_set_units(rows, catalog=catalog)
    assert len(chamadas) == 1


@pytest.mark.parametrize(
    "row, esperado",
    [
        (_s(1), 1.00),
        (_s(1, "backoff"), 0.95),
        (_s(2, "rest_pause", code="rest_pause"), 1.10),
        (_s(2, "drop_set", code="drop_set"), 1.15),
        # Legado sem código: deriva a técnica (forced_rep -> forced_reps) e usa o catálogo.
        (_s(2, "forced_rep"), 1.20),
        (_s(2, "forced_rep", code="partial_reps"), 1.05),
    ],
)
def test_fator_de_recuperacao_por_tecnica(row, esperado):
    assert ta.realized_recovery_factor(row, CATALOG) == pytest.approx(esperado)


# ---------------------------------------------------------------------------
# 3) Overview de recuperação (VolumeService)
# ---------------------------------------------------------------------------


def _overview_rows(sets, rpe=10):
    trained_at = (datetime.now() - timedelta(hours=6)).replace(microsecond=0)
    return [
        {
            "set_id": n,
            "exercise_id": 1,
            "activity_id": 500,
            "set_number": n,
            "reps": reps,
            "weight_kg": 60,
            "rpe": rpe,
            "set_type": set_type,
            "technique_code": code,
            "anchor_set_number": anchor,
            "local_date": trained_at.date().isoformat(),
            "trained_at": trained_at.isoformat(),
            "mapping_id": None,
            "original_name": "Remada",
            "primary_group": "Costas",
            "secondary_group": "Bíceps",
            "primary_group_id": None,
            "secondary_group_id": None,
            "secondary_participation": 0.5,
            "muscle_pain": None,
            "joint_pain": 0,
        }
        for n, (set_type, reps, code, anchor) in enumerate(sets, start=1)
    ]


def _overview(sets, rpe=10):
    rows = _overview_rows(sets, rpe)
    service = VolumeService()
    service._technique_catalog = _catalog()
    service._fetch_window_sets = lambda days, user_id=None: [dict(r) for r in rows]
    service._fetch_full_history_sets = lambda user_id=None, history_days=None: [dict(r) for r in rows]
    service.fetch_all = lambda *a, **k: pytest.fail("teste unitário não toca o banco")
    return {item["muscle_group"]: item for item in service.get_muscle_group_overview(days=14, limit=10, user_id=1, now_local=datetime.now())}


TRABALHO = [("working", 10, None, None), ("working", 10, None, None)]
RP = [("rest_pause", 5, "rest_pause", 2), ("rest_pause", 5, "rest_pause", 2)]


def test_overview_soma_trabalho_mais_fatia_ponderada_pelo_estimulo():
    base = _overview(TRABALHO)
    com_tecnica = _overview(TRABALHO + RP)

    # Diretas: 2 de trabalho + bloco de 1,0 (antes: 4).
    assert com_tecnica["Costas"]["direct_sets"] == 3
    # Indireto pondera pelo peso da contribuição, com a mesma unidade.
    peso = base["Bíceps"]["stimulus_equivalent_sets"] / 2
    assert com_tecnica["Bíceps"]["stimulus_equivalent_sets"] == pytest.approx(3 * peso)
    # Contagem BRUTA segue sendo uma linha por série.
    assert com_tecnica["Costas"]["sets_count"] == 4


def test_overview_dose_usa_o_fator_da_tecnica():
    tecnica = _overview(TRABALHO + RP)["Costas"]["session_dose"]
    trabalho = _overview(TRABALHO)["Costas"]["session_dose"]
    por_serie = MRS.effort_points(10) * MRS.repetition_factor(5) * MRS.exercise_factor(True)
    assert tecnica - trabalho == pytest.approx(2 * por_serie * 1.10)


def test_queda_de_reps_compara_so_series_de_trabalho():
    """Mini-bloco de rest-pause com a mesma carga e poucas reps é a técnica,
    não fadiga inesperada."""
    assert _overview(TRABALHO + RP)["Costas"]["excessive_rep_drop"] is False
    queda = [("working", 10, None, None), ("working", 5, None, None)]
    # Lote 1/P2 (2026-10-11): na falha (RPE 10) a queda e esperada e nao conta;
    # com RPE abaixo do corte ela continua sinal.
    assert _overview(queda)["Costas"]["excessive_rep_drop"] is False
    assert _overview(queda, rpe=8)["Costas"]["excessive_rep_drop"] is True


# ---------------------------------------------------------------------------
# 4) Agregados por exercício e tendência semanal
# ---------------------------------------------------------------------------


class _FetchSpy:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, sql, params=()):
        self.calls.append((sql, params))
        return [row for row in self.rows if row["exercise_id"] in params]


def test_apply_application_units_troca_a_unidade_so_de_quem_tem_aplicacao():
    sets = [
        {**_s(1, exercise=10), "set_id": 1},
        {**_s(2, exercise=10), "set_id": 2},
        {**_s(3, "drop_set", 5, code="drop_set", anchor=2, exercise=10), "set_id": 3},
        {**_s(4, "drop_set", 5, code="drop_set", anchor=2, exercise=10), "set_id": 4},
    ]
    fetch = _FetchSpy(sets)
    rows = [
        {"exercise_id": 10, "sets_count": 4, "technique_rows": 2},
        {"exercise_id": 11, "sets_count": 3, "technique_rows": 0},
    ]
    tv.apply_application_units(rows, fetch, CATALOG)
    assert [row["sets_count"] for row in rows] == [pytest.approx(3.0), 3]
    assert len(fetch.calls) == 1
    assert fetch.calls[0][1] == (10,)


def test_apply_application_units_sem_aplicacao_nao_le_nada():
    fetch = _FetchSpy([])
    rows = [{"exercise_id": n, "sets_count": 3, "technique_rows": 0} for n in range(50)]
    tv.apply_application_units(rows, fetch, CATALOG)
    assert fetch.calls == []


def test_leitura_das_aplicacoes_e_uma_por_pagina_nao_por_exercicio():
    fetch = _FetchSpy([])
    rows = [{"exercise_id": n, "sets_count": 3, "technique_rows": 1} for n in range(tv.EXERCISE_PAGE_SIZE + 10)]
    tv.apply_application_units(rows, fetch, CATALOG)
    assert len(fetch.calls) == 2


def test_tendencia_semanal_conta_a_fatia():
    trend_rows = [
        {
            "bucket_index": 0,
            "local_date": "2026-09-29",
            "exercise_row_id": 7,
            "set_id": n,
            "set_number": n,
            "set_type": set_type,
            "technique_code": code,
            "anchor_set_number": anchor,
            "reps": reps,
            "weight_kg": 40,
            "rpe": 10,
            "primary_group": "Costas",
            "secondary_group": None,
            "primary_group_id": None,
            "secondary_group_id": None,
            "mapping_id": None,
            "secondary_participation": None,
        }
        for n, (set_type, reps, code, anchor) in enumerate(TRABALHO + RP, start=1)
    ]
    service = VolumeService()
    service._technique_catalog = _catalog()
    service.fetch_all = lambda sql, params=(): trend_rows
    (linha,) = service.get_rolling_weekly_trend_by_muscle_group(days=7, user_id=1)
    assert linha["direct_sets"] == 3
    assert linha["stimulus_equivalent_sets"] == 3.0
    assert linha["sets_count"] == 4  # bruto


# ---------------------------------------------------------------------------
# 5) Novo Dashboard: mesma contagem, uma leitura a mais só com técnica
# ---------------------------------------------------------------------------


def _window() -> WindowInfo:
    end = date(2026, 9, 29)
    return WindowInfo(
        kind=WindowKind.ROLLING_7D, date_from=end - timedelta(days=6), date_to=end, timezone="America/Sao_Paulo", days=7
    )


def _technique_rows(groups):
    """Séries do exercício com técnica (2 de trabalho + rest-pause 5+5), uma
    linha por contribuição - como `TECHNIQUE_SETS_SQL` devolve."""
    base = [
        (1, "working", 10, None, None),
        (2, "working", 10, None, None),
        (3, "rest_pause", 5, "rest_pause", 2),
        (4, "rest_pause", 5, "rest_pause", 2),
    ]
    return [
        {
            "activity_id": 900,
            "activity_name": "Treino A",
            "local_date": date(2026, 9, 29),
            "trained_at": datetime(2026, 9, 29, 8),
            "exercise_row_id": 70,
            "exercise_id": 555,
            "original_name": "Remada",
            "equipment_name": "Cabo",
            "set_id": 1000 + n,
            "set_number": n,
            "set_type": set_type,
            "reps": reps,
            "rpe": 10,
            "technique_code": code,
            "anchor_set_number": anchor,
            "muscle_group_id": group_id,
            "role": role,
            "stimulus_weight": weight,
            "fatigue_weight": weight,
        }
        for n, set_type, reps, code, anchor in base
        for group_id, role, weight in groups
    ]


def _summary(monkeypatch, *, com_tecnica: bool, grupos: int):
    groups = [(1, "PRIMARY", 1.0)] + [(g, "SECONDARY", 0.5) for g in range(2, grupos + 1)]
    # O que as agregações em SQL devolvem: só as 2 séries de trabalho entram
    # nas equivalentes (`_WORK`); `technique_rows` acusa as 2 aplicações.
    role_rows = [
        {
            "muscle_group_id": g,
            "role": role,
            "sets_count": 2,
            "stimulus_sets": 2 * w,
            "fatigue_sets": 2 * w,
            "rpe_sum": 40,
            "rpe_n": 4,
            "technique_rows": 2 if com_tecnica else 0,
        }
        for g, role, w in groups
    ]
    session_rows = [
        {
            "muscle_group_id": g,
            "activity_id": 900,
            "activity_name": "Treino A",
            "local_date": date(2026, 9, 29),
            "trained_at": datetime(2026, 9, 29, 8),
            "physical_sets": 4,
            "stimulus_sets": 2 * w,
            "has_direct": 1 if role == "PRIMARY" else 0,
        }
        for g, role, w in groups
    ]
    exercise_rows = [
        {"muscle_group_id": g, "exercise_id": 555, "original_name": "Remada", "role": role, "sets_count": 4, "stimulus_sets": 2 * w, "last_local_date": date(2026, 9, 29)}
        for g, role, w in groups
    ]
    by_sql = {
        ms.ROLE_TOTALS_SQL: role_rows,
        ms._with_trained_at(ms.GROUP_SESSIONS_SQL, _window()): session_rows,
        ms.GROUP_EXERCISES_SQL: exercise_rows,
        ms.UNATTRIBUTED_SQL: [],
        ms.TECHNIQUE_SETS_SQL: _technique_rows(groups),
    }
    statements = []

    def fake_rows(conn, sql, params):
        statements.append(sql)
        return [dict(row) for row in by_sql[sql]]

    monkeypatch.setattr(ms, "_rows", fake_rows)
    monkeypatch.setattr(ms, "new_catalog", _catalog)
    monkeypatch.setattr(ms, "_planned_by_group", lambda user_id, window: {})
    monkeypatch.setattr(ms, "_projected_by_group", lambda user_id, template_plan=None, targets_by_group=None: ({}, 0, None))
    monkeypatch.setattr(ms, "_exercise_names", lambda conn, ids: {})
    items, _limitations, _unattributed = ms._build_items(
        object(),
        user_id=1,
        window=_window(),
        targets_by_group={},
        target_source=TargetSource.FALLBACK_GENERIC,
        include_readiness=False,
        now_local=datetime(2026, 9, 29, 20),
        group_names={g: f"G{g}" for g, _r, _w in groups},
    )
    return {item["muscle_group_id"]: item for item in items}, statements


def test_dashboard_soma_a_fatia_nas_series_equivalentes(monkeypatch):
    items, _ = _summary(monkeypatch, com_tecnica=True, grupos=2)
    direto, indireto = items[1], items[2]

    assert direto["realized"]["direct_sets"]["value"] == pytest.approx(3.0)
    assert direto["realized"]["effective_sets_stimulus"]["value"] == pytest.approx(3.0)
    assert indireto["realized"]["indirect_sets"]["value"] == pytest.approx(3.0)
    assert indireto["realized"]["effective_sets_stimulus"]["value"] == pytest.approx(1.5)
    # Física continua contando linha.
    assert direto["realized"]["physical_sets"]["value"] == 4
    # Top exercícios: o estímulo inteiro do grupo veio da Remada.
    (top,) = direto["top_exercises"]
    assert top["effective_sets_stimulus"] == pytest.approx(3.0)
    assert top["contribution_share"] == pytest.approx(1.0)


@pytest.mark.parametrize("grupos", [2, 12])
def test_dashboard_tecnica_custa_uma_leitura_so_e_nunca_por_grupo(monkeypatch, grupos):
    _, sem = _summary(monkeypatch, com_tecnica=False, grupos=grupos)
    _, com = _summary(monkeypatch, com_tecnica=True, grupos=grupos)
    assert ms.TECHNIQUE_SETS_SQL not in sem
    assert len(com) == len(sem) + 1
    assert com.count(ms.TECHNIQUE_SETS_SQL) == 1


def test_drilldown_pondera_cada_serie_pela_sua_unidade(monkeypatch):
    monkeypatch.setattr(ms, "new_catalog", _catalog)
    rows = [{**row, "position_in_session": 1, "weight_kg": 40} for row in _technique_rows([(1, "PRIMARY", 1.0)])]
    (sessao,) = ms._group_sets_into_sessions(rows, {})
    assert sessao["effective_sets_stimulus"] == pytest.approx(3.0)
    assert sessao["physical_sets"] == 4
    assert [s["effective_sets_stimulus"] for s in sessao["exposures"][0]["sets"]] == [1, 1, 0.5, 0.5]


# ---------------------------------------------------------------------------
# 6) Aderência: completude só com trabalho; técnica aplicada X/N
# ---------------------------------------------------------------------------


def _plan_row(**extra):
    return {
        "plan_exercise_id": 1,
        "plan_sequence": 1,
        "exercise_name": "Remada",
        "mapping_id": 555,
        "sets_count": 3,
        "reps_target": "8-10",
        **extra,
    }


def _performed(working, technique, equivalent=None):
    return {
        "activity_id": 900,
        "exercise_row_id": 70,
        "position_in_session": 1,
        "original_name": "Remada",
        "workout_plan_exercise_id": 1,
        "exercise_id": 555,
        "total_sets": working + technique,
        "working_sets": working,
        "technique_sets": technique,
        "technique_equivalent_sets": equivalent,
        "total_reps": 30,
        "best_weight_kg": 40,
        "rpe_sum": 0,
        "rpe_n": 0,
    }


@pytest.mark.parametrize(
    "caso, plan_extra, working, technique, equivalent, status, esperado",
    [
        (
            "aplicação não completa série de trabalho que faltou",
            {"advanced_technique": "rest_pause", "advanced_technique_applications": 3},
            2, 3, 1.0, "INTERRUPTED",
            {"code": "rest_pause", "planned_applications": 3, "performed_applications": 3, "equivalent_sets": 1.0},
        ),
        (
            "técnica aplicada parcialmente",
            {"advanced_technique": "drop_set", "advanced_technique_applications": 2},
            3, 1, 0.5, "COMPLETED",
            {"code": "drop_set", "planned_applications": 2, "performed_applications": 1, "equivalent_sets": 0.5},
        ),
        (
            "FST-7 só preenche séries: sem N",
            {"advanced_technique": "fst7", "advanced_technique_applications": None},
            3, 0, None, "COMPLETED",
            {"code": "fst7", "planned_applications": None, "performed_applications": 0, "equivalent_sets": None},
        ),
        ("sem técnica na ficha nem registrada: sem bloco", {}, 3, 0, None, "COMPLETED", None),
    ],
)
def test_aderencia_tecnica_aplicada(caso, plan_extra, working, technique, equivalent, status, esperado):
    (item,) = ad.build_session_items([_plan_row(**plan_extra)], [_performed(working, technique, equivalent)])
    assert item["status"] == status, caso
    assert item["technique"] == esperado, caso
    assert item["performed"]["working_sets"] == working
    assert "technique_equivalent_sets" not in item["performed"]
    PlanItemOut.model_validate(item)  # o contrato fechado aceita o bloco novo


def test_sql_de_completude_nao_conta_aplicacao():
    assert "technique_sets" in ad.SESSION_EXERCISES_SQL
    assert "'rest_pause'" in ad.SESSION_EXERCISES_SQL
    assert "advanced_technique_applications" in ad.PLAN_ITEMS_SQL
