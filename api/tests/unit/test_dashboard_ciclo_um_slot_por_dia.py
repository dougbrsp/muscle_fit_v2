"""Tamanho do ciclo do Modelo: um slot = um dia em TODO o dashboard.

O PR #312 trocou a convencao `7 x nº de slots` por
`volume_service.template_cycle_length_days` (a do motor de geracao e da
metodologia) so na meta e no projetado de `/dashboard/musculos`. Sobravam tres
consumidores com a convencao antiga, todos alimentados pelo mesmo numero:

- a aderencia (`build_adherence`): sessoes esperadas = janela / ciclo x slots
  de treino. Com o Modelo de 8 slots do usuario o ciclo virava 56 dias e a
  janela de 28 dias esperava 4 sessoes - 21 realizadas davam 525%;
- `window=cycle` de todos os endpoints v3 (`resolve_cycle_length_days`): a
  "janela do ciclo" cobria 56 dias em vez de 8;
- `GET /analytics/dashboard-cycle`: o realizado era lido em 56 dias e
  normalizado por 7/56.

M76 Lote 4a (E1/E2): `dashboard-cycle` passou a medir a SEMANA do dono
(`methodology_volume`): realizado dos ultimos 7 dias e planejado da semana
(7 dias a partir do 1o slot de treino), na mesma unidade; o status e o do
planejado da semana.

Sem banco e sem relogio vivo: conexao falsa e datas fixas.
"""

from __future__ import annotations

import contextlib
from datetime import date
from types import SimpleNamespace

import pytest

from app import analytics
from app.intelligence.dashboard import adherence_service as ads
from app.intelligence.dashboard.contracts import WindowInfo, WindowKind
from app.intelligence.dashboard.time_windows import resolve_window

HOJE = date(2026, 10, 2)
FUSO = "America/Sao_Paulo"


def _slots(qtd: int, descanso: tuple[int, ...] = ()) -> list[dict]:
    return [
        {
            "sequence": seq,
            "name": "Descanso" if seq in descanso else f"Treino {seq}",
            "slot_type": "descanso" if seq in descanso else "normal",
            "weekday": None,
            "items": [],
        }
        for seq in range(1, qtd + 1)
    ]


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self._rows = self._conn.dispatch(sql)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """`plan_rows`: fichas do Modelo (ordem `id DESC`); `sessions`: linhas
    de `SESSIONS_SUMMARY_SQL`."""

    def __init__(self, *, plan_rows=None, sessions=None, head=None, activation=None):
        self.plan_rows = plan_rows or []
        self.sessions = sessions or []
        self.head = head or []
        self.activation = activation or []

    def cursor(self):
        return _Cursor(self)

    def dispatch(self, sql: str):
        if "SELECT template_slot_sequence" in sql:
            return self.plan_rows
        if sql == ads.SESSIONS_SUMMARY_SQL:
            return self.sessions
        if "FROM workout_plan_template_assignments" in sql:
            return self.head
        if "JOIN workout_plan_template_slot_items" in sql:
            return self.activation
        return []


def _patch_modelo(monkeypatch, slots: list[dict], *, conn: _Conn | None = None) -> _Conn:
    conn = conn or _Conn()
    template = {"id": 28, "name": "ABC-DEF", "slots": slots, "training_type_id": None}
    monkeypatch.setattr("app.analytics._resolve_latest_assigned_template_id", lambda user_id: 28)
    monkeypatch.setattr(
        "app.workout_plan_templates_engine.get_template_if_assigned", lambda tid, user_id, **_kw: template
    )
    monkeypatch.setattr("app.workout_plan_templates_engine.resolve_next_slot", lambda tpl, user_id: slots[0])
    monkeypatch.setattr("app.db.transaction", lambda: contextlib.nullcontext(conn))
    return conn


def _janela_28d() -> WindowInfo:
    return resolve_window(WindowKind.ROLLING_28D, today=HOJE, timezone_name=FUSO)


# ---------------------------------------------------------------------------
# 1) O numero na origem: resolve_current_cycle / resolve_cycle_length_days
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("qtd_slots", [8, 7, 3, 1])
def test_ciclo_do_modelo_tem_um_dia_por_slot(monkeypatch, qtd_slots):
    _patch_modelo(monkeypatch, _slots(qtd_slots))
    assert ads.resolve_current_cycle(1)["cycle_length_days"] == qtd_slots
    assert ads.resolve_cycle_length_days(1) == qtd_slots


