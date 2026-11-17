"""Comparativo T-0..T-3 de /volume-grupamentos pelo MESMO slot do Modelo.

Dono: `intelligence/slot_session_comparison.py` (mapa FONTES_DA_VERDADE §3.5).
Rodar: cd api && python -m pytest tests/unit/test_slot_session_comparison.py -q
"""

from __future__ import annotations

import datetime as dt

from app.intelligence import slot_session_comparison as ssc
from app.intelligence.slot_session_comparison import (
    PERIOD_KEYS,
    SLOT_COMPARISON_SESSIONS,
    STATUS_NO_SLOT,
    STATUS_SAME_SLOT,
    SessionSlot,
    build_slot_comparison,
    select_slot_sessions,
    session_slot_from_row,
)

T = 7  # template_id


def _slot(sequence, name=None, template_id=T, slot_id=None):
    return SessionSlot(template_id=template_id, slot_sequence=sequence, slot_id=slot_id, slot_name=name)


def _vol(activity_id, day, group, volume, sets=3, rpe=None, **hierarchy):
    return {
        "activity_id": activity_id,
        "local_date": dt.date(2026, 10, day),
        "muscle_group": group,
        "volume_kg": volume,
        "sets_count": sets,
        "avg_rpe": rpe,
        **hierarchy,
    }


def _period_ids(row):
    return [row["by_period"][k]["activity_id"] if row["by_period"][k] else None for k in PERIOD_KEYS]


def test_constantes_do_dono():
    assert SLOT_COMPARISON_SESSIONS == 4
    assert PERIOD_KEYS == ("t0", "t1", "t2", "t3")


def test_t1_a_t3_so_do_mesmo_slot_do_t0():
    # Ciclo 1..5: o peitoral treina nos slots 1 e 3. T-0 (dia 20) no slot 1.
    slots = {1: _slot(1), 2: _slot(3), 3: _slot(1), 4: _slot(3), 5: _slot(1), 6: _slot(3), 7: _slot(1), 8: _slot(1)}
    rows = [_vol(aid, aid + 10, "Peitoral", 100 * aid) for aid in range(1, 8)]
    rows.append(_vol(8, 20, "Peitoral", 999))
    payload = build_slot_comparison(rows, [], slots)
    (row,) = payload["levels"]["muscle_group"]["volume"]
    assert row["comparison_status"] == STATUS_SAME_SLOT
    assert row["slot"]["slot_sequence"] == 1
    assert _period_ids(row) == [8, 7, 5, 3]  # nunca 6/4/2 (slot 3)


def test_menos_de_4_no_slot_deixa_colunas_vazias_sem_completar_com_outro_slot():
    slots = {1: _slot(2), 2: _slot(5), 3: _slot(5), 4: _slot(2)}
    rows = [_vol(aid, aid, "Costas", 10.0) for aid in (1, 2, 3, 4)]
    (row,) = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]["volume"]
    assert _period_ids(row) == [4, 1, None, None]


def test_t0_sem_slot_nao_compara():
    slots = {1: _slot(1), 2: _slot(1)}  # sessao 3 = treino livre
    rows = [_vol(aid, aid, "Bíceps", 10.0) for aid in (1, 2, 3)]
    (row,) = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]["volume"]
    assert row["comparison_status"] == STATUS_NO_SLOT
    assert row["slot"] is None
    assert _period_ids(row) == [3, None, None, None]


def test_sessao_sem_slot_nao_entra_como_anterior():
    slots = {1: _slot(1), 3: _slot(1)}
    rows = [_vol(aid, aid, "Bíceps", 10.0) for aid in (1, 2, 3)]
    (row,) = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]["volume"]
    assert _period_ids(row) == [3, 1, None, None]


def test_chave_e_template_e_sequencia_nunca_o_nome_nem_o_id_da_versao():
    # Mesmo nome em slots diferentes nao casa; nome diferente (renomeado) e
    # id de slot novo (versao publicada) na mesma posicao casam.
    slots = {
        1: _slot(1, "Treino A", slot_id=11),
        2: _slot(2, "Treino A", slot_id=12),
        3: _slot(1, "Peito e ombro", slot_id=31),  # v2: id novo, nome novo
        4: _slot(1, "Treino A", template_id=99),  # outro Modelo
        5: _slot(1, "Peito e ombro", slot_id=31),
    }
    rows = [_vol(aid, aid, "Peitoral", 10.0) for aid in (1, 2, 3, 4, 5)]
    (row,) = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]["volume"]
    assert _period_ids(row) == [5, 3, 1, None]
    assert row["slot"]["label"] == "Peito e ombro"


