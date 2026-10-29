"""M7/M50: progressao por grupo muscular (parte pura + contrato do endpoint).

M50: a classificacao compara a tonelagem POR SESSAO das duas janelas. A janela
atual termina hoje (o treino do dia pode nao ter acontecido) e o corte parte o
ciclo do Modelo no meio; comparar TOTAIS dava "regredindo" so por haver menos
sessoes ate agora.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from app import analytics
from app.set_types import sql_not_preparatory
from app.intelligence.muscle_group_progression import (
    DADOS_INSUFICIENTES,
    MIN_SESSIONS_PER_WINDOW,
    build_group_progression,
    classify_change,
    comparison_windows,
)


def _group(current_sessions: int, previous_sessions: int, *, current_avg: float = 1000.0, previous_avg: float = 1000.0):
    rows = []
    if current_sessions:
        rows.append(
            {"period": "current", "muscle_group_id": 1, "muscle_group_name": "Peito",
             "tonnage_kg": current_avg * current_sessions, "sets_count": 4 * current_sessions,
             "sessions_count": current_sessions}
        )
    if previous_sessions:
        rows.append(
            {"period": "previous", "muscle_group_id": 1, "muscle_group_name": "Peito",
             "tonnage_kg": previous_avg * previous_sessions, "sets_count": 4 * previous_sessions,
             "sessions_count": previous_sessions}
        )
    (item,) = build_group_progression(rows)
    return item


def test_classify_change_faixas_por_sessao():
    # 4 sessoes cada: media 1100 x 1000 (total e por sessao andam juntos).
    assert classify_change(4400, 4000, 4, 4) == {
        "classification": "PROGREDINDO", "change_pct": 10.0, "total_change_pct": 10.0,
    }
    assert classify_change(4196, 4000, 4, 4)["classification"] == "ESTAVEL"
    assert classify_change(3600, 4000, 4, 4) == {
        "classification": "REGREDINDO", "change_pct": -10.0, "total_change_pct": -10.0,
    }
    assert classify_change(500, 0, 2, 0)["classification"] == "NOVO"
    assert classify_change(0, 0, 0, 0)["classification"] == "SEM_DADOS"


def test_parou_de_treinar_o_grupo_nao_e_regressao():
    # Antes do M50: REGREDINDO -100%. Nenhuma sessao e frequencia, nao desempenho.
    assert classify_change(0, 800, 0, 4) == {
        "classification": DADOS_INSUFICIENTES, "change_pct": None, "total_change_pct": None,
    }


def test_janela_atual_no_inicio_fica_insuficiente():
    # Nenhuma ou uma sessao do grupo ate agora: nunca "regredindo".
    assert _group(0, 8)["classification"] == DADOS_INSUFICIENTES
    uma = _group(1, 8, current_avg=500.0)  # mesmo uma sessao fraca nao classifica
    assert uma["classification"] == DADOS_INSUFICIENTES and uma["change_pct"] is None
    assert MIN_SESSIONS_PER_WINDOW == 2


def test_janela_atual_no_meio_com_mesmo_desempenho_e_estavel():
    # O falso positivo do M50: metade das sessoes com a MESMA tonelagem por
    # sessao. Total 4000 x 8000 = -50% (era REGREDINDO); por sessao = 0%.
    item = _group(4, 8)
    assert (item["current_tonnage_kg"], item["previous_tonnage_kg"]) == (4000.0, 8000.0)
    assert (item["current_tonnage_per_session_kg"], item["previous_tonnage_per_session_kg"]) == (1000.0, 1000.0)
    assert item["classification"] == "ESTAVEL" and item["change_pct"] == 0.0
    assert item["total_change_pct"] == -50.0  # contexto: o total caiu, o desempenho nao


def test_uma_sessao_a_menos_por_causa_do_corte_nao_regride():
    # Janela de 28 dias cortando o ciclo: 7 sessoes contra 8, mesmo desempenho.
    # Total = -12,5% (fora da faixa de 5%); por sessao = 0%.
    item = _group(7, 8)
    assert item["classification"] == "ESTAVEL" and item["change_pct"] == 0.0


def test_janela_completa_classifica_pelo_desempenho():
    assert _group(8, 8)["classification"] == "ESTAVEL"
    assert _group(8, 8, current_avg=900.0)["classification"] == "REGREDINDO"
    assert _group(8, 8, current_avg=1100.0)["classification"] == "PROGREDINDO"


def test_regressao_real_aparece_mesmo_com_janela_parcial():
    # Menos sessoes E cada uma mais fraca: continua REGREDINDO.
    item = _group(3, 8, current_avg=850.0)
    assert item["classification"] == "REGREDINDO" and item["change_pct"] == -15.0


def test_sinais_discordantes_ficam_estaveis():
    # Grupo perdeu exposicoes INDIRETAS leves: media por sessao dispara com o
    # total identico (medido no DEV: biceps +122% por sessao, total 0%).
    item = _group(9, 20, current_avg=3658.0, previous_avg=1645.0)
    assert item["change_pct"] > 100 and abs(item["total_change_pct"]) < 5
    assert item["classification"] == "ESTAVEL"
    # Menos sessoes, cada uma mais forte, total menor: nao e regressao.
    item = _group(8, 13, current_avg=4713.0, previous_avg=3681.0)
    assert item["change_pct"] > 5 and item["total_change_pct"] < -5
    assert item["classification"] == "ESTAVEL"
    # Mais sessoes, cada uma mais fraca, total maior: nao e progressao.
    assert _group(8, 4, current_avg=800.0)["classification"] == "ESTAVEL"


def test_amostra_de_calculo_ponderada():
    # Supino 3x10x60kg: peito 1.0, ombro 0.4, triceps 0.3 - duas sessoes.
    tonnage = 3 * 10 * 60 * 2
    rows = [
        {"period": "current", "muscle_group_id": 1, "muscle_group_name": "Peito", "tonnage_kg": tonnage * 1.0,
         "sets_count": 6, "sessions_count": 2},
        {"period": "previous", "muscle_group_id": 1, "muscle_group_name": "Peito", "tonnage_kg": 3000.0,
         "sets_count": 6, "sessions_count": 2},
        {"period": "current", "muscle_group_id": 2, "muscle_group_name": "Ombro", "tonnage_kg": tonnage * 0.4,
         "sets_count": 6, "sessions_count": 2},
    ]
    items = build_group_progression(rows, {1: {"phase": "ACUMULACAO", "phase_status": "active"}})
    peito, ombro = items
    assert (peito["muscle_group_name"], peito["current_tonnage_kg"], peito["previous_tonnage_kg"]) == ("Peito", 3600.0, 3000.0)
    assert (peito["current_sessions"], peito["previous_sessions"]) == (2, 2)
    assert peito["classification"] == "PROGREDINDO" and peito["change_pct"] == 20.0
    assert peito["phase"] == "ACUMULACAO"
    assert ombro["current_tonnage_kg"] == 1440.0 and ombro["classification"] == "NOVO"
    assert ombro["previous_tonnage_per_session_kg"] is None
    assert ombro["phase"] is None


def test_janelas_do_mesmo_tamanho_e_contiguas():
    w = comparison_windows(date(2026, 9, 30), 7)
    assert w == {
        "current_from": date(2026, 9, 24),
        "current_to": date(2026, 9, 30),
        "previous_from": date(2026, 9, 17),
        "previous_to": date(2026, 9, 23),
    }
    w28 = comparison_windows(date(2026, 9, 30), 28)
    assert (w28["current_to"] - w28["current_from"]).days + 1 == 28
    assert (w28["previous_to"] - w28["previous_from"]).days + 1 == 28


def test_query_sem_preparatorias_janelas_explicitas_e_sessoes():
    captured = {}

    def fake_fetch_all(sql, params=None):
        captured["sql"], captured["params"] = sql, params
        return []

    windows = comparison_windows(date(2026, 9, 30), 30)
    original = analytics._fetch_all
    analytics._fetch_all = fake_fetch_all
    try:
        analytics._fetch_group_tonnage_rows(7, windows, "NULL")
    finally:
        analytics._fetch_all = original
    assert captured["params"] == (date(2026, 9, 1), 7, date(2026, 8, 2), date(2026, 9, 30))
    assert "CURDATE" not in captured["sql"]  # "hoje" vem do fuso do usuario, nao do banco (UTC)
    # Mesmo filtro do volume por grupo (Fase 5): so aquecimento/ajuste saem.
    assert sql_not_preparatory("es.set_type") in captured["sql"]
    assert "= 'working'" not in captured["sql"]
    assert "stimulus_weight" in captured["sql"] and "is_active = 1" in captured["sql"]
    assert "COUNT(DISTINCT" in captured["sql"] and "sessions_count" in captured["sql"]


# ---------------------------------------------------------------------------
# A consulta REAL, executada num SQLite em memoria (so funcoes que existem nos
# dois bancos: COALESCE/LOWER/TRIM/CASE/COUNT DISTINCT). Prova o que entra na
# tonelagem, nao so o texto do SQL.
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE activities (id INTEGER PRIMARY KEY, user_id INTEGER, local_date TEXT);
CREATE TABLE exercises (id INTEGER PRIMARY KEY, activity_id INTEGER, mapping_id INTEGER, equipment_id INTEGER);
CREATE TABLE exercise_sets (id INTEGER PRIMARY KEY, exercise_id INTEGER, set_number INTEGER,
                            set_type TEXT, reps INTEGER, weight_kg REAL,
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


# Maquinas (gym_equipment.id -> convencao do par com o exercicio 22). `None` =
# par cadastrado sem convencao (NULL = total); GE_SEM_PAR = maquina sem par.
GE_TOTAL, GE_NULL, GE_ASSISTED, GE_BODYWEIGHT, GE_SEM_PAR = 7, 8, 9, 10, 11


def _run_real_query(
    sets_by_session,
    windows,
    *,
    user_id=1,
    gym_equipment_id=GE_TOTAL,
    convention_expr="eqex.load_convention",
    reader_id=None,
):
    """`sets_by_session`: {local_date: [(set_type, reps, weight_kg), ...]} -
    um exercicio (Hack, 100% quadriceps) por sessao, gravado por `user_id` e
    lido por `reader_id` (default: o mesmo). `gym_equipment_id` pode ser
    funcao da data (maquina por sessao)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO muscle_groups VALUES (4, 'Quadriceps')")
    conn.execute("INSERT INTO exercise_muscle_contributions VALUES (22, 4, 1.0, 1)")
    for ge_id, equipment_id, convention in (
        (GE_TOTAL, 50, "total"),
        (GE_NULL, 51, None),
        (GE_ASSISTED, 52, "assisted"),
        (GE_BODYWEIGHT, 53, "bodyweight"),
        (GE_SEM_PAR, 99, None),
    ):
        conn.execute("INSERT INTO gym_equipment VALUES (?, ?)", (ge_id, equipment_id))
        if ge_id != GE_SEM_PAR:
            conn.execute("INSERT INTO equipment_exercises VALUES (?, 22, ?, 1)", (equipment_id, convention))
    set_id = 0
    for activity_id, (local_date, sets) in enumerate(sorted(sets_by_session.items()), start=1):
        equipment = gym_equipment_id(local_date) if callable(gym_equipment_id) else gym_equipment_id
        conn.execute("INSERT INTO activities VALUES (?, ?, ?)", (activity_id, user_id, local_date.isoformat()))
        conn.execute("INSERT INTO exercises VALUES (?, ?, 22, ?)", (activity_id, activity_id, equipment))
        for number, serie in enumerate(sets, start=1):
            # (set_type, reps, weight) ou + reps_pending (Modo Intensivo, 092).
            set_type, reps, weight = serie[:3]
            pending = serie[3] if len(serie) > 3 else 0
            set_id += 1
            conn.execute(
                "INSERT INTO exercise_sets (id, exercise_id, set_number, set_type, reps, weight_kg, reps_pending) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (set_id, activity_id, number, set_type, reps, weight, pending),
            )

    def fake_fetch_all(sql, params=None):
        sqlite_params = tuple(p.isoformat() if isinstance(p, date) else p for p in (params or ()))
        return [dict(r) for r in conn.execute(re.sub(r"%s", "?", sql), sqlite_params).fetchall()]

    original = analytics._fetch_all
    analytics._fetch_all = fake_fetch_all
    try:
        reader = user_id if reader_id is None else reader_id
        return analytics._fetch_group_tonnage_rows(reader, windows, convention_expr)
    finally:
        analytics._fetch_all = original
        conn.close()


