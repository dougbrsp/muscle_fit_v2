"""Unit (sem banco): blocos de dias de `load_rows` e regras do agregado
canonico de `app/muscle_groups_mapped.py` a partir de linhas ja agregadas.

M76 Lote 3c: o peso de cada grupo e o `stimulus_weight` cadastrado (resolvido
pelo dono, `VolumeService`), nao mais 1,0 / 0,5 fixos."""

from __future__ import annotations

import datetime as _dt
import inspect

import pytest

from app import muscle_groups_mapped as mgm
from app.security import Identity


@pytest.mark.parametrize(
    "days,chunk,expected",
    [
        (30, 90, [(30, None)]),
        (90, 90, [(90, None)]),
        (91, 90, [(91, 1), (1, None)]),
        (365, 90, [(365, 275), (275, 185), (185, 95), (95, 5), (5, None)]),
        (7, 3, [(7, 4), (4, 1), (1, None)]),
        (5, 0, [(5, 4), (4, 3), (3, 2), (2, 1), (1, None)]),
    ],
)
def test_day_windows(days, chunk, expected):
    assert mgm.day_windows(days, chunk) == expected


def test_day_windows_cobre_a_janela_sem_sobreposicao():
    windows = mgm.day_windows(2000, 90)
    covered = set()
    for start, end in windows:
        days = set(range(end if end is not None else 0, start + 1)) - ({end} if end is not None else set())
        assert not covered & days
        covered |= days
    assert covered == set(range(0, 2001))


def test_sql_agrega_e_limita_por_bloco():
    sc = {
        "activity_id": "id", "date_col": "local_date", "exercise_id": "id", "exercise_activity": "activity_id",
        "original_name": "original_name", "set_exercise": "exercise_id", "reps": "reps", "weight": "weight_kg",
        "rpe": "rpe", "set_type": "set_type",
    }
    bounded = mgm._load_rows_sql(sc, bounded=True, by_user=True)
    assert bounded.count("%s") == 3
    assert "GROUP BY t.activity_date, t.exercise_name, t.canonical_exercise_id" in bounded
    assert "LIMIT 1\n" in bounded and "ANY_VALUE" not in bounded
    # Lote 3c: grupos e pesos saem das contribuicoes (VolumeService), nao do SQL.
    assert "exercise_muscle_contributions" not in bounded
    assert mgm._load_rows_sql(sc, bounded=False, by_user=False).count("%s") == 1


def _row(day, name, canonical_id, *, sets, reps, weight_sum, volume_kg, volume_metric, rpe_sum, rpe_count):
    return {
        "activity_date": day, "exercise_name": name, "canonical_exercise_id": canonical_id, "set_count": sets,
        "reps_sum": reps, "weight_sum": weight_sum, "volume_kg_sum": volume_kg, "volume_metric_sum": volume_metric,
        "rpe_sum": rpe_sum, "rpe_count": rpe_count,
    }


REMADA, BARRA, ROSCA_SEM_CONTRIBUICAO, AGACHAMENTO, PRANCHA, REMADA_DUPLA = 10, 11, 12, 13, 14, 15
COSTAS, BICEPS, CORE, QUADRICEPS, GLUTEOS = 1, 2, 3, 4, 5
GROUP_NAMES = {COSTAS: "Costas", BICEPS: "Bíceps", CORE: "Core", QUADRICEPS: "Quadríceps", GLUTEOS: "Glúteos"}


def _emc(group_id, role, weight):
    return {"muscle_group_id": group_id, "role": role, "stimulus_weight": weight, "fatigue_weight": weight}