def test_rotulo_sem_nome_usa_a_posicao():
    assert _slot(3).label == "Slot 3"
    assert _slot(3, "  ").label == "Slot 3"


def test_session_slot_from_row():
    assert session_slot_from_row({"template_id": None, "slot_sequence": 1}) is None
    assert session_slot_from_row({"template_id": 1, "slot_sequence": None}) is None
    slot = session_slot_from_row({"template_id": "4", "slot_sequence": 2, "slot_id": 9, "slot_name": "B"})
    assert slot.key == (4, 2) and slot.slot_id == 9


def test_agregacao_por_macro_grupo_soma_a_sessao_antes_de_escolher():
    macro = {"volume_class_id": 1, "volume_class_name": "Grandes", "volume_class_order": 1}
    slots = {1: _slot(1), 2: _slot(2), 3: _slot(1)}
    rows = [
        _vol(1, 1, "Peitoral", 100.0, sets=2, rpe=8.0, **macro),
        _vol(1, 1, "Costas", 50.0, sets=2, rpe=6.0, **macro),
        _vol(2, 2, "Costas", 70.0, **macro),
        _vol(3, 3, "Peitoral", 30.0, sets=1, rpe=9.0, **macro),
    ]
    payload = build_slot_comparison(rows, [], slots)
    (row,) = payload["levels"]["volume_class"]["volume"]
    assert row["group"] == {"key": "macro:1", "label": "Grandes", "order": 1}
    assert _period_ids(row) == [3, 1, None, None]
    t1 = row["by_period"]["t1"]
    assert t1["volume_kg"] == 150.0 and t1["sets_count"] == 4 and t1["avg_rpe"] == 7.0
    # No nivel grupo, Costas tem T-0 no slot 2 (sessao 2) e nada antes nele.
    by_label = {r["group"]["label"]: r for r in payload["levels"]["muscle_group"]["volume"]}
    assert _period_ids(by_label["Costas"]) == [2, None, None, None]
    assert by_label["Costas"]["slot"]["slot_sequence"] == 2


def test_series_equivalentes_escolhem_as_proprias_sessoes():
    slots = {1: _slot(1), 2: _slot(1)}
    eq = [
        {"activity_id": 1, "local_date": "2026-10-01", "muscle_group": "Tríceps", "equivalent_sets": 1.25},
        {"activity_id": 2, "local_date": "2026-10-02", "muscle_group": "Tríceps", "equivalent_sets": 2.0},
    ]
    (row,) = build_slot_comparison([], eq, slots)["levels"]["muscle_group"]["equivalent_sets"]
    assert row["by_period"]["t0"]["equivalent_sets"] == 2.0
    assert row["by_period"]["t1"]["equivalent_sets"] == 1.25


def test_ordem_das_linhas_macro_pelo_cadastro_balde_por_ultimo():
    rows = [
        _vol(1, 1, "A", 1.0, volume_class_id=None, volume_class_name="Sem macro-grupo", volume_class_order=None),
        _vol(1, 1, "B", 1.0, volume_class_id=2, volume_class_name="Pequenos", volume_class_order=2),
        _vol(1, 1, "C", 1.0, volume_class_id=1, volume_class_name="Grandes", volume_class_order=1),
    ]
    labels = [r["group"]["label"] for r in build_slot_comparison(rows, [], {})["levels"]["volume_class"]["volume"]]
    assert labels == ["Grandes", "Pequenos", "Sem macro-grupo"]


def test_desempate_no_mesmo_dia_pelo_activity_id():
    slots = {1: _slot(1), 2: _slot(1)}
    rows = [_vol(1, 5, "Peitoral", 1.0), _vol(2, 5, "Peitoral", 2.0)]
    (row,) = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]["volume"]
    assert _period_ids(row) == [2, 1, None, None]


