"""Rótulos pt-BR do status de progressão por exercício (M76 Lote 3c, C13).

Dono ÚNICO do texto que o usuário lê para cada código de
`ExerciseProgressionStatus` (`intelligence/dashboard/metrics_registry.py`).
Até o Lote 3c o mesmo texto vivia em três lugares que já tinham divergido:
`metrics_registry` (sem acento: "Estavel", "Suspeita de plato", "Nao
comparavel" - e era ESTE que a API mandava para a home do dashboard),
`domain/exercise_briefing.py` e `frontend/src/lib/dashboard-status.ts`.

Mora em `domain/` (Python puro, sem I/O, sem importar o pacote
`intelligence`) para que o briefing do exercício e qualquer Lambda o importem
sem carregar os serviços do dashboard. O frontend recebe estes rótulos por
`frontend/src/lib/motor-constants.generated.json`, gerado por
`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar` (o teste falha se o
JSON divergir daqui).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

EXERCISE_PROGRESSION_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "PROGRESSING": "Progredindo",
        "STABLE": "Estável",
        "PLATEAU_SUSPECTED": "Suspeita de platô",
        "REGRESSING": "Regredindo",
        "INSUFFICIENT_DATA": "Dados insuficientes",
        "NOT_COMPARABLE": "Não comparável",
    }
)

# ---------------------------------------------------------------------------
# Escopo das leituras de progressão (issue #152)
#
# O mesmo vocabulário (progredindo/regredindo) aparece em quatro leituras
# DIFERENTES. Cada resposta da API carrega o descritor da(s) leitura(s) que ela
# traz, para a tela dizer O QUE foi comparado. Só descreve: nenhuma
# classificação nasce aqui (o status é de `comparability`; o volume do grupo é
# de `muscle_group_progression`). Sem limiares escritos à mão nos textos.
# ---------------------------------------------------------------------------

SCOPE_EXERCISE_LAST_VS_PREVIOUS: Final[str] = "exercise_last_vs_previous_exposure"
SCOPE_GROUP_WEIGHTED_EXERCISES: Final[str] = "group_weighted_exercise_status"
SCOPE_GROUP_VOLUME_WINDOWS: Final[str] = "group_volume_current_vs_previous_window"
SCOPE_E1RM_WINDOWS: Final[str] = "e1rm_current_vs_previous_window"

ENTITY_EXERCISE: Final[str] = "exercise"
ENTITY_MUSCLE_GROUP: Final[str] = "muscle_group"

PROGRESSION_SCOPES: Final[Mapping[str, Mapping[str, str]]] = MappingProxyType(
    {
        SCOPE_EXERCISE_LAST_VS_PREVIOUS: MappingProxyType(
            {
                "code": SCOPE_EXERCISE_LAST_VS_PREVIOUS,
                "entity": ENTITY_EXERCISE,
                "metric": "exercise_progression_status",
                "comparator": "last_vs_previous_comparable_exposure",
                "label": "Última exposição vs. anterior",
                "description": (
                    "Compara a última exposição do exercício com a anterior, na mesma máquina, "
                    "convenção de carga e contexto de fadiga. A janela só escolhe quais exposições "
                    "entram; trocar de equipamento não conta como piora."
                ),
            }
        ),
        SCOPE_GROUP_WEIGHTED_EXERCISES: MappingProxyType(
            {
                "code": SCOPE_GROUP_WEIGHTED_EXERCISES,
                "entity": ENTITY_MUSCLE_GROUP,
                "metric": "group_exercise_progression_status",
                "comparator": "weighted_exercise_status",
                "label": "Grupo: soma ponderada dos exercícios",
                "description": (
                    "Resume o grupo a partir do status de cada exercício (última exposição vs. "
                    "anterior), ponderado pela participação do exercício no grupo. Vence o conjunto "
                    "de maior peso; empate vale como estável. Um exercício pode piorar e o grupo "
                    "progredir se os demais, de maior peso, progridem."
                ),
            }
        ),
        SCOPE_GROUP_VOLUME_WINDOWS: MappingProxyType(
            {
                "code": SCOPE_GROUP_VOLUME_WINDOWS,
                "entity": ENTITY_MUSCLE_GROUP,
                "metric": "tonnage_per_session",
                "comparator": "current_vs_previous_window",
                "label": "Volume: janela atual vs. anterior",
                "description": (
                    "Compara a tonelagem média por sessão do grupo na janela atual com a da janela "
                    "anterior, de mesmo tamanho. Mede volume, não desempenho: reduzir séries de "
                    "propósito não é piora."
                ),
            }
        ),
        SCOPE_E1RM_WINDOWS: MappingProxyType(
            {
                "code": SCOPE_E1RM_WINDOWS,
                "entity": ENTITY_MUSCLE_GROUP,
                "metric": "best_e1rm",
                "comparator": "best_current_vs_previous_window",
                "label": "e1RM: janela atual vs. anterior",
                "description": (
                    "Compara o melhor e1RM do mesmo exercício (mesma máquina e convenção) na janela "
                    "atual com o da janela anterior. É informativo: não define o status."
                ),
            }
        ),
    }
)


def progression_scope(code: str) -> dict[str, str]:
    """Cópia do descritor de `code` (KeyError se o código não existe)."""
    return dict(PROGRESSION_SCOPES[code])
