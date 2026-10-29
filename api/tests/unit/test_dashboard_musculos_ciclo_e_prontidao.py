"""`/dashboard/musculos`: meta do Modelo por equipamento, projetado e prontidao.

Tres defeitos vistos em producao (2026-10-02, usuario com Modelo
`equipment_series` de 8 slots - 6 de treino + 2 de descanso - treinando um
slot por dia):

1. **Todo grupo ACIMA do planejado.** A meta semanal era a ativacao do ciclo
   x 7 / (7 x nº de slots): o ciclo de 8 dias virava 56 e a meta saia 7x
   menor. Ombros: 25,3 series efetivas no ciclo -> meta 3,16/sem contra
   21,7/sem realizadas. Um slot = um dia (convencao do motor de geracao):
   meta 22,1/sem.
2. **Projetado vazio.** A projecao so somava itens `muscle_group`; um Modelo
   por equipamento so tem itens `equipment`, entao a soma era sempre `{}`.
3. **Prontidao cinza.** O bloco repassava o `status` do servico de
   recuperacao (`green|yellow|red`, a COR), e o frontend le o dominio
   `readiness` (`READY|REDUCED_DOSE|NO_DIRECT_WORK`): todo selo caia em
   "Estado nao previsto no contrato desta versao do frontend".

Sem banco e sem relogio vivo: conexao falsa e datas fixas.
"""

from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta

import pytest

from app.intelligence.dashboard import muscle_summary_service as mss
from app.intelligence.dashboard.contracts import MusclesResponse, WindowInfo, WindowKind
from app.intelligence.volume_service import classify_planned_volume_status, template_cycle_length_days

OMBROS, TRICEPS, PERNAS = 10, 14, 4
NOMES = {OMBROS: "Ombros", TRICEPS: "Triceps", PERNAS: "Pernas"}

# Os tres codigos que o frontend conhece no dominio `readiness`
# (`frontend/src/lib/dashboard-status.ts`, `READINESS`).
CODIGOS_DE_PRONTIDAO_DO_FRONTEND = {"READY", "REDUCED_DOSE", "NO_DIRECT_WORK"}


def _window(days: int = 28) -> WindowInfo:
    end = date(2026, 10, 2)
    return WindowInfo(
        kind=WindowKind.ROLLING_28D,
        date_from=end - timedelta(days=days - 1),
        date_to=end,
        timezone="America/Sao_Paulo",
        days=days,
    )


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self._conn.executed.append(sql)
        self._rows = self._conn.dispatch(sql)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, *, head=None, activation=None, roles=None, plan_rows=None):
        self.head = head or []
        self.activation = activation or []
        self.roles = roles or []
        self.plan_rows = plan_rows or []
        self.executed: list[str] = []

    def cursor(self):
        return _Cursor(self)

    def dispatch(self, sql: str):
        if "FROM workout_plan_template_assignments" in sql:
            return self.head
        if "JOIN workout_plan_template_slot_items" in sql:
            return self.activation
        if "SELECT template_slot_sequence" in sql:
            return self.plan_rows
        if sql.strip() == "SELECT id, name FROM muscle_groups":
            return [{"id": gid, "name": nome} for gid, nome in NOMES.items()]
        if "AS rpe_n" in sql:
            return self.roles
        return []


# Modelo de 8 slots, como o do usuario: 6 de treino e 2 de descanso.
HEAD_8_SLOTS = [{"template_id": 5, "template_name": "ABC-DEF", "volume_mode": "equipment_series", "slots_count": 8}]
# Ombros nos slots 3 e 7 (25,3 no ciclo); triceps nos slots 5 e 7 (24,7).
ATIVACAO_8_SLOTS = [
    {"slot_sequence": 3, "muscle_group_id": OMBROS, "planned_sets": 12.0},
    {"slot_sequence": 7, "muscle_group_id": OMBROS, "planned_sets": 13.3},
    {"slot_sequence": 5, "muscle_group_id": TRICEPS, "planned_sets": 12.0},
    {"slot_sequence": 7, "muscle_group_id": TRICEPS, "planned_sets": 12.7},
]


