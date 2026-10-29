"""`/dashboard/progressao` usa a MESMA regra da home e do drill-down.

Antes, o veredito da tela vinha de `classify_progression_trend` (e1RM contra a
MEDIA das sessoes, sem chave de equipamento, >= 3 sessoes) e a seta por linha
do `TrendBadge` comparava so o e1RM com a linha anterior, sem limiar. A home
(`exposure_service.classify_batch`) e o drill-down (`build_exercise_progress`)
usam `comparability.classify_exercise_progression`. Resultado: o mesmo
exercicio no mesmo periodo tinha status diferentes.

Estes testes provam a paridade (mesmas exposicoes -> mesmo status nas tres
telas) e a seta por sessao calculada no backend com `compare_exposures`.
Sem banco: cursor fake e dubles nas leituras.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import analytics, load_conventions, security
from app.intelligence.dashboard import exposure_service as svc
from app.intelligence.dashboard.contracts import WindowInfo, WindowKind
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus

EXERCICIO = 1001
USUARIO = 7
SQUAT_RACK = 11
SMITH = 12
HOJE = date(2026, 10, 6)
JANELA = WindowInfo(
    kind=WindowKind.CUSTOM, date_from=date(2026, 9, 7), date_to=HOJE, timezone="America/Sao_Paulo", days=30
)

PROGRESSING = ExerciseProgressionStatus.PROGRESSING.value
REGRESSING = ExerciseProgressionStatus.REGRESSING.value
STABLE = ExerciseProgressionStatus.STABLE.value
NOT_COMPARABLE = ExerciseProgressionStatus.NOT_COMPARABLE.value


@pytest.fixture(autouse=True)
def _sem_cache_de_coluna():
    load_conventions.reset_load_convention_column_cache()
    yield
    load_conventions.reset_load_convention_column_cache()


# ---------------------------------------------------------------------------
# Dubles
# ---------------------------------------------------------------------------


def exposicao(row_id: int, activity_id: int, dia: date, series, *, equipment_id=SQUAT_RACK) -> list[dict]:
    """Linhas planas de UMA exposicao no formato das queries do
    `exposure_service` (individual e lote): `series` = [(reps, kg, rpe)]."""
    return [
        {
            "canonical_exercise_id": EXERCICIO,
            "exercise_name": "Agachamento livre",
            "exercise_row_id": row_id,
            "activity_id": activity_id,
            "local_date": dia,
            "started_at": datetime(dia.year, dia.month, dia.day, 19, 0, 0),
            "position_in_session": 1,
            "equipment_id": equipment_id,
            "equipment_name": {SQUAT_RACK: "Gaiola", SMITH: "Smith"}.get(equipment_id),
            "load_convention": None,
            "prescribed_reps_target": "8-12",
            "set_number": numero,
            "reps": reps,
            "weight_kg": kg,
            "rpe": rpe,
            "rest_seconds": 120,
            "notes": None,
        }
        for numero, (reps, kg, rpe) in enumerate(series, start=1)
    ]


def agachamento_real() -> list[dict]:
    """Cenario sintetico baseado em caso real: Agachamento livre, 50 kg,
    mesmo aparelho.
    02/10 11@9, 10@9, 10@9 contra 06/10 14@9.5, 13@9."""
    return exposicao(1, 100, date(2026, 10, 2), [(11, 50.0, 9.0), (10, 50.0, 9.0), (10, 50.0, 9.0)]) + exposicao(
        2, 200, date(2026, 10, 6), [(14, 50.0, 9.5), (13, 50.0, 9.0)]
    )


class FakeCursor:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.db.executed.append((normalized, tuple(params)))
        if normalized.startswith("SELECT DATABASE()"):
            self._result = [{"db": "musclefit"}]
        elif "information_schema.COLUMNS" in normalized:
            self._result = [{"n": 1}]
        elif normalized.startswith("SELECT sel.exercise_row_id"):
            self._result = self.db.rows
        elif "FROM exercise_catalog ec" in normalized:
            self._result = [{"exercise_id": EXERCICIO, "exercise_name": "Agachamento livre"}]
        else:
            self._result = []

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeDb:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed: list[tuple[str, tuple]] = []

    def cursor(self):
        return FakeCursor(self)

    def exposure_params(self) -> list[tuple]:
        return [params for sql, params in self.executed if sql.startswith("SELECT sel.exercise_row_id")]


def tela(rows) -> dict:
    """Veredito da tela de progressao (rota v2)."""
    exposures = svc.get_exposures_between(
        FakeDb(rows), EXERCICIO, user_id=USUARIO, date_from=JANELA.date_from, date_to=JANELA.date_to
    )
    return svc.progression_verdict(exposures)


def home(rows) -> dict:
    """Status do bloco "Progressao" da home (caminho em lote)."""
    itens, _truncated = svc.classify_batch(rows, per_exercise_limit=svc.MAX_BATCH_EXPOSURES_PER_EXERCISE)
    (item,) = itens
    return item


def drill_down(rows) -> dict:
    return svc.build_exercise_progress(FakeDb(rows), EXERCICIO, user_id=USUARIO, window=JANELA)


# ---------------------------------------------------------------------------
# Paridade do veredito
# ---------------------------------------------------------------------------


def test_caso_real_do_agachamento_e_progresso_nas_tres_telas():
    rows = agachamento_real()
    verdict = tela(rows)

    assert verdict["status"] == PROGRESSING
    assert "same_load_more_reps" in verdict["status_reasons"]
    assert "fewer_working_sets" in verdict["status_reasons"]
    assert verdict["status"] == home(rows)["status"] == drill_down(rows)["status"]
    assert verdict["status_reasons"] == home(rows)["status_reasons"] == drill_down(rows)["status_reasons"]
    # a tela diz onde comparou
    assert verdict["equipment_name"] == "Gaiola"
    assert verdict["load_convention"] == "total"
    assert verdict["comparable_exposures"] == 2


def _cenarios():
    d = lambda dia: date(2026, 9, dia)  # noqa: E731
    return {
        "regressao_mesma_carga": exposicao(1, 1, d(10), [(10, 60.0, 8.0)] * 3)
        + exposicao(2, 2, d(14), [(8, 60.0, 8.0)] * 3),
        "plato": exposicao(1, 1, d(10), [(10, 60.0, 8.0)] * 3)
        + exposicao(2, 2, d(14), [(10, 60.0, 8.0)] * 3)
        + exposicao(3, 3, d(18), [(10, 60.0, 8.0)] * 3),
        "troca_de_equipamento": exposicao(1, 1, d(10), [(10, 60.0, 8.0)] * 3)
        + exposicao(2, 2, d(14), [(10, 50.0, 8.0)] * 3, equipment_id=SMITH),
        "uma_exposicao": exposicao(1, 1, d(10), [(10, 60.0, 8.0)] * 3),
        "e1rm_decide_com_poucas_reps": exposicao(1, 1, d(10), [(6, 80.0, 8.0)] * 3)
        + exposicao(2, 2, d(14), [(6, 85.0, 8.0)] * 3),
    }


@pytest.mark.parametrize("nome", list(_cenarios()))
def test_mesmas_exposicoes_mesmo_status_na_tela_na_home_e_no_drill_down(nome):
    rows = _cenarios()[nome]
    verdict = tela(rows)
    assert verdict["status"] == home(rows)["status"] == drill_down(rows)["status"], nome
    assert verdict["status_reasons"] == home(rows)["status_reasons"] == drill_down(rows)["status_reasons"], nome


def test_sem_exposicoes_e_dados_insuficientes_sem_chave():
    verdict = svc.progression_verdict([])
    assert verdict["status"] == ExerciseProgressionStatus.INSUFFICIENT_DATA.value
    assert verdict["comparability_key"] is None and verdict["equipment_name"] is None


def test_leitura_da_tela_e_a_mesma_query_do_drill_down_com_as_datas_da_tela():
    db = FakeDb(agachamento_real())
    svc.get_exposures_between(db, EXERCICIO, user_id=USUARIO, date_from=date(2026, 9, 1), date_to=HOJE, limit=500)
    ((owner, canonical, _goal, inicio, fim, cap),) = db.exposure_params()
    assert (owner, canonical, inicio, fim) == (USUARIO, EXERCICIO, date(2026, 9, 1), HOJE)
    assert cap == svc.MAX_TABLE_EXPOSURES  # teto da tabela, nunca ilimitado


# ---------------------------------------------------------------------------
# Seta por sessao (`consecutive_comparisons`)
# ---------------------------------------------------------------------------


def _exposures(rows):
    return svc.get_exposures_between(
        FakeDb(rows), EXERCICIO, user_id=USUARIO, date_from=JANELA.date_from, date_to=JANELA.date_to
    )


def test_seta_do_caso_real_e_progresso_com_aviso_de_menos_series():
    setas = svc.consecutive_comparisons(_exposures(agachamento_real()))
    assert setas[0] is None  # primeira exposicao lida: sem anterior
    assert setas[1]["status"] == PROGRESSING
    assert "fewer_working_sets" in setas[1]["reasons"]


def test_seta_da_ultima_sessao_e_a_ultima_transicao_do_veredito():
    for nome, rows in _cenarios().items():
        exposures = _exposures(rows)
        verdict = svc.progression_verdict(exposures)
        ultima = svc.consecutive_comparisons(exposures)[-1]
        if verdict["status"] in (PROGRESSING, REGRESSING, NOT_COMPARABLE):
            assert ultima["status"] == verdict["status"], nome
            assert ultima["reasons"] == verdict["status_reasons"], nome


def test_troca_de_equipamento_nao_compara():
    d = lambda dia: date(2026, 9, dia)  # noqa: E731
    rows = (
        exposicao(1, 1, d(10), [(10, 60.0, 8.0)] * 3)
        + exposicao(2, 2, d(14), [(10, 40.0, 8.0)] * 3, equipment_id=SMITH)  # carga menor em outra maquina
        + exposicao(3, 3, d(18), [(12, 40.0, 8.0)] * 3, equipment_id=SMITH)
    )
    setas = svc.consecutive_comparisons(_exposures(rows))

    assert setas[0] is None
    # 60 kg -> 40 kg em OUTRA maquina nao e regressao: e "nao comparavel"
    assert setas[1] == {"status": NOT_COMPARABLE, "reasons": ["comparability_key_changed"]}
    # dentro da nova chave volta a comparar normalmente
    assert setas[2]["status"] == PROGRESSING


def test_sem_equipamento_resolvido_no_meio_avisa_o_motivo():
    d = lambda dia: date(2026, 9, dia)  # noqa: E731
    rows = exposicao(1, 1, d(10), [(10, 60.0, 8.0)] * 3) + exposicao(
        2, 2, d(14), [(10, 60.0, 8.0)] * 3, equipment_id=None
    )
    setas = svc.consecutive_comparisons(_exposures(rows))
    assert setas[1]["status"] == NOT_COMPARABLE
    assert setas[1]["reasons"] == ["comparability_key_changed", "equipment_not_resolved"]


def test_sem_progresso_nem_regressao_e_estavel():
    d = lambda dia: date(2026, 9, dia)  # noqa: E731
    rows = exposicao(1, 1, d(10), [(10, 60.0, 8.0)] * 3) + exposicao(2, 2, d(14), [(10, 60.0, 8.0)] * 3)
    assert svc.consecutive_comparisons(_exposures(rows))[1]["status"] == STABLE


# ---------------------------------------------------------------------------
# Contrato das rotas v2
# ---------------------------------------------------------------------------


def _sessao(activity_id: int, dia: str) -> dict:
    return {"activity_id": activity_id, "local_date": dia, "activity_name": "Treino A", "set_type": "working",
            "reps": 10, "weight_kg": 40.0, "rpe": 9.0, "set_number": 1, "gym_equipment_id": None}


@pytest.fixture
def rotas(monkeypatch):
    log: dict = {"exposures": [], "falhar": False, "rows": []}

    def fake_exposures(user_id, canonical, days, date_range, limit, training_goal=None):
        log["exposures"].append(
            {"canonical": canonical, "days": days, "date_range": date_range, "limit": limit, "goal": training_goal}
        )
        if log["falhar"]:
            raise RuntimeError("Data API fora")
        return _exposures(log["rows"])

    monkeypatch.setattr(analytics, "_user_today", lambda user_id: HOJE)
    monkeypatch.setattr(analytics, "_progression_exposures", fake_exposures)
    monkeypatch.setattr(analytics, "_failure_allowed_for_rows", lambda rows: None)
    monkeypatch.setattr(
        analytics,
        "_fetch_exercise_set_rows",
        lambda user_id, days, exercise_name=None, mapping_id=None, date_range=None, training_goal=None: [
            _sessao(100, "2026-10-02"),
            _sessao(200, "2026-10-06"),
            _sessao(300, "2026-10-06"),  # sessao sem exposicao correspondente
        ],
    )

    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[security.get_current_identity] = lambda: security.Identity(
        user_id=USUARIO, role="regular", source="session"
    )
    return TestClient(app), log


def test_rota_do_veredito_devolve_o_status_da_home(rotas):
    client, log = rotas
    log["rows"] = agachamento_real()

    body = client.get(f"/analytics/dashboard-progression-trend?mapping_id={EXERCICIO}&days=30").json()

    assert body["status"] == PROGRESSING
    assert body["status"] == home(agachamento_real())["status"]
    assert "fewer_working_sets" in body["status_reasons"]
    assert body["equipment_name"] == "Gaiola"
    assert body["comparable_exposures"] == 2
    # campos legados (deprecados) seguem coerentes com o novo veredito
    assert body["classification"] == "PROGREDINDO"
    # M97 F4: sem `?goal=` o veredito le o objetivo default (o de sempre).
    assert log["exposures"] == [
        {"canonical": EXERCICIO, "days": 30, "date_range": None, "limit": svc.MAX_EXPOSURES, "goal": "hypertrophy"}
    ]


def test_rota_da_tabela_entrega_a_seta_pronta_por_sessao(rotas):
    client, log = rotas
    log["rows"] = agachamento_real()

    body = client.get(f"/analytics/dashboard-progression?mapping_id={EXERCICIO}&days=30").json()
    evolucao = {item["activity_id"]: item["evolution"] for item in body["items"]}

    assert evolucao[100] is None
    assert evolucao[200]["status"] == PROGRESSING
    assert "fewer_working_sets" in evolucao[200]["reasons"]
    assert evolucao[300] is None  # sem exposicao: sem seta inventada
    assert log["exposures"][0]["limit"] == svc.MAX_TABLE_EXPOSURES


def test_duas_exposicoes_na_mesma_sessao_a_seta_e_a_da_ultima(rotas):
    client, log = rotas
    dia = date(2026, 10, 6)
    log["rows"] = (
        exposicao(1, 100, date(2026, 10, 2), [(10, 40.0, 8.0)] * 2)
        + exposicao(2, 200, dia, [(12, 40.0, 8.0)] * 2)  # progresso
        + exposicao(3, 200, dia, [(8, 40.0, 8.0)] * 2)  # regressao contra a de cima
    )
    body = client.get(f"/analytics/dashboard-progression?mapping_id={EXERCICIO}&days=30").json()
    evolucao = {item["activity_id"]: item["evolution"] for item in body["items"]}
    assert evolucao[200]["status"] == REGRESSING


def test_falha_na_leitura_das_exposicoes_nao_derruba_a_tabela(rotas):
    client, log = rotas
    log["falhar"] = True
    resp = client.get(f"/analytics/dashboard-progression?mapping_id={EXERCICIO}&days=30")
    assert resp.status_code == 200
    assert all(item["evolution"] is None for item in resp.json()["items"])


def test_janela_custom_chega_inteira_as_duas_rotas(rotas):
    client, log = rotas
    faixa = "date_from=2026-09-01&date_to=2026-10-06"
    client.get(f"/analytics/dashboard-progression?mapping_id={EXERCICIO}&{faixa}")
    client.get(f"/analytics/dashboard-progression-trend?mapping_id={EXERCICIO}&{faixa}")
    assert [chamada["date_range"] for chamada in log["exposures"]] == [(date(2026, 9, 1), HOJE)] * 2


def test_janela_por_days_termina_hoje_no_fuso_do_usuario_como_a_home(monkeypatch):
    """`days=180` = os mesmos 180 dias de `window=180d` (hoje inclusive)."""
    from app.intelligence.dashboard import time_windows

    db = FakeDb(agachamento_real())

    class _Conn:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(analytics, "_connect", lambda: _Conn())
    monkeypatch.setattr(time_windows, "user_timezone", lambda conn, user_id: "America/Sao_Paulo")
    monkeypatch.setattr(time_windows, "user_today", lambda tz: HOJE)

    analytics._progression_exposures(USUARIO, EXERCICIO, 180, None, svc.MAX_EXPOSURES)

    ((_owner, _canonical, _goal, inicio, fim, cap),) = db.exposure_params()
    esperado = time_windows.resolve_window("180d", today=HOJE)
    assert (inicio, fim) == (esperado.date_from, esperado.date_to)
    assert cap == svc.MAX_EXPOSURES
