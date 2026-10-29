"""M97-D2 (2026-10-26): troca de par no Modo Treino numa sessão de deload.

Sem MySQL. Antes, a 1ª série do par novo (`GET /exercises/suggested-progression`)
vinha com a sugestão cheia e só a 2ª caía pela regra `NEXT_SET_DELOAD`. Prova que:
- com `session_id` de uma sessão de deload, a sugestão do PAR NOVO sai com a
  prescrição de deload do dono (`domain/deload.py`) desde a 1ª série: carga
  reduzida, reps no piso, RPE-alvo do deload, sem faixa %1RM, em hipertrofia e
  em força com e sem e1RM do par novo;
- as séries seguintes (regra da próxima série) partem da MESMA base do par
  novo, não do snapshot do item original (exemplo: hack 80 kg -> leg press);
- fora do deload, a troca de um item de FORÇA sai no objetivo do item (o
  resolvido do item da ficha da sessão vence o do pedido); item de hipertrofia
  ou pedido sem objetivo: a resposta IDÊNTICA à de sempre;
- a sessão normal de hipertrofia devolve a resposta IDÊNTICA à de sem
  `session_id`; sem `session_id` nenhuma leitura nova.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal

import pytest

from app import deload_periods, engines_extra, services
from app.domain import deload as dl
from app.domain import effective_intensity as ei
from app.domain import prescription_service as svc
from app.domain import progression_engine as pe
from app.domain import training_goal as tg
from app.workout_plan_ai import normalize_load_target
from tests.support.fake_db import CursorFactoryConn, NullContext

H = tg.TrainingGoal.HYPERTROPHY.value
S = tg.TrainingGoal.STRENGTH.value
PASSO = 2.5
PAR_NOVO = 21
GYM_EQ = 33
FAIXA = pe.e1rm_load_range(123.33, PASSO, S).to_dict()  # sugerido 100 kg


class _Cursor(NullContext):
    """Catálogo, origem do deload da sessão e trilha do par novo."""

    def __init__(self, state):
        self.state = state
        self._last = ""

    def execute(self, sql, params=None):
        self._last = " ".join(sql.split())
        self.state["executed"].append(self._last)

    def fetchone(self):
        if "deload_source" in self._last:
            return {"deload_source": self.state["source"]}
        if "wpe.training_goal" in self._last:
            return self.state["item"]
        if "progression_tracks" in self._last:
            track = self.state["track"]
            return {"working_load_kg": track} if track is not None else None
        return {"id": PAR_NOVO}


@pytest.fixture
def rota(monkeypatch):
    state = {"source": None, "track": None, "e1rm": True, "executed": [], "goals": [], "item": None, "load": 90.0}

    @contextmanager
    def fake_transaction():
        yield CursorFactoryConn(_Cursor, state)

    def fake_suggest(conn, user_id, exercise_id, equipment_id=None, **kwargs):
        goal = kwargs.get("training_goal")
        state["goals"].append(goal)
        forca = goal == S
        evidence = {pe.EVIDENCE_STRENGTH_E1RM_LOAD: FAIXA} if forca and state["e1rm"] else {}
        return svc.PairSuggestion(
            exercise_id=PAR_NOVO,
            equipment_id=7,
            decision={
                "decision": pe.INCREASE_LOAD,
                "evidence": evidence,
                "suggested_load_kg": state["load"],
                "suggested_reps_min": 3 if forca else 10,
                "suggested_reps_max": 5 if forca else 12,
                "effective_rpe_target": 8,
                "effective_rpe_ceiling": 9,
            },
            suggested_load_kg=state["load"],
            load_source=svc.LOAD_SOURCE_ENGINE,
            load_increment_kg=PASSO,
        )

    monkeypatch.setattr(services, "transaction", fake_transaction)
    monkeypatch.setattr(services, "_resolve_canonical_exercise_id", lambda mapping_id, cur: PAR_NOVO)
    monkeypatch.setattr(services, "list_exercise_logs", lambda limit, user_id, only_mapping_id: [])
    monkeypatch.setattr(ei, "equipment_id_of_gym_equipment", lambda conn, gym_equipment_id: 7)
    monkeypatch.setattr(svc, "suggest_for_pair", fake_suggest)
    return state


def _sugestao(**kwargs):
    return services.suggested_progression_for_mapping(PAR_NOVO, user_id=1, gym_equipment_id=GYM_EQ, **kwargs)


def _assert_alvos_de_deload(resposta, carga_kg, reps):
    assert resposta["suggested_load_kg"] == carga_kg
    assert resposta["load_target"] == normalize_load_target(carga_kg)
    assert resposta["reps_target"] == str(reps) and resposta["suggested_reps"] == reps
    assert resposta["rpe_target"] == str(dl.DELOAD_TARGET_RPE)
    assert "strength_load" not in resposta
    assert resposta["progression_note"] == dl.deload_reason_label()
    bloco = resposta["deload"]
    assert bloco["load_kg"] == carga_kg and bloco["target_rpe"] == dl.DELOAD_TARGET_RPE
    # séries: a ficha da sessão já as traz reduzidas (sem corte duplo)
    assert bloco["sets_count"] is None
    # a sugestão do par nunca traz técnica
    assert not any("technique" in chave for chave in resposta)


# ---------------------------------------------------------------------------
# Troca de par numa sessão de deload: 1ª série com a prescrição de deload
# ---------------------------------------------------------------------------


def test_troca_em_deload_hipertrofia_parte_da_trilha_do_par_novo(rota):
    rota.update(source=dl.DeloadSource.MANUAL.value, track=80)
    resposta = _sugestao(session_id=9, item_training_goal=H)
    _assert_alvos_de_deload(resposta, dl.deload_load_kg(80, PASSO), 10)
    assert resposta["deload"]["working_load_kg"] == 80
    assert rota["goals"] == [None]  # hipertrofia = o objetivo default do motor


def test_troca_em_deload_hipertrofia_sem_trilha_parte_da_sugestao(rota):
    rota.update(source=dl.DeloadSource.PERIOD.value, track=None)
    resposta = _sugestao(session_id=9, item_training_goal=H)
    _assert_alvos_de_deload(resposta, dl.deload_load_kg(90, PASSO), 10)


def test_troca_em_deload_forca_com_e1rm_do_par_novo_parte_da_faixa(rota):
    rota.update(source=dl.DeloadSource.MANUAL.value, track=80, e1rm=True)
    resposta = _sugestao(session_id=9, item_training_goal=S)
    assert rota["goals"] == [S]  # a sugestão do par novo é a do objetivo do item
    assert resposta["deload"]["working_load_kg"] == FAIXA["suggested_kg"]
    _assert_alvos_de_deload(resposta, dl.deload_load_kg(FAIXA["suggested_kg"], PASSO), 3)
    assert resposta["deload"]["training_goal"] == S


def test_troca_em_deload_forca_sem_e1rm_parte_da_trilha(rota):
    rota.update(source=dl.DeloadSource.MANUAL.value, track=80, e1rm=False)
    resposta = _sugestao(session_id=9, item_training_goal=S)
    _assert_alvos_de_deload(resposta, dl.deload_load_kg(80, PASSO), 3)


def test_rpe_de_deload_nunca_passa_o_teto_do_par():
    sugestao = {"rpe_target": "8", "effective_rpe_ceiling": dl.DELOAD_TARGET_RPE - 1}
    prescricao = dl.pair_deload_prescription(track_working_load_kg=None, suggestion=sugestao, training_goal=H)
    saida = dl.deload_pair_suggestion(sugestao, prescricao)
    assert saida["rpe_target"] == str(dl.DELOAD_TARGET_RPE - 1)


def test_par_sem_historico_mantem_a_carga_vazia_e_aplica_reps_e_rpe():
    vazia = {"reps_target": "", "load_target": "", "rpe_target": "", "progression_note": "Sem histórico registrado."}
    prescricao = dl.pair_deload_prescription(track_working_load_kg=None, suggestion=vazia, training_goal=S)
    saida = dl.deload_pair_suggestion(vazia, prescricao, format_load=normalize_load_target)
    assert saida["load_target"] == "" and prescricao.load_kg is None
    piso_forca, _ = dl.parse_reps_range(dl.defaults_for(S).reps_target)
    assert saida["reps_target"] == str(piso_forca)
    assert saida["rpe_target"] == str(dl.DELOAD_TARGET_RPE)


def test_falha_no_overlay_devolve_a_sugestao_de_sempre(rota, monkeypatch):
    rota.update(source=dl.DeloadSource.MANUAL.value, track=80)

    def boom(**kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(dl, "pair_deload_prescription", boom)
    resposta = _sugestao(session_id=9, item_training_goal=H)
    assert "deload" not in resposta and resposta["reps_target"] == "10-12"


# ---------------------------------------------------------------------------
# Sessão normal e sem sessão: a resposta de sempre
# ---------------------------------------------------------------------------


def test_sessao_normal_hipertrofia_devolve_a_resposta_identica(rota):
    antes = _sugestao()
    sem_leituras = list(rota["executed"])
    rota["item"] = {"training_goal": H, "prescription_json": '{"training_goal": "%s"}' % H}
    depois = _sugestao(session_id=9, workout_plan_exercise_id=7, item_training_goal=H)
    assert depois == antes
    assert "deload" not in depois and rota["goals"] == [None, None]
    novas = rota["executed"][len(sem_leituras):]
    assert len(novas) == len(sem_leituras) + 2  # a origem da sessão e o objetivo do item
    assert not any("progression_tracks" in sql for sql in novas)


@pytest.mark.parametrize("objetivo", [None, "", H, "desconhecido"])
def test_pedido_sem_objetivo_ou_de_hipertrofia_e_identico(rota, objetivo):
    antes = _sugestao()
    assert _sugestao(item_training_goal=objetivo) == antes
    assert rota["goals"] == [None, None]
    assert len(rota["executed"]) == 2  # só o catálogo, nos dois pedidos


def test_troca_normal_de_item_de_forca_sai_no_objetivo_do_item(rota):
    hipertrofia = _sugestao()
    forca = _sugestao(item_training_goal=S)
    assert rota["goals"] == [None, S]
    assert forca["strength_load"] == FAIXA and forca["reps_target"] == "3-5"
    assert "strength_load" not in hipertrofia and "deload" not in forca


def test_servidor_prefere_o_objetivo_resolvido_do_item_da_ficha(rota):
    # o cliente diz hipertrofia, mas o item congelado da ficha da sessão é de força
    rota["item"] = {"training_goal": H, "prescription_json": '{"training_goal": "%s"}' % S}
    _sugestao(session_id=9, workout_plan_exercise_id=7, item_training_goal=H)
    # item fora da ficha da sessão: vale o do pedido
    rota["item"] = None
    _sugestao(session_id=9, workout_plan_exercise_id=8, item_training_goal=S)
    # snapshot sem objetivo: a coluna da 093
    rota["item"] = {"training_goal": S, "prescription_json": None}
    _sugestao(session_id=9, workout_plan_exercise_id=7, item_training_goal=None)
    assert rota["goals"] == [S, S, S]


def test_sem_sessao_nenhuma_leitura_nova(rota):
    rota["source"] = dl.DeloadSource.MANUAL.value  # irrelevante sem session_id
    resposta = _sugestao(workout_plan_exercise_id=7, item_training_goal=H)
    assert "deload" not in resposta
    assert not any(
        "deload_source" in sql or "progression_tracks" in sql or "workout_plan_exercises" in sql
        for sql in rota["executed"]
    )


def test_rota_repassa_sessao_item_e_objetivo(monkeypatch):
    chamadas = []

    def fake(mapping_id, **kwargs):
        chamadas.append((mapping_id, kwargs))
        return {"reps_target": "", "clamps": []}

    monkeypatch.setattr(services, "suggested_progression_for_mapping", fake)
    identidade = type("I", (), {"user_id": 1})()
    engines_extra.get_suggested_progression(
        mapping_id=5, equipment_id=GYM_EQ, session_id=9, workout_plan_exercise_id=7, training_goal=S,
        identity=identidade,
    )
    engines_extra.get_suggested_progression(
        mapping_id=5, equipment_id=None, session_id=None, workout_plan_exercise_id=None, training_goal=None,
        identity=identidade,
    )
    assert chamadas == [
        (5, {"user_id": 1, "gym_equipment_id": GYM_EQ, "session_id": 9, "workout_plan_exercise_id": 7,
             "item_training_goal": S}),
        (5, {"user_id": 1, "gym_equipment_id": None, "session_id": None, "workout_plan_exercise_id": None,
             "item_training_goal": None}),
    ]


# ---------------------------------------------------------------------------
# Séries seguintes do par trocado: a MESMA base da 1ª série
# ---------------------------------------------------------------------------


HACK, LEG_PRESS, GYM_HACK = 31, PAR_NOVO, 40


def _proxima_serie(rota, prescription_json, plan_row):
    from app import engine_decision_context as edc

    conn = CursorFactoryConn(_Cursor, rota)
    return edc.load_deload_prescription(conn, 1, LEG_PRESS, GYM_EQ, prescription_json, PASSO, "10-12", plan_row=plan_row)


def test_hack_80_para_leg_press_a_segunda_serie_parte_da_base_do_par_novo(rota):
    # o leg press não tem trilha; a sugestão do motor para ele é 200 kg
    rota.update(source=dl.DeloadSource.MANUAL.value, track=None, load=200.0)
    primeira = _sugestao(session_id=9, workout_plan_exercise_id=7, item_training_goal=H)
    snapshot_do_hack = '{"training_goal": "%s", "suggested_load_kg": 80.0, "reps_target": "10-12"}' % H
    plano = {"plan_mapping_id": HACK, "plan_gym_equipment_id": GYM_HACK, "plan_training_goal": H}
    proxima = _proxima_serie(rota, snapshot_do_hack, plano)
    assert proxima.load_kg == primeira["deload"]["load_kg"] == dl.deload_load_kg(200, PASSO)
    # executou a carga da 1ª série: mantém (antes caía para 60% do hack = 48 kg)
    assert dl.deload_next_set_load(proxima.load_kg, proxima, PASSO).direction == pe.HOLD
    assert proxima.load_kg != dl.deload_load_kg(80, PASSO)


def test_forca_com_e1rm_do_par_novo_a_segunda_serie_parte_da_faixa_do_par_novo(rota):
    rota.update(source=dl.DeloadSource.MANUAL.value, track=80, e1rm=True)
    primeira = _sugestao(session_id=9, workout_plan_exercise_id=7, item_training_goal=S)
    faixa_do_original = json.dumps(pe.e1rm_load_range(200, PASSO, S).to_dict())
    snapshot = '{"training_goal": "%s", "strength_load": %s}' % (S, faixa_do_original)
    plano = {"plan_mapping_id": HACK, "plan_gym_equipment_id": GYM_HACK}
    proxima = _proxima_serie(rota, snapshot, plano)
    assert proxima.load_kg == primeira["deload"]["load_kg"] == dl.deload_load_kg(FAIXA["suggested_kg"], PASSO)
    assert rota["goals"][-1] == S  # objetivo do snapshot do item


def test_par_planejado_usa_o_snapshot_como_antes(rota, monkeypatch):
    rota.update(track=None)

    def nunca(*args, **kwargs):
        raise AssertionError("par planejado não consulta o par novo")

    monkeypatch.setattr(services, "swapped_pair_deload_prescription", nunca)
    snapshot = '{"training_goal": "%s", "suggested_load_kg": 80.0}' % H
    proxima = _proxima_serie(rota, snapshot, {"plan_mapping_id": LEG_PRESS, "plan_gym_equipment_id": GYM_EQ})
    assert proxima.load_kg == dl.deload_load_kg(80, PASSO)
    assert _proxima_serie(rota, snapshot, None).load_kg == dl.deload_load_kg(80, PASSO)


def test_falha_na_base_do_par_novo_volta_a_base_de_antes(rota, monkeypatch):
    rota.update(track=None)

    def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(services, "swapped_pair_deload_prescription", boom)
    snapshot = '{"training_goal": "%s", "suggested_load_kg": 80.0}' % H
    proxima = _proxima_serie(rota, snapshot, {"plan_mapping_id": HACK, "plan_gym_equipment_id": GYM_HACK})
    assert proxima.load_kg == dl.deload_load_kg(80, PASSO)


@pytest.mark.parametrize(
    "planejado, lancado, trocado",
    [
        ((HACK, GYM_HACK), (HACK, GYM_HACK), False),
        ((HACK, GYM_HACK), (LEG_PRESS, GYM_HACK), True),
        ((HACK, GYM_HACK), (HACK, GYM_EQ), True),
        ((HACK, None), (HACK, GYM_EQ), True),
        ((None, None), (HACK, GYM_EQ), False),
        (("31", "40"), (HACK, GYM_HACK), False),
    ],
)
def test_par_trocado(planejado, lancado, trocado):
    assert dl.pair_swapped(*planejado, *lancado) is trocado


def test_overlay_usa_a_trilha_do_par_novo_no_objetivo_do_item():
    lidos = []

    class _C(NullContext):
        def execute(self, sql, params=None):
            lidos.append(params)

        def fetchone(self):
            return {"working_load_kg": Decimal("80")}

    conn = type("Conn", (), {"cursor": lambda self: _C()})()
    saida = deload_periods.apply_session_deload_to_pair_suggestion(
        conn, {"reps_target": "3-5", "load_increment_kg": PASSO}, 1,
        mapping_id=PAR_NOVO, gym_equipment_id=GYM_EQ, training_goal=S,
    )
    assert lidos == [(1, PAR_NOVO, GYM_EQ, S)]
    assert saida["suggested_load_kg"] == dl.deload_load_kg(80, PASSO)
