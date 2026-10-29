"""M3 - unidade de carga (kg x lb): conversao, arredondamento e a garantia de
que NADA grava libras no banco.

`exercise_sets.weight_kg` e DECIMAL(8,2) em QUILOS e continua sendo carga TOTAL
(D12). A preferencia `users.preferred_weight_unit` (migracao 040) e so de
entrada/exibicao. Este teste protege as tres coisas que poderiam quebrar essa
regra:

1. a conversao em si (fator exato, casas decimais);
2. o ida-e-volta lb -> kg -> lb, que nao pode derivar a cada edicao;
3. os caminhos de ESCRITA do servidor, que nao podem passar a aceitar libras.

O modulo `app/weight_units.py` e espelho de `frontend/src/lib/weight-units.ts`
(a tela e quem converte, de fato); o teste compara os dois lados para que uma
mudanca de fator ou de arredondamento num lado quebre aqui.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app import weight_units as wu

REPO = Path(__file__).resolve().parents[3]
FRONT_MODULE = REPO / "frontend" / "src" / "lib" / "weight-units.ts"

# Grade em lb que o usuario consegue DIGITAR (1 casa decimal), de 0.5 a 400 lb.
LB_INPUT_GRID = [Decimal(tenths) / 10 for tenths in range(5, 4001)]


# ---------------------------------------------------------------------------
# 1. conversao
# ---------------------------------------------------------------------------


def test_fator_e_a_libra_avoirdupois_exata():
    assert wu.LB_IN_KG == Decimal("0.45359237")


def test_kg_e_o_default_de_quem_nunca_escolheu_nada():
    assert wu.DEFAULT_WEIGHT_UNIT == "kg"
    assert wu.WEIGHT_UNITS == ("kg", "lb")


@pytest.mark.parametrize(
    "raw",
    [None, "", "  ", "libra", "pounds", "KGS", 42, object()],
)
def test_unidade_desconhecida_cai_para_kg(raw):
    assert wu.normalize_weight_unit(raw) == "kg"
    assert wu.is_weight_unit(raw) is False


@pytest.mark.parametrize("raw,esperado", [("kg", "kg"), ("lb", "lb"), ("KG", "kg"), (" Lb ", "lb")])
def test_unidade_valida_e_normalizada(raw, esperado):
    assert wu.normalize_weight_unit(raw) == esperado
    assert wu.is_weight_unit(raw) is True


@pytest.mark.parametrize(
    "lb,kg",
    [
        ("50", "22.68"),  # 50 lb = 22.6796185 kg
        ("100", "45.36"),
        ("45", "20.41"),
        ("1", "0.45"),
        ("135", "61.23"),
    ],
)
def test_libras_viram_quilos_com_o_fator_exato(lb, kg):
    assert wu.to_kg(lb, "lb") == Decimal(kg)


def test_quilos_so_sao_quantizados_na_precisao_da_coluna():
    assert wu.to_kg("22.5", "kg") == Decimal("22.50")
    assert wu.to_kg("22.567", "kg") == Decimal("22.57")


def test_to_kg_nunca_passa_de_duas_casas():
    """Mais casas que `DECIMAL(8,2)` seria mentira: o banco truncaria."""
    for lb in LB_INPUT_GRID:
        assert wu.to_kg(lb, "lb").as_tuple().exponent == -2


def test_um_valor_em_lb_nunca_e_gravado_como_se_fosse_kg():
    for lb in (Decimal(5), Decimal(50), Decimal(135), Decimal(315)):
        assert wu.to_kg(lb, "lb") < lb


def test_exibicao_em_lb_tem_uma_casa_e_nao_22_6796():
    assert wu.from_kg("22.68", "lb") == Decimal("50.0")
    assert wu.from_kg("20", "lb") == Decimal("44.1")
    assert wu.format_weight("22.68", "lb") == "50 lb"
    assert wu.format_weight("20", "kg") == "20 kg"
    assert wu.format_weight("22.5", "kg") == "22.5 kg"


# ---------------------------------------------------------------------------
# 2. round-trip
# ---------------------------------------------------------------------------


def test_lb_para_kg_para_lb_devolve_o_valor_digitado_em_toda_a_grade():
    for lb in LB_INPUT_GRID:
        assert wu.from_kg(wu.to_kg(lb, "lb"), "lb") == lb


def test_salvar_reabrir_salvar_em_lb_e_ponto_fixo():
    """O risco do M3: digitar 50 lb, salvar 22.68 kg, reabrir vendo 49.9 lb,
    salvar de novo... ate o peso fugir do real. Nao acontece - depois da
    primeira gravacao o par (kg exibido, kg gravado) nao muda mais."""
    for lb in LB_INPUT_GRID:
        primeira = wu.to_kg(lb, "lb")
        exibido, gravado = wu.from_kg(primeira, "lb"), primeira
        for _ in range(25):
            gravado = wu.to_kg(exibido, "lb")
            exibido = wu.from_kg(gravado, "lb")
        assert gravado == primeira
        assert exibido == lb


def test_kg_para_exibicao_para_kg_e_identidade():
    for cents in range(25, 30001, 7):
        kg = Decimal(cents) / 100
        assert wu.to_kg(wu.from_kg(kg, "kg"), "kg") == kg


# ---------------------------------------------------------------------------
# 3. paridade com o modulo da tela (quem de fato converte)
# ---------------------------------------------------------------------------


def _front_source() -> str:
    return FRONT_MODULE.read_text(encoding="utf-8")


def test_a_tela_usa_o_mesmo_fator_e_as_mesmas_casas():
    src = _front_source()
    assert "export const LB_IN_KG = 0.45359237;" in src
    assert "DISPLAY_DECIMALS: Record<WeightUnit, number> = { kg: 2, lb: 1 }" in src
    assert f"kg: {wu.DISPLAY_DECIMALS['kg']}, lb: {wu.DISPLAY_DECIMALS['lb']}" in src


def test_a_tela_converte_para_kg_antes_de_salvar_e_nunca_envia_a_unidade():
    """`SetLoggerValues.weight_kg` (o payload da serie) sai do card em QUILOS:
    e o `kg` ancorado do campo, nao o numero digitado."""
    card = (REPO / "frontend" / "src" / "app" / "(fullscreen)" / "modo-treino" / "SetLoggerCard.tsx").read_text(
        encoding="utf-8"
    )
    # os tres `onSave({...})` do card (aquecimento/ajuste, aplicacao de tecnica
    # e serie de trabalho) mandam o kg ancorado. Fase 5: a aplicacao da tecnica
    # deixou de ter carga estatica e passou a ter o campo editavel.
    # M102: a serie de trabalho de exercicio unilateral (E/D) e um quarto
    # payload, com a mesma carga unica em kg ancorado.
    assert card.count("weight_kg: weightField.kg ?? 0,") == 4
    assert "staticLoadKg" not in card
    # todo `weight_kg:` do card sai do kg ancorado (inclusive os payloads
    # montados fora de `onSave({`, como os ternarios do card de trabalho)
    linhas_de_carga = [
        linha.strip()
        for linha in card.splitlines()
        if linha.strip().startswith("weight_kg:") and not linha.strip().endswith("| null;")  # fora o tipo
    ]
    assert linhas_de_carga and all("weightField.kg" in linha for linha in linhas_de_carga)
    assert all("weightField.display" not in linha for linha in linhas_de_carga)
    # nenhum payload leva o numero EXIBIDO (que pode estar em libras)
    for trecho in card.split("onSave({")[1:]:
        payload = trecho.split("})")[0]
        assert "weightField.display" not in payload


# ---------------------------------------------------------------------------
# 4. nenhum caminho de escrita do servidor aceita libras
# ---------------------------------------------------------------------------

_INSERT_SETS_RE = re.compile(r"INSERT INTO exercise_sets\b", re.IGNORECASE)


def test_nenhum_caminho_de_escrita_de_serie_conhece_libras():
    """Varredura dos modulos que gravam `exercise_sets` (modo treino, edicao
    pela tela de registro e importacao) + a IA que sugere carga: nenhum deles
    pode ganhar um campo em libras por engano. Todos continuam recebendo e
    gravando `weight_kg` em quilos."""
    app_dir = REPO / "api" / "app"
    suspeitos = []
    for path in sorted(app_dir.rglob("*.py")):
        if path.name == "weight_units.py":
            continue  # o unico modulo que PODE falar de libras
        source = path.read_text(encoding="utf-8")
        codigo = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        for termo in ("weight_lb", "weight_lbs", "load_lb", "lb_to_kg", "pounds"):
            if termo in codigo:
                suspeitos.append(f"{path.relative_to(REPO)}: {termo}")
    assert suspeitos == []


def test_os_tres_inserts_de_serie_continuam_gravando_weight_kg():
    escritores = {
        REPO / "api" / "app" / "workout_sessions.py": 1,
        REPO / "api" / "app" / "services.py": 2,
    }
    for path, esperado in escritores.items():
        source = path.read_text(encoding="utf-8")
        assert len(_INSERT_SETS_RE.findall(source)) == esperado, path
        for trecho in source.split("INSERT INTO exercise_sets")[1:]:
            cabecalho = trecho.split(")", 1)[0]
            assert "weight_kg" in cabecalho, path
