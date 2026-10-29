"""Modo de contagem do volume - Lote 4: progressao e realizado pelo Modelo da sessao.

Regra (decisao do usuario, 2026-10-16; mapa §41 de
`docs/motores/FONTES_DA_VERDADE.md`):
- o teto de ativacao / a trava de ADD_SET e a "Ativacao 7 dias" resolvem o modo
  pela versao do Modelo que originou a sessao; sem Modelo = `primary_only`;
- a janela avaliada na data D usa UM modo, o da sessao mais recente ate D;
- sem replay: `evidence_json.activation` passa a gravar `counting_mode`;
- usuario sem Modelo ponderado: tudo bit a bit igual ao de antes.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app import progression_state as ps
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain.training_history import SetRecord
from app.intelligence import methodology_volume_service as mvs
from app.intelligence.methodology_realized import METRIC_VERSION, evaluate_realized_window
from app.intelligence.methodology_volume import (
    ContributionRow,
    MethodologyGroupInput,
    MethodologyRange,
    RangeSource,
    VolumeClassSpec,
    VolumeCountingMode,
    counting_mode_for_window,
    counting_mode_or_default,
    counts_as_frequency_session,
)
from app.intelligence.progression_activation import ActivationCeiling
from tests.support import progression_fixtures as fx

PO, PS = VolumeCountingMode.PRIMARY_ONLY, VolumeCountingMode.PRIMARY_SECONDARY
PUXADA, BANCO = 1, 1
COSTAS, BICEPS = 20, 30


def _classes(costas: tuple[int, int], biceps: tuple[int, int]) -> dict[int, VolumeClassSpec]:
    def spec(class_id: int, name: str, rng: tuple[int, int]) -> VolumeClassSpec:
        ranges = {
            bucket: MethodologyRange(sets_min=rng[0], sets_max=rng[1], sets_source=RangeSource.ADMIN)
            for bucket in ("1x", "2x")
        }
        return VolumeClassSpec(id=class_id, name=name, ranges=ranges)

    return {1: spec(1, "Grandes", costas), 2: spec(2, "Pequenos", biceps)}


# Faixas DIFERENTES por modo de proposito: prova qual cadastro foi usado.
CLASSES_PO = _classes(costas=(4, 6), biceps=(1, 3))
CLASSES_PS = _classes(costas=(2, 4), biceps=(1, 3))
GROUPS = (
    MethodologyGroupInput(id=COSTAS, name="Costas", volume_class_id=1),
    MethodologyGroupInput(id=BICEPS, name="Biceps", volume_class_id=2),
)
# Linhas CRUAS do cadastro, como `load_volume_contributions` entrega.
ROWS = {
    PUXADA: (
        ContributionRow(muscle_group_id=COSTAS, role="PRIMARY", stimulus_weight=1.0),
        ContributionRow(muscle_group_id=BICEPS, role="SECONDARY", stimulus_weight=0.5),
    )
}


def _set(number: int) -> SetRecord:
    return SetRecord(set_number=number, set_type="working", reps=12, weight_kg=Decimal("40"), rpe=Decimal("10"))


def _session(day: str, work: int, activity_id: int):
    execution = fx.execution(exercise_id=PUXADA, equipment_id=BANCO, set_list=[_set(n) for n in range(1, work + 1)])
    return fx.session(activity_id=activity_id, day=day, executions=[execution])


def _ceiling(session_modes: dict[int, VolumeCountingMode], *, by_mode=None) -> ActivationCeiling:
    by_mode = {PO: CLASSES_PO, PS: CLASSES_PS} if by_mode is None else by_mode
    return ActivationCeiling(
        contributions=ROWS,
        groups=GROUPS,
        volume_classes=by_mode.get(PO, {}),
        technique_catalog={},
        session_modes=session_modes,
        volume_classes_by_mode=by_mode,
    )


# ---------------------------------------------------------------------------
# Motor: regras novas, puras
# ---------------------------------------------------------------------------


def test_modo_da_janela_e_o_da_sessao_mais_recente():
    assert counting_mode_for_window([]) is PO
    assert counting_mode_for_window([PS, None]) is PO  # a mais recente nao tem Modelo
    assert counting_mode_for_window([None, PO, "primary_secondary"]) is PS
    assert counting_mode_or_default(None) is PO and counting_mode_or_default("") is PO
    with pytest.raises(ValueError):
        counting_mode_or_default("meias_series")


@pytest.mark.parametrize(
    "has_primary, secondary, counts",
    [(True, 0.0, True), (False, 0.0, False), (False, 0.5, False), (False, 0.999, True), (False, 1.0, True)],
)
def test_sessao_conta_na_frequencia_com_primary_ou_uma_serie_de_secundario(has_primary, secondary, counts):
    assert counts_as_frequency_session(has_primary, secondary) is counts


# ---------------------------------------------------------------------------
# Teto de ativacao pelo Modelo da sessao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("work", [2, 3, 4, 5, 7])
def test_paridade_bit_a_bit_sem_modelo_ponderado(work):
    """Usuario sem Modelo ponderado: a leitura nova (linhas cruas com
    SECONDARY, modo por sessao, faixas por modo) e IDENTICA a de antes do
    Lote 4 (conjunto de grupos PRIMARY, cadastro unico)."""
    yesterday, today = _session("2026-10-14", work, 1), _session("2026-10-15", work, 2)
    antes = ActivationCeiling(
        contributions={PUXADA: frozenset({COSTAS})}, groups=GROUPS, volume_classes=CLASSES_PO, technique_catalog={}
    )
    for session_modes in ({}, {1: PO, 2: PO}):
        depois = _ceiling(session_modes)
        for window in ([today], [yesterday, today]):
            assert depois(window, today) == antes(window, today)
    evidence = _ceiling({})([today], today)[COSTAS].to_evidence()
    assert evidence["counting_mode"] == "primary_only"


def test_sessao_de_modelo_ponderado_usa_pesos_e_faixas_do_modo():
    today = _session("2026-10-15", 4, 2)
    so_primario = _ceiling({2: PO})([today], today)
    ponderado = _ceiling({2: PS})([today], today)
    # Costas: 4 series; faixa 4-6 (primary_only) -> dentro; 2-4 (ponderado) -> teto.
    assert (so_primario[COSTAS].in_range, so_primario[COSTAS].reached) == (True, False)
    assert (ponderado[COSTAS].in_range, ponderado[COSTAS].reached) == (False, True)
    # Biceps: 4 x 0,5 = 2 series so no ponderado (>= 1 serie: conta a sessao).
    assert ponderado[BICEPS].direct_sets_per_session == 2.0 and ponderado[BICEPS].in_range
    assert so_primario.get(BICEPS) is None or so_primario[BICEPS].direct_sets_per_session is None
    assert ponderado[BICEPS].to_evidence()["counting_mode"] == "primary_secondary"


def test_troca_de_modo_no_meio_da_janela_usa_o_modo_da_sessao_avaliada():
    """Uma regua so por janela: a da sessao mais recente ate D (a avaliada)."""
    yesterday, today = _session("2026-10-14", 2, 1), _session("2026-10-15", 2, 2)
    # Ontem ponderado, hoje primary_only: o biceps de ontem NAO conta hoje.
    hoje_po = _ceiling({1: PS, 2: PO})([yesterday, today], today)
    assert hoje_po.get(BICEPS) is None or hoje_po[BICEPS].direct_sets_per_session is None
    assert hoje_po[COSTAS].counting_mode is PO and hoje_po[COSTAS].direct_sets_per_session == 2.0
    # Ontem primary_only, hoje ponderado: as duas sessoes contam o biceps.
    hoje_ps = _ceiling({1: PO, 2: PS})([yesterday, today], today)
    assert hoje_ps[BICEPS].direct_sets_per_session == 1.0 and hoje_ps[BICEPS].counting_mode is PS
    # A sessao de ontem, avaliada no dia dela, usa o modo dela.
    ontem = _ceiling({1: PS, 2: PO})([yesterday], yesterday)
    assert ontem[BICEPS].direct_sets_per_session == 1.0 and ontem[COSTAS].counting_mode is PS


def test_sessao_sem_modelo_e_primary_only():
    today = _session("2026-10-15", 4, 99)
    ceiling = _ceiling({1: PS})  # 99 fora do mapa: treino livre / ficha sem Modelo
    assert ceiling.mode_for([today], today) is PO
    assert ceiling([today], today)[COSTAS].counting_mode is PO


def test_modo_sem_cadastro_nao_avalia_nunca_usa_a_faixa_de_outro_modo():
    today = _session("2026-10-15", 4, 2)
    assert _ceiling({2: PS}, by_mode={PO: CLASSES_PO})([today], today) == {}


def _run_f4(mode: VolumeCountingMode, work: int):
    first, last = _session("2026-10-01", work, 1), _session("2026-10-15", work, 2)
    tracks = [fx.track(exercise_id=PUXADA, equipment_id=BANCO, muscle_group_id=COSTAS)]
    group_states, track_states = fx.state_maps(tracks, [fx.group(muscle_group_id=COSTAS, phase=pe.F4)])
    inputs = replace(fx.inputs(tracks=tracks), activation_probe=_ceiling({1: mode, 2: mode}))
    return ev.evaluate_session(last, [first, last], group_states=group_states, tracks=track_states, inputs=inputs)


def test_trava_de_add_set_e_evidencia_seguem_o_modo_da_sessao():
    """3 series de costas: abaixo de 4-6 (primary_only) -> ADD_SET; dentro de
    2-4 (ponderado) -> F6 com `BLOCKED_VOLUME_IN_RANGE`. A evidencia grava a
    regua usada."""
    so_primario = _run_f4(PO, 3)
    assert so_primario.group_states[COSTAS].pending_action == pe.ADD_SET
    assert so_primario.decisions[0].evidence["activation"]["counting_mode"] == "primary_only"
    ponderado = _run_f4(PS, 3)
    assert ponderado.decisions[0].blocked_by == (pe.BLOCKED_VOLUME_IN_RANGE,)
    assert ponderado.decisions[0].evidence["activation"]["counting_mode"] == "primary_secondary"


# ---------------------------------------------------------------------------
# Leituras: uma consulta por janela, sem escrita
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, log, modes):
        self.log, self.modes, self._rows = log, modes, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))
        if "FROM activities a" in sql:
            self._rows = [
                {"activity_id": aid, "volume_counting_mode": mode} for aid, mode in self.modes.items() if aid in (params or ())
            ]
        elif "FROM exercise_muscle_contributions" in sql:
            self._rows = [
                {"exercise_id": PUXADA, "muscle_group_id": COSTAS, "role": "PRIMARY", "stimulus_weight": 1.0},
                {"exercise_id": PUXADA, "muscle_group_id": BICEPS, "role": "SECONDARY", "stimulus_weight": 0.5},
            ]
        else:
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, modes=None):
        self.log: list = []
        self.modes = modes or {}

    def cursor(self):
        return _Cursor(self.log, self.modes)


def test_modo_por_sessao_em_uma_consulta_pela_versao_da_ficha():
    conn = _Conn({1: "primary_secondary", 2: None})
    with conn.cursor() as cur:
        modes = mvs.load_session_counting_modes(cur, [3, 2, 1, 2])
        assert mvs.load_session_counting_modes(cur, []) == {}
    assert modes == {1: PS, 2: PO, 3: PO}  # 3: sem linha (sem ficha/Modelo)
    (sql, params), = conn.log
    assert params == (1, 2, 3)
    # versao da ficha pela regra unica dos leitores por data (066 ou a vigente no fim do plan_date)
    assert "COALESCE(wp.template_version_id" in sql and "LEFT JOIN workout_plans wp" in sql


@pytest.mark.parametrize("sessions_modes, statements", [({1: None, 2: "primary_only"}, 5), ({1: None, 2: "primary_secondary"}, 7)])
def test_teto_em_numero_fixo_de_statements_e_so_leitura(sessions_modes, statements):
    """Modo (1) + contribuicoes (1) + grupos (1) + 2 por modo presente. Sem
    sessao ponderada: as leituras de antes + a do modo, com as mesmas
    contribuicoes (so PRIMARY). Nenhuma escrita: decisao antiga nao muda."""
    conn = _Conn(sessions_modes)
    sessions = [_session("2026-10-14", 3, 1), _session("2026-10-15", 3, 2)]
    ceiling = ps.load_activation_ceiling(conn, [PUXADA], sessions)
    assert len(conn.log) == statements
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in conn.log)
    (contrib_params,) = [params for sql, params in conn.log if "exercise_muscle_contributions" in sql]
    roles = set(contrib_params[1:])
    assert roles == ({"PRIMARY"} if statements == 5 else {"PRIMARY", "SECONDARY"})
    assert set(ceiling.volume_classes_by_mode) == ({PO} if statements == 5 else {PO, PS})


def test_sem_sessoes_as_leituras_sao_as_de_antes():
    conn = _Conn()
    ps.load_activation_ceiling(conn, [PUXADA])
    assert len(conn.log) == 4


def test_modo_da_janela_do_realizado_e_o_da_sessao_mais_recente_ate_o_dia():
    conn = _Conn()
    with conn.cursor() as cur:
        assert mvs.load_counting_mode_at(cur, 7, "2026-10-15") is PO  # nenhuma sessao
    (sql, params), = conn.log
    assert params == (7, "2026-10-15")
    # a sessao e escolhida antes do JOIN com ficha/versao (subconsulta so em activities)
    assert "WHERE a.id = (SELECT s.id FROM activities s WHERE s.user_id = %s AND s.local_date <= %s" in sql
    assert sql.endswith("ORDER BY s.local_date DESC, COALESCE(s.ended_at, s.started_at, s.created_at) DESC, s.id DESC LIMIT 1)")


# ---------------------------------------------------------------------------
# Realizado ("Ativacao 7 dias")
# ---------------------------------------------------------------------------

DAYS = [f"2026-10-{day:02d}" for day in range(9, 16)]


def _act(day: int, group: str, gid: int, direct: float, secondary: float | None = None):
    item = {"local_date": DAYS[day], "muscle_group": group, "muscle_group_id": gid, "sets_count": direct, "direct_sets": direct}
    if secondary is not None:
        item["direct_secondary_sets"] = secondary
    return item


def test_realizado_ponderado_dia_so_com_fracao_menor_que_uma_serie_nao_conta():
    items = [
        _act(0, "Biceps", BICEPS, 0.5, 0.5),  # so fracao: soma na semana, nao conta o dia
        _act(3, "Biceps", BICEPS, 1.0, 1.0),  # fracao de 1 serie: conta o dia
        _act(5, "Biceps", BICEPS, 3.5, 0.5),  # PRIMARY + fracao
    ]
    (biceps,) = [row for row in evaluate_realized_window(items, DAYS, GROUPS, CLASSES_PS) if row["muscle_group_id"] == BICEPS]
    assert biceps["days_trained"] == 2 and biceps["frequency_bucket"] == "2x"
    assert biceps["weekly_total"] == 5.0
    assert biceps["direct_sets_per_session"] == 2.5
    assert [d["status"] for d in biceps["days"]][0] is None
    assert biceps["days"][3]["status"] == "WITHIN_RANGE"


def test_realizado_primary_only_igual_ao_de_antes():
    """Sem `direct_secondary_sets` (primary_only): dia com serie direta conta."""
    items = [_act(0, "Biceps", BICEPS, 2.0), _act(2, "Biceps", BICEPS, 0.0)]
    (biceps,) = [row for row in evaluate_realized_window(items, DAYS, GROUPS, CLASSES_PO) if row["muscle_group_id"] == BICEPS]
    assert biceps["days_trained"] == 1 and biceps["days"][2]["status"] is None


def test_endpoint_do_realizado_usa_o_modo_resolvido_e_o_expoe(monkeypatch):
    import datetime
    from contextlib import contextmanager

    from app import analytics, db
    from app.intelligence import volume_service
    from app.security import Identity

    pedidos: dict = {}

    @contextmanager
    def transacao():
        yield _Conn()

    monkeypatch.setattr(db, "transaction", transacao)
    monkeypatch.setattr(mvs, "load_counting_mode_at", lambda cur, user_id, day: PS)
    monkeypatch.setattr(mvs, "load_methodology_groups", lambda cur: GROUPS)
    monkeypatch.setattr(mvs, "load_volume_classes", lambda cur, mode=PO: pedidos.setdefault("classes", mode) and CLASSES_PS)

    def diaria(self, **kwargs):
        pedidos["diaria"] = kwargs["counting_mode"]
        return []

    monkeypatch.setattr(volume_service.VolumeService, "get_daily_muscle_activation", diaria)
    monkeypatch.setattr(volume_service.VolumeService, "_user_today", lambda self, user_id: datetime.date(2026, 10, 15))
    corpo = analytics.methodology_realized(Identity(user_id=1, role="regular", source="session"))
    assert corpo["counting_mode"] == "primary_secondary" and corpo["metric_version"] == METRIC_VERSION
    assert pedidos == {"classes": PS, "diaria": PS}


# ---------------------------------------------------------------------------
# Fatia + pai: planejado e realizado na MESMA regua (juncao por bloco do motor)
# ---------------------------------------------------------------------------

PAI, FATIA, REMADA = 40, 41, 3
GROUPS_BLOCO = (
    MethodologyGroupInput(id=PAI, name="Costas", volume_class_id=1),
    MethodologyGroupInput(id=FATIA, name="Dorsal alto", volume_class_id=1, methodology_parent_group_id=PAI),
)
# Como no cadastro real (exercicios 38/81/110 no DEV): PRIMARY na fatia e
# SECONDARY no pai do MESMO bloco.
CONTRIB_BLOCO = [
    {"muscle_group_id": FATIA, "muscle_group_name": "Dorsal alto", "role": "PRIMARY", "stimulus_weight": 1.0},
    {"muscle_group_id": PAI, "muscle_group_name": "Costas", "role": "SECONDARY", "stimulus_weight": 0.5},
]


def test_juncao_por_membro_soma_o_mesmo_que_o_bloco_do_motor():
    from app.intelligence.methodology_volume import block_contributions, block_contributions_by_member

    rows = [ContributionRow(c["muscle_group_id"], c["role"], c["stimulus_weight"]) for c in CONTRIB_BLOCO]
    block_of = {PAI: PAI, FATIA: PAI}
    por_membro = block_contributions_by_member(rows, PS, block_of)
    assert set(por_membro) == {FATIA}  # o membro de maior peso leva o bloco
    assert por_membro[FATIA] == block_contributions(rows, PS, block_of)[PAI]
    assert por_membro[FATIA].weight == 1.0 and por_membro[FATIA].is_primary


def _diaria(monkeypatch, modo, block_of):
    from app.intelligence import muscle_contribution_resolver as resolver
    from app.intelligence.volume_service import VolumeService

    linhas = [
        {"exercise_id": 1, "mapping_id": REMADA, "sets_count": 4, "local_date": DAYS[6], "had_rpe_9_10": 0, "technique_rows": 0}
    ]
    monkeypatch.setattr(resolver, "_resolve_muscle_contributions_for_mappings", lambda ids: {REMADA: CONTRIB_BLOCO})
    service = VolumeService()
    service._technique_catalog = {}
    service.fetch_all = lambda sql, params=(): [dict(row) for row in linhas]
    return service.get_daily_muscle_activation(
        start_date=DAYS[0], end_date=DAYS[6], user_id=1, counting_mode=modo, block_of=block_of
    )


def test_realizado_ponderado_conta_fatia_mais_pai_como_o_planejado(monkeypatch):
    from app.intelligence.methodology_realized import block_map
    from app.intelligence.methodology_volume import MethodologyItemInput, MethodologySlotInput, evaluate_methodology_volume

    classes = _classes(costas=(2, 6), biceps=(1, 3))
    planejado = evaluate_methodology_volume(
        (MethodologySlotInput(sequence=1, items=(MethodologyItemInput(mapping_id=REMADA, canonical_exercise_id=REMADA, sets_count=4),)),),
        {REMADA: tuple(ContributionRow(c["muscle_group_id"], c["role"], c["stimulus_weight"]) for c in CONTRIB_BLOCO)},
        GROUPS_BLOCO,
        "equipment_series",
        volume_classes=classes,
        counting_mode=PS,
    )
    (bloco_planejado,) = [g for g in planejado.groups if g.muscle_group_id == PAI]

    diaria = _diaria(monkeypatch, PS, block_map(GROUPS_BLOCO))
    (bloco_realizado,) = [r for r in evaluate_realized_window(diaria, DAYS, GROUPS_BLOCO, classes) if r["muscle_group_id"] == PAI]
    # 4 series da remada: 4 x max(1,0; 0,5) = 4 no bloco, nunca 4 + 2.
    assert bloco_realizado["weekly_total"] == bloco_planejado.direct_sets_cycle_total == 4
    assert bloco_realizado["days_trained"] == 1


def test_realizado_primary_only_nao_muda_com_o_mapa_de_blocos(monkeypatch):
    """Em `primary_only` o mapa de blocos e ignorado: juncao por grupo, linha
    bit a bit a de antes do Lote 4 (paridade)."""
    com_mapa = _diaria(monkeypatch, PO, {PAI: PAI, FATIA: PAI})
    sem_mapa = _diaria(monkeypatch, PO, None)
    assert com_mapa == sem_mapa
    assert {r["muscle_group"]: r["direct_sets"] for r in sem_mapa} == {"Dorsal alto": 4.0, "Costas": 0.0}
