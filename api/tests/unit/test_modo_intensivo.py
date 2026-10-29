"""Modo Intensivo para técnicas avançadas - PR 1 (backend).

- RPE-alvo do protocolo (`training_safety.protocol_target_rpe`): teto 10 ->
  10; 9, desconhecido ou sujo -> 9; nunca pela recuperação; congelado só
  restringe;
- POST de série: reps pendentes (reps NULL, `reps_pending = 1`), sem RPE,
  alvo do protocolo em `rpe_target_effective` (nunca em `rpe`), carga
  planejada, origem do ajuste e hora da conclusão; `client_key` repetida
  devolve o gravado;
- PATCH da série: confirmação (limpa a pendência) e correção;
- mescla pura (`exercise_set_update_merge`) e agregados sem a pendente;
- `rpe_target_effective` não alimenta o e1RM.

Rodar: cd api && python -m pytest tests/unit/test_modo_intensivo.py
"""

from __future__ import annotations

import datetime as _dt
import inspect
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import analytics, services
from app import workout_sessions as ws
from app.domain import effective_intensity as ei
from app.domain import training_safety as ts
from app.exercise_set_update_merge import merge_set_patch, merge_sets_for_update, resolve_reps_pending
from tests.unit.test_client_key_sessao_idempotente import SESSION, FakeSessionDB, _identity, _install

# ---------------------------------------------------------------------------
# Motor: RPE-alvo do protocolo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("teto, alvo", [(10, 10), (9, 9), (8, 9), (6, 9), (None, 9), ("lixo", 9), (True, 9), (11, 9)])
def test_protocol_target_rpe_pelo_teto_do_par(teto, alvo):
    assert ts.protocol_target_rpe(teto) == alvo


def test_protocolo_e_constantes_nomeadas_do_dono():
    assert ts.PROTOCOL_TARGET_RPE_WITH_FAILURE == ts.FAILURE_RPE
    assert ts.PROTOCOL_TARGET_RPE_WITHOUT_FAILURE < ts.FAILURE_RPE
    assert ts.RPE_TARGET_SOURCES == (ts.RPE_TARGET_SOURCE_PROTOCOL,)


@pytest.mark.parametrize(
    "vivo, congelado, alvo",
    [(10, None, 10), (10, 10, 10), (10, 9, 9), (9, 10, 9), (None, 10, 9), (10, 7, 10), (10, "x", 10)],
)
def test_alvo_efetivo_do_protocolo_so_restringe(vivo, congelado, alvo):
    assert ts.effective_protocol_target_rpe(vivo, congelado) == alvo


def test_protocolo_nao_olha_a_recuperacao():
    """A assinatura do dono só recebe o teto do par: não há como a recuperação entrar."""
    assert list(inspect.signature(ts.protocol_target_rpe).parameters) == ["max_safe_rpe"]


def test_alvo_congelado_lido_da_prescricao():
    assert ei.frozen_protocol_target_rpe('{"advanced_technique_parameters": {"target_rpe": 9}}') == 9
    assert ei.frozen_protocol_target_rpe({"advanced_technique_parameters": None}) is None
    assert ei.frozen_protocol_target_rpe(None) is None


# ---------------------------------------------------------------------------
# Rotas: POST e PATCH de série
# ---------------------------------------------------------------------------


class FakeIntensiveDB(FakeSessionDB):
    """+ a leitura travante do PATCH e o UPDATE da série."""

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        p = tuple(params or ())
        if flat.startswith("SELECT id, set_number, set_type") and "set_number = %s FOR UPDATE" in flat:
            self.sql.append(sql)
            for index, row in enumerate(self.sets):
                if row["exercise_id"] == p[0] and row["set_number"] == p[1]:
                    self._result = {"id": index, **row}
                    return
            self._result = None
        elif flat.startswith("UPDATE exercise_sets SET reps"):
            self.sql.append(sql)
            reps, weight, rpe, pending, event, reps_left, reps_right, rpe_left, rpe_right, index = p
            self.sets[index].update(
                reps=reps, weight_kg=weight, rpe=rpe, reps_pending=pending, safety_event=event,
                reps_left=reps_left, reps_right=reps_right, rpe_left=rpe_left, rpe_right=rpe_right,
            )
            self.updates = getattr(self, "updates", 0) + 1
        else:
            super().execute(sql, params)