def test_window_cycle_do_modelo_de_oito_slots_cobre_oito_dias(monkeypatch):
    """`window=cycle` dos endpoints v3 (`analytics_dashboard._resolve`) sai
    de `resolve_cycle_length_days`: 8 slots = 8 dias, nao 56."""
    _patch_modelo(monkeypatch, _slots(8, descanso=(4, 8)))
    janela = resolve_window(
        WindowKind.CYCLE, today=HOJE, cycle_length_days=ads.resolve_cycle_length_days(1), timezone_name=FUSO
    )
    assert janela.days == 8
    assert (janela.date_from, janela.date_to) == (date(2026, 9, 25), HOJE)


# ---------------------------------------------------------------------------
# 2) Aderencia: sessoes esperadas na janela
# ---------------------------------------------------------------------------


def _sessoes_planejadas(qtd: int) -> list[dict]:
    return [
        {"activity_id": i, "local_date": HOJE, "session_status": "done", "workout_plan_id": i, "template_id": 28}
        for i in range(1, qtd + 1)
    ]


def test_aderencia_de_quem_treina_todo_slot_de_treino_e_cem_por_cento(monkeypatch):
    """Modelo de 8 slots (6 de treino + 2 de descanso), seguido a risca por
    28 dias: 3,5 voltas x 6 treinos = 21 sessoes esperadas e 21 feitas.
    Antes: ciclo de 56 dias -> 3 esperadas -> aderencia de 700%."""
    conn = _patch_modelo(monkeypatch, _slots(8, descanso=(4, 8)), conn=_Conn(sessions=_sessoes_planejadas(21)))
    corpo = ads.build_adherence(conn, user_id=1, window=_janela_28d())

    assert corpo["cycle_length_days"] == 8
    assert corpo["expected_sessions"]["value"] == 21.0
    assert corpo["missed_sessions"]["value"] == 0.0
    assert corpo["rate"]["value"] == 1.0


def test_slot_normal_sem_itens_ainda_conta_como_esperado(monkeypatch):
    """Cenario sintetico baseado em caso real: os slots 4 e 8 se chamam
    "Descanso" mas estao como `normal`. Sem decisao de produto em contrario,
    eles contam como sessao esperada (8 por volta de 8 dias)."""
    conn = _patch_modelo(monkeypatch, _slots(8), conn=_Conn(sessions=_sessoes_planejadas(21)))
    corpo = ads.build_adherence(conn, user_id=1, window=_janela_28d())

    assert corpo["expected_sessions"]["value"] == 28.0
    assert corpo["rate"]["value"] == 0.75


# ---------------------------------------------------------------------------
# 3) /analytics/dashboard-cycle
# ---------------------------------------------------------------------------


def _patch_cycle_endpoint(monkeypatch, slots, *, conn=None, targets=None, overview=None):
    _patch_modelo(monkeypatch, slots, conn=conn)
    monkeypatch.setattr(analytics, "_fetch_all", lambda sql, params=None: [])
    pedidos: list[int] = []

    def _overview(self, *, days, limit, user_id):
        pedidos.append(days)
        return list(overview or [])

    monkeypatch.setattr("app.intelligence.volume_service.VolumeService.get_muscle_group_overview", _overview)
    monkeypatch.setattr("app.training_types.resolve_active_training_type_for_user", lambda user_id: 5)
    monkeypatch.setattr(
        "app.training_types.get_training_type_muscle_group_targets",
        lambda tt: targets if targets is not None else {10: {"min": 10, "target": 15, "max": 20}},
    )
    return pedidos


def test_dashboard_cycle_le_o_realizado_na_semana_do_dono(monkeypatch):
    """Realizado dos ultimos 7 dias (a semana do dono), qualquer que seja o
    ciclo. Antes: janela = ciclo (8 dias) e `x 7/8`."""
    pedidos = _patch_cycle_endpoint(
        monkeypatch,
        _slots(8, descanso=(4, 8)),
        overview=[{"muscle_group_id": 10, "muscle_group": "Ombros", "stimulus_equivalent_sets": 21.0}],
    )

    corpo = analytics.dashboard_cycle(identity=SimpleNamespace(user_id=1))

    assert corpo["cycle_length_days"] == 8
    assert corpo["window_days"] == 7
    assert pedidos == [7]
    ombros = next(c for c in corpo["coverage"] if c["muscle_group_id"] == 10)
    assert ombros["actual_weekly_sets"] == 21.0


