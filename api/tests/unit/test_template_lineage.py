"""Linhagem do Modelo (copia -> origem, 090): o mesmo slot de um Modelo
duplicado e o slot do Modelo de origem.

Dono: `intelligence/slot_session_comparison.py` (`lineage_root_of`,
`resolve_lineage_roots`, `SessionSlot.key`); leitura:
`intelligence/template_lineage_reader.py`. Mapa FONTES_DA_VERDADE §3.5.
Rodar: cd api && python -m pytest tests/unit/test_template_lineage.py -q
"""

from __future__ import annotations

import datetime as dt

from app.intelligence import slot_session_comparison as ssc
from app.intelligence import template_lineage_reader as reader
from app.intelligence.slot_session_comparison import (
    MAX_TEMPLATE_LINEAGE_DEPTH,
    SessionSlot,
    build_slot_comparison,
    lineage_root_of,
    resolve_lineage_roots,
    session_group_of,
    session_slot_from_row,
)


def test_raiz_sobe_a_cadeia_e_original_e_raiz_de_si():
    parents = {36: 34, 40: 36}
    assert lineage_root_of(34, parents) == 34
    assert lineage_root_of(36, parents) == 34
    assert lineage_root_of(40, parents) == 34
    assert lineage_root_of(7, parents) == 7
    # Origem fora do mapa (apagada fisicamente) e raiz comum das copias.
    assert lineage_root_of(51, {51: 50, 52: 50}) == lineage_root_of(52, {51: 50, 52: 50}) == 50


def test_ciclo_responde_o_menor_id_para_todos_os_membros():
    parents = {5: 9, 9: 7, 7: 5}
    assert {lineage_root_of(t, parents) for t in (5, 7, 9)} == {5}
    # Ciclo mais acima na cadeia: a copia herda a raiz do ciclo.
    assert lineage_root_of(20, {20: 9, **parents}) == 5
    assert lineage_root_of(3, {3: 3}) == 3


def test_cadeia_alem_do_teto_fica_sem_linhagem():
    longa = {i: i - 1 for i in range(2, MAX_TEMPLATE_LINEAGE_DEPTH + 3)}
    topo = MAX_TEMPLATE_LINEAGE_DEPTH + 2
    assert lineage_root_of(topo, longa) == topo
    assert lineage_root_of(MAX_TEMPLATE_LINEAGE_DEPTH + 1, longa) == 1
    assert MAX_TEMPLATE_LINEAGE_DEPTH == 32


def test_resolve_lineage_roots_ignora_none():
    assert resolve_lineage_roots([36, None, 34, 36], {36: 34}) == {36: 34, 34: 34}


def test_chave_do_slot_usa_a_raiz_e_payload_mantem_o_modelo_da_sessao():
    roots = {36: 34}
    copia = session_slot_from_row({"template_id": 36, "slot_sequence": 2, "slot_name": "Pull - SEX"}, roots)
    original = session_slot_from_row({"template_id": 34, "slot_sequence": 2, "slot_name": "Pull"}, roots)
    assert copia.key == original.key == (34, 2)
    assert copia.as_payload()["template_id"] == 36
    assert copia.as_payload()["lineage_template_id"] == 34
    # Sem mapa (ou Modelo fora dele): o proprio Modelo, como antes.
    assert session_slot_from_row({"template_id": 36, "slot_sequence": 2}).key == (36, 2)
    assert SessionSlot(template_id=8, slot_sequence=1).lineage_id == 8


def test_grupo_da_copia_e_o_grupo_da_origem():
    roots = {36: 34, 34: 34}
    copia = session_group_of(
        workout_plan_id=2, template_id=36, slot_sequence=3, slot_name="Legs - SAB",
        template_name="PPL-D-UL-D - Ponderado", lineage_roots=roots,
    )
    original = session_group_of(
        workout_plan_id=1, template_id=34, slot_sequence=3, slot_name="Legs",
        template_name="PPL", lineage_roots=roots,
    )
    assert copia["key"] == original["key"] == "slot:34:3"
    assert copia["lineage_template_id"] == original["lineage_template_id"] == 34
    assert (copia["template_id"], copia["template_name"], copia["label"]) == (36, "PPL-D-UL-D - Ponderado", "Legs - SAB")
    # Outra posicao da mesma linhagem nao funde.
    outro = session_group_of(workout_plan_id=3, template_id=36, slot_sequence=2, lineage_roots=roots)
    assert outro["key"] != copia["key"]
    sem_ficha = session_group_of(workout_plan_id=None, template_id=None, slot_sequence=None)
    assert sem_ficha["lineage_template_id"] is None


