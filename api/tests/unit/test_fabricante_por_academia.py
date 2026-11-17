"""Fabricante do aparelho por academia (#188): dono `domain/equipment_context`,
edicao em PATCH /gym-equipment-catalog/{link_id} (dono ou admin), leitura
aditiva no catalogo da academia, no briefing e nas opcoes de troca."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app import exercise_briefing, workout_plan_equipment as wpe
from app.domain import equipment_context as ec
from app.request_models import GymEquipmentManufacturerIn
from tests.unit import test_gyms_permissoes_rbac as rbac
from tests.unit.test_gyms_permissoes_rbac import ctx, _chamar  # noqa: F401  (fixture)

# ---------------------------------------------------------------------------
# Dono da regra
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gym,catalogo,esperado",
    [
        ("Technogym", "Life Fitness", ("Technogym", "gym")),
        ("  Hammer  ", None, ("Hammer", "gym")),
        (None, "Life Fitness", ("Life Fitness", "catalog")),
        ("   ", "Life Fitness", ("Life Fitness", "catalog")),
        ("", "", (None, None)),
        (None, None, (None, None)),
    ],
)
def test_efetivo_academia_senao_catalogo_senao_nulo(gym, catalogo, esperado):
    assert ec.effective_manufacturer(gym, catalogo) == esperado


def test_mesma_maquina_em_duas_academias_com_fabricantes_diferentes():
    a = ec.manufacturer_fields({"gym_equipment_catalog_id": 1, "gym_manufacturer": "Technogym",
                                "catalog_manufacturer": "Matrix", "gym_owner_user_id": 10}, lambda o: o == 10)
    b = ec.manufacturer_fields({"gym_equipment_catalog_id": 2, "gym_manufacturer": "Hammer",
                                "catalog_manufacturer": "Matrix", "gym_owner_user_id": 11}, lambda o: o == 10)
    assert (a["manufacturer"], a["can_edit_manufacturer"]) == ("Technogym", True)
    assert (b["manufacturer"], b["can_edit_manufacturer"]) == ("Hammer", False)


def test_sem_linha_no_catalogo_da_academia_ninguem_edita():
    campos = ec.manufacturer_fields({"catalog_manufacturer": "Matrix"}, lambda _o: True)
    assert campos == {"gym_equipment_catalog_id": None, "manufacturer": "Matrix",
                      "manufacturer_source": "catalog", "can_edit_manufacturer": False}


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def test_entrada_valida_tamanho_controle_e_vazio():
    assert GymEquipmentManufacturerIn(manufacturer="  Technogym ").manufacturer == "Technogym"
    assert GymEquipmentManufacturerIn(manufacturer="   ").manufacturer is None
    assert "manufacturer" not in GymEquipmentManufacturerIn().model_fields_set
    GymEquipmentManufacturerIn(manufacturer="x" * 100)
    for ruim in ("x" * 101, "Tech\x00gym", "Tech\ngym", "a\x7f"):
        with pytest.raises(ValidationError):
            GymEquipmentManufacturerIn(manufacturer=ruim)
    with pytest.raises(ValidationError):
        GymEquipmentManufacturerIn(manufacturer="ok", is_active=1)


# ---------------------------------------------------------------------------
# PATCH /gym-equipment-catalog/{link_id}
# ---------------------------------------------------------------------------


def _updates(cur):
    return [(sql, params) for sql, params in cur.executed if sql.startswith("UPDATE")]


@pytest.mark.parametrize("quem", ["dono", "admin"])
def test_dono_e_admin_gravam_aparado(ctx, quem):  # noqa: F811
    client, cur, _ = ctx
    resp = _chamar(client, quem, "PATCH", "/gym-equipment-catalog/88", {"manufacturer": " Technogym "})
    assert resp.status_code == 200, resp.text
    assert _updates(cur) == [("UPDATE gym_equipment_catalog SET manufacturer = %s WHERE id = %s", ("Technogym", 88))]
    assert resp.json()["item"]["manufacturer"] is None  # duble nao projeta a coluna nova (so le de volta)


@pytest.mark.parametrize("quem", ["terceiro", "pt"])
def test_nao_dono_recebe_403_e_nada_e_gravado(ctx, quem):  # noqa: F811
    client, cur, _ = ctx
    resp = _chamar(client, quem, "PATCH", "/gym-equipment-catalog/88", {"manufacturer": "Technogym"})
    assert resp.status_code == 403
    assert cur.writes() == []


def test_campo_ausente_nao_altera_e_vazio_volta_ao_padrao(ctx):  # noqa: F811
    client, cur, _ = ctx
    assert _chamar(client, "dono", "PATCH", "/gym-equipment-catalog/88", {}).status_code == 200
    assert cur.writes() == []
    assert _chamar(client, "dono", "PATCH", "/gym-equipment-catalog/88", {"manufacturer": ""}).status_code == 200
    assert _updates(cur)[-1][1] == (None, 88)
    assert _chamar(client, "dono", "PATCH", "/gym-equipment-catalog/88", {"manufacturer": None}).status_code == 200
    assert _updates(cur)[-1][1] == (None, 88)


@pytest.mark.parametrize("corpo", [{"manufacturer": "x" * 101}, {"manufacturer": "a\tb"}, {"is_active": 1}])
def test_entrada_invalida_e_422_sem_gravar(ctx, corpo):  # noqa: F811
    client, cur, _ = ctx
    assert _chamar(client, "dono", "PATCH", "/gym-equipment-catalog/88", corpo).status_code == 422
    assert cur.writes() == []


def test_editar_fabricante_nao_reativa_nem_cria_vinculo(ctx, monkeypatch):  # noqa: F811
    client, cur, _ = ctx
    inativo = [{"id": 88, "gym_id": 5, "equipment_id": 9, "is_active": 0}]
    monkeypatch.setitem(rbac.TABELAS, "gym_equipment_catalog", inativo)
    assert _chamar(client, "dono", "PATCH", "/gym-equipment-catalog/88", {"manufacturer": "X"}).status_code == 200
    assert all("is_active" not in sql and not sql.startswith("INSERT") for sql in cur.writes())
    assert _chamar(client, "dono", "PATCH", "/gym-equipment-catalog/999", {"manufacturer": "X"}).status_code == 404
    assert len(cur.writes()) == 1


def test_lista_do_catalogo_traz_cru_padrao_efetivo_e_posse(ctx, monkeypatch):  # noqa: F811
    client, cur, _ = ctx
    linhas = [
        {"id": 88, "gym_id": 5, "gym_name": "A", "equipment_id": 9, "is_active": 1,
         "gym_equipment_catalog_id": 88, "gym_manufacturer": "Technogym", "catalog_manufacturer": "Matrix",
         "gym_owner_user_id": rbac.DONO},
        {"id": 90, "gym_id": 7, "gym_name": "B", "equipment_id": 9, "is_active": 1,
         "gym_equipment_catalog_id": 90, "gym_manufacturer": None, "catalog_manufacturer": "Matrix",
         "gym_owner_user_id": rbac.TERCEIRO},
    ]
    monkeypatch.setattr(cur, "fetchall", lambda: [dict(r) for r in linhas])
    itens = _chamar(client, "dono", "GET", "/gym-equipment-catalog").json()["items"]
    assert [(i["manufacturer"], i["catalog_manufacturer"], i["effective_manufacturer"], i["manufacturer_source"],
             i["can_edit_manufacturer"]) for i in itens] == [
        ("Technogym", "Matrix", "Technogym", "gym", True),
        (None, "Matrix", "Matrix", "catalog", False),
    ]
    assert all("gym_owner_user_id" not in i for i in itens)
    itens_admin = _chamar(client, "admin", "GET", "/gym-equipment-catalog").json()["items"]
    assert [i["can_edit_manufacturer"] for i in itens_admin] == [True, True]


# ---------------------------------------------------------------------------
# Briefing e opcoes de troca
# ---------------------------------------------------------------------------


class _Cur:
    def __init__(self, row):
        self.row, self.sql = row, ""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return self.row


def test_briefing_traz_fabricante_efetivo_e_posse():
    cur = _Cur({"gym_equipment_id": 55, "equipment_id": 9, "gym_id": 5, "name": "Leg", "photo_url": None,
                "gym_equipment_catalog_id": 88, "gym_manufacturer": None, "catalog_manufacturer": "Matrix",
                "gym_owner_user_id": 10})
    eq = exercise_briefing._equipment(cur, 55, "Leg", lambda owner: owner == 10)
    assert "LEFT JOIN gym_equipment_catalog gec" in cur.sql
    assert {k: eq[k] for k in ("gym_id", "equipment_id", "gym_equipment_catalog_id", "manufacturer",
                               "manufacturer_source", "can_edit_manufacturer")} == {
        "gym_id": 5, "equipment_id": 9, "gym_equipment_catalog_id": 88, "manufacturer": "Matrix",
        "manufacturer_source": "catalog", "can_edit_manufacturer": True}
    sem_vinculo = exercise_briefing._equipment(cur, None, "Leg")
    assert sem_vinculo["manufacturer"] is None and sem_vinculo["can_edit_manufacturer"] is False


def test_opcoes_de_troca_trazem_fabricante_e_sem_identidade_ninguem_edita(monkeypatch):
    link = {"id": 11, "equipment_id": 1, "equipment_name": "A", "photo_url": None, "canonical_exercise_id": 5,
            "gym_equipment_catalog_id": 88, "gym_manufacturer": "Hammer", "catalog_manufacturer": "Matrix",
            "gym_owner_user_id": 10}
    monkeypatch.setattr(wpe, "_same_exercise_links", lambda c, g, m: [dict(link)])
    monkeypatch.setattr(wpe, "rank_gym_equipment_rows", lambda cur, rows, **k: rows)
    monkeypatch.setattr(wpe.ts, "resolve_training_safety_bulk", lambda conn, pairs: {})

    class _C:
        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    items, _, _ = wpe._ranked_equipment_options(_C(), 5, 5, 10, None, None, lambda owner: owner == 10)
    assert {k: items[0][k] for k in ("gym_id", "equipment_id", "manufacturer", "manufacturer_source",
                                     "can_edit_manufacturer")} == {
        "gym_id": 5, "equipment_id": 1, "manufacturer": "Hammer", "manufacturer_source": "gym",
        "can_edit_manufacturer": True}
    assert wpe._manufacturer_editor(None)(10) is False


# ---------------------------------------------------------------------------
# Rodada 1: posse nas opcoes da ficha, entrada Cf e troca do Modo Treino
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("quem", "esperado"), [("dono", True), ("terceiro", False), ("admin", True)])
def test_opcoes_da_ficha_com_identidade_seguem_a_posse(ctx, quem, esperado):  # noqa: F811
    assert wpe._manufacturer_editor(rbac.IDENTIDADES[quem])(rbac.DONO) is esperado


@pytest.mark.parametrize("valor", ["Tech\u202egym", "Ham\u200bmer", "A\u2060B", "\ufeffX"])
def test_entrada_rejeita_caracteres_de_formatacao(valor):
    with pytest.raises(ValidationError):
        GymEquipmentManufacturerIn(manufacturer=valor)


@pytest.mark.parametrize("valor", ["Técnica Força", "Матрица", "体育器材"])
def test_entrada_aceita_acentos_e_outros_alfabetos(valor):
    assert GymEquipmentManufacturerIn(manufacturer=valor).manufacturer == valor


_LINHAS_GE = [
    {"id": 1, "gym_id": 5, "equipment_id": 9, "gym_equipment_catalog_id": 88, "gym_manufacturer": "Technogym",
     "catalog_manufacturer": "Matrix", "gym_owner_user_id": rbac.DONO},
    {"id": 2, "gym_id": 5, "equipment_id": 8, "gym_equipment_catalog_id": 89, "gym_manufacturer": None,
     "catalog_manufacturer": "Matrix", "gym_owner_user_id": rbac.DONO},
    {"id": 3, "gym_id": 5, "equipment_id": 7, "gym_equipment_catalog_id": None, "gym_manufacturer": None,
     "catalog_manufacturer": None, "gym_owner_user_id": None},
]
_ESPERADO_GE = [
    (1, 88, "Technogym", "gym"),
    (2, 89, "Matrix", "catalog"),
    (3, None, None, None),
]


@pytest.mark.parametrize(("quem", "posse"), [("dono", [True, True, False]), ("terceiro", [False, False, False])])
def test_gym_equipment_traz_fabricante_e_posse_sem_mudar_ordem(ctx, monkeypatch, quem, posse):  # noqa: F811
    _client, cur, _ = ctx
    monkeypatch.setattr(cur, "fetchall", lambda: [dict(r) for r in _LINHAS_GE])
    itens = _chamar(_client, quem, "GET", "/gym-equipment?gym_id=5").json()["items"]
    assert [(i["id"], i["gym_equipment_catalog_id"], i["manufacturer"], i["manufacturer_source"]) for i in itens] == (
        _ESPERADO_GE
    )
    assert [i["can_edit_manufacturer"] for i in itens] == posse
    assert all(k not in i for i in itens for k in ("gym_owner_user_id", "gym_manufacturer", "catalog_manufacturer"))


def test_sql_de_gym_equipment_e_do_recomendador_usam_o_join_do_dono():
    import inspect

    from app import equipment_recommender as er, gyms

    for fonte in (inspect.getsource(gyms.list_gym_equipment), inspect.getsource(er._load_candidates)):
        assert "manufacturer_join_sql(" in fonte and "manufacturer_columns_sql()" in fonte
    assert "LEFT JOIN gym_equipment_catalog gec ON gec.gym_id = ge.gym_id" in ec.manufacturer_join_sql(
        "ge.gym_id", "ge.equipment_id")


def test_recomendador_traz_fabricante_e_posse(monkeypatch):
    from app import equipment_recommender as er

    monkeypatch.setattr(er, "load_user_recency", lambda *a, **k: {})
    monkeypatch.setattr(er, "load_user_stars", lambda *a, **k: {})
    monkeypatch.setattr(er.ts, "resolve_training_safety_bulk", lambda conn, pairs: {p: None for p in pairs})
    monkeypatch.setattr(er.ranking, "safety_fields", lambda s: {})
    monkeypatch.setattr(er.ranking, "rank_items", lambda items: list(items))
    linhas = [{**r, "mapping_id": 4, "mapping_aliases_raw": None} for r in _LINHAS_GE]
    itens, _ = er._enrich_and_rank(None, None, linhas, 5, 10, lambda owner: owner == rbac.DONO)
    assert [(i["id"], i["gym_equipment_catalog_id"], i["manufacturer"], i["manufacturer_source"]) for i in itens] == (
        _ESPERADO_GE
    )
    assert [i["can_edit_manufacturer"] for i in itens] == [True, True, False]
    assert "gym_owner_user_id" not in itens[0]
    fechado, _ = er._enrich_and_rank(None, None, linhas, 5, 10)
    assert [i["can_edit_manufacturer"] for i in fechado] == [False, False, False]


@pytest.mark.parametrize(("quem", "esperado"), [("dono", True), ("terceiro", False), ("admin", True)])
def test_rota_recommend_passa_a_posse_da_requisicao(ctx, monkeypatch, quem, esperado):  # noqa: F811
    from app import equipment_recommender as er

    monkeypatch.setattr(er, "recommend_for_group", lambda conn, **k: {"posse": k["can_edit_manufacturer"](rbac.DONO)})
    resp = _chamar(ctx[0], quem, "GET", "/gym-equipment/recommend?gym_id=5&primary_group=Peito")
    assert resp.json()["posse"] is esperado
