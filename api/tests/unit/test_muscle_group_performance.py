"""Volume x Desempenho por grupo muscular em `/dashboard/progressao` (2026-10-02).

Cenario sintetico baseado em caso real (28 dias): o Modelo novo cortou as series por exercicio de
~3-4,5 para ~2 e a tonelagem caiu, enquanto o e1RM do mesmo exercicio subia.
A tela dizia "REGREDINDO" em 10 grupos. Agora volume e desempenho sao duas
leituras separadas e queda de volume nunca e "regredindo".

Datas fixas via `comparison_windows` - nenhum `date.today()`.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from app import analytics
from app.intelligence.muscle_group_performance import (
    ALL_OUTLIERS,
    LOW_ACTIVATION,
    MIN_ACTIVATION_SUM,
    NO_COMPARABLE_EXERCISE,
    OUTLIER_CHANGE,
    OUTLIER_CHANGE_PCT,
    PERFORMANCE_BASIS,
    build_group_performance,
)
from app.intelligence.muscle_group_progression import (
    VOLUME_DOWN,
    VOLUME_STABLE,
    VOLUME_UP,
    build_group_progression,
    comparison_windows,
    volume_status,
)

QUAD = 4


def _e1rm_row(period, best, *, exercise_id=22, equipment_id=50, convention="total", group=QUAD, weight=1.0, name="Hack"):
    return {
        "period": period,
        "exercise_id": exercise_id,
        "exercise_name": name,
        "equipment_id": equipment_id,
        "load_convention": convention,
        "muscle_group_id": group,
        "stimulus_weight": weight,
        "best_e1rm_kg": best,
    }


def _tonnage_rows(current_avg, previous_avg, sessions=4, group=QUAD):
    return [
        {"period": "current", "muscle_group_id": group, "muscle_group_name": "Quadriceps",
         "tonnage_kg": current_avg * sessions, "sets_count": 2 * sessions, "sessions_count": sessions},
        {"period": "previous", "muscle_group_id": group, "muscle_group_name": "Quadriceps",
         "tonnage_kg": previous_avg * sessions, "sets_count": 4 * sessions, "sessions_count": sessions},
    ]


def test_volume_cai_e_desempenho_sobe():
    """O caso que motivou a mudanca: metade das series (tonelagem -40%), carga maior
    (e1RM 160 -> 180, +12,5%). Volume menor + PROGREDINDO - nunca REGREDINDO."""
    performance = build_group_performance([_e1rm_row("previous", 160.0), _e1rm_row("current", 180.0)])
    (item,) = build_group_progression(_tonnage_rows(2400.0, 4000.0), None, performance)

    assert item["volume_status"] == VOLUME_DOWN
    assert item["change_pct"] == -40.0
    assert item["performance_status"] == "PROGREDINDO"
    assert item["performance_change_pct"] == 12.5
    assert item["performance_exercises_compared"] == 1
    (exercise,) = item["performance_exercises"]
    assert exercise["previous_e1rm_kg"] == 160.0 and exercise["current_e1rm_kg"] == 180.0
    # Compatibilidade: o campo antigo continua (deprecado), com o valor de antes.
    assert item["classification"] == "REGREDINDO"


def test_sem_exercicio_em_comum_e_dados_insuficientes():
    """Exercicio trocado entre as janelas: nao ha o que comparar."""
    performance = build_group_performance(
        [_e1rm_row("previous", 160.0, exercise_id=22), _e1rm_row("current", 200.0, exercise_id=60)]
    )
    assert performance == {}
    (item,) = build_group_progression(_tonnage_rows(1000.0, 1000.0), None, performance)
    assert item["volume_status"] == VOLUME_STABLE
    assert item["performance_status"] == "DADOS_INSUFICIENTES"
    assert item["performance_change_pct"] is None
    assert item["performance_exercises"] == []


def test_troca_de_maquina_nao_compara():
    performance = build_group_performance(
        [_e1rm_row("previous", 160.0, equipment_id=50), _e1rm_row("current", 120.0, equipment_id=18)]
    )
    assert performance == {}


def test_assistido_e_peso_corporal_nao_tem_e1rm():
    rows = []
    for convention in ("assisted", "bodyweight"):
        rows += [_e1rm_row("previous", 50.0, convention=convention), _e1rm_row("current", 80.0, convention=convention)]
    assert build_group_performance(rows) == {}


def test_convencao_ausente_e_total_sao_a_mesma_chave():
    # Sem cadastro em `equipment_exercises` (None) = `total` (D12).
    performance = build_group_performance(
        [_e1rm_row("previous", 100.0, convention=None), _e1rm_row("current", 110.0, convention="total")]
    )
    assert performance[QUAD]["performance_change_pct"] == 10.0


def test_equipamento_ausente_so_compara_quando_todas_as_exposicoes_estao_sem():
    so_sem = build_group_performance(
        [_e1rm_row("previous", 100.0, equipment_id=None), _e1rm_row("current", 110.0, equipment_id=None)]
    )
    assert so_sem[QUAD]["performance_status"] == "PROGREDINDO"

    misto = build_group_performance(
        [
            _e1rm_row("previous", 100.0, equipment_id=None),
            _e1rm_row("current", 110.0, equipment_id=None),
            _e1rm_row("current", 90.0, equipment_id=50),
        ]
    )
    assert misto == {}


def test_media_ponderada_pela_ativacao_e_faixa_estavel():
    """Extensora (100% quadriceps) +4%, agachamento (50%) +10%:
    (4x1 + 10x0,5) / 1,5 = 6,0% -> PROGREDINDO pela faixa de 5% do painel."""
    rows = [
        _e1rm_row("previous", 100.0, exercise_id=20, name="Extensora"),
        _e1rm_row("current", 104.0, exercise_id=20, name="Extensora"),
        _e1rm_row("previous", 100.0, exercise_id=58, weight=0.5, name="Agachamento"),
        _e1rm_row("current", 110.0, exercise_id=58, weight=0.5, name="Agachamento"),
    ]
    group = build_group_performance(rows)[QUAD]
    assert group["performance_change_pct"] == 6.0
    assert group["performance_status"] == "PROGREDINDO"
    assert [e["exercise_name"] for e in group["performance_exercises"]] == ["Extensora", "Agachamento"]

    so_extensora = build_group_performance(rows[:2])[QUAD]
    assert so_extensora["performance_status"] == "ESTAVEL"

    queda = build_group_performance([_e1rm_row("previous", 100.0), _e1rm_row("current", 94.0)])[QUAD]
    assert queda["performance_status"] == "REGREDINDO"


# ---------------------------------------------------------------------------
# Travas aprovadas em 2026-10-02: variacao atipica e ativacao minima.
# ---------------------------------------------------------------------------

DORSAL = 12
ADUTORES = 17


def test_constantes_documentadas():
    assert OUTLIER_CHANGE_PCT == 50.0
    assert MIN_ACTIVATION_SUM == 0.5


def test_atipico_e_descartado_mas_continua_listado():
    """Cenario sintetico baseado em caso real: remada articulada 45 -> 100 kg
    (+122%) inflava Costas
    (dorsal). Sai do calculo, fica na lista marcada para conferir."""
    rows = [
        _e1rm_row("previous", 100.0, exercise_id=30, group=DORSAL, name="Puxada"),
        _e1rm_row("current", 106.0, exercise_id=30, group=DORSAL, name="Puxada"),
        _e1rm_row("previous", 45.0, exercise_id=31, group=DORSAL, name="Remada articulada"),
        _e1rm_row("current", 100.0, exercise_id=31, group=DORSAL, name="Remada articulada"),
    ]
    group = build_group_performance(rows)[DORSAL]
    assert group["performance_change_pct"] == 6.0
    assert group["performance_status"] == "PROGREDINDO"
    assert group["performance_exercises_compared"] == 1
    assert group["performance_exercises_excluded"] == 1
    assert group["performance_activation_sum"] == 1.0
    assert group["performance_insufficient_reason"] is None
    usado, atipico = group["performance_exercises"]
    assert (usado["exercise_name"], usado["outlier"], usado["excluded_reason"]) == ("Puxada", False, None)
    assert (atipico["exercise_name"], atipico["outlier"], atipico["excluded_reason"]) == (
        "Remada articulada",
        True,
        OUTLIER_CHANGE,
    )
    assert atipico["change_pct"] == 122.2


def test_limite_do_atipico_e_estrito_nos_dois_sentidos():
    exato = build_group_performance([_e1rm_row("previous", 100.0), _e1rm_row("current", 150.0)])[QUAD]
    assert exato["performance_exercises_excluded"] == 0
    assert exato["performance_change_pct"] == 50.0

    queda = build_group_performance([_e1rm_row("previous", 100.0), _e1rm_row("current", 49.0)])[QUAD]
    assert queda["performance_exercises_excluded"] == 1
    assert queda["performance_status"] == "DADOS_INSUFICIENTES"


def test_todos_atipicos_e_dados_insuficientes():
    rows = [
        _e1rm_row("previous", 40.0, exercise_id=31, group=DORSAL, name="Remada articulada"),
        _e1rm_row("current", 100.0, exercise_id=31, group=DORSAL, name="Remada articulada"),
        _e1rm_row("previous", 100.0, exercise_id=30, group=DORSAL, name="Puxada"),
        _e1rm_row("current", 40.0, exercise_id=30, group=DORSAL, name="Puxada"),
    ]
    group = build_group_performance(rows)[DORSAL]
    assert group["performance_status"] == "DADOS_INSUFICIENTES"
    assert group["performance_change_pct"] is None
    assert group["performance_exercises_compared"] == 0
    assert group["performance_exercises_excluded"] == 2
    assert group["performance_activation_sum"] == 0.0
    assert group["performance_insufficient_reason"] == ALL_OUTLIERS
    assert all(e["outlier"] for e in group["performance_exercises"])

    # O merge no painel mantem a leitura (nao cai no `insufficient()` vazio).
    (item,) = build_group_progression(_tonnage_rows(1000.0, 1000.0, group=DORSAL), None, {DORSAL: group})
    assert item["performance_status"] == "DADOS_INSUFICIENTES"
    assert len(item["performance_exercises"]) == 2


def test_ativacao_0_2_e_dados_insuficientes():
    """Cenario sintetico baseado em caso real: Adutores em 7 dias, +100% de um unico exercicio a 20%."""
    rows = [
        _e1rm_row("previous", 50.0, group=ADUTORES, weight=0.2, name="Agachamento sumo"),
        _e1rm_row("current", 74.0, group=ADUTORES, weight=0.2, name="Agachamento sumo"),
    ]
    group = build_group_performance(rows)[ADUTORES]
    assert group["performance_status"] == "DADOS_INSUFICIENTES"
    assert group["performance_change_pct"] is None
    assert group["performance_insufficient_reason"] == LOW_ACTIVATION
    assert group["performance_activation_sum"] == 0.2
    assert group["performance_exercises_compared"] == 1
    assert group["performance_exercises"][0]["outlier"] is False


def test_ativacao_0_5_exata_classifica():
    """0,2 + 0,3 = 0,49999... em ponto flutuante: o limiar ainda classifica."""
    rows = [
        _e1rm_row("previous", 100.0, exercise_id=40, group=ADUTORES, weight=0.2, name="A"),
        _e1rm_row("current", 110.0, exercise_id=40, group=ADUTORES, weight=0.2, name="A"),
        _e1rm_row("previous", 100.0, exercise_id=41, group=ADUTORES, weight=0.3, name="B"),
        _e1rm_row("current", 110.0, exercise_id=41, group=ADUTORES, weight=0.3, name="B"),
    ]
    group = build_group_performance(rows)[ADUTORES]
    assert group["performance_activation_sum"] == 0.5
    assert group["performance_status"] == "PROGREDINDO"
    assert group["performance_change_pct"] == 10.0
    assert group["performance_insufficient_reason"] is None

    unico = build_group_performance(
        [_e1rm_row("previous", 100.0, weight=0.5), _e1rm_row("current", 102.0, weight=0.5)]
    )[QUAD]
    assert unico["performance_status"] == "ESTAVEL"


def test_atipico_e_ativacao_minima_juntos():
    """Sem o atipico (100% de ativacao) sobra so 0,4: DADOS_INSUFICIENTES por
    ativacao - o atipico nao conta para a soma."""
    rows = [
        _e1rm_row("previous", 45.0, exercise_id=31, group=DORSAL, weight=1.0, name="Remada articulada"),
        _e1rm_row("current", 100.0, exercise_id=31, group=DORSAL, weight=1.0, name="Remada articulada"),
        _e1rm_row("previous", 100.0, exercise_id=58, group=DORSAL, weight=0.4, name="Levantamento terra"),
        _e1rm_row("current", 108.0, exercise_id=58, group=DORSAL, weight=0.4, name="Levantamento terra"),
    ]
    group = build_group_performance(rows)[DORSAL]
    assert group["performance_status"] == "DADOS_INSUFICIENTES"
    assert group["performance_insufficient_reason"] == LOW_ACTIVATION
    assert group["performance_activation_sum"] == 0.4
    assert (group["performance_exercises_compared"], group["performance_exercises_excluded"]) == (1, 1)
    assert [e["outlier"] for e in group["performance_exercises"]] == [False, True]


def test_grupo_sem_leitura_explica_o_motivo():
    (item,) = build_group_progression(_tonnage_rows(1000.0, 1000.0), None, {})
    assert item["performance_insufficient_reason"] == NO_COMPARABLE_EXERCISE
    assert item["performance_exercises_excluded"] == 0


def test_volume_status_neutro():
    assert volume_status("PROGREDINDO") == VOLUME_UP
    assert volume_status("ESTAVEL") == VOLUME_STABLE
    assert volume_status("REGREDINDO") == VOLUME_DOWN
    for passthrough in ("DADOS_INSUFICIENTES", "NOVO", "SEM_DADOS"):
        assert volume_status(passthrough) == passthrough


# ---------------------------------------------------------------------------
# A consulta REAL num SQLite em memoria (mesma tecnica de
# test_muscle_group_progression.py): prova o que entra no e1RM.
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE activities (id INTEGER PRIMARY KEY, user_id INTEGER, local_date TEXT, deload_source TEXT);
CREATE TABLE exercises (id INTEGER PRIMARY KEY, activity_id INTEGER, mapping_id INTEGER,
                        original_name TEXT, equipment_id INTEGER);
CREATE TABLE exercise_sets (id INTEGER PRIMARY KEY, exercise_id INTEGER, set_number INTEGER,
                            set_type TEXT, reps INTEGER, weight_kg REAL, rpe REAL,
                            measure TEXT NOT NULL DEFAULT 'reps', duration_seconds INTEGER,
                            reps_pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE exercise_legacy_id_map (old_exercise_id INTEGER, canonical_exercise_id INTEGER);
CREATE TABLE gym_equipment (id INTEGER PRIMARY KEY, equipment_id INTEGER);
CREATE TABLE equipment_exercises (equipment_id INTEGER, exercise_id INTEGER, load_convention TEXT,
                                  is_active INTEGER);
CREATE TABLE exercise_muscle_contributions (exercise_id INTEGER, muscle_group_id INTEGER,
                                            stimulus_weight REAL, is_active INTEGER);
CREATE TABLE muscle_groups (id INTEGER PRIMARY KEY, name TEXT);
"""


