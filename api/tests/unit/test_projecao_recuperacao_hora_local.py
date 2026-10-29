"""A projecao de recuperacao usada para gerar/congelar a ficha mede a
antecedencia ate `plan_date`/`plan_time` a partir da hora LOCAL do usuario -
a mesma referencia de `hours_since_trained` -, nunca do relogio do processo.

Cenario sintetico baseado em caso real: treino 2026-10-05 06:00 local (UTC-3), ficha criada em
2026-10-04 19:00 local = 22:00 UTC. Com `datetime.now()` (UTC na Lambda) a
projecao usava 8h de antecedencia em vez de 11h.

Rodar: cd api && python -m pytest tests/unit/test_projecao_recuperacao_hora_local.py
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from app import workout_plan_ai
from app.generation import freeze_mode_b
from app.intelligence import recovery_projection
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.intelligence.volume_service import VolumeService
from tests.support.isolamento_v3 import congelar_relogio

_REAL_DATETIME = datetime


def _servidor_em_utc(instante_utc: datetime):
    """`datetime` cujo `now()` devolve o relogio UTC do processo (Lambda)."""

    class _DatetimeServidor(_REAL_DATETIME):
        @classmethod
        def now(cls, tz=None):
            return instante_utc if tz is None else instante_utc.replace(tzinfo=tz)

    return _DatetimeServidor


@pytest.fixture
def relogio(monkeypatch):
    """Usuario em America/Sao_Paulo (UTC-3), processo em UTC."""

    def configurar(local: datetime) -> dict:
        congelar_relogio(monkeypatch, local)
        utc = local + timedelta(hours=3)
        for modulo in (freeze_mode_b, workout_plan_ai):
            monkeypatch.setattr(modulo, "datetime", _servidor_em_utc(utc))
        monkeypatch.setattr(VolumeService, "_user_timezone_name", lambda self, user_id: "America/Sao_Paulo")

        capturado: dict = {}

        def fake_overview(self, days=14, limit=24, user_id=None, **kwargs):
            capturado["now_local"] = kwargs.get("now_local")
            return [{"muscle_group_id": 3}]

        def fake_project(items, now, target):
            capturado["now"] = now
            capturado["target"] = target
            return []

        monkeypatch.setattr(MuscleRecoveryService, "get_recovery_overview", fake_overview)
        monkeypatch.setattr(recovery_projection, "project_recovery_items", fake_project)
        monkeypatch.setattr(workout_plan_ai, "project_recovery_items", fake_project)
        return capturado

    return configurar


def _ahead(capturado: dict) -> timedelta:
    return capturado["target"] - capturado["now"]


# --- congelamento (freeze_mode_b / freeze_mode_a) ---------------------------


def test_congelamento_projeta_da_hora_local_ficha_131(relogio):
    capturado = relogio(datetime(2026, 10, 4, 19, 0))

    freeze_mode_b.projected_readiness(42, date(2026, 10, 5), timedelta(hours=6))

    assert capturado["now"] == datetime(2026, 10, 4, 19, 0)
    assert capturado["target"] == datetime(2026, 10, 5, 6, 0)
    assert _ahead(capturado) == timedelta(hours=11)
    # Retrato e projecao medem a partir do MESMO instante.
    assert capturado["now_local"] == capturado["now"]


def test_congelamento_treino_proximo_nao_zera_antecedencia(relogio):
    # 05:30 local (08:30 UTC) para treino as 06:00 local: em UTC o "agora" ja
    # passou do alvo e `max(now, target)` zerava a antecedencia.
    capturado = relogio(datetime(2026, 10, 5, 5, 30))

    freeze_mode_b.projected_readiness(42, date(2026, 10, 5), time(6, 0))

    assert _ahead(capturado) == timedelta(minutes=30)


# --- geracao do rascunho (project_recovery_for_date) ------------------------


def test_rascunho_projeta_da_hora_local_ficha_131(relogio):
    capturado = relogio(datetime(2026, 10, 4, 19, 0))

    workout_plan_ai.project_recovery_for_date([], "2026-10-05", "06:00", user_id=42)

    assert capturado["now"] == datetime(2026, 10, 4, 19, 0)
    assert _ahead(capturado) == timedelta(hours=11)


def test_rascunho_treino_proximo_nao_zera_antecedencia(relogio):
    capturado = relogio(datetime(2026, 10, 5, 5, 30))

    workout_plan_ai.project_recovery_for_date([], "2026-10-05", "06:00", user_id=42)

    assert _ahead(capturado) == timedelta(minutes=30)


def test_rascunho_usa_o_agora_informado_pelo_chamador(relogio):
    capturado = relogio(datetime(2026, 10, 4, 19, 0))
    agora = datetime(2026, 10, 4, 18, 0)

    workout_plan_ai.project_recovery_for_date([], "2026-10-05", "06:00", now=agora)

    assert capturado["now"] == agora
    assert _ahead(capturado) == timedelta(hours=12)


def test_user_now_e_a_hora_local_do_usuario(relogio):
    relogio(datetime(2026, 10, 4, 19, 0))

    assert MuscleRecoveryService().user_now(42) == datetime(2026, 10, 4, 19, 0)
