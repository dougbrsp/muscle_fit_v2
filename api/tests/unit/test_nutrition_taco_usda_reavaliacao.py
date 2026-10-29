"""M85: campos `*` ("análise em reavaliação") da TACO 4ª ed. preenchidos com o
alimento equivalente do USDA FoodData Central (migração 084; decisões do usuário
de 2026-10-17 e, para o iogurte sabor abacaxi ficar sem valor, de 2026-10-18).

- O gerador (`scripts/gerar_taco_usda_reavaliacao.py`) reproduz o bloco da 084
  OFFLINE a partir do snapshot versionado do USDA e da planilha da TACO, pelo
  motor do L3b (`external_sources.map_usda_food`): mesmo SHA-256, mesmos valores.
- Cobertura: todo item com `*` tem equivalente ou motivo; só campos `*` mudam;
  `NA` (sais, fibra do leite) continua NULL.
- Proveniência: o JSON gravado é o do dono (`catalog.FieldSource`) e a nota da
  tela vem de `labels`.
- Diário (L4): um café com leite passa a fechar o dia (energia + 3 macros
  completos: entra no banco e na aderência); só com a 081 o dia ficava fora.
- Rotas: detalhe/busca do alimento e preview/gravação da refeição devolvem
  `field_sources`; sem a coluna da 084, 503 citando a 084.

Rodar: cd api && python -m pytest tests/unit/test_nutrition_taco_usda_reavaliacao.py
"""

from __future__ import annotations

import functools
import importlib.util
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import nutrition_foods as foods_mod
from app import nutrition_meals as meals_mod
from app import nutrition_schema
from app.domain.nutrition import catalog, labels
from app.domain.nutrition import external_sources as fontes
from app.domain.nutrition import meal_math as mm
from app.domain.nutrition import weekly_budget as wb
from app.security import Identity, get_current_identity
from tests.support.nutrition_diary_fake import FakeDiaryDB
from tests.support.nutrition_foods_fake import FakeFoodsDB

GERADOR = Path(__file__).resolve().parents[2] / "scripts" / "gerar_taco_usda_reavaliacao.py"
M081 = m.DEFAULT_MIGRATIONS_DIR / "081_food_catalog_seed_taco.py"
M084 = m.DEFAULT_MIGRATIONS_DIR / "084_food_items_taco_usda_reavaliacao.py"
CAFE, LEITE_INTEGRAL, LEITE_DESNATADO, IOGURTE_ABACAXI, COCO_VERDE, SAL_GROSSO = "471", "458", "457", "450", "591", "517"
DIA = date(2026, 10, 17)


@functools.lru_cache(maxsize=None)
def _globals(path) -> dict:
    return m.load_python_migration(path).__globals__


@functools.lru_cache(maxsize=1)
def _gerador():
    spec = importlib.util.spec_from_file_location("gerar_taco_usda_reavaliacao", GERADOR)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo  # `@dataclass` resolve o módulo por sys.modules
    spec.loader.exec_module(modulo)
    return modulo


def _taco_081() -> dict[str, dict]:
    g = _globals(M081)
    return {linha[0]: dict(zip(g["SEED_COLUMNS"], linha)) for linha in g["SEED_ROWS"]}


def _taco_084() -> dict[str, dict]:
    """O catálogo da TACO depois da 081 + 084 (valores e proveniência)."""
    catalogo = _taco_081()
    for registro in _globals(M084)["fills"]():
        catalogo[registro["source_id"]] = {
            **catalogo[registro["source_id"]],
            **{campo: str(valor) for campo, valor in registro["values"].items()},
            "field_sources_json": registro["json"],
        }
    return catalogo


def _fill(source_id: str) -> dict:
    return next(r for r in _globals(M084)["fills"]() if r["source_id"] == source_id)


# ---------------------------------------------------------------------------
# Gerador, snapshot do USDA e planilha
# ---------------------------------------------------------------------------


