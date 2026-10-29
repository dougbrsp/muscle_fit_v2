"""/volume-grupamentos agrupado por macro-grupo, grupo pai ou grupo (2026-10-17).

Dono da resolucao grupo -> pai -> macro-grupo: `intelligence/methodology_volume.py`
(`resolve_group_hierarchy`, que reusa `block_of_group` e `block_volume_class_id`,
e o rotulo `VOLUME_CLASS_UNCLASSIFIED_LABEL`). Leitura em
`methodology_volume_service.load_group_hierarchy` (um statement). As tres rotas
da pagina so acrescentam os campos por linha; os numeros nao mudam.
Mapa: docs/motores/FONTES_DA_VERDADE.md §3.5 e §50.

Rodar: cd api && python -m pytest tests/unit/test_volume_grupamentos_hierarquia.py
"""

from __future__ import annotations

import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import dashboard_modules
from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as mvs
from app.intelligence import volume_service
from app.intelligence.methodology_volume import (
    VOLUME_CLASS_UNCLASSIFIED_LABEL,
    hierarchy_of_group_name,
    index_hierarchy_by_name,
    resolve_group_hierarchy,
)
from app.security import Identity

IDENTIDADE = Identity(user_id=1, role="regular", source="session")

# Cadastro de grupos musculares (catalogo do app, sem dado de usuario):
# `muscle_groups` LEFT JOIN `muscle_volume_classes`, a forma de
# `_GROUP_HIERARCHY_SQL`. Core (55) nao tem pai nem classe (055: de proposito).
_CLASSES = {1: ("Grandes", 1), 2: ("Pequenos", 2), 3: ("Outros", 3)}
_GRUPOS = (
    (1, "Abdômen", None, 3),
    (2, "Adutores", None, 3),
    (3, "Antebraço anterior", 50, None),
    (4, "Quadríceps", None, 1),
    (5, "Bíceps", None, 2),
    (6, "Costas", None, 1),
    (7, "Costas meio", 6, None),
    (8, "Glúteos", None, 2),
    (9, "Lombar", None, 3),
    (10, "Ombros / Deltoides", None, 2),
    (12, "Peitoral", None, 1),
    (13, "Posterior de coxa", None, 1),
    (14, "Tríceps", None, 2),
    (50, "Antebraço", None, 2),
    (51, "Panturrilha", None, 2),
    (52, "Peitoral inferior", 12, None),
    (53, "Panturrilha anterior", 51, None),
    (54, "Flexores do quadril", None, 3),
    (55, "Core", None, None),
    (56, "Manguito rotador", None, 3),
)


def _linhas_dev() -> list[dict]:
    linhas = []
    for group_id, name, parent, class_id in _GRUPOS:
        class_name, sort_order = _CLASSES.get(class_id, (None, None))
        linhas.append(
            {
                "id": group_id,
                "name": name,
                "size_class": "unclassified",
                "methodology_parent_group_id": parent,
                "volume_class_id": class_id,
                "volume_class_name": class_name,
                "volume_class_sort_order": sort_order,
            }
        )
    return linhas


# ---------------------------------------------------------------------------
# Dono (puro)
# ---------------------------------------------------------------------------


def _g(group_id, name, parent=None, class_id=None):
    return SimpleNamespace(id=group_id, name=name, methodology_parent_group_id=parent, volume_class_id=class_id)


def test_fatia_herda_o_macro_grupo_e_soma_no_pai():
    out = resolve_group_hierarchy([_g(6, "Costas", class_id=1), _g(7, "Costas meio", parent=6)], {1: "Grandes"})
    fatia = out[7]
    assert (fatia.parent_group_id, fatia.parent_group_name) == (6, "Costas")
    assert (fatia.volume_class_id, fatia.volume_class_name, fatia.volume_class_order) == (1, "Grandes", 0)


def test_grupo_sem_pai_e_o_proprio_pai():
    out = resolve_group_hierarchy([_g(5, "Bíceps", class_id=2)], {2: "Pequenos"})
    assert (out[5].parent_group_id, out[5].parent_group_name) == (5, "Bíceps")


def test_grupo_sem_classe_cai_no_balde_do_dono():
    out = resolve_group_hierarchy([_g(55, "Core")], {1: "Grandes"})
    assert out[55].volume_class_id is None
    assert out[55].volume_class_order is None
    assert out[55].volume_class_name == VOLUME_CLASS_UNCLASSIFIED_LABEL == "Sem macro-grupo"


def test_classe_fora_do_cadastro_ou_sem_nome_cai_no_balde():
    out = resolve_group_hierarchy([_g(1, "A", class_id=9), _g(2, "B", class_id=3)], {3: ""})
    assert out[1].volume_class_name == out[2].volume_class_name == VOLUME_CLASS_UNCLASSIFIED_LABEL
    assert out[1].volume_class_id is None and out[2].volume_class_id is None


