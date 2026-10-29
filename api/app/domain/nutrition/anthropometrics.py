"""Antropometria básica do motor de nutrição: sexo canônico, idade, IMC,
nível de atividade e o `Subject` que os outros módulos recebem.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `fator_atividade`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.2 e §7.

- `ACTIVITY_FACTORS`: fatores clássicos (1,2 / 1,375 / 1,55 / 1,725 / 1,9),
  usados quando o gasto do treino fica EMBUTIDO no fator
  (`energy_expenditure.TdeeMode.INCLUDED_IN_FACTOR`). TEF já embutido.
- `NON_EXERCISE_ACTIVITY_FACTORS`: fatores de NÃO-exercício (1,2 / 1,3 / 1,4 /
  1,5 / 1,6; decisão D-A do plano — sem fonte canônica única, adotados com o
  TDEE adaptativo sempre ao lado), usados quando o gasto do treino do dia
  entra EXPLÍCITO (`TdeeMode.EXPLICIT_TRAINING`), evitando dupla contagem.
- `HEIGHT_CM_RANGE`: faixa de sanidade da altura no formulário e nas contas
  (fora dela -> `None`, "não calculável").

Contrato público estável (os Lotes L0/L2 dependem destes nomes): `Sex`,
`normalize_sex`, `ActivityLevel`, `normalize_activity_level`,
`ACTIVITY_FACTORS`, `NON_EXERCISE_ACTIVITY_FACTORS`, `HEIGHT_CM_RANGE`,
`age_years`, `bmi`, `Subject`.

Perfil antropométrico (Lote L2a, 2026-10-17; mapa §3.7 e §46): `sex_code` e
`activity_level_code` dão o código canônico que a API grava e devolve
(`users.py`, `/auth/me`), e `INVALID_SEX`, `INVALID_ACTIVITY_LEVEL` e
`INVALID_HEIGHT` são os códigos do 400 da validação do perfil. A migração 077
converte os valores antigos de `users.sex` com uma cópia congelada de
`_SEX_ALIASES` (o teste dela confere a paridade com `normalize_sex`).

Módulo PURO: sem banco, sem FastAPI, sem I/O, sem relógio (a data de
referência é sempre argumento).
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from typing import Any, Final, Mapping, Optional


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class ActivityLevel(str, Enum):
    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"


# Fatores clássicos de atividade (gasto do treino embutido). Plano §3.2.
ACTIVITY_FACTORS: Final[Mapping[ActivityLevel, float]] = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.ACTIVE: 1.725,
    ActivityLevel.VERY_ACTIVE: 1.9,
}

# Fatores de não-exercício (gasto do treino somado à parte). Decisão D-A.
NON_EXERCISE_ACTIVITY_FACTORS: Final[Mapping[ActivityLevel, float]] = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.3,
    ActivityLevel.MODERATE: 1.4,
    ActivityLevel.ACTIVE: 1.5,
    ActivityLevel.VERY_ACTIVE: 1.6,
}

# Faixa de sanidade da altura (cm): fora dela a conta devolve None.
HEIGHT_CM_RANGE: Final[tuple[float, float]] = (100.0, 250.0)

# Faixa de sanidade do peso INFORMADO num formulário (peso-alvo do plano, L2b).
# Default de desenho, sem fonte: só barra erro de digitação (kg × g, vírgula).
# Não filtra as pesagens que as contas recebem (elas usam `positive_number`).
WEIGHT_KG_RANGE: Final[tuple[float, float]] = (20.0, 350.0)

# Conversão de unidades usada em todo o pacote.
CM_PER_METER: Final[float] = 100.0

# Códigos de erro da validação do perfil (L2a): `PUT /users/me`, `PUT/POST /users`
# respondem 400 com `{"error": <código>, "message": ...}`.
INVALID_SEX: Final[str] = "invalid_sex"
INVALID_ACTIVITY_LEVEL: Final[str] = "invalid_activity_level"
INVALID_HEIGHT: Final[str] = "invalid_height_cm"


def fold_text(raw: Any) -> str:
    """Minúsculas, sem acento, sem hífen/sublinhado, espaços únicos."""
    texto = unicodedata.normalize("NFKD", str(raw))
    sem_acento = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(sem_acento.lower().replace("-", " ").replace("_", " ").split())


_SEX_ALIASES: Final[Mapping[str, Sex]] = {
    "m": Sex.MALE,
    "h": Sex.MALE,
    "male": Sex.MALE,
    "man": Sex.MALE,
    "masc": Sex.MALE,
    "masculino": Sex.MALE,
    "homem": Sex.MALE,
    "f": Sex.FEMALE,
    "female": Sex.FEMALE,
    "woman": Sex.FEMALE,
    "fem": Sex.FEMALE,
    "feminino": Sex.FEMALE,
    "feminina": Sex.FEMALE,
    "mulher": Sex.FEMALE,
    "o": Sex.OTHER,
    "x": Sex.OTHER,
    "other": Sex.OTHER,
    "outro": Sex.OTHER,
    "outra": Sex.OTHER,
    "outros": Sex.OTHER,
    "nao binario": Sex.OTHER,
    "nao binaria": Sex.OTHER,
    "non binary": Sex.OTHER,
    "nonbinary": Sex.OTHER,
    "nb": Sex.OTHER,
}


def normalize_sex(raw: Any) -> Optional[Sex]:
    """Sexo canônico a partir de texto livre (pt/en, "M"/"F", "masculino",
    "mulher", maiúsculas, espaços, acentos). Desconhecido/vazio -> None."""
    if raw is None:
        return None
    if isinstance(raw, Sex):
        return raw
    chave = fold_text(raw)
    if not chave:
        return None
    try:
        return Sex(chave)
    except ValueError:
        return _SEX_ALIASES.get(chave)


def sex_code(raw: Any) -> Optional[str]:
    """Código canônico (`male`/`female`/`other`) que a API grava e devolve;
    desconhecido/vazio -> None."""
    sexo = normalize_sex(raw)
    return sexo.value if sexo is not None else None


_ACTIVITY_ALIASES: Final[Mapping[str, ActivityLevel]] = {
    "sedentario": ActivityLevel.SEDENTARY,
    "sedentaria": ActivityLevel.SEDENTARY,
    "leve": ActivityLevel.LIGHT,
    "lightly active": ActivityLevel.LIGHT,
    "moderado": ActivityLevel.MODERATE,
    "moderada": ActivityLevel.MODERATE,
    "moderately active": ActivityLevel.MODERATE,
    "ativo": ActivityLevel.ACTIVE,
    "ativa": ActivityLevel.ACTIVE,
    "muito ativo": ActivityLevel.VERY_ACTIVE,
    "muito ativa": ActivityLevel.VERY_ACTIVE,
}


def normalize_activity_level(raw: Any) -> Optional[ActivityLevel]:
    """Nível de atividade canônico a partir do código (`very_active`) ou de
    texto livre pt/en. Desconhecido/vazio -> None."""
    if raw is None:
        return None
    if isinstance(raw, ActivityLevel):
        return raw
    chave = fold_text(raw)
    if not chave:
        return None
    try:
        return ActivityLevel(chave.replace(" ", "_"))
    except ValueError:
        return _ACTIVITY_ALIASES.get(chave)


def activity_level_code(raw: Any) -> Optional[str]:
    """Código canônico do nível de atividade que a API grava e devolve;
    desconhecido/vazio -> None."""
    nivel = normalize_activity_level(raw)
    return nivel.value if nivel is not None else None


def age_years(birth_date: Optional[date], on_date: Optional[date]) -> Optional[int]:
    """Idade completa em anos em `on_date`. Sem uma das datas, ou nascimento
    depois da referência -> None."""
    if birth_date is None or on_date is None:
        return None
    anos = on_date.year - birth_date.year
    if (on_date.month, on_date.day) < (birth_date.month, birth_date.day):
        anos -= 1
    return anos if anos >= 0 else None


def positive_number(value: Any) -> Optional[float]:
    """float finito e > 0, ou None (bool, texto inválido, NaN, inf, <= 0)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numero) or numero <= 0:
        return None
    return numero