def test_gerador_reproduz_o_bloco_da_084_offline_pelo_motor_do_l3b():
    gerador = _gerador()
    bloco, itens = gerador.bloco_esperado()
    assert gerador.bloco_atual(M084.read_text(encoding="utf-8")) == bloco
    g = _globals(M084)
    assert g["USDA_SNAPSHOT_SHA256"] == gerador.sha256_texto(gerador.SNAPSHOT)
    assert g["TACO_SOURCE_FILE_SHA256"] == _globals(M081)["SOURCE_FILE_SHA256"]
    assert g["USDA_SNAPSHOT_FILE"] == gerador.SNAPSHOT_REL
    assert [str(item.numero) for item in itens] == [r["source_id"] for r in g["fills"]()]


def test_valores_sao_os_do_detalhe_do_usda_mapeado_pelo_motor():
    """Cada valor gravado = o campo do `map_usda_food` sobre o payload do snapshot,
    em 2 casas; a versão e a citação também vêm do motor (tipo SR Legacy)."""
    gerador = _gerador()
    snapshot = gerador.ler_snapshot()
    assert snapshot["retrieved_on"] == _globals(M084)["RETRIEVED_ON"]
    for numero, fdc_id in gerador.EQUIVALENTES_USDA.items():
        alimento = fontes.map_usda_food(snapshot["foods"][str(fdc_id)], fdc_id)
        assert alimento.ok and alimento.energy_origin == fontes.EnergyOrigin.SOURCE_KCAL
        assert alimento.source_version.startswith("sr_legacy-")
        registro = _fill(str(numero))
        for campo, valor in registro["values"].items():
            assert valor == Decimal(str(alimento.values[campo])).quantize(Decimal("0.01")), (numero, campo)
        origem = catalog.parse_field_sources(registro["json"])[0]
        assert (origem.source, origem.source_id, origem.source_version) == (
            catalog.FoodSource.USDA, str(fdc_id), alimento.source_version)
        assert origem.citation == alimento.citation and origem.license == catalog.USDA_LICENSE
        assert origem.retrieved_on == snapshot["retrieved_on"] and origem.name == alimento.name


def test_todo_item_em_reavaliacao_tem_equivalente_ou_motivo_e_so_campos_estrela_mudam():
    gerador = _gerador()
    reavaliacao = gerador.em_reavaliacao()
    gerador.conferir_cobertura(reavaliacao)
    assert set(reavaliacao) == {280, 330, 331, 415, 416, 417, 450, 457, 458, 591}
    assert set(gerador.EQUIVALENTES_USDA) == {457, 458}
    assert 450 in gerador.SEM_EQUIVALENTE and 591 in gerador.SEM_EQUIVALENTE  # decisão do usuário (2026-10-18)
    for registro in _globals(M084)["fills"]():
        assert set(registro["values"]) <= set(reavaliacao[int(registro["source_id"])]), registro["source_id"]
    # Os 2 sais sem energia são `NA` (não aplicável), não `*`: nunca entram.
    assert 516 not in reavaliacao and 517 not in reavaliacao
    with pytest.raises(gerador.DadosInvalidos):
        gerador.conferir_cobertura({**reavaliacao, 999: ("energy_kcal",)})


def test_linha_final_passa_no_catalogo_e_erro_falha_alto():
    """O gerador valida a linha como fica no banco (TACO medido + USDA) pelas
    regras de entrada do catálogo; os 2 leites passam sem aviso de Atwater."""
    gerador = _gerador()
    _, itens = gerador.bloco_esperado()
    assert [item.avisos for item in itens] == [(), ()]
    linha_taco = gerador.linhas_da_taco()[458]
    with pytest.raises(gerador.DadosInvalidos, match="não passa no catálogo"):
        gerador.validar_linha_final(458, linha_taco, [("protein_g", Decimal("120.00"))])


