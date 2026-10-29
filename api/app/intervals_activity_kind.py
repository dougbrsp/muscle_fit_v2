"""Classificacao de uma atividade do Intervals.icu em CARDIO, FORCA ou OUTRA.

Modulo PURO: nao conhece banco, nao faz rede, nao le ambiente. Recebe o
`sport_type` e o nome da atividade e devolve a classe.

Por que ele existe (F2.3): a heuristica ja estava escrita em
`app/intervals_integration.py`, mas ali ela opera sobre o JSON CRU da API do
Intervals.icu (dezenas de aliases de chave) e vive atras de funcoes privadas
acopladas ao router. O hub novo (`intelligence/dashboard/cardio_service.py`)
le as linhas JA persistidas de `intervals_activities`, onde os campos sao dois
(`sport_type`, `name`). Duplicar as listas de padroes criaria DUAS verdades
sobre o que e cardio - a tela antiga e a nova divergiriam com o tempo, na
calada. Entao as listas moram aqui e os dois lados importam daqui.

Regras (as mesmas de sempre, agora escritas em um lugar so):

1. FORCA vence. `WeightTraining`, `StrengthTraining`, "musculacao", "gym"...
   Uma atividade de forca NUNCA entra no cardio, mesmo que o nome tambem
   mencione uma palavra de cardio ("Treino de forca + esteira").
2. CARDIO e o que casa com a lista de cardio e nao e forca.
3. OUTRA e todo o resto (yoga, alongamento, `Other`...). Ela nao vira cardio
   por eliminacao DE PROPOSITO: somar yoga aos minutos de cardio seria
   inventar dado. A tela declara quantas ficaram de fora.

O casamento e por SUBSTRING sobre o texto normalizado (minusculo, sem espaco,
hifen ou underline), sobre `sport_type` + nome - e por isso `VirtualRide` casa
com `ride` e "Corrida na esteira" casa com `corrida`.
"""

from __future__ import annotations

from typing import Any, Final

CARDIO: Final = "cardio"
STRENGTH: Final = "strength"
OTHER: Final = "other"

# As listas historicas de `intervals_integration` (a tela `/cardio` nasceu com
# elas). Mudar uma palavra aqui muda as duas telas - que e exatamente o ponto.
STRENGTH_PATTERNS: Final[tuple[str, ...]] = (
    "weighttraining",
    "strengthtraining",
    "strength",
    "weights",
    "musculacao",
    "musculação",
    "treinodeforca",
    "treinodeforça",
    "gym",
)

CARDIO_PATTERNS: Final[tuple[str, ...]] = (
    "virtualrun",
    "walk",
    "walking",
    "caminhada",
    "run",
    "running",
    "corrida",
    "ride",
    "virtualride",
    "bike",
    "biking",
    "cycling",
    "bicycle",
    "bicicleta",
    "indoorcycling",
    "spinning",
    "hike",
    "hiking",
    "trail",
    "treadmill",
    "elliptical",
    "cardio",
    "row",
    "rowing",
    "swim",
    "swimming",
)


def normalize_text(value: Any) -> str:
    """Minusculo, sem espaco/hifen/underline - a forma em que os padroes sao
    comparados por substring."""
    text = str(value or "").strip().lower()
    for char in (" ", "-", "_"):
        text = text.replace(char, "")
    return text


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(normalize_text(pattern) in text for pattern in patterns)


def classify(sport_type: Any, name: Any = None) -> str:
    """`CARDIO | STRENGTH | OTHER` a partir do tipo e do nome da atividade."""
    text = normalize_text(f"{sport_type or ''} {name or ''}")
    if _matches(text, STRENGTH_PATTERNS):
        return STRENGTH
    if _matches(text, CARDIO_PATTERNS):
        return CARDIO
    return OTHER


def is_cardio(sport_type: Any, name: Any = None) -> bool:
    return classify(sport_type, name) == CARDIO


def is_strength(sport_type: Any, name: Any = None) -> bool:
    return classify(sport_type, name) == STRENGTH


__all__ = [
    "CARDIO",
    "CARDIO_PATTERNS",
    "OTHER",
    "STRENGTH",
    "STRENGTH_PATTERNS",
    "classify",
    "is_cardio",
    "is_strength",
    "normalize_text",
]
