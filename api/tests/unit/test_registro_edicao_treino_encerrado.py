"""#216/#215: edicao de treino encerrado pelo Registro (pareamento por serie de
origem, pendencia, dor limpavel, alvo de deload, exercicio novo, exclusao).

Rodar: cd api && python -m pytest tests/unit/test_registro_edicao_treino_encerrado.py
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app import exercise_set_update_merge as merge
from app import progression_state, services
from app.domain import effective_intensity
from app.main import _exercise_log_data
from app.models import ExerciseLogCreateIn, ExerciseLogUpdateIn
from tests.support.fake_db import LoopingExecuteMany, fake_transaction


def _stored(number, **extra):
    return {"set_number": number, "reps_pending": 0, "notes": f"n{number}", **extra}


def test_remover_serie_do_meio_mantem_pendencia_da_serie_de_origem():
    stored = [_stored(1), _stored(2), _stored(3, reps_pending=1, rpe_target_effective=9, rpe_target_source="protocol")]
    incoming = [
        {"set_number": 1, "source_set_number": 1, "reps": 10},
        {"set_number": 2, "source_set_number": 3, "reps": None},  # antiga 3, renumerada
    ]
    out = merge.merge_sets_for_update(incoming, stored)
    assert out[1]["notes"] == "n3" and out[1]["reps_pending"] == 1 and out[1]["rpe_target_effective"] == 9
    assert out[0]["notes"] == "n1" and "source_set_number" not in out[1]
    # sem o campo (cliente antigo): pareia por set_number, como antes
    legado = merge.merge_sets_for_update([{"set_number": 2, "reps": 8}], stored)
    assert legado[0]["notes"] == "n2"


def test_serie_nova_com_source_null_nao_herda_nada():
    out = merge.merge_sets_for_update(
        [{"set_number": 1, "source_set_number": None, "reps": 8}, {"set_number": 2, "source_set_number": 2, "reps": 8}],
        [_stored(1), _stored(2)],
    )
    assert out[0]["notes"] is None and out[1]["notes"] == "n2"


def test_pendente_com_reps_zero_do_cliente_antigo_continua_pendente():
    out = merge.merge_sets_for_update([{"set_number": 1, "reps": 0}], [_stored(1, reps_pending=1)])
    assert out[0]["reps_pending"] == 1 and out[0]["reps"] is None
    confirmada = merge.merge_sets_for_update([{"set_number": 1, "reps": 0}], [_stored(1)])
    assert confirmada[0]["reps_pending"] == 0 and confirmada[0]["reps"] == 0


class _Cur(LoopingExecuteMany):
    def __init__(self, status="done", deload=None, owned=True, marked=0, plan_owned=True, ended_at=None, started_at=None):
        self.plan_owned, self.ended_at, self.started_at = plan_owned, ended_at, started_at
        self.status, self.deload, self.owned, self.marked = status, deload, owned, marked
        self.log: list[tuple[str, tuple]] = []
        self.lastrowid = 77
        self._result: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.log.append((flat, tuple(params or ())))
        row = {"id": 10, "activity_id": 20, "original_name": "Supino", "mapping_id": 5,
               "workout_plan_exercise_id": None, "equipment_id": None, "muscle_pain": 5,
               "joint_pain": True, "form_practice": self.marked, "session_status": self.status,
               "deload_source": self.deload, "local_date": date(2026, 11, 10), "started_at": self.started_at, "ended_at": self.ended_at}
        if flat.startswith("SELECT e.id, e.activity_id") or flat.startswith("SELECT id, activity_id"):
            self._result = [row]
        elif flat.startswith("SELECT session_status FROM activities"):
            self._result = [row] if self.owned else []
        elif flat.startswith("SELECT wpe.id"):
            self._result = [{"id": 1}] if self.plan_owned else []
        elif flat.startswith("SELECT COALESCE(MAX"):
            self._result = [{"seq": 4}]
        elif flat.startswith("SELECT set_number,"):
            self._result = list(getattr(self, "stored_sets", []))
        else:
            self._result = [{"avg_rpe": None}]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result

    def starting(self, prefix):
        return [p for s, p in self.log if s.startswith(prefix)]


def _setup(monkeypatch, cur, target=8):
    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    intensity = replace(effective_intensity.UNKNOWN_INTENSITY, rpe_target_effective=target)
    monkeypatch.setattr(effective_intensity, "resolve_for_logged_exercise", lambda conn, eid: intensity)
    calls = []

    def fake_reeval(conn, uid, aid, **kw):
        calls.append((uid, aid, len(cur.log), kw.get("anchor_at")))
        return {"ok": 1}

    monkeypatch.setattr(progression_state, "reevaluate_after_correction", fake_reeval)
    return intensity, calls


def _body(**extra):
    return {"local_date": "2026-11-10", "original_name": "Supino",
            "sets": [{"set_number": 1, "reps": 8, "weight_kg": 50}], **extra}


def _put(cur, **extra):
    data = _exercise_log_data(ExerciseLogUpdateIn.model_validate(_body(**extra)))
    return services.update_exercise_log(10, data, user_id=7)


def test_dor_do_exercicio_null_enviado_limpa_e_ausente_preserva(monkeypatch):
    cur = _Cur()
    _setup(monkeypatch, cur)
    _put(cur, muscle_pain=None, joint_pain=None)
    assert cur.starting("UPDATE exercises SET")[0][-5:-3] == (None, None)
    cur2 = _Cur()
    _setup(monkeypatch, cur2)
    _put(cur2)
    assert cur2.starting("UPDATE exercises SET")[0][-5:-3] == (5, True)


def test_put_em_sessao_de_deload_grava_o_alvo_de_deload(monkeypatch):
    cur = _Cur(deload="manual")
    intensity, _ = _setup(monkeypatch, cur, target=8)
    _put(cur)
    gravado = cur.starting("INSERT INTO exercise_sets")[0][12]
    esperado = effective_intensity.stored_rpe_target("working", intensity, deload_source="manual")
    assert gravado == esperado and gravado != 8


def test_put_fora_do_deload_mantem_o_alvo_da_prescricao(monkeypatch):
    cur = _Cur()
    _setup(monkeypatch, cur, target=8)
    _put(cur)
    assert cur.starting("INSERT INTO exercise_sets")[0][12] == 8


def test_exercicio_novo_em_sessao_encerrada_grava_com_series_e_reavalia_uma_vez(monkeypatch):
    cur = _Cur()
    _, calls = _setup(monkeypatch, cur)
    body = ExerciseLogCreateIn.model_validate({**_body(), "activity_id": 20})
    out = services.create_exercise_log(20, _exercise_log_data(body), user_id=7)
    ins = cur.starting("INSERT INTO exercises")[0]
    assert ins[0] == 20 and ins[1] == 4 and ins[2] == "Supino"
    assert len(cur.starting("INSERT INTO exercise_sets")) == 1
    assert [c[:2] for c in calls] == [(7, 20)] and out["progression_reevaluated"] is True


def test_exercicio_novo_recusa_sessao_em_andamento(monkeypatch):
    cur = _Cur(status="in_progress")
    _, calls = _setup(monkeypatch, cur)
    with pytest.raises(services.SessionNotClosedError):
        services.create_exercise_log(20, _body(), user_id=7)
    assert cur.starting("INSERT INTO exercises") == [] and calls == []


def test_exercicio_novo_em_sessao_de_outro_usuario_devolve_none(monkeypatch):
    cur = _Cur(owned=False)
    _, calls = _setup(monkeypatch, cur)
    assert services.create_exercise_log(20, _body(), user_id=7) is None
    assert cur.starting("INSERT INTO exercises") == [] and calls == []
    assert cur.log[0][1] == (20, 7)


def test_exclusao_reavalia_depois_do_delete_ancorada_na_data_da_sessao(monkeypatch):
    cur = _Cur()
    _, calls = _setup(monkeypatch, cur)
    services.delete_exercise_log(10, user_id=7)
    ultimo_delete = max(i for i, (s, _) in enumerate(cur.log) if s.startswith("DELETE FROM exercises"))
    _, _, vistos, ancora = calls[0]
    assert vistos > ultimo_delete + 1  # reavalia DEPOIS do DELETE (e do recalculo)
    assert ancora.date() == date(2026, 11, 10)


def test_exclusao_sem_user_id_nao_filtra_por_dono_nem_reavalia(monkeypatch):
    cur = _Cur()
    _, calls = _setup(monkeypatch, cur)
    assert services.delete_exercise_log(10) is not None
    assert "a.user_id" not in cur.log[0][0] and calls == []


def test_exclusao_ancora_inclui_ended_at(monkeypatch):
    cur = _Cur(ended_at=datetime(2026, 11, 9, 23, 0))
    _, calls = _setup(monkeypatch, cur)
    services.delete_exercise_log(10, user_id=7)
    assert "a.ended_at" in cur.log[0][0] and calls[0][3] == datetime(2026, 11, 9, 23, 0)


def test_put_com_data_alterada_ancora_na_posicao_mais_antiga(monkeypatch):
    cur = _Cur()
    _, calls = _setup(monkeypatch, cur)
    _put(cur, local_date="2026-11-12")
    assert calls[0][3].date() == date(2026, 11, 10)
    cur2 = _Cur()
    _, calls2 = _setup(monkeypatch, cur2)
    _put(cur2)
    assert calls2[0][3] is None


def test_put_alvo_de_protocolo_mantido_com_alvo_gravado_nulo_grava_null(monkeypatch):
    cur = _Cur()
    _setup(monkeypatch, cur, target=8)
    monkeypatch.setattr(
        merge, "merge_sets_for_update",
        lambda inc, stored, **kw: [{"set_number": 1, "set_type": "working", "reps": 8, "weight_kg": 50,
                              "rpe_target_source": "protocol", "rpe_target_effective": None}],
    )
    monkeypatch.setattr(services, "merge_sets_for_update", merge.merge_sets_for_update)
    _put(cur)
    assert cur.starting("INSERT INTO exercise_sets")[0][12] is None


def test_serie_com_origem_duplicada_so_a_primeira_herda():
    stored = [_stored(1, completed_at="t")]
    out = merge.merge_sets_for_update(
        [{"set_number": 1, "source_set_number": 1, "reps": 8}, {"set_number": 2, "source_set_number": 1, "reps": 8}],
        stored,
    )
    assert out[0]["notes"] == "n1" and out[1]["notes"] is None and out[1]["completed_at"] is None


def test_caso_misto_e_origem_inexistente_sao_series_novas():
    out = merge.merge_sets_for_update(
        [{"set_number": 1, "source_set_number": 1, "reps": 8}, {"set_number": 2, "reps": 8},
         {"set_number": 3, "source_set_number": 99, "reps": 8}],
        [_stored(1), _stored(2)],
    )
    assert out[0]["notes"] == "n1" and out[1]["notes"] is None and out[2]["notes"] is None


def test_sets_acima_do_teto_da_422():
    from pydantic import ValidationError
    from app.models import MAX_SETS_PER_LOGGED_EXERCISE

    sets = [{"set_number": i + 1, "reps": 8} for i in range(MAX_SETS_PER_LOGGED_EXERCISE + 1)]
    with pytest.raises(ValidationError):
        ExerciseLogUpdateIn.model_validate({**_body(), "sets": sets})


def test_exercicio_novo_com_item_de_ficha_alheio_404_sem_insert(monkeypatch):
    cur = _Cur(plan_owned=False)
    _, calls = _setup(monkeypatch, cur)
    with pytest.raises(services.WorkoutPlanExerciseNotFoundError):
        services.create_exercise_log(20, _body(workout_plan_exercise_id=999, workout_plan_exercise_id_provided=True), user_id=7)
    assert cur.starting("INSERT INTO exercises") == [] and calls == []


def test_criacao_exige_serie_mas_put_aceita_lista_vazia():
    with pytest.raises(ValidationError):
        ExerciseLogCreateIn.model_validate({**_body(sets=[]), "activity_id": 20})
    assert ExerciseLogUpdateIn.model_validate(_body(sets=[])).sets == []


def _put_max_reps(monkeypatch, pending):
    cur = _Cur()
    cur.stored_sets = [_stored(1, reps_pending=pending, measure="max_reps")]
    _setup(monkeypatch, cur)
    body = _body(sets=[{"set_number": 1, "reps": None, "weight_kg": 50, "measure": "max_reps"}])
    services.update_exercise_log(10, _exercise_log_data(ExerciseLogUpdateIn.model_validate(body)), user_id=7)
    return cur


def test_put_serie_pendente_de_max_reps_mantida_nula_salva_e_continua_pendente(monkeypatch):
    cur = _put_max_reps(monkeypatch, pending=1)
    assert cur.starting("INSERT INTO exercise_sets")


def test_put_serie_nao_pendente_de_max_reps_com_reps_nulas_da_422(monkeypatch):
    from app.set_measure_input import SetMeasureInputError

    with pytest.raises(SetMeasureInputError) as exc:
        _put_max_reps(monkeypatch, pending=0)
    assert "reps_required" in exc.value.codes


def test_codigos_de_medida_da_serie_mesclada_isentam_reps_so_se_pendente():
    codes = ["reps_required", "reps_below_min", "duration_required"]
    assert merge.measure_codes_for_merged_set({"reps_pending": 1}, codes) == ["duration_required"]
    assert merge.measure_codes_for_merged_set({"reps_pending": 0}, codes) == codes


def test_put_que_muda_so_started_at_ancora_no_instante_antigo_e_no_novo(monkeypatch):
    cur = _Cur(started_at=datetime(2026, 11, 10, 18, 0), ended_at=datetime(2026, 11, 10, 19, 0))
    _, calls = _setup(monkeypatch, cur)
    _put(cur, started_at="2026-11-10T07:30:00")
    assert calls[0][3] == datetime(2026, 11, 10, 0, 0)  # meia-noite da data (mais antiga); cobre antigo e novo
    cur2 = _Cur(started_at=datetime(2026, 11, 10, 18, 0))
    _, calls2 = _setup(monkeypatch, cur2)
    _put(cur2, started_at="2026-11-10T07:30:00")
    assert calls2[0][3] is not None


def test_put_que_reenvia_mesmo_started_at_e_mesma_data_nao_ancora(monkeypatch):
    cur = _Cur(started_at=datetime(2026, 11, 10, 18, 0))
    _, calls = _setup(monkeypatch, cur)
    _put(cur, started_at="2026-11-10T18:00:00")
    assert calls[0][3] is None
    cur2 = _Cur(started_at=datetime(2026, 11, 10, 18, 0))
    _, calls2 = _setup(monkeypatch, cur2)
    _put(cur2)  # started_at ausente: COALESCE preserva, nao e mudanca
    assert calls2[0][3] is None
