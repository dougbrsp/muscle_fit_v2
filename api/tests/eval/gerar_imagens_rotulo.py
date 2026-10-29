"""Gera as imagens SINTÉTICAS de tabela nutricional do eval da foto do rótulo
(nutrição L6; `tests/eval/casos/nutrition_label.json`).

Por que sintéticas: não há banco de fotos de rótulos brasileiros com licença
clara para redistribuir no repositório. As tabelas seguem o MODELO da RDC nº
429/2020 + IN nº 75/2020 (por 100 g/ml, por porção e %VD) e uma no modelo antigo
da RDC nº 360/2003 (só por porção, kcal = kJ); produtos, marcas e números são
FICTÍCIOS. Fonte: Inter (SIL Open Font License 1.1, já versionada em
`app/assets/fonts/inter/`). As "fotos" simulam celular: papel com textura,
leve rotação, perspectiva, ruído, desfoque e iluminação desigual.

O desenho (tabela, rotação, perspectiva, desfoque) é fixo; o granulado do
Pillow (`Image.effect_noise`) não aceita semente, então os bytes mudam a cada
execução. As imagens VERSIONADAS são a referência do eval (SHA-256 no README da
pasta). Regenerar só se mudar o desenho (e rodar o eval de novo):

    cd api && python tests/eval/gerar_imagens_rotulo.py

Cada arquivo sai com no máximo `TAMANHO_MAXIMO_BYTES` (300 KB, plano §9).
"""

from __future__ import annotations

import io
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

RAIZ_API = Path(__file__).resolve().parents[2]
FONTES = RAIZ_API / "app" / "assets" / "fonts" / "inter"
DESTINO = Path(__file__).resolve().parent / "imagens" / "nutrition_labels"
CASOS = Path(__file__).resolve().parent / "casos" / "nutrition_label.json"
TAMANHO_MAXIMO_BYTES = 300 * 1024

REGULAR = str(FONTES / "Inter-Regular.ttf")
NEGRITO = str(FONTES / "Inter-SemiBold.ttf")


