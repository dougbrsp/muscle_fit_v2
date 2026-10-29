"""Fase 5, PR 6 - a progressao le as tecnicas avancadas.

- `classify_sets` usa `technique_code`/`anchor_set_number`; `backoff` e trabalho;
- teto de ativacao (`activation_max_reached`) e "dentro da faixa" calculados
  com as series equivalentes REALIZADAS da janela (trabalho + fatias);
- gate da F5 (`ADD_SET`): so depois da F4 e so abaixo da faixa;
- `prescribe` preenche `advanced_technique_parameters` (e o snapshot os leva);
- leitura das entradas do teto em numero FIXO de statements.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain.prescription import build_prescription
from app.domain.training_history import SetRecord
from app.intelligence.methodology_volume import MethodologyGroupInput, MethodologyRange, RangeSource, VolumeClassSpec
from app.intelligence.progression_activation import ActivationCeiling
from app import progression_state as ps
from tests.support import progression_fixtures as fx

SUPINO, BANCO, PEITO = 1, 1, 10

COEFFICIENTS = {
    "rest_pause": {"code": "rest_pause", "activation_set_equivalent": 0.40, "max_equivalent_per_anchor": 1.0},
    "drop_set": {"code": "drop_set", "activation_set_equivalent": 0.50, "max_equivalent_per_anchor": 1.0},
}
CATALOG = {
    **fx.CATALOG,
    "rest_pause": replace(fx.CATALOG["rest_pause"], activation_set_equivalent=0.40, max_equivalent_per_anchor=1.0),
    "drop_set": replace(fx.CATALOG["drop_set"], activation_set_equivalent=0.50, max_equivalent_per_anchor=1.0),
    "fst7": replace(fx.CATALOG["fst7"], default_sets_count=7, max_equivalent_per_anchor=0.0),
}


def _set(number, set_type="working", *, reps=12, rpe=10, weight=40, code=None, anchor=None):
    return SetRecord(
        set_number=number,
        set_type=set_type,
        reps=reps,
        weight_kg=None if weight is None else Decimal(str(weight)),
        rpe=None if rpe is None else Decimal(str(rpe)),
        technique_code=code,
        anchor_set_number=anchor,
    )


# ===========================================================================
# classify_sets
# ===========================================================================


@pytest.mark.parametrize(
    "record, bucket",
    [
        (_set(1), "working"),
        (_set(1, None), "working"),  # legado sem set_type
        (_set(1, "backoff", weight=30), "backoff"),  # antes: unknown
        (_set(2, "rest_pause", reps=6, code="rest_pause", anchor=1), "technique"),
        (_set(2, "rest_pause", reps=6), "technique"),  # sem vinculo (Zepp/legado)
        (_set(2, "dropset", reps=6), "technique"),  # grafia legada
        (_set(2, "forced_rep", reps=2, code="forced_reps", anchor=1), "technique"),
        (_set(2, "working", reps=6, code="drop_set", anchor=1), "technique"),  # aplicacao gravada como working
        (_set(2, "working", code="fst7", anchor=1), "working"),  # FST-7 so preenche series
        (_set(2, "working", code="rest_pause"), "working"),  # codigo sem ancora: a propria ancora
        (_set(1, "warmup", code="rest_pause", anchor=1), "preparatory"),
        (_set(1, "feeler"), "preparatory"),
        (_set(1, "giant_set"), "unknown"),
    ],
)
def test_classify_sets_em_tabela(record, bucket):
    classification = pe.classify_sets([record])
    found = [name for name in ("working", "technique", "preparatory", "unknown", "backoff") if getattr(classification, name)]
    assert found == [bucket]


def test_backoff_e_aplicacao_nao_mexem_na_carga_base_nem_no_julgamento():
    """Backoff mais leve e aplicacao com vinculo: nada entra no minimo da
    sessao, nada gera `unknown_set_type`, a sessao qualifica."""
    sets_ = [_set(1), _set(2), _set(3), _set(4, "backoff", weight=30, reps=15), _set(5, "working", reps=6, weight=32, code="drop_set", anchor=3)]
    classification = pe.classify_sets(sets_)
    assert len(classification.working) == 3 and len(classification.work) == 4
    assert pe.session_working_load(classification.working) == Decimal("40")
    outcome = pe.evaluate_track_session(
        fx.execution(set_list=sets_), fx.track(), reps_min=10, reps_max=12, ceiling=10, failure_allowed=True
    )
    assert outcome.outcome == pe.QUALIFY
    assert pe.EVIDENCE_UNKNOWN_SET_TYPE not in outcome.reasons
    assert outcome.working_sets == 3


def test_sem_tecnica_a_classificacao_e_a_de_antes():
    """Registro sem tecnica (so working/warmup/feeler): mesmos buckets de antes."""
    sets_ = [_set(1, "warmup"), _set(2, "feeler"), _set(3), _set(4), _set(5)]
    classification = pe.classify_sets(sets_)
    assert [len(classification.preparatory), len(classification.working)] == [2, 3]
    assert classification.technique == () and classification.backoff == () and not classification.has_unknown


# ===========================================================================
# teto de ativacao / dentro da faixa (series equivalentes realizadas)
# ===========================================================================

# Macro-grupo com faixa 4-6 series por sessao na coluna 1x, sem meta semanal.
VOLUME_CLASSES = {
    1: VolumeClassSpec(
        id=1,
        name="Grandes",
        ranges={"1x": MethodologyRange(sets_min=4, sets_max=6, sets_source=RangeSource.ADMIN)},
    )
}
GROUPS = (MethodologyGroupInput(id=PEITO, name="Peito", volume_class_id=1),)


def _ceiling(catalog=COEFFICIENTS):
    return ActivationCeiling(
        contributions={SUPINO: frozenset({PEITO})},
        groups=GROUPS,
        volume_classes=VOLUME_CLASSES,
        technique_catalog=catalog,
    )


def _session(day, work=3, mini_blocks=(), activity_id=2):
    sets_ = [_set(n) for n in range(1, work + 1)]
    sets_ += [
        _set(work + 1 + index, "rest_pause", reps=reps, code="rest_pause", anchor=work)
        for index, reps in enumerate(mini_blocks)
    ]
    execution = fx.execution(exercise_id=SUPINO, equipment_id=BANCO, set_list=sets_)
    return fx.session(activity_id=activity_id, day=day, executions=[execution])


@pytest.mark.parametrize(
    "work, mini_blocks, value, in_range, reached",
    [
        (3, (), 3.0, False, False),  # abaixo da faixa
        (3, (6, 6), 4.0, True, False),  # na faixa SO por causa do equivalente (6/12 + 6/12)
        (4, (), 4.0, True, False),  # na faixa sem tecnica
        (5, (6, 6), 6.0, False, True),  # teto atingido com o equivalente
        (7, (), 7.0, False, True),  # acima da faixa sem tecnica
    ],
)
def test_teto_e_faixa_com_equivalentes(work, mini_blocks, value, in_range, reached):
    session = _session("2026-09-09", work=work, mini_blocks=mini_blocks)
    activation = _ceiling()([session], session)[PEITO]
    assert activation.direct_sets_per_session == value
    assert (activation.in_range, activation.reached) == (in_range, reached)
    assert activation.technique_equivalent == (1.0 if mini_blocks else 0.0)


def test_janela_de_7_dias_termina_na_sessao():
    """Sessao de 8 dias antes fica fora; a da vespera entra (2 sessoes -> 2x,
    sem faixa cadastrada para 2x -> nao avaliavel, nada bloqueia)."""
    old = _session("2026-09-01", work=6, activity_id=1)
    today = _session("2026-09-09", work=3)
    only_today = _ceiling()([old, today], today)[PEITO]
    assert only_today.direct_sets_per_session == 3.0
    yesterday = _session("2026-09-08", work=3, activity_id=3)
    both = _ceiling()([yesterday, today], today)[PEITO]
    assert both.status == "NOT_EVALUABLE" and not both.reached and not both.in_range


def test_sem_cadastro_de_macro_grupo_nada_muda():
    session = _session("2026-09-09", work=7)
    assert ActivationCeiling(contributions={SUPINO: frozenset({PEITO})})([session], session) == {}


def test_falha_na_leitura_nunca_derruba_a_avaliacao(monkeypatch):
    ceiling = _ceiling()

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(ActivationCeiling, "evaluate", explode)
    session = _session("2026-09-09")
    assert ceiling([session], session) == {}


# ===========================================================================
# gate da F5 (ADD_SET)
# ===========================================================================


def _run_f4(last_session, *, probe=True, phase=pe.F4):
    first = _session("2026-09-01", activity_id=1)
    sessions = [first, last_session]
    tracks = [fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)]
    group_states, track_states = fx.state_maps(tracks, [fx.group(muscle_group_id=PEITO, phase=phase)])
    inputs = fx.inputs(tracks=tracks)
    if probe:
        inputs = replace(inputs, activation_probe=_ceiling())
    return ev.evaluate_session(sessions[-1], sessions, group_states=group_states, tracks=track_states, inputs=inputs)


def test_f4_nao_aplicada_nao_chega_na_f5():
    """F3 com tecnica elegivel: o degrau e a tecnica (F4), mesmo abaixo da
    faixa e mesmo com a faixa ja atingida - o gate de volume e so da F5."""
    tracks = [fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)]
    eligible = {tracks[0].key: ("rest_pause",)}
    for in_range in (False, True):
        step = pe.next_step(
            fx.group(muscle_group_id=PEITO, phase=pe.F3),
            tracks,
            now=datetime(2026, 9, 9),
            eligible_by_track=eligible,
            volume_in_range=in_range,
        )
        assert (step.pending_action, step.phase_after) == (pe.APPLY_TECHNIQUE, pe.F4)


def test_f4_aplicada_e_abaixo_da_faixa_soma_serie():
    result = _run_f4(_session("2026-09-09", work=3))
    state = result.group_states[PEITO]
    assert state.pending_action == pe.ADD_SET
    assert result.decisions[0].evidence["activation"]["direct_sets_per_session"] == 3.0


def test_f4_aplicada_e_na_faixa_so_pelos_equivalentes_nao_soma_serie():
    """3 series de trabalho (abaixo de 4) + rest-pause 6+6 (= 1,0): o grupo
    esta na faixa POR CAUSA da tecnica -> F6, sem ADD_SET."""
    result = _run_f4(_session("2026-09-09", work=3, mini_blocks=(6, 6)))
    state = result.group_states[PEITO]
    assert state.pending_action is None and state.phase == pe.F6
    assert result.decisions[0].blocked_by == (pe.BLOCKED_VOLUME_IN_RANGE,)
    assert result.decisions[0].evidence["activation"]["technique_equivalent"] == 1.0


def test_f4_aplicada_e_teto_atingido_pelos_equivalentes_bloqueia_por_activation_max():
    result = _run_f4(_session("2026-09-09", work=5, mini_blocks=(6, 6)))
    state = result.group_states[PEITO]
    assert (state.phase, state.phase_status) == (pe.F6, pe.PHASE_BLOCKED_ACTIVATION_MAX)
    assert result.decisions[0].blocked_by == (pe.BLOCKED_ACTIVATION_MAX,)


def test_sem_leitura_do_teto_o_comportamento_e_o_de_antes():
    """Sem `activation_probe` (chamador antigo): mesma sessao da faixa por
    equivalentes vira ADD_SET, como antes do PR 6."""
    result = _run_f4(_session("2026-09-09", work=3, mini_blocks=(6, 6)), probe=False)
    assert result.group_states[PEITO].pending_action == pe.ADD_SET
    assert "activation" not in result.decisions[0].evidence


def test_f5_na_faixa_vai_para_f6():
    result = _run_f4(_session("2026-09-09", work=4), phase=pe.F5)
    state = result.group_states[PEITO]
    assert state.phase == pe.F6 and result.decisions[0].blocked_by == (pe.BLOCKED_VOLUME_IN_RANGE,)


# ===========================================================================
# advanced_technique_parameters
# ===========================================================================


def _prescribe(track, **kwargs):
    return pe.prescribe(
        track,
        # F4 efetivada: a tecnica gravada na trilha e a que a progressao prescreve
        fx.group(muscle_group_id=PEITO, phase=pe.F4, technique_applied_at=datetime(2026, 9, 1)),
        fx.safety(),
        now=datetime(2026, 9, 10),
        catalog=CATALOG,
        **kwargs,
    )


@pytest.mark.parametrize(
    "track_code, model, applications, pause, expected",
    [
        (None, None, None, None, None),
        (
            None,
            "rest_pause",
            2,
            20,
            {
                "technique_code": "rest_pause",
                "source": "model",
                "sets_only": False,
                "set_type": "rest_pause",
                "applications": 2,
                "pause_seconds": 20,
                "anchor": "LAST_WORKING_SET",
                "planned_equivalent": 0.8,
                "application_loads_kg": [40.0, 40.0],
                # Modo Intensivo: RPE-alvo do protocolo pelo teto do par (fx.safety: teto 10).
                "target_rpe": 10,
            },
        ),
        (
            "drop_set",
            None,
            None,
            None,
            {
                "technique_code": "drop_set",
                "source": "progression",
                "sets_only": False,
                "set_type": "drop_set",
                "applications": 1,
                "pause_seconds": None,
                "anchor": "LAST_WORKING_SET",
                "planned_equivalent": 0.5,
                "load_factor_per_application": 0.8,
                "application_loads_kg": [32.5],
                # Modo Intensivo: RPE-alvo do protocolo pelo teto do par (fx.safety: teto 10).
                "target_rpe": 10,
            },
        ),
        (
            None,
            "fst7",
            3,
            30,
            {
                "technique_code": "fst7",
                "source": "model",
                "sets_only": True,
                "default_sets_count": 7,
                "applications": None,
                "pause_seconds": None,
                "planned_equivalent": 0.0,
                # Modo Intensivo: RPE-alvo do protocolo pelo teto do par (fx.safety: teto 10).
                "target_rpe": 10,
            },
        ),
    ],
)
def test_parametros_da_tecnica_na_prescricao(track_code, model, applications, pause, expected):
    decision = _prescribe(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, technique_code=track_code),
        model_technique=model,
        model_technique_applications=applications,
        model_technique_duration_seconds=pause,
    )
    assert decision.advanced_technique_parameters == expected
    assert decision.to_dict()["advanced_technique_parameters"] == expected


def test_drop_set_de_varios_drops_segue_o_fator_do_modo_treino():
    """Mesmo arredondamento de `computeTechniqueApplicationLoad` (100 kg ->
    80, 65, 50)."""
    params = pe.technique_parameters("drop_set", source="model", catalog=CATALOG, applications=3, working_load_kg=Decimal("100"))
    assert params["application_loads_kg"] == [80.0, 65.0, 50.0]
    assert params["planned_equivalent"] == 1.0  # 3 x 0,5 limitado pelo teto


@pytest.mark.parametrize("pair_max, esperado", [(10, 10), (9, 9), (8, 9), (None, 9), ("lixo", 9)])
def test_target_rpe_do_protocolo_pelo_teto_do_par(pair_max, esperado):
    """Modo Intensivo: `target_rpe` = 10 só quando o par libera a falha; 9 em
    qualquer outro caso, inclusive desconhecido (dono `training_safety`)."""
    for code in ("fst7", "drop_set", "rest_pause"):
        params = pe.technique_parameters(code, source="model", catalog=CATALOG, pair_max_safe_rpe=pair_max)
        assert params["target_rpe"] == esperado


def test_carga_da_prescricao_nao_muda_com_a_tecnica():
    base = _prescribe(fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO))
    with_technique = _prescribe(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO), model_technique="rest_pause"
    )
    assert (base.suggested_load_kg, base.suggested_sets, base.decision) == (
        with_technique.suggested_load_kg,
        with_technique.suggested_sets,
        with_technique.decision,
    )


def test_snapshot_leva_os_parametros_de_forma_aditiva():
    decision = _prescribe(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO),
        model_technique="rest_pause",
        model_technique_applications=2,
    ).to_dict()
    exercise = {"sets_count": 3, "reps_target": "10-12", "advanced_technique": "rest_pause"}
    snapshot = build_prescription(exercise, safety=fx.safety(), decision=decision)
    assert snapshot["schema_version"] == 1
    assert snapshot["advanced_technique_parameters"]["applications"] == 2
    legacy = build_prescription({"sets_count": 3}, safety=fx.safety())
    assert legacy["advanced_technique_parameters"] is None
    # tecnica da ficha diferente da decisao (ex.: removida pela seguranca): nao
    # grava parametro de outra tecnica - os da linha saem pelo mesmo dono
    other = build_prescription({**exercise, "advanced_technique": "drop_set"}, safety=fx.safety(), decision=decision)
    assert other["advanced_technique_parameters"]["technique_code"] == "drop_set"
    assert other["advanced_technique_parameters"]["source"] == pe.TECHNIQUE_SOURCE_PLAN


# ===========================================================================
# statements
# ===========================================================================


class _Cursor:
    def __init__(self, log):
        self.log = log
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append(" ".join(sql.split()))
        if "FROM exercise_muscle_contributions" in sql:
            self._rows = [{"exercise_id": SUPINO, "muscle_group_id": PEITO, "role": "PRIMARY", "stimulus_weight": 1.0}]
        elif "FROM muscle_groups" in sql:
            self._rows = [
                {"id": PEITO, "name": "Peito", "size_class": "large", "methodology_parent_group_id": None, "volume_class_id": 1}
            ]
        elif "FROM muscle_volume_classes" in sql:
            self._rows = [
                {"id": 1, "name": "Grandes", "weekly_sets_min": None, "weekly_sets_max": None, "weekly_source": None, "weekly_evidence": None}
            ]
        elif "FROM muscle_volume_class_ranges" in sql:
            self._rows = [
                {
                    "class_id": 1,
                    "frequency_bucket": "1x",
                    "sets_min": 4,
                    "sets_max": 6,
                    "exercises_min": None,
                    "exercises_max": None,
                    "sets_source": "admin",
                    "sets_evidence": None,
                    "exercises_source": None,
                    "exercises_evidence": None,
                }
            ]
        elif "FROM advanced_techniques" in sql:
            self._rows = [dict(row) for row in COEFFICIENTS.values()]
        else:
            self._rows = []

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self):
        self.log: list[str] = []

    def cursor(self):
        return _Cursor(self.log)


@pytest.mark.parametrize("exercise_count", [1, 40])
def test_entradas_do_teto_em_numero_fixo_de_statements(exercise_count):
    """4 statements (contribuicoes, grupos, macro-grupos, faixas), qualquer
    que seja o numero de exercicios - nada por grupo ou exercicio."""
    conn = _Conn()
    ps.load_activation_ceiling(conn, list(range(1, exercise_count + 1)))
    assert len(conn.log) == 4


def test_catalogo_de_coeficientes_so_e_lido_com_aplicacao_de_tecnica():
    conn = _Conn()
    ceiling = ps.load_activation_ceiling(conn, [SUPINO])
    plain = _session("2026-09-09", work=3)
    ceiling([plain], plain)
    assert len(conn.log) == 4  # sem tecnica: nenhuma leitura a mais
    with_technique = _session("2026-09-10", work=3, mini_blocks=(6, 6))
    activation = ceiling([with_technique], with_technique)[PEITO]
    ceiling([with_technique], with_technique)
    assert len(conn.log) == 5  # uma leitura do catalogo, uma vez so
    assert activation.technique_equivalent == 1.0 and activation.in_range


# ===========================================================================
# rascunho do Modo B: tecnica da progressao ganha as aplicacoes do default
# ===========================================================================


def test_rascunho_usa_aplicacoes_so_da_tecnica_da_progressao():
    from types import SimpleNamespace

    from app.generation.mode_b import _progression_technique_applications

    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    by_progression = SimpleNamespace(decision=_prescribe(replace(track, technique_code="drop_set")))
    by_model = SimpleNamespace(decision=_prescribe(track, model_technique="rest_pause"))
    without = SimpleNamespace(decision=_prescribe(track))
    assert _progression_technique_applications(by_progression) == 1
    assert _progression_technique_applications(by_model) is None  # o Modelo manda
    assert _progression_technique_applications(without) is None
    assert _progression_technique_applications(None) is None