def test_select_slot_sessions_vazio():
    slot, picked = select_slot_sessions([], lambda _: None)
    assert slot is None and picked == [None] * SLOT_COMPARISON_SESSIONS


def test_chave_de_agrupamento_paridade_com_o_frontend():
    """Mesma fixture de `frontend/src/lib/volume-grouping.test.ts`: o dono e o
    `groupingKeyOf` da tela (tabelas por semana/equipamento/mapa) não divergem."""
    import json
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "volume-grouping.parity.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    assert cases
    for case in cases:
        assert ssc.grouping_key_of(case["row"], case["level"]) == case["expected"], case


def test_niveis_do_seletor():
    assert ssc.GROUPING_LEVELS == ("volume_class", "parent_group", "muscle_group")


def test_volume_service_monta_o_payload_com_o_slot_de_cada_sessao(monkeypatch):
    from app.intelligence import methodology_volume_service as mvs
    from app.intelligence.template_lineage_reader import TEMPLATE_LINEAGE_SQL
    from app.intelligence.volume_service import _ACTIVITY_SLOTS_SQL, VolumeService

    service = VolumeService()
    volume = [_vol(1, 1, "Peitoral", 10.0), _vol(2, 2, "Peitoral", 20.0), _vol(3, 3, "Peitoral", 30.0)]
    monkeypatch.setattr(service, "get_recent_exercise_volume", lambda **kw: [dict(r) for r in volume])
    monkeypatch.setattr(service, "get_equivalent_sets_by_activity", lambda **kw: [])
    monkeypatch.setattr(mvs, "load_group_hierarchy", lambda: {})
    calls = []

    def fake_fetch_all(sql, params=()):
        calls.append((sql, params))
        if sql == TEMPLATE_LINEAGE_SQL:
            return []
        return [
            {"activity_id": 1, "template_id": 5, "slot_sequence": 1, "slot_id": 10, "slot_name": "A", "template_name": "M"},
            {"activity_id": 2, "template_id": 5, "slot_sequence": 2, "slot_id": 11, "slot_name": "B", "template_name": "M"},
            {"activity_id": 3, "template_id": 5, "slot_sequence": 1, "slot_id": 10, "slot_name": "A", "template_name": "M"},
        ]

    monkeypatch.setattr(service, "fetch_all", fake_fetch_all)
    payload = service.get_slot_session_comparison(user_id=1, limit=400)
    assert calls == [(_ACTIVITY_SLOTS_SQL, (1, "2026-10-01")), (TEMPLATE_LINEAGE_SQL, ())]
    assert "version_id" in _ACTIVITY_SLOTS_SQL
    (row,) = payload["levels"]["muscle_group"]["volume"]
    assert _period_ids(row) == [3, 1, None, None]
    assert row["slot"]["label"] == "A" and payload["limit"] == 400


# ---------------------------------------------------------------------------
# Grupo da sessao em /dashboard/treinos (2026-10-24)
# ---------------------------------------------------------------------------


def test_grupo_da_sessao_e_modelo_mais_posicao_e_ignora_os_nomes():
    antes = ssc.session_group_of(
        workout_plan_id=1, template_id=36, slot_sequence=2, slot_name="Pull", template_name="PPL antigo"
    )
    depois = ssc.session_group_of(
        workout_plan_id=2, template_id=36, slot_sequence=2, slot_name="Pull - SEX", template_name="PPL-D-UL-D"
    )
    assert antes["key"] == depois["key"] == ssc.slot_group_key(SessionSlot(template_id=36, slot_sequence=2))
    assert depois["kind"] == ssc.GROUP_KIND_SLOT
    assert depois["label"] == "Pull - SEX"
    assert depois["template_name"] == "PPL-D-UL-D"
    assert depois["slot_sequence"] == 2


def test_grupo_nao_funde_modelos_ou_posicoes_diferentes_com_o_mesmo_nome():
    keys = {
        ssc.session_group_of(workout_plan_id=1, template_id=t, slot_sequence=s, slot_name="Pull")["key"]
        for t, s in ((34, 2), (36, 2), (36, 3))
    }
    assert len(keys) == 3


def test_slot_sem_nome_tem_rotulo_da_posicao():
    grupo = ssc.session_group_of(workout_plan_id=1, template_id=9, slot_sequence=4, slot_name="  ")
    assert grupo["label"] == "Slot 4"


