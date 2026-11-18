"""Texto de alimento: normalização de nome, sinônimos pt-BR, matching e busca
no catálogo.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §48; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). Plano:
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §5 (D2), §7 e §8.1.

Semelhança em 0..1: igualdade após normalizar = 1,0; senão
`QUERY_COVERAGE_WEIGHT` × cobertura (fração dos termos da busca presentes no
candidato) + o resto × (média entre o Jaccard dos termos e a razão do
`difflib.SequenceMatcher` sobre o texto normalizado). Candidato aparece a partir
de `MATCH_MIN_SCORE` (0,6). Aceite automático (pré-seleção) só do PRIMEIRO
candidato, a partir de `MATCH_AUTO_ACCEPT_SCORE` (0,85) e com folga de
`MATCH_AUTO_ACCEPT_MIN_MARGIN` sobre o segundo: busca ambígua não escolhe um
corte sozinha. O 0,85 é expresso como `1 − MATCH_AUTO_ACCEPT_MAX_DISTANCE`
(distância máxima 0,15): o literal `0.85` em Python é reservado pela guarda ao
motor de recuperação (`limiar_fadiga`), e o valor aqui é o mesmo do plano.

Calibração contra os nomes reais da TACO 4ª ed. (Lote L3a, 2026-10-17; mapa
§48; casos em `tests/unit/test_nutrition_food_text_taco.py`). Medido em 49
buscas reais: acerto no topo com nota de candidato 33 → 47 (as 2 restantes são
ambíguas e ficam entre os 3 primeiros), pré-seleção errada 1 → 0.
- "sem" deixou de ser stopword: "sem pele", "sem gordura" e "sem sal"
  distinguem itens da TACO ("peito sem pele cru" casava com "com pele").
- Sinônimos pt-BR: qualificador que ninguém escreve (leite de vaca, ovo de
  galinha inteiro, carne bovina, pão de trigo), variedade (arroz branco = tipo
  1, mussarela = mozarela, castanha-do-pará = castanha-do-brasil) e gênero e
  número do preparo (cozida = cozido, crua = cru...).
- `NOISE_TOKENS`: detalhe de processo que não distingue o alimento (UHT do
  leite longa vida, "cozido/10minutos" do ovo).
- Cobertura da busca na nota: nome longo do catálogo não derruba mais uma
  busca curta ("arroz branco cozido" × "Arroz, tipo 1, cozido": 0,57 → 1,0).

Defaults de desenho, SEM fonte bibliográfica: `TOKEN_JACCARD_WEIGHT` (meio a
meio entre termos e sequência), `QUERY_COVERAGE_WEIGHT` e
`MATCH_AUTO_ACCEPT_MIN_MARGIN` (calibrados na TACO, acima), e os parâmetros da
busca (`SEARCH_*`). `FULLTEXT_MIN_TOKEN_CHARS` é o `innodb_ft_min_token_size`
padrão do MySQL 8 (3): termo menor não entra no índice FULLTEXT.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Final, Hashable, Mapping, Optional, Sequence

from .anthropometrics import fold_text

MATCH_MIN_SCORE: Final[float] = 0.6
MATCH_AUTO_ACCEPT_MAX_DISTANCE: Final[float] = 0.15
MATCH_AUTO_ACCEPT_SCORE: Final[float] = 1.0 - MATCH_AUTO_ACCEPT_MAX_DISTANCE  # 0,85
# Folga mínima do primeiro sobre o segundo para pré-selecionar (default de
# desenho, L3a): "frango com pele assado" dá coxa 0,870 × peito 0,863.
MATCH_AUTO_ACCEPT_MIN_MARGIN: Final[float] = 0.05

# Peso de cada componente da semelhança (defaults de desenho, sem fonte
# bibliográfica; o da cobertura foi calibrado na TACO real no L3a).
QUERY_COVERAGE_WEIGHT: Final[float] = 0.3
TOKEN_JACCARD_WEIGHT: Final[float] = 0.5
SEQUENCE_RATIO_WEIGHT: Final[float] = 1.0 - TOKEN_JACCARD_WEIGHT

# Busca do catálogo (L3a): termos que o FULLTEXT do MySQL indexa, quantos
# candidatos o FULLTEXT entrega para a nota ordenar e quantos a tela recebe.
FULLTEXT_MIN_TOKEN_CHARS: Final[int] = 3
SEARCH_CANDIDATE_POOL: Final[int] = 60
SEARCH_RESULT_LIMIT_DEFAULT: Final[int] = 20
SEARCH_RESULT_LIMIT_MAX: Final[int] = 50
# Marca (#195): a busca também casa `brand LIKE %termo%` a partir de 3 caracteres
# (mesmo tamanho mínimo do FULLTEXT); sem índice na coluna, termo curto varreria à toa.
BRAND_MIN_CHARS: Final[int] = FULLTEXT_MIN_TOKEN_CHARS
# Listagem do cadastro por letra (#195): alfabeto, balde de nomes que não começam
# por A-Z (dígito, símbolo) e página (defaults de desenho, sem fonte bibliográfica).
LETTERS: Final[tuple[str, ...]] = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
OTHER_BUCKET: Final[str] = "outros"
LIST_BUCKETS: Final[tuple[str, ...]] = (*LETTERS, OTHER_BUCKET)
LIST_PAGE_DEFAULT: Final[int] = 50
LIST_PAGE_MAX: Final[int] = 100

# Forma canônica -> variantes regionais/ortográficas (pt-BR), já sem acento.
# A troca é feita numa passada só, da variante mais longa para a mais curta;
# por isso nenhuma forma canônica pode conter outra variante (teste do dono).
SYNONYMS: Final[Mapping[str, tuple[str, ...]]] = {
    "mandioca": ("aipim", "macaxeira"),
    "tangerina": ("mexerica", "bergamota", "mimosa", "poncã", "ponkan"),
    "abobora": ("jerimum",),
    # "galinha" NÃO é sinônimo de "frango": nas tabelas aparece em "Ovo, de
    # galinha", e a troca fazia uma busca por ovo casar com frango. O ovo de
    # galinha inteiro é "o ovo" para quem descreve a refeição (L3a).
    "iogurte": ("yogurte", "yogurt"),
    "batata doce": ("batata-doce",),
    "pao frances": ("pao de sal", "cacetinho", "pao careca"),
    "feijao": ("feijao carioca", "feijao carioquinha"),
    "requeijao": ("requeijao cremoso",),
    "abacaxi": ("ananas",),
    "amendoim": ("peanut",),
    "aveia": ("oat", "oats", "oatmeal"),
    "arroz": ("rice",),
    "ovo": ("ovos", "egg", "eggs", "ovo de galinha", "ovo de galinha inteiro", "ovo inteiro"),
    "leite": ("milk", "leite de vaca"),
    "queijo": ("cheese",),
    "whey": ("whey protein", "proteina do soro"),
    "pimentao": ("pimentão",),
    "quiabo": ("okra",),
    "couve": ("couve manteiga",),
    "inhame": ("cara",),
    # L3a: qualificador implícito (a TACO escreve "Carne, bovina", "Pão, trigo").
    "carne": ("carne bovina", "carne de vaca", "carne de boi"),
    "pao": ("pao trigo", "pao de trigo"),
    # L3a: variedade com outro nome no uso corrente.
    "arroz tipo 1": ("arroz branco",),
    "mozarela": ("mussarela", "muçarela", "mozzarella", "muzzarella", "muzarela"),
    "castanha do brasil": ("castanha do para", "castanha para"),
    # L3a: gênero e número do preparo (a TACO concorda com o alimento).
    "cozido": ("cozida", "cozidos", "cozidas"),
    "cru": ("crua", "crus", "cruas"),
    "assado": ("assada", "assados", "assadas"),
    "grelhado": ("grelhada", "grelhados", "grelhadas"),
    "frito": ("frita", "fritos", "fritas"),
    "refogado": ("refogada", "refogados", "refogadas"),
    "moido": ("moida", "moidos", "moidas"),
}

# "sem" NÃO é stopword desde o L3a ("sem pele" × "com pele").
STOPWORDS: Final[frozenset[str]] = frozenset({"de", "da", "do", "das", "dos", "com", "e", "a", "o", "em", "ao", "para"})

# Detalhe de processo/preparo que não distingue o alimento para quem descreve a
# refeição (L3a): UHT do leite longa vida; "cozido/10minutos" do ovo na TACO.
NOISE_TOKENS: Final[frozenset[str]] = frozenset({"uht", "10minutos"})

_NAO_LETRA = re.compile(r"[^a-z0-9 ]+")

_ALIAS_TO_CANONICAL: Final[Mapping[str, str]] = {
    fold_text(alias): canonico for canonico, aliases in SYNONYMS.items() for alias in aliases
}
# Uma passada, variante mais longa primeiro em cada posição.
_ALIAS_RE = re.compile(
    r"\b(" + "|".join(re.escape(alias) for alias in sorted(_ALIAS_TO_CANONICAL, key=lambda a: (-len(a), a))) + r")\b"
)


def normalize_name(raw: Any) -> str:
    """Minúsculas, sem acento, sem pontuação; sinônimo -> canônico; sem
    stopwords nem `NOISE_TOKENS`. Idempotente."""
    if raw is None:
        return ""
    texto = _NAO_LETRA.sub(" ", fold_text(raw))
    texto = " ".join(texto.split())
    if not texto:
        return ""
    texto = _ALIAS_RE.sub(lambda achado: _ALIAS_TO_CANONICAL[achado.group(1)], texto)
    palavras = [p for p in texto.split() if p not in STOPWORDS and p not in NOISE_TOKENS]
    return " ".join(palavras) if palavras else texto


def tokens(raw: Any) -> frozenset[str]:
    return frozenset(normalize_name(raw).split())


def score(query: Any, candidate: Any) -> float:
    """Semelhança 0..1 entre a descrição e o nome do candidato."""
    a = normalize_name(query)
    b = normalize_name(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = frozenset(a.split()), frozenset(b.split())
    comuns = len(ta & tb)
    cobertura = comuns / len(ta)
    jaccard = comuns / len(ta | tb)
    razao = SequenceMatcher(None, a, b).ratio()
    semelhanca = TOKEN_JACCARD_WEIGHT * jaccard + SEQUENCE_RATIO_WEIGHT * razao
    return QUERY_COVERAGE_WEIGHT * cobertura + (1.0 - QUERY_COVERAGE_WEIGHT) * semelhanca


@dataclass(frozen=True)
class Match:
    candidate_id: Hashable
    name: str
    score: float
    auto_accept: bool

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "name": self.name, "score": self.score, "auto_accept": self.auto_accept}


def brand_score(query: Any, name: Any, brand: Any) -> float:
    """Nota com marca (busca da tela, #195): a MAIOR entre a nota do nome, a da
    marca (mesma `score`, sobre a marca normalizada) e a do texto "nome marca".
    Sem marca = `score(query, name)`. Alimento que só casa pela marca nunca fica
    abaixo da nota da marca. Limitação: a busca que mistura nome e marca ("whey
    growth") não recupera o item (o `brand LIKE` exige a frase contígua e o
    FULLTEXT só olha o nome); o "nome marca" só pontua quem já foi recuperado."""
    nota = score(query, name)
    if not normalize_name(brand):
        return nota
    return max(nota, score(query, brand), score(query, f"{name} {brand}"))


def _ordered(
    query: Any, candidates: Sequence[tuple[Hashable, str] | tuple[Hashable, str, Any]], min_score: float
) -> tuple[Match, ...]:
    """Candidatos com nota >= `min_score`, do melhor para o pior (desempate pelo
    nome). Só o líder POR NOME pode ser pré-selecionado: nota DO NOME >=
    `MATCH_AUTO_ACCEPT_SCORE` e folga >= `MATCH_AUTO_ACCEPT_MIN_MARGIN` sobre o
    segundo por nome. Candidato de 3 posições (id, nome, marca) usa `brand_score` na ordem,
    mas a marca sozinha nunca pré-seleciona (`auto_accept` olha só o nome).
    Ressalva: quem entra só pela marca conta como "segundo por nome" (reduz a folga do líder) e
    o item com `auto_accept` pode ficar abaixo de itens que subiram pela marca."""
    pontuados = []
    for candidato in candidates:
        cid, nome = candidato[0], candidato[1]
        marca = candidato[2] if len(candidato) > 2 else None
        nota = brand_score(query, nome, marca)
        if nota >= min_score:
            pontuados.append((nota, cid, nome, score(query, nome)))
    # Pré-seleção decidida só pelas notas do NOME (como antes da marca): o líder
    # por nome qualifica e a folga é contra o segundo por nome; a marca só ordena.
    por_nome = sorted(pontuados, key=lambda item: (-item[3], item[2]))
    aceito = None
    if por_nome and por_nome[0][3] >= MATCH_AUTO_ACCEPT_SCORE:
        if len(por_nome) == 1 or por_nome[0][3] - por_nome[1][3] >= MATCH_AUTO_ACCEPT_MIN_MARGIN:
            aceito = por_nome[0][1]
    pontuados.sort(key=lambda item: (-item[0], item[2]))
    return tuple(
        Match(candidate_id=cid, name=nome, score=nota, auto_accept=cid == aceito)
        for nota, cid, nome, _ in pontuados
    )


def rank_candidates(query: Any, candidates: Sequence[tuple[Hashable, str]]) -> tuple[Match, ...]:
    """Casamento (IA/diário): só candidatos com nota >= `MATCH_MIN_SCORE`."""
    return _ordered(query, candidates, MATCH_MIN_SCORE)


def order_for_search(
    query: Any, candidates: Sequence[tuple[Hashable, str] | tuple[Hashable, str, Any]]
) -> tuple[Match, ...]:
    """Busca da tela: TODOS os candidatos que o FULLTEXT trouxe, ordenados pela
    mesma nota (o limiar de candidato não corta a lista; a pré-seleção segue a
    mesma regra de `rank_candidates`). Candidato (id, nome, marca): ver `brand_score`."""
    return _ordered(query, candidates, 0.0)


def brand_pattern(query: Any) -> Optional[str]:
    """Padrão `LIKE` `%termo%` da marca (`food_items.brand`, coluna sem
    normalização e sem índice; a collation `utf8mb4_0900_ai_ci` já ignora acento e
    caixa). Curingas do usuário (`\\`, `%`, `_`) são escapados. None se a busca
    tem menos de `BRAND_MIN_CHARS` caracteres (não varre a tabela por "a")."""
    texto = " ".join(str(query or "").split())
    if len(texto) < BRAND_MIN_CHARS:
        return None
    escapado = texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escapado}%"


def normalize_letter(raw: Any) -> Optional[str]:
    """Letra da listagem do cadastro: 'a'..'z' (qualquer caixa) -> 'A'..'Z';
    `OTHER_BUCKET` (qualquer caixa) -> `OTHER_BUCKET`; senão None (inválida)."""
    texto = str(raw or "").strip()
    if texto.lower() == OTHER_BUCKET:
        return OTHER_BUCKET
    return texto.upper() if len(texto) == 1 and texto.upper() in LETTERS else None


def letter_prefixes(letter: str) -> tuple[str, ...]:
    """Padrões `LIKE` de prefixo do nome EXIBIDO (a collation ai_ci ignora acento
    e caixa: "Água" e "abacate" casam com 'A%'). Letra -> 1 padrão; balde de
    não-letras -> os 26 (a rota usa NOT)."""
    return tuple(f"{l}%" for l in LETTERS) if letter == OTHER_BUCKET else (f"{letter}%",)


def list_page(limit: Any, offset: Any) -> tuple[int, int]:
    """Página da listagem: limite em 1..`LIST_PAGE_MAX` (ausente/inválido =
    `LIST_PAGE_DEFAULT`) e deslocamento >= 0 (inválido = 0)."""
    try:
        pedido = int(limit)
    except (TypeError, ValueError):
        pedido = LIST_PAGE_DEFAULT
    try:
        deslocamento = int(offset)
    except (TypeError, ValueError):
        deslocamento = 0
    return max(1, min(pedido, LIST_PAGE_MAX)), max(0, deslocamento)


def search_terms(query: Any) -> tuple[str, ...]:
    """Termos da busca que o FULLTEXT indexa (normalizados, sem repetição, na
    ordem da busca, com pelo menos `FULLTEXT_MIN_TOKEN_CHARS` caracteres)."""
    vistos: list[str] = []
    for termo in normalize_name(query).split():
        if len(termo) >= FULLTEXT_MIN_TOKEN_CHARS and termo not in vistos:
            vistos.append(termo)
    return tuple(vistos)


def fulltext_boolean_query(query: Any) -> Optional[str]:
    """Expressão do FULLTEXT em modo booleano: cada termo como prefixo
    (`termo*`), em OU (a relevância ordena quem casa mais termos). None quando
    nenhum termo tem o tamanho que o índice guarda."""
    termos = search_terms(query)
    return " ".join(f"{termo}*" for termo in termos) if termos else None


def prefix_pattern(query: Any) -> Optional[str]:
    """Padrão `LIKE` de prefixo do nome normalizado, para a busca curta demais
    para o FULLTEXT ("ov" -> "ov%"). Só [a-z0-9 ] sobra da normalização, então
    nenhum curinga do LIKE vem do usuário. None se a busca normaliza para vazio."""
    normalizado = normalize_name(query)
    return f"{normalizado}%" if normalizado else None


def favorites_first(matches: Sequence[Match], favorite_ids: Any) -> tuple[Match, ...]:
    """Preferidos do usuário (N3) antes dos demais, mantendo a ordem da nota dentro de cada
    grupo. Roda ANTES do corte por `search_limit`, para o favorito não cair fora da lista."""
    favoritos = set(favorite_ids)
    return (
        *(m for m in matches if m.candidate_id in favoritos),
        *(m for m in matches if m.candidate_id not in favoritos),
    )


def search_limit(raw: Any) -> int:
    """Quantos resultados devolver: o pedido, limitado a 1..`SEARCH_RESULT_LIMIT_MAX`;
    ausente/inválido = `SEARCH_RESULT_LIMIT_DEFAULT`."""
    try:
        pedido = int(raw)
    except (TypeError, ValueError):
        return SEARCH_RESULT_LIMIT_DEFAULT
    return max(1, min(pedido, SEARCH_RESULT_LIMIT_MAX))
