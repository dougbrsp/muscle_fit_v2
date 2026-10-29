"""Lista de exercicios elegiveis para a troca numa linha salva da ficha nao
finalizada (`GET /workout-plans/{id}/exercise-swap-options`).

A regra e a mesma da validacao do save (422 `muscle_group_locked`): o teste de
paridade garante que a tela nunca oferece o que o servidor recusaria, nem
esconde o que ele aceitaria - inclusive para exercicios com mais de um grupo
PRIMARY (115 e 137 em producao), em que o grupo vale pelo desempate do dono
(`progression_state.primary_groups_from_cursor`: maior peso, depois menor id).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import services
from app import workout_plan_edit_rules as rules

# exercicio -> [(grupo, peso)] com role PRIMARY. 115 e 137 tem dois PRIMARY
# (como em producao): 115 empata no peso (vence o menor id do grupo), 137 tem
# pesos diferentes (vence o maior peso, mesmo com id de grupo maior).
PRIMARY_CONTRIBUTIONS: dict[int, list[tuple[int, float]]] = {
    5: [(12, 1.0)],  # peitoral
    6: [(12, 1.0)],  # peitoral
    7: [(6, 1.0)],  # costas
    115: [(55, 1.0), (1, 1.0)],  # abdomen (1) por desempate de id
    137: [(1, 0.6), (55, 1.0)],  # core (55) pelo peso
    114: [(1, 1.0)],  # abdomen
    200: [(55, 1.0)],  # core
    300: [],  # sem PRIMARY
}
ACTIVE_CATALOG = [5, 6, 7, 114, 115, 137, 200, 300]


class _Cursor:
    def __init__(self, status="planned", plan_rows=None, legacy=None):
        self.status = status
        self.plan_rows = plan_rows if plan_rows is not None else []
        self.legacy = legacy or {}
        self.calls: list[str] = []
        self._result: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.calls.append(flat)
        if flat.startswith("SELECT id, status FROM workout_plans"):
            self._result = [{"id": params[0], "status": self.status}] if self.status else []
        elif flat.startswith("SELECT id, mapping_id FROM workout_plan_exercises"):
            self._result = list(self.plan_rows)
        elif flat.startswith("SELECT id FROM exercise_catalog"):
            self._result = [{"id": i} for i in ACTIVE_CATALOG]
        elif flat.startswith("SELECT old_exercise_id, canonical_exercise_id FROM exercise_legacy_id_map"):
            self._result = [
                {"old_exercise_id": old, "canonical_exercise_id": self.legacy[old]} for old in params if old in self.legacy
            ]
        elif "exercise_legacy_id_map" in flat:
            old = params[0]
            self._result = [{"canonical_exercise_id": self.legacy[old]}] if old in self.legacy else []
        elif "exercise_muscle_contributions" in flat:
            rows = [
                {"exercise_id": eid, "muscle_group_id": gid, "stimulus_weight": w}
                for eid in params
                for gid, w in PRIMARY_CONTRIBUTIONS.get(eid, [])
            ]
            # Mesma ordenacao que o SQL do dono pede ao banco.
            self._result = sorted(rows, key=lambda r: (-r["stimulus_weight"], r["muscle_group_id"]))
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur


@pytest.fixture
def ficha(monkeypatch):
    def _fake(**kwargs):
        cur = _Cursor(**kwargs)

        @contextmanager
        def _transaction():
            yield _Conn(cur)

        monkeypatch.setattr(services, "transaction", _transaction)
        return cur

    return _fake


# ---------------------------------------------------------------------------
# Regra pura (dono: workout_plan_edit_rules)
# ---------------------------------------------------------------------------


def test_elegiveis_sao_os_do_mesmo_grupo_e_os_sem_grupo():
    ids = rules.eligible_swap_exercise_ids(persisted_group_id=12, candidate_groups={5: 12, 6: 12, 7: 6, 300: None})
    assert ids == [5, 6, 300]


def test_linha_sem_grupo_conhecido_nao_restringe():
    assert rules.eligible_swap_exercise_ids(persisted_group_id=None, candidate_groups={5: 12}) is None


@pytest.mark.parametrize(
    "old, new, ok", [(12, 12, True), (12, 6, False), (None, 6, True), (12, None, True), (None, None, True)]
)
def test_predicado_unico_da_troca(old, new, ok):
    assert rules.is_same_primary_group_swap(old, new) is ok


# ---------------------------------------------------------------------------
# services.list_exercise_swap_options
# ---------------------------------------------------------------------------


def _rows_by_id(result):
    return {row["workout_plan_exercise_id"]: row for row in result["rows"]}


def test_lista_por_linha_salva_usa_o_grupo_do_dono(ficha):
    ficha(plan_rows=[{"id": 1, "mapping_id": 5}, {"id": 2, "mapping_id": 7}, {"id": 3, "mapping_id": None}])
    rows = _rows_by_id(services.list_exercise_swap_options(10, user_id=1))
    assert rows[1] == {"workout_plan_exercise_id": 1, "primary_group_id": 12, "exercise_ids": [5, 6, 300]}
    assert rows[2]["exercise_ids"] == [7, 300]
    # Linha sem exercicio vinculado: o servidor nao compara grupo.
    assert rows[3] == {"workout_plan_exercise_id": 3, "primary_group_id": None, "exercise_ids": None}


def test_exercicios_com_mais_de_um_primary_seguem_o_desempate_do_dono(ficha):
    ficha(plan_rows=[{"id": 1, "mapping_id": 115}, {"id": 2, "mapping_id": 137}])
    rows = _rows_by_id(services.list_exercise_swap_options(10, user_id=1))
    assert rows[1]["primary_group_id"] == 1
    assert rows[1]["exercise_ids"] == [114, 115, 300]
    assert rows[2]["primary_group_id"] == 55
    assert rows[2]["exercise_ids"] == [137, 200, 300]


def test_id_legado_da_linha_e_resolvido_para_o_canonico(ficha):
    ficha(plan_rows=[{"id": 1, "mapping_id": 999}], legacy={999: 6})
    rows = _rows_by_id(services.list_exercise_swap_options(10, user_id=1))
    assert rows[1]["primary_group_id"] == 12
    assert 5 in rows[1]["exercise_ids"]


def test_ficha_finalizada_nao_oferece_troca(ficha):
    cur = ficha(status="done", plan_rows=[{"id": 1, "mapping_id": 5}])
    assert services.list_exercise_swap_options(10, user_id=1) == {"plan_id": 10, "editable": False, "rows": []}
    assert not [c for c in cur.calls if "exercise_catalog" in c]


def test_ficha_de_outro_usuario_e_404(ficha):
    ficha(status=None)
    with pytest.raises(services.PlanNotFoundError):
        services.list_exercise_swap_options(10, user_id=1)


# ---------------------------------------------------------------------------
# Paridade: o que a lista oferece == o que o save aceita
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persisted", [5, 7, 114, 115, 137, 200, 300])
def test_lista_elegivel_e_exatamente_o_que_o_save_aceita(ficha, persisted):
    cur = ficha(plan_rows=[{"id": 1, "mapping_id": persisted}])
    offered = _rows_by_id(services.list_exercise_swap_options(10, user_id=1))[1]["exercise_ids"]
    for candidate in ACTIVE_CATALOG:
        try:
            services._assert_exercise_keeps_primary_group(cur, persisted, candidate, "x")
            accepted = True
        except rules.PlanEditError:
            accepted = False
        assert accepted == (offered is None or candidate in offered), (persisted, candidate)
