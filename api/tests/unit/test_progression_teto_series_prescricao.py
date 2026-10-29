"""O teto de 4 series vale para QUALQUER origem do `sets_count` da trilha
(PLANO_20260912.md secoes 5.4, 5.6 e D9).

Bug de producao (2026-09-19). Depois do replay que corrigiu o teto vindo da
EXECUCAO (PR #133), sobraram **5 trilhas congeladas** com `sets_count > 4`.
Cenario sintetico baseado nesse caso real (ids e cargas ilustrativos):

| trilha | exercicio                                  | equipamento                | sets | carga   |
|--------|--------------------------------------------|----------------------------|------|---------|
| 1      | Supino inclinado na maquina                | Maquina articulada         | 9    | 40 kg   |
| 2      | Elevacao lateral na maquina                | Halteres                   | 5    | 10 kg   |
| 3      | Aducao de quadril em pe na maquina         | Portico multi-quadril      | 5    | 55 kg   |
| 4      | Flexao de quadril em pe na maquina         | Portico multi-quadril      | 9    | 55 kg   |
| 5      | Elevacao de panturrilhas no hack squat     | Hack squat                 | 9    | 90 kg   |

A origem NAO e a execucao: e a **prescricao**. `progression_evaluator.track_for`
criava a trilha com `execution.prescribed.sets` sem teto, e fichas geradas entre
2026-08-23 e 2026-09-07 pelo caminho legado (`engine_version` nulo, geracao por
LLM) traziam 5, 6 e 9 series - a IA tratou o `percent_budget` de um item de
GRUPO MUSCULAR do modelo (Modo A: orcamento do grupo no slot, para distribuir
entre 2-3 exercicios) como series de um unico exercicio. Os modelos do usuario
estao corretos.

Efeito: a secao 5.4 exige `nº de working >= S`; com `S = 9` todo treino normal
virava NEUTRO por `sets_below_prescribed_dose` e a trilha nunca mais podia
qualificar, zerar a sequencia nem regredir (D10) - congelada para sempre.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

import app.domain.progression_engine as pe
import app.domain.progression_evaluator as ev
from app import progression_state as ps
from app.domain.training_history import PrescribedSpec
from tests.support import progression_fixtures as fx
from tests.support.fake_db import SharedCursorConn

NOW = datetime(2026, 9, 19, 12, 25)

# (trilha, exercise_id, equipment_id, muscle_group_id, series prescritas, carga)
CASOS = [
    (1, 101, 201, 11, 9, 40),
    (2, 102, 202, 12, 5, 10),
    (3, 103, 203, 13, 5, 55),
    (4, 104, 203, 13, 9, 55),
    (5, 105, 205, 14, 9, 90),
]
IDS_CASOS = [f"trilha-{caso[0]}" for caso in CASOS]


def _prescricao(sets_count: int) -> PrescribedSpec:
    """A prescricao legada da ficha: `sets_count` do item de grupo do modelo."""
    return PrescribedSpec(sets=sets_count, reps_min=6, reps_max=8, rpe_target=10)


def _historia(exercise_id: int, equipment_id: int, prescritas: int, carga: Any, series_executadas: int):
    """Duas sessoes: a primeira calibra a trilha (neutra, secao 5.10), a
    segunda e um treino normal que DEVE poder qualificar."""
    prescribed = _prescricao(prescritas)
    return [
        fx.session(
            activity_id=1,
            day="2026-09-05",
            executions=[
                fx.execution(
                    exercise_id=exercise_id,
                    equipment_id=equipment_id,
                    prescribed=prescribed,
                    set_list=fx.sets(reps=10, weight=carga, rpe=10, count=series_executadas),
                )
            ],
        ),
        fx.session(
            activity_id=2,
            day="2026-09-12",
            executions=[
                fx.execution(
                    exercise_id=exercise_id,
                    equipment_id=equipment_id,
                    prescribed=prescribed,
                    set_list=fx.sets(reps=10, weight=carga, rpe=10, count=series_executadas),
                )
            ],
        ),
    ]


def _inputs(exercise_id: int, equipment_id: int, muscle_group_id: int):
    """Trilha AINDA NAO existe: o `SessionInputs` so leva os metadados de
    catalogo (e assim que as 5 trilhas reais nasceram, da prescricao)."""
    return fx.inputs(
        tracks=[
            fx.track(
                exercise_id=exercise_id,
                equipment_id=equipment_id,
                muscle_group_id=muscle_group_id,
                load=None,
            )
        ]
    )


# ===========================================================================
# clamp_sets_count: o teto, em um lugar so
# ===========================================================================


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (1, 1),
        (2, 2),
        (3, 3),
        (4, 4),
        (5, pe.MAX_SETS_COUNT),
        (6, pe.MAX_SETS_COUNT),
        (9, pe.MAX_SETS_COUNT),
        (99, pe.MAX_SETS_COUNT),
    ],
)
def test_clamp_respeita_o_teto_da_secao_5_6(entrada, esperado):
    assert pe.clamp_sets_count(entrada) == esperado


@pytest.mark.parametrize("entrada", [None, 0, -3, "", "abc"])
def test_clamp_sem_numero_utilizavel_cai_no_default_de_tres(entrada):
    assert pe.clamp_sets_count(entrada) == pe.DEFAULT_SETS_COUNT


# ===========================================================================
# os 5 casos (sinteticos, baseados no caso real)
# ===========================================================================


@pytest.mark.parametrize(("trilha", "exercise_id", "equipment_id", "group_id", "prescritas", "carga"), CASOS, ids=IDS_CASOS)
def test_caso_real_trilha_da_prescricao_nasce_com_no_maximo_quatro_series(
    trilha, exercise_id, equipment_id, group_id, prescritas, carga
):
    """Antes: a trilha nascia com 5 ou 9 series porque a ficha pedia isso."""
    result = ev.evaluate_history(
        _historia(exercise_id, equipment_id, prescritas, carga, 4),
        inputs=_inputs(exercise_id, equipment_id, group_id),
    )
    track = result.tracks[pe.track_key(exercise_id, equipment_id, "rested")]
    assert track.sets_count == pe.MAX_SETS_COUNT, f"trilha {trilha}"


@pytest.mark.parametrize(("trilha", "exercise_id", "equipment_id", "group_id", "prescritas", "carga"), CASOS, ids=IDS_CASOS)
def test_caso_real_treino_normal_volta_a_qualificar(trilha, exercise_id, equipment_id, group_id, prescritas, carga):
    """O efeito que precisa acabar: com `S = 9` (ou 5) a segunda sessao era
    NEUTRA por `sets_below_prescribed_dose` e a trilha ficava congelada."""
    sessions = _historia(exercise_id, equipment_id, prescritas, carga, pe.MAX_SETS_COUNT)
    inputs = _inputs(exercise_id, equipment_id, group_id)
    # a 1a sessao calibra a trilha (neutra, secao 5.10); o veredito e da 2a
    calibrada = ev.evaluate_history(sessions[:1], inputs=inputs)
    result = ev.evaluate_session(
        sessions[1], sessions, group_states=calibrada.group_states, tracks=calibrada.tracks, inputs=inputs
    )
    assert calibrada.tracks[pe.track_key(exercise_id, equipment_id, "rested")].sets_count == pe.MAX_SETS_COUNT
    outcome = result.track_outcomes[pe.track_key(exercise_id, equipment_id, "rested")]
    assert outcome.outcome == pe.QUALIFY, f"trilha {trilha}: {outcome.reason}"
    assert outcome.reason != pe.REASON_SETS_BELOW_DOSE


def test_caso_real_com_a_prescricao_antiga_a_trilha_ficava_congelada():
    """Prova do bug (o que acontecia ANTES): sem o teto na prescricao, `S = 9`
    tornava impossivel qualificar com 4 series de trabalho."""
    trilha, exercise_id, equipment_id, group_id, prescritas, carga = CASOS[0]
    congelada = fx.track(
        exercise_id=exercise_id,
        equipment_id=equipment_id,
        muscle_group_id=group_id,
        load=carga,
        sets_count=prescritas,  # o estado que a trilha congelada tinha: 9
    )
    outcome = pe.evaluate_track_session(
        fx.execution(
            exercise_id=exercise_id,
            equipment_id=equipment_id,
            prescribed=_prescricao(prescritas),
            set_list=fx.sets(reps=10, weight=carga, rpe=10, count=4),
        ),
        congelada,
        reps_min=6,
        reps_max=8,
        ceiling=Decimal("10"),
        failure_allowed=True,
    )
    assert outcome.outcome == pe.NEUTRAL
    assert outcome.reason == pe.REASON_SETS_BELOW_DOSE


# ===========================================================================
# o predicado da secao 5.4 NAO foi afrouxado
# ===========================================================================


def test_sessao_com_menos_series_que_o_teto_continua_neutra():
    """Regressao: o teto limita `S`, nao afrouxa o predicado. Trilha com 4
    series e sessao com 3 continua NEUTRA por decisao de dose."""
    track = fx.track(sets_count=pe.MAX_SETS_COUNT, load=40)
    outcome = pe.evaluate_track_session(
        fx.execution(set_list=fx.sets(reps=12, weight=40, rpe=10, count=3)),
        track,
        reps_min=10,
        reps_max=12,
        ceiling=Decimal("10"),
        failure_allowed=True,
    )
    assert outcome.outcome == pe.NEUTRAL
    assert outcome.reason == pe.REASON_SETS_BELOW_DOSE


def test_trilha_de_tres_series_continua_exigindo_tres():
    """O teto nao empurra ninguem para 4: quem tem `S = 3` qualifica com 3."""
    track = fx.track(sets_count=3, load=40)
    outcome = pe.evaluate_track_session(
        fx.execution(set_list=fx.sets(reps=12, weight=40, rpe=10, count=3)),
        track,
        reps_min=10,
        reps_max=12,
        ceiling=Decimal("10"),
        failure_allowed=True,
    )
    assert outcome.outcome == pe.QUALIFY


# ===========================================================================
# execucao: o caso do abdominal (PR #133) continua funcionando
# ===========================================================================


def test_execucao_com_seis_series_nao_muda_o_contador_da_trilha():
    """Cenario sintetico baseado em caso real: 6 series `working` numa sessao. Antes parava no
    teto de 4; Lote 2/P3 (2026-10-11): series extras nao mudam `sets_count`
    (so `ADD_SET`), entao a trilha fica nas 3 do Modelo."""
    track = fx.track(sets_count=3, load=35)
    updated = ev.update_track_execution(
        track,
        fx.execution(set_list=fx.sets(reps=12, weight=40, rpe=9, count=6)),
        now=NOW,
        reps_min=10,
    )
    assert updated.sets_count == 3


def test_execucao_com_duas_series_nao_reduz_o_estado():
    """A trilha nunca BAIXA `sets_count` pela execucao (a F5 subiu por platô)."""
    track = fx.track(sets_count=pe.MAX_SETS_COUNT, load=35)
    updated = ev.update_track_execution(
        track,
        fx.execution(set_list=fx.sets(reps=12, weight=35, rpe=9, count=2)),
        now=NOW,
        reps_min=10,
    )
    assert updated.sets_count == pe.MAX_SETS_COUNT


# ===========================================================================
# D9 / Modo B: as series da FICHA continuam sendo do modelo
# ===========================================================================


def test_modo_b_a_ficha_preserva_as_series_do_modelo():
    """O teto e do ESTADO da progressao, nao da ficha (D9). No Modo B a trilha
    e so o transporte das series do modelo para `prescribe`."""
    track = fx.track(sets_count=9, load=35)  # 9 series vindas do modelo
    group = fx.group(phase=pe.F5)
    decision = pe.prescribe(track, group, fx.safety(), model_fixed_sets=True)
    assert decision.suggested_sets == 9


def test_modo_b_f5_continua_virando_recomendacao():
    """D9 (a): `blocked_by=MODEL_FIXED_SETS` e HOLD, sem alterar a ficha."""
    track = fx.track(sets_count=3, load=35)
    group = fx.group(phase=pe.F5, pending_action=pe.ADD_SET)
    decision = pe.prescribe(track, group, fx.safety(), model_fixed_sets=True)
    assert decision.decision == pe.HOLD
    assert pe.BLOCKED_MODEL_FIXED_SETS in decision.blocked_by
    assert decision.suggested_sets == 3


def test_modo_a_f5_continua_subindo_uma_serie_ate_quatro():
    """Modo A (`model_fixed_sets=False`): a F5 sobe 1 serie e para em 4."""
    group = fx.group(phase=pe.F5, pending_action=pe.ADD_SET)
    subiu = pe.prescribe(fx.track(sets_count=3, load=35), group, fx.safety(), model_fixed_sets=False)
    assert subiu.decision == pe.ADD_SET and subiu.suggested_sets == pe.MAX_SETS_COUNT

    no_teto = pe.prescribe(
        fx.track(sets_count=pe.MAX_SETS_COUNT, load=35), group, fx.safety(), model_fixed_sets=False
    )
    assert no_teto.decision == pe.HOLD and pe.BLOCKED_MAX_SETS in no_teto.blocked_by


# ===========================================================================
# persistencia: nem a leitura nem a escrita passam do teto
# ===========================================================================


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.executed: list[tuple[str, Any]] = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql: str, params: Any = None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return {"id": 1}


def _conn(rows: list[dict[str, Any]]) -> SharedCursorConn:
    return SharedCursorConn(_FakeCursor(rows))


def _row(sets_count: int) -> dict[str, Any]:
    return {
        "id": 1,
        "exercise_id": 101,
        "equipment_id": 201,
        "muscle_group_id": 11,
        "load_status": pe.ACTIVE,
        "working_load_kg": Decimal("40"),
        "sets_count": sets_count,
        "technique_code": None,
        "pending_action_applied_at": None,
        "last_load_change_at": None,
        "last_sets_change_at": None,
        "last_executed_at": None,
    }


def test_leitura_da_trilha_descongela_a_linha_antiga_sem_esperar_replay():
    """A linha antiga tem 9; o motor tem de ler 4."""
    conn = _conn([_row(9)])
    tracks = ps.load_tracks(conn, 1)
    assert tracks[pe.track_key(101, 201, "rested")].sets_count == pe.MAX_SETS_COUNT


def test_escrita_da_trilha_nunca_grava_acima_do_teto():
    conn = _conn([])
    ps.save_track(conn, 1, fx.track(exercise_id=101, equipment_id=201, sets_count=9, load=35))
    sql, params = conn.cur.executed[0]
    assert sql.startswith("UPDATE")
    assert pe.MAX_SETS_COUNT in params
    assert 9 not in params