def _fonte(tamanho: int, negrito: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(NEGRITO if negrito else REGULAR, tamanho)


def _numero(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor
    texto = f"{valor:g}" if float(valor) != int(valor) else f"{int(valor)}"
    return texto.replace(".", ",")


def desenhar_tabela(tabela: dict) -> Image.Image:
    """Tabela branca com bordas pretas no modelo da RDC 429 (ou da 360, `modelo_antigo`)."""
    escala = 2
    largura = 760 * escala
    margem = 24 * escala
    linhas = tabela["linhas"]
    antigo = tabela.get("modelo_antigo", False)
    altura_linha = 46 * escala
    topo_extra = 3 if not antigo else 2
    altura = margem * 2 + altura_linha * (len(linhas) + topo_extra + 2) + 60 * escala * len(tabela.get("rodape", []))
    img = Image.new("L", (largura, altura), 255)
    d = ImageDraw.Draw(img)
    preto = 0
    x0, x1 = margem, largura - margem
    y = margem
    d.rectangle([x0, y, x1, altura - margem], outline=preto, width=4 * escala)
    titulo = _fonte(30 * escala, True)
    d.text((x0 + 18 * escala, y + 10 * escala), "INFORMAÇÃO NUTRICIONAL", font=titulo, fill=preto)
    y += altura_linha + 8 * escala
    texto = _fonte(22 * escala)
    negrito = _fonte(22 * escala, True)
    if not antigo:
        d.line([x0, y, x1, y], fill=preto, width=2 * escala)
        d.text((x0 + 18 * escala, y + 8 * escala), f"Porções por embalagem: {tabela['porcoes']}", font=texto, fill=preto)
        y += altura_linha
    d.text((x0 + 18 * escala, y + 8 * escala), f"Porção: {tabela['porcao']}", font=texto, fill=preto)
    y += altura_linha
    d.line([x0, y, x1, y], fill=preto, width=6 * escala)
    colunas = tabela["colunas"]
    inicio, passo = (440, 100) if len(colunas) == 3 else (380, 230)
    xs = [x0 + (inicio + i * passo) * escala for i in range(len(colunas))]
    for xc, cabecalho in zip(xs, colunas):
        d.text((xc, y + 8 * escala), cabecalho, font=negrito, fill=preto)
    y += altura_linha
    d.line([x0, y, x1, y], fill=preto, width=3 * escala)
    for nome, valores, recuo in linhas:
        d.text((x0 + (18 + 28 * recuo) * escala, y + 8 * escala), nome, font=texto, fill=preto)
        for xc, valor in zip(xs, valores):
            d.text((xc, y + 8 * escala), _numero(valor), font=texto, fill=preto)
        y += altura_linha
        d.line([x0 + 12 * escala, y, x1 - 12 * escala, y], fill=preto, width=1 * escala)
    pequena = _fonte(17 * escala)
    for nota in tabela.get("rodape", []):
        d.text((x0 + 18 * escala, y + 14 * escala), nota, font=pequena, fill=preto)
        y += 60 * escala
    if tabela.get("cabecalho_produto"):
        produto = Image.new("L", (largura, 170 * escala), 255)
        dp = ImageDraw.Draw(produto)
        dp.text((margem, 18 * escala), tabela["cabecalho_produto"], font=_fonte(34 * escala, True), fill=preto)
        dp.text((margem, 76 * escala), tabela["marca"], font=_fonte(24 * escala), fill=preto)
        dp.text((margem, 118 * escala), tabela["codigo_barras"], font=_fonte(24 * escala), fill=preto)
        junto = Image.new("L", (largura, produto.height + img.height), 255)
        junto.paste(produto, (0, 0))
        junto.paste(img, (0, produto.height))
        img = junto
    return img.resize((img.width // escala, img.height // escala), Image.LANCZOS)


def fotografar(tabela: Image.Image, *, semente: int, rotacao: float, desfoque: float, ruido: int,
               perspectiva: float, largura_final: int) -> Image.Image:
    """Simula a foto de celular: papel creme, rotação, perspectiva, luz desigual, ruído e desfoque."""
    rng = random.Random(semente)
    borda = 90
    papel = Image.new("RGB", (tabela.width + 2 * borda, tabela.height + 2 * borda), (236, 228, 210))
    textura = Image.effect_noise(papel.size, 18).convert("RGB")
    papel = Image.blend(papel, textura, 0.08)
    papel.paste(Image.merge("RGB", [tabela] * 3), (borda, borda))
    foto = papel.rotate(rotacao, resample=Image.BICUBIC, expand=True, fillcolor=(120, 112, 100))
    if perspectiva:
        w, h = foto.size
        d = perspectiva * w
        coef = _coeficientes([(0, 0), (w, 0), (w, h), (0, h)], [(d, 0), (w - d * 0.4, d * 0.6), (w, h), (0, h - d * 0.3)])
        foto = foto.transform((w, h), Image.PERSPECTIVE, coef, Image.BICUBIC, fillcolor=(120, 112, 100))
    luz = Image.linear_gradient("L").resize(foto.size).rotate(rng.choice([30, 120, 210]))
    foto = Image.composite(foto, ImageEnhance.Brightness(foto).enhance(0.72), luz)
    if ruido:
        granulado = Image.effect_noise(foto.size, ruido).convert("RGB")
        foto = Image.blend(foto, granulado, 0.06)
    if desfoque:
        foto = foto.filter(ImageFilter.GaussianBlur(desfoque))
    proporcao = largura_final / foto.width
    return foto.resize((largura_final, int(foto.height * proporcao)), Image.LANCZOS)


def _coeficientes(origem, destino):
    """Coeficientes de `Image.PERSPECTIVE` (destino -> origem), por mínimos quadrados exatos (8x8)."""
    linhas, b = [], []
    for (x, y), (u, v) in zip(destino, origem):
        linhas.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        linhas.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.extend([u, v])
    return _resolver(linhas, b)


def _resolver(a, b):
    n = len(b)
    m = [linha[:] + [b[i]] for i, linha in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [vr - f * vc for vr, vc in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def salvar_jpeg(img: Image.Image, destino: Path, qualidade: int = 82) -> int:
    for q in range(qualidade, 40, -6):
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, "JPEG", quality=q, optimize=True)
        if buffer.tell() <= TAMANHO_MAXIMO_BYTES:
            destino.write_bytes(buffer.getvalue())
            return buffer.tell()
    raise RuntimeError(f"{destino.name}: não coube em {TAMANHO_MAXIMO_BYTES} bytes")


def main() -> int:
    casos = json.loads(CASOS.read_text(encoding="utf-8"))
    DESTINO.mkdir(parents=True, exist_ok=True)
    for caso in casos["cases"]:
        desenho = caso.get("desenho")
        if desenho is None:  # caso que reusa a imagem de outro (ex.: a foto grande, ampliada no eval)
            continue
        tabela = desenhar_tabela(desenho["tabela"])
        foto = fotografar(tabela, **desenho["foto"])
        tamanho = salvar_jpeg(foto, DESTINO / caso["image"])
        print(f"{caso['image']}: {foto.width}x{foto.height}, {tamanho} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