@pytest.fixture
def db(monkeypatch):
    fake = _install(monkeypatch, FakeIntensiveDB())
    monkeypatch.setattr(ws, "user_timezone", lambda conn, user_id: "America/Sao_Paulo")
    fake.pair_max = 10
    monkeypatch.setattr(ei, "resolve_pair_safety", lambda conn, m, g: SimpleNamespace(max_safe_rpe=fake.pair_max))
    return fake


def _post(exercise_id, **kw):
    return ws.add_session_set(SESSION, exercise_id, ws.SessionSetIn(**kw), identity=_identity())


def _patch(exercise_id, set_number, session_id=SESSION, **kw):
    return ws.update_session_set(session_id, exercise_id, set_number, ws.SessionSetPatchIn(**kw), identity=_identity())


def _intensiva(**kw):
    base = {
        "reps": None,
        "reps_pending": True,
        "weight_kg": Decimal("65"),
        "planned_weight_kg": Decimal("65"),
        "load_adjust_source": "fatigue",
        "rpe_target_source": "protocol",
        "technique_code": "fst7",
        "completed_at": _dt.datetime(2026, 10, 24, 12, 0, 30, 250000, tzinfo=_dt.timezone.utc),
    }
    return {**base, **kw}


def test_post_pendente_sem_rpe_grava_o_alvo_do_protocolo_fora_do_rpe(db):
    ex = db.add_exercise()
    out = _post(ex, **_intensiva())
    row = db.sets[0]
    assert row["reps"] is None and row["reps_pending"] == 1
    assert row["rpe"] is None  # o RPE prescrito nunca vai para o relatado
    assert row["rpe_target_effective"] == 10 and row["rpe_target_source"] == "protocol"
    assert row["planned_weight_kg"] == Decimal("65") and row["load_adjust_source"] == "fatigue"
    # aware -> hora de parede do usuário (UTC-3), naive, com milissegundos
    assert row["completed_at"] == _dt.datetime(2026, 10, 24, 9, 0, 30, 250000)
    assert out["reps_pending"] is True and out["rpe_target_effective"] == 10
    assert out["rpe_target_source"] == "protocol" and out["planned_weight_kg"] == 65.0
    assert out["completed_at"] == "2026-10-24T09:00:30.250000"
    # sem reps não há carga da próxima série a decidir
    assert out["next_set_load"] is None


@pytest.mark.parametrize("teto, alvo", [(10, 10), (9, 9), (None, 9)])
def test_post_alvo_do_protocolo_segue_o_teto_do_par(db, monkeypatch, teto, alvo):
    if teto is None:
        monkeypatch.setattr(ei, "resolve_pair_safety", lambda conn, m, g: None)
    else:
        db.pair_max = teto
    ex = db.add_exercise()
    assert _post(ex, **_intensiva())["rpe_target_effective"] == alvo


def test_post_sem_protocolo_mantem_o_alvo_da_prescricao(db):
    ex = db.add_exercise()
    out = _post(ex, reps=8, weight_kg=Decimal(40))
    row = db.sets[0]
    assert row["rpe_target_effective"] == 9 and row["rpe_target_source"] is None  # 9 = intensidade do fake
    assert row["reps_pending"] == 0 and row["completed_at"] is None and row["planned_weight_kg"] is None
    assert out["reps_pending"] is False


def test_post_protocolo_exige_tecnica_e_serie_que_nao_seja_preparatoria(db):
    ex = db.add_exercise()
    with pytest.raises(HTTPException) as err:
        _post(ex, **_intensiva(technique_code=None))
    assert err.value.status_code == 422
    with pytest.raises(HTTPException) as err:
        _post(ex, **_intensiva(set_type="warmup"))
    assert err.value.status_code == 422
    assert db.sets == []


