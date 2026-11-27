"""Métricas do benchmark da foto do prato (issue #326). Funções puras, sem rede nem banco.

Identificação por item: o nome lido (pt-BR) passa pelo glossário `GLOSSARIO_PT_EN` para o
vocabulário do Nutrition5k (inglês) e é comparado a cada ingrediente real por
`similaridade` = contenção de palavras: fração das palavras do nome mais curto que têm
par no outro, em nomes normalizados (minúsculas, sem acento, só letras e dígitos); duas
palavras são iguais com razão do `difflib.SequenceMatcher` >= `LIMIAR_PALAVRA` (aceita
plural e grafia: "tomato" x "tomatoes"; recusa "white rice" x "brown rice" = 0,5). Pareamento guloso pela maior similaridade, cada item no máximo uma vez; par só
com similaridade >= `LIMIAR_SIMILARIDADE`. Previsto sem par = invenção; ingrediente real
sem par e com massa >= `massa_min_visivel` = omissão (temperos de poucos gramas, como sal
e óleo, não são visíveis na foto e não contam como omissão, mas contam se acertados).

Limites conhecidos: a contenção aceita "sweet potato" x "potato"; o glossário é curto
(os ingredientes mais frequentes do dataset) e nome fora dele depende da grafia.

Auto-checagem: `python tools/benchmark_meal_photo/metricas.py`.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping, Optional, Sequence

LIMIAR_SIMILARIDADE = 0.6
LIMIAR_PALAVRA = 0.85
MASSA_MIN_VISIVEL_G = 5.0
NUTRIENTES = ("kcal", "proteina_g", "carbo_g", "gordura_g")

# Nome lido (normalizado, sem acento) -> termo do Nutrition5k. Frases antes das palavras.
GLOSSARIO_PT_EN: dict[str, str] = {
    "azeite de oliva": "olive oil", "azeite": "olive oil", "oleo": "oil", "sal": "salt", "alho": "garlic",
    "pimenta do reino": "pepper", "pimentao": "bell peppers", "pimenta": "pepper", "vinagre": "vinegar",
    "cebolinha": "green onions", "cebola": "onions", "suco de limao": "lemon juice", "limao": "lemon",
    "brocolis": "broccoli", "rucula": "arugula", "salsinha": "parsley", "salsa": "parsley", "cenoura": "carrot",
    "tomate cereja": "cherry tomatoes", "tomate": "tomatoes", "espinafre": "spinach", "chalota": "shallots",
    "pepino": "cucumbers", "peito de frango": "chicken breast", "coxa de frango": "chicken thighs",
    "frango": "chicken", "mostarda": "mustard", "folhas verdes": "mixed greens", "mix de folhas": "mixed greens",
    "salada verde": "mixed greens", "couve flor": "cauliflower", "arroz integral": "brown rice",
    "arroz branco": "white rice", "arroz": "white rice", "trigo em grao": "wheat berry", "tomilho": "thyme",
    "azeitona": "olives", "ovos mexidos": "scrambled eggs", "ovo mexido": "scrambled eggs",
    "clara de ovo": "egg whites", "ovo": "eggs", "pizza de queijo": "cheese pizza",
    "pizza de pepperoni": "pepperoni pizza", "frutas vermelhas": "berries", "bacon": "bacon", "abacaxi": "pineapple",
    "salmao": "salmon", "peixe": "fish", "maca": "apple", "acelga": "chard", "batata doce": "sweet potato",
    "cogumelo": "mushroom", "champignon": "mushroom", "couve de bruxelas": "brussels sprouts", "couve": "kale",
    "carne bovina": "beef", "carne": "beef", "bife": "steak", "melao": "cantaloupe", "abobora": "squash",
    "salada caesar": "caesar salad", "painco": "millet", "coentro": "cilantro", "amendoa": "almonds",
    "mirtilo": "blueberries", "carne de porco": "pork", "porco": "pork", "batata assada": "roasted potatoes",
    "batata": "potatoes", "linguica": "sausage", "salsicha": "sausage", "vagem": "green beans", "alface": "lettuce",
    "manjericao": "basil", "alecrim": "rosemary", "quinoa": "quinoa", "tofu": "tofu", "uva": "grapes",
    "abobrinha": "zucchini", "milho": "corn", "molho de soja": "soy sauce", "shoyu": "soy sauce",
    "rabanete": "radishes", "queijo parmesao": "parmesan cheese", "parmesao": "parmesan cheese",
    "melancia": "watermelon", "inhame": "yam", "laranja": "orange", "aspargo": "asparagus",
    "berinjela": "eggplant", "tortilha": "tortilla", "nozes": "walnuts", "queijo": "cheese", "abacate": "avocado",
    "creme de leite": "cream", "gengibre": "ginger", "repolho": "cabbage", "presunto": "ham", "pera": "pears",
    "framboesa": "raspberries", "aveia": "oatmeal", "morango": "strawberries", "castanha": "nuts",
    "amora": "blackberries", "maionese": "mayonnaise", "kiwi": "kiwi", "creme azedo": "sour cream",
    "acucar": "sugar", "vinho": "wine", "batata chips": "potato chips", "feijao": "beans", "macarrao": "pasta",
    "pao": "bread",
}
_GLOSSARIO_ORDEM = sorted(GLOSSARIO_PT_EN, key=len, reverse=True)


def normalizar_nome(nome: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(nome or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texto).split())


def traduzir(nome: Any) -> str:
    """Nome lido -> vocabulário do dataset pelo glossário (frase mais longa primeiro)."""
    texto = f" {normalizar_nome(nome)} "
    for termo in _GLOSSARIO_ORDEM:
        texto = texto.replace(f" {termo} ", f" {GLOSSARIO_PT_EN[termo]} ")
        # Plural simples: "tomates", "ovos".
        texto = texto.replace(f" {termo}s ", f" {GLOSSARIO_PT_EN[termo]} ")
    return " ".join(texto.split())


def similaridade(a: Any, b: Any) -> float:
    na, nb = normalizar_nome(a), normalizar_nome(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    menor, maior = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    iguais = sum(1 for t in menor if any(_palavras_iguais(t, u) for u in maior))
    return iguais / len(menor)


def _palavras_iguais(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= LIMIAR_PALAVRA


def parear_itens(
    previstos: Sequence[str], reais: Sequence[str], limiar: float = LIMIAR_SIMILARIDADE
) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """(pares (i_previsto, j_real, similaridade), índices previstos sem par, índices reais sem par)."""
    candidatos = sorted(
        ((similaridade(traduzir(p), r), i, j) for i, p in enumerate(previstos) for j, r in enumerate(reais)),
        reverse=True,
    )
    usados_p: set[int] = set()
    usados_r: set[int] = set()
    pares = []
    for nota, i, j in candidatos:
        if nota < limiar:
            break
        if i in usados_p or j in usados_r:
            continue
        usados_p.add(i)
        usados_r.add(j)
        pares.append((i, j, nota))
    return (
        sorted(pares),
        [i for i in range(len(previstos)) if i not in usados_p],
        [j for j in range(len(reais)) if j not in usados_r],
    )


def _relativo(erro: float, referencia: float) -> Optional[float]:
    return erro / referencia if referencia > 0 else None


def avaliar_leitura(
    previstos: Sequence[Mapping[str, Any]],
    reais: Sequence[Mapping[str, Any]],
    totais_previstos: Mapping[str, Optional[float]],
    totais_reais: Mapping[str, float],
    massa_real_total: float,
    massa_min_visivel: float = MASSA_MIN_VISIVEL_G,
) -> dict[str, Any]:
    """Uma leitura contra o prato real. Item previsto: `nome`, `gramas`; real: `nome`, `gramas`.
    Totais: chaves de `NUTRIENTES` (previsto `None` = desconhecido, sem erro calculado)."""
    pares, sem_par_p, sem_par_r = parear_itens([p["nome"] for p in previstos], [r["nome"] for r in reais])
    omissoes = [j for j in sem_par_r if float(reais[j]["gramas"]) >= massa_min_visivel]
    visiveis = sum(1 for r in reais if float(r["gramas"]) >= massa_min_visivel)
    acertos_visiveis = sum(1 for _, j, _ in pares if float(reais[j]["gramas"]) >= massa_min_visivel)
    erros_item = [abs(float(previstos[i]["gramas"] or 0) - float(reais[j]["gramas"])) for i, j, _ in pares]
    massa_prevista = sum(float(p["gramas"] or 0) for p in previstos)
    erro_massa = abs(massa_prevista - massa_real_total)
    nutrientes = {}
    for chave in NUTRIENTES:
        previsto = totais_previstos.get(chave)
        erro = None if previsto is None else abs(float(previsto) - float(totais_reais[chave]))
        nutrientes[chave] = {
            "previsto": previsto,
            "real": totais_reais[chave],
            "erro_abs": erro,
            "erro_rel": None if erro is None else _relativo(erro, float(totais_reais[chave])),
        }
    return {
        "acertos": len(pares),
        "omissoes": len(omissoes),
        "invencoes": len(sem_par_p),
        "reais_visiveis": visiveis,
        "recall_visivel": _relativo(acertos_visiveis, visiveis),
        "precisao": _relativo(len(pares), len(previstos)),
        "pares": [
            {"previsto": previstos[i]["nome"], "real": reais[j]["nome"], "similaridade": round(nota, 3),
             "erro_gramas": round(abs(float(previstos[i]["gramas"] or 0) - float(reais[j]["gramas"])), 1)}
            for i, j, nota in pares
        ],
        "omitidos": [reais[j]["nome"] for j in omissoes],
        "inventados": [previstos[i]["nome"] for i in sem_par_p],
        "erros_gramas_item": erros_item,
        "massa_prevista": massa_prevista,
        "erro_massa_abs": erro_massa,
        "erro_massa_rel": _relativo(erro_massa, massa_real_total),
        "nutrientes": nutrientes,
    }


def percentil(valores: Iterable[float], p: float) -> Optional[float]:
    """Percentil com interpolação linear (o `numpy.percentile` padrão); vazio -> None."""
    ordenados = sorted(float(v) for v in valores)
    if not ordenados:
        return None
    posicao = (len(ordenados) - 1) * p / 100
    baixo, alto = math.floor(posicao), math.ceil(posicao)
    return ordenados[baixo] + (ordenados[alto] - ordenados[baixo]) * (posicao - baixo)


def distribuicao(valores: Iterable[Optional[float]]) -> dict[str, Optional[float]]:
    lista = [float(v) for v in valores if v is not None]
    return {
        "n": len(lista),
        "media": sum(lista) / len(lista) if lista else None,
        "p50": percentil(lista, 50),
        "p90": percentil(lista, 90),
        "p95": percentil(lista, 95),
        "max": max(lista) if lista else None,
    }


def resumir_config(leituras: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Agrega as leituras de uma configuração. Leitura: `ok`, `schema_valido` (None = falha
    de transporte/autenticação/cobrança/rede, fora do denominador; False = resposta cortada,
    JSON inválido ou recusa), `latencia_s`, `custo_usd` e, se ok, `metricas` de `avaliar_leitura`.
    Falhas ficam fora de precisão/recall/erros; falha sem tokens não tem custo (custo_total subestima)."""
    ok = [l for l in leituras if l.get("ok")]
    respondidas = [l for l in leituras if l.get("schema_valido") is not None]
    met = [l["metricas"] for l in ok]
    falhas_por_codigo: dict[str, int] = {}
    for l in leituras:
        if not l.get("ok"):
            codigo = str(l.get("erro") or "desconhecido")
            falhas_por_codigo[codigo] = falhas_por_codigo.get(codigo, 0) + 1
    acertos = sum(m["acertos"] for m in met)
    previstos = acertos + sum(m["invencoes"] for m in met)
    visiveis = sum(m["reais_visiveis"] for m in met)
    acertos_visiveis = sum(m["reais_visiveis"] - m["omissoes"] for m in met)
    return {
        "leituras": len(leituras),
        "ok": len(ok),
        "falhas": len(leituras) - len(ok),
        "falhas_por_codigo": falhas_por_codigo,
        # Leituras ok com total desconhecido por nutriente: somem das distribuições de erro (nunca viram 0).
        "desconhecidos": {c: sum(1 for m in met if m["nutrientes"][c]["erro_abs"] is None) for c in NUTRIENTES},
        "taxa_schema_valido": _relativo(sum(1 for l in respondidas if l["schema_valido"]), len(respondidas)),
        "precisao_micro": _relativo(acertos, previstos),
        "recall_visivel_micro": _relativo(acertos_visiveis, visiveis),
        "invencoes_por_leitura": distribuicao(m["invencoes"] for m in met),
        "omissoes_por_leitura": distribuicao(m["omissoes"] for m in met),
        "erro_gramas_item": distribuicao(e for m in met for e in m["erros_gramas_item"]),
        "erro_massa_total_abs": distribuicao(m["erro_massa_abs"] for m in met),
        "erro_massa_total_rel": distribuicao(m["erro_massa_rel"] for m in met),
        **{
            f"erro_{chave}_{tipo}": distribuicao(m["nutrientes"][chave][f"erro_{tipo}"] for m in met)
            for chave in NUTRIENTES
            for tipo in ("abs", "rel")
        },
        "latencia_s": distribuicao(l.get("latencia_s") for l in leituras),  # geral (inclui falhas)
        "latencia_ok_s": distribuicao(l.get("latencia_s") for l in ok),  # só leituras ok
        "custo_usd": distribuicao(l.get("custo_usd") for l in leituras),
        "custo_total_usd": sum(float(l["custo_usd"]) for l in leituras if l.get("custo_usd") is not None),
    }