def _role_row(group_id: int, stimulus: float) -> dict:
    return {
        "muscle_group_id": group_id,
        "role": "PRIMARY",
        "sets_count": stimulus,
        "stimulus_sets": stimulus,
        "fatigue_sets": stimulus,
        "rpe_sum": 0,
        "rpe_n": 0,
    }


# ---------------------------------------------------------------------------
# 1) Tamanho do ciclo: um slot = um dia
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slots, dias", [(8, 8), (7, 7), (3, 3), (1, 1), (0, 0)])
def test_ciclo_tem_um_dia_por_slot(slots, dias):
    assert template_cycle_length_days(slots) == dias


def test_modelo_de_8_slots_vira_ciclo_de_8_dias_e_meta_proporcional():
    plano = mss.resolve_template_plan(_Conn(head=HEAD_8_SLOTS, activation=ATIVACAO_8_SLOTS), user_id=1)
    assert plano is not None
    assert plano.cycle_length_days == 8
    assert plano.activation_by_group[OMBROS] == pytest.approx(25.3)
    # 25,3 x 7 / 8 = 22,1375. Antes: 25,3 x 7 / 56 = 3,1625.
    assert plano.weekly_targets[OMBROS] == pytest.approx(22.1375)
    assert plano.weekly_targets[TRICEPS] == pytest.approx(21.6125)


def test_ativacao_tambem_fica_aberta_por_slot():
    plano = mss.resolve_template_plan(_Conn(head=HEAD_8_SLOTS, activation=ATIVACAO_8_SLOTS), user_id=1)
    assert plano.activation_by_slot == {
        3: {OMBROS: pytest.approx(12.0)},
        5: {TRICEPS: pytest.approx(12.0)},
        7: {OMBROS: pytest.approx(13.3), TRICEPS: pytest.approx(12.7)},
    }
    assert "s.sequence" in mss.TEMPLATE_PLAN_ACTIVATION_SQL


def test_realizado_tipico_nao_fica_todo_acima_do_planejado(monkeypatch):
    """Cenario sintetico baseado em caso real (28 dias): ombros 21,5/sem e
    triceps 17,0/sem. Com a meta 7x menor, os dois davam ABOVE_TARGET; com
    um slot por dia, ombros fica dentro da tolerancia e triceps abaixo."""
    monkeypatch.setattr(mss, "_planned_by_group", lambda user_id, window: {})
    monkeypatch.setattr(mss, "_projected_by_group", lambda user_id, template_plan=None, targets_by_group=None: ({}, 0, None))
    conn = _Conn(
        head=HEAD_8_SLOTS,
        activation=ATIVACAO_8_SLOTS,
        roles=[_role_row(OMBROS, 21.5 * 4), _role_row(TRICEPS, 17.0 * 4)],
    )
    payload = mss.build_muscle_summary(conn, user_id=1, window=_window(28), include_readiness=False)
    MusclesResponse.model_validate(payload)
    itens = {item["muscle_group_id"]: item for item in payload["items"]}
    assert itens[OMBROS]["targets"]["target"]["value"] == pytest.approx(22.14, abs=0.01)
    assert itens[OMBROS]["status"] == "ON_TARGET"
    assert itens[TRICEPS]["status"] == "BELOW_TARGET"
    assert any("ciclo de 8 dias" in limitacao for limitacao in payload["limitations"])


def test_meta_antiga_marcava_tudo_acima():
    """Documenta o defeito: com a meta 7x menor, ate o realizado abaixo do
    plano (triceps) aparecia ACIMA."""
    meta_antiga = 24.7 * 7 / 56
    assert classify_planned_volume_status(17.0, meta_antiga) == "ABOVE_TARGET"
    assert classify_planned_volume_status(17.0, 24.7 * 7 / 8) == "BELOW_TARGET"


