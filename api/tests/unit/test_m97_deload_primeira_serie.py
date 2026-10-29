"""Fix M97 (2026-10-26): sessão de deload desde a 1ª série e sem técnica.

Sem MySQL. Prova que:
- a ficha da sessão de deload (`GET /workout-plans/{id}?session_id=`) sai com a
  prescrição de deload do dono (`domain/deload.py`) em cada item: carga, reps,
  séries e RPE-alvo desde a 1ª série, em hipertrofia e em força com e sem e1RM,
  com as cargas de aquecimento/ajuste sobre a âncora de deload;
- a sessão de deload não tem técnica (ficha, briefing, avaliação de série e
  prompt), pela constante do dono `DELOAD_ALLOWED_TECHNIQUES`;
- a sessão normal (e a leitura sem `session_id`) devolve a ficha INTACTA;
- ligar o deload pelo PATCH meta antes da 1ª série muda a 1ª série (a ficha da
  sessão passa a sair com o overlay).
"""

from __future__ import annotations

import copy
from decimal import Decimal
from types import SimpleNamespace

from app import deload_periods, goal_prompt_context, workout_plans, workout_sessions
from app.domain import deload as dl
from app.domain import effective_intensity as ei
from app.domain import progression_engine as pe
from app.domain import session_loads
from app.domain import training_goal as tg
from app.engine_decision_context import PairEngineContext
from app.workout_plan_ai import normalize_load_target
from tests.support.fake_db import NullContext, fake_transaction

H = tg.TrainingGoal.HYPERTROPHY.value
S = tg.TrainingGoal.STRENGTH.value
PASSO = 2.5


def _item(goal=H, *, prescription=True, technique="drop_set", **extra):
    item = {
        "workout_plan_exercise_id": 7,
        "mapping_id": 11,
        "preferred_equipment_id": 2,
        "training_goal": goal,
        "sets_count": 4,
        "reps_target": "10-12",
        "load_target": "100kg",
        "rpe_target": "8",
        "advanced_technique": technique,
        "advanced_technique_applications": 2 if technique else None,
        "advanced_technique_duration_seconds": None,
        "feeler_set_sets_count": 1,
        "load_increment_kg": PASSO,
        "suggested_load_kg": 100.0,
        "suggested_reps": 10,
        **extra,
    }
    if prescription:
        shown = {
            "training_goal": goal,
            "sets_count": 4,
            "reps_target": "10-12",
            "load_target": "100kg",
            "suggested_load_kg": 100.0,
            "rpe_target": "8",
            "effective_rpe_target": 8,
            "effective_rpe_ceiling": 9,
            "advanced_technique": technique,
            "advanced_technique_parameters": {"application_loads_kg": [80.0, 65.0]} if technique else None,
        }
        shown.update(session_loads.session_load_plan(100.0, load_increment_kg=PASSO, feeler_sets_count=1))
        item["prescription"] = shown
    return item


class _PlanCursor(NullContext):
    """Origem do deload da sessão e carga da trilha por par."""

    def __init__(self, source, track_kg=None):
        self.source = source
        self.track_kg = track_kg
        self.executed: list[str] = []
        self._last = ""

    def execute(self, sql, params=None):
        self._last = " ".join(sql.split())
        self.executed.append(self._last)

    def fetchone(self):
        if "deload_source" in self._last:
            return {"deload_source": self.source}
        if "progression_tracks" in self._last:
            return {"working_load_kg": self.track_kg} if self.track_kg is not None else None
        return None


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _plano(*items):
    return {"id": 3, "exercises": list(items)}


# ---------------------------------------------------------------------------
# Dono: item da ficha na sessão de deload
# ---------------------------------------------------------------------------


def test_constante_de_tecnica_do_dono():
    assert dl.DELOAD_ALLOWED_TECHNIQUES == frozenset()
    assert dl.deload_allows_technique(None) and dl.deload_allows_technique("")
    assert not dl.deload_allows_technique("drop_set")
    assert not dl.deload_allows_technique("rest_pause")