# Contribuicoes cadastradas (o que `exercise_muscle_contributions` devolveria).
CONTRIBUTIONS = {
    REMADA: [_emc(COSTAS, "PRIMARY", 1.0), _emc(BICEPS, "SECONDARY", 0.5)],
    # Peso secundario REAL diferente de 0,5 e um estabilizador.
    BARRA: [_emc(COSTAS, "PRIMARY", 1.0), _emc(BICEPS, "SECONDARY", 0.7), _emc(CORE, "STABILIZER", 0.1)],
    # PRIMARY com peso < 1: antes a tela forcava 1,0.
    AGACHAMENTO: [_emc(QUADRICEPS, "PRIMARY", 0.8), _emc(GLUTEOS, "SECONDARY", 0.5)],
    PRANCHA: [_emc(CORE, "PRIMARY", 1.0)],
    # Mesmo grupo em dois papeis do mesmo exercicio.
    REMADA_DUPLA: [_emc(COSTAS, "PRIMARY", 1.0), _emc(COSTAS, "STABILIZER", 0.2)],
}


@pytest.fixture
def contributions(monkeypatch):
    """Dono das contribuicoes (`VolumeService`) sem banco: so a leitura e
    trocada; a resolucao (`_resolve_contributions`) e a real."""
    calls = []

    def fetch(self, ids):
        calls.append(set(ids))
        return {int(i): list(CONTRIBUTIONS[int(i)]) for i in ids if i in CONTRIBUTIONS}

    monkeypatch.setattr(mgm.VolumeService, "_fetch_contributions_by_mapping", fetch)
    monkeypatch.setattr(mgm.VolumeService, "muscle_group_names_by_id", lambda self: dict(GROUP_NAMES))
    return calls


def _with_rows(monkeypatch, rows):
    monkeypatch.setattr(mgm, "load_rows", lambda days, user_id=None: (rows, {"ok": True}))


@pytest.fixture
def fake_rows(monkeypatch, contributions):
    day = _dt.date(2026, 9, 1)
    rows = [
        _row(day, "Remada", REMADA, sets=3, reps=30, weight_sum=150, volume_kg=1500, volume_metric=1500, rpe_sum=24, rpe_count=3),
        _row(day, "Barra fixa", BARRA, sets=2, reps=16, weight_sum=0, volume_kg=0, volume_metric=16, rpe_sum=9, rpe_count=1),
        _row(day, "Rosca", ROSCA_SEM_CONTRIBUICAO, sets=4, reps=40, weight_sum=40, volume_kg=400, volume_metric=400, rpe_sum=0, rpe_count=0),
        _row(day, "", None, sets=1, reps=10, weight_sum=0, volume_kg=0, volume_metric=10, rpe_sum=None, rpe_count=0),
    ]
    _with_rows(monkeypatch, rows)
    return rows


def test_aggregate_pondera_pelo_stimulus_weight_cadastrado(fake_rows, contributions):
    result = mgm.aggregate("weekly", "volume", 30, True, user_id=1)

    assert contributions == [{REMADA, BARRA, ROSCA_SEM_CONTRIBUICAO}]  # uma leitura so
    assert (result["total_sets"], result["mapped_sets"], result["unmapped_sets"], result["mapped_ratio"]) == (10, 5, 5, 0.5)
    assert result["unmapped"] == [{"exercise": "Rosca", "sets": 4}, {"exercise": "(sem nome)", "sets": 1}]
    by_group = {row["group"]: row for row in result["table_rows"]}
    costas, biceps, core = by_group["Costas"], by_group["Bíceps"], by_group["Core"]
    assert (costas["sets"], costas["value"], costas["volume_kg"], costas["avg_rpe"], costas["rpe_sets"]) == (
        5, 1516.0, 1500.0, 8.25, 4,
    )
    # Bíceps: 0,5 da Remada e 0,7 (cadastrado) da Barra - nao 0,5 fixo.
    assert biceps["value"] == round(1500 * 0.5 + 16 * 0.7, 2)
    assert (biceps["volume_kg"], biceps["sets"], biceps["rpe_sets"]) == (750.0, 5, 4)
    assert biceps["avg_rpe"] == round((24 * 0.5 + 9 * 0.7) / (3 * 0.5 + 1 * 0.7), 2)
    # Estabilizador entra com o seu peso quando os secundarios estao incluidos.
    assert (core["value"], core["sets"]) == (1.6, 2)
    assert costas["exercises"] == ["Barra fixa", "Remada"] and costas["period_count"] == 1
    assert not {"rpe_sum", "rpe_weight", "periods"} & set(costas)
    sources = {row["group"]: row["source"] for row in result["trend_rows"]}
    assert sources == {
        "Costas": "canonical_primary",
        "Bíceps": "canonical_secondary",
        "Core": "canonical_stabilizer",
        "Outros": "unmapped",
    }