@pytest.mark.parametrize(
    "kw",
    [
        {"reps": 7, "reps_pending": True},  # pendente com reps
        {"reps_pending": True, "measure": "duration", "duration_seconds": 30},
        {"reps_pending": True, "measure": "max_reps"},
        {"load_adjust_source": "outro"},
        {"rpe_target_source": "prescription"},
        {"planned_weight_kg": Decimal("-1")},
    ],
)
def test_validacao_dos_campos_novos(kw):
    with pytest.raises(ValidationError):
        ws.SessionSetIn(**kw)


def test_cliente_antigo_sem_campos_novos_continua_valido():
    payload = ws.SessionSetIn(reps=10, weight_kg=Decimal(40))
    assert payload.reps_pending is False and payload.rpe_target_source is None
    # série por tempo continua sem reps e sem pendência
    assert ws.SessionSetIn(measure="duration", duration_seconds=60).reps is None


def test_serie_sem_reps_e_sem_marca_continua_aceita_como_antes():
    """Compatibilidade: a tela /registro-exercicios lança série com reps vazias
    (`reps` NULL já era aceita); só `reps_pending` tira a série dos motores."""
    payload = ws.SessionSetIn(reps=None, weight_kg=Decimal(40))
    assert payload.reps is None and payload.reps_pending is False


def test_client_key_repetida_devolve_a_serie_pendente_gravada(db):
    ex = db.add_exercise()
    primeiro = _post(ex, client_key="s-1", **_intensiva())
    segundo = _post(ex, client_key="s-1", **_intensiva(planned_weight_kg=Decimal("60")))
    assert segundo["duplicate"] is True and len(db.sets) == 1
    for campo in ("reps_pending", "rpe_target_effective", "rpe_target_source", "planned_weight_kg", "completed_at"):
        assert segundo[campo] == primeiro[campo], campo


def test_patch_confirma_as_reps_e_limpa_a_pendencia(db):
    ex = db.add_exercise()
    _post(ex, **_intensiva())
    out = _patch(ex, 1, reps=7)
    row = db.sets[0]
    assert row["reps"] == 7 and row["reps_pending"] == 0
    assert row["weight_kg"] == Decimal("65") and row["rpe"] is None  # ausentes mantêm
    assert row["rpe_target_effective"] == 10 and row["rpe_target_source"] == "protocol"
    assert out["reps_pending"] is False and out["set_number"] == 1
    assert db.calls["aggregates"] == 2 and db.calls["avg_rpe"] == 2


def test_patch_corrige_carga_e_rpe_sem_confirmar_a_pendente(db):
    ex = db.add_exercise()
    _post(ex, **_intensiva())
    out = _patch(ex, 1, weight_kg=Decimal("60"), rpe=Decimal("9"))
    row = db.sets[0]
    assert row["weight_kg"] == Decimal("60") and row["rpe"] == Decimal("9")
    assert row["reps"] is None and row["reps_pending"] == 1 and out["reps_pending"] is True


def test_patch_corrige_serie_confirmada(db):
    ex = db.add_exercise()
    _post(ex, reps=10, weight_kg=Decimal(40), rpe=Decimal(8))
    _patch(ex, 1, reps=9)
    assert db.sets[0]["reps"] == 9 and db.sets[0]["reps_pending"] == 0 and db.sets[0]["rpe"] == Decimal(8)


def test_patch_reps_nulas_em_serie_de_reps_e_422(db):
    ex = db.add_exercise()
    _post(ex, reps=10, weight_kg=Decimal(40))
    with pytest.raises(HTTPException) as err:
        _patch(ex, 1, reps=None)
    assert err.value.status_code == 422 and getattr(db, "updates", 0) == 0


def test_patch_vazio_e_recusado():
    with pytest.raises(ValidationError):
        ws.SessionSetPatchIn()


def test_patch_serie_inexistente_404_e_treino_finalizado_409(db, monkeypatch):
    ex = db.add_exercise()
    with pytest.raises(HTTPException) as err:
        _patch(ex, 3, reps=8)
    assert err.value.status_code == 404
    fake = _install(monkeypatch, FakeIntensiveDB(status="done"))
    ex = fake.add_exercise()
    with pytest.raises(HTTPException) as err:
        _patch(ex, 1, reps=8)
    assert err.value.status_code == 409


# ---------------------------------------------------------------------------
# Mescla pura e agregados
# ---------------------------------------------------------------------------