def test_primeira_serie_hipertrofia_vem_do_dono():
    item = _item(H)
    prescricao = dl.item_deload_prescription(
        track_working_load_kg=100, snapshot=item["prescription"], plan_row=item, step_kg=PASSO
    )
    saida = dl.deload_plan_item(
        item, prescricao, format_load=normalize_load_target, session_load_plan=deload_periods._session_load_plan_for
    )
    carga = dl.deload_load_kg(100, PASSO)
    assert carga == prescricao.load_kg and carga < 100
    assert saida["load_target"] == normalize_load_target(carga)
    assert saida["suggested_load_kg"] == carga
    assert saida["sets_count"] == dl.deload_sets(4)
    assert saida["reps_target"] == str(dl.deload_reps("10-12", H))
    assert saida["rpe_target"] == str(dl.DELOAD_TARGET_RPE)
    shown = saida["prescription"]
    assert shown["effective_rpe_target"] == dl.DELOAD_TARGET_RPE
    assert shown["sets_count"] == dl.deload_sets(4)
    assert shown["suggested_load_kg"] == carga
    # aquecimento/ajuste sobre a âncora de deload (dono `session_loads`)
    esperado = session_loads.session_load_plan(carga, load_increment_kg=PASSO, feeler_sets_count=1)
    for chave in ("load_anchor_kg", "warmup_loads_kg", "feeler_load_kg", "feeler_loads_kg"):
        assert shown[chave] == esperado[chave]
    # sem técnica
    assert saida["advanced_technique"] is None and saida["advanced_technique_applications"] is None
    assert shown["advanced_technique"] is None and shown["advanced_technique_parameters"] is None
    assert shown["application_loads_kg"] == []
    assert saida["deload"] == prescricao.to_dict()
    # o item de entrada (a ficha) não muda
    assert item == _item(H)


def test_primeira_serie_forca_com_e1rm_parte_da_faixa_1rm():
    faixa = pe.e1rm_load_range(Decimal("123.33"), PASSO, S)
    assert faixa is not None
    item = _item(S, technique=None)
    item["prescription"]["strength_load"] = faixa.to_dict()
    prescricao = dl.item_deload_prescription(
        track_working_load_kg=90, snapshot=item["prescription"], plan_row=item, step_kg=PASSO
    )
    saida = dl.deload_plan_item(item, prescricao, format_load=normalize_load_target)
    assert prescricao.working_load_kg == faixa.suggested_kg
    assert saida["suggested_load_kg"] == dl.deload_load_kg(faixa.suggested_kg, PASSO)
    assert saida["prescription"]["strength_load"] is None  # a faixa cheia não é a carga de hoje
    assert saida["reps_target"] == str(dl.deload_reps("10-12", S))


def test_primeira_serie_forca_sem_e1rm_parte_da_trilha():
    item = _item(S, technique=None, reps_target=None)
    item["prescription"]["reps_target"] = None
    prescricao = dl.item_deload_prescription(
        track_working_load_kg=80, snapshot=item["prescription"], plan_row=item, step_kg=PASSO
    )
    saida = dl.deload_plan_item(item, prescricao, format_load=normalize_load_target)
    assert saida["suggested_load_kg"] == dl.deload_load_kg(80, PASSO)
    # sem faixa no item: o piso do default do objetivo (dono `prescription_defaults`)
    assert saida["reps_target"] == str(dl.deload_reps(None, S))


def test_rpe_alvo_de_deload_nunca_passa_o_teto_exibido():
    item = _item(H)
    item["prescription"]["effective_rpe_ceiling"] = dl.DELOAD_TARGET_RPE - 1
    prescricao = dl.item_deload_prescription(track_working_load_kg=100, snapshot=item["prescription"], plan_row=item)
    saida = dl.deload_plan_item(item, prescricao)
    assert saida["prescription"]["effective_rpe_target"] == dl.DELOAD_TARGET_RPE - 1


def test_sem_carga_de_base_mantem_a_carga_do_item():
    item = _item(H, prescription=False, load_target="corpo", suggested_load_kg=None)
    prescricao = dl.item_deload_prescription(track_working_load_kg=None, snapshot={}, plan_row=item)
    saida = dl.deload_plan_item(item, prescricao)
    assert prescricao.load_kg is None
    assert saida["load_target"] == "corpo" and saida["suggested_load_kg"] is None
    assert saida["sets_count"] == dl.deload_sets(4)