def test_na_continua_null_depois_da_084():
    taco = _taco_084()
    assert taco[SAL_GROSSO]["energy_kcal"] is None  # sal: energia NA
    assert taco[LEITE_INTEGRAL]["fiber_g"] is None  # leite: fibra NA na TACO
    assert taco[LEITE_INTEGRAL]["sodium_mg"] == "63.76"  # medido pela TACO: fica
    assert taco[COCO_VERDE]["energy_kcal"] is None  # sem equivalente no FDC: fica NULL
    assert taco[IOGURTE_ABACAXI]["energy_kcal"] is None  # decisão do usuário (2026-10-18): sem valor
    assert "field_sources_json" not in taco[IOGURTE_ABACAXI]
    leite = mm.complete_missing_nutrients(mm.nutrients_from_values(taco[LEITE_INTEGRAL]))
    assert leite.unknown == ("fiber_g",) and leite.inferred_zero == ()


# ---------------------------------------------------------------------------
# Proveniência e nota
# ---------------------------------------------------------------------------


def test_proveniencia_gravada_e_a_do_dono_com_a_nota_da_tela():
    origem = catalog.parse_field_sources(_fill(LEITE_INTEGRAL)["json"])
    assert len(origem) == 1 and catalog.field_sources_json(origem) == _fill(LEITE_INTEGRAL)["json"]
    assert origem[0].fields == ("energy_kcal", "carbohydrates_g", "protein_g", "total_fat_g")
    assert origem[0].reason == catalog.FieldSourceReason.TACO_UNDER_REVIEW
    nota = labels.field_source_note(origem[0])
    assert nota == (
        "Valor energético, carboidratos, proteínas e gorduras totais: USDA FoodData Central "
        "(em reavaliação na TACO 4ª ed.)."
    )
    publico = labels.field_sources_public(_fill(LEITE_INTEGRAL)["json"])
    assert publico[0]["note"] == nota and publico[0]["source_label"] == "USDA FoodData Central"
    assert publico[0]["source_id"] == "172217"
    assert publico[0]["detail"] == (
        "Milk, whole, 3.25% milkfat, without added vitamin A and vitamin D · " + origem[0].citation
        + " · Licença: " + catalog.USDA_LICENSE + " · Consulta em 18/10/2026"
    )
    assert catalog.field_sources_snapshot(_fill(LEITE_INTEGRAL)["json"]) == [origem[0].to_dict()]
    assert catalog.field_sources_snapshot(None) == []


@pytest.mark.parametrize("bruto", [None, "", "nao e json", "{}", "[1, 2]", [{"source": "usda", "fields": ["x"]}],
                                   [{"source": "outra", "fields": ["energy_kcal"]}], b"[]"])
def test_proveniencia_ausente_ou_malformada_nao_quebra(bruto):
    assert catalog.parse_field_sources(bruto) == ()
    assert labels.field_sources_public(bruto) == []


def test_um_campo_tem_uma_origem_so():
    duas = [{"source": "usda", "fields": ["energy_kcal", "protein_g"]}, {"source": "off", "fields": ["protein_g", "fiber_g"]}]
    origens = catalog.parse_field_sources(json.dumps(duas))
    assert [o.fields for o in origens] == [("energy_kcal", "protein_g"), ("fiber_g",)]


# ---------------------------------------------------------------------------
# Diário (L4): completude do dia
# ---------------------------------------------------------------------------


def _dia_com_cafe_com_leite(catalogo: dict[str, dict]) -> mm.NutritionTotals:
    itens = [
        mm.item_nutrition(mm.nutrients_from_values(catalogo[CAFE]), mm.resolve_amount(100, "g", "g")),
        mm.item_nutrition(mm.nutrients_from_values(catalogo[LEITE_INTEGRAL]), mm.resolve_amount(200, "g", "g")),
    ]
    return mm.combine_totals([mm.sum_items(itens)])