def test_fatia_com_classe_propria_usa_a_do_bloco_como_o_motor():
    # Mesmo criterio de `block_volume_class_id` (M76 Lote 4a, E12): a raiz vale.
    grupos = [_g(1, "Pai", class_id=10), _g(2, "Fatia", parent=1, class_id=20), _g(3, "Fatia da fatia", parent=2)]
    out = resolve_group_hierarchy(grupos, {10: "Grandes", 20: "Pequenos"})
    assert {gid: (h.parent_group_id, h.volume_class_name) for gid, h in out.items()} == {
        1: (1, "Grandes"),
        2: (1, "Grandes"),
        3: (1, "Grandes"),
    }


def test_ciclo_de_cadastro_nao_trava():
    out = resolve_group_hierarchy([_g(4, "A", parent=5), _g(5, "B", parent=4)], {})
    assert {out[4].volume_class_name, out[5].volume_class_name} == {VOLUME_CLASS_UNCLASSIFIED_LABEL}


def test_ordem_do_macro_grupo_e_a_do_cadastro():
    out = resolve_group_hierarchy(
        [_g(1, "A", class_id=3), _g(2, "B", class_id=1), _g(3, "C", class_id=2)],
        {2: "Pequenos", 3: "Outros", 1: "Grandes"},  # ordem do cadastro, nao do id
    )
    assert [out[i].volume_class_order for i in (3, 1, 2)] == [0, 1, 2]


def test_nome_fora_do_cadastro_e_bloco_proprio_no_balde():
    by_name = index_hierarchy_by_name(resolve_group_hierarchy([_g(6, "Costas", class_id=1)], {1: "Grandes"}))
    outros = hierarchy_of_group_name("Outros", by_name)
    assert outros.group_id is None and outros.parent_group_id is None
    assert outros.parent_group_name == "Outros"
    assert outros.volume_class_name == VOLUME_CLASS_UNCLASSIFIED_LABEL
    # Mesma comparacao de nome do realizado: sem caixa e sem espacos nas pontas.
    assert hierarchy_of_group_name("  costas ", by_name).group_id == 6


def test_relatorio_usa_o_rotulo_do_dono():
    from app.intelligence.report import markdown_report_service as report

    assert report.MACRO_GROUP_UNCLASSIFIED is VOLUME_CLASS_UNCLASSIFIED_LABEL


# ---------------------------------------------------------------------------
# Leitura (service do dono)
# ---------------------------------------------------------------------------


def test_cadastro_real_do_dev_nos_tres_niveis():
    by_name = mvs.group_hierarchy_by_name(_linhas_dev())
    esperado = {
        "Costas meio": ("Costas", "Grandes"),
        "Peitoral inferior": ("Peitoral", "Grandes"),
        "Antebraço anterior": ("Antebraço", "Pequenos"),
        "Panturrilha anterior": ("Panturrilha", "Pequenos"),
        "Core": ("Core", VOLUME_CLASS_UNCLASSIFIED_LABEL),
        "Abdômen": ("Abdômen", "Outros"),
        "Quadríceps": ("Quadríceps", "Grandes"),
    }
    for nome, (pai, macro) in esperado.items():
        item = hierarchy_of_group_name(nome, by_name)
        assert (item.parent_group_name, item.volume_class_name) == (pai, macro), nome
    assert {h.volume_class_name: h.volume_class_order for h in by_name.values()} == {
        "Grandes": 0,
        "Pequenos": 1,
        "Outros": 2,
        VOLUME_CLASS_UNCLASSIFIED_LABEL: None,
    }


def test_ordem_das_classes_segue_sort_order_e_id():
    linhas = [
        {"id": 1, "name": "A", "size_class": None, "methodology_parent_group_id": None, "volume_class_id": 7,
         "volume_class_name": "Sete", "volume_class_sort_order": Decimal("2")},
        {"id": 2, "name": "B", "size_class": None, "methodology_parent_group_id": None, "volume_class_id": 9,
         "volume_class_name": "Nove", "volume_class_sort_order": 1},
        {"id": 3, "name": "C", "size_class": None, "methodology_parent_group_id": None, "volume_class_id": 8,
         "volume_class_name": "Oito", "volume_class_sort_order": 2},
    ]
    by_name = mvs.group_hierarchy_by_name(linhas)
    assert [by_name[n].volume_class_name for n in ("b", "a", "c")] == ["Nove", "Sete", "Oito"]
    assert [by_name[n].volume_class_order for n in ("b", "a", "c")] == [0, 1, 2]


def test_load_group_hierarchy_e_um_statement(monkeypatch):
    from app import db

    chamadas: list[str] = []

    def fetch_rows(sql, params=None):
        chamadas.append(sql)
        return _linhas_dev()

    monkeypatch.setattr(db, "fetch_rows", fetch_rows)
    by_name = mvs.load_group_hierarchy()
    assert len(chamadas) == 1
    assert "LEFT JOIN muscle_volume_classes" in chamadas[0]
    assert hierarchy_of_group_name("Costas meio", by_name).parent_group_id == 6