def test_pendencia_so_continua_sem_reps():
    assert resolve_reps_pending({"reps_pending": 1}, None) == 1
    assert resolve_reps_pending({"reps_pending": 1}, 8) == 0
    assert resolve_reps_pending({"reps_pending": 0}, None) == 0
    assert resolve_reps_pending(None, None) == 0


def test_merge_set_patch_presente_vale_ausente_mantem():
    gravada = {"reps": None, "weight_kg": Decimal(65), "rpe": None, "reps_pending": 1, "set_type": "working"}
    assert merge_set_patch(gravada, {"reps": 8}) == {**gravada, "reps": 8, "reps_pending": 0}
    assert merge_set_patch(gravada, {"rpe": Decimal(9)})["reps_pending"] == 1
    assert merge_set_patch(gravada, {"set_type": "warmup"})["set_type"] == "working"  # fora dos editáveis


def test_put_preserva_as_colunas_do_modo_intensivo_e_a_pendencia():
    gravadas = [
        {"set_number": 1, "set_type": "working", "reps_pending": 1, "completed_at": "t1", "planned_weight_kg": 65,
         "load_adjust_source": "fatigue", "rpe_target_source": "protocol", "rpe_target_effective": 10},
        {"set_number": 2, "set_type": "working", "reps_pending": 1, "rpe_target_source": "protocol",
         "rpe_target_effective": 10},
    ]
    enviadas = [{"set_number": 1, "reps": None, "weight_kg": 65}, {"set_number": 2, "reps": 6, "weight_kg": 60}]
    um, dois = merge_sets_for_update(enviadas, gravadas)
    assert um["reps_pending"] == 1 and um["planned_weight_kg"] == 65 and um["load_adjust_source"] == "fatigue"
    assert um["rpe_target_effective"] == 10 and um["completed_at"] == "t1"
    assert dois["reps_pending"] == 0  # a edição informou as reps: confirmada
    nova = merge_sets_for_update([{"set_number": 3, "reps": None}], gravadas)[0]
    assert nova["reps_pending"] == 0 and nova["rpe_target_source"] is None


def test_agregados_ignoram_a_pendente():
    confirmada = {"set_type": "working", "reps": 10, "weight_kg": 50, "rpe": 8, "reps_pending": 0}
    pendente = {"set_type": "working", "reps": None, "weight_kg": 300, "rpe": None, "reps_pending": 1}
    assert services.compute_exercise_stats([confirmada, pendente]) == services.compute_exercise_stats([confirmada])


def test_rpe_target_effective_nao_alimenta_o_e1rm():
    """O e1RM lê só o RPE RELATADO (`es.rpe`); o alvo do protocolo fica em
    `rpe_target_effective` e não entra nas consultas de e1RM."""
    for funcao in (analytics._fetch_group_e1rm_rows, services.get_last_session_sets):
        assert "rpe_target_effective" not in inspect.getsource(funcao)
    from app.domain import e1rm

    assert "rpe_target" not in inspect.getsource(e1rm)


# ---------------------------------------------------------------------------
# Achado E2E 1: parametros da tecnica sem decisao do motor (ficha manual,
# refresh-prescription) - o RPE-alvo exibido e o mesmo que o POST grava.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("teto, alvo", [(10, 10), (9, 9), (8, 9)])
def test_snapshot_sem_decisao_leva_o_alvo_do_protocolo_pelo_teto_do_par(teto, alvo):
    from app.domain.prescription import build_prescription
    from app.domain.progression_engine import TECHNIQUE_SOURCE_PLAN

    safety = ts.build_training_safety(exercise_id=1, equipment_id=2, equipment_max_safe_rpe=teto)
    row = {
        "sets_count": 3,
        "reps_target": "8-10",
        "advanced_technique": "rest_pause",
        "advanced_technique_applications": "2",
        "advanced_technique_duration_seconds": 15,
    }
    params = build_prescription(row, safety=safety)["advanced_technique_parameters"]
    assert params["technique_code"] == "rest_pause"
    assert params["source"] == TECHNIQUE_SOURCE_PLAN
    assert params["applications"] == 2
    assert params["pause_seconds"] == 15
    assert params["target_rpe"] == alvo == ts.protocol_target_rpe(teto)
    # o POST grava o alvo do teto ao vivo cortado pelo congelado: mesmo numero
    assert ts.effective_protocol_target_rpe(teto, params["target_rpe"]) == alvo


