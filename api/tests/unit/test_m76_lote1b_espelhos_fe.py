"""Espelhos do frontend exportados pelo backend (M76, Lote 1b — C7 e C10;
Lote 3c — pesos default por papel e rótulos de status de progressão;
Lote 4c — defaults de prescrição (E8) e "Tempo total de treino" do Modelo (E7)).

O Modo Treino precisa calcular cargas de aquecimento/ajuste/drop a partir de
âncoras que só existem DURANTE a sessão (a série de trabalho real que acabou de
ser lançada, a sugestão da IA, a carga escolhida após trocar de equipamento).
Ir ao servidor a cada drop-set (descanso de 0-10 s, academia com sinal ruim)
não é viável, então o FE mantém um espelho da conta de
`api/app/domain/session_loads.py` — SEM constantes próprias:

- `frontend/src/lib/motor-constants.generated.json`: os fatores e defaults
  (aquecimento, ajuste, drop, passo default; RPE seguro de `training_safety`)
  exportados DAQUI. O FE importa os números desse JSON; nenhum é escrito à mão.
- `frontend/src/lib/session-loads.parity.json`: casos (âncora × passo) com as
  cargas calculadas pelo dono. O vitest roda o espelho TS contra os mesmos
  casos (`workout-mode-load-suggestions.test.ts`).
- `frontend/src/lib/template-time-estimate.parity.json` (Lote 4c): casos de
  slot com o resultado COMPLETO do dono `periodization/time_estimate.py`
  (total, parcelas, itens sem descanso, reps estimadas). O builder recalcula o
  tempo a cada tecla (o preview só responde depois do debounce), então o
  espelho `template-time-estimate.ts` roda contra estes casos no vitest.
- `frontend/src/lib/intensity-density.parity.json` (2026-10-13): itens e
  grupos com o resultado do dono `domain/intensity_density.py` (Intensidade ×
  Densidade do editor de Modelo, calculada na tela porque o preview não leva
  RPE, descanso nem pico); o espelho `intensity-density.ts` roda contra eles.
- `frontend/src/lib/set-measures.parity.json` (2026-10-20, medidas de série T1):
  entradas cruas normalizadas e textos ("10 reps", "60 s", "15 reps em 30 s")
  calculados pelo dono `domain/set_measures.py`; o espelho `set-measures.ts`
  roda contra eles.
- `frontend/src/lib/cardio-prescription.parity.json` (M98 C1, 2026-10-26):
  itens de cardio do Modelo com o tempo total, o texto canônico (`describe`) e
  o resumo do item recolhido (`describe_short`) calculados pelo dono
  `domain/cardio_prescription.py`; o espelho `cardio-prescription.ts` roda
  contra eles (o editor recalcula a cada tecla, sem ir ao servidor).
- `frontend/src/lib/technique-accounting.parity.json` (2026-10-13): catálogos,
  aplicações e blocos com o resultado do dono `domain/technique_accounting.py`
  (o "= 0,5 série equivalente" do Modo Treino); o espelho
  `technique-accounting.ts` roda contra eles.

Mudou a regra no dono? Regenere os arquivos e rode o vitest:

    cd api && python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar

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

from app.domain import body_measurement_sites as bms  # noqa: E402
from app.domain import intensity_density as idd  # noqa: E402
from app.domain import nutrition  # noqa: E402
from app.domain import intensity_status_labels as isl  # noqa: E402
from app.domain import technique_accounting as ta  # noqa: E402
from app.domain import next_set_load as nsl  # noqa: E402
from app.domain import set_evaluation_categories as sec  # noqa: E402
from app.domain import set_measures as smeas  # noqa: E402
from app.domain.exercise_briefing import DECISION_LABELS  # noqa: E402
from app.engine_decision_context import REASON_ENGINE_TARGET, REASON_EQUIPMENT_STEP  # noqa: E402
from app.domain import prescription_defaults as pd  # noqa: E402
from app.domain import session_loads as sl  # noqa: E402
from app.domain import training_safety as ts  # noqa: E402
from app.domain.progression_status_labels import EXERCISE_PROGRESSION_LABELS  # noqa: E402
from app.intelligence import methodology_volume as mvol  # noqa: E402
from app.intelligence.muscle_contribution_resolver import DEFAULT_WRITE_WEIGHTS  # noqa: E402
from app.periodization import limits  # noqa: E402
from app.periodization import time_estimate as te  # noqa: E402
from app.domain import cardio_prescription as cp  # noqa: E402
from app.domain import hr_zones as hz  # noqa: E402

LIB = RAIZ / "frontend" / "src" / "lib"
CONSTANTES = LIB / "motor-constants.generated.json"
CASOS = LIB / "session-loads.parity.json"
CASOS_TEMPO = LIB / "template-time-estimate.parity.json"
CASOS_INTENSIDADE = LIB / "intensity-density.parity.json"
CASOS_TECNICA = LIB / "technique-accounting.parity.json"
CASOS_MEDIDAS = LIB / "set-measures.parity.json"
CASOS_CARDIO = LIB / "cardio-prescription.parity.json"

# Âncoras reais e de borda: cargas fora do passo (libras convertidas: 22,68 /
# 44,63), meio-passo exato (31,25 × 20% = 6,25 → 7,5; 11,35 × 50% = 5,675 com
# passo 2,27 → 6,81, o caso que o float do JS erra sem quantizar), zero.
_ANCORAS = (0, 7.5, 11.35, 12.5, 22.68, 31.25, 37.5, 44.63, 55, 63, 72.5, 80, 100, 101, 140)
# None = equipamento sem `load_increment_kg` (vale o default).
_PASSOS = (None, 0.5, 1, 2.27, 2.5, 4.54, 5)
_AJUSTES = 3
_APLICACOES = 3


def constantes() -> dict[str, Any]:
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote1b_espelhos_fe.py (--gravar) a partir de "
            "api/app/domain/session_loads.py, api/app/domain/training_safety.py, "
            "api/app/intelligence/muscle_contribution_resolver.py e "
            "api/app/domain/progression_status_labels.py, api/app/domain/prescription_defaults.py e "
            "api/app/periodization/time_estimate.py e limits.py, api/app/domain/intensity_status_labels.py, "
            "api/app/domain/exercise_briefing.py (DECISION_LABELS), api/app/domain/set_evaluation_categories.py, "
            "api/app/domain/next_set_load.py, api/app/engine_decision_context.py, "
            "api/app/domain/intensity_density.py e api/app/domain/technique_accounting.py "
            "(pontos de esforço de api/app/intelligence/muscle_recovery_service.py) e "
            "api/app/intelligence/methodology_volume.py (modo de contagem do volume), "
            "api/app/domain/body_measurement_sites.py (locais de medida, L0 de nutrição), "
            "api/app/domain/nutrition (bloco nutrition, L1 de nutrição) e "
            "api/app/domain/set_measures.py (bloco set_measures, Lote T1 das medidas de série). "
            "Não edite à mão: mude o dono e regenere. "
            "M76 Lote 1b (C7, C10), Lote 3c (pesos por papel, C13), Lote 4c (E7, E8), RPE 10 Lotes 1 (P1) e 6 (P9) "
            "e espelhos de intensidade × densidade (E13) e de técnica (2026-10-13)."
        ),
        "session_loads": {
            "default_load_step_kg": float(sl.DEFAULT_LOAD_STEP_KG),
            "warmup_load_factors": [float(f) for f in sl.WARMUP_LOAD_FACTORS],
            "generic_warmup_reps": sl.GENERIC_WARMUP_REPS,
            "feeler_first_factor": float(sl.FEELER_FIRST_FACTOR),
            "feeler_step_factor": float(sl.FEELER_STEP_FACTOR),
            "drop_set_load_factor": float(sl.DROP_SET_LOAD_FACTOR),
            # Modo Intensivo (2026-10-24): ajuste rápido de carga por fadiga.
            "quick_adjust_actions": list(sl.QUICK_ADJUST_ACTIONS),
            "quick_adjust_factors": {acao: float(fator) for acao, fator in sl.QUICK_ADJUST_FACTORS.items()},
            "load_adjust_sources": list(sl.LOAD_ADJUST_SOURCES),
        },
        "training_safety": {
            "min_safe_rpe": ts.MIN_SAFE_RPE,
            "max_safe_rpe": ts.MAX_SAFE_RPE,
            "default_max_safe_rpe": ts.DEFAULT_MAX_SAFE_RPE,
        },
        # Lote 3c: valor inicial (estímulo = fadiga) que o admin vê ao cadastrar
        # uma contribuição muscular (`MuscleContributionsSection.tsx`).
        "muscle_contributions": {
            "default_write_weights": {role: float(weight) for role, weight in DEFAULT_WRITE_WEIGHTS.items()},
        },
        # Lote 3c (C13): rótulo de cada código de status de progressão.
        "exercise_progression_labels": dict(EXERCISE_PROGRESSION_LABELS),
        # Lote 4c (E8): defaults de prescrição (placeholders da ficha, intervalo
        # de descanso da geração, descanso do timer do Modo Treino).
        "prescription": pd.export_for_frontend(),
        # Lote 4c (E7): premissas do "Tempo total de treino" do Modelo.
        "time_estimate": te.export_for_frontend(),
        # M98 C0: prescrição de cardio do Modelo (modos, unidades, padrões e
        # tetos) e zonas de FC do app (Z1-Z5).
        "cardio_prescription": cp.export_for_frontend(),
        "hr_zones": hz.export_for_frontend(),
        # Lote 4c (E9): tetos do complemento e de priorizados (rótulos do
        # relatório da Periodização e validação do builder).
        "periodization_limits": limits.export_for_frontend(),
        # Lote 1/P1 da investigação RPE 10 (2026-10-11): códigos e rótulos de
        # `intensity_status`/`safety_event` (dono `domain/intensity_status_labels.py`).
        "intensity": isl.export_for_frontend(),
        # RPE 10 Lote 6/P9: rótulos das ações do motor (`DECISION_LABELS`, que o
        # FE lia de uma cópia manual em `exercise-progress.ts`) e categorias da
        # IA por série no vocabulário do motor, com os motivos de ajuste de carga.
        "progression_decision_labels": dict(DECISION_LABELS),
        "set_evaluation": {
            **sec.export_for_frontend(),
            "load_adjustment_reason_engine_target": REASON_ENGINE_TARGET,
            "load_adjustment_reason_equipment_step": REASON_EQUIPMENT_STEP,
        },
        # 2026-10-12: carga da PRÓXIMA série na sessão (regra do usuário, dono
        # `domain/next_set_load.py`): motivos e rótulos (limiares interpolados).
        "next_set_load": nsl.export_for_frontend(),
        # 2026-10-13 (E13): "Intensidade × Densidade" do editor de Modelo (dono
        # `domain/intensity_density.py`; o espelho TS calcula na tela).
        "intensity_density": idd.export_for_frontend(),
        # 2026-10-13: contabilização de técnica (dono `domain/technique_accounting.py`;
        # pontos de esforço de `MuscleRecoveryService._EFFORT_POINTS`).
        "technique_accounting": ta.export_for_frontend(),
        # 2026-10-16 (modo de contagem do volume, Lote 3): valores, rótulos e
        # régua de cada `VolumeCountingMode` (dono `intelligence/methodology_volume.py`).
        "methodology_volume": mvol.export_for_frontend(),
        # 2026-10-15 (nutrição L0): locais de medida por fita (ordem do ENUM 047+075,
        # rótulos, lado); o FE lê via `body-measurement-sites.ts`, sem texto à mão.
        "body_measurement_sites": bms.export_for_frontend(),
        # 2026-10-16 (nutrição, L1): códigos, rótulos, faixas de formulário, RDC 429 e
        # porções caseiras (dono `domain/nutrition`); lido por `nutrition-constants.ts`.
        "nutrition": nutrition.export_for_frontend(),
        # 2026-10-20 (medidas de série, Lote T1): valores, rótulos, `rep_based`, limites e o motivo
        # "não baseada em reps" (dono `domain/set_measures.py`); lido por `set-measures.ts`.
        "set_measures": smeas.export_for_frontend(),
    }


# Medidas de série (T1): as entradas cruas que `normalize_measure` resolve e os
# textos de `format_set_measure`, calculados pelo dono. O espelho
# `set-measures.ts` (`isRepBased`, `measureLabel`, `formatSetMeasureText`) roda
# contra eles no vitest (`set-measures.test.ts`).
_ENTRADAS_MEDIDA: tuple[Any, ...] = (None, "", "  ", "reps", "REPS ", "duration", "Duration", "max_reps", "tempo")
_CASOS_TEXTO: tuple[tuple[Any, Any, Any], ...] = (
    ("reps", 10, None),
    (None, 8, None),
    ("reps", "12", None),
    ("reps", 10, 30),
    ("reps", None, None),
    ("duration", None, 60),
    ("duration", 10, 60),
    ("duration", None, None),
    ("max_reps", 15, 30),
    ("max_reps", 15, None),
    ("max_reps", None, 30),
    ("tempo", 9, 45),
)
# Lote T2: tempo de execução planejado de uma série (`planned_set_execution_seconds`),
# que o espelho `plannedSetExecutionSeconds` usa no "Tempo total de treino".
_CASOS_EXECUCAO: tuple[tuple[Any, Any, Any], ...] = (
    ("reps", None, None),
    (None, 60, 30),
    ("duration", 60, None),
    ("duration", "45", None),
    ("duration", None, None),
    ("duration", 60, 30),
    ("max_reps", None, 30),
    ("max_reps", 60, None),
    ("max_reps", None, None),
    ("tempo", 60, 30),
)


def casos_medidas() -> dict[str, Any]:
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote1b_espelhos_fe.py (--gravar): normalização e texto das "
            "medidas de série calculados pelo dono api/app/domain/set_measures.py. Não edite à mão: mude o dono "
            "e regenere. Medidas de série, Lote T1 (2026-10-20)."
        ),
        "normalize": [
            {
                "input": valor,
                "normalized": smeas.normalize_measure(valor).value,
                "rep_based": smeas.is_rep_based(valor),
                "label": smeas.measure_label(valor),
            }
            for valor in _ENTRADAS_MEDIDA
        ],
        "format": [
            {
                "measure": measure,
                "reps": reps,
                "duration_seconds": duration,
                "text": smeas.format_set_measure(measure, reps, duration),
            }
            for measure, reps, duration in _CASOS_TEXTO
        ],
        "planned_execution": [
            {
                "measure": measure,
                "duration_seconds_target": target,
                "time_window_seconds": window,
                "seconds": smeas.planned_set_execution_seconds(measure, target, window),
            }
            for measure, target, window in _CASOS_EXECUCAO
        ],
    }


def casos() -> dict[str, Any]:
    lista = []
    for passo in _PASSOS:
        for ancora in _ANCORAS:
            lista.append(
                {
                    "anchor_kg": ancora,
                    "load_increment_kg": passo,
                    "round_to_step": sl.round_to_step(ancora, passo),
                    "warmup_loads_kg": sl.warmup_loads(ancora, passo),
                    "feeler_loads_kg": sl.feeler_loads(ancora, _AJUSTES, passo),
                    "drop_set_loads_kg": sl.technique_application_loads("drop_set", ancora, _APLICACOES, passo),
                    "rest_pause_loads_kg": sl.technique_application_loads("rest_pause", ancora, _APLICACOES, passo),
                }
            )
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote1b_espelhos_fe.py (--gravar): cargas calculadas pelo dono "
            "(api/app/domain/session_loads.py). O vitest roda o espelho TS (workout-mode-load-suggestions.ts) "
            "contra os mesmos casos. `quick_load_adjust_cases` (Modo Intensivo): ajuste rápido de carga "
            "(`session_loads.quick_load_adjust`) em kg, em lb (valores já convertidos pelo cliente) e nas bordas. "
            "Não edite à mão."
        ),
        "cases": lista,
        "quick_load_adjust_cases": casos_ajuste_rapido(),
    }


# Ajuste rápido (Modo Intensivo): (carga exibida, passo exibido, unidade). A
# função é agnóstica de unidade; `unit` só documenta o caso para o espelho.
_AJUSTES_RAPIDOS: tuple[tuple[Any, Any, str], ...] = (
    (70, 5, "kg"),  # plano: 65 / 65 / 60
    (70, 2.5, "kg"),
    (44.63, 2.5, "kg"),  # fora do passo: degrau imediatamente inferior
    (11.35, 2.27, "kg"),  # passo de equipamento em libras convertido
    (20, None, "kg"),  # passo nulo = default
    (155, 5, "lb"),
    (45, 2.5, "lb"),
    (2, 2.5, "kg"),  # piso 0
    (2.5, 2.5, "kg"),
    (0, 2.5, "kg"),
)


def casos_ajuste_rapido() -> list[dict[str, Any]]:
    return [
        {
            "current": atual,
            "step": passo,
            "unit": unidade,
            "results": {acao: sl.quick_load_adjust(atual, passo, acao) for acao in sl.QUICK_ADJUST_ACTIONS},
        }
        for atual, passo, unidade in _AJUSTES_RAPIDOS
    ]


# Casos do "Tempo total de treino" (entradas; a saída é do dono). Os 11
# primeiros são os da Fase 7/M46 (os totais não mudaram no Lote 4c); os
# seguintes cobrem o que antes só o TS calculava (parcelas, itens sem
# descanso, reps estimadas).
_CASOS_TEMPO: tuple[dict[str, Any], ...] = (
    {"name": "slot vazio", "items": []},
    {
        "name": "rascunho da IA: faixa de reps, sem descanso",
        "items": [{"item_kind": "equipment", "sets_count": 4, "reps_target": "8-12", "rest_seconds": None}],
    },
    {
        "name": "descanso e pico de contração",
        "items": [
            {"item_kind": "equipment", "sets_count": 3, "reps_target": "10", "rest_seconds": 90, "peak_contraction_seconds": 2}
        ],
    },
    {
        "name": "aquecimento, ajuste e faixa por extenso",
        "items": [
            {
                "item_kind": "equipment",
                "sets_count": 3,
                "reps_target": "8 a 12",
                "warmup_sets_count": 2,
                "warmup_reps": 12,
                "warmup_rest_seconds": 60,
                "feeler_set_sets_count": 1,
                "feeler_set_reps": 5,
                "feeler_set_rest_seconds": 30,
            }
        ],
    },
    {
        "name": "cardio e técnica com pausa",
        "items": [
            {"item_kind": "cardio", "cardio_duration_minutes": 20},
            {
                "item_kind": "equipment",
                "sets_count": 2,
                "reps_target": "12",
                "advanced_technique_applications": 3,
                "advanced_technique_duration_seconds": 15,
            },
        ],
    },
    {
        "name": "reps vazio vira 10; vírgula decimal; três números usam os dois primeiros",
        "items": [
            {"item_kind": "equipment", "sets_count": 2, "reps_target": None},
            {"item_kind": "equipment", "sets_count": 1, "reps_target": "7,5"},
            {"item_kind": "equipment", "sets_count": 1, "reps_target": "8-12-15"},
        ],
    },
    {
        "name": "arredondamento do Math.round (meio para cima)",
        "items": [{"item_kind": "equipment", "sets_count": 1, "reps_target": "1,5"}],
    },
    {
        "name": "sem séries de trabalho: só o aquecimento conta",
        "items": [
            {
                "item_kind": "equipment",
                "sets_count": None,
                "warmup_sets_count": 1,
                "warmup_reps": 10,
                "warmup_rest_seconds": 45,
            }
        ],
    },
    {
        "name": "complemento: descanso padrão de 90 s quando rest_seconds é nulo",
        "default_rest_seconds": 90,
        "items": [{"item_kind": "equipment", "sets_count": 4, "reps_target": "8-12", "rest_seconds": None}],
    },
    {
        "name": "complemento: descanso definido no item prevalece sobre o padrão",
        "default_rest_seconds": 90,
        "items": [
            {"item_kind": "equipment", "sets_count": 3, "reps_target": "10", "rest_seconds": 60},
            {"item_kind": "equipment", "sets_count": 2, "reps_target": "10", "rest_seconds": None},
        ],
    },
    {
        "name": "complemento: padrão não toca aquecimento, ajuste, técnica nem cardio",
        "default_rest_seconds": 90,
        "items": [
            {"item_kind": "cardio", "cardio_duration_minutes": 10},
            {
                "item_kind": "equipment",
                "sets_count": None,
                "warmup_sets_count": 1,
                "warmup_reps": 10,
                "warmup_rest_seconds": None,
                "advanced_technique_applications": 2,
                "advanced_technique_duration_seconds": None,
            },
        ],
    },
    {
        "name": "descanso 0 explícito também é 'sem descanso definido'",
        "items": [
            {"item_kind": "equipment", "sets_count": 3, "reps_target": "10", "rest_seconds": 0},
            {"item_kind": "equipment", "sets_count": 3, "reps_target": "10", "rest_seconds": 60},
        ],
    },
    {
        "name": "item de grupo muscular com séries não conta como exercício sem descanso",
        "items": [{"item_kind": "muscle_group", "sets_count": 3, "reps_target": "10", "rest_seconds": None}],
    },
    {
        "name": "parcelas de execução e descanso arredondadas à parte (meio segundo)",
        "items": [
            {
                "item_kind": "equipment",
                "sets_count": 1,
                "reps_target": "8,5",
                "rest_seconds": 30,
                "peak_contraction_seconds": 1,
                "advanced_technique_applications": 1,
                "advanced_technique_duration_seconds": 7.5,
            }
        ],
    },
    {
        # 2026-10-25: pausa padrão do rest-pause (dono `progression_engine`).
        "name": "rest-pause sem duração usa a pausa padrão do dono; com duração, a do item; drop set sem padrão",
        "items": [
            {
                "item_kind": "equipment",
                "advanced_technique": "rest_pause",
                "advanced_technique_applications": 2,
                "advanced_technique_duration_seconds": None,
            },
            {
                "item_kind": "equipment",
                "advanced_technique": "rest_pause",
                "advanced_technique_applications": 2,
                "advanced_technique_duration_seconds": 20,
            },
            {
                "item_kind": "equipment",
                "advanced_technique": "drop_set",
                "advanced_technique_applications": 2,
                "advanced_technique_duration_seconds": None,
            },
        ],
    },
)


# Lote T2 (medidas de série): itens por tempo e por máximo de reps. O tempo da
# série vem do dono (`set_measures.planned_set_execution_seconds`); os casos
# antigos acima (sem `measure`) dão o MESMO resultado de antes.
_CASOS_TEMPO_MEDIDA: tuple[dict[str, Any], ...] = (
    {
        "name": "prancha: 3 séries de 60 s com 30 s de descanso",
        "items": [
            {"item_kind": "equipment", "sets_count": 3, "measure": "duration", "duration_seconds_target": 60,
             "rest_seconds": 30},
        ],
    },
    {
        "name": "máximo de reps com janela de 30 s usa a janela",
        "items": [
            {"item_kind": "equipment", "sets_count": 2, "measure": "max_reps", "time_window_seconds": 30,
             "rest_seconds": 60},
        ],
    },
    {
        "name": "máximo de reps sem janela estima as repetições",
        "items": [{"item_kind": "equipment", "sets_count": 2, "measure": "max_reps", "rest_seconds": 60}],
    },
    {
        "name": "medida 'reps' explícita é a conta de sempre (pico conta)",
        "items": [
            {"item_kind": "equipment", "sets_count": 3, "measure": "reps", "reps_target": "10",
             "peak_contraction_seconds": 2, "rest_seconds": 60},
        ],
    },
    {
        "name": "tempo por série ignora reps e pico; medida desconhecida lê como reps",
        "items": [
            {"item_kind": "equipment", "sets_count": 2, "measure": "duration", "duration_seconds_target": 45,
             "reps_target": "12", "peak_contraction_seconds": 2, "rest_seconds": None},
            {"item_kind": "equipment", "sets_count": 1, "measure": "minutos", "reps_target": "10",
             "rest_seconds": 90},
        ],
    },
    {
        "name": "medida por tempo sem alvo cai na conta por repetições",
        "items": [{"item_kind": "equipment", "sets_count": 1, "measure": "duration", "rest_seconds": 30}],
        "default_rest_seconds": 90,
    },
)


# M98 C0 (prescrição de cardio): o tempo do item de cardio vem do dono
# `domain/cardio_prescription.total_seconds` (Normal/HIIT, aquecimento e
# recuperação). Os casos antigos (só duração) dão o MESMO resultado de antes.
_CASOS_TEMPO_CARDIO: tuple[dict[str, Any], ...] = (
    {
        "name": "cardio Normal com aquecimento, zona e recuperação",
        "items": [
            {"item_kind": "cardio", "cardio_mode": "continuous", "cardio_warmup_minutes": 5,
             "cardio_duration_minutes": 30, "cardio_target_zone": 2, "cardio_cooldown_minutes": 5},
        ],
    },
    {
        "name": "cardio HIIT: séries x (alta + baixa) em segundos + aquecimento e recuperação",
        "items": [
            {"item_kind": "cardio", "cardio_mode": "hiit", "cardio_warmup_minutes": 10, "hiit_low_seconds": 90,
             "hiit_high_seconds": 30, "hiit_low_zone": 2, "hiit_high_zone": 4, "hiit_rounds": 8,
             "cardio_cooldown_minutes": 5, "cardio_duration_minutes": 99},
        ],
    },
    {
        "name": "cardio sem modo é Normal; modo em maiúsculas e modo desconhecido",
        "items": [
            {"item_kind": "cardio", "cardio_duration_minutes": 12.5, "cardio_cooldown_minutes": None},
            {"item_kind": "cardio", "cardio_mode": " HIIT ", "hiit_low_seconds": 45, "hiit_high_seconds": 15,
             "hiit_rounds": 3},
            {"item_kind": "cardio", "cardio_mode": "intervalado", "cardio_duration_minutes": 10,
             "hiit_rounds": 4, "hiit_high_seconds": 30},
        ],
    },
    {
        "name": "cardio HIIT incompleto e valores negativos contam 0",
        "items": [
            {"item_kind": "cardio", "cardio_mode": "hiit", "hiit_high_seconds": 20, "hiit_rounds": None,
             "cardio_warmup_minutes": -5, "cardio_cooldown_minutes": 2},
            {"item_kind": "cardio", "cardio_mode": "hiit", "hiit_low_seconds": -10, "hiit_high_seconds": 20,
             "hiit_rounds": 2.5},
        ],
    },
)


def casos_tempo() -> dict[str, Any]:
    lista = []
    for caso in (*_CASOS_TEMPO, *_CASOS_TEMPO_MEDIDA, *_CASOS_TEMPO_CARDIO):
        args = (caso["default_rest_seconds"],) if "default_rest_seconds" in caso else ()
        lista.append({**caso, "expected": dict(te.estimate_slot_time(caso["items"], *args))})
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote1b_espelhos_fe.py (--gravar): resultado COMPLETO do dono "
            "(api/app/periodization/time_estimate.py::estimate_slot_time). O vitest roda o espelho TS "
            "(template-time-estimate.ts) contra os mesmos casos. 'default_rest_seconds' ausente = o que a tela "
            "mostra (descanso não definido conta 0 s, Fase 7); 90 = a conta do complemento de volume (M46). "
            "Não edite à mão."
        ),
        "cases": lista,
    }


# ---------------------------------------------------------------------------
# Intensidade × Densidade (E13, 2026-10-13)
# ---------------------------------------------------------------------------

# `failure_requirement` do seed da migração 031 (o mesmo do
# `FALLBACK_ADVANCED_TECHNIQUES` do FE); só o campo que a regra lê.
_CATALOGO_FALHA: tuple[dict[str, str], ...] = (
    {"code": "rest_pause", "failure_requirement": "requires_failure"},
    {"code": "drop_set", "failure_requirement": "requires_failure"},
    {"code": "fst7", "failure_requirement": "requires_failure"},
    {"code": "gvt", "failure_requirement": "failure_not_required"},
    {"code": "forced_reps", "failure_requirement": "beyond_failure"},
    {"code": "negative_reps", "failure_requirement": "beyond_failure"},
    {"code": "partial_reps", "failure_requirement": "beyond_failure"},
    {"code": "cheat_reps", "failure_requirement": "beyond_failure"},
)

_RAMPAS: tuple[Any, ...] = ("8-9-10", "9", "8,5", 9, 8.5, "", None, "8-8.5-9-9.5-10", "RPE 9", "sem número")

# Dimensões do item; os casos gerados combinam cada uma pelo índice (módulos
# diferentes cobrem as combinações sem o produto cartesiano inteiro).
_SERIES = (None, 2, 3, 4, 5, 6, 1)
_RPES = (None, "7", "8", "8-9", "8-9-10", "9,5", "10", "6-7")
_REPS = (None, "6", "8-12", "15", "20", "10", "3")
_DESCANSOS = (None, 30, 45, 112.5, 180, 240, 0, 90)
_PICOS = (None, 0, 2.5, 5, 7)
_TECNICAS = (None, "drop_set", "gvt", "forced_reps", "fst7", "codigo_fora_do_catalogo")
_ITENS_GERADOS = 84


def _item(sets=None, rpe=None, reps=None, rest=None, peak=None, technique=None, measure=None) -> dict[str, Any]:
    item = {
        "sets_count": sets,
        "reps_target": reps,
        "rpe_target": rpe,
        "rest_seconds": rest,
        "peak_contraction_seconds": peak,
        "advanced_technique": technique,
    }
    # Medida da série (Lote T2): só nos casos novos - os antigos ficam como eram.
    if measure is not None:
        item["measure"] = measure
    return item


_ITENS_NOMEADOS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("vazio", _item()),
    ("pesado até a falha", _item(2, "10", "6", 180, 0)),
    ("leve e denso", _item(5, "7", "15", 45, 5)),
    ("rampa curta repete o último degrau", _item(4, "8-9")),
    ("técnica de falha: última série RPE 10", _item(3, "8", technique="drop_set")),
    ("além da falha também força a última série", _item(3, "8", technique="forced_reps")),
    ("GVT não exige falha", _item(3, "8", technique="gvt")),
    ("sem séries: tamanho da rampa", _item(None, "8-9-10")),
    ("só densidade: p = 1 − D", _item(3, None, "15")),
    ("descanso no meio da escala", _item(rest=112.5)),
    ("séries fracionárias arredondam meio para cima", _item(2.5, "8-9-10")),
    ("faixa de reps por extenso", _item(3, "9", "8 a 12", 90, 1)),
    ("perto do equilíbrio", _item(2, "9", "8-12", 120, 2)),
    # Lote T2: item fora de `reps` não usa o alvo de reps (subíndice ausente).
    ("por tempo: sem subíndice de reps", _item(3, "8", "15", 45, 0, measure="duration")),
    ("máximo de reps: sem subíndice de reps", _item(3, "9", "12", 60, None, measure="max_reps")),
    ("medida reps explícita: igual a ausente", _item(3, "9", "12", 60, None, measure="reps")),
    ("medida desconhecida lê como reps", _item(3, "9", "12", 60, None, measure="minutos")),
)


def _itens_gerados() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            f"gerado {i}",
            _item(
                _SERIES[i % len(_SERIES)],
                _RPES[i % len(_RPES)],
                _REPS[i % len(_REPS)],
                _DESCANSOS[i % len(_DESCANSOS)],
                _PICOS[i % len(_PICOS)],
                _TECNICAS[i % len(_TECNICAS)],
            ),
        )
        for i in range(_ITENS_GERADOS)
    ]


def _contrib(grupo: str, papel: str, valor: float, grupo_id: Any = None) -> dict[str, Any]:
    return {"muscle_group_id": grupo_id, "muscle_group": grupo, "role": papel, "value": valor}


_GRUPOS: tuple[dict[str, Any], ...] = (
    {
        "name": "pondera por value (secundária pesa menos) e ignora estabilizador",
        "entries": [
            {
                "item": _item(2, "10", "6", 180, 0),
                "contributions": [
                    _contrib("Peitoral", "primary", 14, 1),
                    _contrib("Tríceps", "secondary", 7, 2),
                    _contrib("Core", "stabilizer", 3, 3),
                ],
            },
            {"item": _item(5, "7", "15", 45, 5), "contributions": [_contrib("Tríceps", "primary", 21, 2)]},
        ],
    },
    {
        "name": "grupo sem insumo fica sem dados",
        "entries": [{"item": _item(), "contributions": [_contrib("Costas", "primary", 3, 5)]}],
    },
    {
        "name": "peso zero ou negativo não entra; subíndice ausente não dilui",
        "entries": [
            {"item": _item(3, "9", "8-12"), "contributions": [_contrib("Ombro", "primary", 9)]},
            {"item": _item(4, None, None, 60, 3), "contributions": [_contrib("Ombro", "secondary", 4.5)]},
            {"item": _item(4, "10", "5", 200, 0), "contributions": [_contrib("Ombro", "primary", 0)]},
            {"item": _item(4, "10", "5", 200, 0), "contributions": [_contrib("Bíceps", "primary", -2)]},
        ],
    },
    {
        "name": "vários grupos com técnica e rampa",
        "entries": [
            {
                "item": _item(3, "8-9-10", "10-12", 90, 1, "rest_pause"),
                "contributions": [_contrib("Quadríceps", "primary", 21, 10), _contrib("Glúteos", "secondary", 10.5, 11)],
            },
            {
                "item": _item(4, "8", "15-20", 60, 0),
                "contributions": [_contrib("Quadríceps", "primary", 28, 10), _contrib("Panturrilha", "stabilizer", 7, 12)],
            },
            {
                "item": _item(3, "9", "8", 150, 2, "gvt"),
                "contributions": [_contrib("Glúteos", "primary", 12, 11), _contrib("Posterior", "secondary", 6, 13)],
            },
        ],
    },
    {
        # Desempate por nome = `localeCompare(..., 'pt-BR')` do TS: letra antes de
        # acento ("Abdômen" < "Abdutores Quadril", o codepoint de "ô" invertia),
        # acento antes de caixa e minúscula antes de maiúscula ("core" < "Core").
        "name": "empate de peso ordena o nome como pt-BR (acento, caixa)",
        "entries": [
            {
                "item": _item(3, "9", "8-12", 90, 1),
                "contributions": [
                    _contrib("Abdutores Quadril", "primary", 6, 20),
                    _contrib("Core", "primary", 6, 21),
                    _contrib("Abdômen", "primary", 6, 22),
                    _contrib("core", "primary", 6, 23),
                    _contrib("Abdomen", "primary", 6, 24),
                    _contrib("Pã", "primary", 6, 25),
                    _contrib("Pâ", "primary", 6, 26),
                    _contrib("Pà", "primary", 6, 27),
                    _contrib("Pá", "primary", 6, 28),
                    _contrib("Pä", "primary", 6, 29),
                ],
            },
        ],
    },
)


def casos_intensidade() -> dict[str, Any]:
    catalogo = [dict(linha) for linha in _CATALOGO_FALHA]
    itens = []
    for nome, item in (*_ITENS_NOMEADOS, *_itens_gerados()):
        calculado = idd.compute_item(item, catalogo)
        itens.append(
            {"name": nome, "item": item, "expected": {**calculado, "direction": idd.direction_of(calculado["position"])}}
        )
    grupos = []
    for caso in _GRUPOS:
        entradas = [(e["item"], e["contributions"]) for e in caso["entries"]]
        grupos.append({**caso, "expected": idd.aggregate_by_group(entradas, catalogo)})
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote1b_espelhos_fe.py (--gravar): resultado do dono "
            "(api/app/domain/intensity_density.py; rampa por training_history.parse_rpe_ramp, reps por "
            "periodization/time_estimate.parse_reps_target). O vitest roda o espelho TS (intensity-density.ts) "
            "contra os mesmos casos. Não edite à mão."
        ),
        "catalog": catalogo,
        "ramps": [{"input": rampa, "expected": idd.rpe_ramp(rampa)} for rampa in _RAMPAS],
        "items": itens,
        "groups": grupos,
    }


# ---------------------------------------------------------------------------
# Contabilização de técnica (2026-10-13)
# ---------------------------------------------------------------------------

# Catálogo como a migração 060 o deixa (o de test_technique_accounting.py, mais
# cheat_reps e uma técnica nova sem regra por reps) e uma linha não calibrada.
_CATALOGOS_TECNICA: dict[str, dict[str, dict[str, Any]]] = {
    "migracao_060": {
        "drop_set": {"activation_set_equivalent": 0.50, "max_equivalent_per_anchor": 1.00, "set_type": "drop_set"},
        "rest_pause": {"activation_set_equivalent": 0.40, "max_equivalent_per_anchor": 1.00, "set_type": "rest_pause"},
        "forced_reps": {"activation_set_equivalent": 0.25, "max_equivalent_per_anchor": 0.50, "set_type": "forced_rep"},
        "negative_reps": {"activation_set_equivalent": 0.25, "max_equivalent_per_anchor": 0.50, "set_type": "forced_rep"},
        "partial_reps": {"activation_set_equivalent": 0.15, "max_equivalent_per_anchor": 0.50, "set_type": "forced_rep"},
        "cheat_reps": {"activation_set_equivalent": 0.15, "max_equivalent_per_anchor": 0.50, "set_type": "forced_rep"},
        "fst7": {"activation_set_equivalent": 0, "max_equivalent_per_anchor": 0, "default_sets_count": 7, "set_type": "working"},
        "gvt": {"activation_set_equivalent": 0, "max_equivalent_per_anchor": 0, "default_sets_count": 10, "set_type": "working"},
        "tecnica_nova": {"activation_set_equivalent": 0.3, "max_equivalent_per_anchor": 0.6, "set_type": "working"},
    },
    "nao_calibrado": {"rest_pause": {"activation_set_equivalent": 0.4, "max_equivalent_per_anchor": None}},
}

_RPES_ESFORCO: tuple[Any, ...] = (None, 5, 6.5, 7, 7.25, 8, 8.5, 8.75, 9, 9.5, 10, 11)
_RPES_REFERENCIA: tuple[Any, ...] = (None, 7, 8, 9, 10)


def _app(code: Any, reps: Any = None, rpe: Any = None) -> dict[str, Any]:
    return {"technique_code": code, "reps": reps, "rpe": rpe}


def _apps(dados: list[dict[str, Any]]) -> list[Any]:
    return [ta.TechniqueApplication(d["technique_code"], reps=d["reps"], rpe=d["rpe"]) for d in dados]


_RP = "rest_pause"

_BLOCOS: tuple[dict[str, Any], ...] = (
    {"name": "6 reps de uma série de 12 = meia série", "applications": [_app(_RP, 6, 10)], "anchor_reps": 12, "anchor_rpe": 10},
    {"name": "dois mini-blocos de 6 = uma série", "applications": [_app(_RP, 6, 10)] * 2, "anchor_reps": 12, "anchor_rpe": 10},
    {
        "name": "6+4+3 = 1,08, o teto reduz a 1,0",
        "applications": [_app(_RP, 6, 10), _app(_RP, 4, 10), _app(_RP, 3, 10)],
        "anchor_reps": 12,
        "anchor_rpe": 10,
    },
    {"name": "RPE baixo pondera pelos pontos de esforço", "applications": [_app(_RP, 6, 8)], "anchor_reps": 12, "anchor_rpe": 10},
    {"name": "RPE >= 9 vale esforço cheio mesmo abaixo da âncora", "applications": [_app(_RP, 6, 9)], "anchor_reps": 12, "anchor_rpe": 10},
    {"name": "sem âncora: topo da faixa prescrita", "applications": [_app(_RP, 6, 10)], "reps_target": "10-12"},
    {
        "name": "âncora sem reps: topo da faixa prescrita",
        "applications": [_app(_RP, 3, 10)],
        "anchor_reps": 0,
        "anchor_rpe": 10,
        "reps_target": "6",
    },
    {"name": "reps forçadas: extras / ref × 1,0", "applications": [_app("forced_reps", 3)], "anchor_reps": 12, "anchor_rpe": 10},
    {"name": "negativas: extras / ref × 1,0", "applications": [_app("negative_reps", 2)], "anchor_reps": 10, "anchor_rpe": 10},
    {"name": "parciais: peso 0,5 e teto 0,5", "applications": [_app("partial_reps", 12)], "anchor_reps": 12, "anchor_rpe": 10},
    {"name": "cheat: peso 0,5", "applications": [_app("cheat_reps", 4)], "anchor_reps": 10, "anchor_rpe": 9},
    {"name": "sem reps: coeficiente do catálogo × aplicações", "applications": [_app("drop_set")] * 2, "anchor_reps": 12, "anchor_rpe": 10},
    {"name": "sem referência nenhuma: coeficiente do catálogo", "applications": [_app(_RP, 6, 10)]},
    {"name": "FST-7 não soma equivalente", "applications": [_app("fst7", 10)], "anchor_reps": 10, "anchor_rpe": 10},
    {"name": "GVT não soma equivalente", "applications": [_app("gvt", 10)], "anchor_reps": 10, "anchor_rpe": 10},
    {"name": "código fora do catálogo vale 0", "applications": [_app("myo_reps", 6)], "anchor_reps": 12, "anchor_rpe": 10},
    {"name": "técnica nova sem regra por reps: coeficiente", "applications": [_app("tecnica_nova", 8)], "anchor_reps": 12, "anchor_rpe": 10},
    {
        "name": "teto é por técnica na mesma âncora",
        "applications": [_app(_RP, 6, 10)] * 3 + [_app("forced_reps", 12)],
        "anchor_reps": 12,
        "anchor_rpe": 10,
    },
    {"name": "âncora sem RPE usa a referência padrão", "applications": [_app(_RP, 6, 8)], "anchor_reps": 12},
    {"name": "aplicação sem RPE = esforço cheio", "applications": [_app(_RP, 6)], "anchor_reps": 12, "anchor_rpe": 8},
    {
        "name": "drop com RPE fracionário",
        "applications": [_app("drop_set", 8, 7.5), _app("drop_set", 5, 8.5)],
        "anchor_reps": 10,
        "anchor_rpe": 9.5,
    },
    {"name": "reps negativas não descontam", "applications": [_app(_RP, -3, 10)], "anchor_reps": 12, "anchor_rpe": 10},
    {"name": "âncora fracionária trunca", "applications": [_app(_RP, 5, 10)], "anchor_reps": 10.7, "anchor_rpe": 10},
    {"name": "faixa por extenso", "applications": [_app(_RP, 4, 10)], "reps_target": "8 a 12"},
    {
        "name": "linha não calibrada usa teto padrão de uma série",
        "catalog": "nao_calibrado",
        "applications": [_app(_RP, 12)] * 2,
        "anchor_reps": 12,
    },
)

_PLANEJADOS: tuple[tuple[Any, Any], ...] = (
    (_RP, 2), (_RP, 3), ("drop_set", 1), ("forced_reps", 4), ("partial_reps", 2), ("fst7", 7), ("gvt", 10),
    (None, 2), (_RP, 0), (_RP, -1), (_RP, "2"), ("myo_reps", 2), ("tecnica_nova", 1), ("tecnica_nova", 5), (_RP, 1.5),
)

_REFERENCIAS: tuple[tuple[Any, Any], ...] = (
    (12, "8-10"), (None, "8-10"), (None, None), (0, "12"), (None, "8 a 12"), (10.9, None), ("9", None), (None, "7,5"),
)

_APLICACAO_CODIGOS = ("drop_set", _RP, "forced_reps", "partial_reps", "fst7", "tecnica_nova", "myo_reps")
_APLICACAO_REPS_RPE: tuple[tuple[Any, Any], ...] = ((None, None), (6, None), (6, 8), (10, 9.5), (0, 7))
_APLICACAO_REFERENCIAS: tuple[tuple[Any, Any], ...] = ((12, 10), (None, None), (8, 8.5))


def casos_tecnica() -> dict[str, Any]:
    catalogo = _CATALOGOS_TECNICA["migracao_060"]
    aplicacoes = []
    for code in _APLICACAO_CODIGOS:
        for reps, rpe in _APLICACAO_REPS_RPE:
            for ref_reps, anchor_rpe in _APLICACAO_REFERENCIAS:
                app = _app(code, reps, rpe)
                valor = ta.application_equivalent(_apps([app])[0], ref_reps=ref_reps, anchor_rpe=anchor_rpe, catalog=catalogo)
                aplicacoes.append({"application": app, "ref_reps": ref_reps, "anchor_rpe": anchor_rpe, "expected": valor})
    blocos = []
    for caso in _BLOCOS:
        nome_catalogo = caso.get("catalog", "migracao_060")
        bloco = ta.realized_block_equivalent(
            _apps(caso["applications"]),
            anchor_reps=caso.get("anchor_reps"),
            anchor_rpe=caso.get("anchor_rpe"),
            reps_target=caso.get("reps_target"),
            catalog=_CATALOGOS_TECNICA[nome_catalogo],
        )
        blocos.append(
            {
                "name": caso["name"],
                "catalog": nome_catalogo,
                "applications": caso["applications"],
                "anchor_reps": caso.get("anchor_reps"),
                "anchor_rpe": caso.get("anchor_rpe"),
                "reps_target": caso.get("reps_target"),
                "expected": {
                    "total": bloco.total,
                    "by_technique": dict(bloco.by_technique),
                    "raw_by_technique": dict(bloco.raw_by_technique),
                    "ref_reps": bloco.ref_reps,
                    "capped": bloco.capped,
                },
            }
        )
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote1b_espelhos_fe.py (--gravar): resultado do dono "
            "(api/app/domain/technique_accounting.py; pontos de esforço de MuscleRecoveryService). O vitest roda "
            "o espelho TS (technique-accounting.ts) contra os mesmos casos. Não edite à mão."
        ),
        "catalogs": _CATALOGOS_TECNICA,
        "effort_factor": [
            {"rpe_application": app, "rpe_reference": ref, "expected": ta.effort_factor(app, ref)}
            for app in _RPES_ESFORCO
            for ref in _RPES_REFERENCIA
        ],
        "application_equivalent": aplicacoes,
        "realized_block_equivalent": blocos,
        "planned_equivalent": [
            {"technique_code": code, "applications": n, "expected": ta.planned_equivalent(code, n, catalogo)}
            for code, n in _PLANEJADOS
        ],
        "reference_reps": [
            {"anchor_reps": reps, "reps_target": alvo, "expected": ta.reference_reps(reps, alvo)}
            for reps, alvo in _REFERENCIAS
        ],
    }


# M98 C1: itens de cardio do editor do Modelo (Normal, HIIT, antigo, padrões,
# incompletos, zona ausente, frações e modo desconhecido).
_ITENS_CARDIO: tuple[tuple[str, dict[str, Any]], ...] = (
    ("antigo: só a duração", {"cardio_duration_minutes": 20}),
    ("padrão Normal do dono", dict(cp.DEFAULTS[cp.CardioMode.CONTINUOUS], cardio_mode="continuous")),
    ("padrão HIIT do dono", dict(cp.DEFAULTS[cp.CardioMode.HIIT], cardio_mode="hiit")),
    ("HIIT do exemplo do plano", {"cardio_mode": "hiit", "cardio_warmup_minutes": 5, "hiit_rounds": 8,
                                  "hiit_high_seconds": 30, "hiit_high_zone": 4, "hiit_low_seconds": 90,
                                  "hiit_low_zone": 2, "cardio_cooldown_minutes": 5}),
    ("Normal só com aquecimento", {"cardio_mode": "continuous", "cardio_warmup_minutes": 10,
                                   "cardio_duration_minutes": 30, "cardio_target_zone": 3}),
    ("Normal só com recuperação e sem zona", {"cardio_mode": "continuous", "cardio_duration_minutes": 25,
                                              "cardio_cooldown_minutes": 7}),
    ("HIIT sem zonas", {"cardio_mode": "hiit", "hiit_rounds": 6, "hiit_high_seconds": 40, "hiit_low_seconds": 20}),
    ("HIIT incompleto", {"cardio_mode": "hiit", "hiit_high_seconds": 20, "cardio_warmup_minutes": 3}),
    ("Normal vazio", {"cardio_mode": "continuous"}),
    ("frações e negativos", {"cardio_duration_minutes": 12.5, "cardio_warmup_minutes": -5,
                             "cardio_cooldown_minutes": 2.5, "cardio_target_zone": 9}),
    ("modo em maiúsculas e sobras do outro modo", {"cardio_mode": " HIIT ", "cardio_duration_minutes": 99,
                                                   "cardio_target_zone": 2, "hiit_rounds": 4,
                                                   "hiit_high_seconds": 15, "hiit_high_zone": 5,
                                                   "hiit_low_seconds": 45, "hiit_low_zone": 1}),
    ("modo desconhecido é Normal", {"cardio_mode": "fartlek", "cardio_duration_minutes": 15,
                                    "cardio_target_zone": 2}),
)


def casos_cardio() -> dict[str, Any]:
    return {
        "_comment": (
            "GERADO por api/tests/unit/test_m76_lote1b_espelhos_fe.py (--gravar): tempo total, texto canônico e "
            "resumo curto calculados pelo dono api/app/domain/cardio_prescription.py. NÃO EDITAR À MÃO."
        ),
        "items": [
            {
                "name": nome,
                "item": item,
                "expected": {
                    "mode": cp.mode_of(item).value,
                    "total_seconds": cp.total_seconds(item),
                    "text": cp.describe(item),
                    "short_text": cp.describe_short(item),
                },
            }
            for nome, item in _ITENS_CARDIO
        ],
    }


def _ler(caminho: Path) -> Any:
    return json.loads(caminho.read_text(encoding="utf-8"))


def _gravar(caminho: Path, dados: Any) -> None:
    # LF sempre: no Windows o modo texto gravaria CRLF e sujaria o diff.
    with caminho.open("w", encoding="utf-8", newline="\n") as arquivo:
        arquivo.write(json.dumps(dados, ensure_ascii=False, indent=2) + "\n")


def test_constantes_do_fe_sao_as_do_dono():
    assert _ler(CONSTANTES) == constantes(), (
        f"{CONSTANTES.name} divergiu do dono: rode `python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`"
    )


def test_casos_de_paridade_sao_os_do_dono():
    assert _ler(CASOS) == casos(), (
        f"{CASOS.name} divergiu do dono: rode `python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`"
    )


def test_casos_de_tempo_sao_os_do_dono():
    assert _ler(CASOS_TEMPO) == casos_tempo(), (
        f"{CASOS_TEMPO.name} divergiu do dono: rode `python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`"
    )


def test_casos_de_intensidade_densidade_sao_os_do_dono():
    assert _ler(CASOS_INTENSIDADE) == casos_intensidade(), (
        f"{CASOS_INTENSIDADE.name} divergiu do dono: rode `python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`"
    )


def test_casos_de_tecnica_sao_os_do_dono():
    assert _ler(CASOS_TECNICA) == casos_tecnica(), (
        f"{CASOS_TECNICA.name} divergiu do dono: rode `python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`"
    )


def test_casos_de_medidas_de_serie_sao_os_do_dono():
    assert _ler(CASOS_MEDIDAS) == casos_medidas(), (
        f"{CASOS_MEDIDAS.name} divergiu do dono: rode `python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`"
    )


def test_casos_de_cardio_sao_os_do_dono():
    assert _ler(CASOS_CARDIO) == casos_cardio(), (
        f"{CASOS_CARDIO.name} divergiu do dono: rode `python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`"
    )


def test_resumo_curto_do_cardio_e_o_do_plano():
    """M98 C1: o exemplo do pedido ("HIIT 8× 30 s Z4 / 90 s Z2 · 5+5 min") e o item antigo."""
    curtos = {c["name"]: c["expected"]["short_text"] for c in casos_cardio()["items"]}
    assert curtos["HIIT do exemplo do plano"] == "HIIT 8× 30 s Z4 / 90 s Z2 · 5+5 min"
    assert curtos["antigo: só a duração"] == "Normal 20 min"


def test_bloco_de_medidas_de_serie_e_o_do_dono():
    """T1: o FE recebe o vocabulário com `rep_based` por medida e os limites nomeados; nada é escrito à mão."""
    bloco = constantes()["set_measures"]
    assert [m["value"] for m in bloco["measures"]] == [m.value for m in smeas.SetMeasure]
    assert {m["value"]: m["rep_based"] for m in bloco["measures"]} == {m.value: smeas.is_rep_based(m) for m in smeas.SetMeasure}
    assert bloco["default_measure"] == smeas.DEFAULT_MEASURE.value
    assert bloco["limits"]["duration_seconds_max"] == smeas.DURATION_SECONDS_MAX
    assert bloco["reason_labels"][bloco["reason_not_rep_based"]] == smeas.MEASURE_REASON_LABELS[smeas.REASON_MEASURE_NOT_REP_BASED]
    textos = {(c["measure"], c["reps"], c["duration_seconds"]): c["text"] for c in casos_medidas()["format"]}
    assert textos[("max_reps", 15, 30)] == "15 reps em 30 s"
    assert textos[("duration", None, 60)] == "60 s"
    assert textos[("tempo", 9, 45)] == "9 reps"  # desconhecido lê como reps


def test_intensidade_densidade_nao_mudou_de_semantica():
    """Os números que o TS (então dono, E13) dava nos testes antigos: a troca de
    dono (TS -> backend) não muda nenhum resultado."""
    itens = {c["name"]: c["expected"] for c in casos_intensidade()["items"]}
    assert itens["pesado até a falha"]["position"] == 1
    assert itens["leve e denso"]["position"] == 0
    assert abs(itens["rampa curta repete o último degrau"]["intensity"] - (0.3 + 0.7 * 3) / 4) < 1e-9
    assert abs(itens["técnica de falha: última série RPE 10"]["intensity"] - (0.3 * 2 + 1) / 3) < 1e-9
    assert abs(itens["GVT não exige falha"]["intensity"] - 0.3) < 1e-9
    assert itens["descanso no meio da escala"]["subindices"]["rest"] == 0.5
    assert itens["vazio"]["position"] is None and itens["vazio"]["direction"] is None
    grupos = {c["name"]: c["expected"] for c in casos_intensidade()["groups"]}
    linhas = grupos["pondera por value (secundária pesa menos) e ignora estabilizador"]
    assert [r["muscle_group"] for r in linhas] == ["Tríceps", "Peitoral"]
    assert linhas[0]["weight"] == 28 and abs(linhas[0]["position"] - 0.25) < 1e-9
    assert linhas[0]["direction"] == "density" and linhas[1]["direction"] == "intensity"
    assert grupos["grupo sem insumo fica sem dados"][0]["position"] is None


def test_tecnica_nao_mudou_de_semantica():
    """Os totais dos casos que o TS e o Python duplicavam à mão."""
    blocos = {c["name"]: c["expected"] for c in casos_tecnica()["realized_block_equivalent"]}
    assert abs(blocos["RPE baixo pondera pelos pontos de esforço"]["total"] - 0.5 * 0.6 / 1.05) < 1e-9
    assert blocos["6+4+3 = 1,08, o teto reduz a 1,0"]["total"] == 1.0
    assert blocos["teto é por técnica na mesma âncora"]["by_technique"] == {"rest_pause": 1.0, "forced_reps": 0.5}
    assert blocos["teto é por técnica na mesma âncora"]["capped"] is True
    assert blocos["linha não calibrada usa teto padrão de uma série"]["total"] == 1.0


def test_categorias_da_ia_sao_acoes_do_motor_com_o_mesmo_rotulo():
    """RPE 10 Lote 6/P9: toda categoria da IA por série é um código de ação do
    motor e o rótulo é o de `DECISION_LABELS` (uma voz só na tela)."""
    bloco = constantes()["set_evaluation"]
    assert bloco["categories"] == ["HOLD", "INCREASE_LOAD", "DECREASE_LOAD"]
    for code in bloco["categories"]:
        assert bloco["category_labels"][code] == DECISION_LABELS[code]
    assert set(bloco["category_directions"]) == set(bloco["categories"])


def test_carga_da_proxima_serie_exporta_motivos_e_rotulos_do_dono():
    """Regra da carga da próxima série (2026-10-12): o FE mostra o motivo pelo
    código, com o rótulo do dono (limiares interpolados das constantes)."""
    bloco = constantes()["next_set_load"]
    assert bloco["reasons"] == list(nsl.NEXT_SET_REASONS)
    assert bloco["reason_labels"] == dict(nsl.reason_labels())
    assert bloco["load_adjustment_reason_rule"] == nsl.REASON_NEXT_SET_RULE
    assert str(nsl.NEXT_SET_INCREASE_ABOVE_REPS) in bloco["reason_labels"][nsl.REASON_REPS_ABOVE_LIMIT]


def test_medidas_de_serie_no_tempo_e_na_intensidade():
    """Lote T2: tempo por série no lugar da execução por reps; janela do
    máximo; sem janela, as reps estimadas; subíndice de reps ausente fora de
    `reps`. Os números são os do dono (aqui só a forma da regra)."""
    tempo = {c["name"]: c["expected"] for c in casos_tempo()["cases"]}
    prancha = tempo["prancha: 3 séries de 60 s com 30 s de descanso"]
    assert prancha["breakdown"]["exec_seconds"] == 3 * 60 and prancha["total_seconds"] == 3 * (60 + 30)
    janela = tempo["máximo de reps com janela de 30 s usa a janela"]
    assert janela["breakdown"]["exec_seconds"] == 2 * 30 and janela["has_estimated_reps"] is False
    assert tempo["máximo de reps sem janela estima as repetições"]["has_estimated_reps"] is True
    itens = {c["name"]: c["expected"] for c in casos_intensidade()["items"]}
    assert itens["por tempo: sem subíndice de reps"]["subindices"]["reps"] is None
    assert itens["máximo de reps: sem subíndice de reps"]["subindices"]["reps"] is None
    sem_medida = idd.compute_item(_item(3, "9", "12", 60), [dict(linha) for linha in _CATALOGO_FALHA])
    explicita = {k: v for k, v in itens["medida reps explícita: igual a ausente"].items() if k != "direction"}
    assert explicita == sem_medida
    execucao = {
        (c["measure"], c["duration_seconds_target"], c["time_window_seconds"]): c["seconds"]
        for c in casos_medidas()["planned_execution"]
    }
    assert execucao[("duration", 60, None)] == 60 and execucao[("max_reps", None, 30)] == 30
    assert execucao[("reps", None, None)] is None and execucao[("max_reps", None, None)] is None


def test_totais_de_tempo_da_fase_7_nao_mudaram():
    """Os totais que a fixture antiga (escrita à mão a partir do TS) trazia: a
    troca de dono (TS -> backend) não muda nenhum número."""
    totais = {c["name"]: c["expected"]["total_seconds"] for c in casos_tempo()["cases"]}
    assert totais["slot vazio"] == 0
    assert totais["rascunho da IA: faixa de reps, sem descanso"] == 120
    assert totais["descanso e pico de contração"] == 420
    assert totais["aquecimento, ajuste e faixa por extenso"] == 327
    assert totais["cardio e técnica com pausa"] == 1317
    assert totais["reps vazio vira 10; vírgula decimal; três números usam os dois primeiros"] == 113
    assert totais["arredondamento do Math.round (meio para cima)"] == 5
    assert totais["sem séries de trabalho: só o aquecimento conta"] == 75
    assert totais["complemento: descanso padrão de 90 s quando rest_seconds é nulo"] == 480


def test_totais_de_tempo_do_cardio_m98():
    """M98 C0: Normal = (aquec. + tempo + recuperação) x 60; HIIT = aquec. x 60
    + séries x (alta + baixa) + recuperação x 60; sem modo = Normal."""
    totais = {c["name"]: c["expected"]["total_seconds"] for c in casos_tempo()["cases"]}
    assert totais["cardio Normal com aquecimento, zona e recuperação"] == (5 + 30 + 5) * 60
    assert totais["cardio HIIT: séries x (alta + baixa) em segundos + aquecimento e recuperação"] == (
        10 * 60 + 8 * (30 + 90) + 5 * 60
    )
    assert totais["cardio sem modo é Normal; modo em maiúsculas e modo desconhecido"] == 750 + 180 + 600
    assert totais["cardio HIIT incompleto e valores negativos contam 0"] == 120 + 50


def test_casos_cobrem_meio_passo_e_passo_default():
    """Sanidade dos próprios casos: o meio-passo arredonda para cima e o passo
    nulo vale o default (são os dois pontos onde um espelho costuma errar)."""
    por_chave = {(c["anchor_kg"], c["load_increment_kg"]): c for c in casos()["cases"]}
    assert por_chave[(31.25, None)]["warmup_loads_kg"][0] == 7.5
    assert por_chave[(11.35, 2.27)]["feeler_loads_kg"][0] == 6.81
    assert por_chave[(100, None)]["drop_set_loads_kg"] == [80.0, 65.0, 50.0]
    # Repetição de carga não é arredondada (rest-pause repete a âncora).
    assert por_chave[(44.63, 2.5)]["rest_pause_loads_kg"] == [44.63, 44.63, 44.63]


def test_ajuste_rapido_do_plano_e_bordas():
    """Casos do plano do Modo Intensivo: 70 kg com passo 5 -> 65/65/60; nunca
    abaixo de 0; sempre na grade do passo; sempre abaixo da carga atual (>0)."""
    por_chave = {(c["current"], c["step"]): c["results"] for c in casos_ajuste_rapido()}
    assert por_chave[(70, 5)] == {"minus_step": 65.0, "minus_5pct": 65.0, "minus_10pct": 60.0}
    assert por_chave[(155, 5)] == {"minus_step": 150.0, "minus_5pct": 145.0, "minus_10pct": 135.0}
    assert por_chave[(44.63, 2.5)]["minus_step"] == 42.5
    assert por_chave[(2, 2.5)] == {"minus_step": 0.0, "minus_5pct": 0.0, "minus_10pct": 0.0}
    assert por_chave[(0, 2.5)] == {"minus_step": 0.0, "minus_5pct": 0.0, "minus_10pct": 0.0}
    for caso in casos_ajuste_rapido():
        passo = caso["step"] if caso["step"] is not None else float(sl.DEFAULT_LOAD_STEP_KG)
        for valor in caso["results"].values():
            assert valor >= 0
            assert abs(round(valor / passo) * passo - valor) < 1e-9
            if caso["current"] > 0:
                assert valor < caso["current"]


if __name__ == "__main__":
    if "--gravar" not in sys.argv:
        raise SystemExit("uso: python tests/unit/test_m76_lote1b_espelhos_fe.py --gravar")
    _gravar(CONSTANTES, constantes())
    _gravar(CASOS, casos())
    _gravar(CASOS_TEMPO, casos_tempo())
    _gravar(CASOS_INTENSIDADE, casos_intensidade())
    _gravar(CASOS_TECNICA, casos_tecnica())
    _gravar(CASOS_MEDIDAS, casos_medidas())
    _gravar(CASOS_CARDIO, casos_cardio())
    print(
        f"gravados: {CONSTANTES}, {CASOS}, {CASOS_TEMPO}, {CASOS_INTENSIDADE}, {CASOS_TECNICA}, {CASOS_MEDIDAS} "
        f"e {CASOS_CARDIO}"
    )
