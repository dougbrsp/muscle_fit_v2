#!/usr/bin/env python3
"""Gera os dados da TACO 4ª ed. embutidos na migração 081 (nutrição, Lote L3a).

Fonte: `docs/nutricao/fontes/taco_4a_edicao.xlsx` (proveniência, SHA-256 e
citação em `docs/nutricao/fontes/README.md`). Só biblioteca padrão para ler a
planilha (`zipfile` + `xml.etree`); o nome normalizado vem do motor
(`app.domain.nutrition.food_text.normalize_name`) e a citação/licença de
`app.domain.nutrition.catalog`, ambos Python puro.

Os dados ficam DENTRO da migração (`081_food_catalog_seed_taco.py`, entre os
marcadores `# >>> DADOS GERADOS` e `# <<< DADOS GERADOS`) para o checksum do
runner cobrir os dados (plano §6.1, R6). Migração aplicada nunca é editada: se a
planilha ou o motor mudarem depois da 081 em produção, a correção é uma
migração NOVA (o teste `test_migration_081_plano.py` acusa a divergência).

Regras de conversão (documentadas também no README de fontes):
- `NA` (não aplicável), `*` (análise em reavaliação na publicação) e célula
  vazia (análise não solicitada) viram NULL;
- `Tr` (traço: valor arredondado que cai entre 0 e a última casa, ou abaixo do
  limite de quantificação) vira 0;
- número: `Decimal` da representação mais curta do double da célula (o xlsx
  grava `2.5499999999999998` para 2,55; `repr` devolve `2.55`, o valor que o
  Excel mostra), arredondado para 2 casas (ROUND_HALF_UP);
  negativo acima de -0,05 vira 0 (carboidrato calculado por diferença em 4
  itens; a publicação mostra 0,0); negativo maior é erro;
- energia em kcal da coluna `Energia(kcal)`; carboidrato é o total da TACO
  (por diferença, inclui a fibra); sódio em mg;
- açúcares totais/adicionados e gorduras saturada/trans: a planilha não tem as
  colunas -> NULL;
- nome: espaços colapsados e espaço depois de vírgula; as chamadas de nota de
  rodapé "1" e "2" (teor alcoólico na publicação) saem de "Cana, aguardente" e
  "Cerveja, pilsen";
- grupo: o da planilha, exceto os itens 587–597, que a planilha junta a
  "Leguminosas e derivados" e a publicação (Tabela 1, p. 63–64) classifica em
  "Nozes e sementes" (15 grupos, como na TACO).

Uso (a partir de `api/`):
    python scripts/gerar_seed_taco.py              # resumo, sem gravar
    python scripts/gerar_seed_taco.py --verificar  # sai 1 se o bloco divergir
    # Só enquanto a 081 não foi aplicada em ambiente nenhum (o checksum dela fica
    # congelado em `test_migration_081_plano.py` desde o PR do L3a):
    python scripts/gerar_seed_taco.py --gravar --migracao-nao-aplicada
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

API_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = API_DIR.parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from app.domain.nutrition import catalog  # noqa: E402
from app.domain.nutrition.food_text import normalize_name  # noqa: E402

PLANILHA = REPO_DIR / "docs" / "nutricao" / "fontes" / "taco_4a_edicao.xlsx"
MIGRACAO = API_DIR / "db" / "migrations" / "081_food_catalog_seed_taco.py"
ABA = "Taco"
TOTAL_ESPERADO = 597

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Cabeçalho exato da aba (coluna -> título). Layout diferente falha alto.
CABECALHO: dict[str, str] = {
    "A": "Número",
    "B": "Grupo",
    "C": "Descrição do Alimento",
    "D": "Umidade(%)",
    "E": "Energia(kcal)",
    "F": "Energia(kJ)",
    "G": "Proteína(g)",
    "H": "Lipídeos(g)",
    "I": "Colesterol(mg)",
    "J": "Carboidrato(g)",
    "K": "Fibra Alimentar(g)",
    "L": "Cinzas(g)",
    "M": "Cálcio(mg)",
    "N": "Magnésio(mg)",
    "O": "Manganês(mg)",
    "P": "Fósforo(mg)",
    "Q": "Ferro(mg)",
    "R": "Sódio(mg)",
    "S": "Potássio(mg)",
    "T": "Cobre(mg)",
    "U": "Zinco(mg)",
    "V": "Retinol(mcg)",
    "W": "RE(mcg)",
    "X": "RAE (mcg)",
    "Y": "Tiamina(mg)",
    "Z": "Riboflavina(mg)",
    "AA": "Piridoxina(mg)",
    "AB": "Niacina(mg)",
    "AC": "VitaminaC(mg)",
}

# Campo do catálogo (código da RDC 429, `catalog.PER_100_FIELDS`) -> coluna da planilha.
COLUNA_DO_CAMPO: dict[str, str] = {
    "energy_kcal": "E",
    "carbohydrates_g": "J",
    "protein_g": "G",
    "total_fat_g": "H",
    "fiber_g": "K",
    "sodium_mg": "R",
}
CAMPOS_SEM_COLUNA: tuple[str, ...] = tuple(c for c in catalog.PER_100_FIELDS if c not in COLUNA_DO_CAMPO)

MARCADOR_NAO_APLICAVEL = "NA"
# Em reavaliação: a 081 grava NULL; a 084 (M85) preenche com o equivalente do USDA
# (`scripts/gerar_taco_usda_reavaliacao.py`, que lê os itens por `campos_marcados`).
MARCADOR_REAVALIACAO = "*"
MARCADORES_NULOS: dict[str, str] = {
    "": "vazio: análise não solicitada",
    MARCADOR_NAO_APLICAVEL: "não aplicável",
    MARCADOR_REAVALIACAO: "análise em reavaliação na publicação",
}
MARCADOR_TRACO = "Tr"
CASAS = Decimal("0.01")
# Negativo que a publicação (uma casa decimal) mostra como 0,0.
NEGATIVO_ARREDONDA_PARA_ZERO = Decimal("0.05")

# Chamadas de nota de rodapé no nome (teor alcoólico, Tabela 1 da publicação).
NOMES_CORRIGIDOS: dict[int, tuple[str, str]] = {
    472: ("Cana, aguardente 1", "Cana, aguardente"),
    474: ("Cerveja, pilsen 2", "Cerveja, pilsen"),
}
# Itens que a planilha põe em "Leguminosas e derivados" e a publicação em "Nozes e sementes".
GRUPO_NOZES = "Nozes e sementes"
ITENS_NOZES_E_SEMENTES = range(587, 598)
GRUPO_DA_PLANILHA_NOZES = "Leguminosas e derivados"

AVISO_MIGRACAO_APLICADA = (
    "--gravar reescreve a migração no lugar: só antes de ela ser aplicada em qualquer ambiente (o runner "
    "registra o checksum e recusa arquivo alterado). Confirme com --migracao-nao-aplicada; depois da promoção "
    "use uma migração nova (ver test_migration_081_plano.py, CHECKSUM_CONGELADO)."
)

INICIO_BLOCO = "# >>> DADOS GERADOS por api/scripts/gerar_seed_taco.py (nao editar a mao) >>>"
FIM_BLOCO = "# <<< DADOS GERADOS <<<"

SEED_COLUMNS: tuple[str, ...] = ("source_id", "name", "name_normalized", "category", *COLUNA_DO_CAMPO)


@dataclass(frozen=True)
class LinhaTaco:
    numero: int
    grupo: str
    nome: str
    nome_normalizado: str
    valores: dict[str, Optional[Decimal]]

    def seed_row(self) -> tuple[Optional[str], ...]:
        return (
            str(self.numero),
            self.nome,
            self.nome_normalizado,
            self.grupo,
            *(None if self.valores[campo] is None else str(self.valores[campo]) for campo in COLUNA_DO_CAMPO),
        )


class PlanilhaInvalida(Exception):
    pass


def sha256_arquivo(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def _coluna(referencia: str) -> str:
    return "".join(ch for ch in referencia if ch.isalpha())


def _ler_celulas(caminho: Path) -> list[dict[str, Optional[str]]]:
    with zipfile.ZipFile(caminho) as pacote:
        compartilhadas: list[str] = []
        if "xl/sharedStrings.xml" in pacote.namelist():
            raiz = ET.fromstring(pacote.read("xl/sharedStrings.xml"))
            compartilhadas = ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in raiz.findall(f"{_NS}si")]
        livro = ET.fromstring(pacote.read("xl/workbook.xml"))
        aba = next((s for s in livro.iter(f"{_NS}sheet") if s.get("name") == ABA), None)
        if aba is None:
            raise PlanilhaInvalida(f"aba {ABA!r} não encontrada")
        rel_id = aba.get(f"{_NS_REL}id")
        rels = ET.fromstring(pacote.read("xl/_rels/workbook.xml.rels"))
        alvo = next((r.get("Target") for r in rels if r.get("Id") == rel_id), None)
        if not alvo:
            raise PlanilhaInvalida(f"relação {rel_id!r} da aba {ABA!r} não encontrada")
        caminho_aba = alvo.lstrip("/") if alvo.startswith("/") else f"xl/{alvo}"
        folha = ET.fromstring(pacote.read(caminho_aba))
    linhas: list[dict[str, Optional[str]]] = []
    for linha in folha.iter(f"{_NS}row"):
        celulas: dict[str, Optional[str]] = {}
        for celula in linha.findall(f"{_NS}c"):
            tipo = celula.get("t")
            valor = celula.find(f"{_NS}v")
            if tipo == "s":
                texto: Optional[str] = compartilhadas[int(valor.text)] if valor is not None else None
            elif tipo == "inlineStr":
                texto = "".join(t.text or "" for t in celula.iter(f"{_NS}t"))
            else:
                texto = valor.text if valor is not None else None
            celulas[_coluna(celula.get("r") or "")] = texto
        linhas.append(celulas)
    return linhas


def valor_decimal(texto: str) -> Decimal:
    """Número da célula como o Excel o mostra: o xlsx grava o double com 17
    algarismos (`2.5499999999999998`); a representação mais curta que volta ao
    mesmo double (`repr`, `2.55`) é o valor digitado na tabela."""
    return Decimal(repr(float(texto)))


def converter_valor(texto: Optional[str], *, onde: str) -> Optional[Decimal]:
    """Célula -> Decimal com 2 casas, 0 (traço) ou None (NA, `*`, vazio)."""
    bruto = (texto or "").strip()
    if bruto in MARCADORES_NULOS:
        return None
    if bruto == MARCADOR_TRACO:
        return Decimal("0.00")
    try:
        numero = valor_decimal(bruto)
    except (InvalidOperation, ValueError) as exc:
        raise PlanilhaInvalida(f"{onde}: valor {texto!r} não é número nem marcador conhecido") from exc
    if not numero.is_finite():
        raise PlanilhaInvalida(f"{onde}: valor {texto!r} não finito")
    if numero < 0:
        # Carboidrato por diferença levemente negativo (4 itens, de -0,045 a
        # -0,007 g): a publicação arredonda para uma casa e mostra 0,0.
        if -numero >= NEGATIVO_ARREDONDA_PARA_ZERO:
            raise PlanilhaInvalida(f"{onde}: valor {texto!r} negativo")
        return Decimal("0.00")
    return numero.quantize(CASAS, rounding=ROUND_HALF_UP)


def limpar_nome(numero: int, bruto: str) -> str:
    nome = " ".join(bruto.split())
    nome = re.sub(r",(?=\S)", ", ", nome)
    if numero in NOMES_CORRIGIDOS:
        esperado, corrigido = NOMES_CORRIGIDOS[numero]
        if nome != esperado:
            raise PlanilhaInvalida(f"item {numero}: nome {nome!r}, esperado {esperado!r} para a correção da nota de rodapé")
        nome = corrigido
    return nome


def grupo_do_item(numero: int, grupo_planilha: str) -> str:
    grupo = " ".join(grupo_planilha.split())
    if numero in ITENS_NOZES_E_SEMENTES:
        if grupo != GRUPO_DA_PLANILHA_NOZES:
            raise PlanilhaInvalida(f"item {numero}: grupo {grupo!r}, esperado {GRUPO_DA_PLANILHA_NOZES!r} na planilha")
        return GRUPO_NOZES
    return grupo


def ler_planilha(caminho: Path = PLANILHA) -> list[LinhaTaco]:
    celulas = _ler_celulas(caminho)
    if not celulas:
        raise PlanilhaInvalida("planilha vazia")
    cabecalho = {col: (texto or "").strip() for col, texto in celulas[0].items()}
    if cabecalho != CABECALHO:
        raise PlanilhaInvalida(f"cabeçalho divergente: {cabecalho}")
    linhas: list[LinhaTaco] = []
    for celula in celulas[1:]:
        if not any((v or "").strip() for v in celula.values()):
            continue
        numero = int(Decimal((celula.get("A") or "").strip()))
        nome = limpar_nome(numero, celula.get("C") or "")
        valores = {
            campo: converter_valor(celula.get(coluna), onde=f"item {numero}, {CABECALHO[coluna]}")
            for campo, coluna in COLUNA_DO_CAMPO.items()
        }
        linhas.append(
            LinhaTaco(
                numero=numero,
                grupo=grupo_do_item(numero, celula.get("B") or ""),
                nome=nome,
                nome_normalizado=normalize_name(nome),
                valores=valores,
            )
        )
    numeros = [linha.numero for linha in linhas]
    if numeros != list(range(1, TOTAL_ESPERADO + 1)):
        raise PlanilhaInvalida(f"esperados os itens 1..{TOTAL_ESPERADO} em ordem; vieram {len(numeros)}")
    vazios = [linha.numero for linha in linhas if not linha.nome_normalizado]
    if vazios:
        raise PlanilhaInvalida(f"itens sem nome normalizado: {vazios}")
    return linhas


def campos_marcados(marcador: str, caminho: Path = PLANILHA) -> dict[int, tuple[str, ...]]:
    """Item -> campos do catálogo (`COLUNA_DO_CAMPO`, na ordem) cuja célula é
    exatamente `marcador` (ex.: `MARCADOR_REAVALIACAO`). Valida a planilha
    inteira antes (`ler_planilha`: cabeçalho, itens 1..597, valores)."""
    ler_planilha(caminho)
    marcados: dict[int, tuple[str, ...]] = {}
    for celula in _ler_celulas(caminho)[1:]:
        if not any((v or "").strip() for v in celula.values()):
            continue
        campos = tuple(
            campo for campo, coluna in COLUNA_DO_CAMPO.items() if (celula.get(coluna) or "").strip() == marcador
        )
        if campos:
            marcados[int(Decimal((celula.get("A") or "").strip()))] = campos
    return marcados


def _texto(valor: Optional[str]) -> str:
    return "None" if valor is None else repr(valor)


def gerar_bloco(linhas: list[LinhaTaco], sha_planilha: str) -> str:
    saida = [
        INICIO_BLOCO,
        f"SOURCE_FILE = {'docs/nutricao/fontes/taco_4a_edicao.xlsx'!r}",
        f"SOURCE_FILE_SHA256 = {sha_planilha!r}",
        f"SOURCE_VERSION = {catalog.TACO_SOURCE_VERSION!r}",
        f"CITATION = {catalog.TACO_CITATION!r}",
        f"LICENSE = {catalog.TACO_LICENSE!r}",
        f"NULL_COLUMNS = {CAMPOS_SEM_COLUNA!r}",
        f"SEED_COLUMNS = {SEED_COLUMNS!r}",
        "SEED_ROWS = (",
    ]
    for linha in linhas:
        saida.append("    (" + ", ".join(_texto(v) for v in linha.seed_row()) + "),")
    saida.append(")")
    saida.append(FIM_BLOCO)
    return "\n".join(saida)


def bloco_atual(texto_migracao: str) -> str:
    inicio = texto_migracao.index(INICIO_BLOCO)
    fim = texto_migracao.index(FIM_BLOCO) + len(FIM_BLOCO)
    return texto_migracao[inicio:fim]


def resumo(linhas: list[LinhaTaco]) -> str:
    grupos: dict[str, int] = {}
    for linha in linhas:
        grupos[linha.grupo] = grupos.get(linha.grupo, 0) + 1
    nulos = {campo: sum(1 for l in linhas if l.valores[campo] is None) for campo in COLUNA_DO_CAMPO}
    partes = [f"{len(linhas)} alimentos em {len(grupos)} grupos"]
    partes += [f"  {grupo}: {n}" for grupo, n in grupos.items()]
    partes.append("NULL por campo: " + ", ".join(f"{c}={n}" for c, n in nulos.items()))
    return "\n".join(partes)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--planilha", type=Path, default=PLANILHA)
    parser.add_argument("--migracao", type=Path, default=MIGRACAO)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--gravar", action="store_true", help="reescreve o bloco de dados da migração")
    modo.add_argument("--verificar", action="store_true", help="sai 1 se o bloco da migração divergir")
    parser.add_argument(
        "--migracao-nao-aplicada",
        action="store_true",
        help="confirma que a migração ainda NÃO foi aplicada em ambiente nenhum (exigido por --gravar)",
    )
    args = parser.parse_args(argv)

    linhas = ler_planilha(args.planilha)
    bloco = gerar_bloco(linhas, sha256_arquivo(args.planilha))
    texto = args.migracao.read_text(encoding="utf-8")
    atual = bloco_atual(texto)
    if args.gravar:
        # Migração aplicada tem o checksum registrado: reescrevê-la trava TODAS as
        # migrações do deploy (`migrate._check_consistency`). Depois da promoção,
        # dado novo ou motor diferente vira migração NOVA.
        if not args.migracao_nao_aplicada:
            print(AVISO_MIGRACAO_APLICADA, file=sys.stderr)
            return 2
        args.migracao.write_text(texto.replace(atual, bloco), encoding="utf-8", newline="\n")
        print(f"bloco gravado em {args.migracao}")
    elif args.verificar:
        if atual != bloco:
            print(
                f"{args.migracao.name}: bloco de dados DIVERGE da planilha/motor. Se a migração já foi aplicada "
                "em algum ambiente, NÃO a regrave: crie uma migração nova. " + AVISO_MIGRACAO_APLICADA,
                file=sys.stderr,
            )
            return 1
        print(f"{args.migracao.name}: bloco de dados confere com a planilha e o motor")
    print(resumo(linhas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