def test_cafe_com_leite_passa_a_fechar_o_dia():
    """Antes (só a 081): energia, proteína, carboidrato e gordura do leite NULL ->
    total do dia incompleto, dia fora do banco e da aderência. Depois da 084: os
    quatro completos -> dia registrado. A fibra segue desconhecida (café e leite
    sem fibra na TACO: vazio e `NA`), e a fibra não entra no dia registrado."""
    antes = _dia_com_cafe_com_leite(_taco_081())
    assert wb.intake_macros(antes) is None
    assert {"energy_kcal", "protein_g", "carbohydrates_g", "total_fat_g"} <= set(antes.incomplete_fields)
    relatorio_antes = wb.week_report([DIA], [None], [antes])
    assert relatorio_antes.incomplete_days == (DIA,) and relatorio_antes.logged_days == ()

    depois = _dia_com_cafe_com_leite(_taco_084())
    ingestao = wb.intake_macros(depois)
    assert ingestao is not None
    # 100 g de café (9,07 kcal) + 200 g de leite integral (61 kcal/100 g do USDA = 122 kcal).
    assert ingestao.kcal == pytest.approx(9.07 + 122.0)
    assert ingestao.protein_g == pytest.approx(0.71 + 6.30)
    assert depois.incomplete_fields == ("fiber_g",)
    relatorio = wb.week_report([DIA], [None], [depois])
    assert relatorio.logged_days == (DIA,) and relatorio.incomplete_days == ()


# ---------------------------------------------------------------------------
# Rotas: alimento e refeição devolvem a proveniência
# ---------------------------------------------------------------------------

LEITE_084 = {campo: float(valor) for campo, valor in _fill(LEITE_INTEGRAL)["values"].items()}


def _identidade(user_id: int = 2):
    return lambda: Identity(user_id=user_id, role="regular", source="session")


def test_detalhe_e_busca_do_alimento_mostram_a_origem_dos_valores(monkeypatch):
    banco = FakeFoodsDB()
    leite = banco.add_food("Leite, de vaca, integral", source_id=LEITE_INTEGRAL, sodium_mg=63.76,
                           field_sources_json=_fill(LEITE_INTEGRAL)["json"], **LEITE_084)
    banco.add_food("Leite, de vaca, achocolatado", energy_kcal=82.82, carbohydrates_g=14.16, protein_g=2.10,
                   total_fat_g=2.17)
    monkeypatch.setattr(foods_mod, "get_conn", banco.conn)
    monkeypatch.setattr(foods_mod, "require_food_tables", lambda cur: None)
    app = FastAPI()
    app.include_router(foods_mod.router)
    app.dependency_overrides[get_current_identity] = _identidade()
    cliente = TestClient(app, raise_server_exceptions=False)

    detalhe = cliente.get(f"/nutrition/foods/{leite}").json()
    assert detalhe["per_100"]["energy_kcal"] == 61.0 and detalhe["per_100"]["sodium_mg"] == 63.76
    assert detalhe["citation"] == catalog.TACO_CITATION
    assert [o["source_id"] for o in detalhe["field_sources"]] == ["172217"]
    assert detalhe["field_sources"][0]["note"].startswith("Valor energético, carboidratos")
    busca = cliente.get("/nutrition/foods/search", params={"q": "leite"}).json()["items"]
    por_nome = {item["name"]: item["field_sources"] for item in busca}
    assert por_nome["Leite, de vaca, achocolatado"] == []
    assert por_nome["Leite, de vaca, integral"][0]["source"] == "usda"


