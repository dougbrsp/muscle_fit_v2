"""Motores DETERMINISTICOS de geracao de ficha (PLANO_20260912.md secao 6).

`mode_b.py`  - `volume_mode='equipment_series'` (PR-12).
`mode_a.py`  - `volume_mode='percent_budget'` (PR-13).

Cada um tem o par legado ao lado (`mode_b_legacy.py`, `mode_a_legacy.py`),
congelado e selecionado pela flag abaixo; e o congelamento da prescricao de
cada modo (`freeze_mode_b.py`, `freeze_mode_a.py`) sai das MESMAS funcoes puras
de `deterministic_prescription.py` que a geracao usa - e por isso que rascunho e
snapshot batem.

Aqui mora tambem a feature flag `GENERATION_ENGINE` (secao 12), temporaria e
removida no PR-20.

    GENERATION_ENGINE=legacy         (default do codigo, quando a env falta)
    GENERATION_ENGINE=deterministic  (motor novo; e o que PRODUCAO roda, junto
                                      com PROGRESSION_ENGINE=v2 - ver
                                      `infra/lambda_motores_deterministicos.tf`)

**A dependencia que esta flag NAO pode esconder.** Os motores deterministicos
prescrevem a partir das trilhas do motor de progressao v2
(`progression_tracks` / `progression_group_states`). Essas tabelas so ficam
atualizadas quando o hook do `finish` roda, e quem decide isso e a OUTRA
flag, `PROGRESSION_ENGINE`. Ligar so a geracao deixaria o motor prescrevendo
sobre estado vazio - toda trilha em CALIBRATION, carga `null`, todo grupo em
F1 - silenciosamente.

Por isso `deterministic_enabled()` exige as DUAS flags:

    GENERATION_ENGINE=deterministic  E  PROGRESSION_ENGINE=v2

Qualquer outra combinacao usa o caminho legado e, quando ha intencao
declarada (`GENERATION_ENGINE=deterministic` sem `PROGRESSION_ENGINE=v2`),
loga `generation.flag_mismatch` - a configuracao pela metade e visivel, nunca
silenciosa.

**Ordem de ativacao em producao** (documentada tambem em
`docs/refatoracao-motores/ATIVACAO_MOTORES_DETERMINISTICOS.md`):

1. `python api/scripts/rebuild_progression_tracks.py --all` (replay: popula as
   trilhas a partir do historico real);
2. `PROGRESSION_ENGINE=v2` (o `finish` passa a manter as trilhas vivas);
3. `GENERATION_ENGINE=deterministic`.

Inverter 1 e 3 e o unico erro grave possivel aqui.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("fitness.generation")

# `reason_code` do `generation.fallback` emitido por este modulo.
FALLBACK_FLAG_MISMATCH = "GENERATION_FLAG_MISMATCH"

GENERATION_FLAG_ENV = "GENERATION_ENGINE"
ENGINE_LEGACY = "legacy"
ENGINE_DETERMINISTIC = "deterministic"
VALID_GENERATION_MODES = (ENGINE_LEGACY, ENGINE_DETERMINISTIC)


def generation_mode() -> str:
    """`GENERATION_ENGINE` normalizado. Ausente/desconhecido -> `legacy`
    (nunca liga o motor novo por acidente)."""
    raw = (os.environ.get(GENERATION_FLAG_ENV) or "").strip().lower()
    return raw if raw in VALID_GENERATION_MODES else ENGINE_LEGACY


def progression_v2_enabled() -> bool:
    """`PROGRESSION_ENGINE=v2`: as trilhas sao mantidas pelo `finish` E podem
    alimentar a prescricao. `shadow` grava mas nao prescreve."""
    from ..progression_state import ENGINE_V2, engine_mode

    return engine_mode() == ENGINE_V2


def deterministic_enabled() -> bool:
    """True so com as duas flags ligadas (ver docstring do modulo)."""
    if generation_mode() != ENGINE_DETERMINISTIC:
        return False
    if progression_v2_enabled():
        return True
    from ..progression_state import engine_mode

    logger.warning(
        json.dumps(
            {
                "event": "generation.flag_mismatch",
                "generation_engine": ENGINE_DETERMINISTIC,
                "progression_engine": engine_mode(),
                "reason": "PROGRESSION_ENGINE precisa ser 'v2' para o motor deterministico prescrever",
            },
            ensure_ascii=False,
        )
    )
    # Secao 14: a configuracao pela metade FAZ a geracao cair no motor legado -
    # e um caminho alternativo ao pretendido, ou seja, um `generation.fallback`
    # (metrica esperada ZERO). O `flag_mismatch` acima continua como estava,
    # para nao quebrar quem ja filtra por ele.
    from .fallback_log import log_generation_fallback

    log_generation_fallback(
        reason_code=FALLBACK_FLAG_MISMATCH,
        engine_version=ENGINE_DETERMINISTIC,
        progression_engine=engine_mode(),
    )
    return False
