"""Interpolação dos placeholders do RAG (M97 F5; mapa `docs/motores/FONTES_DA_VERDADE.md` §68).

Princípio 4 do mapa: Markdown de `rag/markdown` injetado em prompt conta como
prompt, e prompt não tem número escrito à mão. O material de RAG de um objetivo
(`rag/markdown/OBJETIVOS/<pasta>/`, ver `training_goal.GOAL_RAG_FOLDERS`) descreve a
prescrição DO APP com placeholders `{{<objetivo>.<campo>}}` (ex.:
`{{strength.reps_min}}`), e este módulo troca cada um pelo valor do motor dono,
no momento da leitura:

- reps, RPE, descanso e séries: `prescription_defaults.defaults_for(objetivo)`;
- RPE-alvo, teto e técnicas fora da política: `training_safety`;
- RIR equivalente ao RPE: `e1rm.rir_from_rpe` (RIR = 10 - RPE);
- piso de exercícios e faixa de compostos pesados do dia: `periodization/limits`;
- cardio (`{{cardio.<campo>}}`, M98 C3a, `rag/markdown/CARDIO.md`): zona-alvo e
  aquecimento padrão do Normal (`cardio_prescription`), escada de Z2, faixa de
  correção, passo de progressão e protocolo de HIIT (`cardio_policy`), teto do
  contínuo e frequência/HIIT por semana (`cardio_envelope`).

Nenhum número mora aqui: só a tabela "nome do placeholder -> função do dono" e
a formatação (inteiro sem casa; minuto a partir dos segundos do dono).

Placeholder desconhecido: em `strict=True` (testes) levanta `UnknownPlaceholder`;
na leitura de produção fica no texto como estava e o aviso vai para o log (um
erro de digitação no Markdown não derruba a geração). O teste
`test_m97_f5_rag_objetivo.py` garante que todo placeholder do repositório resolve.

Python puro, sem I/O. Os donos são importados DENTRO da função: `rag_chunking`
importa este módulo, e `periodization` (que importa `rag_chunking`) não pode
entrar num ciclo de import.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+(?:\.[a-z0-9_]+)*)\s*\}\}")
SECONDS_PER_MINUTE = 60


class UnknownPlaceholder(KeyError):
    """Placeholder sem valor no dono (só em `strict=True`)."""


def _number(value: Any) -> str:
    """Inteiro sem casas; fração com vírgula (2.5 -> "2,5")."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _range_bounds(text: Any) -> tuple[Optional[int], Optional[int]]:
    from .domain.training_history import parse_reps_range

    return parse_reps_range(text)


@lru_cache(maxsize=1)
def placeholder_values() -> Mapping[str, str]:
    """`{nome: texto}` de todo placeholder conhecido, um bloco por objetivo do
    vocabulário. Campo sem valor no dono (ex.: teto de RPE da hipertrofia, que
    não existe) não gera placeholder."""
    from .domain.e1rm import rir_from_rpe
    from .domain.prescription_defaults import defaults_for
    from .domain.progression_engine import ladder_for
    from .domain.training_goal import TrainingGoal, goal_label
    from .domain.training_safety import goal_disallowed_techniques, goal_rpe_ceiling, goal_target_rpe
    from .periodization.limits import heavy_compound_range, session_exercises_min

    values: dict[str, str] = {}
    for goal in TrainingGoal:
        prefix = goal.value
        defaults = defaults_for(goal)
        reps_min, reps_max = _range_bounds(defaults.reps_target)
        rpe_min, rpe_max = _range_bounds(defaults.rpe_target)
        fields: dict[str, Any] = {
            "rotulo": goal_label(goal),
            "reps": defaults.reps_target,
            "reps_min": reps_min,
            "reps_max": reps_max,
            "rpe": defaults.rpe_target,
            "rpe_piso": rpe_min,
            "rpe_alvo": goal_target_rpe(goal),
            "rpe_teto": goal_rpe_ceiling(goal),
            "rir_min": None if rpe_max is None else rir_from_rpe(rpe_max),
            "rir_max": None if rpe_min is None else rir_from_rpe(rpe_min),
            "descanso_min_s": defaults.min_rest_seconds,
            "descanso_max_s": defaults.max_rest_seconds,
            "descanso_min_min": defaults.min_rest_seconds / SECONDS_PER_MINUTE,
            "descanso_max_min": defaults.max_rest_seconds / SECONDS_PER_MINUTE,
            "series": defaults.sets_count,
            "exercicios_min": session_exercises_min(goal),
        }
        compound_range = heavy_compound_range(goal)
        if compound_range is not None:
            fields["compostos_min"], fields["compostos_max"] = compound_range
        # Fix M97 (2026-10-26): faixa de carga por %1RM do objetivo (so quem
        # tem o regime: forca), do dono `progression_engine`.
        load_pct = ladder_for(goal).load_pct_1rm
        if load_pct is not None:
            fields["carga_pct_1rm_min"], fields["carga_pct_1rm_max"] = load_pct
        techniques = sorted(goal_disallowed_techniques(goal))
        if techniques:
            fields["tecnicas_fora"] = ", ".join(techniques)
        for name, value in fields.items():
            if value is None:
                continue
            values[f"{prefix}.{name}"] = value if isinstance(value, str) else _number(value)
    for name, value in _cardio_fields().items():
        values[f"{CARDIO_PREFIX}.{name}"] = value if isinstance(value, str) else _number(value)
    return values