def test_refeicao_com_leite_mostra_a_origem_e_fecha_a_energia(monkeypatch):
    banco = FakeDiaryDB()
    monkeypatch.setattr(meals_mod, "get_conn", banco.conn)
    monkeypatch.setattr(meals_mod, "require_diary_tables", lambda cur: None)
    monkeypatch.setattr(meals_mod, "require_food_tables", lambda cur: None)
    cafe = banco.add_food("Café, infusão 10%", energy_kcal=9.07, carbohydrates_g=1.48, protein_g=0.71,
                          total_fat_g=0.07, sodium_mg=1.03)
    leite = banco.add_food("Leite, de vaca, integral", sodium_mg=63.76, field_sources_json=_fill(LEITE_INTEGRAL)["json"],
                           **LEITE_084)
    app = FastAPI()
    app.include_router(meals_mod.router)
    app.dependency_overrides[get_current_identity] = _identidade()
    cliente = TestClient(app, raise_server_exceptions=False)
    corpo = {"local_date": DIA.isoformat(), "meal_slot": "breakfast",
             "items": [{"food_id": cafe, "quantity": 100, "unit": "g"}, {"food_id": leite, "quantity": 200, "unit": "g"}]}

    previa = cliente.post("/nutrition/meals/preview", json=corpo).json()
    assert previa["items"][0]["field_sources"] == []
    assert previa["items"][1]["field_sources"][0]["note"] == labels.field_source_note(
        catalog.parse_field_sources(_fill(LEITE_INTEGRAL)["json"])[0])
    assert previa["totals"]["incomplete_fields"] == ["fiber_g"]
    assert previa["totals"]["nutrients"]["energy_kcal"] == pytest.approx(9.07 + 122.0)

    gravada = cliente.post("/nutrition/meals", json=corpo)
    assert gravada.status_code == 201, gravada.text
    # O snapshot congelado leva a proveniência (E4): a refeição relida mostra a nota.
    assert gravada.json()["items"][1]["field_sources"][0]["source_id"] == "172217"
    snapshots = [json.loads(item["catalog_snapshot_json"])["food"] for item in banco.items.values()]
    assert [s["field_sources"] for s in snapshots][0] == []
    assert snapshots[1]["field_sources"] == [o.to_dict() for o in catalog.parse_field_sources(_fill(LEITE_INTEGRAL)["json"])]


def test_refeicao_antiga_sem_proveniencia_no_snapshot_continua_igual():
    """Item gravado antes da 084 (snapshot sem `field_sources`) não é reescrito."""
    linha = {"id": 1, "position": 0, "food_id": 3, "portion_id": None, "item_label": "Leite", "quantity": 200,
             "unit": "g", "amount": 200, "basis": "g", "nature": "DERIVED", "limitations_json": "[]",
             "catalog_snapshot_json": json.dumps({"food": {"source": "taco", "citation": catalog.TACO_CITATION},
                                                  "portion": None})}
    publico = meals_mod._item_row_public(linha)
    assert publico["field_sources"] == [] and publico["nutrients"]["energy_kcal"] is None


# ---------------------------------------------------------------------------
# Janela de deploy: código novo antes da 084
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, *, coluna: bool):
        self.coluna = coluna
        self.sql: list[str] = []
        self._linha = None

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "information_schema.TABLES" in sql:
            self._linha = {"n": len(params)}
        elif "information_schema.COLUMNS" in sql:
            assert params == ("food_items", "field_sources_json")
            self._linha = {"n": 1 if self.coluna else 0}
        elif sql.startswith("SELECT DATABASE()"):
            self._linha = {"db": "schema_m85"}
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self._linha


def test_sem_a_coluna_da_084_o_catalogo_responde_503_citando_a_084():
    nutrition_schema.reset_nutrition_tables_cache()
    with pytest.raises(Exception) as erro:
        nutrition_schema.require_food_tables(_Cursor(coluna=False))
    assert erro.value.status_code == 503
    assert erro.value.detail["error"] == "food_catalog_migration_pending" and erro.value.detail["migration"] == "084"
    # Ausência não fica em cache (a migração pode rodar a qualquer momento); presença fica.
    com_coluna = _Cursor(coluna=True)
    nutrition_schema.require_food_tables(com_coluna)
    depois = _Cursor(coluna=False)
    nutrition_schema.require_food_tables(depois)
    assert not any("information_schema" in sql for sql in depois.sql)
    nutrition_schema.reset_nutrition_tables_cache()