def test_tecnica_avancada_entra_na_tonelagem_e_preparatorias_nao():
    """Cenario sintetico baseado em caso real (Hack Squat, set/2026): a janela anterior
    tinha 3 series de trabalho; a atual, depois da troca de Modelo, 2 series
    de trabalho + mini-blocos de rest-pause, com a MESMA carga e reps. Antes
    (filtro `= 'working'`) a tonelagem atual caia 33% e o grupo "regredia";
    o trabalho do rest-pause e real e entra como no volume por grupo.
    Aquecimento e serie de ajuste continuam fora, e os mini-blocos nao
    viram "series" (sets_count conta so as que nao sao aplicacao)."""
    windows = comparison_windows(date(2026, 10, 2), 7)
    previous = [("working", 10, 110.0)] * 3
    current = [
        ("warmup", 15, 40.0),
        ("feeler", 6, 80.0),
        ("working", 10, 110.0),
        ("working", 10, 110.0),
        ("rest_pause", 5, 110.0),
        ("rest_pause", 5, 110.0),
    ]
    rows = _run_real_query(
        {
            date(2026, 9, 21): previous,
            date(2026, 9, 24): previous,
            date(2026, 9, 28): current,
            date(2026, 10, 1): current,
        },
        windows,
    )
    by_period = {r["period"]: r for r in rows}
    assert by_period["previous"]["tonnage_kg"] == 2 * 3300
    assert by_period["current"]["tonnage_kg"] == 2 * 3300  # 2x1100 + 2x550 por sessao
    assert by_period["current"]["sets_count"] == 4  # 2 series de trabalho x 2 sessoes
    assert by_period["previous"]["sets_count"] == 6
    assert by_period["current"]["sessions_count"] == 2

    (item,) = build_group_progression(rows)
    assert item["classification"] == "ESTAVEL"
    assert item["change_pct"] == 0.0


