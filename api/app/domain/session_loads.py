"""Cargas das séries que NÃO são de trabalho, a partir da carga de trabalho
(M76, Lote 1a — C7). Dono único da regra; o Modo Treino só lê.

Pergunta que este módulo responde: dada a carga de trabalho prescrita para o
par exercício × equipamento (a ÂNCORA) e o passo do equipamento, quanto pôr no
aquecimento, na série de ajuste e em cada aplicação de técnica?

Antes, a regra morava no frontend (`frontend/src/lib/workout-mode-load-suggestions.ts`:
aquecimento 20%/40%, ajuste 0,5 + 0,2·(n−1), drop 0,8ⁱ, sempre arredondado a
2,5 kg) e o motor tinha a sua cópia do drop (`progression_engine.technique_parameters`).
Agora as duas leem daqui, com o passo REAL do equipamento
(`equipment.load_increment_kg`; NULL → `DEFAULT_LOAD_STEP_KG`, o mesmo default
do incremento de carga do motor v2).

Módulo PURO: sem banco, sem FastAPI.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Optional, Sequence

# Passo de carga quando o equipamento não declara `load_increment_kg` (D12).
# Fonte única: `progression_engine.DEFAULT_LOAD_STEP_KG` importa daqui.
DEFAULT_LOAD_STEP_KG = Decimal("2.5")

# Aquecimento genérico do Modo Treino (2 séries antes do 1º exercício de um
# grupo ainda não ativado): 20% e 40% da carga de trabalho.
WARMUP_LOAD_FACTORS: tuple[Decimal, ...] = (Decimal("0.2"), Decimal("0.4"))

# Repetições de cada série do aquecimento genérico acima. Antes era a
# constante `GENERIC_WARMUP_REPS = 15` escrita à mão em
# `PlanExecutionScreen.tsx`; agora o núcleo do Modo Treino
# (`frontend/src/lib/workout-mode-generic-warmup.ts`, celular e relógio) lê
# daqui via `motor-constants.generated.json` (PR B da Evolução ZEPP).
GENERIC_WARMUP_REPS = 15

# Série de ajuste (feeler) N (1-based): 50% da carga de trabalho na 1ª e +20
# pontos percentuais por série seguinte — fórmula ADITIVA, não composta
# (100 kg -> 50, 70, 90).
FEELER_FIRST_FACTOR = Decimal("0.5")
FEELER_STEP_FACTOR = Decimal("0.2")

# Drop-set: cada aplicação tira ~20% da anterior (composto a partir da carga
# de trabalho). Demais técnicas com aplicações (rest-pause...) repetem a carga
# de trabalho — a dificuldade vem das reps, não de menos peso.
DROP_SET_LOAD_FACTOR = Decimal("0.8")


# Ajuste rápido de carga por fadiga no Modo Intensivo (2026-10-24): botões
# "−1 incremento", "−5%" e "−10%" entre as séries de um bloco (FST-7...). As
# reduções percentuais arredondam PARA BAIXO até o passo (nunca sobe a carga
# por arredondamento); o resultado nunca fica abaixo de 0 nem fora do passo.
QUICK_ADJUST_MINUS_STEP = "minus_step"
QUICK_ADJUST_MINUS_5PCT = "minus_5pct"
QUICK_ADJUST_MINUS_10PCT = "minus_10pct"
QUICK_ADJUST_FACTORS: dict[str, Decimal] = {
    QUICK_ADJUST_MINUS_5PCT: Decimal("0.95"),
    QUICK_ADJUST_MINUS_10PCT: Decimal("0.90"),
}
QUICK_ADJUST_ACTIONS: tuple[str, ...] = (QUICK_ADJUST_MINUS_STEP, QUICK_ADJUST_MINUS_5PCT, QUICK_ADJUST_MINUS_10PCT)

# `exercise_sets.load_adjust_source` (092): de onde veio a carga planejada da
# série. `protocol` = redução prevista pela técnica (drop ×0,8); `fatigue` =
# ajuste rápido acima; `manual` = carga digitada.
LOAD_ADJUST_SOURCE_PROTOCOL = "protocol"
LOAD_ADJUST_SOURCE_FATIGUE = "fatigue"
LOAD_ADJUST_SOURCE_MANUAL = "manual"
LOAD_ADJUST_SOURCES: tuple[str, ...] = (
    LOAD_ADJUST_SOURCE_PROTOCOL,
    LOAD_ADJUST_SOURCE_FATIGUE,
    LOAD_ADJUST_SOURCE_MANUAL,
)


def load_step(load_increment_kg: Any) -> Decimal:
    """Passo efetivo do equipamento: `load_increment_kg` > 0, senão o default."""
    if load_increment_kg in (None, ""):
        return DEFAULT_LOAD_STEP_KG
    try:
        step = Decimal(str(load_increment_kg))
    except Exception:  # noqa: BLE001 - valor sujo = sem passo declarado
        return DEFAULT_LOAD_STEP_KG
    return step if step > 0 else DEFAULT_LOAD_STEP_KG


def round_to_step(value: Any, step: Any = None) -> float:
    """Múltiplo mais próximo do passo do equipamento (meio arredonda para cima)."""
    passo = load_step(step)
    quantidade = (Decimal(str(value)) / passo).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(quantidade * passo)


def quick_load_adjust(current_kg: Any, step_kg: Any, action: str) -> float:
    """Carga planejada da próxima série depois do ajuste rápido `action`.

    Unidade e convenção são as EXIBIDAS: `current_kg` e `step_kg` chegam na
    mesma unidade (kg, ou lb convertido pelo cliente) e na mesma convenção
    (total, por lado, por halter) em que o stepper mostra a carga; a função
    só opera na grade do passo. Passo nulo/sujo = `DEFAULT_LOAD_STEP_KG`.

    - `minus_step`: um degrau abaixo na grade (carga fora do passo cai no
      degrau imediatamente inferior: 44,63 com passo 2,5 -> 42,5);
    - `minus_5pct` / `minus_10pct`: `current × fator` arredondado PARA BAIXO
      ao passo (70 com passo 5 -> 65 / 60);
    - piso 0. Ação desconhecida -> `ValueError`."""
    if action not in QUICK_ADJUST_ACTIONS:
        raise ValueError(f"acao de ajuste desconhecida: {action!r}")
    passo = load_step(step_kg)
    atual = max(Decimal(str(current_kg)), Decimal("0"))
    if action == QUICK_ADJUST_MINUS_STEP:
        degraus = atual / passo
        inteiro = degraus.to_integral_value(rounding=ROUND_FLOOR)
        alvo = (inteiro - 1 if inteiro == degraus else inteiro) * passo
    else:
        alvo = ((atual * QUICK_ADJUST_FACTORS[action]) / passo).to_integral_value(rounding=ROUND_FLOOR) * passo
    return float(max(alvo, Decimal("0")))


def warmup_loads(anchor_kg: Any, step: Any = None) -> list[float]:
    """Cargas das séries de aquecimento genéricas (uma por fator)."""
    if anchor_kg is None:
        return []
    base = Decimal(str(anchor_kg))
    return [round_to_step(base * fator, step) for fator in WARMUP_LOAD_FACTORS]


def feeler_load(anchor_kg: Any, index: int, step: Any = None) -> Optional[float]:
    """Carga da série de ajuste `index` (1-based)."""
    if anchor_kg is None or int(index) < 1:
        return None
    fator = FEELER_FIRST_FACTOR + FEELER_STEP_FACTOR * (int(index) - 1)
    return round_to_step(Decimal(str(anchor_kg)) * fator, step)


def feeler_loads(anchor_kg: Any, count: Any, step: Any = None) -> list[float]:
    try:
        total = int(count or 0)
    except (TypeError, ValueError):
        total = 0
    return [value for value in (feeler_load(anchor_kg, index, step) for index in range(1, total + 1)) if value is not None]


def technique_application_loads(code: Optional[str], anchor_kg: Any, count: int, step: Any = None) -> list[float]:
    """Carga de cada aplicação da técnica (1..count). Drop-set reduz ×0,8 por
    aplicação (carga NOVA: arredondada ao passo); as demais REPETEM a carga de
    trabalho tal como está - repetição de carga não é arredondada (29,7% das
    séries de trabalho em 30 dias estão fora do passo cadastrado, várias em
    libras convertidas: arredondar mudaria a carga que a pessoa acabou de usar)."""
    if anchor_kg is None or not code or int(count) < 1:
        return []
    base = Decimal(str(anchor_kg))
    if code == "drop_set":
        return [round_to_step(base * DROP_SET_LOAD_FACTOR**index, step) for index in range(1, int(count) + 1)]
    return [float(base)] * int(count)


def session_load_plan(
    anchor_kg: Any,
    *,
    load_increment_kg: Any = None,
    feeler_sets_count: Any = None,
    application_loads_kg: Optional[Sequence[Any]] = None,
) -> dict[str, Any]:
    """Bloco que o Modo Treino lê (contrato do M76 Lote 1a para o PR 1b).

    - `load_anchor_kg`: carga de trabalho usada como âncora (a prescrita).
    - `load_increment_kg`: passo efetivo do equipamento (stepper e arredondamento).
    - `warmup_loads_kg`: aquecimento genérico (20% e 40%).
    - `feeler_load_kg` / `feeler_loads_kg`: 1ª série de ajuste / todas as
      configuradas na ficha (`feeler_set_sets_count`; ao menos a 1ª).
    - `application_loads_kg`: o que já veio congelado em
      `advanced_technique_parameters` (mesma âncora), repetido aqui só por
      conveniência — não é recalculado.
    """
    passo = load_step(load_increment_kg)
    if anchor_kg is None:
        return {
            "load_anchor_kg": None,
            "load_increment_kg": float(passo),
            "warmup_loads_kg": [],
            "feeler_load_kg": None,
            "feeler_loads_kg": [],
            "application_loads_kg": list(application_loads_kg or []),
        }
    try:
        configuradas = int(feeler_sets_count or 0)
    except (TypeError, ValueError):
        configuradas = 0
    return {
        "load_anchor_kg": float(Decimal(str(anchor_kg))),
        "load_increment_kg": float(passo),
        "warmup_loads_kg": warmup_loads(anchor_kg, passo),
        "feeler_load_kg": feeler_load(anchor_kg, 1, passo),
        "feeler_loads_kg": feeler_loads(anchor_kg, max(1, configuradas), passo),
        "application_loads_kg": list(application_loads_kg or []),
    }
