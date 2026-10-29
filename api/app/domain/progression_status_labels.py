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
