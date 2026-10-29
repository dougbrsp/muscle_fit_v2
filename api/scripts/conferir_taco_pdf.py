#!/usr/bin/env python3
"""Confere a planilha da TACO (seed da 081) contra o PDF oficial da 4ª ed.

Fonte oficial: NEPA-UNICAMP, "Tabela Brasileira de Composição de Alimentos –
TACO", 4ª ed. rev. e ampl., 2011 (URL e SHA-256 do PDF conferido em
`docs/nutricao/fontes/README.md`). O PDF não é versionado: baixe-o e extraia o
texto com o `pdftotext` do poppler no modo `-raw` (ordem do conteúdo; o modo
`-layout` desloca colunas inteiras em algumas páginas e gera falsas divergências):

    pdftotext -raw -enc UTF-8 taco_4_edicao_ampliada_e_revisada.pdf taco.txt
    python scripts/conferir_taco_pdf.py taco.txt          # a partir de api/

O que confere, na Tabela 1 (centesimal, minerais...):
- parte centesimal: linhas `número nome + 11 valores` (umidade, kcal, kJ,
  proteína, lipídeos, colesterol, carboidrato, fibra, cinzas, cálcio,
  magnésio) cujo número e nome batem com a planilha; energia (kcal),
  proteína, lipídeos, carboidrato e fibra;
- parte de minerais: linhas `número + valores`; o 4º valor é o sódio (mg),
  conferido só quando os 3 anteriores (Mn, P, Fe) batem com a planilha;
- cada valor da PLANILHA (precisão cheia) arredondado à precisão impressa
  (ROUND_HALF_UP) contra o valor do PDF; `Tr` exige valor que arredonda para 0
  (ou traço na planilha); `NA`/`*` exigem NULL;
- os grupos: o cabeçalho de cada um e o primeiro número que vem depois.
Linha com célula em branco no PDF ("análise não solicitada") tem menos de 11
valores e fica de fora da parte centesimal; o resumo diz quantas foram
conferidas.

Só biblioteca padrão; lê a planilha pelo gerador (`gerar_seed_taco`).
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gerar_seed_taco as gerador  # noqa: E402

_VALOR = r"(?:\d+,\d+|\d+|NA|Tr|\*)"
_CENTESIMAL = re.compile(rf"^(\d{{1,3}}) (.+?) ((?:{_VALOR} ){{10}}{_VALOR})$")
_MINERAIS = re.compile(rf"^(\d{{1,3}}) ((?:{_VALOR}[ab]? ?)+)$")
# Campo -> (índice no grupo de 11 valores da parte centesimal, coluna da planilha).
CENTESIMAL: dict[str, tuple[int, str]] = {
    "energy_kcal": (1, "E"),
    "protein_g": (3, "G"),
    "total_fat_g": (4, "H"),
    "carbohydrates_g": (6, "J"),
    "fiber_g": (7, "K"),
}
SODIO_INDICE, SODIO_COLUNA = 3, "R"  # Mn, P, Fe, Na
ANCORA_DO_SODIO = ("O", "P", "Q")  # Mn, P, Fe na planilha
GRUPOS_DA_PUBLICACAO = (
    "Cereais e derivados",
    "Verduras, hortaliças e derivados",
    "Frutas e derivados",
    "Gorduras e óleos",
    "Pescados e frutos do mar",
    "Carnes e derivados",
    "Leite e derivados",
    "Bebidas (alcoólicas e não alcoólicas)",
    "Ovos e derivados",
    "Produtos açucarados",
    "Miscelâneas",
    "Outros alimentos industrializados",
    "Alimentos preparados",
    "Leguminosas e derivados",
    "Nozes e sementes",
)


def _chave(texto: str) -> str:
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def _planilha_bruta(celula: Optional[str]) -> Optional[Decimal]:
    """Precisão cheia da planilha (traço = 0; negativo mínimo = 0, como no gerador)."""
    texto = (celula or "").strip()
    if texto in gerador.MARCADORES_NULOS:
        return None
    if texto == gerador.MARCADOR_TRACO:
        return Decimal(0)
    numero = gerador.valor_decimal(texto)
    return Decimal(0) if numero < 0 else numero


def comparar(impresso: str, planilha: Optional[Decimal]) -> bool:
    impresso = impresso.rstrip("ab")
    if impresso in ("NA", "*"):
        return planilha is None
    if planilha is None:
        return False
    if impresso == "Tr":
        return planilha.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) == 0
    casas = len(impresso.split(",")[1]) if "," in impresso else 0
    arredondado = planilha.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)
    return arredondado == Decimal(impresso.replace(",", "."))


def _tabela1(texto_pdf: str) -> list[str]:
    linhas = texto_pdf.splitlines()
    inicio = next(i for i, l in enumerate(linhas) if l.startswith("Tabela 1."))
    fim = next(i for i, l in enumerate(linhas) if l.startswith("Tabela 2."))
    return [" ".join(l.split()) for l in linhas[inicio:fim]]


def conferir(texto_pdf: str) -> dict:
    celulas = {int(Decimal((c.get("A") or "0").strip())): c for c in gerador._ler_celulas(gerador.PLANILHA)[1:]}
    seed = {linha.numero: linha for linha in gerador.ler_planilha()}
    tabela1 = _tabela1(texto_pdf)

    centesimal: dict[int, dict[str, bool]] = {}
    sodio: dict[int, bool] = {}
    for linha in tabela1:
        achado = _CENTESIMAL.match(linha)
        if achado and int(achado.group(1)) in celulas and int(achado.group(1)) not in centesimal:
            numero = int(achado.group(1))
            nome_pdf = re.sub(r" \d$", "", achado.group(2))  # chamada de nota de rodapé
            if _chave(nome_pdf) == _chave(seed[numero].nome):
                valores = achado.group(3).split()
                centesimal[numero] = {
                    campo: comparar(valores[indice], _planilha_bruta(celulas[numero].get(coluna)))
                    for campo, (indice, coluna) in CENTESIMAL.items()
                }
            continue
        achado = _MINERAIS.match(linha)
        if achado and int(achado.group(1)) in celulas and int(achado.group(1)) not in sodio:
            numero = int(achado.group(1))
            valores = achado.group(2).split()
            # Célula em branco some no `-raw` e desalinha as colunas: o sódio só é
            # conferido quando Mn, P e Fe (as 3 antes dele) batem com a planilha.
            ancora = len(valores) > SODIO_INDICE and all(
                comparar(valores[i], _planilha_bruta(celulas[numero].get(coluna)))
                for i, coluna in enumerate(ANCORA_DO_SODIO)
            )
            if ancora:
                sodio[numero] = comparar(valores[SODIO_INDICE], _planilha_bruta(celulas[numero].get(SODIO_COLUNA)))

    grupos: list[tuple[str, Optional[int]]] = []
    for indice, linha in enumerate(tabela1):
        if linha in GRUPOS_DA_PUBLICACAO and linha not in [g for g, _ in grupos]:
            primeiro = next(
                (int(m.group(1)) for l in tabela1[indice + 1 :] for m in [re.match(r"^(\d{1,3}) [^\d\s]", l)] if m),
                None,
            )
            grupos.append((linha, primeiro))
    return {"centesimal": centesimal, "sodio": sodio, "grupos": grupos, "seed": seed}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("texto_pdf", type=Path, help="saída do `pdftotext -raw` do PDF oficial")
    args = parser.parse_args(argv)
    resultado = conferir(args.texto_pdf.read_text(encoding="utf-8"))
    centesimal, sodio, seed = resultado["centesimal"], resultado["sodio"], resultado["seed"]
    divergentes = {n: r for n, r in centesimal.items() if not all(r.values())}
    campos = sum(len(r) for r in centesimal.values())
    campos_ok = sum(sum(r.values()) for r in centesimal.values())
    print(f"Centesimal: {len(centesimal)} de {len(seed)} alimentos conferidos (linha completa no PDF)")
    print(f"  {len(centesimal) - len(divergentes)} batem nos 5 campos; {len(divergentes)} com divergência")
    print(f"  {campos_ok} de {campos} valores (energia, proteína, lipídeos, carboidrato, fibra) iguais")
    for numero, r in sorted(divergentes.items()):
        print(f"  DIVERGE {numero} {seed[numero].nome}: {[c for c, ok in r.items() if not ok]}")
    sodio_div = sorted(n for n, ok in sodio.items() if not ok)
    print(f"Sódio: {len(sodio)} conferidos, {len(sodio) - len(sodio_div)} iguais; divergentes: {sodio_div}")
    grupos_seed: dict[str, int] = {}
    for linha in seed.values():
        grupos_seed.setdefault(linha.grupo, linha.numero)
    print(f"Grupos: {len(resultado['grupos'])} cabeçalhos na Tabela 1")
    for grupo, primeiro in resultado["grupos"]:
        marca = "ok" if grupos_seed.get(grupo) == primeiro else f"DIVERGE (seed {grupos_seed.get(grupo)})"
        print(f"  {grupo}: começa no {primeiro} {marca}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
