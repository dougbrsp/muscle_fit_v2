"""Meta vinda do PROPRIO Modelo quando ele e por equipamento
(`volume_mode = 'equipment_series'`, `target_source = template_plan`).

Defeito observado em producao (2026-09-24): um usuario com Modelo
`equipment_series` via todos os grupos com meta generica (6/9/27) e qualidade
LOW em `/dashboard/musculos`. Esse tipo de Modelo nao tem Meta de Ativacao
porque a propria especificacao dele - exercicio a exercicio, series fixas - e
a meta. O que este arquivo prova, sem banco:

- a meta semanal de cada grupo = ativacao planejada do ciclo x 7 /
  `cycle_length_days`, com o ciclo vindo do numero de slots - um slot = um
  dia, descanso incluso (8 slots = 8 dias; nada de 7 fixo);
- `min`/`max` sao `None` (um plano fixo nao tem faixa), nunca zero;
- o status tem TRES estados, com a MESMA tolerancia de
  `classify_weekly_volume_status`;
- grupo que o Modelo nao trabalha entra com alvo 0 e a limitacao "fora do
  Modelo" - realizado nele e trabalho extra, nao deficit;
- Modelo `percent_budget` continua produzindo EXATAMENTE o mesmo payload de
  antes; `training_type_id` explicito continua vencendo;
- quem ja resolveu o plano (visao geral) nao dispara as consultas de novo.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.intelligence.dashboard import metrics_registry as reg
from app.intelligence.dashboard import muscle_summary_service as mss
from app.intelligence.dashboard import overview_service as ov
from app.intelligence.dashboard.contracts import (
    DataQuality,
    MuscleDrilldownResponse,
    MusclesResponse,
    PriorityMusclesBlockOut,
    TargetSource,
    WindowInfo,
    WindowKind,
)
from app.intelligence.dashboard.muscle_summary_service import (
    OUTSIDE_TEMPLATE_PLAN_LIMITATION,
    TemplatePlan,
    build_muscle_summary,
    item_data_quality,
    template_plan_weekly_targets,
)
from app.intelligence.volume_service import (
    ON_TARGET_TOLERANCE_PCT,
    PLANNED_VOLUME_STATUSES,
    classify_planned_volume_status,
)

PEITO, TRICEPS, COSTAS, PERNAS = 1, 2, 3, 4
NOMES = {PEITO: "Peitoral", TRICEPS: "Triceps", COSTAS: "Costas", PERNAS: "Pernas"}


def _window(days: int = 28) -> WindowInfo:
    end = date(2026, 9, 22)
    return WindowInfo(
        kind=WindowKind.ROLLING_28D,
        date_from=end - timedelta(days=days - 1),
        date_to=end,
        timezone="America/Sao_Paulo",
        days=days,
    )


# ---------------------------------------------------------------------------
# 1) Normalizacao: ciclo -> semana
# ---------------------------------------------------------------------------


def test_meta_semanal_e_ativacao_do_ciclo_normalizada_pelo_tamanho_do_ciclo():
    """Normalizacao pura: 16 series efetivas num ciclo de 56 dias -> 2/semana.
    Nao e `/ 7` nem `/ 8` hardcoded: e `x 7 / ciclo`."""
    weekly = template_plan_weekly_targets({PEITO: 16.0, COSTAS: 8.0}, cycle_length_days=56)
    assert weekly == {PEITO: pytest.approx(2.0), COSTAS: pytest.approx(1.0)}


def test_meta_semanal_de_ciclo_de_tres_slots():
    weekly = template_plan_weekly_targets({PEITO: 9.0}, cycle_length_days=21)
    assert weekly[PEITO] == pytest.approx(3.0)


def test_grupo_com_ativacao_zero_fica_fora_das_metas():
    weekly = template_plan_weekly_targets({PEITO: 6.0, PERNAS: 0.0}, cycle_length_days=21)
    assert PERNAS not in weekly


def test_ciclo_invalido_nao_inventa_meta():
    assert template_plan_weekly_targets({PEITO: 6.0}, cycle_length_days=0) == {}


def test_template_plan_diz_se_o_grupo_esta_no_modelo():
    plano = TemplatePlan(
        template_id=7,
        template_name="Equipamentos A/B",
        slots_count=2,
        cycle_length_days=14,  # valor explicito: aqui so importa a normalizacao
        activation_by_group={PEITO: 6.0, PERNAS: 0.0},
    )
    assert plano.contains(PEITO)
    assert not plano.contains(PERNAS)
    assert not plano.contains(COSTAS)
    assert plano.weekly_targets == {PEITO: pytest.approx(3.0)}


# ---------------------------------------------------------------------------
# 2) Tres estados, mesma tolerancia
# ---------------------------------------------------------------------------


def test_status_planejado_usa_a_mesma_tolerancia_do_registry():
    planejado = 10.0
    folga = planejado * ON_TARGET_TOLERANCE_PCT
    assert classify_planned_volume_status(planejado - folga - 0.01, planejado) == "BELOW_TARGET"
    assert classify_planned_volume_status(planejado - folga, planejado) == "ON_TARGET"
    assert classify_planned_volume_status(planejado, planejado) == "ON_TARGET"
    assert classify_planned_volume_status(planejado + folga, planejado) == "ON_TARGET"
    assert classify_planned_volume_status(planejado + folga + 0.01, planejado) == "ABOVE_TARGET"


def test_status_planejado_zero_nunca_e_deficit():
    """Grupo fora do Modelo: zero realizado e "no planejado"; qualquer
    realizado e trabalho EXTRA (acima), nunca abaixo."""
    assert classify_planned_volume_status(0.0, 0.0) == "ON_TARGET"
    assert classify_planned_volume_status(0.5, 0.0) == "ABOVE_TARGET"


@pytest.mark.parametrize("realizado,planejado", [(0.0, 0.0), (3.0, 10.0), (10.0, 10.0), (30.0, 10.0)])
def test_status_planejado_sempre_dentro_da_lista_registrada(realizado, planejado):
    assert classify_planned_volume_status(realizado, planejado) in reg.PLANNED_VOLUME_STATUS_CODES


def test_registry_reexporta_os_tres_estados_com_rotulo():
    assert reg.PLANNED_VOLUME_STATUS_CODES is PLANNED_VOLUME_STATUSES
    assert set(reg.PLANNED_VOLUME_STATUS_LABELS) == set(reg.PLANNED_VOLUME_STATUS_CODES)
    # `BETWEEN_*` nao existe sem min/max - e uma lista PROPRIA, nao a de 5.
    assert set(reg.PLANNED_VOLUME_STATUS_CODES) & set(reg.WEEKLY_VOLUME_STATUS_CODES) == {"ON_TARGET"}


# ---------------------------------------------------------------------------
# 3) Qualidade do item
# ---------------------------------------------------------------------------


def test_meta_do_modelo_mantem_qualidade_alta():
    quality, limitations = item_data_quality(
        target_source=TargetSource.TEMPLATE_PLAN, direct_sets=4.0, unattributed_sets_in_window=0
    )
    assert quality is DataQuality.HIGH
    assert limitations == []


def test_grupo_fora_do_modelo_declara_limitacao_sem_derrubar_qualidade():
    quality, limitations = item_data_quality(
        target_source=TargetSource.TEMPLATE_PLAN,
        direct_sets=4.0,
        unattributed_sets_in_window=0,
        outside_template_plan=True,
    )
    assert quality is DataQuality.HIGH
    assert limitations == [OUTSIDE_TEMPLATE_PLAN_LIMITATION]


def test_fora_do_modelo_so_faz_sentido_para_template_plan():
    _, limitations = item_data_quality(
        target_source=TargetSource.ASSIGNED_TEMPLATE,
        direct_sets=4.0,
        unattributed_sets_in_window=0,
        outside_template_plan=True,
    )
    assert OUTSIDE_TEMPLATE_PLAN_LIMITATION not in limitations


# ---------------------------------------------------------------------------
# 4) `build_muscle_summary` com conexao falsa (dispatch por SQL)
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self._conn.executed.append((sql, params))
        self._rows = self._conn.dispatch(sql)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """Devolve linhas por consulta, identificando cada SQL do servico por um
    trecho que so ela contem."""

    def __init__(self, *, head=None, activation=None, roles=None, sessions=None):
        self.head = head or []
        self.activation = activation or []
        self.roles = roles or []
        self.sessions = sessions or []
        self.executed: list[tuple[str, tuple]] = []

    def cursor(self):
        return _Cursor(self)

    def dispatch(self, sql: str):
        if "FROM workout_plan_template_assignments" in sql:
            return self.head
        if "JOIN workout_plan_template_slot_items" in sql:
            return self.activation
        if sql.strip() == "SELECT id, name FROM muscle_groups":
            return [{"id": gid, "name": nome} for gid, nome in NOMES.items()]
        if "emc.id IS NULL" in sql:
            return []
        if "AS has_direct" in sql:
            return self.sessions
        if "AS last_local_date" in sql:
            return []
        if "FROM exercise_catalog" in sql:
            return []
        if "AS set_id" in sql:
            return []
        if "AS rpe_n" in sql:
            return self.roles
        raise AssertionError(f"SQL inesperada no teste: {sql[:80]}")


def _role_row(group_id: int, role: str, sets: float, stimulus: float) -> dict:
    return {
        "muscle_group_id": group_id,
        "role": role,
        "sets_count": sets,
        "stimulus_sets": stimulus,
        "fatigue_sets": stimulus,
        "rpe_sum": 0,
        "rpe_n": 0,
    }


def _session_row(group_id: int, activity_id: int, sets: int, stimulus: float) -> dict:
    return {
        "muscle_group_id": group_id,
        "activity_id": activity_id,
        "activity_name": "Treino",
        "local_date": date(2026, 9, 20),
        "trained_at": datetime(2026, 9, 20, 19, 0),
        "physical_sets": sets,
        "stimulus_sets": stimulus,
        "has_direct": 1,
    }


HEAD_EQUIPAMENTO = [{"template_id": 7, "template_name": "Equipamentos A/B", "volume_mode": "equipment_series", "slots_count": 2}]
HEAD_PERCENTUAL = [{"template_id": 8, "template_name": "Percentual", "volume_mode": "percent_budget", "slots_count": 3}]
# Ciclo de 2 slots = 2 dias (um slot por dia). 4 series efetivas de peito no
# ciclo -> 14/sem; 1 de triceps -> 3,5/sem. Pernas nao aparece: fora do Modelo.
ATIVACAO = [
    {"slot_sequence": 1, "muscle_group_id": PEITO, "planned_sets": 4.0},
    {"slot_sequence": 2, "muscle_group_id": TRICEPS, "planned_sets": 1.0},
]


@pytest.fixture
def sem_blocos_opcionais(monkeypatch):
    """Planejado (fichas) e projetado (ciclo) sao blocos com I/O proprio,
    irrelevantes para a META: zerados aqui."""
    monkeypatch.setattr(mss, "_planned_by_group", lambda user_id, window: {})
    monkeypatch.setattr(mss, "_projected_by_group", lambda user_id, template_plan=None, targets_by_group=None: ({}, 0, None))


def _resumo(conn, **kwargs):
    return build_muscle_summary(conn, user_id=42, window=_window(28), include_readiness=False, **kwargs)


def test_modelo_por_equipamento_da_a_meta_de_cada_grupo(sem_blocos_opcionais):
    """28 dias de janela: 56 series efetivas de peito = 14/sem = exatamente o
    planejado (ON_TARGET); 4 de triceps = 1/sem contra 3,5 (BELOW_TARGET);
    pernas fora do Modelo com 8 series = trabalho extra (ABOVE_TARGET + limitacao)."""
    conn = _Conn(
        head=HEAD_EQUIPAMENTO,
        activation=ATIVACAO,
        roles=[
            _role_row(PEITO, "PRIMARY", 56, 56.0),
            _role_row(TRICEPS, "PRIMARY", 8, 4.0),
            _role_row(PERNAS, "PRIMARY", 8, 8.0),
        ],
        sessions=[_session_row(PEITO, 1, 56, 56.0), _session_row(TRICEPS, 1, 8, 4.0), _session_row(PERNAS, 2, 8, 8.0)],
    )
    payload = _resumo(conn)
    MusclesResponse.model_validate(payload)

    assert payload["target_source"] is TargetSource.TEMPLATE_PLAN
    assert payload["training_type_id"] is None
    assert payload["template_id"] == 7 and payload["template_name"] == "Equipamentos A/B"
    assert payload["data_quality"] is DataQuality.HIGH
    assert any("ciclo de 2 dias" in limitacao for limitacao in payload["limitations"])

    itens = {item["muscle_group_id"]: item for item in payload["items"]}

    peito = itens[PEITO]
    assert peito["targets"]["source"] is TargetSource.TEMPLATE_PLAN
    assert peito["targets"]["target"]["value"] == pytest.approx(14.0)
    assert peito["targets"]["min"]["value"] is None
    assert peito["targets"]["max"]["value"] is None
    assert peito["targets"]["training_type_id"] is None
    assert peito["status"] == "ON_TARGET"
    assert peito["status_basis"] == "realized_only"
    assert peito["data_quality"] is DataQuality.HIGH
    assert OUTSIDE_TEMPLATE_PLAN_LIMITATION not in peito["limitations"]

    triceps = itens[TRICEPS]
    assert triceps["targets"]["target"]["value"] == pytest.approx(3.5)
    assert triceps["status"] == "BELOW_TARGET"

    pernas = itens[PERNAS]
    assert pernas["targets"]["target"]["value"] == 0.0
    assert pernas["targets"]["min"]["value"] is None
    assert pernas["status"] == "ABOVE_TARGET"
    assert OUTSIDE_TEMPLATE_PLAN_LIMITATION in pernas["limitations"]
    assert pernas["data_quality"] is DataQuality.HIGH


def test_grupo_do_modelo_sem_nenhuma_serie_aparece_abaixo_do_planejado(sem_blocos_opcionais):
    """"Nao treinei nada" e a resposta mais importante da tela: o grupo que
    o Modelo trabalha entra mesmo sem serie, abaixo do planejado."""
    conn = _Conn(head=HEAD_EQUIPAMENTO, activation=ATIVACAO)
    payload = _resumo(conn)
    itens = {item["muscle_group_id"]: item for item in payload["items"]}
    assert set(itens) == {PEITO, TRICEPS}
    assert itens[PEITO]["status"] == "BELOW_TARGET"
    assert itens[PEITO]["realized"]["weekly_effective_sets"]["value"] == 0.0


def test_modelo_percentual_produz_o_mesmo_payload_de_antes(sem_blocos_opcionais):
    """Nenhum numero muda para `percent_budget`: o payload e IDENTICO ao de
    uma leitura sem resolucao de plano (comportamento anterior)."""
    linhas = dict(roles=[_role_row(PEITO, "PRIMARY", 20, 20.0)], sessions=[_session_row(PEITO, 1, 20, 20.0)])
    com_modelo_percentual = _resumo(_Conn(head=HEAD_PERCENTUAL, activation=ATIVACAO, **linhas))
    sem_resolucao = _resumo(_Conn(head=[], activation=[], **linhas), template_plan=None, template_plan_resolved=True)

    assert com_modelo_percentual == sem_resolucao
    assert com_modelo_percentual["target_source"] is TargetSource.FALLBACK_GENERIC
    assert com_modelo_percentual["template_id"] is None
    peito = com_modelo_percentual["items"][0]
    assert peito["targets"]["min"]["value"] == 6.0 and peito["targets"]["max"]["value"] == 27.0
    assert peito["status"] in reg.WEEKLY_VOLUME_STATUS_CODES


def test_modelo_percentual_nao_le_a_ativacao(sem_blocos_opcionais):
    conn = _Conn(head=HEAD_PERCENTUAL, activation=ATIVACAO)
    _resumo(conn)
    assert not any("JOIN workout_plan_template_slot_items" in sql for sql, _ in conn.executed)


def test_meta_explicita_continua_vencendo_o_modelo(sem_blocos_opcionais):
    conn = _Conn(head=HEAD_EQUIPAMENTO, activation=ATIVACAO)
    payload = _resumo(
        conn,
        training_type_id=99,
        training_type_source=TargetSource.EXPLICIT,
        targets_by_group={PEITO: {"min": 8.0, "target": 12.0, "max": 16.0}},
    )
    assert payload["target_source"] is TargetSource.EXPLICIT
    assert payload["training_type_id"] == 99
    assert not any("FROM workout_plan_template_assignments" in sql for sql, _ in conn.executed)


def test_plano_ja_resolvido_nao_consulta_o_modelo_de_novo(sem_blocos_opcionais):
    """A visao geral chama o servico tres vezes na mesma requisicao: resolve
    o plano uma vez e passa adiante - zero consulta de Modelo aqui."""
    plano = TemplatePlan(template_id=7, template_name="X", slots_count=2, cycle_length_days=14, activation_by_group={PEITO: 28.0})
    conn = _Conn(head=[], activation=[])
    payload = _resumo(conn, status_only=True, targets_by_group={}, template_plan=plano, template_plan_resolved=True)
    assert payload["target_source"] is TargetSource.TEMPLATE_PLAN
    assert payload["items"][0]["targets"]["target"]["value"] == pytest.approx(14.0)
    assert not any("workout_plan_template" in sql for sql, _ in conn.executed)


def test_falha_na_leitura_do_modelo_cai_na_cadeia_antiga(sem_blocos_opcionais):
    class _ConnQueQuebraNoModelo(_Conn):
        def dispatch(self, sql):
            if "FROM workout_plan_template_assignments" in sql:
                raise RuntimeError("tabela indisponivel")
            return super().dispatch(sql)

    payload = _resumo(_ConnQueQuebraNoModelo(roles=[_role_row(PEITO, "PRIMARY", 20, 20.0)]))
    assert payload["target_source"] is TargetSource.FALLBACK_GENERIC


def test_drilldown_de_grupo_fora_do_modelo_sem_serie_e_alvo_zero(sem_blocos_opcionais):
    conn = _Conn(head=HEAD_EQUIPAMENTO, activation=ATIVACAO)
    payload = mss.build_muscle_drilldown(conn, PERNAS, user_id=42, window=_window(28), include_readiness=False)
    MuscleDrilldownResponse.model_validate(payload)
    item = payload["item"]
    assert payload["target_source"] is TargetSource.TEMPLATE_PLAN
    assert payload["template_id"] == 7
    assert item["targets"]["source"] is TargetSource.TEMPLATE_PLAN
    assert item["targets"]["target"]["value"] == 0.0
    assert item["targets"]["min"]["value"] is None and item["targets"]["max"]["value"] is None
    assert item["status"] == "ON_TARGET"
    assert OUTSIDE_TEMPLATE_PLAN_LIMITATION in item["limitations"]


# ---------------------------------------------------------------------------
# 5) Visao geral: bloco de musculos prioritarios tolera min/max nulos
# ---------------------------------------------------------------------------


def _m(value, nature="DERIVED"):
    return {"value": value, "unit": "séries/sem", "nature": nature, "metric_version": "v", "data_quality": "HIGH", "limitations": []}


def _item_plano(group_id, name, *, status, weekly, target):
    return {
        "muscle_group_id": group_id,
        "muscle_group": name,
        "status": status,
        "targets": {"min": _m(None, "PLANNED"), "target": _m(target, "PLANNED"), "max": _m(None, "PLANNED"), "source": TargetSource.TEMPLATE_PLAN},
        "realized": {"physical_sets": _m(10.0, "OBSERVED"), "weekly_effective_sets": _m(weekly), "direct_sets": _m(5.0, "OBSERVED"), "indirect_sets": _m(2.0, "OBSERVED")},
        "frequency": {"direct_sessions": _m(2.0)},
        "hours_since_last_stimulus": _m(24.0),
        "last_direct_local_date": date(2026, 9, 22),
        "data_quality": DataQuality.HIGH,
        "limitations": [],
    }


def test_bloco_prioritario_le_os_tres_estados_e_ignora_grupo_fora_do_modelo():
    muscles = {
        "items": [
            _item_plano(PEITO, "Peitoral", status="BELOW_TARGET", weekly=4.0, target=14.0),
            _item_plano(COSTAS, "Costas", status="ABOVE_TARGET", weekly=20.0, target=10.0),
            _item_plano(PERNAS, "Pernas", status="ABOVE_TARGET", weekly=3.0, target=0.0),
            _item_plano(TRICEPS, "Triceps", status="ON_TARGET", weekly=3.5, target=3.5),
        ],
        "target_source": TargetSource.TEMPLATE_PLAN,
        "data_quality": DataQuality.HIGH,
        "limitations": [],
    }
    bloco = ov.priority_muscles_block(muscles, window=_window(28))
    PriorityMusclesBlockOut.model_validate(bloco)
    assert bloco["target_source"] is TargetSource.TEMPLATE_PLAN
    assert bloco["groups_with_target"] == 4
    assert [g["muscle_group"] for g in bloco["below_min"]] == ["Peitoral"]
    # Pernas esta fora do Modelo (alvo 0): trabalho extra, nao "excesso".
    assert [g["muscle_group"] for g in bloco["above_max"]] == ["Costas"]
    assert bloco["below_min"][0]["target_min"]["value"] is None
    assert bloco["above_max"][0]["target_max"]["value"] is None