def _slots_com_grupo(qtd: int, descanso: tuple[int, ...], grupo: int, pct: tuple[int, int]) -> list[dict]:
    slots = _slots(qtd, descanso)
    for slot in slots:
        if slot["slot_type"] != "descanso":
            slot["items"] = [
                {
                    "item_kind": "muscle_group",
                    "muscle_group_id": grupo,
                    "sets_count": None,
                    "priority_min_pct": pct[0],
                    "priority_max_pct": pct[1],
                }
            ]
    return slots


@pytest.mark.parametrize(
    "qtd, descanso, vezes_na_semana",
    [
        (3, (), 7),  # 1,2,3,1,2,3,1: da a volta
        (4, (4,), 6),  # 1,2,3,D,1,2,3
        (7, (4, 7), 5),
        (8, (4, 8), 6),  # corta o 8o dia
        (24, (), 7),  # corta 17 dias
    ],
)
def test_planejado_da_semana_do_modo_a_e_o_orcamento_na_janela(monkeypatch, qtd, descanso, vezes_na_semana):
    """Modo A sem `sets_count` digitado: o planejado e o ponto medio do
    orcamento do item (20-30% de 15 = 3,75 por sessao) - antes o projetado
    somava `sets_count` e dava ZERO. Somado na semana do dono: sem `x 7/ciclo`."""
    _patch_cycle_endpoint(monkeypatch, _slots_com_grupo(qtd, descanso, 10, (20, 30)))

    corpo = analytics.dashboard_cycle(identity=SimpleNamespace(user_id=1))

    ombros = next(c for c in corpo["coverage"] if c["muscle_group_id"] == 10)
    assert ombros["planned_weekly_sets"] == pytest.approx(round(3.75 * vezes_na_semana, 2))
    assert ombros["projected_weekly_sets"] == ombros["planned_weekly_sets"]
    assert ombros["status_basis"] == "planned_weekly"
    esperado = analytics_status(ombros["planned_weekly_sets"], 10, 15, 20)
    assert ombros["status"] == esperado


def analytics_status(valor, minimo, alvo, maximo):
    from app.intelligence.volume_service import classify_weekly_volume_status

    return classify_weekly_volume_status(valor, minimo, alvo, maximo)


def test_planejado_do_modelo_por_equipamento_nao_e_zero_e_bate_com_o_editor(monkeypatch):
    """Modo B: a ativacao ponderada por slot (`resolve_template_plan`) somada
    nos slots 1..7 - o numero do editor. Antes: soma dos itens `muscle_group`
    (inexistentes no Modo B) -> projetado 0."""
    slots = _slots(8, descanso=(4, 8))
    conn = _Conn(
        head=[
            {
                "template_id": 28,
                "template_name": "ABC-DEF",
                "volume_mode": "equipment_series",
                "slots_count": 8,
                "slot_types": "1:normal,2:normal,3:normal,4:descanso,5:normal,6:normal,7:normal,8:descanso",
            }
        ],
        activation=[
            {"slot_sequence": 3, "muscle_group_id": 10, "planned_sets": 12.0},
            {"slot_sequence": 7, "muscle_group_id": 10, "planned_sets": 13.3},
        ],
        plan_rows=[{"template_slot_sequence": 2}, {"template_slot_sequence": 1}],
    )
    _patch_cycle_endpoint(monkeypatch, slots, conn=conn, targets={10: {"min": 10, "target": 20, "max": 24}})

    corpo = analytics.dashboard_cycle(identity=SimpleNamespace(user_id=1))

    ombros = next(c for c in corpo["coverage"] if c["muscle_group_id"] == 10)
    assert ombros["planned_weekly_sets"] == pytest.approx(25.3)
    assert ombros["pending_cycle_sets"] == pytest.approx(25.3)
    assert ombros["status"] == "ABOVE_MAX"