def test_snapshot_sem_decisao_par_desconhecido_cai_em_9_e_rest_pause_sem_pausa_usa_o_padrao_do_dono():
    from app.domain.prescription import build_prescription
    from app.domain.progression_engine import REST_PAUSE_DEFAULT_PAUSE_SECONDS

    params = build_prescription({"sets_count": 3, "advanced_technique": "rest_pause"})["advanced_technique_parameters"]
    assert params["target_rpe"] == ts.PROTOCOL_TARGET_RPE_WITHOUT_FAILURE
    # sem pausa na ficha: o padrao do dono (decisao do usuario, 2026-10-25)
    assert params["pause_seconds"] == REST_PAUSE_DEFAULT_PAUSE_SECONDS == 15


def test_snapshot_rest_pause_com_duracao_na_ficha_vale_a_da_ficha():
    from app.domain.prescription import build_prescription

    row = {"sets_count": 3, "advanced_technique": "rest_pause", "advanced_technique_duration_seconds": 25}
    assert build_prescription(row)["advanced_technique_parameters"]["pause_seconds"] == 25


def test_snapshot_drop_set_sem_duracao_continua_sem_pausa():
    from app.domain.prescription import build_prescription

    row = {"sets_count": 3, "advanced_technique": "drop_set"}
    assert build_prescription(row)["advanced_technique_parameters"]["pause_seconds"] is None


@pytest.mark.parametrize(
    ("code", "duration", "expected"),
    [
        ("rest_pause", None, 15),
        ("rest_pause", 0, 15),
        ("rest_pause", 10, 10),
        ("drop_set", None, None),
        ("drop_set", 5, 5),
        ("forced_rep", None, None),
        (None, None, None),
    ],
)
def test_technique_pause_seconds_padrao_so_no_rest_pause(code, duration, expected):
    from app.domain.progression_engine import technique_pause_seconds

    assert technique_pause_seconds(code, duration) == expected


@pytest.mark.parametrize("code", ["fst7", "gvt"])
def test_snapshot_sem_decisao_sets_only_e_sem_tecnica(code):
    from app.domain.prescription import build_prescription

    safety = ts.build_training_safety(exercise_id=1, equipment_id=2, equipment_max_safe_rpe=10)
    row = {"sets_count": 7, "advanced_technique": code, "advanced_technique_applications": 3, "advanced_technique_duration_seconds": 30}
    params = build_prescription(row, safety=safety)["advanced_technique_parameters"]
    # FST-7/GVT sem catalogo: so series, sem aplicacoes nem pausa inventadas
    assert params["sets_only"] is True
    assert params["applications"] is None and params["pause_seconds"] is None
    assert params["target_rpe"] == ts.PROTOCOL_TARGET_RPE_WITH_FAILURE
    assert build_prescription({"sets_count": 3}, safety=safety)["advanced_technique_parameters"] is None


def test_decisao_sem_tecnica_nao_apaga_os_parametros_da_linha():
    """Linha manual: o motor v2 por par decide sem tecnica do Modelo."""
    from app.domain.prescription import build_prescription

    safety = ts.build_training_safety(exercise_id=1, equipment_id=2, equipment_max_safe_rpe=10)
    decision = {"decision": "HOLD", "advanced_technique_parameters": None, "safety_constraints": {"max_safe_rpe": 10}}
    snapshot = build_prescription({"sets_count": 3, "advanced_technique": "drop_set"}, safety=safety, decision=decision)
    assert snapshot["advanced_technique_parameters"]["technique_code"] == "drop_set"
    assert snapshot["advanced_technique_parameters"]["target_rpe"] == ts.PROTOCOL_TARGET_RPE_WITH_FAILURE


def test_congelamento_le_aplicacoes_e_pausa_da_linha():
    assert "advanced_technique_applications" in services._FREEZE_COLUMNS
    assert "advanced_technique_duration_seconds" in services._FREEZE_COLUMNS