def casos_dificeis(leituras: Sequence[Mapping[str, Any]], limite: int = 5) -> list[Mapping[str, Any]]:
    """Os piores por erro relativo de kcal (falha conta como pior)."""
    def chave(leitura: Mapping[str, Any]) -> float:
        if not leitura.get("ok"):
            return math.inf
        erro = leitura["metricas"]["nutrientes"]["kcal"]["erro_rel"]
        return math.inf if erro is None else erro  # kcal desconhecido conta como pior, não 0
    return sorted(leituras, key=chave, reverse=True)[:limite]


def classificar_falha_ia(code: Optional[str], mensagem: str, diagnostic: Optional[str],
                         recusa: str, msg_invalida: str) -> tuple[str, Optional[bool]]:
    """(código do relatório, schema_valido) de uma OpenAIGenerationError (levantada também pelo Claude).

    Resposta cortada/JSON inválido/recusa = schema False; rede, chave ausente e demais falhas = None (transporte).
    shortcut: rede e chave ausente chegam sem code/status próprio; distinguidos pelo texto estável do
    `diagnostic` ("Erro de rede", "nao configurada"); trocar por code dedicado se o cliente passar a expor.
    """
    diag = diagnostic or ""
    if "Erro de rede" in diag:
        return "REDE", None
    if "nao configurada" in diag:
        return "SEM_CHAVE", None
    if code == recusa:
        return code, False
    if code is None or mensagem == msg_invalida:  # o cliente usa a mesma mensagem para corte/JSON inválido
        return code or "RESPOSTA_INVALIDA", False
    return code, None