def test_legado_sem_set_type_continua_sendo_trabalho():
    windows = comparison_windows(date(2026, 10, 2), 7)
    rows = _run_real_query(
        {date(2026, 9, 21): [(None, 10, 50.0), ("", 10, 50.0)], date(2026, 9, 28): [("WORKING ", 10, 50.0)]},
        windows,
    )
    by_period = {r["period"]: r for r in rows}
    assert by_period["previous"]["tonnage_kg"] == 1000
    assert by_period["current"]["tonnage_kg"] == 500


# ---------------------------------------------------------------------------
# M58 - convencao de carga na tonelagem (mesma regra do relatorio Markdown)
# ---------------------------------------------------------------------------


def test_assistido_e_peso_corporal_ficam_fora_da_tonelagem_total_e_nulo_entram():
    """Uma sessao por maquina, todas na janela atual. `total` e par sem
    convencao (NULL) contam; `assisted` e `bodyweight` nao entram na tonelagem
    nem no denominador de sessoes, mas continuam em `sets_count` e sao
    declarados em `tonnage_excluded_sets`. Maquina sem par cadastrado = total."""
    windows = comparison_windows(date(2026, 10, 2), 7)
    maquina = {
        date(2026, 9, 26): GE_TOTAL,
        date(2026, 9, 27): GE_NULL,
        date(2026, 9, 28): GE_ASSISTED,
        date(2026, 9, 29): GE_BODYWEIGHT,
        date(2026, 9, 30): GE_SEM_PAR,
    }
    rows = _run_real_query(
        {dia: [("working", 10, 100.0), ("working", 10, 100.0)] for dia in maquina},
        windows,
        gym_equipment_id=lambda dia: maquina[dia],
    )
    (current,) = rows
    assert current["tonnage_kg"] == 3 * 2000  # total + NULL + sem par
    assert current["sessions_count"] == 3
    assert current["sets_count"] == 10  # volume continua contando tudo
    assert current["tonnage_excluded_sets"] == 4  # 2 assistidas + 2 peso corporal


