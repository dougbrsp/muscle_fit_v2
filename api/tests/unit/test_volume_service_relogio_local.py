"""`VolumeService` mede o tempo na hora LOCAL do usuario, nunca no relogio UTC
do servidor.

Os horarios de treino (`activities.started_at` -> `trained_at`) sao gravados
na hora local, sem fuso (convencao restaurada no #305). A Lambda roda em UTC:
com `datetime.now()` as horas desde o ultimo estimulo saiam +3h infladas
(America/Sao_Paulo) e o corte de progressao de e1RM andava 3h para frente.

Cenario fixo: servidor em 2026-10-02 12:00Z (= 09:00 em Sao Paulo), treino as
08:00 locais -> 1h desde o estimulo, e nao 4h."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.intelligence import volume_service as volume_module
from app.intelligence.dashboard import time_windows
from app.intelligence.volume_service import VolumeService

AGORA_UTC = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
AGORA_UTC_NAIVE = AGORA_UTC.replace(tzinfo=None)  # o que `datetime.now()` da Lambda devolve
AGORA_LOCAL = datetime(2026, 10, 2, 9, 0, 0)  # America/Sao_Paulo (UTC-3)


@pytest.fixture(autouse=True)
def relogio_utc_fixo(monkeypatch):
    """Servidor em UTC: tanto o `datetime.now()` ingenuo do modulo quanto o
    instante UTC de `user_now` apontam para 12:00Z."""
    real_user_now = time_windows.user_now
    monkeypatch.setattr(
        time_windows, "user_now", lambda tz, now_utc=None: real_user_now(tz, now_utc or AGORA_UTC)
    )

    class _DatetimeServidor(datetime):
        @classmethod
        def now(cls, tz=None):
            return AGORA_UTC.astimezone(tz) if tz else AGORA_UTC_NAIVE

    monkeypatch.setattr(volume_module, "datetime", _DatetimeServidor)


def _row(set_id: int, activity_id: int, trained_at: datetime, *, weight: float = 80.0) -> dict:
    return {
        "set_id": set_id,
        "exercise_id": 1,
        "activity_id": activity_id,
        "set_number": 1,
        "reps": 8,
        "weight_kg": weight,
        "rpe": 8,
        "set_type": "working",
        "local_date": trained_at.date().isoformat(),
        "trained_at": trained_at.isoformat(),
        "mapping_id": None,
        "original_name": "Supino reto",
        "primary_group": "Peitoral",
        "secondary_group": None,
        "primary_group_id": None,
        "secondary_group_id": None,
        "secondary_participation": None,
        "muscle_pain": None,
        "joint_pain": 0,
        "activity_workout_plan_id": None,
        "raw_payload_workout_plan_id": None,
    }


def _service(rows: list[dict], *, timezone_name: str | None = "America/Sao_Paulo") -> VolumeService:
    service = VolumeService()
    service._fetch_window_sets = lambda days, user_id=None: rows
    service._fetch_full_history_sets = lambda user_id=None, history_days=None: rows
    consultas: list[str] = []

    def _fetch_one(sql, params=()):
        consultas.append(sql)
        assert "FROM users" in sql, sql
        return {"timezone": timezone_name}

    def _no_db(*_args, **_kwargs):
        raise AssertionError("teste unitario nao pode tocar o banco")

    service.fetch_one = _fetch_one
    service.fetch_all = _no_db
    service.consultas = consultas  # type: ignore[attr-defined]
    return service


def test_hours_since_trained_usa_hora_local_e_nao_utc():
    service = _service([_row(1, 500, datetime(2026, 10, 2, 8, 0, 0))])

    (item,) = service.get_muscle_group_overview(days=14, limit=10, user_id=1)

    assert item["hours_since_trained"] == pytest.approx(1.0)  # e nao 4.0
    assert item["days_since_trained"] == 0


def test_dose_72h_corta_na_hora_local():
    """Sessao a 73h na hora local (= 70h no relogio UTC) fica FORA da densidade
    de 72h; a das 08:00 de hoje entra."""
    antiga = AGORA_LOCAL - timedelta(hours=73)
    rows = [_row(1, 500, datetime(2026, 10, 2, 8, 0, 0)), _row(2, 400, antiga)]
    service = _service(rows)

    (item,) = service.get_muscle_group_overview(days=14, limit=10, user_id=1)

    assert item["dose_last_72h"] == pytest.approx(item["session_dose"])


def test_now_local_do_chamador_dispensa_consulta_de_fuso():
    service = _service([_row(1, 500, datetime(2026, 10, 2, 8, 0, 0))])

    (item,) = service.get_muscle_group_overview(
        days=14, limit=10, user_id=1, now_local=datetime(2026, 10, 2, 10, 30, 0)
    )

    assert item["hours_since_trained"] == pytest.approx(2.5)
    assert service.consultas == []


def test_fuso_lido_uma_vez_por_instancia_e_fuso_invalido_cai_no_default():
    service = _service([_row(1, 500, datetime(2026, 10, 2, 8, 0, 0))], timezone_name="Lua/Base")

    (item,) = service.get_muscle_group_overview(days=14, limit=10, user_id=1)
    service.get_muscle_group_overview(days=30, limit=10, user_id=1)

    assert item["hours_since_trained"] == pytest.approx(1.0)  # America/Sao_Paulo
    assert len(service.consultas) == 1


def test_fuso_do_usuario_e_respeitado():
    # Lisboa em outubro = UTC+1 -> 13:00 locais; treino as 08:00 locais = 5h.
    service = _service([_row(1, 500, datetime(2026, 10, 2, 8, 0, 0))], timezone_name="Europe/Lisbon")

    (item,) = service.get_muscle_group_overview(days=14, limit=10, user_id=1)

    assert item["hours_since_trained"] == pytest.approx(5.0)


def test_sem_user_id_usa_fuso_default_sem_consultar():
    service = _service([_row(1, 500, datetime(2026, 10, 2, 8, 0, 0))])

    assert service._user_now(None) == AGORA_LOCAL
    assert service.consultas == []