def _run_real_query(sets_by_session, windows, *, user_id=1, gym_equipment_id=7):
    """`sets_by_session`: {local_date: [(set_type, reps, weight_kg), ...]} -
    Hack squat (exercicio 22: 100% quadriceps, 50% gluteos) na maquina 50."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO gym_equipment VALUES (7, 50)")
    conn.execute("INSERT INTO gym_equipment VALUES (8, 18)")
    conn.execute("INSERT INTO equipment_exercises VALUES (50, 22, 'total', 1)")
    conn.execute("INSERT INTO exercise_muscle_contributions VALUES (22, 4, 1.0, 1)")
    conn.execute("INSERT INTO exercise_muscle_contributions VALUES (22, 9, 0.5, 1)")
    set_id = 0
    for activity_id, (local_date, sets) in enumerate(sorted(sets_by_session.items()), start=1):
        equipment = gym_equipment_id(local_date) if callable(gym_equipment_id) else gym_equipment_id
        conn.execute("INSERT INTO activities VALUES (?, ?, ?, NULL)", (activity_id, user_id, local_date.isoformat()))
        conn.execute("INSERT INTO exercises VALUES (?, ?, 22, 'Hack squat', ?)", (activity_id, activity_id, equipment))
        for number, serie in enumerate(sets, start=1):
            # (set_type, reps, weight) ou (set_type, reps, weight, rpe). Sem RPE
            # explicito vale RPE 10 (RIR 0): o Epley+RIR fica igual ao Epley.
            set_type, reps, weight = serie[:3]
            rpe = serie[3] if len(serie) > 3 else 10.0
            # 5º elemento: reps_pending (Modo Intensivo, 092).
            pending = serie[4] if len(serie) > 4 else 0
            set_id += 1
            conn.execute(
                "INSERT INTO exercise_sets (id, exercise_id, set_number, set_type, reps, weight_kg, rpe, reps_pending) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (set_id, activity_id, number, set_type, reps, weight, rpe, pending),
            )

    def fake_fetch_all(sql, params=None):
        sqlite_params = tuple(p.isoformat() if isinstance(p, date) else p for p in (params or ()))
        return [dict(r) for r in conn.execute(re.sub(r"%s", "?", sql), sqlite_params).fetchall()]

    original = analytics._fetch_all
    analytics._fetch_all = fake_fetch_all
    try:
        return analytics._fetch_group_e1rm_rows(user_id, windows, "eqex.load_convention")
    finally:
        analytics._fetch_all = original
        conn.close()


def test_query_real_epley_so_series_de_trabalho():
    """Melhor e1RM = Epley da melhor serie de TRABALHO. Aquecimento, ajuste,
    mini-blocos de rest-pause, drop e backoff ficam de fora (um mini-bloco de
    5 reps na carga da serie nao mede forca maxima)."""
    windows = comparison_windows(date(2026, 10, 2), 7)
    previous = [("working", 10, 120.0)] * 3  # Epley 160
    current = [
        ("warmup", 15, 140.0),  # Epley 210: ficaria acima, mas e aquecimento
        ("feeler", 8, 150.0),
        ("working", 8, 150.0),  # Epley 190
        ("rest_pause", 5, 150.0),
        ("drop_set", 20, 160.0),
        ("backoff", 15, 150.0),
        ("working", 0, 200.0),  # sem reps: nao entra
    ]
    rows = _run_real_query({date(2026, 9, 22): previous, date(2026, 9, 29): current}, windows)
    by_key = {(r["period"], r["muscle_group_id"]): r for r in rows}
    assert by_key[("previous", 4)]["best_e1rm_kg"] == 160.0
    assert by_key[("current", 4)]["best_e1rm_kg"] == 190.0
    assert by_key[("current", 9)]["stimulus_weight"] == 0.5
    assert {r["load_convention"] for r in rows} == {"total"}
    assert {r["equipment_id"] for r in rows} == {50}  # equipamento canonico, nao o id da academia

    performance = build_group_performance(rows)
    assert performance[4]["performance_change_pct"] == 18.8
    assert performance[9]["performance_change_pct"] == 18.8


def test_query_real_troca_de_maquina_vira_dados_insuficientes():
    windows = comparison_windows(date(2026, 10, 2), 7)
    rows = _run_real_query(
        {date(2026, 9, 22): [("working", 10, 120.0)], date(2026, 9, 29): [("working", 10, 80.0)]},
        windows,
        gym_equipment_id=lambda d: 7 if d < date(2026, 9, 26) else 8,
    )
    assert {r["equipment_id"] for r in rows} == {50, 18}
    assert build_group_performance(rows) == {}


def test_query_sem_null_no_group_by_antes_da_045():
    captured = {}

    def fake_fetch_all(sql, params=None):
        captured["sql"], captured["params"] = sql, params
        return []

    windows = comparison_windows(date(2026, 9, 30), 28)
    original = analytics._fetch_all
    analytics._fetch_all = fake_fetch_all
    try:
        analytics._fetch_group_e1rm_rows(7, windows, "NULL")
    finally:
        analytics._fetch_all = original
    janela = (7, date(2026, 8, 6), date(2026, 9, 30))
    assert captured["params"] == (date(2026, 9, 3), *janela, *janela)
    assert "NULL AS load_convention" in captured["sql"]
    group_by = captured["sql"].split("GROUP BY", 1)[1].split("UNION ALL", 1)[0]
    assert "NULL" not in group_by
    assert "CURDATE" not in captured["sql"]
    # M76 Lote 3a: a formula nao mora mais no SQL - so colunas cruas.
    assert "/ 30" not in captured["sql"] and "10 - es.rpe" not in captured["sql"]


def test_endpoint_devolve_as_duas_dimensoes(monkeypatch):
    windows_today = date(2026, 10, 2)
    monkeypatch.setattr(analytics, "_user_today", lambda user_id: windows_today)
    monkeypatch.setattr(analytics, "_group_phases", lambda user_id, *_: {})
    # M58: a rota detecta a convencao UMA vez e passa as duas leituras.
    monkeypatch.setattr(analytics, "_load_convention_expr", lambda: "eqex.load_convention")
    monkeypatch.setattr(
        analytics,
        "_fetch_group_tonnage_rows",
        lambda user_id, windows, lc=None: _tonnage_rows(2400.0, 4000.0) if lc == "eqex.load_convention" else [],
    )
    monkeypatch.setattr(
        analytics,
        "_fetch_group_e1rm_rows",
        lambda user_id, windows, lc=None: [_e1rm_row("previous", 160.0), _e1rm_row("current", 180.0)]
        if lc == "eqex.load_convention"
        else [],
    )

    class _Identity:
        user_id = 1

    payload = analytics.dashboard_progression_by_muscle_group(
        days=28, date_from=None, date_to=None, identity=_Identity()
    )
    assert payload["performance_basis"] == PERFORMANCE_BASIS
    assert payload["stable_band_pct"] == 5.0
    (item,) = payload["items"]
    assert (item["volume_status"], item["performance_status"]) == (VOLUME_DOWN, "PROGREDINDO")


# ---------------------------------------------------------------------------
# 2026-10-06 (decisao do usuario, meio-termo): Epley+RIR quando ha RPE,
# RIR 0 sem RPE (series sem RPE continuam contando) e teto de 12 reps.
# ---------------------------------------------------------------------------


def test_query_real_paridade_com_estimated_1rm_com_e_sem_rpe():
    from app.intelligence.muscle_recovery_service import MuscleRecoveryService

    windows = comparison_windows(date(2026, 10, 2), 7)
    rows = _run_real_query(
        {
            date(2026, 9, 22): [("working", 10, 100.0, 8.0)],  # com RPE: RIR 2
            date(2026, 9, 29): [("working", 8, 110.0, None)],  # sem RPE: RIR 0
        },
        windows,
    )
    by_key = {(r["period"], r["muscle_group_id"]): r for r in rows}
    # com RPE: identico a `estimated_1rm`
    assert round(by_key[("previous", 4)]["best_e1rm_kg"], 6) == round(
        MuscleRecoveryService.estimated_1rm(100.0, 10, 8.0), 6
    )
    # sem RPE: RIR 0 = `estimated_1rm` com RPE 10 (Epley puro)
    assert round(by_key[("current", 4)]["best_e1rm_kg"], 6) == round(
        MuscleRecoveryService.estimated_1rm(110.0, 8, 10.0), 6
    )


def test_query_real_serie_sem_rpe_conta_e_acima_de_12_reps_nao():
    windows = comparison_windows(date(2026, 10, 2), 7)
    rows = _run_real_query(
        {
            date(2026, 9, 22): [("working", 10, 100.0, 10.0)],
            date(2026, 9, 29): [
                ("working", 15, 150.0, 10.0),  # Epley 225, mas 15 reps: nao confiavel
                ("working", 10, 120.0, None),  # sem RPE: RIR 0 -> 160, conta
                ("working", 12, 100.0, 10.0),  # 140
            ],
        },
        windows,
    )
    by_key = {(r["period"], r["muscle_group_id"]): r for r in rows}
    assert by_key[("current", 4)]["best_e1rm_kg"] == 160.0


def test_query_real_sem_serie_ate_12_reps_na_janela_nao_compara():
    windows = comparison_windows(date(2026, 10, 2), 7)
    rows = _run_real_query(
        {
            date(2026, 9, 22): [("working", 10, 100.0, 10.0)],
            date(2026, 9, 29): [("working", 15, 120.0, 9.0), ("working", 13, 120.0, None)],
        },
        windows,
    )
    assert {r["period"] for r in rows} == {"previous"}
    assert build_group_performance(rows) == {}


# ---------------------------------------------------------------------------
# M76 Lote 3a: e1RM calculado em Python pelo dono (`domain/e1rm`), politica
# RIR_ZERO; o SQL so traz colunas cruas, em UMA consulta.
# ---------------------------------------------------------------------------


def test_query_real_usa_o_dono_com_rir_zero():
    from app.domain.e1rm import MissingRpePolicy, reliable_e1rm

    windows = comparison_windows(date(2026, 10, 2), 7)
    rows = _run_real_query(
        {
            date(2026, 9, 22): [("working", 10, 100.0, 8.0), ("working", 10, 100.0, 9.0)],
            date(2026, 9, 29): [("working", 8, 110.0, None), ("working", 12, 100.0, 10.0)],
        },
        windows,
    )
    by_key = {(r["period"], r["muscle_group_id"]): r for r in rows}
    assert by_key[("previous", 4)]["best_e1rm_kg"] == reliable_e1rm(100.0, 10, 8.0)
    assert by_key[("current", 4)]["best_e1rm_kg"] == max(
        reliable_e1rm(110.0, 8, None, missing_rpe=MissingRpePolicy.RIR_ZERO), reliable_e1rm(100.0, 12, 10.0)
    )
    # uma linha por (janela, grupo): os grupos vem da parte `contribution`
    assert sorted((r["period"], r["muscle_group_id"], r["stimulus_weight"]) for r in rows) == [
        ("current", 4, 1.0),
        ("current", 9, 0.5),
        ("previous", 4, 1.0),
        ("previous", 9, 0.5),
    ]


def test_uma_consulta_so():
    chamadas = []

    def fake_fetch_all(sql, params=None):
        chamadas.append(sql)
        return []

    original = analytics._fetch_all
    analytics._fetch_all = fake_fetch_all
    try:
        assert analytics._fetch_group_e1rm_rows(7, comparison_windows(date(2026, 9, 30), 28), "NULL") == []
    finally:
        analytics._fetch_all = original
    assert len(chamadas) == 1


def test_query_real_serie_pendente_nao_entra_no_e1rm():
    """Modo Intensivo (092): série com reps pendentes (reps NULL) não vira
    e1RM até a confirmação - aqui com reps preenchidas de propósito, para
    provar que quem exclui é o predicado `reps_pending` (não o `reps > 0`)."""
    windows = comparison_windows(date(2026, 10, 2), 7)
    base = {
        date(2026, 9, 22): [("working", 10, 100.0, 8.0)],
        date(2026, 9, 29): [("working", 10, 100.0, 8.0)],
    }
    com_pendente = {
        date(2026, 9, 22): [("working", 10, 100.0, 8.0)],
        date(2026, 9, 29): [("working", 10, 100.0, 8.0), ("working", 10, 200.0, None, 1)],
    }
    assert _run_real_query(com_pendente, windows) == _run_real_query(base, windows)