CARDIO_PREFIX = "cardio"


def _cardio_fields() -> dict[str, Any]:
    """Placeholders do cardio (M98 C3a): só leitura dos donos."""
    from .domain import cardio_envelope, cardio_policy, cardio_prescription
    from .domain.hr_zones import zone_label

    defaults = cardio_prescription.DEFAULTS[cardio_prescription.CardioMode.CONTINUOUS]
    correction_min, correction_max = cardio_policy.Z2_TREND_CORRECTION_MINUTES
    return {
        "zona_alvo_continuo": zone_label(defaults[cardio_prescription.TARGET_ZONE_FIELD]),
        "aquecimento_padrao_min": defaults[cardio_prescription.WARMUP_FIELD],
        "z2_inicial_min": cardio_policy.Z2_DURATION_LADDER_MINUTES[0],
        "z2_faixa_min": correction_min,
        "z2_faixa_max": correction_max,
        "progressao_passo_min": cardio_policy.CONTINUOUS_PROGRESSION_STEP_MINUTES,
        "continuo_teto_min": cardio_envelope.CONTINUOUS_SESSION_MAX_MINUTES,
        "sessoes_semana_min": cardio_envelope.WEEKLY_CARDIO_SESSIONS_MIN,
        "sessoes_semana_max": cardio_envelope.WEEKLY_CARDIO_SESSIONS_MAX,
        "hiit_semana_max": cardio_envelope.WEEKLY_HIIT_MAX,
        "hiit_protocolo": cardio_policy.hiit_protocol_text(),
    }


def placeholders_in(text: str) -> list[str]:
    """Nomes dos placeholders do texto, na ordem em que aparecem."""
    return [match.group(1) for match in PLACEHOLDER_RE.finditer(text or "")]


def interpolate(text: str, *, strict: bool = False, values: Optional[Mapping[str, str]] = None) -> str:
    """Troca cada `{{nome}}` pelo valor do dono. Ver o cabeçalho do módulo
    para o placeholder desconhecido."""
    table = placeholder_values() if values is None else values
    unknown: list[str] = []

    def _swap(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in table:
            return table[name]
        unknown.append(name)
        return match.group(0)

    rendered = PLACEHOLDER_RE.sub(_swap, text or "")
    if unknown:
        if strict:
            raise UnknownPlaceholder(", ".join(sorted(set(unknown))))
        logger.warning("rag_placeholders: placeholder(s) sem valor no dono: %s", ", ".join(sorted(set(unknown))))
    return rendered


__all__ = ["PLACEHOLDER_RE", "UnknownPlaceholder", "interpolate", "placeholder_values", "placeholders_in"]
