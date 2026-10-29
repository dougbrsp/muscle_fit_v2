"""Locais de medida por fita métrica: vocabulário, ordem do ENUM, rótulos pt-BR e lado.

Dono único (regra "motores = fonte única da verdade", plano de nutrição L0,
2026-10-15). Quem consome DAQUI:

- o ENUM `body_measurements.site` (migrações 047 e 075; o teste de plano de cada
  uma compara a tupla da migração com `MEASUREMENT_SITES`);
- a validação da API (`app/body_measurements.py`, que só reexporta);
- as séries do dashboard Corpo (`intelligence/dashboard/body_service.py`), o
  pacote de inteligência e o relatório Markdown (rótulo da cintura);
- o frontend, pelo bloco `body_measurement_sites` de
  `frontend/src/lib/motor-constants.generated.json` (gerado por
  `tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`), lido por
  `frontend/src/lib/body-measurement-sites.ts`.

Nenhum rótulo de local é escrito à mão fora deste módulo (guarda
`rotulo_site_medida` em `tests/unit/test_guarda_fontes_da_verdade.py`).

Python puro, sem FastAPI: a engines importa sem arrastar o router da api-core
(a mesma razão de `context_events_schema.py`; antes o `body_service` mantinha
uma cópia dos rótulos por isso).

Ordem = ordem do ENUM. A 075 só ACRESCENTA no fim (`neck`, `forearm_l`,
`forearm_r`): o pescoço entra na estimativa de % de gordura por circunferência
(plano §3) e o antebraço completa o par braço/antebraço.
"""

from __future__ import annotations

from typing import Any, Final

# Ordem CANÔNICA = ordem do ENUM da coluna (047 + 075).
MEASUREMENT_SITES: Final[tuple[str, ...]] = (
    "waist",
    "hip",
    "chest",
    "arm_l",
    "arm_r",
    "thigh_l",
    "thigh_r",
    "calf_l",
    "calf_r",
    "neck",
    "forearm_l",
    "forearm_r",
)

# Prefixo criado pela 047 e os três locais acrescentados pela 075 (no fim).
SITES_047: Final[tuple[str, ...]] = MEASUREMENT_SITES[:9]
SITES_ADDED_075: Final[tuple[str, ...]] = MEASUREMENT_SITES[9:]

SITE_LABELS: Final[dict[str, str]] = {
    "waist": "Cintura",
    "hip": "Quadril",
    "chest": "Peitoral",
    "arm_l": "Braço esquerdo",
    "arm_r": "Braço direito",
    "thigh_l": "Coxa esquerda",
    "thigh_r": "Coxa direita",
    "calf_l": "Panturrilha esquerda",
    "calf_r": "Panturrilha direita",
    "neck": "Pescoço",
    "forearm_l": "Antebraço esquerdo",
    "forearm_r": "Antebraço direito",
}

# Locais com papel próprio nos motores: a cintura separa recomposição de
# manutenção (`recomp-v1`); pescoço, cintura e quadril (mulher) entram na
# fórmula por circunferência (`domain/nutrition/subject.py`, L2b).
WAIST_SITE: Final[str] = "waist"
NECK_SITE: Final[str] = "neck"
HIP_SITE: Final[str] = "hip"

SIDE_LEFT: Final[str] = "left"
SIDE_RIGHT: Final[str] = "right"
_SIDE_SUFFIXES: Final[dict[str, str]] = {"_l": SIDE_LEFT, "_r": SIDE_RIGHT}


def is_measurement_site(value: Any) -> bool:
    return isinstance(value, str) and value in MEASUREMENT_SITES


def site_label(site: str) -> str:
    """Rótulo pt-BR do local; o próprio código quando desconhecido (nunca levanta)."""
    return SITE_LABELS.get(site, site)


def site_side(site: str) -> str | None:
    """`left`/`right` para locais bilaterais (`*_l`/`*_r`); `None` para os centrais."""
    for suffix, side in _SIDE_SUFFIXES.items():
        if site.endswith(suffix):
            return side
    return None


def export_for_frontend() -> dict[str, Any]:
    """Bloco `body_measurement_sites` de `motor-constants.generated.json`."""
    return {
        "sites": list(MEASUREMENT_SITES),
        "labels": dict(SITE_LABELS),
        "sides": {site: side for site in MEASUREMENT_SITES if (side := site_side(site)) is not None},
        "waist_site": WAIST_SITE,
        "neck_site": NECK_SITE,
    }


__all__ = [
    "MEASUREMENT_SITES",
    "NECK_SITE",
    "SIDE_LEFT",
    "SIDE_RIGHT",
    "SITES_047",
    "SITES_ADDED_075",
    "SITE_LABELS",
    "WAIST_SITE",
    "export_for_frontend",
    "is_measurement_site",
    "site_label",
    "site_side",
]