def test_ficha_sem_modelo_ou_sem_posicao_cai_no_grupo_unico_sem_modelo():
    legado = ssc.session_group_of(workout_plan_id=70, template_id=None, slot_sequence=None)
    sem_posicao = ssc.session_group_of(workout_plan_id=71, template_id=9, slot_sequence=None)
    assert legado["key"] == sem_posicao["key"] == ssc.NO_TEMPLATE_GROUP_KEY
    assert legado["kind"] == ssc.GROUP_KIND_NO_TEMPLATE
    assert legado["label"] == ssc.NO_TEMPLATE_GROUP_LABEL
    assert legado["template_id"] is None


def test_sessao_sem_ficha_cai_no_grupo_sem_ficha():
    grupo = ssc.session_group_of(workout_plan_id=None, template_id=None, slot_sequence=None)
    assert grupo["key"] == ssc.UNLINKED_GROUP_KEY
    assert grupo["kind"] == ssc.GROUP_KIND_UNLINKED
    assert grupo["label"] == ssc.UNLINKED_GROUP_LABEL


def test_payload_da_sessao_do_dashboard_usa_o_grupo_do_dono():
    from app.intelligence.dashboard import adherence_service as svc
    from app.intelligence.dashboard.contracts import WorkoutSessionOut

    row = {
        "activity_id": 252,
        "local_date": dt.date(2026, 10, 24),
        "trained_at": None,
        "activity_name": "PPL-D-UL-D - Ponderado - Legs - SAB",
        "session_status": "DONE",
        "workout_plan_id": 153,
        "plan_name": "qualquer nome",
        "template_id": 36,
        "template_slot_sequence": 3,
        "template_name": "PPL-D-UL-D - Ponderado",
        "template_version_id": 30,
    }
    payload = svc._session_payload(
        row, totals={}, effective={}, counts={}, records={}, slot_names={(30, 3): "Legs - SAB"}
    )
    assert payload["group"] == ssc.session_group_of(
        workout_plan_id=153,
        template_id=36,
        slot_sequence=3,
        slot_name="Legs - SAB",
        template_name="PPL-D-UL-D - Ponderado",
    )
    sem_ficha = svc._session_payload(
        {**row, "workout_plan_id": None, "template_id": None, "template_slot_sequence": None},
        totals={},
        effective={},
        counts={},
        records={},
        slot_names={},
    )
    assert sem_ficha["group"]["kind"] == ssc.GROUP_KIND_UNLINKED
    assert "group" in WorkoutSessionOut.model_fields


def test_leitura_das_sessoes_traz_o_nome_atual_do_modelo():
    from app.intelligence.dashboard import adherence_service as svc

    for sql in (svc.SESSIONS_PAGE_SQL, svc.SESSION_BY_ID_SQL):
        assert "AS template_name" in sql
        assert "LEFT JOIN workout_plan_templates wpt" in sql


# --- #188 etapa 3: variacao/direcao prontas (dono `volume_comparison`) ---


def _js_trend(current, previous, epsilon=0.0001):
    """Formula de `getTrendDirection` da tela (paridade)."""
    if current is None or previous is None:
        return None
    delta = current - previous
    if abs(delta) <= epsilon:
        return "stable"
    return "up" if delta > 0 else "down"


def test_cada_periodo_contra_o_mais_antigo_volume_e_rpe():
    slots = {aid: _slot(1) for aid in (1, 2, 3, 4)}
    rows = [
        _vol(1, 1, "Peitoral", 100.0, rpe=8.0),
        _vol(2, 2, "Peitoral", 100.00005, rpe=8.04),  # dentro das tolerancias
        _vol(3, 3, "Peitoral", 90.0, rpe=8.2),
        _vol(4, 4, "Peitoral", 120.5, rpe=8.0),
    ]
    (row,) = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]["volume"]
    p = row["by_period"]
    assert (p["t0"]["volume_delta_kg"], p["t0"]["volume_direction"], p["t0"]["rpe_direction"]) == (30.5, "up", "down")
    assert (p["t1"]["volume_delta_kg"], p["t1"]["volume_direction"], p["t1"]["rpe_direction"]) == (-10.0, "down", "up")
    assert (p["t2"]["volume_direction"], p["t2"]["rpe_direction"]) == ("stable", "stable")
    assert (p["t3"]["volume_delta_kg"], p["t3"]["volume_direction"], p["t3"]["rpe_direction"]) == (None, None, None)
    for k, older in (("t0", "t1"), ("t1", "t2"), ("t2", "t3")):
        assert p[k]["volume_direction"] == _js_trend(p[k]["volume_kg"], p[older]["volume_kg"])
        assert p[k]["rpe_direction"] == _js_trend(p[k]["avg_rpe"], p[older]["avg_rpe"], 0.05)