# ---------------------------------------------------------------------------
# Leitura da ficha pela sessão
# ---------------------------------------------------------------------------


def test_sessao_normal_devolve_a_ficha_intacta():
    plano = _plano(_item(H), _item(S, technique=None))
    antes = copy.deepcopy(plano)
    cursor = _PlanCursor(source=None, track_kg=100)
    saida = deload_periods.apply_session_deload_to_plan(_Conn(cursor), plano, 1, 9)
    assert saida is plano and saida == antes
    assert len(cursor.executed) == 1  # só a origem do deload da sessão
    assert not any("progression_tracks" in sql for sql in cursor.executed)


def test_sem_sessao_nada_e_lido():
    plano = _plano(_item(H))
    cursor = _PlanCursor(source="manual")
    assert deload_periods.apply_session_deload_to_plan(_Conn(cursor), plano, 1, None) is plano
    assert cursor.executed == []


def test_sessao_de_deload_aplica_o_dono_em_cada_item():
    plano = _plano(_item(H))
    antes = copy.deepcopy(plano)
    cursor = _PlanCursor(source="period", track_kg=Decimal("100"))
    saida = deload_periods.apply_session_deload_to_plan(_Conn(cursor), plano, 1, 9)
    assert plano == antes  # a ficha lida não é alterada
    item = saida["exercises"][0]
    assert item["suggested_load_kg"] == dl.deload_load_kg(100, PASSO)
    assert item["sets_count"] == dl.deload_sets(4)
    assert item["advanced_technique"] is None
    assert saida["deload"]["active"] is True and saida["deload"]["source"] == "period"


def test_falha_no_overlay_devolve_a_ficha_de_sempre(monkeypatch):
    plano = _plano(_item(H))

    def _quebra(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(deload_periods.deload_owner, "deload_plan_item", _quebra)
    saida = deload_periods.apply_session_deload_to_plan(_Conn(_PlanCursor("manual", 100)), plano, 1, 9)
    assert saida is plano


def test_ligar_deload_pelo_meta_antes_da_primeira_serie_muda_a_primeira_serie():
    """A origem é relida a cada leitura: depois do PATCH meta (sem série
    gravada), a mesma ficha passa a sair com a prescrição de deload."""
    plano = _plano(_item(H))
    normal = deload_periods.apply_session_deload_to_plan(_Conn(_PlanCursor(None, 100)), plano, 1, 9)
    assert normal["exercises"][0]["load_target"] == "100kg"
    origem = dl.toggled_deload_source(None, requested=True, saved_sets=0, period_active=False)
    deload = deload_periods.apply_session_deload_to_plan(_Conn(_PlanCursor(origem, 100)), plano, 1, 9)
    assert deload["exercises"][0]["load_target"] == normalize_load_target(dl.deload_load_kg(100, PASSO))


def test_rota_da_ficha_sem_sessao_nao_abre_transacao(monkeypatch):
    plano = _plano(_item(H))
    monkeypatch.setattr(workout_plans.services, "get_workout_plan", lambda pid, user_id=None: plano)

    def _proibido():
        raise AssertionError("sem session_id não lê deload")

    monkeypatch.setattr(workout_plans, "transaction", _proibido)
    assert workout_plans.get_plan(3, session_id=None, identity=SimpleNamespace(user_id=1)) is plano


def test_rota_da_ficha_com_sessao_de_deload(monkeypatch):
    monkeypatch.setattr(workout_plans.services, "get_workout_plan", lambda pid, user_id=None: _plano(_item(H)))
    monkeypatch.setattr(workout_plans, "transaction", fake_transaction(_PlanCursor("manual", 100)))
    saida = workout_plans.get_plan(3, session_id=9, identity=SimpleNamespace(user_id=1))
    assert saida["exercises"][0]["sets_count"] == dl.deload_sets(4)


# ---------------------------------------------------------------------------
# Avaliação de série: sem protocolo de técnica no deload
# ---------------------------------------------------------------------------


class _SerieCursor(NullContext):
    def __init__(self, row):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), tuple(params or ())))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


