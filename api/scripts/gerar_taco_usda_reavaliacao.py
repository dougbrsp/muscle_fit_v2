#!/usr/bin/env python3
"""Gera os dados da migração 084: os campos `*` ("análise em reavaliação") da
TACO 4ª ed. preenchidos com o alimento equivalente do USDA FoodData Central
(M85; decisão do usuário de 2026-10-17).

Fontes:
- TACO: a mesma planilha do seed da 081, lida por `gerar_seed_taco`
  (`campos_marcados(MARCADOR_REAVALIACAO)`: quais itens e campos têm `*`);
- USDA: o detalhe de cada alimento (`GET /food/{fdcId}`, formato completo),
  baixado pelo cliente do L3b (`app.external_food_sources.usda_food`) e
  versionado em `docs/nutricao/fontes/usda_taco_reavaliacao.json` com a data da
  consulta. O mapeamento para os campos do catálogo (ids de nutriente, unidade,
  energia, base de 100 g, citação e versão) é o do motor
  (`app.domain.nutrition.external_sources.map_usda_food`); o arredondamento
  para 2 casas é o da 081 (`gerar_seed_taco.converter_valor`); a proveniência
  gravada por linha é a de `catalog.FieldSource`/`catalog.field_sources_json`.

Regras:
- Só os campos `*` mudam; o que a TACO mediu continua da TACO (ex.: sódio do
  leite). `NA` e célula vazia continuam NULL (nunca entram aqui).
- Todo item com `*` está em `EQUIVALENTES_USDA` (preenche) ou em
  `SEM_EQUIVALENTE` (fica NULL, com o motivo): item novo com `*` falha alto.
- Campo `*` que o USDA também não informa fica NULL (aparece no resumo).
- A linha final (o que a TACO mediu + o preenchido) passa pelas regras de
  entrada do catálogo (`catalog.validate_food_values`): erro falha alto; aviso
  (Atwater, subtotal) aparece no resumo.

Os dados ficam DENTRO da migração (`084_food_items_taco_usda_reavaliacao.py`,
entre os marcadores `# >>> DADOS GERADOS` e `# <<< DADOS GERADOS`), literais e
sem rede na migração; o checksum do runner cobre os dados. Migração aplicada
nunca é editada: equivalente diferente ou dado novo do USDA vira migração NOVA.

Uso (a partir de `api/`):
    python scripts/gerar_taco_usda_reavaliacao.py              # resumo, sem gravar
    python scripts/gerar_taco_usda_reavaliacao.py --verificar  # sai 1 se o bloco divergir
    # Refaz o snapshot do USDA (rede; chave em USDA_API_KEY, nunca impressa):
    python scripts/gerar_taco_usda_reavaliacao.py --baixar
    # Só enquanto a 084 não foi aplicada em ambiente nenhum:
    python scripts/gerar_taco_usda_reavaliacao.py --gravar --migracao-nao-aplicada
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
API_DIR = SCRIPTS_DIR.parent
REPO_DIR = API_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.domain.nutrition import catalog  # noqa: E402
from app.domain.nutrition import external_sources as fontes  # noqa: E402

SNAPSHOT = REPO_DIR / "docs" / "nutricao" / "fontes" / "usda_taco_reavaliacao.json"
SNAPSHOT_REL = "docs/nutricao/fontes/usda_taco_reavaliacao.json"
MIGRACAO = API_DIR / "db" / "migrations" / "084_food_items_taco_usda_reavaliacao.py"
OPERACAO_USDA = "GET https://api.nal.usda.gov/fdc/v1/food/{fdcId} (formato completo)"

# Item da TACO em reavaliação -> fdcId do equivalente no FDC (Foundation ou SR
# Legacy, os tipos que o motor aceita). Escolha registrada no PR do M85.
EQUIVALENTES_USDA: dict[int, int] = {
    # Leite, de vaca, desnatado, UHT -> "Milk, nonfat, fluid, without added vitamin A and vitamin D (fat
    # free or skim)" (SR Legacy): sem fortificação, como o leite brasileiro; UHT não muda os macros.
    457: 173432,
    # Leite, de vaca, integral -> "Milk, whole, 3.25% milkfat, without added vitamin A and vitamin D"
    # (SR Legacy): mesma composição do 171265 (com vitamina D adicionada), sem fortificação.
    458: 172217,
}

# Itens com `*` que continuam NULL: o FDC (Foundation/SR Legacy) não tem equivalente.
SEM_EQUIVALENTE: dict[int, str] = {
    280: "bacalhau salgado REFOGADO: o FDC só tem o bacalhau seco e salgado cru (carboidrato 0); "
    "o refogado da TACO tem 1,22 g de carboidrato dos ingredientes do refogado",
    330: "almôndega bovina crua: o FDC só tem 'Meatballs, frozen, Italian style' (produto pronto com "
    "farinha e queijo, 2,3 g de fibra), sem versão crua",
    331: "almôndega bovina frita: mesmo motivo do item 330, sem versão frita",
    415: "hambúrguer bovino industrial cru (4,15 g de carboidrato): o FDC só tem hambúrguer de carne "
    "moída pura (carboidrato e fibra 0)",
    416: "hambúrguer bovino industrial frito (6,32 g de carboidrato): mesmo motivo do item 415",
    417: "hambúrguer bovino industrial grelhado (11,33 g de carboidrato): mesmo motivo do item 415",
    # Decisão do usuário (2026-10-18): o 450 fica sem valor, como o coco verde.
    450: "iogurte sabor abacaxi: o FDC não tem iogurte de abacaxi, e o iogurte de frutas mais próximo ('Yogurt, "
    "fruit, low fat, 9 g protein/8 oz', fdcId 170889, ~99 kcal) é outro produto: os iogurtes de sabor da própria "
    "TACO têm ~68–70 kcal (morango 451, pêssego 452)",
    591:"coco verde: o FDC só tem a polpa MADURA ('Nuts, coconut meat, raw', 354 kcal; a TACO já a "
    "tem como 'Coco, cru', item 590) e a água de coco (TACO 478)",
}

AVISO_MIGRACAO_APLICADA = (
    "--gravar reescreve a migração no lugar: só antes de ela ser aplicada em qualquer ambiente (o runner "
    "registra o checksum e recusa arquivo alterado). Confirme com --migracao-nao-aplicada; depois disso, "
    "equivalente ou dado novo do USDA viram migração nova (ver test_migration_084_plano.py, CHECKSUM_CONGELADO)."
)

INICIO_BLOCO = "# >>> DADOS GERADOS por api/scripts/gerar_taco_usda_reavaliacao.py (nao editar a mao) >>>"
FIM_BLOCO = "# <<< DADOS GERADOS <<<"


class DadosInvalidos(Exception):
    pass


def _seed_taco() -> ModuleType:
    """O gerador da 081 (planilha, marcadores e arredondamento são dele)."""
    nome = "gerar_seed_taco"
    if nome not in sys.modules:
        spec = importlib.util.spec_from_file_location(nome, SCRIPTS_DIR / f"{nome}.py")
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nome] = modulo  # `@dataclass` resolve o módulo por sys.modules
        spec.loader.exec_module(modulo)
    return sys.modules[nome]


def sha256_texto(caminho: Path) -> str:
    """sha256 com fim de linha normalizado para LF (como `migrate.compute_checksum`)."""
    return hashlib.sha256(caminho.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@dataclass(frozen=True)
class Preenchimento:
    """Um item da TACO: os campos `*` com o valor do USDA (2 casas) e a origem."""

    numero: int
    valores: tuple[tuple[str, Decimal], ...]
    origem: catalog.FieldSource
    sem_valor_no_usda: tuple[str, ...]
    avisos: tuple[str, ...] = ()

    def linha(self) -> tuple[str, tuple[tuple[str, str], ...], str]:
        return (
            str(self.numero),
            tuple((campo, str(valor)) for campo, valor in self.valores),
            catalog.field_sources_json([self.origem]) or "",
        )


def em_reavaliacao(caminho: Optional[Path] = None) -> dict[int, tuple[str, ...]]:
    taco = _seed_taco()
    return taco.campos_marcados(taco.MARCADOR_REAVALIACAO, caminho or taco.PLANILHA)


def conferir_cobertura(reavaliacao: dict[int, tuple[str, ...]]) -> None:
    """Todo item com `*` tem equivalente ou motivo; nada sobra nas tabelas."""
    sem_destino = sorted(set(reavaliacao) - set(EQUIVALENTES_USDA) - set(SEM_EQUIVALENTE))
    sobrando = sorted((set(EQUIVALENTES_USDA) | set(SEM_EQUIVALENTE)) - set(reavaliacao))
    duplicados = sorted(set(EQUIVALENTES_USDA) & set(SEM_EQUIVALENTE))
    if sem_destino or sobrando or duplicados:
        raise DadosInvalidos(
            f"itens `*` sem destino {sem_destino}; fora da planilha {sobrando}; nas duas tabelas {duplicados}"
        )


def ler_snapshot(caminho: Path = SNAPSHOT) -> dict[str, Any]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(dados, dict) or not isinstance(dados.get("foods"), dict) or not dados.get("retrieved_on"):
        raise DadosInvalidos(f"{caminho.name}: formato inesperado (retrieved_on + foods)")
    date.fromisoformat(str(dados["retrieved_on"]))
    return dados


def linhas_da_taco(caminho: Optional[Path] = None) -> dict[int, dict[str, Optional[Decimal]]]:
    """Item -> valores do seed da 081 (as regras de conversão são do gerador dela)."""
    taco = _seed_taco()
    return {linha.numero: dict(linha.valores) for linha in taco.ler_planilha(caminho or taco.PLANILHA)}


def validar_linha_final(numero: int, linha_taco: dict[str, Optional[Decimal]], valores: list[tuple[str, Decimal]]) -> tuple[str, ...]:
    """Regras de entrada do catálogo sobre a linha como fica no banco; erro falha
    alto, avisos voltam para o resumo."""
    final = {campo: None if valor is None else float(valor) for campo, valor in linha_taco.items()}
    final.update({campo: float(valor) for campo, valor in valores})
    validacao = catalog.validate_food_values(final, catalog.DEFAULT_FOOD_BASIS)
    if not validacao.ok:
        raise DadosInvalidos(f"item {numero}: a linha final não passa no catálogo: {validacao.errors}")
    return validacao.warnings


def preenchimentos(
    snapshot: dict[str, Any],
    reavaliacao: dict[int, tuple[str, ...]],
    taco_linhas: Optional[dict[int, dict[str, Optional[Decimal]]]] = None,
) -> list[Preenchimento]:
    """Valores do USDA (mapeados pelo motor) para os campos `*` de cada item."""
    taco = _seed_taco()
    conferir_cobertura(reavaliacao)
    taco_linhas = linhas_da_taco() if taco_linhas is None else taco_linhas
    saida: list[Preenchimento] = []
    for numero in sorted(EQUIVALENTES_USDA):
        fdc_id = EQUIVALENTES_USDA[numero]
        detalhe = snapshot["foods"].get(str(fdc_id))
        if not isinstance(detalhe, dict):
            raise DadosInvalidos(f"item {numero}: fdcId {fdc_id} ausente do snapshot (rode --baixar)")
        alimento = fontes.map_usda_food(detalhe, fdc_id)
        if not alimento.ok or alimento.source_id != str(fdc_id):
            raise DadosInvalidos(f"item {numero}: fdcId {fdc_id} recusado pelo motor: {alimento.errors}")
        valores: list[tuple[str, Decimal]] = []
        sem_valor: list[str] = []
        for campo in reavaliacao[numero]:
            bruto = alimento.values.get(campo)
            if bruto is None:
                sem_valor.append(campo)
                continue
            valores.append((campo, taco.converter_valor(repr(float(bruto)), onde=f"fdcId {fdc_id}, {campo}")))
        if not valores:
            raise DadosInvalidos(f"item {numero}: o fdcId {fdc_id} não informa nenhum dos campos {reavaliacao[numero]}")
        origem = catalog.FieldSource(
            fields=tuple(campo for campo, _ in valores),
            source=alimento.source,
            source_id=alimento.source_id,
            source_version=alimento.source_version,
            name=alimento.name,
            retrieved_on=str(snapshot["retrieved_on"]),
            reason=catalog.FieldSourceReason.TACO_UNDER_REVIEW,
            citation=alimento.citation,
            license=alimento.license,
        )
        avisos = validar_linha_final(numero, taco_linhas[numero], valores)
        saida.append(Preenchimento(numero, tuple(valores), origem, tuple(sem_valor), avisos))
    return saida


def gerar_bloco(itens: list[Preenchimento], snapshot: dict[str, Any], sha_snapshot: str, sha_planilha: str) -> str:
    saida = [
        INICIO_BLOCO,
        f"TACO_SOURCE_FILE_SHA256 = {sha_planilha!r}",
        f"USDA_SNAPSHOT_FILE = {SNAPSHOT_REL!r}",
        f"USDA_SNAPSHOT_SHA256 = {sha_snapshot!r}",
        f"RETRIEVED_ON = {str(snapshot['retrieved_on'])!r}",
        "# (source_id da TACO, ((campo, valor por 100 g), ...), field_sources_json)",
        "FILLS = (",
    ]
    for item in itens:
        numero, valores, origem = item.linha()
        saida.append(f"    ({numero!r}, {valores!r},")
        saida.append(f"     {origem!r}),")
    saida.append(")")
    saida.append(FIM_BLOCO)
    return "\n".join(saida)


def bloco_atual(texto_migracao: str) -> str:
    inicio = texto_migracao.index(INICIO_BLOCO)
    fim = texto_migracao.index(FIM_BLOCO) + len(FIM_BLOCO)
    return texto_migracao[inicio:fim]


def bloco_esperado(snapshot_path: Path = SNAPSHOT, planilha: Optional[Path] = None) -> tuple[str, list[Preenchimento]]:
    taco = _seed_taco()
    caminho_planilha = planilha or taco.PLANILHA
    snapshot = ler_snapshot(snapshot_path)
    itens = preenchimentos(snapshot, em_reavaliacao(caminho_planilha), linhas_da_taco(caminho_planilha))
    return gerar_bloco(itens, snapshot, sha256_texto(snapshot_path), taco.sha256_arquivo(caminho_planilha)), itens


def baixar(hoje: date, caminho: Path = SNAPSHOT) -> None:
    """Refaz o snapshot pelo cliente do L3b (chave em `USDA_API_KEY`)."""
    from app import external_food_sources as ext

    ext.ensure_usda_configured()
    foods = {str(fdc_id): ext.usda_food(fdc_id) for fdc_id in sorted(set(EQUIVALENTES_USDA.values()))}
    dados = {"retrieved_on": hoje.isoformat(), "operation": OPERACAO_USDA, "foods": foods}
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"snapshot gravado em {caminho} ({len(foods)} alimentos, consulta {hoje.isoformat()})")


def resumo(itens: list[Preenchimento], reavaliacao: dict[int, tuple[str, ...]]) -> str:
    linhas = [f"{len(reavaliacao)} itens da TACO com `*` nos campos do catálogo"]
    for item in itens:
        valores = ", ".join(f"{campo}={valor}" for campo, valor in item.valores)
        linhas.append(f"  {item.numero} <- USDA {item.origem.source_id} ({item.origem.source_version}): {valores}")
        if item.sem_valor_no_usda:
            linhas.append(f"      sem valor no USDA (fica NULL): {', '.join(item.sem_valor_no_usda)}")
        if item.avisos:
            linhas.append(f"      avisos do catálogo: {', '.join(item.avisos)}")
    for numero in sorted(SEM_EQUIVALENTE):
        linhas.append(f"  {numero} fica NULL ({', '.join(reavaliacao[numero])}): {SEM_EQUIVALENTE[numero]}")
    total = sum(len(item.valores) for item in itens)
    linhas.append(f"preenche {total} campos em {len(itens)} itens")
    return "\n".join(linhas)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--migracao", type=Path, default=MIGRACAO)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--baixar", action="store_true", help="refaz o snapshot do USDA (rede, USDA_API_KEY)")
    modo.add_argument("--gravar", action="store_true", help="reescreve o bloco de dados da migração")
    modo.add_argument("--verificar", action="store_true", help="sai 1 se o bloco da migração divergir")
    parser.add_argument(
        "--migracao-nao-aplicada",
        action="store_true",
        help="confirma que a migração ainda NÃO foi aplicada em ambiente nenhum (exigido por --gravar)",
    )
    args = parser.parse_args(argv)

    if args.baixar:
        baixar(date.today(), args.snapshot)
    bloco, itens = bloco_esperado(args.snapshot)
    texto = args.migracao.read_text(encoding="utf-8")
    atual = bloco_atual(texto)
    if args.gravar:
        if not args.migracao_nao_aplicada:
            print(AVISO_MIGRACAO_APLICADA, file=sys.stderr)
            return 2
        args.migracao.write_text(texto.replace(atual, bloco), encoding="utf-8", newline="\n")
        print(f"bloco gravado em {args.migracao}")
    elif args.verificar:
        if atual != bloco:
            print(
                f"{args.migracao.name}: bloco de dados DIVERGE do snapshot/planilha/motor. Se a migração já foi "
                "aplicada em algum ambiente, NÃO a regrave: crie uma migração nova. " + AVISO_MIGRACAO_APLICADA,
                file=sys.stderr,
            )
            return 1
        print(f"{args.migracao.name}: bloco de dados confere com o snapshot, a planilha e o motor")
    print(resumo(itens, em_reavaliacao()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