def non_negative_number(value: Any) -> Optional[float]:
    """float finito e >= 0, ou None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numero) or numero < 0:
        return None
    return numero


def height_is_valid(height_cm: Any) -> bool:
    altura = positive_number(height_cm)
    return altura is not None and HEIGHT_CM_RANGE[0] <= altura <= HEIGHT_CM_RANGE[1]


def weight_is_valid(weight_kg: Any) -> bool:
    """Peso informado em formulário dentro de `WEIGHT_KG_RANGE`."""
    peso = positive_number(weight_kg)
    return peso is not None and WEIGHT_KG_RANGE[0] <= peso <= WEIGHT_KG_RANGE[1]


def height_m(height_cm: Any) -> Optional[float]:
    """Altura em metros, ou None fora de `HEIGHT_CM_RANGE`."""
    if not height_is_valid(height_cm):
        return None
    return float(height_cm) / CM_PER_METER


def bmi(weight_kg: Any, height_cm: Any) -> Optional[float]:
    """IMC = kg / m². Peso não positivo ou altura fora da faixa -> None.
    Só informativo (plano §3.4): nenhuma meta usa o IMC."""
    peso = positive_number(weight_kg)
    altura = height_m(height_cm)
    if peso is None or altura is None:
        return None
    return peso / (altura * altura)


@dataclass(frozen=True)
class Subject:
    """Quem recebe o plano: só o que os motores precisam, já normalizado.
    Campos ausentes ficam None e cada conta decide se é calculável."""

    sex: Optional[Sex]
    age_years: Optional[int]
    height_cm: Optional[float]
    weight_kg: Optional[float]
    body_fat_percent: Optional[float] = None
    fat_free_mass_kg: Optional[float] = None
    activity_level: Optional[ActivityLevel] = None

    @property
    def bmi(self) -> Optional[float]:
        return bmi(self.weight_kg, self.height_cm)

    def to_dict(self) -> dict[str, Any]:
        dados = asdict(self)
        dados["sex"] = self.sex.value if self.sex else None
        dados["activity_level"] = self.activity_level.value if self.activity_level else None
        dados["bmi"] = self.bmi
        return dados