def test_anotar_nao_muda_nenhum_numero():
    by_name = mvs.group_hierarchy_by_name(_linhas_dev())
    linhas = [
        {"activity_id": 3, "local_date": "2026-10-10", "muscle_group": "Costas meio", "equivalent_sets": 3.25},
        {"activity_id": 3, "local_date": "2026-10-10", "muscle_group": "Outros", "equivalent_sets": 1.0},
    ]
    antes = copy.deepcopy(linhas)
    mvs.annotate_group_hierarchy(linhas, by_name)
    for nova, velha in zip(linhas, antes):
        assert {k: nova[k] for k in velha} == velha
    assert {k: linhas[0][k] for k in mvs.group_hierarchy_fields(by_name["costas meio"])} == {
        "parent_group_id": 6,
        "parent_group_name": "Costas",
        "volume_class_id": 1,
        "volume_class_name": "Grandes",
        "volume_class_order": 0,
    }
    assert linhas[1]["parent_group_name"] == "Outros"
    assert linhas[1]["volume_class_name"] == VOLUME_CLASS_UNCLASSIFIED_LABEL


# ---------------------------------------------------------------------------
# Rotas da pagina (Lambda engines): campos aditivos, numeros iguais
# ---------------------------------------------------------------------------


@pytest.fixture
def hierarquia(monkeypatch):
    leituras: list[int] = []

    def carregar():
        leituras.append(1)
        return mvs.group_hierarchy_by_name(_linhas_dev())

    monkeypatch.setattr(mvs, "load_group_hierarchy", carregar)
    return leituras


_CAMPOS = {"parent_group_id", "parent_group_name", "volume_class_id", "volume_class_name", "volume_class_order"}


def test_rota_series_equivalentes(monkeypatch, hierarquia):
    from app import analytics

    linhas = [{"activity_id": 9, "local_date": "2026-10-10", "muscle_group": "Peitoral inferior", "equivalent_sets": 0.9}]
    monkeypatch.setattr(
        volume_service.VolumeService,
        "get_equivalent_sets_by_activity",
        lambda self, limit, user_id: copy.deepcopy(linhas),
    )
    corpo = analytics.muscle_groups_equivalent_sets_by_activity(limit=400, identity=IDENTIDADE)
    (item,) = corpo["items"]
    assert _CAMPOS <= item.keys()
    assert {k: item[k] for k in linhas[0]} == linhas[0]
    assert (item["parent_group_name"], item["volume_class_name"]) == ("Peitoral", "Grandes")
    assert hierarquia == [1]


def test_rota_volume_recente(monkeypatch, hierarquia):
    from app import analytics

    linhas = [
        {"activity_id": 9, "local_date": "2026-10-10", "muscle_group": "Core", "equipment_name": None,
         "sets_count": 3, "volume_kg": 0.0, "avg_rpe": 8.0, "last_weight_kg": None},
    ]
    monkeypatch.setattr(
        volume_service.VolumeService, "get_recent_exercise_volume", lambda self, **kw: copy.deepcopy(linhas)
    )
    corpo = analytics.muscle_groups_recent_exercise_volume(
        limit=None, date_from=None, date_to=None, identity=IDENTIDADE
    )
    (item,) = corpo["items"]
    assert {k: item[k] for k in linhas[0]} == linhas[0]
    assert (item["parent_group_name"], item["volume_class_id"], item["volume_class_name"]) == (
        "Core",
        None,
        VOLUME_CLASS_UNCLASSIFIED_LABEL,
    )
    assert hierarquia == [1]


def test_rota_training_volume_anota_so_a_serie(monkeypatch, hierarquia):
    overview = {"days": 14, "items": [{"muscle_group": "Costas", "muscle_group_id": 6, "volume_status": "ideal"}]}
    serie = {"days": 14, "items": [{"period": "2026-10-10", "muscle_group": "Antebraço anterior", "sets_count": 4,
                                    "reps_total": 40, "volume_kg": 800.0}]}
    monkeypatch.setattr(volume_service.VolumeService, "get_volume_breakdown", lambda self, days, user_id: copy.deepcopy(overview))
    monkeypatch.setattr(volume_service.VolumeService, "get_volume_trend", lambda self, days, user_id: copy.deepcopy(serie))
    corpo = dashboard_modules.training_volume(days=14, identity=IDENTIDADE)
    assert corpo["overview"] == overview  # status e metas por grupo nao se agregam: sem campo novo
    (item,) = corpo["trend"]["items"]
    assert {k: item[k] for k in serie["items"][0]} == serie["items"][0]
    assert (item["parent_group_id"], item["parent_group_name"], item["volume_class_name"]) == (
        50,
        "Antebraço",
        "Pequenos",
    )
    assert hierarquia == [1]


def test_dono_exporta_os_simbolos():
    for nome in ("GroupHierarchy", "VOLUME_CLASS_UNCLASSIFIED_LABEL", "resolve_group_hierarchy",
                 "hierarchy_of_group_name", "index_hierarchy_by_name"):
        assert nome in mv.__all__