def test_periodo_sem_vizinho_ou_sem_rpe_fica_none():
    slots = {1: _slot(1), 2: _slot(1)}
    rows = [_vol(1, 1, "Costas", 50.0), _vol(2, 2, "Costas", 40.0, rpe=7.0)]
    (row,) = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]["volume"]
    t0 = row["by_period"]["t0"]
    assert t0["volume_direction"] == "down" and t0["rpe_direction"] is None
    assert row["by_period"]["t2"] is None


def test_series_equivalentes_ganham_delta_e_direcao():
    slots = {1: _slot(1), 2: _slot(1)}
    eq = [
        {"activity_id": 1, "local_date": "2026-10-01", "muscle_group": "Tríceps", "equivalent_sets": 1.25},
        {"activity_id": 2, "local_date": "2026-10-02", "muscle_group": "Tríceps", "equivalent_sets": 2.0},
    ]
    (row,) = build_slot_comparison([], eq, slots)["levels"]["muscle_group"]["equivalent_sets"]
    assert row["by_period"]["t0"]["equivalent_sets_delta"] == 0.75
    assert row["by_period"]["t0"]["sets_direction"] == "up"
    assert row["by_period"]["t1"]["sets_direction"] is None


def test_totais_e_top_volume_t0_pela_formula_da_tela():
    slots = {1: _slot(1), 2: _slot(1), 3: _slot(2)}
    rows = [
        _vol(1, 1, "Peitoral", 100.0, rpe=8.0),
        _vol(2, 2, "Peitoral", 150.0, sets=1, rpe=9.0),
        _vol(3, 3, "Costas", 80.0, sets=10, rpe=6.0),
        _vol(3, 3, "Bíceps", 20.0),
    ]
    level = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]
    totals = level["totals"]
    assert totals["by_period"] == {"t0": 250.0, "t1": 100.0, "t2": 0.0, "t3": 0.0}
    assert (totals["groups_with_t0"], totals["groups_tracked"]) == (3, 3)
    # Decisao do usuario (17/11/2026): media PONDERADA pelas series de T-0
    # (9 x 1 + 6 x 10) / 11 = 6,27; a media simples seria 7,5. Biceps sem RPE fica fora.
    assert totals["avg_rpe_t0"] == 6.27
    assert [(i["group"]["label"], i["t0"], i["t1"]) for i in level["top_volume_t0"]] == [
        ("Peitoral", 150.0, 100.0),
        ("Costas", 80.0, 0.0),
        ("Bíceps", 20.0, 0.0),
    ]
    assert ssc.TOP_VOLUME_T0_LIMIT == 10


def test_top_volume_t0_limita_a_10():
    slots = {aid: _slot(aid) for aid in range(1, 13)}
    rows = [_vol(aid, aid, f"G{aid:02d}", float(aid)) for aid in range(1, 13)]
    top = build_slot_comparison(rows, [], slots)["levels"]["muscle_group"]["top_volume_t0"]
    assert [i["t0"] for i in top] == [float(v) for v in range(12, 2, -1)]


def test_totais_vazios():
    totals = build_slot_comparison([], [], {})["levels"]["muscle_group"]["totals"]
    assert totals == {
        "by_period": {"t0": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0},
        "groups_with_t0": 0, "groups_tracked": 0, "avg_rpe_t0": None,
    }


def test_total_sem_serie_com_rpe_em_t0_sai_sem_rpe_medio():
    slots = {1: _slot(1)}
    totals = build_slot_comparison([_vol(1, 1, "Costas", 50.0)], [], slots)["levels"]["muscle_group"]["totals"]
    assert totals["avg_rpe_t0"] is None