def test_sem_a_coluna_045_tudo_conta_como_total():
    """Antes da migracao 045 a deteccao devolve o literal `NULL`: nada sai."""
    windows = comparison_windows(date(2026, 10, 2), 7)
    rows = _run_real_query(
        {date(2026, 9, 28): [("working", 10, 100.0)]},
        windows,
        gym_equipment_id=GE_ASSISTED,
        convention_expr="NULL",
    )
    (current,) = rows
    assert current["tonnage_kg"] == 1000
    assert current["tonnage_excluded_sets"] == 0


def test_grupo_so_com_assistido_fica_sem_dados_e_declara_as_series():
    windows = comparison_windows(date(2026, 10, 2), 7)
    rows = _run_real_query(
        {
            date(2026, 9, 21): [("working", 8, 30.0)],
            date(2026, 9, 24): [("working", 8, 30.0)],
            date(2026, 9, 28): [("working", 8, 30.0)],
            date(2026, 10, 1): [("working", 8, 30.0)],
        },
        windows,
        gym_equipment_id=GE_ASSISTED,
    )
    (item,) = build_group_progression(rows)
    assert item["current_tonnage_kg"] == 0.0 and item["previous_tonnage_kg"] == 0.0
    assert item["classification"] == "SEM_DADOS"
    assert item["current_tonnage_excluded_sets"] == 2
    assert item["previous_tonnage_excluded_sets"] == 2


def test_query_real_isola_usuario():
    """IDOR: series gravadas pelo usuario 1 nao aparecem na leitura do 2."""
    windows = comparison_windows(date(2026, 10, 2), 7)
    sessoes = {date(2026, 9, 28): [("working", 10, 100.0)]}
    assert _run_real_query(sessoes, windows, user_id=1)[0]["tonnage_kg"] == 1000
    assert _run_real_query(sessoes, windows, user_id=1, reader_id=2) == []


def test_serie_com_reps_pendentes_fica_fora_da_tonelagem_e_das_series():
    """Modo Intensivo (092): a série concluída com um toque (reps NULL,
    `reps_pending = 1`) não existe para a tonelagem nem para a contagem de
    séries até a confirmação - nem pelo fallback de reps."""
    windows = comparison_windows(date(2026, 10, 2), 7)
    base = {
        date(2026, 9, 22): [("working", 10, 100.0)],
        date(2026, 9, 29): [("working", 10, 100.0)],
    }
    com_pendente = {
        date(2026, 9, 22): [("working", 10, 100.0)],
        date(2026, 9, 29): [("working", 10, 100.0), ("working", None, 300.0, 1)],
    }
    assert _run_real_query(com_pendente, windows) == _run_real_query(base, windows)