def test_sem_contribuicao_cai_em_outros_como_no_volume_service(fake_rows):
    """Politica unica (M76 Lote 3c): exercicio sem contribuicao cadastrada
    conta em "Outros" com peso PRIMARY - igual a `VolumeService` - e continua
    listado em `unmapped` para a associacao."""
    result = mgm.aggregate("weekly", "sets", 30, False)
    outros = next(row for row in result["table_rows"] if row["group"] == "Outros")
    assert (outros["sets"], outros["value"], outros["exercises"]) == (5, 5.0, ["", "Rosca"])
    assert {row["group"] for row in result["table_rows"]} == {"Costas", "Outros"}


def test_sem_secundarios_so_conta_primary_com_o_peso_dele(monkeypatch, contributions):
    _with_rows(monkeypatch, [
        _row(_dt.date(2026, 9, 1), "Agachamento", AGACHAMENTO, sets=4, reps=40, weight_sum=400, volume_kg=4000,
             volume_metric=4000, rpe_sum=32, rpe_count=4),
    ])

    so_primario = {row["group"]: row["value"] for row in mgm.aggregate("weekly", "volume", 30, False)["table_rows"]}
    com_secundario = {row["group"]: row["value"] for row in mgm.aggregate("weekly", "volume", 30, True)["table_rows"]}

    assert so_primario == {"Quadríceps": 3200.0}  # 0,8 cadastrado, nao 1,0 fixo
    assert com_secundario == {"Quadríceps": 3200.0, "Glúteos": 2000.0}


def test_grupo_em_dois_papeis_do_mesmo_exercicio_nao_dobra_series(monkeypatch, contributions):
    _with_rows(monkeypatch, [
        _row(_dt.date(2026, 9, 1), "Remada curvada", REMADA_DUPLA, sets=3, reps=30, weight_sum=150, volume_kg=1500,
             volume_metric=1500, rpe_sum=24, rpe_count=3),
    ])
    [costas] = mgm.aggregate("weekly", "volume", 30, True)["table_rows"]
    assert (costas["sets"], costas["value"]) == (3, 1800.0)
    [so_primario] = mgm.aggregate("weekly", "volume", 30, False)["table_rows"]
    assert so_primario["value"] == 1500.0  # so o papel PRIMARY (o estabilizador fica de fora)


def test_aggregate_grupo_sem_rpe_tem_avg_rpe_none(monkeypatch, contributions):
    _with_rows(monkeypatch, [
        _row(_dt.date(2026, 9, 1), "Prancha", PRANCHA, sets=3, reps=0, weight_sum=0, volume_kg=0, volume_metric=0,
             rpe_sum=None, rpe_count=0),
    ])
    [core] = mgm.aggregate("weekly", "sets", 30, False)["table_rows"]
    assert (core["value"], core["avg_rpe"], core["rpe_sets"]) == (3.0, None, 0)


def test_aggregate_sem_series(monkeypatch, contributions):
    _with_rows(monkeypatch, [])
    result = mgm.aggregate("weekly", "volume", 30, False)
    assert (result["total_sets"], result["unmapped_sets"], result["mapped_ratio"], result["table_rows"]) == (0, 0, 0, [])


def test_health_lista_so_grupos_primarios_cadastrados(fake_rows):
    payload = mgm.health(days=30, identity=Identity(user_id=1, role="regular", source="session"))
    assert payload["groups"] == ["Costas"]
    assert payload["set_count"] == 10


def test_rotas_nao_aceitam_mais_fator_fixo_de_secundario():
    for funcao in (mgm.trend, mgm.table, mgm.aggregate):
        assert "secondary_factor" not in inspect.signature(funcao).parameters
