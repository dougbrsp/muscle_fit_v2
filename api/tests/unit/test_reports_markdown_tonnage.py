"""Tonelagem do relatorio em Markdown e helpers puros do coletor
(`app/intelligence/report/markdown_report_service.py`).

A regra de convencao de carga (assisted/bodyweight fora) mora em Python e e
testada aqui com linhas falsas. O filtro de tipo de serie e a ponderacao por
`stimulus_weight` moram no SQL: aqui se prova que o SQL usa a MESMA regra
compartilhada (`set_types.sql_is_working`, `load_convention_select_expr`); os
numeros de ponta a ponta contra MySQL estao em
`tests/integration/dataapi/test_reports_markdown.py`.

Rodar: cd api && python -m pytest tests/unit/test_reports_markdown_tonnage.py
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.intelligence.report import markdown_report_service as svc
from app.set_types import sql_is_working, sql_not_preparatory


def test_exclui_assisted_e_bodyweight_e_conta_as_series_excluidas():
    group_rows = [
        {"muscle_group_id": 1, "load_conv": None, "tonnage_kg": "2400.00", "sets_count": 4},
        {"muscle_group_id": 1, "load_conv": "per_dumbbell", "tonnage_kg": 600, "sets_count": 2},
        {"muscle_group_id": 2, "load_conv": "assisted", "tonnage_kg": 900, "sets_count": 3},
        {"muscle_group_id": 2, "load_conv": "bodyweight", "tonnage_kg": 0, "sets_count": 2},
        {"muscle_group_id": 3, "load_conv": "bodyweight_plus", "tonnage_kg": "150.5", "sets_count": 1},
    ]
    convention_rows = [
        {"load_conv": None, "sets_count": 6},
        {"load_conv": "assisted", "sets_count": 3},
        {"load_conv": "bodyweight", "sets_count": 2},
    ]

    out = svc.summarize_tonnage(group_rows, convention_rows)

    # NULL = total; per_dumbbell ja e TOTAL gravado (nada convertido).
    assert out["by_group"][1] == {"tonnage_kg": 3000.0, "sets": 6, "excluded_sets": 0}
    # Grupo so com assistido/peso corporal: nenhuma tonelagem, series declaradas.
    assert out["by_group"][2] == {"tonnage_kg": 0.0, "sets": 0, "excluded_sets": 5}
    assert out["by_group"][3]["tonnage_kg"] == 150.5
    assert out["excluded_sets"] == 5
    assert out["excluded_by_convention"] == {"assisted": 3, "bodyweight": 2}
    assert out["unknown_conventions"] == []


def test_convencao_desconhecida_conta_como_total_e_e_declarada():
    out = svc.summarize_tonnage(
        [{"muscle_group_id": 1, "load_conv": "por_braco", "tonnage_kg": 100, "sets_count": 1}],
        [{"load_conv": "por_braco", "sets_count": 1}],
    )
    assert out["by_group"][1]["tonnage_kg"] == 100.0
    assert out["excluded_sets"] == 0
    assert out["unknown_conventions"] == ["por_braco"]


def test_sem_linhas_devolve_vazio_sem_zeros_inventados():
    out = svc.summarize_tonnage([], [])
    assert out == {"by_group": {}, "excluded_sets": 0, "excluded_by_convention": {}, "unknown_conventions": []}


def test_sql_so_conta_series_de_trabalho_e_pondera_pelo_estimulo():
    for template in (svc.TONNAGE_BY_GROUP_SQL_TEMPLATE, svc.WORKING_SETS_BY_CONVENTION_SQL_TEMPLATE):
        sql = template.format(load_convention="eqex.load_convention")
        # Filtro de trabalho e o compartilhado: aquecimento, ajuste e
        # aplicacao de tecnica ficam fora; nada de "so nao-preparatorias".
        assert sql_is_working("es.set_type") in sql
        assert sql_not_preparatory("es.set_type") not in sql
        assert "a.user_id = %s" in sql
        assert "LEFT JOIN equipment_exercises eqex" in sql
        assert "eqex.is_active = 1" in sql
    group_sql = svc.TONNAGE_BY_GROUP_SQL_TEMPLATE.format(load_convention="NULL")
    assert "COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0) * COALESCE(emc.stimulus_weight, 0)" in group_sql
    assert "emc.is_active = 1" in group_sql


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        self._sql = sql

    def fetchone(self):
        if "DATABASE()" in self._sql:
            return {"db": "x"}
        return {"n": 0}  # coluna da 045 ausente -> NULL = total

    def fetchall(self):
        if "GROUP BY emc.muscle_group_id" in self._sql:
            return [{"muscle_group_id": 9, "load_conv": None, "tonnage_kg": 500, "sets_count": 2}]
        return [{"load_conv": None, "sets_count": 2}]


class _Conn:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return _Cursor(self)


def test_load_tonnage_sem_coluna_045_usa_null_e_passa_usuario_e_janela():
    from app import load_conventions

    load_conventions.reset_load_convention_column_cache()
    conn = _Conn()
    window = SimpleNamespace(date_from=date(2026, 7, 1), date_to=date(2026, 9, 28))
    out = svc.load_tonnage(conn, user_id=42, window=window)
    assert out["by_group"][9]["tonnage_kg"] == 500.0
    tonnage_sql = [s for s, p in conn.executed if "GROUP BY" in s]
    assert len(tonnage_sql) == 2
    assert all("NULL" in s and "eqex.load_convention" not in s for s in tonnage_sql)
    assert all(p == (42, date(2026, 7, 1), date(2026, 9, 28)) for s, p in conn.executed if "GROUP BY" in s)


def test_macro_grupo_herda_do_pai_e_sem_classe_vira_sem_macro_grupo():
    groups = [
        SimpleNamespace(id=1, volume_class_id=10, methodology_parent_group_id=None),
        SimpleNamespace(id=2, volume_class_id=None, methodology_parent_group_id=1),  # fatia
        SimpleNamespace(id=3, volume_class_id=None, methodology_parent_group_id=None),
        SimpleNamespace(id=4, volume_class_id=None, methodology_parent_group_id=5),  # ciclo
        SimpleNamespace(id=5, volume_class_id=None, methodology_parent_group_id=4),
    ]
    classes = {10: SimpleNamespace(name="Grandes")}
    out = svc.resolve_macro_groups(groups, classes)
    assert out == {1: "Grandes", 2: "Grandes", 3: svc.MACRO_GROUP_UNCLASSIFIED, 4: svc.MACRO_GROUP_UNCLASSIFIED,
                   5: svc.MACRO_GROUP_UNCLASSIFIED}


def test_fatia_com_classe_propria_usa_o_macro_grupo_do_bloco_como_o_motor():
    """M76 Lote 4a (E12): a fatia e somada no pai e o motor usa a faixa da
    RAIZ; o relatorio parava no 1o no com classe e divergia."""
    groups = [
        SimpleNamespace(id=1, volume_class_id=10, methodology_parent_group_id=None),
        SimpleNamespace(id=2, volume_class_id=20, methodology_parent_group_id=1),  # fatia com classe propria
        SimpleNamespace(id=3, volume_class_id=None, methodology_parent_group_id=2),  # fatia da fatia
    ]
    classes = {10: SimpleNamespace(name="Grandes"), 20: SimpleNamespace(name="Pequenos")}
    assert svc.resolve_macro_groups(groups, classes) == {1: "Grandes", 2: "Grandes", 3: "Grandes"}


def test_subtotais_seguem_a_ordem_do_cadastro_e_tonelagem_ausente_continua_ausente():
    groups = [
        {"macro_group": "Pequenos", "physical_sets": 10, "effective_sets": 8, "weekly_effective_sets": 2, "tonnage_kg": None},
        {"macro_group": "Grandes", "physical_sets": 20, "effective_sets": 20, "weekly_effective_sets": 5, "tonnage_kg": 1000},
        {"macro_group": "Grandes", "physical_sets": 5, "effective_sets": 4.5, "weekly_effective_sets": 1, "tonnage_kg": 200},
        {"macro_group": None, "physical_sets": 1, "effective_sets": 1, "weekly_effective_sets": None, "tonnage_kg": None},
    ]
    out = svc.macro_group_subtotals(groups, ["Grandes", "Pequenos", "Outros"])
    assert [m["name"] for m in out] == ["Grandes", "Pequenos", svc.MACRO_GROUP_UNCLASSIFIED]
    assert out[0] == {"name": "Grandes", "groups": 2, "physical_sets": 25.0, "effective_sets": 24.5,
                      "weekly_effective_sets": 6.0, "tonnage_kg": 1200.0}
    assert out[1]["tonnage_kg"] is None


def test_sessoes_por_trecho_de_vigencia():
    segments = [
        SimpleNamespace(date_from=date(2026, 9, 1), date_to=date(2026, 9, 10)),
        SimpleNamespace(date_from=date(2026, 9, 11), date_to=date(2026, 9, 30)),
    ]
    by_day = {date(2026, 9, 1): 1, date(2026, 9, 10): 2, date(2026, 9, 11): 1, date(2026, 10, 1): 5}
    assert svc.sessions_per_segment(segments, by_day) == [3, 1]


def test_nome_do_arquivo():
    assert svc.report_filename(date(2026, 7, 4), date(2026, 10, 1)) == "relatorio-musclefit-2026-07-04_2026-10-01.md"