def test_aderencia_perfeita_a_um_ciclo_de_8_slots_fica_no_planejado():
    """M76 Lote 4a: o realizado e uma taxa de dias corridos, entao a meta que
    ele encara e o RITMO do ciclo no calendario (`calendar_weekly_rate`), nao a
    semana do editor (`weekly_normalization`, que corta o 8o dia). Quem roda
    um slot por dia faz o ciclo de 8 slots a cada 8 dias: `ciclo x 7/8` por
    semana. Contra a semana do editor isso daria BELOW_TARGET para sempre."""
    from app.intelligence.methodology_volume import calendar_weekly_rate, weekly_normalization

    plano = mss.resolve_template_plan(_Conn(head=HEAD_8_SLOTS, activation=ATIVACAO_8_SLOTS), user_id=1)
    # 8 semanas seguindo a risca: 7 ciclos inteiros em 56 dias.
    realizado_semana = calendar_weekly_rate(plano.activation_by_group[OMBROS] * 7, 56)
    assert classify_planned_volume_status(realizado_semana, plano.weekly_targets[OMBROS]) == "ON_TARGET"

    tipos = ["normal", "normal", "normal", "descanso", "normal", "normal", "normal", "descanso"]
    slots = [mss.PlanSlot(sequence=i, slot_type=t) for i, t in enumerate(tipos, start=1)]
    semana_do_editor = weekly_normalization(slots, plano.activation_by_slot)[OMBROS]
    assert classify_planned_volume_status(realizado_semana, semana_do_editor) == "BELOW_TARGET"


# ---------------------------------------------------------------------------
# 2) Projetado do Modelo por equipamento
# ---------------------------------------------------------------------------


def test_projetado_soma_a_ativacao_dos_slots_pendentes():
    por_slot = {3: {OMBROS: 12.0}, 5: {TRICEPS: 12.0}, 7: {OMBROS: 13.3, TRICEPS: 12.7}}
    assert mss.sum_projected_activation_from_pending_slots(por_slot, {5, 7, 8}) == {
        OMBROS: pytest.approx(13.3),
        TRICEPS: pytest.approx(24.7),
    }
    # Slot de descanso (sem ativacao) e ciclo ja completo nao inventam nada.
    assert mss.sum_projected_activation_from_pending_slots(por_slot, {4, 8}) == {}
    assert mss.sum_projected_activation_from_pending_slots(por_slot, set()) == {}


def _patch_ciclo(monkeypatch, *, template_id: int, plan_rows: list[dict]):
    """Modelo atribuido com 8 slots so de itens `equipment` (como o real) e
    fichas ja geradas no ciclo corrente."""
    slots = [
        {"sequence": seq, "items": [{"item_kind": "equipment", "mapping_id": 100 + seq, "sets_count": 3}]}
        for seq in range(1, 9)
    ]
    monkeypatch.setattr("app.analytics._resolve_latest_assigned_template_id", lambda user_id: template_id)
    monkeypatch.setattr(
        "app.workout_plan_templates_engine.get_template_if_assigned",
        lambda tid, user_id: {"id": tid, "slots": slots},
    )
    conn = _Conn(plan_rows=plan_rows)
    monkeypatch.setattr("app.db.transaction", lambda: contextlib.nullcontext(conn))


def test_projetado_do_modelo_por_equipamento_nao_fica_vazio(monkeypatch):
    # Ciclo corrente: slots 1 e 2 ja rodaram -> 3..8 pendentes.
    _patch_ciclo(monkeypatch, template_id=5, plan_rows=[{"template_slot_sequence": 2}, {"template_slot_sequence": 1}])
    plano = mss.resolve_template_plan(_Conn(head=HEAD_8_SLOTS, activation=ATIVACAO_8_SLOTS), user_id=1)

    projetado, pendentes, ciclo = mss._projected_by_group(1, template_plan=plano)

    assert projetado == {OMBROS: pytest.approx(25.3), TRICEPS: pytest.approx(24.7)}
    assert pendentes == 6
    assert ciclo == 8


def test_projetado_sem_plano_por_equipamento_mantem_a_regra_antiga(monkeypatch):
    """Sem `template_plan` (Modelo percentual) a soma continua sendo a dos
    itens `muscle_group` - que aqui nao existem, entao nada muda."""
    _patch_ciclo(monkeypatch, template_id=5, plan_rows=[{"template_slot_sequence": 1}])
    projetado, pendentes, ciclo = mss._projected_by_group(1)
    assert projetado == {}
    assert pendentes == 7
    assert ciclo == 8