def _autoteste() -> None:
    g = "OPENAI_GENERATION_FAILED"
    assert classificar_falha_ia(g, "indisp", "Erro de rede ao chamar OpenAI: x", "R", "inv") == ("REDE", None)
    assert classificar_falha_ia(g, "indisp", "OPENAI_API_KEY nao configurada no backend.", "R", "inv") == ("SEM_CHAVE", None)
    assert classificar_falha_ia(g, "inv", "OpenAI devolveu resposta que nao e JSON.", "R", "inv") == (g, False)
    assert classificar_falha_ia("R", "x", None, "R", "inv") == ("R", False)
    assert classificar_falha_ia("OPENAI_RATE_LIMIT", "x", "429", "R", "inv") == ("OPENAI_RATE_LIMIT", None)
    assert normalizar_nome("Feijão-Carioca (cozido)") == "feijao carioca cozido"
    assert traduzir("Arroz branco") == "white rice"
    assert traduzir("Tomates cereja") in ("tomatoes cereja", "cherry tomatoes")
    assert traduzir("Peito de frango grelhado") == "chicken breast grelhado"
    assert similaridade("chicken breast grelhado", "chicken breast") == 1.0
    assert similaridade("white rice", "brown rice") < LIMIAR_SIMILARIDADE
    assert similaridade("", "rice") == 0.0

    reais = [{"nome": "white rice", "gramas": 150}, {"nome": "chicken", "gramas": 100},
             {"nome": "salt", "gramas": 1}, {"nome": "broccoli", "gramas": 60}]
    previstos = [{"nome": "Arroz branco", "gramas": 120}, {"nome": "Frango grelhado", "gramas": 130},
                 {"nome": "Batata frita", "gramas": 50}]
    reais_tot = {"kcal": 500.0, "proteina_g": 40.0, "carbo_g": 50.0, "gordura_g": 10.0}
    prev_tot = {"kcal": 550.0, "proteina_g": 30.0, "carbo_g": None, "gordura_g": 10.0}
    m = avaliar_leitura(previstos, reais, prev_tot, reais_tot, massa_real_total=311)
    assert (m["acertos"], m["invencoes"], m["omissoes"]) == (2, 1, 1), m
    assert m["omitidos"] == ["broccoli"] and m["inventados"] == ["Batata frita"]
    assert m["reais_visiveis"] == 3 and abs(m["recall_visivel"] - 2 / 3) < 1e-9
    assert sorted(m["erros_gramas_item"]) == [30.0, 30.0]
    assert m["massa_prevista"] == 300 and m["erro_massa_abs"] == 11
    assert m["nutrientes"]["kcal"]["erro_abs"] == 50 and m["nutrientes"]["kcal"]["erro_rel"] == 0.1
    assert m["nutrientes"]["carbo_g"]["erro_abs"] is None

    assert percentil([], 50) is None
    assert percentil([1, 2, 3, 4], 50) == 2.5
    assert percentil([10], 95) == 10
    assert abs(percentil(range(1, 101), 90) - 90.1) < 1e-9
    d = distribuicao([3, None, 1, 2])
    assert d["n"] == 3 and d["p50"] == 2 and d["max"] == 3 and d["media"] == 2

    falha = {"ok": False, "schema_valido": False, "latencia_s": 9.0, "custo_usd": 0.002}
    boa = {"ok": True, "schema_valido": True, "latencia_s": 3.0, "custo_usd": 0.001, "metricas": m}
    r = resumir_config([boa, falha, {"ok": False, "schema_valido": None, "latencia_s": None, "custo_usd": None}])
    assert (r["leituras"], r["ok"], r["falhas"]) == (3, 1, 2)
    assert r["taxa_schema_valido"] == 0.5
    assert abs(r["precisao_micro"] - 2 / 3) < 1e-9 and abs(r["recall_visivel_micro"] - 2 / 3) < 1e-9
    assert abs(r["custo_total_usd"] - 0.003) < 1e-12 and r["latencia_s"]["p50"] == 6.0
    assert casos_dificeis([boa, falha], 1) == [falha]
    assert r["desconhecidos"] == {"kcal": 0, "proteina_g": 0, "carbo_g": 1,"gordura_g": 0}, r["desconhecidos"]
    assert r["falhas_por_codigo"] == {"desconhecido": 2}
    # Corte/JSON inválido (schema False) entra no denominador; transporte (None) não.
    cortada = {"ok": False, "schema_valido": False, "erro": "OPENAI_INVALID_RESPONSE"}
    rede = {"ok": False, "schema_valido": None, "erro": "ANTHROPIC_CONNECTION"}
    r2 = resumir_config([boa, cortada, rede])
    assert r2["taxa_schema_valido"] == 0.5 and r2["falhas_por_codigo"] == {"OPENAI_INVALID_RESPONSE": 1, "ANTHROPIC_CONNECTION": 1}
    assert r["latencia_ok_s"]["p50"] == 3.0
    sem_kcal = {"ok": True, "metricas": {"nutrientes": {"kcal": {"erro_rel": None}}}}
    assert casos_dificeis([boa, sem_kcal], 1) == [sem_kcal]


if __name__ == "__main__":
    _autoteste()
    print("metricas: auto-checagem OK")
