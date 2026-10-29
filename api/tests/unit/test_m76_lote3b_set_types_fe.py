"""Vocabulário de `set_type` do frontend exportado pelo backend (M76, Lote 3b).

O dono da classificação de séries é `api/app/set_types.py` (trabalho,
preparatória, aplicação de técnica, `backoff`, apelidos legados). O frontend
tinha quatro cópias à mão (`workout-mode-types.ts`, `exercise-set-edit.ts`,
`workout-mode-technique.ts`, `workout-mode-previous-session.ts` — esta última
tratando `backoff` como série de trabalho, diferente do backend). Agora há UM
espelho, `frontend/src/lib/set-types.ts`, que lê:

- `frontend/src/lib/set-types.generated.json`: o vocabulário (canônicos,
  apelidos, conjuntos) exportado DAQUI, mais casos de paridade (entrada crua →
  normalizado e classificação calculados pelo dono). O vitest
  (`set-types.test.ts`) roda o espelho TS contra os mesmos casos.

Mudou a regra no dono? Regenere e rode o vitest:

    cd api && python tests/unit/test_m76_lote3b_set_types_fe.py --gravar

Este teste falha se o JSON commitado divergir do que o dono calcula hoje.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[3]
if __name__ == "__main__":  # execução direta (--gravar): `app` importável sem pytest
    sys.path.insert(0, str(RAIZ / "api"))

from app import set_types as st  # noqa: E402

ARQUIVO = RAIZ / "frontend" / "src" / "lib" / "set-types.generated.json"

# Entradas cruas: ausente, vazio, só espaço, cada canônico, cada apelido,
# variações de caixa/espaço e um valor fora do vocabulário.
_EXTRAS: tuple[Any, ...] = (None, "", "   ", "WARMUP", " Drop-Set ", "Rest Pause", "BACKOFF", "superset")


def _entradas() -> list[Any]:
    vistas: list[Any] = []
    for valor in (*_EXTRAS, *sorted(st.KNOWN_SET_TYPES), *sorted(st._ALIASES)):
        if valor not in vistas:
            vistas.append(valor)
    return vistas


def gerado() -> dict[str, Any]:
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote3b_set_types_fe.py (--gravar) a partir de "
            "api/app/set_types.py. Não edite à mão: mude o dono e regenere. M76 Lote 3b."
        ),
        "working": st.WORKING,
        "backoff": st.BACKOFF,
        "laboral": st.LABORAL,
        "aliases": dict(sorted(st._ALIASES.items())),
        "preparatory": sorted(st.PREPARATORY_SET_TYPES),
        "technique_application": sorted(st.TECHNIQUE_APPLICATION_SET_TYPES),
        "known": sorted(st.KNOWN_SET_TYPES),
        "cases": [
            {
                "input": valor,
                "normalized": st.normalize_set_type(valor),
                "is_working": st.is_working_set(valor),
                "is_preparatory": st.is_preparatory_set(valor),
                "is_technique_application": st.is_technique_application(valor),
            }
            for valor in _entradas()
        ],
    }


def _gravar() -> None:
    ARQUIVO.write_text(json.dumps(gerado(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_vocabulario_do_fe_e_o_do_dono():
    assert json.loads(ARQUIVO.read_text(encoding="utf-8")) == gerado(), (
        f"{ARQUIVO.name} divergiu do dono: rode `python tests/unit/test_m76_lote3b_set_types_fe.py --gravar`"
    )


def test_casos_cobrem_apelidos_backoff_e_legado():
    """Sanidade dos próprios casos: os pontos onde as cópias do FE erravam."""
    por_entrada = {caso["input"]: caso for caso in gerado()["cases"]}
    # legado NULL/vazio = working
    assert por_entrada[None]["normalized"] == "working" and por_entrada[None]["is_working"]
    assert por_entrada["   "]["is_working"]
    # `backoff` é trabalho REDUZIDO, não série de trabalho (o FE dizia que era)
    assert por_entrada["backoff"]["is_working"] is False
    assert por_entrada["BACKOFF"]["normalized"] == "backoff"
    # apelidos além de `dropset` (o FE só conhecia esse)
    assert por_entrada["warm-up"]["is_preparatory"] and por_entrada["feeler_set"]["is_preparatory"]
    assert por_entrada[" Drop-Set "]["is_technique_application"]
    assert por_entrada["restpause"]["normalized"] == "rest_pause"
    # fora do vocabulário: não vira trabalho em silêncio
    assert por_entrada["superset"] == {
        "input": "superset",
        "normalized": "superset",
        "is_working": False,
        "is_preparatory": False,
        "is_technique_application": False,
    }


if __name__ == "__main__":
    if "--gravar" not in sys.argv:
        raise SystemExit("uso: python tests/unit/test_m76_lote3b_set_types_fe.py --gravar")
    _gravar()
    print(f"gravado: {ARQUIVO}")