def test_projetado_do_modo_a_usa_o_orcamento_do_item_e_nao_sets_count(monkeypatch):
    """M76 Lote 4a (E2): Modelo `percent_budget` sem series digitadas
    projetava 0. O valor por slot agora e o do editor: ponto medio do
    orcamento (20-30% de uma meta de 15 = 3,75); sem meta, `sets_count`."""
    slots = [
        {
            "sequence": seq,
            "slot_type": "normal",
            "items": [
                {"item_kind": "muscle_group", "muscle_group_id": OMBROS, "sets_count": None,
                 "priority_min_pct": 20, "priority_max_pct": 30},
                {"item_kind": "muscle_group", "muscle_group_id": TRICEPS, "sets_count": 2,
                 "priority_min_pct": 20, "priority_max_pct": 30},
            ],
        }
        for seq in (1, 2, 3)
    ]
    monkeypatch.setattr("app.analytics._resolve_latest_assigned_template_id", lambda user_id: 24)
    monkeypatch.setattr(
        "app.workout_plan_templates_engine.get_template_if_assigned",
        lambda tid, user_id: {"id": tid, "slots": slots},
    )
    conn = _Conn(plan_rows=[{"template_slot_sequence": 1}])
    monkeypatch.setattr("app.db.transaction", lambda: contextlib.nullcontext(conn))

    projetado, pendentes, _ = mss._projected_by_group(1, targets_by_group={OMBROS: {"min": 10, "target": 15, "max": 20}})

    assert pendentes == 2
    assert projetado == {OMBROS: pytest.approx(7.5), TRICEPS: pytest.approx(4.0)}


def test_projetado_ignora_plano_de_outro_modelo(monkeypatch):
    """Se o Modelo da meta e o do ciclo divergirem (flag de fronteira de
    versao), a ativacao de um nao e usada para projetar o outro."""
    _patch_ciclo(monkeypatch, template_id=99, plan_rows=[])
    plano = mss.resolve_template_plan(_Conn(head=HEAD_8_SLOTS, activation=ATIVACAO_8_SLOTS), user_id=1)
    projetado, _, _ = mss._projected_by_group(1, template_plan=plano)
    assert projetado == {}


# ---------------------------------------------------------------------------
# 3) Prontidao: codigo canonico, nunca a cor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "readiness_status, cor, orientacao",
    [("READY", "green", "ideal"), ("REDUCED_DOSE", "yellow", "recovering"), ("NO_DIRECT_WORK", "red", "avoid")],
)
def test_bloco_de_prontidao_devolve_o_codigo_canonico(readiness_status, cor, orientacao):
    linha = {
        "readiness_status": readiness_status,
        "status": cor,
        "guidance": orientacao,
        "estimated_recovery_hours": 48,
        "hours_since_trained": 20,
        "explanation": ["x"],
    }
    bloco = mss._readiness_block(linha)
    assert bloco["status"] == readiness_status
    assert bloco["status"] in CODIGOS_DE_PRONTIDAO_DO_FRONTEND
    assert bloco["guidance"] == orientacao


def test_resposta_de_musculos_so_traz_codigos_que_o_frontend_conhece(monkeypatch):
    monkeypatch.setattr(mss, "_planned_by_group", lambda user_id, window: {})
    monkeypatch.setattr(mss, "_projected_by_group", lambda user_id, template_plan=None, targets_by_group=None: ({}, 0, None))
    monkeypatch.setattr(
        mss,
        "_readiness_by_group",
        lambda user_id: {
            OMBROS: {"readiness_status": "REDUCED_DOSE", "status": "yellow", "guidance": "recovering"},
            TRICEPS: {"readiness_status": "READY", "status": "green", "guidance": "ideal"},
        },
    )
    conn = _Conn(
        head=HEAD_8_SLOTS,
        activation=ATIVACAO_8_SLOTS,
        roles=[_role_row(OMBROS, 80.0), _role_row(TRICEPS, 70.0)],
    )
    payload = mss.build_muscle_summary(
        conn, user_id=1, window=_window(28), include_readiness=True, now_local=datetime(2026, 10, 2, 12, 0)
    )
    MusclesResponse.model_validate(payload)
    estados = {item["muscle_group_id"]: item["readiness"]["status"] for item in payload["items"]}
    assert estados == {OMBROS: "REDUCED_DOSE", TRICEPS: "READY"}