def test_comparativo_t1_vem_da_sessao_do_modelo_de_origem():
    roots = {36: 34, 34: 34}
    linhas = [
        {"activity_id": aid, "local_date": dt.date(2026, 10, day), "muscle_group": "Costas",
         "volume_kg": vol, "sets_count": 3, "avg_rpe": None}
        for aid, day, vol in ((240, 16, 1000.0), (250, 23, 1200.0))
    ]
    slots = {
        240: session_slot_from_row({"template_id": 34, "slot_sequence": 2, "slot_name": "Pull"}, roots),
        250: session_slot_from_row({"template_id": 36, "slot_sequence": 2, "slot_name": "Pull - SEX"}, roots),
    }
    (row,) = build_slot_comparison(linhas, [], slots)["levels"]["muscle_group"]["volume"]
    assert row["comparison_status"] == ssc.STATUS_SAME_SLOT
    assert [row["by_period"][k] and row["by_period"][k]["activity_id"] for k in ssc.PERIOD_KEYS] == [250, 240, None, None]
    assert row["slot"]["template_id"] == 36 and row["slot"]["label"] == "Pull - SEX"


def test_leitor_traz_so_ids_e_nao_consulta_sem_modelo():
    calls = []

    def fetch(sql, params):
        calls.append((sql, params))
        return [{"template_id": 36, "source_template_id": 34}, {"template_id": 9, "source_template_id": None}]

    assert reader.load_lineage_roots(fetch, []) == {}
    assert reader.load_lineage_roots(fetch, [None]) == {}
    assert calls == []
    assert reader.load_lineage_roots(fetch, [36, 34, 7]) == {36: 34, 34: 34, 7: 7}
    assert calls == [(reader.TEMPLATE_LINEAGE_SQL, ())]
    sql = " ".join(reader.TEMPLATE_LINEAGE_SQL.split())
    assert sql == "SELECT id AS template_id, source_template_id FROM workout_plan_templates WHERE source_template_id IS NOT NULL"


def test_dashboard_de_treinos_le_a_linhagem_uma_vez_por_pagina():
    from app.intelligence.dashboard import adherence_service as svc

    row = {
        "activity_id": 240, "local_date": dt.date(2026, 10, 16), "trained_at": None, "activity_name": "x",
        "session_status": "DONE", "workout_plan_id": 140, "plan_name": "p", "template_id": 34,
        "template_slot_sequence": 2, "template_name": "PPL", "template_version_id": 21,
    }
    payload = svc._session_payload(
        row, totals={}, effective={}, counts={}, records={}, slot_names={}, lineage_roots={34: 34, 36: 34}
    )
    assert payload["group"]["key"] == "slot:34:2"
    copia = svc._session_payload(
        {**row, "template_id": 36}, totals={}, effective={}, counts={}, records={}, slot_names={},
        lineage_roots={34: 34, 36: 34},
    )
    assert copia["group"]["key"] == payload["group"]["key"]
    from app.intelligence.dashboard.contracts import SessionGroupOut

    assert "lineage_template_id" in SessionGroupOut.model_fields


def test_duplicar_modelo_grava_a_origem():
    from pathlib import Path

    fonte = (Path(__file__).resolve().parents[2] / "app" / "workout_plan_templates.py").read_text(encoding="utf-8")
    inicio = fonte.index("def duplicate_template(")
    corpo = fonte[inicio : fonte.index("\n@router", inicio)]
    # O comportamento (a copia grava a origem) e provado na integracao
    # `test_template_grid_layout.test_duplicate_copia_a_grade`.
    assert "source_template_id" in corpo
