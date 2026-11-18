"""Aprimoramento de técnica na sessão ao vivo e nas leituras de carga (#196,
ADR 0017, mapa §105): marcação só na criação do exercício, herdada pela
continuação por troca de equipamento; série de execução marcada sai neutra
(sem status nem carga da próxima série); a execução marcada não é "sessão
anterior", e1RM nem carga de partida; o detalhe devolve booleano.

Rodar: cd api && python -m pytest tests/unit/test_form_practice_sessao.py
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import services
from app import workout_sessions as ws
from app.domain import effective_intensity as ei
from app.domain.form_practice import sql_counts_for_progression
from app.domain.prescription_service import last_execution_from_logs
from tests.support.fake_db import fake_transaction
from tests.unit import test_client_key_sessao_idempotente as base
from tests.unit.test_last_session_sets import _Cursor as _LastCursor

SESSION = base.SESSION


class _FakeDB(base.FakeSessionDB):
    """O banco da idempotência, com a coluna da 120: a herança por item da
    ficha e a linha do exercício da sessão trazem `form_practice`."""

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        p = tuple(params or ())
        if flat.startswith("SELECT MAX(e.form_practice) AS marked FROM exercises e"):
            self.sql.append(sql)
            rows = [e for e in self.exercises if e["activity_id"] == p[0] and e.get("workout_plan_exercise_id") == p[1]]
            self._result = {"marked": max((e["form_practice"] for e in rows), default=None)}
            return
        super().execute(sql, params)
        if "FROM exercises e LEFT JOIN workout_plan_exercises" in flat and self._result:
            ex = next(e for e in self.exercises if e["id"] == p[0])
            self._result["form_practice"] = ex.get("form_practice", 0)


def _db(monkeypatch):
    return base._install(monkeypatch, _FakeDB())


def _add(**kw):
    return base._add_exercise(**kw)


def test_post_sem_campo_grava_zero_e_devolve_false(monkeypatch):
    db = _db(monkeypatch)
    out = _add(client_key="ex-1")
    assert out["form_practice"] is False and db.exercises[-1]["form_practice"] == 0


def test_post_com_campo_grava_um_e_devolve_true(monkeypatch):
    db = _db(monkeypatch)
    out = _add(client_key="ex-1", form_practice=True)
    assert out["form_practice"] is True and db.exercises[-1]["form_practice"] == 1


def test_repeticao_por_client_key_nao_altera_a_marcacao(monkeypatch):
    db = _db(monkeypatch)
    _add(client_key="ex-1", form_practice=True)
    repetido = _add(client_key="ex-1", form_practice=False)
    assert repetido["duplicate"] is True and repetido["form_practice"] is True
    assert len(db.inserts) == 1 and db.exercises[0]["form_practice"] == 1


def test_continuacao_por_troca_de_equipamento_herda_a_marcacao(monkeypatch):
    db = _db(monkeypatch)
    _add(client_key="ex-1", workout_plan_exercise_id=77, form_practice=True)
    # Continuação: outra linha do MESMO item da ficha, corpo sem o campo.
    cont = _add(client_key="ex-2", workout_plan_exercise_id=77)
    assert cont["form_practice"] is True and db.exercises[-1]["form_practice"] == 1
    # Outro item da ficha não herda.
    outro = _add(client_key="ex-3", workout_plan_exercise_id=78)
    assert outro["form_practice"] is False


def _set(exercise_id, **kw):
    return base._add_set(exercise_id, client_key=kw.pop("client_key", None), **kw)


def test_serie_de_execucao_marcada_sai_neutra_e_a_normal_e_avaliada(monkeypatch):
    db = _db(monkeypatch)
    marcada = _add(client_key="ex-1", form_practice=True)["exercise_id"]
    normal = _add(client_key="ex-2")["exercise_id"]
    out_m = _set(marcada, client_key="s-1")
    out_n = _set(normal, client_key="s-2")
    assert out_m["intensity_status"] is None and out_m["reps_position"] is None and out_m["next_set_load"] is None
    assert out_n["intensity_status"] is not None and out_n["next_set_load"] is not None
    # Repetição da série por `client_key` (3º chamador) também sai neutra.
    rep = _set(marcada, client_key="s-1")
    assert rep["duplicate"] is True and rep["intensity_status"] is None and rep["next_set_load"] is None
    assert len(db.sets) == 2


def test_evaluate_set_neutro_mantem_o_evento_de_seguranca():
    # Mesmo caminho do PATCH da série: só a linha do exercício decide.
    intensity = ei.EffectiveIntensity(rpe_target_effective=8, ceiling=9, failure_allowed=False)
    reading, next_set = ws._evaluate_set(
        None, None, 5, {"form_practice": 1}, intensity, user_id=1, session_id=SESSION, set_type="working",
        set_number=1, reps=10, weight_kg=Decimal(40), rpe=Decimal(10), joint_pain=None, muscle_pain=None,
    )
    assert next_set is None and reading.status is None and reading.reps_position is None
    esperado = ei.set_intensity_reading("working", Decimal(10), intensity).safety_event
    assert reading.safety_event == esperado


def test_serie_marcada_em_deload_tem_o_mesmo_evento_da_serie_normal():
    # O alvo do deload entra antes do ramo neutro: evento e alvo iguais aos da
    # série normal equivalente (reps pendentes = mesma leitura, sem DB).
    from app.domain.deload import DeloadSource

    intensity = ei.EffectiveIntensity(rpe_target_effective=8, ceiling=9, failure_allowed=False)
    kw = dict(
        user_id=1, session_id=SESSION, set_type="working", set_number=1, reps=10, weight_kg=Decimal(40),
        rpe=Decimal(10), joint_pain=None, muscle_pain=None, protocol_target=9,
    )
    linha = {"deload_source": DeloadSource.MANUAL.value}
    marcada, prox = ws._evaluate_set(None, None, 5, {**linha, "form_practice": 1}, intensity, **kw)
    normal, _ = ws._evaluate_set(None, None, 5, linha, intensity, reps_pending=True, **kw)
    assert prox is None and marcada.status is None
    assert marcada.safety_event == normal.safety_event and marcada.safety_event is not None
    assert marcada.target == normal.target != 9


def test_sessao_anterior_e_e1rm_usam_o_predicado_do_dono(monkeypatch):
    cur = _LastCursor([])
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    services.get_last_session_sets(11, exclude_activity_id=99)
    anterior = next(sql for sql in cur.sql if "AS global_equipment_id" in sql)
    assert sql_counts_for_progression("e") in anterior


def _log(form_practice, weight, day):
    return {
        "mapping_id": 11, "equipment_catalog_id": 2, "local_date": date(2026, 11, day), "activity_id": day,
        "form_practice": form_practice,
        "sets": [{"set_number": 1, "set_type": "working", "reps": 10, "weight_kg": weight, "rpe": 8}],
    }


def test_carga_de_partida_pula_a_execucao_marcada():
    logs = [_log(1, 20, 18), _log(0, 40, 15)]  # do mais recente ao mais antigo
    last = last_execution_from_logs(logs, 11)
    assert last is not None and last.last_load_kg == 40
    assert last_execution_from_logs([_log(True, 20, 18)], 11) is None


def test_detalhe_da_atividade_devolve_booleano(monkeypatch):
    class _Cur(_LastCursor):
        def execute(self, sql, params=None):
            flat = " ".join(sql.split())
            super().execute(sql, params)
            if flat.startswith("SELECT a.*"):
                self._one = {"id": 9, "user_id": 1, "calories_kcal": None, "calories_source": None}
            elif flat.startswith("SELECT e.*,"):
                self._all = [{"id": 1, "form_practice": 1}, {"id": 2, "form_practice": 0}, {"id": 3}]

    monkeypatch.setattr(services, "transaction", fake_transaction(_Cur([])))
    detalhe = services.activity_detail(9, user_id=1)
    assert [ex["form_practice"] for ex in detalhe["exercises"]] == [True, False, False]


def test_edicao_no_historico_pode_corrigir_a_marcacao():
    # #205: o Registro aceita o campo (None preserva) e o UPDATE grava a coluna.
    import inspect

    fonte = inspect.getsource(services.update_exercise_log)
    update = fonte[fonte.index("UPDATE exercises") : fonte.index("WHERE", fonte.index("UPDATE exercises"))]
    assert "FORM_PRACTICE_COLUMN" in update
    campo = ws.SessionExerciseIn.model_fields["form_practice"]
    assert campo.default is False
    from app.models import ExerciseLogUpdateIn

    assert ExerciseLogUpdateIn.model_fields["form_practice"].default is None
