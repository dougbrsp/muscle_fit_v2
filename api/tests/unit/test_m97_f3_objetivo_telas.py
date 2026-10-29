"""M97 F3: leituras de objetivo para as telas e deload numa sessão já iniciada.

- `GET /training-goals`: rótulos e defaults de cada objetivo montados pelos
  donos (`training_goal`, `prescription_defaults.defaults_for`, `training_safety`);
- `frontend/src/lib/training-goal.parity.json`: a precedência do dono
  (`resolve_goal`) e o payload real da rota, para o espelho do editor
  (`lib/training-goals.ts`) rodar no vitest contra os mesmos casos;
- `PATCH /workout-sessions/{id}/meta` com `deload` e
  `GET /workout-sessions/{id}/deload`: liga/desliga SÓ sem série gravada
  (dono `domain/deload.toggled_deload_source`);
- histórico de versões do Modelo com o objetivo da versão.

Regenerar a fixture (mudou a regra no dono):

    cd api && python tests/unit/test_m97_f3_objetivo_telas.py --gravar
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest

RAIZ = Path(__file__).resolve().parents[3]
if __name__ == "__main__":  # execução direta (--gravar): `app` importável sem pytest
    sys.path.insert(0, str(RAIZ / "api"))

from fastapi import HTTPException  # noqa: E402

from app import deload_periods, training_goals, workout_plan_templates, workout_sessions  # noqa: E402
from app.domain import deload as dl  # noqa: E402
from app.domain import prescription_defaults as pd  # noqa: E402
from app.domain import training_goal as tg  # noqa: E402
from app.domain import training_safety as ts  # noqa: E402
from app.security import Identity  # noqa: E402

FIXTURE = RAIZ / "frontend" / "src" / "lib" / "training-goal.parity.json"

H = tg.TrainingGoal.HYPERTROPHY.value
S = tg.TrainingGoal.STRENGTH.value
IDENTITY = Identity(user_id=1, role="regular", source="session")


# ---------------------------------------------------------------------------
# GET /training-goals
# ---------------------------------------------------------------------------


def test_training_goals_vem_dos_donos():
    out = training_goals.list_training_goals()
    assert out["default"] == tg.DEFAULT_GOAL.value
    assert [item["value"] for item in out["items"]] == list(tg.GOAL_VALUES)
    by_value = {item["value"]: item for item in out["items"]}
    for goal in tg.TrainingGoal:
        item = by_value[goal.value]
        assert item["label"] == tg.GOAL_LABELS[goal]
        assert item["is_default"] is (goal is tg.DEFAULT_GOAL)
        assert item["defaults"] == pd.defaults_for(goal).to_dict()
        assert item["rest_follows_generation_interval"] is (goal is tg.TrainingGoal.HYPERTROPHY)
        assert item["target_rpe"] == ts.goal_target_rpe(goal)
        assert item["rpe_ceiling"] == ts.goal_rpe_ceiling(goal)
        assert item["disallowed_techniques"] == sorted(ts.goal_disallowed_techniques(goal))


def test_campos_reservados_da_f2_chegam_a_tela():
    forca = next(item for item in training_goals.list_training_goals()["items"] if item["value"] == S)
    defaults = forca["defaults"]
    assert defaults["sets_count"] == pd.STRENGTH_SETS_COUNT
    assert defaults["rpe_target"] == pd.STRENGTH_RPE_TARGET
    assert defaults["rest_seconds_sanity_max"] == pd.STRENGTH_REST_SECONDS_SANITY_MAX
    assert defaults["rest_seconds"] == pd.STRENGTH_DEFAULTS.rest_seconds
    assert forca["disallowed_techniques"] == sorted(ts.STRENGTH_DISALLOWED_TECHNIQUES)


def test_hipertrofia_e_o_perfil_de_sempre():
    hip = next(item for item in training_goals.list_training_goals()["items"] if item["value"] == H)
    assert hip["defaults"]["reps_target"] == pd.DEFAULT_REPS_TARGET
    assert hip["defaults"]["rest_seconds"] == pd.default_rest_seconds()
    assert hip["rpe_ceiling"] is None and hip["disallowed_techniques"] == []


def test_rotulo_tolerante():
    assert tg.goal_label(None) == tg.GOAL_LABELS[tg.DEFAULT_GOAL]
    assert tg.goal_label("desconhecido") == tg.GOAL_LABELS[tg.DEFAULT_GOAL]
    assert tg.goal_label(S) == tg.GOAL_LABELS[tg.TrainingGoal.STRENGTH]


# ---------------------------------------------------------------------------
# Fixture de paridade do espelho do editor
# ---------------------------------------------------------------------------


def fixture() -> dict[str, Any]:
    overrides = [None, "", *tg.GOAL_VALUES, "desconhecido"]
    templates = [None, *tg.GOAL_VALUES, "desconhecido"]
    return {
        "_doc": (
            "GERADO por api/tests/unit/test_m97_f3_objetivo_telas.py (--gravar): precedência do dono "
            "api/app/domain/training_goal.py (resolve_goal), o payload de GET /training-goals e os números "
            "do deload (api/app/domain/deload.py). "
            "Não edite à mão."
        ),
        "payload": training_goals.training_goals_payload(),
        # Números do deload para os textos do Modo Treino (`lib/deload.ts`):
        # as constantes de `/deload-periods/current` e uma prescrição do dono.
        "deload_constants": deload_periods._constants(),
        "deload_prescription": dl.deload_prescription(
            working_load_kg=80, sets_count=3, reps_target="8-12", training_goal=S, step_kg=2.5
        ).to_dict(),
        "precedence": [
            {"override": override, "template_goal": template, "resolved": tg.resolved_goal_value(override, template)}
            for override in overrides
            for template in templates
        ],
    }


def _gravar(dados: Any) -> None:
    with FIXTURE.open("w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(json.dumps(dados, ensure_ascii=False, indent=2) + "\n")


def test_fixture_do_fe_e_a_do_dono():
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == fixture(), (
        f"{FIXTURE.name} divergiu do dono: rode `python tests/unit/test_m97_f3_objetivo_telas.py --gravar`"
    )


# ---------------------------------------------------------------------------
# Dono: ligar/desligar deload numa sessão já iniciada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "atual,pedido,periodo,esperado",
    [
        (None, True, False, dl.DeloadSource.MANUAL.value),
        (None, True, True, dl.DeloadSource.PERIOD.value),
        (dl.DeloadSource.MANUAL.value, False, False, None),
        (dl.DeloadSource.PERIOD.value, False, True, None),
        (dl.DeloadSource.MANUAL.value, True, True, dl.DeloadSource.MANUAL.value),
        (None, False, True, None),
    ],
)
def test_toggle_sem_serie(atual, pedido, periodo, esperado):
    assert dl.toggled_deload_source(atual, requested=pedido, saved_sets=0, period_active=periodo) == esperado


def test_toggle_com_serie_gravada_trava_so_quando_muda():
    with pytest.raises(dl.DeloadToggleLocked):
        dl.toggled_deload_source(None, requested=True, saved_sets=1, period_active=False)
    with pytest.raises(dl.DeloadToggleLocked):
        dl.toggled_deload_source(dl.DeloadSource.MANUAL.value, requested=False, saved_sets=3, period_active=False)
    # pedido igual ao estado atual: idempotente mesmo com série
    assert dl.toggled_deload_source(
        dl.DeloadSource.MANUAL.value, requested=True, saved_sets=3, period_active=False
    ) == dl.DeloadSource.MANUAL.value
    assert dl.toggled_deload_source(None, requested=False, saved_sets=3, period_active=False) is None
    assert dl.can_toggle_deload(0) and dl.can_toggle_deload(None) and not dl.can_toggle_deload(1)


# ---------------------------------------------------------------------------
# Rotas: PATCH meta com deload e GET do deload da sessão
# ---------------------------------------------------------------------------


class _MetaCursor:
    def __init__(self, *, source=None, saved_sets=0, period=None, local_date=_dt.date(2026, 10, 25)):
        self.source = source
        self.saved_sets = saved_sets
        self.period = period
        self.local_date = local_date
        self.executed: list[tuple[str, tuple]] = []
        self._pending: Any = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        if flat.startswith("SELECT id, gym_id FROM activities"):
            self._pending = {"id": params[0], "gym_id": None}
        elif flat.startswith("SELECT a.deload_source"):
            self._pending = {"deload_source": self.source, "local_date": self.local_date}
        elif "FROM exercise_sets" in flat:
            self._pending = {"n": self.saved_sets}
        elif "FROM deload_periods" in flat:
            self._pending = self.period
        else:
            self._pending = None

    def fetchone(self):
        return self._pending

    def updates(self):
        return [(sql, params) for sql, params in self.executed if sql.startswith("UPDATE activities")]


def _patch(monkeypatch, cursor, **payload):
    from tests.support.fake_db import fake_transaction

    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    return workout_sessions.update_session_meta(77, workout_sessions.SessionMetaIn(**payload), identity=IDENTITY)


PERIODO = {"id": 4, "starts_on": _dt.date(2026, 10, 20), "ends_on": _dt.date(2026, 10, 26), "cancelled_at": None}


def test_meta_sem_deload_nao_le_nem_grava_deload(monkeypatch):
    cursor = _MetaCursor()
    out = _patch(monkeypatch, cursor, name="Treino A")
    assert "deload" not in out
    assert not any("deload" in sql for sql, _ in cursor.executed)


def test_meta_liga_deload_avulso_sem_serie(monkeypatch):
    cursor = _MetaCursor()
    out = _patch(monkeypatch, cursor, deload=True)
    (sql, params), = cursor.updates()
    assert "deload_source = %s" in sql and params == (dl.DeloadSource.MANUAL.value, 77)
    assert out["deload"]["active"] is True and out["deload"]["source"] == dl.DeloadSource.MANUAL.value
    assert out["deload"]["can_toggle"] is True


def test_meta_liga_deload_em_periodo_carimba_period(monkeypatch):
    cursor = _MetaCursor(period=PERIODO)
    out = _patch(monkeypatch, cursor, deload=True)
    (_sql, params), = cursor.updates()
    assert params[0] == dl.DeloadSource.PERIOD.value
    assert out["deload"]["period"]["id"] == 4


def test_meta_desliga_deload_do_periodo(monkeypatch):
    cursor = _MetaCursor(source=dl.DeloadSource.PERIOD.value, period=PERIODO)
    out = _patch(monkeypatch, cursor, deload=False)
    (sql, params), = cursor.updates()
    assert params == (None, 77)
    assert out["deload"] == {"active": False, "source": None, "period": None, "can_toggle": True}


def test_meta_com_serie_gravada_responde_409_e_nao_grava(monkeypatch):
    cursor = _MetaCursor(saved_sets=2)
    with pytest.raises(HTTPException) as erro:
        _patch(monkeypatch, cursor, deload=True)
    assert erro.value.status_code == 409
    assert erro.value.detail["error"] == "deload_locked"
    assert cursor.updates() == []


def test_meta_pedido_igual_ao_atual_nao_grava(monkeypatch):
    cursor = _MetaCursor(source=dl.DeloadSource.MANUAL.value, saved_sets=5)
    out = _patch(monkeypatch, cursor, deload=True)
    assert cursor.updates() == []
    assert out["deload"]["active"] is True and out["deload"]["can_toggle"] is False


def test_get_deload_da_sessao(monkeypatch):
    from tests.support.fake_db import fake_transaction

    cursor = _MetaCursor(source=dl.DeloadSource.MANUAL.value, saved_sets=0)
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    out = workout_sessions.get_session_deload(77, identity=IDENTITY)
    assert out["deload"]["active"] is True and out["deload"]["can_toggle"] is True
    # ownership: a leitura da sessão filtra pelo usuário
    sql, params = next((s, p) for s, p in cursor.executed if s.startswith("SELECT a.deload_source"))
    assert "a.user_id = %s" in sql and params == (77, 1)


def test_get_deload_de_sessao_alheia_e_404(monkeypatch):
    from tests.support.fake_db import fake_transaction

    class _Vazio(_MetaCursor):
        def execute(self, sql, params=()):
            super().execute(sql, params)
            if sql.lstrip().startswith("SELECT a.deload_source"):
                self._pending = None

    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(_Vazio()))
    with pytest.raises(HTTPException) as erro:
        workout_sessions.get_session_deload(77, identity=IDENTITY)
    assert erro.value.status_code == 404


# ---------------------------------------------------------------------------
# Histórico de versões com o objetivo
# ---------------------------------------------------------------------------


def test_historico_de_versoes_traz_o_objetivo(monkeypatch):
    from tests.support.fake_db import fake_transaction

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            self.sql = sql

        def fetchone(self):
            return {"id": 3, "name": "M", "description": None, "is_active": 1}

        def fetchall(self):
            return [
                {"id": 2, "version_number": 2, "training_goal": S, "superseded_at": None},
                {"id": 1, "version_number": 1, "training_goal": None, "superseded_at": _dt.datetime(2026, 10, 1)},
            ]

    monkeypatch.setattr(workout_plan_templates, "transaction", fake_transaction(_Cur()))
    out = workout_plan_templates.list_template_versions(3)
    assert [item["training_goal"] for item in out["items"]] == [S, tg.DEFAULT_GOAL.value]
    assert "v.training_goal" in workout_plan_templates.VERSIONS_LIST_SQL


if __name__ == "__main__":
    if "--gravar" not in sys.argv:
        raise SystemExit("uso: python tests/unit/test_m97_f3_objetivo_telas.py --gravar")
    _gravar(fixture())
    print(f"gravado: {FIXTURE}")