def _salvar(monkeypatch, row, payload, protocol=9):
    cursor = _SerieCursor(row)
    monkeypatch.setattr(workout_sessions, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(workout_sessions, "ensure_valid_codes", lambda codes: None)
    monkeypatch.setattr(workout_sessions, "_get_live_session", lambda cur, sid, uid: {"id": sid})
    monkeypatch.setattr(workout_sessions, "_refresh_exercise_aggregates", lambda cur, eid: None)
    monkeypatch.setattr(workout_sessions, "_refresh_activity_avg_rpe", lambda cur, sid: None)
    monkeypatch.setattr(workout_sessions, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(workout_sessions, "_protocol_rpe_target", lambda conn, r, st, tc: protocol)
    monkeypatch.setattr(
        workout_sessions.effective_intensity,
        "resolve_for_logged_exercise",
        lambda conn, eid: ei.EffectiveIntensity(rpe_target_effective=9, ceiling=10, failure_allowed=True),
    )
    monkeypatch.setattr(
        workout_sessions, "load_pair_step_context", lambda conn, m, g: PairEngineContext(step_kg=Decimal("2.5"))
    )
    workout_sessions.add_session_set(1, 5, payload, identity=SimpleNamespace(user_id=1))
    return next(params for sql, params in cursor.calls if sql.startswith("INSERT INTO exercise_sets"))


_ROW = {
    "id": 5, "plan_technique": "rest_pause", "plan_reps_target": "10-12", "prescription_json": None,
    "mapping_id": 11, "gym_equipment_id": 2, "working_load_kg": Decimal("50"),
}


def _payload():
    return workout_sessions.SessionSetIn(
        set_number=1, reps=10, weight_kg=Decimal(30), rpe=Decimal(6), rpe_target_source="protocol"
    )


def test_deload_ignora_o_protocolo_da_tecnica(monkeypatch):
    params = _salvar(monkeypatch, {**_ROW, "deload_source": "manual"}, _payload())
    assert dl.DELOAD_TARGET_RPE in params
    assert "protocol" not in params


def test_sessao_normal_mantem_o_protocolo(monkeypatch):
    params = _salvar(monkeypatch, {**_ROW, "deload_source": None}, _payload(), protocol=9)
    assert "protocol" in params
    assert 9 in params


# ---------------------------------------------------------------------------
# Prompt por série em deload
# ---------------------------------------------------------------------------


def test_prompt_de_deload_nao_sugere_tecnica():
    texto = goal_prompt_context.deload_section(True)
    # O aviso (prosa do template) e privado, M103 P2: aqui so a escolha do ramo.
    assert goal_prompt_context._deload_techniques_text() in texto
    assert not goal_prompt_context.DELOAD_ALLOWED_TECHNIQUES
    assert "técnica" not in goal_prompt_context.deload_section(True, scope=goal_prompt_context.SCOPE_WINDOW)
    assert goal_prompt_context.deload_section(False) == ""


# ---------------------------------------------------------------------------
# Briefing: sem técnica na sessão de deload
# ---------------------------------------------------------------------------


def test_briefing_de_deload_nao_oferece_tecnica(monkeypatch):
    from tests.unit import test_m4_briefing_exercicio as base

    respostas = {**base.RESPOSTAS_COMPLETAS, "AS deload_source FROM activities": [{"deload_source": "manual"}]}
    base._montar(monkeypatch, respostas)
    resposta = base.route.get_exercise_briefing(3, 777, session_id=9, identity=base.IDENTIDADE)
    assert resposta["deload"]["active"] is True
    assert resposta["prescription"]["advanced_technique"] is None
    assert resposta["prescription"]["advanced_technique_applications"] is None

    base._montar(monkeypatch, {**base.RESPOSTAS_COMPLETAS, "AS deload_source FROM activities": [{"deload_source": None}]})
    normal = base.route.get_exercise_briefing(3, 777, session_id=9, identity=base.IDENTIDADE)
    assert normal["deload"]["active"] is False
    assert normal["prescription"]["advanced_technique"] == "drop_set"
