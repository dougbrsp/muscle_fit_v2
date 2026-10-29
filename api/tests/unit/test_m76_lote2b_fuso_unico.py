"""M76 Lote 2b - o fuso do usuario como fonte unica de dia e semana.

Dono: `app/intelligence/dashboard/time_windows.py` (`user_now`, `user_today`,
`utc_offset_minutes`, `trained_at_sql`/`with_trained_at`, `user_day_start_db`,
`user_day_end_db`, `user_day_end_db_sql`, `db_instant_to_user_date`).

O caso que importa e a virada: 23h30 de Brasilia (domingo, 04/10/2026) e
02h30 UTC de segunda (05/10). O Aurora e o processo da Lambda ja estao na
segunda; o usuario ainda esta no domingo. Antes do Lote 2b, `CURDATE()` e
`date.today()` respondiam "segunda" e a janela/semana andava um dia.

Sem banco: consumidores recebem dubles de `fetch_all`/cursor.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.intelligence.dashboard import time_windows as tw
from tests.support.isolamento_v3 import congelar_relogio
from tests.support.fake_db import CursorFactoryConn

SP = "America/Sao_Paulo"
VIRADA_UTC = datetime(2026, 10, 5, 2, 30, tzinfo=timezone.utc)  # 23h30 BRT de domingo, 04/10
DOMINGO = date(2026, 10, 4)


# ---------------------------------------------------------------------------
# Dono: helpers de `time_windows`
# ---------------------------------------------------------------------------


def test_hoje_do_usuario_na_virada_ainda_e_domingo():
    assert tw.user_today(SP, VIRADA_UTC) == DOMINGO
    assert tw.user_now(SP, VIRADA_UTC) == datetime(2026, 10, 4, 23, 30)
    assert tw.user_today("UTC", VIRADA_UTC) == date(2026, 10, 5)


def test_deslocamento_do_fuso_em_minutos():
    assert tw.utc_offset_minutes(SP, VIRADA_UTC) == -180
    assert tw.utc_offset_minutes("UTC", VIRADA_UTC) == 0
    assert tw.utc_offset_minutes("Asia/Kolkata", VIRADA_UTC) == 330
    # Fuso invalido cai no default (Sao Paulo), como `user_now`.
    assert tw.utc_offset_minutes("Nao/Existe", VIRADA_UTC) == -180


def test_trained_at_sql_converte_so_o_created_at():
    expr = tw.trained_at_sql("a", offset_minutes=-180)
    assert expr == "COALESCE(a.started_at, a.ended_at, DATE_ADD(a.created_at, INTERVAL -180 MINUTE))"
    # Sem ':' (o shim da Data API trataria como parametro nomeado) nem '%'.
    assert ":" not in expr and "%" not in expr
    assert tw.trained_at_sql("", offset_minutes=0) == (
        "COALESCE(started_at, ended_at, DATE_ADD(created_at, INTERVAL 0 MINUTE))"
    )


def test_with_trained_at_troca_o_marcador_e_preserva_outras_chaves():
    sql = "SELECT {trained_at} AS trained_at, JSON_OBJECT('k', '{x}') FROM activities a"
    resolvido = tw.with_trained_at(sql, SP)
    assert tw.TRAINED_AT_TOKEN not in resolvido
    assert "DATE_ADD(a.created_at, INTERVAL -180 MINUTE)" in resolvido
    assert "'{x}'" in resolvido


def test_inicio_e_fim_do_dia_do_usuario_no_relogio_do_banco():
    assert tw.user_day_start_db(DOMINGO, SP) == datetime(2026, 10, 4, 3, 0, 0)
    assert tw.user_day_end_db(DOMINGO, SP) == datetime(2026, 10, 5, 2, 59, 59)
    # A virada (02h30 UTC de segunda) ainda pertence ao domingo do usuario.
    virada_naive = VIRADA_UTC.replace(tzinfo=None)
    assert tw.user_day_start_db(DOMINGO, SP) <= virada_naive <= tw.user_day_end_db(DOMINGO, SP)
    assert tw.db_instant_to_user_date(virada_naive, SP) == DOMINGO


def test_fim_exclusivo_do_dia_em_sql():
    assert tw.user_day_end_db_sql("wp.plan_date", -180) == "DATE_ADD(wp.plan_date, INTERVAL 1620 MINUTE)"
    assert tw.user_day_end_db_sql("wp.plan_date", 0) == "DATE_ADD(wp.plan_date, INTERVAL 1440 MINUTE)"


# ---------------------------------------------------------------------------
# Consumidores
# ---------------------------------------------------------------------------


@pytest.fixture
def virada(monkeypatch):
    """Relogio congelado nas 23h30 de domingo em Brasilia (02h30Z de segunda)."""
    congelar_relogio(monkeypatch, datetime(2026, 10, 4, 23, 30))


def test_semana_do_resumo_semanal_e_a_do_usuario(virada, monkeypatch):
    """`training_load_service`: a semana civil (segunda..domingo) e "hoje"
    saem do dia do usuario. Na virada, `WEEKDAY(CURDATE())` ja dava a semana
    SEGUINTE inteira (segunda 05/10); o usuario esta no domingo 04/10."""
    from app.intelligence import training_load_service as tls

    chamadas: list[tuple[str, tuple]] = []

    def fake_fetch_all(self, sql, params=()):
        chamadas.append((sql, tuple(params)))
        return []

    monkeypatch.setattr(tls.TrainingLoadService, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(tls.TrainingLoadService, "_load_convention_expr", staticmethod(lambda: "NULL"))
    tls.TrainingLoadService().get_weekly_summary(user_id=7)

    sem_fuso = [(sql, params) for sql, params in chamadas if "timezone FROM users" not in sql]
    assert all("CURDATE()" not in sql for sql, _ in sem_fuso)
    totais, planos, _ultimos, proximo = sem_fuso
    assert totais[1] == (date(2026, 9, 28), 7)
    assert planos[1] == (date(2026, 9, 28), date(2026, 10, 5), 7)
    assert proximo[1] == (DOMINGO, 7)


def test_janela_do_volume_conta_do_dia_do_usuario(virada, monkeypatch):
    """`VolumeService._fetch_window_sets`: `days` contados do domingo do
    usuario, e o `trained_at` com o `created_at` convertido."""
    from app.intelligence.volume_service import VolumeService

    service = VolumeService()
    chamadas: list[tuple[str, tuple]] = []

    def fake_fetch_all(sql, params=()):
        chamadas.append((sql, tuple(params)))
        return []

    service.fetch_all = fake_fetch_all
    service._fetch_window_sets(7, user_id=3)
    (sql, params) = [c for c in chamadas if "FROM activities a WHERE" in c[0]][0]
    assert "CURDATE()" not in sql
    assert params == (DOMINGO - timedelta(days=7), 3)
    trained_at = service._trained_at_sql(3)
    assert trained_at == "COALESCE(a.started_at, a.ended_at, DATE_ADD(a.created_at, INTERVAL -180 MINUTE))"


def test_limite_diario_de_geracao_conta_no_dia_do_usuario():
    """`periodization/logs.count_generations_today`: o dia comeca a 00h de
    Brasilia (03h UTC) - antes, `created_at >= CURDATE()` zerava o contador
    as 21h e jogava as geracoes da noite para o dia seguinte."""
    from app.periodization.logs import count_generations_today

    executados: list[tuple[str, tuple]] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            executados.append((sql, tuple(params or ())))

        def fetchone(self):
            sql = executados[-1][0]
            return {"timezone": SP} if "timezone FROM users" in sql else {"n": 4}

    assert count_generations_today(CursorFactoryConn(_Cursor), 9, now_utc=VIRADA_UTC.replace(tzinfo=None)) == 4
    sql, params = executados[-1]
    assert "CURDATE()" not in sql and "created_at >= %s" in sql
    assert params == (9, datetime(2026, 10, 4, 3, 0, 0))


def test_tendencia_semanal_global_ancora_no_hoje_do_usuario(virada, monkeypatch):
    """`analytics.compute_rolling_weekly_trend`: bucket e janela a partir do
    domingo do usuario (o fuso cai no default quando o banco nao responde)."""
    from app import analytics

    capturado: list[tuple[str, tuple]] = []
    monkeypatch.setattr(analytics, "_fetch_all", lambda sql, params=None: capturado.append((sql, params)) or [])
    monkeypatch.setattr(analytics, "_load_convention_expr", lambda: "NULL")

    def sem_banco():
        raise RuntimeError("sem banco no teste unitario")

    monkeypatch.setattr(analytics, "_connect", sem_banco)
    analytics.compute_rolling_weekly_trend(14, user_id=5)
    sql, params = capturado[0]
    assert "CURDATE()" not in sql and "DATEDIFF(%s, a.local_date)" in sql
    assert params == (DOMINGO, DOMINGO - timedelta(days=14), 5)


def test_versao_da_ficha_por_data_usa_o_fim_do_dia_do_usuario():
    from app.template_versions import plan_version_expr

    expr = plan_version_expr("wp", by_date=True)
    assert "DATE_ADD(wp.plan_date, INTERVAL 1620 MINUTE)" in expr
    assert "INTERVAL 1 DAY" not in expr
