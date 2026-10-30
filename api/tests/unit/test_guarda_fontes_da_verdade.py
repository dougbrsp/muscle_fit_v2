"""Guarda das fontes da verdade dos motores (M76, Lote 0).

Mapa completo (regra -> dono -> consumidores -> proibido fora do dono):
`docs/motores/FONTES_DA_VERDADE.md`.

O problema: a mesma regra de cálculo (Epley, RIR, dose reduzida, normalização
semanal, `trained_at`...) reescrita em vários arquivos, cada cópia derivando à
sua maneira. Ninguém percebe no dia em que a cópia nasce; o número só fica
diferente de uma tela para outra. Este teste torna a cópia nova barulhenta.

Como funciona (ratchet):
- Cada `Padrao` é uma regex que identifica a regra escrita "à mão", com o(s)
  arquivo(s) DONO(S) onde ela pode morar e o ID do inventário do M76.
- Varre `api/app/**/*.py` e `frontend/src/**/*.ts(x)` (sem testes), já sem
  comentários e docstrings (prosa explicando a regra não é cópia da regra).
  Strings continuam contando: SQL e prompt são justamente onde as cópias moram.
- Varre também `api/privado/rag/markdown/**/*.md` (texto injetado em prompt conta como
  prompt — princípio 4 do mapa), só para os padrões que declaram `md`.
- Varre também os templates de prompt `api/privado/prompts/**/*.md` (M103 P1): a
  prosa que antes era literal `.py` conta como o literal do módulo consumidor
  (`CONSUMIDOR_DO_PROMPT`): todo padrão de `py`, a mesma `regex`, o escopo do
  consumidor, sem isenção de dono.
- `ALLOWLIST` congela o estado de 2026-10-06 (arquivo -> nº de ocorrências).
  Falha se: (a) um arquivo fora da allowlist ganhar ocorrência; (b) a contagem
  de um arquivo SUBIR; (c) a contagem CAIR sem a allowlist ser reduzida junto
  (o diff da allowlist é a prova de cada lote do M76).

Por que só `api/app` e `frontend/src`: testes repetem números de propósito
(fixtures), e as migrações SQL são dados de seed/default de coluna versionados
(mudam por migração nova, nunca editando a antiga) - entram no mapa como
divergência, não na guarda. `node_modules`/`.next`/gerados nem são `src`.

Rodar: cd api && python -m pytest tests/unit/test_guarda_fontes_da_verdade.py
"""

from __future__ import annotations

import ast
import fnmatch
import io
import re
import tokenize
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[3]
DOC = "docs/motores/FONTES_DA_VERDADE.md"

_PASTAS_IGNORADAS = {"node_modules", ".next", "__pycache__", "__tests__"}
_TESTE_TS = re.compile(r"\.(test|spec)\.tsx?$")


# ---------------------------------------------------------------------------
# Padrões
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Padrao:
    chave: str
    regra: str
    regex: re.Pattern[str]
    donos: tuple[str, ...]  # caminhos relativos à raiz do repo (posix)
    inventario: str  # IDs do inventário do M76 (seções A-E do mapa)
    linguagens: frozenset[str] = frozenset({"py", "ts"})  # "md" = api/privado/rag/markdown
    # Prefixos (pasta com "/" no fim), arquivos ou globs com "*" (`fnmatch`,
    # para lotes que ainda vão criar os arquivos) que a guarda varre. None = tudo.
    escopo: tuple[str, ...] | None = None
    # Mesma sintaxe, tirando do escopo (ex.: a tela que só monta Selects com o
    # vocabulário do dono). Vence o `escopo`.
    excluir: tuple[str, ...] = ()
    dono_pendente: str = ""  # quando o dono ainda não existe: qual lote cria
    # Regex só para `.md` (RAG). Quando definida, substitui `regex` nos `.md`: o
    # texto corrido do RAG tem números que no código seriam regra (× 4, 9).
    regex_md: re.Pattern[str] | None = None


# Módulos de motor: só neles "agora" sem fuso é proibido (B10). Telas, auth,
# sync de integrações etc. usam relógio por outros motivos e ficam fora.
MODULOS_DE_MOTOR: tuple[str, ...] = (
    "api/app/domain/",
    "api/app/generation/",
    "api/app/periodization/",
    "api/app/analytics.py",
    "api/app/progression_state.py",
    "api/app/set_types.py",
    "api/app/workout_plan_templates_engine.py",
    "api/app/intelligence/exercise_performance.py",
    "api/app/intelligence/methodology_volume.py",
    "api/app/intelligence/methodology_volume_service.py",
    "api/app/intelligence/muscle_contribution_resolver.py",
    "api/app/intelligence/muscle_group_performance.py",
    "api/app/intelligence/muscle_group_progression.py",
    "api/app/intelligence/muscle_recovery_service.py",
    "api/app/intelligence/progression_activation.py",
    "api/app/intelligence/progression_service.py",
    "api/app/intelligence/recovery_projection.py",
    "api/app/intelligence/recovery_read_model.py",
    "api/app/intelligence/slot_session_comparison.py",
    "api/app/intelligence/technique_volume.py",
    "api/app/intelligence/template_volume_domain.py",
    "api/app/intelligence/template_volume_planner.py",
    "api/app/intelligence/template_volume_validator.py",
    "api/app/intelligence/training_load_service.py",
    "api/app/intelligence/volume_service.py",
    "api/app/intelligence/dashboard/adherence_service.py",
    "api/app/intelligence/dashboard/comparability.py",
    "api/app/intelligence/dashboard/engine_service.py",
    "api/app/intelligence/dashboard/exposure_service.py",
    "api/app/intelligence/dashboard/insight_rules.py",
    "api/app/intelligence/dashboard/metrics_registry.py",
    "api/app/intelligence/dashboard/muscle_summary_service.py",
    "api/app/intelligence/dashboard/recovery_signals_service.py",
    "api/app/intelligence/dashboard/session_records.py",
    # Nutrição L2b: materialização e rotas do plano decidem "hoje" (congelamento,
    # vigência, recálculo) só pelo fuso do aluno (`time_windows.user_today_for`).
    "api/app/nutrition_plans.py",
    "api/app/nutrition_targets_service.py",
    # Nutrição L3b: o limite diário do Open Food Facts conta o "hoje" do fuso do
    # usuário (`time_windows.user_day_start_db`), nunca o dia do servidor.
    "api/app/nutrition_external_foods.py",
    # Nutrição L4: o diário decide a meta do dia (congelamento, retroativa) pelo mesmo "hoje".
    "api/app/nutrition_days.py",
    # Nutrição L5a: a cota de IA conta as tentativas desde o início do dia do
    # usuário (`time_windows.user_day_start_db`), nunca o dia do servidor.
    "api/app/nutrition_ai_quota.py",
)

DONO_RECUPERACAO = "api/app/intelligence/muscle_recovery_service.py"
DONO_E1RM = "api/app/domain/e1rm.py"
DONO_PRESCRICAO = "api/app/domain/prescription_defaults.py"

# Nutrição (L1, 2026-10-16): o motor em `api/app/domain/nutrition/` e onde os
# lotes seguintes vão consumir (plano `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`
# §8, §10 e §7: rotas/serviços `api/app/nutrition_*.py`, dashboard, telas, o
# espelho `frontend/src/lib/nutrition-constants.ts` e o RAG próprio). Padrões
# com número genérico demais para o repo inteiro (1,55; × 4; 3 + 1) só valem aqui.
DONO_NUTRICAO = "api/app/domain/nutrition/"
ESCOPO_NUTRICAO: tuple[str, ...] = (
    DONO_NUTRICAO,
    "api/app/nutrition_*.py",
    "api/app/external_food_sources.py",  # USDA/OFF/TBCA (L3)
    "api/app/intelligence/dashboard/nutrition_service.py",
    "frontend/src/app/(app)/nutricao/",
    "frontend/src/app/(app)/dashboard/nutricao/",
    "frontend/src/components/nutrition/",
    "frontend/src/lib/nutrition-*",
    "api/privado/rag/markdown/NUTRICAO/",
)

# Rótulos pt-BR de sexo (dono: `domain/nutrition/labels.SEX_LABELS`; apelidos em
# `anthropometrics._SEX_ALIASES`), em qualquer caixa e com flexão de gênero.
_ROTULOS_SEXO = r"(?i:masculin[oa]|feminin[oa])"
# Campo comparado/atribuído a literal: `row.get("sex") == "M"`, `sex?: 'male'`,
# `WHERE sex = 'F'`, `sex <> 'F'`, `sex IN ('M', 'F')`, `sex in ("m", "f")`.
_COMPARA_LITERAL = r"""["'\])]*\??\s*(?:[!=]?==?|<>|:|\s(?i:not\s+)?(?i:in)\s*[(\[{]\s*)\s*["'`]"""

# Rótulos pt-BR dos locais de medida (dono: `domain/body_measurement_sites.py`).
# "Peitoral" fica de fora porque também é grupo muscular (ver `rotulo_site_medida`).
_ROTULOS_SITE = (
    r"(?:Cintura|Quadril|Pescoço|Pescoco|(?:Braço|Braco|Antebraço|Antebraco) (?:esquerdo|direito)"
    r"|(?:Coxa|Panturrilha) (?:esquerda|direita))"
)

PADROES: tuple[Padrao, ...] = (
    Padrao(
        chave="epley",
        regra="e1RM de Epley: carga × (1 + (reps + RIR) / 30)",
        regex=re.compile(r"/\s*30(?:\.0)?\b"),
        donos=(DONO_E1RM,),  # estimated_1rm/epley_multiplier (Lote 3a; MuscleRecoveryService só delega)
        inventario="A, C12",
    ),
    Padrao(
        chave="rir",
        regra="RIR = 10 − RPE",
        regex=re.compile(r"\b10(?:\.0)?\s*-\s*(?:float\(|Number\()?\s*[\w.\[\]'\"]*rpe\b|\b10\.0\s*-\s", re.I),
        donos=(DONO_E1RM,),  # rir_from_rpe (Lote 3a; MuscleRecoveryService.estimate_rir só delega)
        inventario="A, C12",
    ),
    Padrao(
        chave="dose_sem_rpe",
        regra="reps/RPE assumidos pela dose quando a série não registrou (8 reps / RPE 7,0)",
        # Lote 3a: `e1rm.dose_inputs` / `DOSE_RPE_WHEN_MISSING` (o SQL de
        # volume por split interpola a constante). O dono usa constantes
        # nomeadas: a regex pega só a cópia escrita à mão.
        regex=re.compile(
            r"""\b(?:reps|rpe)\w*\)?\s+if\s+[\w.\[\]'"()]+\s+is\s+not\s+None\s+else\s+(?:7\.0|8)\b"""
            r"|COALESCE\(\s*(?:\w+\.)?rpe\s*,\s*7(?:\.0)?\s*\)|\brpe\w*\s*(?:\?\?|\|\|)\s*7\b"
        ),
        donos=(DONO_E1RM,),
        inventario="A",
    ),
    Padrao(
        chave="dose_reduzida",
        regra="fator de dose reduzida 0,75 (REDUCED_DOSE)",
        regex=re.compile(r"(?<![\d.])0\.75\b"),
        donos=(DONO_RECUPERACAO,),  # _RECOMMENDATIONS["REDUCED_DOSE"]["volume_multiplier"]
        inventario="B2, C10",
    ),
    Padrao(
        chave="dose_reduzida_texto",
        regra='dose reduzida escrita à mão em texto/prompt ("1-2 séries diretas", "1-2 séries, RPE", "RPE máx. 8-9")',
        # Decisão 1 (2026-10-06): a dose reduzida é `ceil(S × 0,75)`, mín. 1,
        # e o texto sai de `MuscleRecoveryService.reduction_policy()` /
        # `reduced_dose_prompt_rule()`. "1 a 2 séries leves" de aquecimento
        # (RAG) é outra coisa e não casa.
        regex=re.compile(r"(?i)\b1\s?[-–]\s?2\s+s[ée]ries(?=\s+diretas|,\s*RPE)|RPE\s+m[áa]x\.?\s*8\s?[-–]\s?9"),
        donos=(DONO_RECUPERACAO,),
        inventario="B2",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="sinais_expiram",
        regra="sinais de desempenho com expiração própria na projeção (reset ≥72h à frente)",
        # Decisão 2 (2026-10-06): dor e sinais de desempenho persistem até a
        # próxima sessão do grupo, no retrato e na projeção
        # (`MuscleRecoveryService.SIGNALS_POLICY`). Proibição pura.
        regex=re.compile(r"\b(?:performance_signals_reset|PERFORMANCE_SIGNALS_RESET\w*)\b"),
        donos=(DONO_RECUPERACAO,),
        inventario="B3",
        linguagens=frozenset({"py", "ts"}),
    ),
    Padrao(
        chave="defaults_prescricao",
        regra='defaults de prescrição "8-12" (reps) e "8-9" (RPE) escritos à mão',
        regex=re.compile(r"""["'`]8-(?:12|9)["'`]"""),
        donos=(DONO_PRESCRICAO,),  # DEFAULT_REPS_TARGET / DEFAULT_RPE_TARGET (Lote 4c); FE via JSON
        inventario="E8",
    ),
    Padrao(
        chave="descanso_default",
        regra="descanso default à mão (assinatura `min/max_rest_seconds = 30/90`, `round((min + max) / 2)`, `DEFAULT_REST_SECONDS = 60`)",
        # Lote 4c (E8): `prescription_defaults` (DEFAULT_MIN/MAX_REST_SECONDS,
        # midpoint_rest_seconds, default_rest_seconds); FE lê
        # `motor-constants.generated.json` via `prescription-defaults.ts`.
        regex=re.compile(
            r"\b(?:min|max)_rest_seconds\s*(?::\s*int\s*)?=\s*\d+|round\(\(\s*\w*rest\w*\s*\+"
            r"|\bDEFAULT_REST_SECONDS\s*=\s*\d+|\b(?:min|max)Rest\w*\s*\?\?\s*\d+"
            r"|useState<number(?:\s*\|\s*null)?>\(\s*(?:30|90)\s*\)"
        ),
        donos=(DONO_PRESCRICAO,),
        inventario="E7, E8",
    ),
    Padrao(
        chave="tetos_sessao",
        regra="teto/piso de tamanho de sessão à mão (exercícios por slot/grupo/dia, séries por exercício, priorizados, tempo do slot)",
        # Lote 4c (E9): `periodization/limits.py` nomeia cada teto (os valores
        # divergem por motivo legítimo - geração × complemento × legado); quem
        # consome importa a constante. `MAX_EXERCISES_PER_STATUS` (lista de
        # tela) não é tamanho de sessão e não casa.
        regex=re.compile(
            r"\b(?:SESSION_EXERCISES_\w+|\w*MAX_EXERCISES_PER_(?:SLOT|SESSION|DAY|GROUP)\w*|\w*MAX_SETS_PER_EXERCISE\w*"
            r"|MAX_PRIORITIZED_PER_SLOT|SLOT_TIME_LIMIT_\w+|COMPLEMENT_\w+)\s*(?::[^=\n]+)?=\s*\d"
            r"|prioriti[sz]ed_?[cC]ount\s*>\s*\d"
        ),
        donos=("api/app/periodization/limits.py",),
        inventario="E9",
    ),
    Padrao(
        chave="premissas_tempo",
        regra="premissas do tempo de treino à mão (3 s por repetição, 10 reps estimadas, descanso não definido = 0 s)",
        # Lote 4c (E7): o dono é o backend; o espelho TS lê a seção
        # `time_estimate` de `motor-constants.generated.json`.
        regex=re.compile(
            r"\b(?:EXEC_SECONDS_PER_REP|DEFAULT_ESTIMATED_REPS|REST_WHEN_UNDEFINED_SECONDS)\s*(?::[^=\n]+)?=\s*\d"
        ),
        donos=("api/app/periodization/time_estimate.py",),
        inventario="E7",
    ),
    Padrao(
        chave="numeros_prompt_volume",
        regra='faixa de volume escrita à mão em prompt/RAG ("9 a 10 séries semanais", "entre 90 e 110", "pede 4-5 exercicios", "{alvo}-9")',
        # Lote 4c (E10): o prompt da Periodização interpola a TABELA da
        # metodologia (`methodology_ranges_table`), a cobertura do Modo A
        # (`template_volume_domain`) e os tetos de `limits.py`; o RAG remete à
        # meta semanal do macro-grupo. Proibição pura (o dono gera o texto).
        regex=re.compile(
            r"(?i)\b\d+\s?(?:a|-|–)\s?\d+\s+s[ée]ries\s+(?:diretas\s+)?(?:semanais|por\s+semana)"
            r"|\bentre\s+90\s+e\s+110\b|\bpede\s+\d+\s?-\s?\d+\s+exerc[ií]cios|\{ex_target_min\}-\d"
        ),
        donos=("api/app/periodization/context.py",),
        inventario="E10",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="limiar_fadiga",
        regra="limiares de queda de desempenho 0,92 / 0,85 (alerta de fadiga paralelo)",
        regex=re.compile(r"(?<![\d.])0\.(?:92|85)\b"),
        donos=(DONO_RECUPERACAO,),
        inventario="B4, D3",
        # Só Python: no FE 0,85 é opacidade de CSS e o espelho
        # `technique-accounting.ts` (fatores de técnica), sem relação com fadiga.
        linguagens=frozenset({"py"}),
    ),
    Padrao(
        chave="meta_fallback",
        regra="meta semanal de fallback 6/9/27 séries (detectada pelo teto 27)",
        regex=re.compile(r"(?<![\w.])27(?:\.0)?(?![\w.])"),
        donos=("api/app/intelligence/volume_service.py",),  # DASHBOARD_FALLBACK_*_SETS
        inventario="§4 Metas (fallback 6/9/27)",
        # 6.0 e 9.0 sozinhos são ruído (RPE 9.0, FULL_EFFORT_RPE, min_rpe...);
        # o 27 só existe como teto do fallback. Só Python: no FE 27 é coordenada.
        linguagens=frozenset({"py"}),
    ),
    Padrao(
        chave="peso_secundario",
        regra="peso do papel SECONDARY (0,5 / 0,3 / 0,25) escrito à mão",
        regex=re.compile(r"SECONDARY_FACTOR|(?i:secondary)\w*\b.*?(?<![\d.])0\.(?:50?|30?|25)\b"),
        donos=("api/app/intelligence/muscle_contribution_resolver.py",),  # DEFAULT_WRITE_WEIGHTS (Lote 3c)
        inventario="A (pesos por papel)",
    ),
    Padrao(
        chave="rotulo_progressao",
        regra='rótulo pt-BR de status de progressão por exercício escrito à mão ("Estável", "Suspeita de platô"...)',
        # Só o rótulo como literal de string (aspas dos dois lados): frase de
        # prosa que cita o status ("Suspeita de platô não é diagnóstico...")
        # não é cópia do rótulo. "Dados insuficientes" fica fora de propósito:
        # é rótulo genérico de vários domínios (volume, corpo). O FE lê de
        # `motor-constants.generated.json`.
        regex=re.compile(
            r"[\"'](?:Progredindo|Regredindo|Est[aá]vel|Suspeita de plat[oô]|N[aã]o compar[aá]vel)[\"']"
        ),
        donos=("api/app/domain/progression_status_labels.py",),  # EXERCISE_PROGRESSION_LABELS (Lote 3c)
        inventario="C13",
    ),
    Padrao(
        chave="trained_at",
        regra="trained_at = COALESCE(started_at, ended_at, created_at) reescrito em SQL",
        regex=re.compile(r"COALESCE\(\s*(?:\w+\.)?started_at\s*,\s*(?:\w+\.)?ended_at"),
        donos=("api/app/intelligence/dashboard/time_windows.py",),  # trained_at_sql() / with_trained_at() (Lote 2b)
        inventario="A (trained_at), B10",
    ),
    Padrao(
        chave="normalizacao_semanal",
        regra="normalização semanal (× 7 / ciclo, 7.0 / dias, max(7, ciclo))",
        regex=re.compile(r"\*\s*7(?:\.0)?\s*/|(?<![\w.])7\.0\s*/|max\(\s*(?:7|WEEKLY_WINDOW_DAYS)\s*,"),
        donos=("api/app/intelligence/methodology_volume.py",),  # weekly_normalization() / calendar_weekly_rate() (Lote 4a)
        inventario="E1, E2",
    ),
    Padrao(
        chave="relogio_sem_fuso",
        regra="datetime.now() / date.today() / CURDATE() em módulo de motor",
        regex=re.compile(r"\bdatetime\.now\(\s*\)|\bdate\.today\(\s*\)|\bCURDATE\(\s*\)"),
        donos=("api/app/intelligence/dashboard/time_windows.py",),  # user_now / user_today
        inventario="B10",
        linguagens=frozenset({"py"}),
        escopo=MODULOS_DE_MOTOR,
    ),
    Padrao(
        chave="motor_legado_carga",
        regra="motor legado de sugestão de carga (`suggest_load_and_reps`) chamado ou recriado",
        regex=re.compile(r"\bsuggest_load_and_reps\b"),
        donos=("api/app/domain/prescription_service.py",),  # suggest_for_pair(s): motor v2 por par
        inventario="C1, C2",
        linguagens=frozenset({"py"}),
    ),
    Padrao(
        chave="carga_percentual",
        regra="carga sugerida por percentual solto (+5% / ×1,05 / ×(1 + pct) / ×(1 ± p/100)) em vez do incremento "
        "no passo do equipamento",
        # 2026-10-20 (regras de carga, R1): o percentual CADASTRADO numa regra da
        # próxima série vira kg SÓ dentro de `domain/next_set_load.py`
        # (`_percent_load`: arredondado ao passo, ao menos um passo na direção);
        # tela, prompt e SQL recebem a carga pronta. A forma `carga * (1 ± x / 100)`
        # vale também em TS (a tela de cadastro do R4 não calcula nada).
        regex=re.compile(
            r"(?i)\b\w*(?:load|carga|peso)\w*\s*\*\s*(?:\(\s*1(?:\.0)?\s*\+\s*(?:pct|percent\w*|0\.\d+)\s*\)|1\.0[1-9]\d*\b)|_adjust\(\s*0\.05\s*\)"
            r"|\b\w*(?:load|weight|carga|peso)\w*\s*\*\s*\(\s*1\s*[+-]\s*\w+\s*/\s*100\s*\)"
        ),
        # load_increment/next_load (múltiplo do passo); next_set_load (percentual da regra, só no dono)
        donos=("api/app/domain/progression_engine.py", "api/app/domain/next_set_load.py"),
        inventario="C3, R1",
        linguagens=frozenset({"py", "ts"}),
    ),
    Padrao(
        chave="rpe_texto_paralelo",
        regra="parser/corte próprio de `rpe_target` em texto (fora de parse_rpe_ramp/clamp_rpe_text)",
        regex=re.compile(r"\b(?:cap_rpe_target_for_reduced_dose|_clamp_rpe_text|clamp_rpe_target_text|_cap_token)\b"),
        donos=("api/app/domain/effective_intensity.py",),  # clamp_rpe_text (+ training_history.parse_rpe_ramp)
        inventario="C4",
        linguagens=frozenset({"py"}),
    ),
    Padrao(
        chave="percentual_de_carga",
        regra="incremento/redução de carga em percentual escrito à mão em prompt/RAG (+5%, 2,5%-5%)",
        # "+5%", " -5%" (sinal colado no número) e faixas "2,5% a 5%" /
        # "2,5%-5%". Percentuais de outra natureza (FCmáx, composição corporal,
        # % da carga de trabalho no aquecimento, item de lista "- 60%",
        # `translateX(-50%)`) não têm sinal colado nem a faixa com x,5.
        regex=re.compile(r"\+\s?\d+(?:[.,]\d+)?\s?%|(?<=\s)-\d+(?:[.,]\d+)?\s?%|\d[.,]5\s?%\s?(?:-|–|a|e|ou)\s?\d+(?:[.,]\d+)?\s?%"),
        donos=("api/app/domain/progression_engine.py",),  # load_increment(): passo do equipamento, em kg
        inventario="C1, C3, C11",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="fatores_carga_sessao",
        regra="fator de carga de aquecimento/ajuste/drop escrito à mão (×0,2/×0,4, 0,5 + 0,2·(n−1), 0,8ⁱ)",
        # `Math.pow(0.8`, `0.8 **`, `0.5 + 0.2 *` e `<carga> * 0.2|0.4|0.8`.
        # O FE lê os fatores de `motor-constants.generated.json` (exportado
        # pelo dono) e o dono usa constantes nomeadas (WARMUP_LOAD_FACTORS...).
        regex=re.compile(
            r"Math\.pow\(\s*0\.8\b|(?<![\d.])0\.8\s*\*\*|(?<![\d.])0\.5\s*\+\s*0\.2\s*\*"
            r"|(?i:load|carga|weight|peso)\w*\s*\*\s*0\.[248]\b"
        ),
        donos=("api/app/domain/session_loads.py",),  # aquecimento, ajuste e drop no passo do equipamento
        inventario="C7",
    ),
    Padrao(
        chave="set_type_paralelo",
        regra="classificação de `set_type` à mão (apelido `'dropset'`, `CASE` de set_type, `set_type === 'working'`, par 'warmup','feeler')",
        # O vocabulário (canônicos, apelidos, conjuntos) é de `set_types.py`;
        # o FE lê o espelho único `set-types.ts` (JSON exportado pelo dono,
        # Lote 3b). Rótulos de exibição por técnica ('drop_set' → "Drop") e
        # estágios do Modo Treino (`kind === 'warmup'`) não são classificação
        # de série e não casam.
        regex=re.compile(
            r"""["']dropset["']|\bCASE\b[^\n]{0,80}\bset_type\b"""
            r"""|\b(?:set_type|setType|SetType)\b[^=!\n]{0,20}(?:===?|!==?)\s*["'](?:warmup|feeler|working|backoff)["']"""
            r"""|["'](?:warmup|feeler)["']\s*,\s*["'](?:warmup|feeler)["']"""
        ),
        donos=("api/app/set_types.py", "frontend/src/lib/set-types.ts"),
        inventario="A (série de trabalho)",
    ),
    Padrao(
        chave="recorde_paralelo",
        regra="recorde/destaque por critério próprio (`MAX(best_weight_kg)`, `get_recent_progression`)",
        # Decisão 3 (2026-10-06): recorde = `session_records.record_events`
        # (carga direcional estritamente maior na mesma chave; 1ª exposição
        # não é recorde). Proibição pura dos critérios antigos.
        regex=re.compile(r"MAX\(\s*(?:\w+\.)?best_weight_kg\s*\)|\bget_recent_progression\b"),
        donos=("api/app/intelligence/dashboard/session_records.py",),
        inventario="A (recorde)",
    ),
    Padrao(
        chave="e1rm_confiavel_paralelo",
        regra="predicado 'e1RM confiável' (`reps <= teto`) reimplementado fora do dono",
        # COD-04 (varredura 2026-10-08): o predicado é `e1rm.is_reliable_reps`
        # (`reliable_e1rm` para o valor). Consumidor chama o dono; comparar
        # `<= E1RM_MAX_RELIABLE_REPS` / `<= THRESHOLDS["e1rm_max_reliable_reps"]`
        # fora dele é cópia. Proibição pura (o dono compara com o `max_reps`
        # do argumento). O SQL de `analytics` que interpola o teto no WHERE fica
        # na allowlist (filtro em SQL, não tem como chamar o predicado Python).
        regex=re.compile(
            r"""<=\s*\{?\s*(?:int\(\s*)?(?:[\w.]*THRESHOLDS\[\s*["']e1rm_max_reliable_reps["']\s*\]|[\w.]*E1RM_MAX_RELIABLE_REPS\b)"""
        ),
        donos=(DONO_E1RM,),
        inventario="COD-04",
    ),
    Padrao(
        chave="payload_ativacao_sem_tecnica",
        regra="payload de ativação `{mapping_id, sets_count}` montado à mão, sem a técnica avançada",
        # Lote 4b (E3): payload único = `technique_volume.activation_payload`
        # (py) / `activation-payload.ts` (FE), com `advanced_technique` e
        # `advanced_technique_applications`; o VolumeService soma o equivalente.
        # Pega o objeto de uma linha que FECHA logo depois de `sets_count`.
        regex=re.compile(
            r"""^(?!.*advanced_technique).*\bmapping_id["']?\s*:[^;{}]*?\bsets_count["']?\s*:\s*"""
            r"""(?:[^,(){};]|\([^()]*\))+?\s*\}"""
        ),
        donos=("api/app/intelligence/technique_volume.py", "frontend/src/lib/activation-payload.ts"),
        inventario="E3",
    ),
    Padrao(
        chave="limiar_ativacao_fe",
        regra="limiares próprios de ativação no FE (razão 0,9/0,5 do alvo; 6/9 séries equivalentes)",
        # Lote 4b (E4): o status sai de `volume_service.assess_activation_against_target`
        # (`classify_weekly_volume_status` / `classify_planned_volume_status`).
        regex=re.compile(r"\bratio\s*>=\s*0\.(?:9|5)\b|\b\w*[Ee]quivalent\w*\s*<\s*(?:6|9)\b"),
        donos=(),
        inventario="E4",
        linguagens=frozenset({"ts"}),
        dono_pendente="volume_service.assess_activation_against_target (backend)",
    ),
    Padrao(
        chave="muito_abaixo_planejado",
        regra='"muito abaixo do planejado" = realizado < 0,5 do planejado (gravidade do status planejado)',
        # Decisão do usuário 2026-10-07: o nível volta PELO BACKEND
        # (`volume_service.planned_volume_severity`); o FE só pinta `assessment.severity`.
        # Pega a constante redefinida e a conta inline contra planejado/alvo; o
        # `ratio < 0.5` do mapa de calor (`VolumeGrupamentosClient.tsx`) não casa.
        regex=re.compile(
            r"\b\w*(?:FAR_BELOW|MUITO_ABAIXO|farBelow|muitoAbaixo)\w*\s*[:=]\s*0\.5\b"
            r"|\b\w*(?:[Pp]lanned|[Pp]lanejado|[Tt]arget|[Aa]lvo)\w*\s*\*\s*0\.5\b"
            r"|(?<![\w.])0\.5\s*\*\s*\w*(?:[Pp]lanned|[Pp]lanejado|[Tt]arget|[Aa]lvo)"
        ),
        donos=("api/app/intelligence/volume_service.py",),
        inventario="E4",
    ),
    Padrao(
        chave="metodologia_no_fe",
        regra="metodologia do realizado reavaliada no FE (faixa por frequência, comparação, fatia -> bloco)",
        # Lote 4b (E6): `GET /analytics/methodology-realized`
        # (`intelligence/methodology_realized.py`). Proibição pura dos helpers antigos.
        regex=re.compile(
            r"\b(?:classifyAgainstRange|frequencyBucketForDays|sessionRangeFor|weeklyRangeFor|makeBlockResolver)\b"
        ),
        donos=(),
        inventario="E6",
        linguagens=frozenset({"ts"}),
        dono_pendente="intelligence/methodology_realized.py (backend)",
    ),
    Padrao(
        chave="fadiga_previa",
        regra="fadiga prévia na sessão (cortes de faixa, saturação do nível) fora do dono",
        # 2026-10-07: dono `domain/prior_fatigue.py`. Pega constante de fadiga
        # prévia com número e comparação/divisão do nível ou das séries prévias
        # por número (o FE só pinta `prior_fatigue.level` e mostra `band_label`).
        # M79 (2026-10-11): o indicador de fadiga do grupo do Modo Treino
        # (`GROUP_FATIGUE_*`, `group_fatigue`) é do mesmo dono e cai no padrão.
        regex=re.compile(
            r"\b(?:PRIOR_FATIGUE|GROUP_FATIGUE|FADIGA_PREVIA)\w*\s*(?::\s*[\w\[\].]+\s*)?[:=]\s*\d"
            r"|\b(?:prior_?[Ff]atigue|priorFatigue|group_?[Ff]atigue|groupFatigue|prior_sets|priorSets"
            r"|fadiga_?[Pp]revia)\w*(?:\??\.\w+)*"
            r"\s*(?:[<>]=?|/)\s*\d"
        ),
        donos=("api/app/domain/prior_fatigue.py",),
        inventario="F1",
    ),
    Padrao(
        chave="trilha_sem_contexto",
        regra="trilha da progressão buscada só pelo par (exercício, equipamento), sem o contexto de fadiga",
        # 2026-10-07 (PR 2 da fadiga prévia): a trilha é por par + contexto de
        # fadiga; quem prescreve resolve com `progression_engine.resolve_track`
        # (contexto pedido, fallback sem desconto). Pega `tracks.get((a, b))` e
        # `tracks[(a, b)]` com tupla de DUAS posições.
        regex=re.compile(
            r"\btracks\s*(?:\.get\(|\[)\s*\(\s*(?:[^(),]|\([^()]*\))+,\s*(?:[^(),]|\([^()]*\))+\)\s*[\])]"
        ),
        donos=("api/app/domain/progression_engine.py",),
        inventario="F1",
        linguagens=frozenset({"py"}),
    ),
    Padrao(
        chave="adocao_carga_abaixo_do_piso",
        regra="predicado 'todas as séries abaixo do piso de repetições' (D10 / adoção da carga executada) fora do dono",
        # M77 (2026-10-11): se a trilha adota a carga executada abaixo do piso
        # é decisão de `progression_engine.adopted_working_load` /
        # `below_floor_load_is_working`; consumidor não refaz o
        # `all(reps < reps_min)` para decidir carga.
        regex=re.compile(r"\b(?:all|every)\s*\(.*\breps\b.*<.*\breps_?[mM]in\b"),
        donos=("api/app/domain/progression_engine.py",),
        inventario="M77",
    ),
    Padrao(
        chave="mesmas_reps_rpe_maior",
        regra="'mesmas reps cruas emparelhadas' (P8: mesmas reps na mesma carga com RPE maior = Estável) fora do dono",
        # P8 (investigação RPE 10, 2026-10-11): se as reps cruas emparelhadas
        # são as mesmas (e o RPE subiu, logo não é regressão) é decisão de
        # `comparability._same_reps_rpe_change` / `REASON_SAME_REPS_HIGHER_RPE`;
        # tela, IA e recuperação consomem o status/motivo pronto.
        regex=re.compile(
            r"\b(?:previous|prev)_?[rR]eps\s*[!=]==?\s*[\w.]*(?:current|curr)_?[rR]eps\b"
            r"|\b(?:current|curr)_?[rR]eps\s*[!=]==?\s*[\w.]*(?:previous|prev)_?[rR]eps\b"
        ),
        donos=("api/app/intelligence/dashboard/comparability.py",),
        inventario="P8",
    ),
    Padrao(
        chave="dose_exigida_series",
        regra="dose exigida da sessão (nº de séries de trabalho contra a ficha/trilha) ou sets_count da trilha pela execução fora do dono",
        # Lote 2/P3 (2026-10-11): a dose exigida é `progression_engine.required_dose_sets`
        # (séries da ficha congelada do dia; fallback `track.sets_count`) e a execução
        # NÃO muda `sets_count` (só `ADD_SET`). Pega `len(working) < x.sets_count` e
        # `clamp_sets_count(len(...))` (o antigo "sobe por séries extras").
        regex=re.compile(
            r"\blen\(\s*\w*working\w*\s*\)\s*[<>]=?\s*(?:int\(\s*)?[\w.]*(?:sets_count|required_dose_sets)\b"
            r"|\bclamp_sets_count\(\s*len\("
        ),
        donos=("api/app/domain/progression_engine.py",),
        inventario="RPE10-P3",
        linguagens=frozenset({"py"}),
    ),
    Padrao(
        chave="ficha_congelada_sql",
        regra="intensidade da ficha congelada (alvo/teto/falha do prescription_json) lida por SQL fora do dono",
        # Lote 2/P7 (2026-10-11): o fechamento avalia contra a ficha congelada e
        # quem extrai alvo/teto/falha do snapshot por SQL é
        # `training_history.SESSIONS_SQL` (`FrozenPrescription`); consumidor
        # recebe o valor pronto (ou lê o snapshot inteiro por
        # `effective_intensity.intensity_for_row`).
        regex=re.compile(r"\$\.(?:effective_rpe_target|effective_rpe_ceiling|failure_allowed|failure_denied_by)\b"),
        donos=("api/app/domain/training_history.py",),
        inventario="RPE10-P7",
    ),
    Padrao(
        chave="queda_reps_entre_series",
        regra="queda de repetições entre a 1ª e a última série de trabalho (razão 1 − última/primeira e limiar 0,30) fora do dono",
        # Lote 1/P2 da investigação RPE 10 (2026-10-11): o sinal EXCESSIVE_REP_DROP
        # é decidido só por `VolumeService._rep_drop_from_sets` (constantes
        # `REP_DROP_MIN_FRACTION` e `REP_DROP_IGNORE_FROM_RPE`, só séries
        # `working`, sem queda com a última em RPE >= 9). Consumidor lê
        # `excessive_rep_drop` / `rep_drop_details` prontos.
        regex=re.compile(
            r"""\b1(?:\.0)?\s*-\s*\(?\s*(?:float\(|Number\()?\s*[\w.]*last\w*(?:\[["']reps["']\]|_?[rR]eps\b|\.reps\b)"""
            r"|(?i:\brep\w*_?drop\w*\s*(?:=|>=?)\s*0?\.30?\b)"
        ),
        donos=("api/app/intelligence/volume_service.py",),
        inventario="RPE10-P2",
    ),
    Padrao(
        chave="qualificacao_melhor_serie",
        regra="predicado de qualificação por repetições (melhor série no topo da faixa, todas no piso) fora do dono",
        # Lote 3/P4 (2026-10-11, decisão do usuário): a sessão qualifica quando a
        # MELHOR série na carga de topo atinge `reps_max` e todas ficam >=
        # `reps_min` - `progression_engine.evaluate_track_session` (+ `best_set`/
        # `top_load_sets`). Tela, SQL, IA e relatório consomem o veredito
        # (`QUALIFY`/`reps_below_reps_max`/`reps_below_reps_min`), não refazem
        # `any(reps < reps_max)` nem `best.reps >= reps_max`.
        regex=re.compile(
            r"(?:\b(?:any|all)\s*\(|\.(?:every|some)\s*\().*\breps\b.*[<>]=?.*\breps_?[mM]ax\b"
            r"|\bbest\w*\.reps\b\)?\s*[<>]=?\s*[\w.(]*reps_?[mM]ax\b"
        ),
        donos=("api/app/domain/progression_engine.py",),
        inventario="RPE10-P4",
    ),
    Padrao(
        chave="excesso_reps_sobe_carga",
        regra="regra de excesso de repetições (reps_max + N sobe a carga da trilha) fora do dono",
        # Lote 3/P4 (2026-10-11): `reps_max + EXCESS_REPS_FAST_TRACK` no RPE alvo
        # arma o aumento de carga DA TRILHA - `progression_engine.excess_reps_reached`
        # / `arm_excess_reps_fast_track`, prescrito por `prescribe`. Ninguém
        # mais soma um excesso ao topo da faixa para decidir carga.
        regex=re.compile(r"\breps_?[mM]ax\)?\s*\+\s*(?:\d+\b|EXCESS_REPS_FAST_TRACK\b)"),
        donos=("api/app/domain/progression_engine.py",),
        inventario="RPE10-P4",
    ),
    Padrao(
        chave="plato_por_trilha",
        regra="platô de carga (N sessões qualificantes em >= D dias) ou maioria de trilhas da fase recalculados fora do dono",
        # Lote 4/P6 (2026-10-11, decisão do usuário, reabre a D2): o platô que
        # sobe a carga é POR TRILHA (`plateau_confirmed` sobre
        # `progression_evaluator.track_plateau_history`, `arm_track_plateau_load`)
        # e a F2 -> F3 sai pela maioria estrita das trilhas
        # (`group_load_phase_complete`, `GROUP_PHASE_ADVANCE_TRACK_SHARE`).
        # Tela, SQL, IA e relatório consomem a decisão/evidência
        # (`track_plateaus`, `track_plateau_load_increase`), não contam sessões
        # qualificantes nem a fração de trilhas.
        regex=re.compile(
            r"\b(?:PLATEAU_MIN_SESSIONS|PLATEAU_MIN_SPAN_DAYS|GROUP_PHASE_ADVANCE_TRACK_SHARE)\b"
            r"|\b\w*applied\w*\s*/\s*\w*total\w*\s*>"
        ),
        donos=("api/app/domain/progression_engine.py",),
        inventario="RPE10-P6",
    ),
    Padrao(
        chave="carga_topo_repetida",
        regra="carga da sessão 'a de topo se repete em metade das séries' (ceil(n/2)) recalculada fora do dono",
        # Lote 7/D1 (2026-10-11, decisão do usuário): a carga da sessão é a
        # maior L com >= ceil(n * TOP_LOAD_MIN_SHARE_OF_WORKING_SETS) séries de
        # trabalho em L ou acima e ao menos uma delas no piso -
        # `progression_engine.session_working_load` / `top_load_min_sets`.
        # Tela, SQL, IA e relatório recebem a carga pronta.
        regex=re.compile(
            r"\bTOP_LOAD_MIN_SHARE_OF_WORKING_SETS\b"
            r"|\bceil\s*\(\s*(?:len\s*\([^)]*\)|[\w.]*length)\s*(?:/\s*2|\*\s*0?\.5)\b"
        ),
        donos=("api/app/domain/progression_engine.py",),
        inventario="RPE10-L7-D1",
    ),
    Padrao(
        chave="vocabulario_ia_serie",
        regra="categoria da IA por série num vocabulário próprio (PROGREDIR/APRIMORAR TECNICA/REDUZIR/REGREDIR) fora do motor",
        # RPE 10 Lote 6/P9 (2026-10-11): as categorias da avaliação de série
        # são ações do motor (`domain/set_evaluation_categories.py`, rótulos de
        # `DECISION_LABELS`); o FE lê do `motor-constants.generated.json`.
        # Proibição pura: o vocabulário antigo não volta em código.
        regex=re.compile(r"""["'](?:PROGREDIR|APRIMORAR TECNICA|REDUZIR|REGREDIR)["']"""),
        donos=("api/app/domain/set_evaluation_categories.py",),
        inventario="RPE10-P9",
    ),
    Padrao(
        chave="exposicoes_minimas",
        regra='mínimo de exposições escrito à mão em prompt/RAG ("3 ou mais exposições", "mínimo de 3 exposições")',
        # IA-06 (d): o mínimo de exposições comparáveis do platô é
        # `THRESHOLDS["plateau_min_exposures"]`; prompt interpola
        # (`report_ai.PLATEAU_MIN_EXPOSURES`). Proibição pura.
        regex=re.compile(r"(?i)\b\d+\s+ou\s+mais\s+exposi[çc][õo]es|\bm[íi]nimo\s+de\s+\d+\s+exposi[çc][õo]es"),
        donos=("api/app/intelligence/dashboard/metrics_registry.py",),
        inventario="IA-06",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="fator_carga_trabalho_texto",
        regra='percentual da carga de trabalho escrito à mão em prompt/RAG ("30% a 40% da carga de trabalho", "70% da carga de trabalho")',
        # IA-06 (b/d): aquecimento e ajuste saem prontos de `session_loads`
        # (WARMUP_LOAD_FACTORS, FEELER_*); o RAG diz que a carga vem do
        # sistema. "100% da carga de trabalho" (a própria série válida) não casa.
        regex=re.compile(
            r"(?i)(?<![\d.,])\d{1,2}(?:[.,]\d+)?\s?%(?:\s?(?:a|e|-|–)\s?\d{1,3}(?:[.,]\d+)?\s?%)?"
            r"\s+da\s+carga\s+de\s+trabalho"
        ),
        donos=("api/app/domain/session_loads.py",),
        inventario="IA-06, C7",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="faixa_reps_prosa",
        regra='faixa de repetições escrita em prosa no RAG geral ("8 a 12 repetições", "8–12 reps")',
        # IA-06 (b/d): o alvo de reps vem pronto da prescrição (Modelo ou
        # `prescription_defaults.DEFAULT_REPS_TARGET`). Só os arquivos de
        # metodologia GERAL (entram em todo prompt com RAG); os de grupo e os
        # Modelos-exemplo de TIPOS_TREINO descrevem protocolos por exercício
        # (fora, ver §6 do mapa). "1 a 2 repetições antes de..." é RIR e não casa.
        regex=re.compile(r"(?i)\b\d+\s?(?:a|-|–)\s?\d+\s+(?:repeti[çc][õo]es|reps)\b(?!\s+antes)"),
        donos=(DONO_PRESCRICAO,),
        inventario="IA-06, E8",
        linguagens=frozenset({"md"}),
        # M97 F5 (§68): o material do objetivo (`api/privado/rag/markdown/OBJETIVOS/**`) também é
        # metodologia GERAL injetada em prompt: a faixa de reps vem por placeholder do dono
        # (`app/rag_placeholders.py`), nunca em prosa.
        escopo=(
            "api/privado/rag/markdown/HABILIDADES_TREINO.md",
            "api/privado/rag/markdown/HIPERTROFIA.md",
            "api/privado/rag/markdown/PROGRESSAO.md",
            "api/privado/rag/markdown/OBJETIVOS/",
        ),
    ),
    Padrao(
        chave="numeros_prompt_cardio",
        regra='número de domínio do cardio escrito à mão ("N minutos Z2", "60-70% VO2max", "PAI acima de 75", "5x [2 minutos", "mínimo de 3 ocorrências")',
        # IA-06 (c, decisão do usuário 2026-10-08): cardio é motor; o dono é
        # `domain/cardio_policy.py` (constantes nomeadas) e o SYSTEM_PROMPT de
        # `cardio_agent_ai.py` interpola. Proibição pura.
        regex=re.compile(
            r"(?i)\b\d+\s+minutos\s+(?:em\s+)?Z2\b|\b\d+\s?-\s?\d+\s?%\s*VO2|\bPAI\b[^\n\d{}]{0,25}\d{2}\b"
            r"|\b\d+x\s*\[\s*\d+\s+minutos|\bm[íi]nimo\s+de\s+\d+\s+ocorr[êe]ncias"
        ),
        donos=("api/app/domain/cardio_policy.py",),
        inventario="IA-06",
        linguagens=frozenset({"py", "ts"}),
    ),
    Padrao(
        chave="tonelagem_convertida",
        regra='tonelagem convertida para "toneladas" fora do dono (código, prompt ou RAG)',
        # 2026-10-10 (eval pago do resumo de treino: "16,3 toneladas totais"):
        # a tonelagem sai pronta, em kg, de `load_conventions.format_tonnage`;
        # prompt interpola e proíbe converter. Proibição pura.
        regex=re.compile(r"(?i)\btoneladas?\b"),
        donos=("api/app/load_conventions.py",),
        inventario="M58",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="limiar_dor_muscular",
        regra="dor muscular comparada com um número escrito à mão (limiar de dor fora do motor de recuperação)",
        # set-eval-v5 (2026-10-12): o limiar de dor alta é
        # `muscle_recovery_service.MUSCLE_PAIN_BLOCK_ABOVE`, aplicado só por
        # `muscle_pain_blocks_direct_work` (readiness e IA por série); o
        # bloqueio de progressão do motor usa `BLOCKING_MUSCLE_PAIN`. `> 0`
        # ("houve relato") não é limiar e não casa.
        regex=re.compile(r"muscle_?[pP]ain\w*\)?\s*(?:>=?|<=?)\s*[1-9]"),
        donos=(DONO_RECUPERACAO,),
        inventario="SET-EVAL-V5",
    ),
    Padrao(
        chave="rotulo_status_serie",
        regra='rótulo do status de esforço / reps contra a faixa escrito à mão ("Alvo atingido", "Esforço atingido", "Reps abaixo da faixa"...)',
        # set-eval-v5 (2026-10-12): rótulos com dono único
        # (`domain/intensity_status_labels.py`); o FE lê do
        # `motor-constants.generated.json` e o prompt interpola. O rótulo
        # antigo "Alvo atingido" (lido pela IA como alvo geral) não volta.
        regex=re.compile(
            r"""["'](?:Alvo atingido|Abaixo do alvo seguro|Esforço atingido|Esforço abaixo do alvo"""
            r"""|Reps (?:abaixo|dentro|no teto|acima) da faixa)"""
        ),
        donos=("api/app/domain/intensity_status_labels.py",),
        inventario="SET-EVAL-V5",
    ),
    Padrao(
        chave="carga_proxima_serie",
        regra="limiar da carga da próxima série escrito à mão (reps > 15, reps >= 12 com RPE < 9, RPE 10 com reps < 6; \"mais de 15 repetições\")",
        # 2026-10-12 (regra do usuário): a carga da PRÓXIMA série na sessão é
        # de `domain/next_set_load.py` (constantes `NEXT_SET_*`); a IA por
        # série, o salvamento da série e o Modo Treino consomem a sugestão
        # pronta. `reps <= 12` (e1RM confiável) não casa. Proibição pura.
        # 2026-10-20 (regras de carga, R1): o conjunto PADRÃO (`DEFAULT_RULESET`,
        # `RuleSet(reps_floor=<número>`) só existe no dono; os cadastrados
        # chegam do banco (R2) por `ruleset_from_dict`.
        regex=re.compile(
            r"\breps\w*\)?\s*(?:>\s*1[56]|>=\s*1[26]|<\s*6|<=\s*5)\b"
            r"|(?:[Mm]ais\s+de\s+1[56]|antes\s+de\s+[56])\s+repeti"
            r"|\bNEXT_SET_\w+(?:\s*:\s*Final\[int\])?\s*=\s*\d"
            r"|\bDEFAULT_RULESET\s*=|\bRuleSet\(\s*reps_floor\s*=\s*\d"
        ),
        donos=("api/app/domain/next_set_load.py",),
        inventario="SET-EVAL-V6, R1",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="regra_carga_cadastrada",
        regra="condição de uma regra de carga da próxima série avaliada fora do dono (`reps_condition … > 12`, "
        "`rule.reps.op === '<'`, `magnitude.value / 100`)",
        # 2026-10-20 (regras de carga, R1): o conjunto de regras (posição da série,
        # fadiga prévia, reps, RPE, rampa, ação e magnitude) é avaliado SÓ por
        # `next_set_load.evaluate_ruleset`; rota, SQL, prompt e tela recebem a
        # sugestão pronta (`NextSetLoad`, `describe_rule`). A tela de cadastro
        # (`/regras-carga`, R4) só monta Selects com o vocabulário do JSON gerado e
        # fica fora do escopo com o seu helper. `=>` (arrow) não casa.
        regex=re.compile(
            r"\b(?:reps|rpe)_condition\b[^\n]*(?:(?<!=)[<>]=?|===?)\s*(?:\d|facts|reps|rpe)"
            r"|\b(?:rule|regra)\w*\.(?:reps|rpe)\s*\.\s*(?:op|value)\s*(?:===?|!==?)"
            r"|\bmagnitude\w*\.value\s*/\s*100"
        ),
        donos=("api/app/domain/next_set_load.py",),
        inventario="R1",
        escopo=("api/app/", "frontend/src/"),
        excluir=("frontend/src/app/(app)/regras-carga/", "frontend/src/lib/next-set-load-rules*.ts"),
    ),
    Padrao(
        chave="intensidade_densidade",
        regra="calibração da Intensidade × Densidade escrita à mão (pontos de RPE 10/9/8, faixas 6..15 reps, 0..5 s de pico, 180..45 s de descanso, 2..5 séries, banda 0,1)",
        # E13 (2026-10-13): o dono é `domain/intensity_density.py` (constantes
        # nomeadas); o espelho `intensity-density.ts` lê a seção
        # `intensity_density` de `motor-constants.generated.json` e roda contra
        # `intensity-density.parity.json`. Pega a constante nomeada com número,
        # a tabela `{ minRpe: 10, ... }` e o `linear(x, 180, 45)` inline.
        regex=re.compile(
            r"\b(?:RPE_INTENSITY_POINTS|(?:REPS|PEAK|REST)_DENSITY_\w+|SETS_VOLUME_\w+|BALANCED_BAND|FAILURE_LAST_SET_RPE)"
            r"\s*(?::[^=\n]+)?=\s*(?:\d|\{\s*\w+\s*:\s*\d|\[\s*\{\s*\w+\s*:\s*\d)"
            r"|\bminRpe\s*:\s*\d"
            r"|\blinear\(\s*[\w.]+\s*,\s*(?:6|0|180|2)\s*,\s*(?:15|5|45)\s*\)"
        ),
        donos=("api/app/domain/intensity_density.py",),
        inventario="E13",
    ),
    Padrao(
        chave="modo_contagem_volume",
        regra="modo de contagem do volume decidido fora do motor (literal 'primary_only'/'primary_secondary' ou o limiar de sessão por secundário)",
        # Modo de contagem, Lote 1 (2026-10-16): o modo é `VolumeCountingMode`
        # e o limiar "1 série de secundário conta a sessão" é
        # `SECONDARY_SESSION_MIN_SETS`, ambos em `methodology_volume.py`.
        # Consumidor usa o enum (nunca compara com a string) e nunca reescreve
        # o limiar. Lote 3 (telas): vale também para o FE, que lê os valores e
        # rótulos do modo de `motor-constants.generated.json`
        # (`methodology_volume.export_for_frontend`) ou do payload.
        regex=re.compile(
            r"[\"'](?:primary_only|primary_secondary)[\"']|\bSECONDARY_SESSION_MIN_SETS\s*(?::[^=\n]+)?=\s*\d"
        ),
        donos=("api/app/intelligence/methodology_volume.py",),
        inventario="MODO-CONTAGEM",
        linguagens=frozenset({"py", "ts"}),
    ),
    Padrao(
        chave="faixas_sem_modo",
        regra="faixa/meta semanal de um modo de contagem lida sem filtrar o modo (SQL em muscle_volume_class_mode_ranges/_weekly_targets sem `counting_mode =`)",
        # Modo de contagem, Lote 2 (073): as filhas guardam as faixas e metas de
        # TODOS os modos != primary_only; ler sem `counting_mode = ...` mistura
        # modos. As tabelas da 057 (`muscle_volume_class_ranges`,
        # `muscle_volume_classes.weekly_*`) são o cadastro de primary_only por
        # definição e ficam fora. Quem escolhe a fonte pelo modo é o loader
        # (`methodology_volume_service.load_volume_classes`); o CRUD admin filtra
        # pelo modo da rota. Proibição pura (o dono também filtra).
        regex=re.compile(
            r"\b(?:FROM|JOIN)\s+muscle_volume_class_(?:mode_ranges|weekly_targets)\b(?![^\n]*counting_mode\s*=)"
        ),
        donos=("api/app/intelligence/methodology_volume_service.py",),
        inventario="MODO-CONTAGEM",
        linguagens=frozenset({"py"}),
    ),
    Padrao(
        chave="modo_contagem_da_sessao",
        regra="modo de contagem de uma sessão/Modelo lido à mão por motor, leitura ou analytics (coluna `volume_counting_mode` fora do service dono)",
        # Modo de contagem, Lote 4 (2026-10-16): qual versão do Modelo governa a
        # sessão (atividade -> ficha -> `plan_version_expr`) e qual modo vale na
        # janela é do service do motor (`load_session_counting_modes`,
        # `load_counting_mode_at`, `counting_mode_of_template`). Progressão,
        # realizado, dashboard e analytics recebem o modo pronto. O CRUD do
        # Modelo (`workout_plan_templates.py`, `template_versions.py`,
        # `models.py`) grava a coluna e fica fora do escopo.
        regex=re.compile(r"\bvolume_counting_mode\b"),
        donos=("api/app/intelligence/methodology_volume_service.py",),
        inventario="MODO-CONTAGEM",
        linguagens=frozenset({"py"}),
        escopo=(
            "api/app/intelligence/",
            "api/app/domain/",
            "api/app/progression_state.py",
            "api/app/analytics.py",
            "api/app/analytics_dashboard.py",
        ),
    ),
    Padrao(
        chave="pesos_tecnica",
        regra="pesos da família além da falha (forçadas/negativas 1,0, parciais/cheat 0,5) e teto/RPEs de referência da contabilização de técnica escritos à mão",
        # 2026-10-13: dono `domain/technique_accounting.py`; o espelho
        # `technique-accounting.ts` lê a seção `technique_accounting` de
        # `motor-constants.generated.json` e roda contra
        # `technique-accounting.parity.json`. Coeficientes POR TÉCNICA vêm do
        # catálogo pela API (não são literal de regra).
        regex=re.compile(
            r"""\b(?:forced|negative|partial|cheat)_reps["']?\s*:\s*\d"""
            r"|\b(?:DEFAULT_MAX_EQUIVALENT|FULL_EFFORT_RPE|DEFAULT_REFERENCE_RPE)\s*(?::[^=\n]+)?=\s*\d"
        ),
        donos=("api/app/domain/technique_accounting.py",),
        inventario="TEC-ESPELHO",
    ),
    Padrao(
        chave="pontos_esforco",
        regra="tabela RPE → pontos de esforço (6,5 → 0,15 … 10 → 1,05) ou o seu clamp 6,5..10 escritos à mão",
        # 2026-10-13: dono `MuscleRecoveryService._EFFORT_POINTS` / `effort_points`
        # (a contabilização de técnica delega a ele); o FE lê a tabela de
        # `motor-constants.generated.json` (seção `technique_accounting`).
        regex=re.compile(
            r"[\[(]\s*6\.5\s*,\s*0\.15\s*[\])]|[\[(]\s*10(?:\.0)?\s*,\s*1\.05\s*[\])]"
            r"|,\s*6\.5\s*,\s*10(?:\.0)?\s*\)|Math\.max\(\s*6\.5\b"
        ),
        donos=(DONO_RECUPERACAO,),
        inventario="TEC-ESPELHO",
    ),
    # --- Nutrição, Lote L1 (2026-10-16): motor `api/app/domain/nutrition/` ---------
    # Plano `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §7. Cada dono guarda a
    # regra em constante nomeada (`Final`); as regex pegam o literal da fonte
    # (fórmula, coeficiente) e a constante do dono redefinida com número.
    # Sem `keytel`: Keytel saiu do motor (D-K, 2026-10-15).
    Padrao(
        chave="tmb_formula",
        regra="fórmula de TMB escrita à mão (Katch-McArdle 370 + 21,6·MLG, Cunningham 500 + 22·MLG, Mifflin 6,25·cm, Harris-Benedict 88,362/447,593)",
        regex=re.compile(
            r"(?<![\d.,])(?:370\s*\+\s*21[.,]6|500\s*\+\s*22\b|6[.,]25\s*[*×·]|88[.,]362|447[.,]593)"
            r"|\b(?:MIFFLIN|KATCH_MCARDLE|CUNNINGHAM|HARRIS_BENEDICT)_\w+\s*(?::[^=\n]+)?=\s*[-(\d]"
        ),
        donos=("api/app/domain/nutrition/bmr.py",),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="fator_atividade",
        regra="fator de atividade escrito à mão (clássicos 1,375/1,55/1,725, tabela por nível `moderate: 1.4` ou `ACTIVITY_FACTORS`/`NON_EXERCISE_ACTIVITY_FACTORS` redefinida)",
        regex=re.compile(
            r"(?<![\d.,])1[.,](?:375|55|725)(?!\d)|\b(?:NON_EXERCISE_)?ACTIVITY_FACTORS\s*(?::[^=\n]+)?=\s*[{(\[]"
            r"""|\b(?:sedentary|light|moderate|active|very_active|SEDENTARY|LIGHT|MODERATE|ACTIVE|VERY_ACTIVE)["']?\]?\s*:\s*1[.,]\d"""
        ),
        donos=("api/app/domain/nutrition/anthropometrics.py",),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
        escopo=ESCOPO_NUTRICAO,  # 1,55 é ruído fora de nutrição (line-height, escala)
    ),
    Padrao(
        chave="kcal_por_grama",
        regra="kcal por grama de Atwater (4/4/9/7) escrita à mão (`* 4`, `/ 9`, `4 * p`, tabela `fat: 9`, \"4 kcal/g\", `KCAL_PER_G_* = <número>`)",
        # `meal_math` não é dono: consome `macros.kcal_from_macros`. `* 4.184`
        # (kJ) e macros de um alimento com decimal (`protein_g: 4.5`) não casam.
        regex=re.compile(
            r"""[*×/]\s*[49](?:\.0+)?(?![.\d\w'"`])|(?<![\w.])[49](?:\.0+)?\s*[*×]\s*[A-Za-z_(]"""
            r"""|\b(?:protein|prote[ií]na|carb\w*|fat|gordura|alcohol)\w*["']?\s*:\s*[479](?:\.0+)?(?!\d|[.,]\d)"""
            r"|\b[479]\s*kcal\s*(?:/|por)\s*g(?:ramas?)?\b"
            r"|\bKCAL_PER_G_\w+\s*(?::[^=\n]+)?=\s*\d"
        ),
        regex_md=re.compile(r"\b[479]\s*kcal\s*(?:/|por)\s*g(?:ramas?)?\b"),
        donos=("api/app/domain/nutrition/macros.py",),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
        escopo=ESCOPO_NUTRICAO,
    ),
    Padrao(
        chave="met_compendium",
        regra="kcal do treino por MET escrita à mão (`MET × 3,5 × kg / 200 × min`, `MET_STRENGTH_*`/`MET_KCAL_*` = <número>)",
        # `0,0175` = 3,5 / 200 (a mesma conta em kcal/min por kg).
        regex=re.compile(
            r"/\s*200(?:\.0+)?(?![.\d])|(?<![\d.,])0[.,]0175(?!\d)|\bMET_(?:KCAL|STRENGTH)_\w+\s*(?::[^=\n]+)?=\s*\d"
        ),
        donos=("api/app/domain/nutrition/energy_expenditure.py",),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
        escopo=ESCOPO_NUTRICAO + ("api/app/workout_sessions.py",),
    ),
    Padrao(
        chave="navy_gordura",
        regra="% de gordura da Marinha dos EUA escrito à mão (86,010 / 70,041 / 163,205 / 97,684; `NAVY_*` redefinido)",
        regex=re.compile(
            r"(?<![\d.,])(?:86[.,]010|70[.,]041|163[.,]205|97[.,]684)|\bNAVY_(?:MALE|FEMALE)\w*\s*(?::[^=\n]+)?=\s*[(\[\d]"
            # Forma métrica (densidade corporal + Siri): 1,0324 / 0,19077 / 0,15456 / 1,29579 / 0,35004.
            r"|(?<![\d.,])(?:1[.,]0324|0[.,]19077|0[.,]15456|1[.,]29579|0[.,]35004)(?!\d)"
        ),
        donos=("api/app/domain/nutrition/body_composition.py",),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="densidade_energetica",
        regra="densidade energética fixa da variação de peso (7.700 kcal/kg) ou os tecidos de Hall (9.400) escritos à mão",
        regex=re.compile(
            r"(?<![\d.,])(?:7[.,]?700|9[.,]?400)(?:\.0+)?(?!\d|[.,]\d)"
            r"|\b(?:FAT|LEAN)_TISSUE_KCAL_PER_KG\s*(?::[^=\n]+)?=\s*\d"
        ),
        donos=("api/app/domain/nutrition/energy_density.py",),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="faixa_proteina",
        regra='faixa/default em g/kg escrito à mão ("1,6–2,2 g/kg", "2,6 g/kg MLG"; `*PROTEIN*_G_PER_KG* = <número>`)',
        # O repo inteiro (o plano pedia nutrição + RAG de nutrição): hoje o RAG
        # geral não tem g/kg (HYP-031/032 conferidos), então a allowlist nasce
        # vazia e uma faixa nova em prompt de treino também é pega. Carbo mínimo
        # em g/kg ("3,0 g/kg") casa de propósito: mesmo dono (`strategy`).
        # Também as chaves das faixas do formulário com número (`protein_g_per_kg: 2.6`,
        # `fat_kcal_share = (0.15, 0.30)`): o FE lê `form_ranges` do JSON gerado.
        regex=re.compile(
            r"(?<![\d.,])[0-3](?:[.,]\d+)?\s*g\s*(?:/|por)\s*kg\b|\b\w*PROTEIN\w*_G_PER_KG\w*\s*(?::[^=\n]+)?=\s*[(\[\d]"
            r"|\b(?:protein_g_per_kg|fat_kcal_share|surplus_share|weekly_rate_share|carb_min_g_per_kg|max_deficit_share"
            r"""|min_intake_bmr_multiple)\w*["']?\s*[:=]\s*[\d(\[]"""
        ),
        donos=("api/app/domain/nutrition/strategy.py", "api/app/domain/nutrition/macros.py"),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="ffmi_normalizado",
        regra="FFMI normalizado escrito à mão (`+ 6,1 × (1,8 − altura)`; `FFMI_*` = <número>)",
        regex=re.compile(r"(?<![\d.,])6[.,]1\s*[*×·]\s*\(\s*1[.,]8|\bFFMI_\w+\s*(?::[^=\n]+)?=\s*\d"),
        donos=("api/app/domain/nutrition/body_composition.py",),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="pausa_dieta",
        regra='protocolo de pausa de dieta escrito à mão ("2 on/2 off", "3+1", "2 semanas … 2 semanas"; `MATADOR_*`/`ICECAP_*` = <número>)',
        regex=re.compile(
            r"(?i)\b2\s*on\s*/\s*2\s*off\b|(?<![\d.,])3\s*[+:]\s*1(?![\d.,])"
            r"|\b[23]\s*semanas?\b[^\n\d]{0,40}?\b[12]\s*semanas?\b"
            r"|\b(?:MATADOR|ICECAP)_\w+\s*(?::[^=\n]+)?=\s*\d"
        ),
        donos=("api/app/domain/nutrition/phases.py",),
        inventario="NUTRICAO-L1",
        linguagens=frozenset({"py", "ts", "md"}),
        escopo=ESCOPO_NUTRICAO,
    ),
    Padrao(
        chave="rotulo_estrategia",
        regra='rótulo pt-BR de estratégia/fase/dia de nutrição escrito à mão ("Bulking", "Cutting", "Manutenção", "Dia alto/baixo em carbo", "Pausa de dieta")',
        # Só o rótulo inteiro entre aspas; "Cutting (definição)" não casa. O FE
        # lê `motor-constants.generated.json` (bloco `nutrition`, via
        # `nutrition-constants.ts`); prompts interpolam de `labels.py`. Python
        # também conta (o plano pedia só ts): rótulo em prompt é cópia.
        regex=re.compile(
            r"""["'`](?:Bulking|Cutting|Manuten[çc][ãa]o|Dia (?:alto|baixo) em carbo|Pausa de dieta)["'`]"""
            # Texto JSX entre tags: `<Badge>Cutting</Badge>`.
            r"|>\s*(?:Bulking|Cutting|Manuten[çc][ãa]o|Dia (?:alto|baixo) em carbo|Pausa de dieta)\s*<"
        ),
        donos=("api/app/domain/nutrition/labels.py",),
        inventario="NUTRICAO-L1",
    ),
    Padrao(
        chave="kcal_treino_prioridade",
        regra="prioridade da kcal do treino decidida fora do motor (`calories_kcal or/??/|| <estimativa>`, `TRAINING_KCAL_*PRIORITY =`)",
        # Quem decide informada × MET é `energy_expenditure.resolve_training_kcal`.
        # Fallback de EXIBIÇÃO para placeholder (`?? '—'`, `or 0`, `?? null`,
        # `or None`) não é prioridade e não casa. Proveniência (manual ×
        # Intervals) é de `domain/calories_source.py` (L0), não deste padrão.
        regex=re.compile(
            r"""calories_kcal["']?\)?\s*(?:\bor\b|\?\?|\|\|)(?!\s*(?:["'`—–-]|None\b|null\b|undefined\b|0\b))"""
            r"|\bTRAINING_KCAL_\w*PRIORITY\s*(?::[^=\n]+)?="
            # Ternário Python: `calories_kcal if ... else <estimativa>` (`else None`/`else 0` não casa).
            r"|\bcalories_kcal\s+if\b[^\n]*\belse\b(?!\s*(?:None|0)\b)"
        ),
        donos=("api/app/domain/nutrition/energy_expenditure.py",),
        inventario="NUTRICAO-L1",
    ),
    Padrao(
        chave="rotulo_site_medida",
        regra="rótulo pt-BR de um local de medida por fita (Cintura, Quadril, Braço esquerdo, Pescoço…) escrito à mão fora do dono",
        # 2026-10-15 (nutrição L0): dono `domain/body_measurement_sites.py`
        # (`SITE_LABELS`); a API, o body_service, o relatório e o FE
        # (`body-measurement-sites.ts` lendo `motor-constants.generated.json`)
        # consomem dele. "Peitoral" só conta como par `chest: "Peitoral"` (o
        # mesmo nome é grupo muscular legítimo em outro contexto). Delimitado
        # por aspas, crase (template literal) ou como texto JSX (`>Cintura<`).
        regex=re.compile(
            r"""\bchest["']?\s*:\s*["'`]Peitoral["'`]"""
            rf"""|["'`]{_ROTULOS_SITE}["'`]"""
            rf"""|>\s*{_ROTULOS_SITE}\s*<"""
        ),
        donos=("api/app/domain/body_measurement_sites.py",),
        inventario="NUT-L0",
    ),
    Padrao(
        chave="origem_kcal",
        regra="origem da kcal do treino (manual/intervals) decidida fora do dono: `calories_source` comparado "
        "com literal ou gravado como literal, origem escolhida por condicional, ou kcal da atividade comparada "
        "com a do link do Intervals",
        # 2026-10-16 (nutrição L0, revisão do #556): dono `domain/calories_source.py`
        # (`resolve_calories_source` na leitura, `source_on_manual_write` na
        # gravação). Rotas, SQL e telas recebem o código/rótulo prontos. O `=`
        # simples só conta com espaços dos dois lados (SQL), para não pegar
        # kwargs como `calories_kcal=calories_kcal`.
        regex=re.compile(
            r"""\bcalories_?[sS]ource\w*["'\]]*\s*[!=]==?\s*["'`]"""
            r"""|["'`](?:manual|intervals)["'`]\s*[!=]==?\s*[\w.\[\]"']*calories_?[sS]ource\b"""
            r"""|\bcalories_source\s*=\s*["'](?:manual|intervals)["']"""
            r"""|\bCALORIES_SOURCE_(?:MANUAL|INTERVALS)\s+if\b"""
            r"""|\bcalories_kcal\w*["'\]]*\s*(?:<=>|<>|[!=]==?)\s*[\w.\[\]"']*calories_kcal\b"""
            r"""|\bcalories_kcal\s+=\s+[\w.\[\]"']*calories_kcal\b"""
        ),
        donos=("api/app/domain/calories_source.py",),
        inventario="NUT-L0",
    ),
    Padrao(
        chave="vocabulario_sexo",
        regra="opção, código ou rótulo de sexo escrito à mão fora do dono (`value: 'M'`, `sex === 'F'`, "
        "`'Masculino'`/`'Feminino'`, `>Feminino<`)",
        # 2026-10-17 (nutrição L2a): código e rótulo têm dono no motor
        # (`anthropometrics.Sex`/`normalize_sex`/`sex_code` e `labels.SEX_LABELS`);
        # as telas leem `SEX_OPTIONS` de `nutrition-constants.ts` (JSON gerado) e a
        # API grava/devolve `sex_code`. A migração 077 tem a cópia congelada dos
        # apelidos (fora da varredura; paridade em `test_migration_077_plano.py`).
        # `value: 'other'` sozinho não casa (é genérico: refeição "other" etc.).
        regex=re.compile(
            r"""\bvalue\s*:\s*["'`](?:M|F|male|female)["'`]"""
            rf"""|["'`]{_ROTULOS_SEXO}["'`]"""
            rf"""|>\s*{_ROTULOS_SEXO}\s*<"""
            rf"""|\bsex\w*{_COMPARA_LITERAL}(?:[MmFf]|male|female|other)["'`]"""
        ),
        donos=("api/app/domain/nutrition/anthropometrics.py", "api/app/domain/nutrition/labels.py"),
        inventario="NUT-L2A",
    ),
    Padrao(
        chave="nivel_atividade_e_altura",
        regra="nível de atividade ou faixa de altura escritos à mão fora do dono (`activity_level === 'active'`, "
        "`value: 'sedentary'`, `'Sedentário'`/`'Muito ativo'`, `height_cm < 100`, `HEIGHT_CM_RANGE = (`)",
        # 2026-10-17 (nutrição L2a, revisão do guardião): mesmo dono do sexo
        # (`anthropometrics.ActivityLevel`/`activity_level_code`/`HEIGHT_CM_RANGE`/
        # `height_is_valid` + `labels.ACTIVITY_LEVEL_LABELS`). A tela lê
        # `ACTIVITY_LEVEL_OPTIONS` e `NUTRITION_FORM_RANGES.height_cm`. Só os
        # códigos e rótulos sem homônimo comum ("Leve", "Ativo", 'light' e
        # 'active' sozinhos são genéricos demais para o repo inteiro); o código
        # comparado ao campo `activity_level` pega todos.
        regex=re.compile(
            rf"""\bactivity_?[lL]evel\w*{_COMPARA_LITERAL}(?:sedentary|light|moderate|active|very_active)["'`]"""
            r"""|\bvalue\s*:\s*["'`](?:sedentary|very_active)["'`]"""
            r"""|["'`](?i:sedent[aá]ri[oa]|muito ativ[oa])["'`]|>\s*(?i:sedent[aá]ri[oa]|muito ativ[oa])\s*<"""
            r"""|\bheight_?[cC]m\w*["'\])]*\s*(?:<=?|>=?)\s*\d"""
            r"""|\bHEIGHT_CM_RANGE\s*(?::[^=\n]+)?=\s*[(\[]"""
        ),
        donos=("api/app/domain/nutrition/anthropometrics.py", "api/app/domain/nutrition/labels.py"),
        inventario="NUT-L2A",
    ),
    Padrao(
        chave="catalogo_alimentos",
        regra="constante do catálogo de alimentos redefinida com número fora do dono (limiares, pesos e limites da "
        "busca de `food_text`; porção máxima e densidade máxima da base ml de `catalog`)",
        # 2026-10-17 (nutrição L3a): a rota da busca, a tela e o worker do parse (L5)
        # importam os nomes do dono; um `MATCH_MIN_SCORE = 0.6` ou
        # `SEARCH_CANDIDATE_POOL = 60` em outro arquivo é cópia. Comparação
        # (`>= food_text.MATCH_MIN_SCORE`) não casa.
        regex=re.compile(
            r"\b(?:MATCH_(?:MIN_SCORE|AUTO_ACCEPT_\w+)|QUERY_COVERAGE_WEIGHT|TOKEN_JACCARD_WEIGHT|SEQUENCE_RATIO_WEIGHT"
            r"|SEARCH_(?:CANDIDATE_POOL|RESULT_LIMIT_\w+)|FULLTEXT_MIN_TOKEN_CHARS|PORTION_AMOUNT_MAX"
            r"|ML_BASIS_MAX_DENSITY_G_PER_ML)\s*(?::[^=\n]+)?=\s*[\d(]"
            # Vocabulário do matching redefinido (o worker do parse, L5, é o risco óbvio).
            r"|\b(?:SYNONYMS|STOPWORDS|NOISE_TOKENS)\s*(?::[^=\n]+)?=\s*(?:[{(\[]|frozenset|new Set)"
        ),
        donos=("api/app/domain/nutrition/food_text.py", "api/app/domain/nutrition/catalog.py"),
        inventario="NUT-L3A",
    ),
    Padrao(
        chave="nota_matching",
        regra="nota do matching de alimento comparada com número escrito à mão (`score >= 0.6`, `nota > .85`) "
        "fora de `food_text`",
        # 2026-10-17 (nutrição L3a, revisão do guardião): quem decide candidato e
        # pré-seleção é `food_text.rank_candidates`/`order_for_search`, que
        # devolvem `Match.auto_accept`; o resto compara com o nome do dono (ou
        # nem compara). Só no escopo de nutrição: "score" é genérico demais.
        regex=re.compile(r"""\b\w*(?:score|nota)\w*["'\]]*\s*(?:>=|>|<=|<)\s*0?[.,]\d""", re.IGNORECASE),
        donos=("api/app/domain/nutrition/food_text.py",),
        inventario="NUT-L3A",
        escopo=ESCOPO_NUTRICAO,
    ),
    Padrao(
        chave="fontes_externas_alimentos",
        regra="mapeamento ou regra das fontes externas fora do dono: id de nutriente do USDA FoodData Central, "
        "chave de nutriente do Open Food Facts (`energy-kcal`, `saturated-fat`, `*_100g`...), estrutura crua dos "
        "payloads (`foodNutrients`, `nutrientId`, `aggregated_set`, `nutriments`, `value_computed`, `fdcId`, "
        "`dataType`, `totalHits`), tipo de dado/preparo em literal (`\"SR Legacy\"`, `\"as_sold\"`), constante do "
        "dono redefinida (limite do OFF, página do USDA, GTIN, ids, chaves) ou validação de EAN à mão (`\\d{13}`, `% 10`)",
        # 2026-10-17 (nutrição L3b; revisão do guardião): o cliente HTTP e a rota
        # só consomem `external_sources` (`map_usda_food`, `map_off_product`,
        # `usda_search_request`/`usda_search_result`, `normalize_gtin`,
        # `off_lookup_allowed`); ler o payload, escolher id/chave/unidade/energia,
        # validar o EAN ou redefinir as constantes fora do dono é cópia da regra.
        # Os ids sozinhos só no escopo de nutrição, nunca seguidos de casa decimal
        # e sem o 2000 (açúcares totais), genérico demais (`setTimeout(…, 2000)`,
        # 2.000 kcal do %VD): esse fica coberto pela leitura do payload. As chaves
        # genéricas do OFF (`energy`, `fat`, `sodium`, `proteins`) não têm padrão
        # (§49): ficam cobertas por `aggregated_set`/`nutriments`.
        regex=re.compile(
            r"""["'`](?:energy-k(?:cal|j)|saturated-fat|trans-fat|added-sugars|[a-z]+(?:-[a-z]+)*_100g"""
            r"""|as_sold|SR Legacy|Foundation)["'`]"""
            r"|\b(?:foodNutrients|nutrientId|aggregated_set|nutriments|value_computed|fdcId|dataType|totalHits)\b"
            r"|\b(?:1003|1004|1005|1008|1062|1079|1093|1257|1258|2047|2048)\b(?![.,_]?\d)"
            r"|\b(?:OFF_LOOKUPS_PER_USER_DAY|OFF_LOOKUP_COUNTED_SOURCE|USDA_SEARCH_PAGE_SIZE|GTIN_LENGTHS|GS1_\w+"
            r"|USDA_(?:DATA_TYPES|NUTRIENT_IDS|ENERGY_CANDIDATES)"
            r"|OFF_(?:NUTRIENT_KEYS|ENERGY_CANDIDATES|PER_BASIS|ACCEPTED_PREPARATION|IGNORED_NUTRIENT_SOURCES"
            r"|PRODUCT_FIELDS|NAME_FIELDS))\s*(?::[^=\n]+)?=\s*(?:[\d(\[{\"'`]|frozenset|new Set|FoodSource)"
            r"|\\d\{(?:8|1[234])(?:,\s*1[234])?\}|%\s*10\b(?![.,]?\d)"
        ),
        donos=("api/app/domain/nutrition/external_sources.py",),
        inventario="NUT-L3B",
        escopo=ESCOPO_NUTRICAO,
    ),
    Padrao(
        chave="kj_kcal",
        regra="conversão kJ <-> kcal escrita à mão (4,184 ou `KJ_PER_KCAL` redefinido) fora de `meal_math`",
        # 2026-10-17 (nutrição L3b, revisão do guardião): energia em kJ das fontes
        # externas (USDA 1062, OFF `energy-kj`) e do rótulo (L6) vira kcal só por
        # `meal_math.kj_to_kcal`. Repo inteiro: hoje o 4,184 só existe no dono.
        regex=re.compile(r"(?<![\d.,])4[.,]184(?!\d)|\bKJ_PER_KCAL\s*(?::[^=\n]+)?=\s*\d"),
        donos=("api/app/domain/nutrition/meal_math.py",),
        inventario="NUT-L3B",
    ),
    Padrao(
        chave="rotulo_sem_macro_grupo",
        regra='rótulo do balde sem macro-grupo ("Sem macro-grupo") escrito à mão fora do dono',
        # 2026-10-17 (/volume-grupamentos por macro-grupo, mapa §50): o grupo sem
        # macro-grupo resolvido cai no balde `VOLUME_CLASS_UNCLASSIFIED_LABEL`, e
        # as rotas de volume já entregam `volume_class_name` preenchido. Pega a
        # grafia do rótulo em string ou texto JSX; "sem macro-grupo" em prosa
        # (minúscula) não casa.
        regex=re.compile(r"\bSem macro-grupo\b"),
        donos=("api/app/intelligence/methodology_volume.py",),
        inventario="VOLUME-AGRUPAMENTO",
    ),
    Padrao(
        chave="slot_por_nome",
        regra="slot do Modelo identificado pelo nome (comparar `slot_name`) em vez de `template_id` + `slot_sequence`",
        # 2026-10-23 (/volume-grupamentos por slot, mapa §3.5): a posição na ficha
        # é `SessionSlot.key` de `slot_session_comparison` (`template_id` +
        # `template_slot_sequence`); o nome é rótulo e pode repetir ou mudar.
        # Também o id do slot não serve (cada versão publicada grava ids novos).
        # Proibição pura: o dono não compara nome.
        regex=re.compile(r"""\bslot_?[nN]ame\b['"\]]?\s*(?:[!=]==?|<>)|(?:[!=]==?|<>)\s*[\w.\[\]'"?]*\bslot_?[nN]ame\b"""),
        donos=("api/app/intelligence/slot_session_comparison.py",),
        inventario="VOLUME-SLOT",
    ),
    Padrao(
        chave="chave_slot_fora_do_dono",
        regra="chave textual do grupo por slot (`slot:<template_id>:<sequence>`) montada fora do dono",
        # 2026-10-24 (/dashboard/treinos por slot, mapa §3.5): o grupo de cada
        # sessão chega pronto em `session.group.key` (`session_group_of`, que usa
        # `slot_group_key`). A tela montava a própria chave a partir do
        # `plan_link`; uma segunda definição de "mesmo slot" não volta.
        regex=re.compile(r"""['"`]slot:(?:\$\{|\{)"""),
        donos=("api/app/intelligence/slot_session_comparison.py",),
        inventario="VOLUME-SLOT",
    ),
    Padrao(
        chave="linhagem_modelo_fora_do_dono",
        regra="linhagem do Modelo (`source_template_id`, cópia → origem) lida ou percorrida fora do leitor/dono",
        # 2026-10-24 (090, mapa §3.5): a raiz da linhagem é resolvida só pelo dono
        # (`slot_session_comparison.resolve_lineage_roots`) a partir dos ids que
        # `template_lineage_reader` traz; quem grava a origem é só o
        # `duplicate_template`. SQL recursivo, JOIN na origem ou "subir a cadeia"
        # em outra camada seria uma segunda definição de "mesmo Modelo".
        regex=re.compile(r"\bsource_template_id\b|\bsourceTemplateId\b"),
        donos=("api/app/intelligence/template_lineage_reader.py", "api/app/workout_plan_templates.py"),
        inventario="VOLUME-SLOT",
    ),
    Padrao(
        chave="recencia_planejada_fora_do_dono",
        regra="recência de par que conta fichas geradas e não executadas (exercício livre do Modo B)",
        # 2026-10-27 (M101/M101b, mapa §3.5): a escolha do exercício livre é de
        # `generation/mode_b._resolve_free_exercise` sobre o ranking único
        # (`domain/equipment_ranking`); a recência com as fichas planejadas
        # mora em `equipment_recommender.load_user_recency(include_planned=)`.
        # Outro consumidor pedindo essa recência seria uma segunda regra de
        # "qual equipamento usar".
        regex=re.compile(r"\binclude_?[pP]lanned\b"),
        donos=("api/app/equipment_recommender.py", "api/app/generation/mode_b.py"),
        inventario="M101",
    ),
    Padrao(
        chave="exercicio_livre_fora_do_dono",
        regra="o que é o item de exercício livre do Modelo (grupo sem par) decidido fora do dono",
        # 2026-10-27 (M101c, mapa §3.5): `domain/free_exercise_item.is_free_exercise_item`
        # decide; o planejado (prévia, metodologia, dashboard) soma pelo mesmo dono
        # (`technique_volume.free_exercise_primary_sets`). Outro predicado
        # (`def _is_free_exercise_item` ou `not item.mapping_id and not
        # item.equipment_id`) seria uma segunda definição de "livre".
        regex=re.compile(
            r"def\s+_?is_free_exercise_item\b"
            r"|not\s+\w+(?:\.get\([\"']|\.)mapping_id[\"']?\)?\s+and\s+not\s+\w+(?:\.get\([\"']|\.)equipment_id"
        ),
        donos=("api/app/domain/free_exercise_item.py",),
        inventario="M101c",
        linguagens=frozenset({"py"}),
    ),
    Padrao(
        chave="bom_para_paralelo",
        regra="instante \"Bom para\" (grupo pronto para novo estímulo) somando horas restantes/estimadas a um instante fora do dono",
        # 2026-10-17 (coluna "Bom para" da prontidão por grupo): o instante é de
        # `recovery_projection.ready_forecast` (primeiro minuto READY da projeção),
        # e a API o entrega pronto em `ready_at`. Proibido refazer como
        # `agora + remaining_hours` (Python ou TS): daria 100% de tempo, não o
        # READY da coluna "Estado". Proibição pura: o dono busca pela projeção.
        # A guarda é por linha: um `timedelta(` quebrado em várias linhas escapa
        # (limite do mecanismo, igual aos outros padrões).
        regex=re.compile(
            r"timedelta\(\s*(?:hours|minutes|seconds)\s*=[^)\n]*\b(?:remaining_hours|estimated_recovery_hours|estimated_hours)\b"
            r"|\b(?:remaining_hours|estimated_recovery_hours)\b[^;\n]*\*\s*"
            r"(?:3600e3|36e5|3\.6e6|3_?600(?:_?000)?|60\s*\*\s*60)(?![\w.])"
            r"|(?<![\w.])(?:3600e3|36e5|3\.6e6|3_?600(?:_?000)?)\s*\*[^;\n]*\b(?:remaining_hours|estimated_recovery_hours)\b"
        ),
        donos=("api/app/intelligence/recovery_projection.py",),
        inventario="BOM-PARA",
    ),
    Padrao(
        chave="diario_nutricao",
        regra="constante do diário, da semana ou do congelamento redefinida com número fora do dono (teto de itens, "
        "faixa do item, escala de fome, faixa de água, densidade padrão, base por 100, tolerâncias de Atwater/rótulo, "
        "banco e aderência da semana, lag do congelamento) ou tabela de medidas caseiras reescrita",
        # 2026-10-17 (nutrição L4): donos `meal_math` (quantidade, base, Atwater,
        # medidas caseiras), `diary` (entrada da refeição e do dia),
        # `weekly_budget` (banco, aderência) e `daily_target` (congelamento). A rota,
        # a tela e o worker do parse (L5) importam o nome; o FE lê o JSON gerado
        # (`meal_max_items`, `water_ml_range`, `hunger_levels`, `generic_portions`).
        regex=re.compile(
            r"\b(?:MEAL_MAX_ITEMS|ITEM_AMOUNT_MAX|HUNGER_SCALE|WATER_ML_RANGE|DEFAULT_DENSITY_G_PER_ML"
            r"|PER_100G_REFERENCE|ATWATER_TOLERANCE_SHARE|LABEL_SERVING_TOLERANCE_SHARE|MAX_DAILY_CARRYOVER_SHARE"
            r"|MAX_WEEKLY_BANK_SHARE|ADHERENCE_\w+|MIN_LOGGED_DAYS_FOR_WEEK|TARGET_FREEZE_LAG_DAYS)"
            r"\s*(?::[^=\n]+)?=\s*[\d(\[]"
            # Medidas caseiras reescritas: tabela (`colher_sopa: 15`) ou `GENERIC_PORTIONS = {`.
            r"""|\b(?:colher_(?:sopa|sobremesa|cha)|xicara)["']?\s*:\s*\d"""
            r"|\bGENERIC_PORTIONS\s*(?::[^=\n]+)?=\s*[{(\[]"
        ),
        donos=(
            "api/app/domain/nutrition/meal_math.py",
            "api/app/domain/nutrition/diary.py",
            "api/app/domain/nutrition/weekly_budget.py",
            "api/app/domain/nutrition/daily_target.py",
        ),
        inventario="NUT-L4",
    ),
    Padrao(
        chave="soma_nutrientes",
        regra="soma de nutrientes de itens, refeições ou dias (`sum(`/`SUM(`/`.reduce(`/`+=` sobre energy_kcal, "
        "protein_g, carbohydrates_g, total_fat_g, fiber_g, sodium_mg ou as metas da 079) ou escala por 100 g/ml "
        "(`/ 100`, `* 0.01`) fora do dono",
        # 2026-10-17 (nutrição L4): o total incompleto (`meal_math.NutritionTotals`,
        # decisão 1 do L4) só existe se a soma passa pelo dono; uma soma em SQL ou no
        # FE trataria o NULL como 0 em silêncio. Semana e balanço: `weekly_budget`.
        # O dashboard (L7) agrega pelo motor ou lê os totais gravados com a lista de
        # campos incompletos. Só no escopo de nutrição (`/ 100` é genérico demais).
        regex=re.compile(
            r"(?:\b(?:sum|SUM|reduce)\s*\(|\+=)[^\n]*\b(?:energy_kcal|protein_g|carbohydrates_g|total_fat_g|fiber_g"
            r"|sodium_mg|kcal_target|carbs_g|fat_g)\b"
            r"|\b(?:energy_kcal|protein_g|carbohydrates_g|total_fat_g|fiber_g|sodium_mg)\b\s*\+="
            # Acumulação explícita (`total = total + item.energy_kcal`, revisão do guardião).
            r"""|=\s*[\w.\[\]'"]+\s*\+\s*\(?[\w.\[\]'"?]*\b(?:energy_kcal|protein_g|carbohydrates_g|total_fat_g"""
            r"|fiber_g|sodium_mg)\b"
            r"|/\s*100(?:\.0+)?(?![.\d\w])|\*\s*0?[.,]01(?!\d)"
        ),
        # `macros.Macros.plus` é a soma de macros do próprio motor (usada por `meal_math.sum_macros`).
        donos=(
            "api/app/domain/nutrition/meal_math.py",
            "api/app/domain/nutrition/weekly_budget.py",
            "api/app/domain/nutrition/macros.py",
        ),
        inventario="NUT-L4",
        escopo=ESCOPO_NUTRICAO,
    ),
    Padrao(
        chave="cota_ia_nutricao",
        regra="cota, job type ou preço do uso de IA da nutrição fora do dono: constante de `ai_usage` redefinida "
        "(`*_DAILY_QUOTA`, `DAILY_QUOTA`, `FEATURE_JOB_TYPE`, tabela de preços, unidades do custo), job type "
        "`nutrition-meal-parse`/`nutrition-label-ocr` em literal ou cota comparada com número",
        # 2026-10-18 (nutrição L5a): a rota do L5b/L6 reserva pela cota do dono
        # (`nutrition_ai_quota.open_usage` -> `ai_usage.quota_status`), importa o
        # nome do job type do dono (`from .domain.nutrition.ai_usage import
        # MEAL_PARSE_JOB_TYPE`) para `async_jobs.create_job(MEAL_PARSE_JOB_TYPE, ...)`
        # — sem `ai_usage.` na frente, que o teste do M73 não resolve — e a chave de
        # `async_jobs.JOB_TYPE_LAMBDA` é `ai_usage.MEAL_PARSE_JOB_TYPE`; o worker fecha
        # com o custo de `ai_usage.estimate_cost_usd`. O FE lê a cota da API.
        regex=re.compile(
            r"\b(?:MEAL_PARSE_DAILY_QUOTA|LABEL_PHOTO_DAILY_QUOTA|DAILY_QUOTA|FEATURE_JOB_TYPE"
            r"|MODEL_PRICES_USD_CENTS_PER_MTOKEN|MODEL_LONG_PROMPT_PRICES|HAIKU_LONG_PROMPT_THRESHOLD_TOKENS"
            r"|TOKENS_PER_PRICE_UNIT|CENTS_PER_USD|COST_USD_DECIMAL_PLACES)"
            r"\s*(?::[^=\n]+)?=\s*(?:[\d({\[]|MappingProxyType|frozenset)"
            r"""|["'`]nutrition-(?:meal-parse|label-ocr)["'`]"""
            # Cota comparada ou atribuída a número (`cota_usada >= 30`, `remainingQuota === 0`,
            # `quota: int = 10`, `"quota": 30`). "cota"/"quota" como parte do nome (snake ou
            # camelCase), não dentro de outra palavra (`cotacao`).
            # A anotação (`: int`) só vale antes de `=`; o `:` de objeto/dict só com número > 0
            # (`{"quota_used": 0}` e `useState({ usedQuota: 0 })` são inicialização, não cota).
            r"""|(?:\b(?i:(?:\w*_)?(?:quota|cota)(?:_\w*)?)\b|\b\w*(?:Quota|Cota)\w*)["'\])]*\s*"""
            r"""(?::[^=<>!\n]+(?==))?(?:[<>]=?|[!=]?==?)\s*\d"""
            r"""|(?:\b(?i:(?:\w*_)?(?:quota|cota)(?:_\w*)?)\b|\b\w*(?:Quota|Cota)\w*)["']?\s*:\s*[1-9]"""
            # Tabela de preço por modelo copiada com outro nome (OpenAI e, desde o
            # §74, Claude: inclusive a faixa de prompt longo do Haiku).
            r"""|["'`](?:gpt|claude)-[\w.\-]+["'`]\s*:\s*[\d(\[{]|\bModelPrice\("""
        ),
        donos=("api/app/domain/nutrition/ai_usage.py",),
        inventario="NUT-L5A",
    ),
    Padrao(
        chave="cota_ia_copia_nutricao",
        regra="limite diário ou preço de IA paralelo no escopo de nutrição: `*_DAILY_LIMIT`/`*_PER_DAY`/"
        "`LIMITE_DIARIO` com número, cota ou preço lidos de variável de ambiente, `OPENAI_PRICE_*` ou o custo da "
        "periodização (`periodization.logs`)",
        # 2026-10-18 (nutrição L5a, revisão do guardião): os precedentes que o
        # L5b/L6 copiaria usam "limit"/"per_day" e env (`DAILY_GENERATION_LIMIT`,
        # `ENV_MAX_PER_DAY` dos cards, `OPENAI_PRICE_*_PER_MTOKEN` da periodização,
        # cuja política de preço é a oposta: ver o mapa §54). Só no escopo de
        # nutrição: fora dele esses nomes são de outros donos. Proibição pura.
        regex=re.compile(
            # "por dia" é unidade comum na nutrição (kcal, g ou kg por dia): só o teto de chamadas.
            r"(?i:\b\w*(?:DAILY_LIMIT|MAX_PER_DAY|(?:PARSE|OCR|QUOTA|COTA|CALLS|REQUESTS)\w*PER_DAY|LIMITE_DIARIO)\w*)"
            r"\s*(?::[^=<>!\n]+(?==))?=\s*\d"
            r"""|(?i:\bos\.(?:environ\.get|getenv)\(\s*["'][^"']*(?:QUOTA|COTA|PER_DAY|DAILY|PRICE))"""
            r"|(?i:\bprocess\.env\.\w*(?:QUOTA|COTA|PER_DAY|DAILY|PRICE))"
            r"|\bOPENAI_PRICE_\w+|\bperiodization\.logs\b"
        ),
        donos=("api/app/domain/nutrition/ai_usage.py",),
        inventario="NUT-L5A",
        escopo=ESCOPO_NUTRICAO,
    ),
    Padrao(
        chave="leitura_refeicao_ia",
        regra="constante da leitura da refeição por IA redefinida fora do dono (teto do texto, candidatos por item, "
        "tamanho do nome, faixa da confiança, vocabulário de unidades da IA, campos/chave da estimativa, estados que "
        "viram candidato a cadastro) ou a chave da estimativa (`estimated_per_100g`) em literal",
        # 2026-10-18 (nutrição L5b): o prompt, o schema, o worker e a rota importam
        # de `meal_parse` (o schema monta o enum de `AI_UNITS` e a chave de
        # `ESTIMATE_KEY`); os limiares do casamento são de `food_text`
        # (`catalogo_alimentos`, `nota_matching`). A tela lê o bloco `meal_parse`
        # do JSON gerado e decide pelos campos do resultado, não por número.
        regex=re.compile(
            # Com prefixo também (`MEAL_PARSE_TEXT_MAX_CHARS = 1000` no FE), revisão do guardião.
            r"\b\w*(?:TEXT_MAX_CHARS|CANDIDATES_PER_ITEM|ITEM_LABEL_MAX_CHARS|CONFIDENCE_RANGE)"
            r"\s*(?::[^=\n]+)?=\s*[\d(\[]"
            r"|\b\w*(?:AI_UNITS|ESTIMATE_FIELDS|ESTIMATE_REQUIRED_FIELDS|ESTIMATE_KEY|CATALOG_CANDIDATE_STATUSES"
            r"|UNSTATED_QUANTITY_UNIT|ESTIMATE_BASIS)"
            r"""\s*(?::[^=\n]+)?=\s*(?:[(\[{"'`]|f["']|frozenset|new Set)"""
            r"""|["'`]estimated_per_\d+g["'`]"""
        ),
        donos=("api/app/domain/nutrition/meal_parse.py",),
        inventario="NUT-L5B",
    ),
    Padrao(
        chave="foto_rotulo_nutricao",
        regra="foto do rótulo fora do dono: constante de `label_photo` redefinida (limite de bytes e em base64 "
        "`LABEL_PHOTO_MAX_BASE64_CHARS`, tipo aceito, extensão, prefixo S3, retenção, imagens por leitura, borda e "
        "qualidade da redução, `detail`), o prefixo `nutrition-labels` em literal (com ou sem a barra) ou 1,5 MiB à "
        "mão (`1572864`, `1.5 * 1024`, `1024 * 1024 * 1.5`, `1536 * 1024`)",
        # 2026-10-18 (nutrição L6): a rota (413/415), o armazenamento (chave), o
        # worker (leitura e `detail`) e a tela (redução por canvas) consomem o dono
        # `domain/nutrition/label_photo` — a tela pelo bloco `nutrition.label_photo`
        # do JSON gerado. A chave sai de `label_photo_key`; o IAM e o lifecycle do
        # prefixo (`.tf`, que a guarda não varre) têm teste de paridade.
        regex=re.compile(
            r"\b(?:LABEL_PHOTO_(?:MAX_BYTES|MAX_MIB|MAX_BASE64_CHARS|CONTENT_TYPE|ACCEPTED_CONTENT_TYPES|EXTENSION"
            r"|S3_PREFIX|RETENTION_DAYS|IMAGES_PER_READING)|RESIZE_(?:MAX_EDGE_PX|JPEG_QUALITY(?:_PERCENT)?)"
            r"|LABEL_IMAGE_DETAIL)"
            r"""\s*(?::[^=\n]+)?=\s*(?:[\d(\[{"'`]|(?:int|float|Number)\(|frozenset|new Set)"""
            # Prefixo S3 com ou sem a barra (`"nutrition-labels"` + f"{PREFIX}/..."); o id da
            # regra de lifecycle (`nutrition-labels-expire-30d`) não casa.
            r"""|["'`]nutrition-labels(?=[/"'`])"""
            # 1,5 MiB escrito à mão (revisão do guardião: sem falso positivo no repo inteiro).
            r"|(?<![\w.])1_?572_?864(?![\w.])|\b1[.,]5\s*\*\s*1024\b|\b1024\s*\*\s*1024\s*\*\s*1[.,]5\b"
            r"|\b1536\s*\*\s*1024\b"
        ),
        donos=("api/app/domain/nutrition/label_photo.py",),
        inventario="NUT-L6",
    ),
    Padrao(
        chave="foto_rotulo_numeros",
        regra="parâmetro da redução ou do upload da foto do rótulo escrito à mão (borda de 1280 px, qualidade do "
        "JPEG, tipo `image/jpeg`, `detail` da imagem) na nutrição ou no `image-resize.ts`",
        # 2026-10-18 (nutrição L6): `resizeImageForUpload` recebe borda, qualidade e
        # tipo de quem chama (`LabelPhotoUpload`, que lê o JSON gerado); nenhum
        # default numérico no helper nem na tela; a rota, o armazenamento e o worker
        # usam o tipo e o `detail` do dono. Só no escopo de nutrição e no helper:
        # `quality`/`1280`/`"high"` são genéricos demais para o repo inteiro.
        regex=re.compile(
            r"(?<![\w.])1_?280(?![\w.])"
            # Qualidade da imagem (sozinha ou com prefixo de imagem); `DataQuality`/`MIN_DATA_QUALITY`
            # são de outros donos da nutrição e não casam (revisão do guardião, 2ª rodada).
            r"""|(?<![A-Za-z0-9_])(?i:(?:jpe?g|image|img|resize|canvas)_?)?(?i:quality|qualidade)\w*["'\]]*\s*[:=]\s*"""
            r"(?:0?[.,]\d|[1-9]\d\b)"
            # O teto do corpo em base64 (`LABEL_PHOTO_MAX_BASE64_CHARS`) copiado com outro nome.
            r"|(?<![\w.])2_?097_?152(?![\w.])"
            r"|\btoBlob\(.*,\s*0?[.,]\d"
            r"|(?<![\w.,])0?\.85(?!\d)"
            r"""|(?i:max_?edge)\w*["'\]]*\s*[:=]\s*\d"""
            r"""|["'`]image/jpeg["'`]"""
            r"""|\bdetail\w*["']?\s*[:=]\s*["'](?:high|low|auto)["']"""
            r"""|\bImageInput\([^)\n]*["'](?:high|low|auto)["']"""
        ),
        donos=("api/app/domain/nutrition/label_photo.py",),
        inventario="NUT-L6",
        escopo=(*ESCOPO_NUTRICAO, "frontend/src/lib/image-resize.ts"),
    ),
    Padrao(
        chave="fallback_registro_serie",
        regra="piso de séries (`sets_count ?? 1`, `warmup_sets_count || 1`) ou valor inicial do registro da série "
        "(`parseNumericTarget(...) ?? 10` reps / `?? 20` kg) escrito à mão no Modo Treino",
        # 2026-10-19 (Evolução ZEPP, PR B2): o piso é `prescription_defaults.MIN_SETS_COUNT` e o ponto de
        # partida do campo é `SET_LOGGER_FALLBACK_REPS`/`SET_LOGGER_FALLBACK_LOAD_KG`; o FE lê
        # `motor-constants.generated.json` via `prescription-defaults.ts` e os contadores saem de
        # `buildAdvancedExerciseCounters` (núcleo do Modo Treino). `?? 0` de soma não casa.
        # 2026-10-19 (PR B3): também o piso em `max(1, <séries/alvo/restantes>)` (a continuação sintética da
        # troca de equipamento usa `Math.max(MIN_SETS_COUNT, remainingSets)`). `Math.max(1, lista.length)`
        # (divisor da barra de progresso) não casa. Também a ordem invertida (`Math.max(remainingSets, 1)`) e
        # `remaining…`/`…target… ?? N` / `|| N`. Fica de fora, conscientemente: nome sem sets/target/remaining
        # (`Math.max(1, faltam)`), expressão entre parênteses no 2º argumento e o ternário `x < 1 ? 1 : x`.
        regex=re.compile(
            r"\b\w*sets_count\s*(?:\?\?|\|\||\s+or\s)\s*[1-9]"
            r"|\bmax\(\s*[1-9]\s*,\s*[\w.]*(?:[Ss]ets|[Tt]arget|[Rr]emaining)"
            r"|\bmax\(\s*[\w.]*(?:[Ss]ets|[Rr]emaining)\w*\s*,\s*[1-9]\s*\)"
            r"|\b\w*(?:[Rr]emaining|[Tt]arget)\w*\s*(?:\?\?|\|\|)\s*[1-9]"
            # Um nível de parênteses dentro da chamada (`parseNumericTarget(String(x)) ?? 10`).
            r"|\bparseNumericTarget\((?:[^()\n]|\([^()\n]*\))*\)\s*\?\?\s*\d"
            r"|\bSET_LOGGER_FALLBACK_\w+\s*(?::[^=\n]+)?=\s*\d"
        ),
        donos=(DONO_PRESCRICAO,),
        inventario="E8, ZEPP-B2, ZEPP-B3",
    ),
    Padrao(
        chave="fallback_registro_serie_cadeia",
        regra="valor inicial de reps/carga escrito à mão no fim de uma cadeia de `??` no Modo Treino "
        "(`lastSet?.reps ?? 10`, `suggested_load_kg ?? 20`)",
        # 2026-10-19 (PR B2, revisão do guardião): a forma sem `parseNumericTarget` que o treino livre já teve.
        # Só no Modo Treino e no seu núcleo: `?? 10` em reps é genérico demais para o repo inteiro.
        regex=re.compile(r"\b\w*(?:reps|weight_kg|load_kg)\s*\?\?\s*\d{2}"),
        donos=(DONO_PRESCRICAO,),
        inventario="E8, ZEPP-B2",
        linguagens=frozenset({"ts"}),
        escopo=("frontend/src/app/(fullscreen)/modo-treino/", "frontend/src/lib/workout-mode-*"),
    ),
    Padrao(
        chave="modo_treino_pela_ficha",
        regra="item/ficha com configuração avançada (técnica, aquecimento ou ajuste) decidido à mão no Modo Treino, "
        "fora de `exerciseHasAdvancedConfig`/`resolveWorkoutModeFromPlan`",
        # 2026-10-19 (Evolução ZEPP, PR B4): o modo do treino sai da ficha, sem escolha "normal/avançado". O
        # predicado `advanced_technique != null || warmup_reps != null || feeler_set_reps != null` (em qualquer
        # ordem) mora só no núcleo; tela e relógio chamam a função. Uma comparação isolada (o alvo de
        # aquecimento em `buildAdvancedExerciseCounters`, o rótulo de reps da tela) não casa. Também trava a volta
        # do filtro legado do backend (`has_advanced_technique`, que conta pico de contração) no Modo Treino.
        # Escapam, conscientemente: a disjunção quebrada em várias linhas e a forma truthy (`a.warmup_reps || …`).
        regex=re.compile(
            r"\b(?:advanced_technique|warmup_reps|feeler_set_reps)\s*!==?\s*(?:null|undefined)\s*\|\|"
            r"[^\n]*\b(?:advanced_technique|warmup_reps|feeler_set_reps)\s*!==?\s*(?:null|undefined)"
            r"|\bhas_advanced_technique\b"
        ),
        donos=("frontend/src/lib/workout-mode-advanced-stage.ts",),
        inventario="ZEPP-B4",
        linguagens=frozenset({"ts"}),
        escopo=("frontend/src/app/(fullscreen)/modo-treino/", "frontend/src/lib/workout-mode-*"),
    ),
    Padrao(
        chave="medida_serie",
        regra="medida da série (`reps`/`duration`/`max_reps`) comparada ou atribuída à mão (`measure == 'duration'`, "
        "`=== 'max_reps'`, `es.measure = 'duration'` em SQL, `measure === 'reps'`) fora do dono",
        # 2026-10-20 (medidas de série, Lote T1): o vocabulário, `is_rep_based`, os limites e as validações moram em
        # `domain/set_measures.py`; SQL usa `sql_is_rep_based`/`sql_measure_expr`; o FE lê o bloco `set_measures`
        # de `motor-constants.generated.json` via `set-measures.ts` (`isRepBased`, `MEASURE_OPTIONS`). Casa o literal
        # em comparação/atribuição (`== 'duration'`, `= 'max_reps'`, `'duration' in`) e `measure == 'reps'`; não
        # casa o acesso pelo Enum (`SetMeasure.DURATION`), as funções do dono nem chaves de JSON (`"duration":`).
        regex=re.compile(
            r"""(?:===?|!==?|=)\s*['"](?:duration|max_reps)['"]"""
            r"""|['"](?:duration|max_reps)['"]\s*(?:===?|!==?|in\b)"""
            r"""|\bmeasure\b[^=!\n]{0,3}(?:===?|!==?)\s*['"]reps['"]"""
        ),
        donos=("api/app/domain/set_measures.py", "frontend/src/lib/set-measures.ts"),
        inventario="T1 (medidas de série)",
    ),
    Padrao(
        chave="rpe_alvo_protocolo",
        regra="RPE-alvo do protocolo da técnica (9/10) escrito à mão: `target_rpe`/`targetRpe`/`rpe_target_effective` "
        "atribuído ou comparado a 9 ou 10 fora do dono",
        # 2026-10-24 (Modo Intensivo, PR 1): `training_safety.protocol_target_rpe` decide 10 (par libera a falha) ou
        # 9; a prescrição leva `target_rpe` e a API grava `rpe_target_effective`. Tela, rota e SQL só leem.
        regex=re.compile(
            r"""\b(?:\w*target_rpe\w*|targetRpe|rpe_target_effective)['"]?\]?\s*(?:===?|!==?|:|=)\s*(?:9|10)\b""",
            re.IGNORECASE,
        ),
        donos=("api/app/domain/training_safety.py",),
        inventario="Modo Intensivo (§62)",
    ),
    Padrao(
        chave="serie_pendente_a_mao",
        regra="pendência da série (`reps_pending`) comparada a literal 0/1 à mão fora do dono "
        "(`es.reps_pending = 0` em SQL, `reps_pending == 1`)",
        # 2026-10-24 (Modo Intensivo, PR 1): o predicado é `set_types.sql_set_confirmed` (SQL) e
        # `is_pending_set`/`confirmed_sets` (Python). Guarda dedicada: `test_guarda_series_pendentes.py`.
        regex=re.compile(r"""\breps_pending\b[`'"]?\]?\s*(?:===?|!==?|<>|=)\s*[01]\b"""),
        donos=("api/app/set_types.py",),
        inventario="Modo Intensivo (§62)",
    ),
    Padrao(
        chave="pausa_padrao_rest_pause",
        regra="pausa padrão do rest-pause (15 s) escrita à mão: `pause_seconds`/`pauseSeconds`/duração da técnica "
        "atribuída, defaultada (`??`, `||`, `or`) ou comparada a 15 fora do dono",
        # 2026-10-25 (§64.1): `progression_engine.REST_PAUSE_DEFAULT_PAUSE_SECONDS` via
        # `technique_pause_seconds`; o snapshot leva `pause_seconds` pronto e o espelho do tempo de treino
        # lê `time_estimate.rest_pause_default_pause_seconds` do JSON gerado.
        regex=re.compile(
            r"""\b(?:\w*pause_seconds\w*|\w*pauseSeconds\w*|techniqueDurationSeconds|\w*technique_duration_seconds)"""
            r"""['"]?\]?\)?\s*(?:\?\?|\|\||\bor\b|===?|!==?|:|=)\s*15\b""",
            re.IGNORECASE,
        ),
        donos=("api/app/domain/progression_engine.py",),
        inventario="Modo Intensivo (§64.1)",
    ),
    Padrao(
        chave="motivo_manter_carga",
        regra="motivo do MANTER da carga da próxima série escrito à mão (\"Repetições e RPE dentro do esperado\", "
        "\"mantenha a carga e busque o RPE-alvo\"...) fora do dono",
        # M96 (2026-10-25, §65): o motivo do MANTER sai do status de esforço da
        # mesma série (`next_set_load.SetFacts.intensity_status`); tela, prompt
        # e lambdas leem `reason_label` pronto. Proibição pura.
        regex=re.compile(
            r"""["']Repetições e RPE dentro do esperado|mantenha a carga e (?:busque o RPE-alvo|pare antes da falha)"""
        ),
        donos=("api/app/domain/next_set_load.py",),
        inventario="M96 (§65)",
    ),
    Padrao(
        chave="padrao_laboral",
        regra="padrão do exercício laboral (séries/reps/carga do catálogo, colunas `laboral_default_*`) lido ou "
        "resolvido à mão, ou filtro `is_laboral = 0/1` escrito à mão em SQL, fora do dono",
        # M100 (2026-10-27, §72): `domain/laboral.py` resolve o padrão (catálogo > linha > fallback) e monta as
        # colunas do SELECT (`sql_catalog_columns`); `laboral_catalog.py` é a leitura. Congelamento, rascunho,
        # admin e leitura da ficha consomem o valor pronto. Só `api/app`: o FE (tipos/form do admin) é contrato.
        regex=re.compile(r"""\blaboral_default_(?:sets|reps|load_kg)\b|\bis_laboral\s*=\s*[01]\b"""),
        donos=("api/app/domain/laboral.py", "api/app/laboral_catalog.py"),
        inventario="M100 (§72)",
        linguagens=frozenset({"py"}),
        escopo=("api/app/",),
    ),
    Padrao(
        chave="objetivo_treino_literal",
        regra="objetivo de treino escrito à mão: literal 'hypertrophy'/'strength' (Python ou SQL) fora do dono",
        # M97 F0 (2026-10-25, §66): vocabulário, default, precedência (item > Modelo > default) e
        # predicado SQL (`sql_goal_is`) moram em `domain/training_goal.py`. Rota, SQL, motor e
        # relatório comparam/gravam pelo enum ou pelo VALOR vindo do dono, nunca pelo literal.
        regex=re.compile(r"""["'`](?:hypertrophy|strength)["'`]"""),
        donos=("api/app/domain/training_goal.py",),
        inventario="M97 (§66)",
        linguagens=frozenset({"py"}),
        escopo=("api/app/",),
    ),
    Padrao(
        chave="objetivo_treino_filtro_sql",
        regra="filtro/JOIN de objetivo de treino escrito à mão em SQL (`<alias>.training_goal = ...`) fora do dono",
        # M97 F1 (2026-10-25, §66): a progressão, as exposições e as leituras do Modo Treino filtram o
        # objetivo SÓ por `sql_goal_is` (valor) e `sql_goal_join` (mesma linha de objetivo dos dois lados),
        # que montam o fragmento pela constante `TRAINING_GOAL_COLUMN`. `==`/`!=` de Python não casa.
        regex=re.compile(r"\b[A-Za-z_]\w*\.training_goal\s*=(?!=)"),
        donos=("api/app/domain/training_goal.py",),
        inventario="M97 (§66)",
        linguagens=frozenset({"py"}),
        escopo=("api/app/",),
    ),
    Padrao(
        chave="objetivo_treino_ramo",
        regra="ramo por objetivo de treino (`TrainingGoal.STRENGTH`/`HYPERTROPHY`) fora dos motores donos do perfil",
        # M97 F2 (2026-10-25, §66.2): o perfil de FORÇA (defaults, escada da progressão, conjunto da
        # próxima série, teto/técnicas, limites/tempo de sessão, peso no volume) mora SÓ nos donos;
        # rota, SQL, gerador, tela e relatório pedem o valor pronto pelo objetivo (`defaults_for`,
        # `ladder_for`, `ruleset_for_goal`, `compose(..., training_goal=)`, `hypertrophy_volume_weight`).
        regex=re.compile(r"\bTrainingGoal\.(?:STRENGTH|HYPERTROPHY)\b"),
        donos=(
            "api/app/domain/training_goal.py",
            DONO_PRESCRICAO,
            "api/app/domain/progression_engine.py",
            "api/app/domain/next_set_load.py",
            "api/app/domain/training_safety.py",
            "api/app/periodization/limits.py",
            "api/app/intelligence/methodology_volume.py",
            # M97 F4 (§66.4): o painel de força acompanha as sessões de FORÇA (seleção dos compostos).
            "api/app/intelligence/dashboard/strength_panel.py",
        ),
        inventario="M97 (§66.2)",
        linguagens=frozenset({"py"}),
        escopo=("api/app/",),
    ),
    Padrao(
        chave="deload_filtro_sql",
        regra="filtro de sessão de deload escrito à mão em SQL (`deload_source IS [NOT] NULL`) fora do dono",
        # M97 D (2026-10-25, §67): exposições, e1RM, recorde, sessão anterior do Modo Treino e insights
        # identificam/excluem a sessão de deload SÓ por `sql_is_deload`/`sql_not_deload` (e o período ativo
        # por `sql_period_overlaps`), montados pela constante `DELOAD_SOURCE_COLUMN`.
        regex=re.compile(r"(?i)\bdeload_source\s+IS\s+(?:NOT\s+)?NULL\b"),
        donos=("api/app/domain/deload.py",),
        inventario="M97 (§67)",
        linguagens=frozenset({"py"}),
        escopo=("api/app/",),
    ),
    Padrao(
        chave="deload_fator",
        regra="número de deload (fator de carga, RPE-alvo, séries, duração) escrito fora do dono",
        # M97 D (2026-10-25, §67): `DELOAD_LOAD_FACTOR`, `DELOAD_TARGET_RPE`, `DELOAD_SETS_REDUCTION_FRACTION`,
        # `DELOAD_MIN_SETS` e a duração do período moram em `domain/deload.py`; a API devolve os valores
        # prontos (`/deload-periods/current`, bloco `deload` do briefing) e a tela não escreve número.
        regex=re.compile(r"""(?i)\bdeload\w*\s*(?::[^=\n]+)?=\s*(?:Decimal\(\s*)?["']?\d"""),
        donos=("api/app/domain/deload.py",),
        inventario="M97 (§67)",
        escopo=("api/app/", "frontend/src/"),
    ),
    Padrao(
        chave="cardio_prescricao_paralela",
        regra=(
            "regra da prescrição de cardio do Modelo fora do dono (duração/aquecimento/recuperação/HIIT comparados "
            "a número ou multiplicados por número, tetos/mínimos CARDIO_*/HIIT_* com número, minutos x 60 próprio)"
        ),
        # M98 C0 (2026-10-26, §69): modos, campos, mínimos, tetos, padrões e o
        # tempo total moram em `domain/cardio_prescription.py`; a rota delega a
        # validação, o `time_estimate` soma `total_seconds` e o editor lê
        # `motor-constants.generated.json` (seção `cardio_prescription`).
        regex=re.compile(
            r"\bcardio_duration_minutes\b\)?\s*(?:[<>]=?|===?|!==?)\s*\d"
            r"|\b(?:cardio_duration_minutes|cardio_(?:warmup|cooldown)_minutes|hiit_(?:low|high)_seconds|hiit_rounds)"
            r"\b\)?\s*\*\s*\d"
            r"|\b(?:cardio_target_zone|hiit_(?:low|high)_zone|hiit_(?:low|high)_seconds|hiit_rounds"
            r"|cardio_(?:warmup|cooldown)_minutes)\b\)?\s*[<>]=?\s*\d"
            r"|\b(?:CARDIO_(?:DURATION|WARMUP|COOLDOWN|SESSION)|HIIT_(?:INTERVAL|ROUNDS))_\w+\s*(?::[^=\n]+)?=\s*\d"
            r"|\bSECONDS_PER_CARDIO_MINUTE\b"
        ),
        donos=("api/app/domain/cardio_prescription.py",),
        inventario="M98 C0 (§69)",
    ),
    Padrao(
        chave="cardio_padrao_sem_modo",
        regra=(
            "padrão de cardio lido sem o modo (`describe`/`summarize`/`total_seconds` sobre `DEFAULTS[...]` "
            "direto: o item vira Normal e o HIIT sai sem as séries) — use `default_prescription(modo)`"
        ),
        # M98 (2026-10-27, §69.7): o prompt `cardio-agent-v3` descrevia o HIIT padrão como
        # "aquec. + recuperação" porque `describe(DEFAULTS[HIIT])` não leva o `cardio_mode`.
        # O dono expõe `default_prescription(mode)`; ler campo a campo de `DEFAULTS[...]` continua livre.
        regex=re.compile(
            r"\b(?:describe(?:_short|_steps)?|summarize|total_seconds|total_minutes|validate|validation_error)"
            r"\(\s*(?:\w+\.)*DEFAULTS\["
        ),
        donos=("api/app/domain/cardio_prescription.py",),
        inventario="M98 (§69.7)",
        escopo=("api/app/", "frontend/src/"),
    ),
    Padrao(
        chave="zonas_fc_paralelas",
        regra='zona de FC em % da FCmáx escrita fora do dono ("N% da FCmáx", "N-M% FC", "max_hr * 0.x")',
        # M98 C0 (2026-10-26, §69): os percentuais do fallback por FCmáx manual
        # e a precedência Intervals -> FCmáx manual moram em `domain/hr_zones.py`.
        # O RAG `CARDIO.md` tinha os números à mão; na C3a (§69.4) foi reescrito com
        # placeholders `{{cardio.*}}` e a allowlist zerou.
        regex=re.compile(
            r"(?i)\b\d{2,3}\s?%\s*(?:d[ae]\s+)?(?:sua\s+)?(?:FC|frequ[êe]ncia\s+card[íi]aca)\s?m[áa]x"
            r"|\b\d{2}\s?(?:-|–|a|até)\s?\d{2,3}\s?%\s*(?:d[ae]\s+)?(?:sua\s+)?(?:FC|frequ[êe]ncia\s+card[íi]aca)\b"
            r"|\bmax_?hr\w*\s*\*\s*0?\.\d"
        ),
        donos=("api/app/domain/hr_zones.py",),
        inventario="M98 C0 (§69)",
        linguagens=frozenset({"py", "ts", "md"}),
    ),
    Padrao(
        chave="cardio_paralelo",
        regra=(
            "o que é cardio decidido fora do dono (`_is_cardio_activity`/`_CARDIO_PATTERNS` próprios, "
            "`LIKE '%cardio%'` em SQL)"
        ),
        # M98 C2 (2026-10-26, §69.3): `intervals_activity_kind` é o dono único de "o que é cardio";
        # a aderência do cardio filtra as atividades por ele, em Python. Nasceu com as cópias do
        # router do dashboard ao vivo e do SQL de `analytics.py`, zeradas na C4.
        regex=re.compile(r"\b_is_cardio_activity\b|\b_CARDIO_PATTERNS\b|(?i:LIKE\s+'%{1,2}cardio%{1,2}')"),
        donos=("api/app/intervals_activity_kind.py",),
        inventario="M98 C2 (§69.3)",
        linguagens=frozenset({"py"}),
        escopo=("api/app/",),
    ),
    Padrao(
        chave="aderencia_cardio_paralela",
        regra="limiar ou métrica da aderência do cardio (planejado × realizado) escrito fora do dono",
        # M98 C2 (2026-10-26, §69.3): limiares do status e as métricas moram em
        # `domain/cardio_adherence.py`; serviço, rota e tela só consomem o payload pronto.
        regex=re.compile(
            r"\b(?:ADHERENT|PARTIAL)_\w*_PCT\s*(?::[^=\n]+)?=\s*\d"
            r"|\b(?:time_completion_pct|target_zone_completion_pct|high_zone_completion_pct)\b\s*[<>]=?\s*\d"
        ),
        donos=("api/app/domain/cardio_adherence.py",),
        inventario="M98 C2 (§69.3)",
    ),
    Padrao(
        chave="envelope_cardio_paralelo",
        regra=(
            "teto ou limiar do envelope do agente de cardio (HIIT/semana, TSB, horizonte, HRV/FC de repouso, "
            "dia de perna, sessão intensa) escrito fora do dono"
        ),
        # M98 C3a (2026-10-26, §69.4): o envelope de interferência força × cardio mora em
        # `domain/cardio_envelope.py`; o agente, o prompt e o RAG só consomem/interpolam.
        regex=re.compile(
            r"\b(?:WEEKLY_(?:HIIT|CARDIO_SESSIONS)_\w*|HIIT_MIN_TSB|TSB_HIGH_RISK_BELOW|RECOMMENDATION_HORIZON_DAYS"
            r"|HRV_DROP_BAD_PCT|RESTING_HR_RISE_BAD_BPM|RECOVERY_SIGNAL_DAYS|HIGH_INTENSITY_MIN_SECONDS"
            r"|CONTINUOUS_SESSION_MAX_MINUTES)\s*(?::[^=\n]+)?=\s*-?\d"
            r"|\bBAND_(?:MAX_ZONE|WEEKLY_MINUTES_MAX|ALLOWS_HIIT)\s*(?::[^=\n]+)?=\s*\{"
            r"|\b(?:form_tsb|tsb)\b\s*[<>]=?\s*-\d"
            r"|\bLEG_MUSCLE_GROUPS\s*(?::[^=\n]+)?=\s*\("
        ),
        donos=("api/app/domain/cardio_envelope.py",),
        inventario="M98 C3a (§69.4)",
    ),
    Padrao(
        chave="numeros_rag_cardio",
        regra='número de prescrição escrito à mão no RAG do cardio ("N minutos", "N vezes por semana", "N%")',
        # M98 C3a (2026-10-26, §69.4): `CARDIO.md` entra no prompt do agente de cardio; minutos,
        # frequência e zona vêm dos donos por placeholder `{{cardio.*}}` (`app/rag_placeholders.py`).
        regex=re.compile(r"(?i)\b\d+\s*(?:minutos|min\b|vezes|x\s+por\s+semana|sess[õo]es)|\b\d{2,3}\s?%"),
        donos=("api/app/rag_placeholders.py",),
        inventario="M98 C3a (§69.4)",
        linguagens=frozenset({"md"}),
        escopo=("api/privado/rag/markdown/CARDIO.md",),
    ),
    Padrao(
        chave="pesos_esforco_paralelos",
        regra=(
            "peso por zona, janela móvel ou fator do EPOC/PAI estimado escrito fora do dono "
            "(`EPOC_ZONE_WEIGHTS`/`PAI_ZONE_WEIGHTS`/`ROLLING_WINDOW_DAYS` próprios, `pai * 0.x`)"
        ),
        # M99 (2026-10-27, §70): o EPOC/PAI de 7 dias é ESTIMADO do tempo em zona do Intervals por
        # `domain/cardio_load.py`; rota, agente, relatório, dashboard e tela só consomem o valor pronto.
        regex=re.compile(
            r"\b(?:EPOC|PAI)_ZONE_WEIGHTS\s*(?::[^=\n]+)?=\s*[\(\[]"
            r"|\bROLLING_WINDOW_DAYS\s*(?::[^=\n]+)?=\s*\d"
            r"|(?i:\b(?:epoc|pai)\w*\s*(?:\*|\+=)\s*\d*\.\d)"
        ),
        donos=("api/app/domain/cardio_load.py",),
        inventario="M99 (§70)",
    ),
    Padrao(
        chave="pior_lado",
        regra="reps/RPE oficiais da série unilateral (pior lado) calculados fora do dono: `min`/`max`/"
        "`Math.min`/`LEAST`/`GREATEST` sobre `reps_left`/`reps_right`/`rpe_left`/`rpe_right`",
        # M102 (2026-10-27, §71): `domain/set_sides.py` decide reps = menor lado e RPE = maior lado
        # (lado vazio ignorado) na gravação; rota, SQL, relatório, prompt e tela só leem `reps`/`rpe`.
        regex=re.compile(
            r"(?i)\b(?:min|max|least|greatest)\s*\([^)\n]{0,80}\b(?:reps|rpe)_?(?:left|right)\b"
        ),
        donos=("api/app/domain/set_sides.py",),
        inventario="M102 (§71)",
    ),
)

PADROES_POR_CHAVE = {p.chave: p for p in PADROES}


# ---------------------------------------------------------------------------
# Allowlist (estado de origin/develop em 2026-10-06). SÓ PODE ENCOLHER.
# arquivo -> nº de ocorrências fora do dono. Comentário = ID do inventário · lote que remove.
# ---------------------------------------------------------------------------

ALLOWLIST: dict[str, dict[str, int]] = {
    "deload_filtro_sql": {},
    "deload_fator": {},
    # M98 C0 (§69): a validação da rota (`<= 0`) e a do editor (`<= 0`), o `minutos x 60` do
    # `time_estimate` e o `* PREMISES.seconds_per_cardio_minute` do espelho TS saíram neste PR.
    "cardio_prescricao_paralela": {},
    # M98 (§69.7): o único caso (`cardio_agent_ai._DEFAULT_*`) saiu neste PR.
    "cardio_padrao_sem_modo": {},
    # M98 C3a (§69.4): `CARDIO.md` reescrito com placeholders `{{cardio.*}}` e no índice do agente de cardio.
    "zonas_fc_paralelas": {},
    # M98 C2 (§69.3): cópias que já existiam; zeram na C4 (dashboard único).
    # M98 C4: zerada. O dashboard ao vivo (`_is_cardio_activity`/`_CARDIO_PATTERNS`)
    # saiu de `intervals_integration.py` e `analytics.py` classifica pelo dono.
    "cardio_paralelo": {},
    "aderencia_cardio_paralela": {},
    "envelope_cardio_paralelo": {},  # M98 C3a (§69.4): nasce vazia
    "numeros_rag_cardio": {},  # M98 C3a (§69.4): nasce vazia (CARDIO.md reescrito com placeholders)
    "pesos_esforco_paralelos": {},  # M99 (§70): nasce vazia
    "pior_lado": {},  # M102 (§71): nasce vazia
    "objetivo_treino_ramo": {},
    "objetivo_treino_filtro_sql": {},
    "objetivo_treino_literal": {
        # Outra semântica, permanente: TIPO de atividade do Intervals.icu (`STRENGTH = "strength"` e o
        # padrão de nome de atividade "strength"), não o objetivo de treino. Nunca cresce.
        "api/app/intervals_activity_kind.py": 2,
        # Outra semântica, permanente: nome do atributo `strength` (intensidade 0..1 do destaque do mapa
        # corporal) em `object.__setattr__(self, "strength", ...)`. Nunca cresce.
        "api/app/body_map_render.py": 1,
    },
    # A/C12: `analytics._SQL_RIR` (SQL) e o Epley/RIR inline de
    # `exercise_performance` migrados para `domain/e1rm.py` no Lote 3a.
    "epley": {},
    "rir": {},
    "dose_sem_rpe": {},  # A: defaults 8 / 7,0 do volume_service migrados no Lote 3a
    "dose_reduzida": {
        # B2/C10: REDUCED_DOSE_MULTIPLIER do progression_engine removido no Lote 2a
        # (o motor chama MuscleRecoveryService.reduced_sets).
        "api/app/intelligence/dashboard/metrics_registry.py": 2,  # outra semântica (share e delta de RPE); permanente
    },
    # B2: prompts de Modelo por IA e templates_engine v1 interpolam
    # `reduced_dose_prompt_rule()` desde o Lote 2a; o código morto (E14) saiu no Lote 5a.
    "dose_reduzida_texto": {},
    "sinais_expiram": {},  # B3: reset ">=72h" da projeção removido no Lote 2a
    # E8: desde o Lote 4c (26 -> 8) o dono é `domain/prescription_defaults.py`
    # (motores, legado ainda vivo, prompt da Periodização e placeholders da
    # ficha via JSON). O código morto (E14, 8 -> 0) saiu no Lote 5a.
    "defaults_prescricao": {},
    # E7/E8: Lote 4c - assinaturas 30/90, ponto médio e o 60 s do Modo Treino
    # migrados; o código morto (E14, 12 -> 0) saiu no Lote 5a.
    "descanso_default": {},
    "tetos_sessao": {},  # E9: Lote 4c - todo teto nomeado em `periodization/limits.py`
    "premissas_tempo": {},  # E7: Lote 4c - dono no backend, TS lê o JSON
    "numeros_prompt_volume": {},  # E10: Lote 4c - prompt interpolado, RAG sem "9 a 10 séries semanais"
    "limiar_fadiga": {},  # B4/D3: `dashboard_alert_service.py` apagado no Lote 2c
    "meta_fallback": {},
    # A: desde o Lote 3c os defaults de escrita moram em
    # `muscle_contribution_resolver.DEFAULT_WRITE_WEIGHTS` (catálogo legado,
    # fallback do VolumeService e admin FE via `motor-constants.generated.json`)
    # e a tela Tendência pondera pelo `stimulus_weight` cadastrado.
    "peso_secundario": {},
    # A/B10: `trained_at_sql()`/`with_trained_at()` em `time_windows.py` desde o Lote 2b.
    "trained_at": {},
    # E1/E2 (Lote 4a): `methodology_volume.weekly_normalization` (planejado da
    # semana) e `calendar_weekly_rate` (realizado em N dias) são os donos;
    # analytics, activation_window, volume_service e muscle_summary_service
    # consomem.
    "normalizacao_semanal": {
        "api/app/intelligence/dashboard/body_service.py": 1,  # pesagens por semana, não volume; permanente
    },
    "motor_legado_carga": {},  # C1/C2: removido no Lote 1a (progression_service.py apagado)
    "carga_percentual": {},  # C3: +5% removido no Lote 1a
    "rpe_texto_paralelo": {},  # C4: corte único em effective_intensity.clamp_rpe_text (Lote 1a)
    "percentual_de_carga": {},  # E14: `abc_workout_ai.py` apagado no Lote 5a
    # B10: "hoje"/"agora"/semana do usuario via `time_windows` (`user_today`,
    # `user_now`, `user_day_start_db`/`user_day_end_db`) desde o Lote 2b; o SQL
    # recebe a data como parametro em vez de `CURDATE()`.
    "relogio_sem_fuso": {},
    # C7: o FE calculava 20%/40%, 0,5 + 0,2·(n−1) e 0,8ⁱ em
    # `workout-mode-load-suggestions.ts`; desde o Lote 1b lê a API ou o espelho
    # com fatores exportados pelo dono (`motor-constants.generated.json`).
    "fatores_carga_sessao": {},
    # A: o FE tinha 4 cópias (`workout-mode-types.ts` 'dropset',
    # `exercise-set-edit.ts`, `workout-mode-technique.ts`, telas com
    # `set_type !== 'working'`) e `recovery_read_model.py` um CASE próprio
    # (11 ocorrências em 6 arquivos na origin/develop); desde o Lote 3b todos
    # consomem `set_types` / `set-types.ts`.
    "set_type_paralelo": {},
    # A: `progression_service.get_recent_progression` (MAX(best_weight_kg) por
    # nome) virou `get_recent_records` sobre `session_records` no Lote 3b.
    "recorde_paralelo": {},
    # COD-04: `_e1rm_reps_reliable` e `exposure_service.e1rm_reliable` chamam
    # `e1rm.is_reliable_reps`. Sobra o filtro em SQL do e1RM por grupo, que
    # interpola o teto do dono (não há como chamar o predicado Python no WHERE).
    "e1rm_confiavel_paralelo": {
        "api/app/analytics.py": 1,
    },
    # E3: a ficha (`MuscleActivationPreview`, `ExercisesTable`), o breakdown e o
    # resumo do Modo B (e os legados `mode_a_legacy`/`mode_b_legacy`) mandavam
    # `{mapping_id, sets_count}` sem a técnica; desde o Lote 4b usam o payload único.
    "payload_ativacao_sem_tecnica": {
        # `sets_count` JÁ É `planned_item_sets` (séries + equivalente da técnica,
        # Fase 5) no preview do Modelo; permanente.
        "api/app/workout_plan_templates.py": 1,
    },
    # E4: `MuscleActivationTable.tsx` tinha 0,9/0,5 do alvo e 6/9 sem alvo; desde
    # o Lote 4b o status vem de `volume_service.assess_activation_against_target`.
    "limiar_ativacao_fe": {},
    # E6: `muscle-activation-7d.ts` reavaliava a metodologia (por dia, fatia ->
    # bloco 1 nível); desde o Lote 4b só desenha `/analytics/methodology-realized`.
    "muito_abaixo_planejado": {},  # E4 (2026-10-07): só `volume_service.FAR_BELOW_PLANNED_RATIO`
    "metodologia_no_fe": {},
    # IA-06 (2026-10-08): prompts e RAG sem número de domínio à mão.
    "exposicoes_minimas": {},  # `report_ai` interpola PLATEAU_MIN_EXPOSURES
    # HABILIDADES_TREINO (HAB-042/043) e COSTAS remetem a `session_loads`.
    "fator_carga_trabalho_texto": {},
    "faixa_reps_prosa": {},  # HAB-007/053 e HIPERTROFIA remetem à prescrição
    "numeros_prompt_cardio": {},  # `cardio_agent_ai` interpola `cardio_policy`
    "tonelagem_convertida": {},  # 2026-10-10: `workout_summary_ai` interpola `format_tonnage`
    "fadiga_previa": {},  # F1 (2026-10-07): dono novo, nasce sem cópias
    "trilha_sem_contexto": {},  # F1 PR 2 (2026-10-07): nasce vazia (proibição pura)
    "adocao_carga_abaixo_do_piso": {},  # M77 (2026-10-11): nasce vazia
    "mesmas_reps_rpe_maior": {},  # P8 (2026-10-11): nasce vazia
    "dose_exigida_series": {},  # Lote 2/P3 RPE 10 (2026-10-11): nasce vazia (a cópia do avaliador saiu)
    "ficha_congelada_sql": {},  # Lote 2/P7 RPE 10 (2026-10-11): nasce vazia
    "queda_reps_entre_series": {},  # RPE 10 Lote 1/P2 (2026-10-11): nasce vazia
    "qualificacao_melhor_serie": {},  # Lote 3/P4 RPE 10 (2026-10-11): nasce vazia
    "excesso_reps_sobe_carga": {},  # Lote 3/P4 RPE 10 (2026-10-11): nasce vazia
    "plato_por_trilha": {},  # Lote 4/P6 RPE 10 (2026-10-11): nasce vazia
    "carga_topo_repetida": {},  # Lote 7/D1 RPE 10 (2026-10-11): nasce vazia
    "limiar_dor_muscular": {},  # set-eval-v5 (2026-10-12): nasce vazia
    "rotulo_status_serie": {},  # set-eval-v5 (2026-10-12): nasce vazia
    "carga_proxima_serie": {},  # set-eval-v6 (2026-10-12): nasce vazia
    "regra_carga_cadastrada": {},  # regras de carga R1 (2026-10-20): nasce vazia
    # E13 (2026-10-13): `intensity-density.ts` tinha a calibração inteira (era o
    # dono); desde este PR lê o JSON gerado por `domain/intensity_density.py`.
    "intensidade_densidade": {},
    "modo_contagem_volume": {},  # modo de contagem, Lote 1 (2026-10-16): nasce vazia
    "faixas_sem_modo": {},  # modo de contagem, Lote 2 (073, 2026-10-16): nasce vazia
    "modo_contagem_da_sessao": {},  # modo de contagem, Lote 4 (2026-10-16): nasce vazia
    # 2026-10-13: `technique-accounting.ts` copiava pesos, teto, RPEs e a tabela
    # de esforço à mão; desde este PR lê o JSON gerado pelos donos.
    "pesos_tecnica": {},
    "pontos_esforco": {},
    # Nutrição L1 (2026-10-16): o motor nasce sem cópias; nenhum consumidor ainda.
    "tmb_formula": {},
    "fator_atividade": {},
    "kcal_por_grama": {},
    "met_compendium": {},
    "navy_gordura": {},
    "densidade_energetica": {},
    "faixa_proteina": {},  # HYP-031/032 (RAG geral) conferidos: sem g/kg
    "ffmi_normalizado": {},
    "pausa_dieta": {},
    # 2026-10-17 (§50): nasce vazia. As 4 cópias do FE (fallbacks e o título do
    # bloco da aba admin) passaram a ler `UNCLASSIFIED_VOLUME_CLASS_LABEL`
    # (`volume-counting-mode.ts`, do JSON gerado) e o relatório importa do dono.
    "rotulo_sem_macro_grupo": {},
    "slot_por_nome": {},  # 2026-10-23: nasce vazia
    "chave_slot_fora_do_dono": {},  # 2026-10-24: nasce vazia (a tela de treinos lê `session.group`)
    "recencia_planejada_fora_do_dono": {},  # 2026-10-27 (M101): nasce vazia
    "exercicio_livre_fora_do_dono": {},  # 2026-10-27 (M101c): nasce vazia (as 2 cópias saíram no PR)
    "linhagem_modelo_fora_do_dono": {},  # 2026-10-24: nasce vazia (090; só o leitor lê, só o duplicate grava)
    "rotulo_estrategia": {
        # INTENÇÃO da metodologia de volume (§3.5: `MethodologyIntent`, "Abaixo
        # da faixa é esperado"), outro vocabulário que coincide na grafia com a
        # estratégia de nutrição. Pré-existentes e divergentes entre si ("Cutting"
        # × "Cutting (definição)"); inventário inicial, não cresce. Saem com o
        # backlog M84 (`INTENT_LABELS` em `intelligence/methodology_volume.py`,
        # exportado no bloco `methodology_volume`; mapa §3.5).
        "api/app/intelligence/report/markdown_renderer.py": 1,  # INTENT_LABELS["maintenance"]
        "frontend/src/lib/methodology-volume.ts": 2,  # METHODOLOGY_INTENT_OPTIONS (maintenance, cutting)
    },
    "kcal_treino_prioridade": {},
    "vocabulario_ia_serie": {},  # RPE 10 Lote 6/P9 (2026-10-11): nasce vazia (o FE saiu junto)
    # C13: rótulos de status de progressão com um dono só desde o Lote 3c
    # (`domain/progression_status_labels.py`; FE via `motor-constants.generated.json`).
    "rotulo_progressao": {
        # Tendência de VOLUME do grupo ("Estável" = volume sem variação), não
        # status de progressão por exercício; permanente.
        "frontend/src/app/(app)/volume-grupamentos/VolumeGrupamentosClient.tsx": 1,
    },
    # NUT-L0 (2026-10-15): 3 cópias em Python e 1 no TS saíram no mesmo PR; nasce vazia.
    "rotulo_site_medida": {},
    # NUT-L0 (2026-10-16, revisão do #556): a rota do PATCH /meta deixou de decidir a
    # origem (consome `source_on_manual_write`); nasce vazia.
    "origem_kcal": {},
    # NUT-L2A (2026-10-17): `PerfilClient.tsx` (`sexOptions`) e `users-admin-types.ts`
    # (`SEX_OPTIONS`) tinham 'M'/'F' + "Masculino"/"Feminino" à mão (4 + 4 ocorrências);
    # saíram neste PR (as duas telas leem `nutrition-constants.ts`); nasce vazia.
    "vocabulario_sexo": {},
    "nivel_atividade_e_altura": {},  # NUT-L2A (2026-10-17): nasce vazia (a tela lê o JSON gerado)
    "catalogo_alimentos": {},  # NUT-L3A (2026-10-17): nasce vazia (rota e tela importam do dono)
    "nota_matching": {},  # NUT-L3A (2026-10-17, revisão do guardião): nasce vazia
    "fontes_externas_alimentos": {},  # NUT-L3B (2026-10-17): nasce vazia (rota e cliente consomem o dono)
    "kj_kcal": {},  # NUT-L3B (2026-10-17, revisão do guardião): nasce vazia (só `meal_math` converte)
    "bom_para_paralelo": {},  # BOM-PARA (2026-10-17): nasce vazia (a tela só formata `ready_at`)
    "diario_nutricao": {},  # NUT-L4 (2026-10-17): nasce vazia (rotas e tela importam do dono / leem o JSON)
    "soma_nutrientes": {},  # NUT-L4 (2026-10-17): nasce vazia (totais, semana e balanço vêm do motor)
    "cota_ia_nutricao": {},  # NUT-L5A (2026-10-18): nasce vazia (o serviço do ai_usage_log consome o dono)
    "cota_ia_copia_nutricao": {},  # NUT-L5A (2026-10-18, revisão do guardião): nasce vazia (proibição pura)
    "leitura_refeicao_ia": {},  # NUT-L5B (2026-10-18): nasce vazia (prompt, schema, worker e tela consomem o dono)
    "foto_rotulo_nutricao": {},  # NUT-L6 (2026-10-18): nasce vazia (rota, armazenamento, worker e tela consomem o dono)
    "foto_rotulo_numeros": {},  # NUT-L6 (2026-10-18): nasce vazia (a tela lê o JSON gerado; o helper não tem default)
    # ZEPP-B2 (2026-10-19): nasce vazia (as 7 cópias de PlanExecutionScreen/FreeformScreen foram para o dono)
    "fallback_registro_serie": {},
    "fallback_registro_serie_cadeia": {},  # ZEPP-B2 (2026-10-19, revisão do guardião): nasce vazia
    "modo_treino_pela_ficha": {},  # ZEPP-B4 (2026-10-19): nasce vazia (a tela e o relógio chamam o núcleo)
    "medida_serie": {},  # T1 (2026-10-20): nasce vazia (só o dono, o schema e o espelho; consumidores nos PRs T2-T5)
    "rpe_alvo_protocolo": {},  # Modo Intensivo (2026-10-24): nasce vazia
    "serie_pendente_a_mao": {},  # Modo Intensivo (2026-10-24): nasce vazia
    "pausa_padrao_rest_pause": {},  # §64.1 (2026-10-25): nasce vazia
    "motivo_manter_carga": {},  # M96 (2026-10-25): nasce vazia
    "padrao_laboral": {},  # M100 (2026-10-27): nasce vazia
}


# ---------------------------------------------------------------------------
# Leitura do código sem comentários nem docstrings
# ---------------------------------------------------------------------------


def codigo_python(texto: str) -> list[str]:
    """Linhas do arquivo sem comentários `#` e sem strings-expressão
    (docstrings). Strings atribuídas/passadas (SQL, prompt) continuam."""
    linhas = texto.split("\n")
    try:
        arvore = ast.parse(texto)
    except SyntaxError:
        return linhas
    for node in ast.walk(arvore):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for numero in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                linhas[numero - 1] = ""
    try:
        for tok in tokenize.generate_tokens(io.StringIO(texto).readline):
            if tok.type == tokenize.COMMENT:
                linha, coluna = tok.start
                linhas[linha - 1] = linhas[linha - 1][:coluna]
    except (tokenize.TokenError, IndentationError):
        pass
    return linhas


# `/* ... */` só quando começa em início de linha ou depois de espaço/{;(, -
# evita confundir `accept="image/*"` com abertura de comentário.
_BLOCO_TS = re.compile(r"(?:^|(?<=[\s{;(,]))/\*.*?\*/", re.S | re.M)
# `//` só depois de início/espaço/pontuação - `https://` (depois de `:`) fica.
_LINHA_TS = re.compile(r"(?:^|(?<=[\s;{}(),]))//.*$")


def codigo_ts(texto: str) -> list[str]:
    sem_bloco = _BLOCO_TS.sub(lambda m: "\n" * m.group(0).count("\n"), texto)
    return [_LINHA_TS.sub("", linha) for linha in sem_bloco.split("\n")]


def _linguagem(caminho: Path) -> str | None:
    if caminho.suffix == ".py":
        return "py"
    if caminho.suffix in (".ts", ".tsx"):
        return "ts"
    if caminho.suffix == ".md":
        return "md"
    return None


@lru_cache(maxsize=None)
def _linhas_de_codigo(caminho: Path, linguagem: str) -> tuple[str, ...]:
    """Lê e limpa cada arquivo UMA vez para todos os padrões (a varredura
    parametrizada relê os mesmos ~1 mil arquivos dez vezes)."""
    texto = caminho.read_text(encoding="utf-8", errors="replace")
    if linguagem in ("md", "prompt"):
        return tuple(texto.splitlines())  # Markdown injetado em prompt / template de prompt: tudo conta
    return tuple(codigo_python(texto) if linguagem == "py" else codigo_ts(texto))


@lru_cache(maxsize=None)
def _arquivos_em_cache(raiz: Path) -> tuple[tuple[str, str, Path], ...]:
    return tuple(arquivos_varridos(raiz))


# M103 P1: a prosa dos prompts de sistema saiu dos literais `.py` para os templates
# do submódulo privado (`api/privado/prompts/<feature>/*.md`, lidos por
# `app/prompt_store.py`). Cada template é varrido COMO SE FOSSE o literal do módulo
# que o consome: todo padrão de `.py` vale (mesma `regex`, não a `regex_md` do RAG),
# o escopo do módulo consumidor vale para o template, e nenhum template é dono de
# regra (sem isenção). Pasta nova de prompt exige registrar o consumidor aqui
# (`test_todo_template_de_prompt_tem_consumidor`).
PROMPTS_PRIVADOS = "api/privado/prompts"
CONSUMIDOR_DO_PROMPT: dict[str, str] = {
    "api/privado/prompts/cardio_agent/": "api/app/cardio_agent_ai.py",
    "api/privado/prompts/exercise_description/": "api/app/exercise_description_ai.py",
    "api/privado/prompts/nutrition_label/": "api/app/nutrition_label_ai.py",
    "api/privado/prompts/nutrition_meal_parse/": "api/app/nutrition_meal_parse_ai.py",
    # M103 P2: insights, "Análise por IA", resumos e os fragmentos compartilhados.
    "api/privado/prompts/insights/": "api/app/insights_prompt.py",
    "api/privado/prompts/workouts_analysis/": "api/app/report_ai.py",
    "api/privado/prompts/exercise_summary/": "api/app/exercise_summary_ai.py",
    "api/privado/prompts/workout_summary/": "api/app/workout_summary_ai.py",
    "api/privado/prompts/goal_context/": "api/app/goal_prompt_context.py",
    "api/privado/prompts/prompt_user_data/": "api/app/prompt_user_data.py",
    # M103 P3: periodização (com a seção de cardio) e os dois prompts legados
    # de geração de ficha (Modelo v1 e seleção v2).
    "api/privado/prompts/periodization/": "api/app/periodization/context.py",
    "api/privado/prompts/periodization_cardio/": "api/app/periodization/cardio_plan.py",
    "api/privado/prompts/plan_templates/": "api/app/workout_plan_templates_engine.py",
    "api/privado/prompts/template_selector/": "api/app/intelligence/template_ai_selector_v2.py",
    # M103 P4: avaliação de série (IA por série do Modo Treino) e ilustração
    # do card de exercício.
    "api/privado/prompts/set_evaluation/": "api/app/exercise_set_evaluation_ai.py",
    "api/privado/prompts/exercise_card/": "api/app/exercise_card_prompt.py",
    # M103 P4 (varredura final): seção do Intervals.icu da ficha legada.
    "api/privado/prompts/intervals_wellness/": "api/app/workout_plan_ai.py",
}


def consumidor_do_prompt(rel: str) -> str | None:
    return next((modulo for pasta, modulo in CONSUMIDOR_DO_PROMPT.items() if rel.startswith(pasta)), None)


def arquivos_varridos(raiz: Path = RAIZ) -> list[tuple[str, str, Path]]:
    """(relativo, linguagem, caminho) de `api/app` (.py), `frontend/src`
    (.ts/.tsx), `api/privado/rag/markdown` (.md) e `api/privado/prompts` (.md,
    linguagem "prompt"; sem o README), sem testes, `node_modules`, `.next` nem
    `__pycache__`."""
    resultado = []
    bases = (
        ("api/app", ("*.py",)),
        ("frontend/src", ("*.ts", "*.tsx")),
        ("api/privado/rag/markdown", ("*.md",)),
        (PROMPTS_PRIVADOS, ("*.md",)),
    )
    for base, sufixos in bases:
        pasta = raiz / base
        if not pasta.exists():
            continue
        for sufixo in sufixos:
            for caminho in pasta.rglob(sufixo):
                rel = caminho.relative_to(raiz).as_posix()
                if _PASTAS_IGNORADAS.intersection(caminho.relative_to(raiz).parts):
                    continue
                if _TESTE_TS.search(rel):
                    continue
                if base == PROMPTS_PRIVADOS:
                    if caminho.name == "README.md":
                        continue
                    resultado.append((rel, "prompt", caminho))
                    continue
                resultado.append((rel, _linguagem(caminho), caminho))
    return sorted(resultado)


def _casa_escopo(item: str, rel: str) -> bool:
    if "*" in item:
        return fnmatch.fnmatchcase(rel, item)
    return rel == item or (item.endswith("/") and rel.startswith(item))


def _no_escopo(padrao: Padrao, rel: str) -> bool:
    if any(_casa_escopo(item, rel) for item in padrao.excluir):
        return False
    if padrao.escopo is None:
        return True
    return any(_casa_escopo(item, rel) for item in padrao.escopo)


def _varre(padrao: Padrao, rel: str, linguagem: str) -> bool:
    """O padrão olha este arquivo? Template de prompt = literal do consumidor."""
    if rel in padrao.donos:
        return False
    if linguagem == "prompt":
        if "py" not in padrao.linguagens:
            return False
        consumidor = consumidor_do_prompt(rel)
        return _no_escopo(padrao, rel) or (consumidor is not None and _no_escopo(padrao, consumidor))
    return linguagem in padrao.linguagens and _no_escopo(padrao, rel)


def ocorrencias(padrao: Padrao, raiz: Path = RAIZ) -> dict[str, list[tuple[int, str]]]:
    """arquivo -> [(linha, trecho)] das ocorrências FORA do(s) dono(s)."""
    achados: dict[str, list[tuple[int, str]]] = {}
    for rel, linguagem, caminho in _arquivos_em_cache(raiz):
        if not _varre(padrao, rel, linguagem):
            continue
        regex = padrao.regex_md if linguagem == "md" and padrao.regex_md is not None else padrao.regex
        for numero, linha in enumerate(_linhas_de_codigo(caminho, linguagem), start=1):
            for _ in regex.finditer(linha):
                achados.setdefault(rel, []).append((numero, linha.strip()[:120]))
    return achados


def contagem(padrao: Padrao, raiz: Path = RAIZ) -> dict[str, int]:
    return {rel: len(itens) for rel, itens in ocorrencias(padrao, raiz).items()}


def violacoes(padrao: Padrao, atual: dict[str, int], permitido: dict[str, int]) -> list[str]:
    """Compara a contagem atual com a allowlist e devolve as mensagens de falha."""
    onde = f"dono: {', '.join(padrao.donos) or padrao.dono_pendente}; mapa: {DOC}"
    erros = []
    for rel in sorted(set(atual) | set(permitido)):
        n, limite = atual.get(rel, 0), permitido.get(rel, 0)
        if n and not limite:
            erros.append(
                f"[{padrao.chave}] {rel}: {n} ocorrência(s) nova(s) de \"{padrao.regra}\" fora do dono "
                f"({onde}). Consuma o valor do dono em vez de recalcular."
            )
        elif n > limite:
            erros.append(
                f"[{padrao.chave}] {rel}: subiu de {limite} para {n} ocorrência(s) de \"{padrao.regra}\" "
                f"({onde}). A allowlist só pode encolher."
            )
        elif n < limite:
            erros.append(
                f"[{padrao.chave}] {rel}: caiu de {limite} para {n} - ótimo! Reduza a allowlist para {n} "
                f"(ALLOWLIST[\"{padrao.chave}\"][\"{rel}\"]{' -> remova a entrada' if n == 0 else ''}) "
                f"e atualize o {DOC}."
            )
    return erros


# ---------------------------------------------------------------------------
# A guarda
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("padrao", PADROES, ids=[p.chave for p in PADROES])
def test_regra_nao_se_espalha_fora_do_dono(padrao: Padrao):
    achados = ocorrencias(padrao)
    erros = violacoes(padrao, {rel: len(v) for rel, v in achados.items()}, ALLOWLIST[padrao.chave])
    detalhes = [
        f"  {rel}:{linha}: {trecho}"
        for rel, itens in sorted(achados.items())
        if rel not in ALLOWLIST[padrao.chave] or len(itens) > ALLOWLIST[padrao.chave][rel]
        for linha, trecho in itens
    ]
    assert not erros, "\n".join(erros + (["Ocorrências:"] + detalhes if detalhes else []))


# ---------------------------------------------------------------------------
# Sanidade da própria guarda
# ---------------------------------------------------------------------------

EXEMPLOS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "deload_filtro_sql": (
        ("AND a.deload_source IS NULL", "WHERE a2.deload_source is not null", "deload_source IS NOT NULL"),
        ("sql_not_deload('a')", "sql_is_deload('a')", "a.deload_source AS deload_source",
         "deload_source = %s", "if row.get('deload_source') is None:"),
    ),
    "deload_fator": (
        ("deload_factor = 0.6", "DELOAD_RPE: int = 6", "const deloadLoadFactor = 0.6;",
         "deload_sets = Decimal('0.33')", "deload_days = 7"),
        ("deload = is_deload_session(row)", "deload_factor = DELOAD_LOAD_FACTOR", "deload_source = None",
         "load_factor = float(deload_owner.DELOAD_LOAD_FACTOR)", "if deload == other:"),
    ),
    "cardio_prescricao_paralela": (
        ("if not item.cardio_duration_minutes or item.cardio_duration_minutes <= 0:",
         "item.cardio_duration_minutes <= 0", "minutes = item.cardio_duration_minutes * 60",
         "positive(item.hiit_rounds) * 2", "if hiit_high_zone > 5:", "CARDIO_SESSION_MAX_MINUTES = 240",
         "HIIT_ROUNDS_MAX: Final[int] = 50", "SECONDS_PER_CARDIO_MINUTE: Final[int] = 60",
         "cardio_warmup_minutes >= 0", "item.cardio_target_zone < 1"),
        ("cardio_prescription.validate(item)", "cardio_duration_minutes: kind === 'cardio' ? D : null",
         "item.cardio_duration_minutes < CARDIO_LIMITS.cardio_duration_minutes.min",
         "cardio_prescription.total_seconds(item)", "HIIT_PREFERRED_ROUNDS: Final[int] = 5",
         "HIIT_WORK_MINUTES: Final[int] = 2", "CARDIO_DEFAULT_WINDOW = WindowKind.ROLLING_90D",
         "CARDIO_CONTEXT_MAX_DAYS = 30", "hiit_rounds = None"),
    ),
    "cardio_padrao_sem_modo": (
        ("cardio_prescription.describe(cardio_prescription.DEFAULTS[cardio_prescription.CardioMode.HIIT])",
         "describe(DEFAULTS[CardioMode.HIIT])", "cp.total_seconds(cp.DEFAULTS[mode])",
         "describe_short( DEFAULTS[m])"),
        ("cardio_prescription.describe(cardio_prescription.default_prescription(CardioMode.HIIT))",
         "defaults = cardio_prescription.DEFAULTS[cardio_prescription.CardioMode.CONTINUOUS]",
         "defaults[cardio_prescription.TARGET_ZONE_FIELD]", "describe(item)"),
    ),
    "zonas_fc_paralelas": (
        ("entre 65% a 75% da FCmáx", "60-70% FC", "70 a 80% da frequência cardíaca", "zona = max_hr * 0.7",
         "const z2 = maxHr * .6;", "85% FC máx"),
        ("60-70% VO2max", "Z2 a {range_text(cardio_policy.Z2_VO2MAX_PERCENT_RANGE)}% do VO2max",
         "MAX_HR_ZONE_BOUNDS_PERCENT", "lower = max_hr * lower_pct / 100", "75% da carga",
         "max_hr_bpm = row['max_hr_bpm']"),
    ),
    "cardio_paralelo": (
        ("def _is_cardio_activity(item):", "_CARDIO_PATTERNS = list(x)",
         "WHEN LOWER(a.activity_type) LIKE '%%cardio%%'", "activity_type like '%cardio%'"),
        ("intervals_activity_kind.is_cardio(sport, name)", "CARDIO_PATTERNS: Final = ()",
         "_is_strength_activity(item)", "LIKE %s", "item_kind = 'cardio'"),
    ),
    "aderencia_cardio_paralela": (
        ("ADHERENT_TIME_MIN_PCT = 90", "PARTIAL_TIME_MIN_PCT: Final[int] = 50",
         "if time_completion_pct >= 90:", "target_zone_completion_pct < 70"),
        ("ca.ADHERENT_TIME_MIN_PCT", "time_completion_pct = _pct(a, b)", "\"time_completion_pct\": None",
         "if time_pct >= ADHERENT_TIME_MIN_PCT"),
    ),
    "envelope_cardio_paralelo": (
        ("WEEKLY_HIIT_MAX = 2", "HIIT_MIN_TSB: Final[int] = -20", "if form_tsb < -20:", "tsb <= -30",
         "RECOMMENDATION_HORIZON_DAYS = 7", "LEG_MUSCLE_GROUPS = (\"quadriceps\",)",
         "BAND_MAX_ZONE = {\"ok\": 5}", "BAND_WEEKLY_MINUTES_MAX: Final[dict] = {"),
        ("cardio_envelope.WEEKLY_HIIT_MAX", "if value < HIIT_MIN_TSB:", "\"form_tsb\": None",
         "band = recovery_band(tsb)", "WEEKLY_HIIT_MAX,"),
    ),
    "pior_lado": (
        ("reps = min(st['reps_left'], st['reps_right'])", "rpe: Math.max(set.rpeLeft, set.rpeRight)",
         "LEAST(es.reps_left, es.reps_right)", "GREATEST(s.rpe_left, s.rpe_right) AS rpe",
         "Math.min(values.reps_left ?? 99, values.reps_right ?? 99)"),
        ("reps_left = st.get('reps_left')", "min(values) if values else None", "canonical_reps(left, right)",
         "Math.min(reps, 10)", "max(set_rows)", "values.reps_left ?? null"),
    ),
    "pesos_esforco_paralelos": (
        ("EPOC_ZONE_WEIGHTS = (0.3, 0.9)", "PAI_ZONE_WEIGHTS: Final[tuple[float, ...]] = (",
         "ROLLING_WINDOW_DAYS = 7", "pai_score * 0.64", "epoc_total += .9"),
        ("cardio_load.EPOC_ZONE_WEIGHTS", "cardio_load.ROLLING_WINDOW_DAYS", "pai_7d_estimated",
         "PAI_TARGET_MIN: Final[int] = 75", "epoc_7d_estimated = row.get('x')"),
    ),
    "numeros_rag_cardio": (
        ("o mínimo é **20 minutos por sessão**", "3 vezes por semana", "65% a 75% da FCmáx", "cardio 5x por semana"),
        ("**{{cardio.z2_inicial_min}} minutos por sessão**", "## CAR-012 — Frequência mínima semanal",
         "1. **Fisiologia**", "VO₂ máximo"),
    ),
    "objetivo_treino_ramo": (
        ("if goal is TrainingGoal.STRENGTH:", "rest = 240 if read_goal(g) == TrainingGoal.STRENGTH else 60",
         "TrainingGoal.HYPERTROPHY: 1.0"),
        ("defaults_for(goal).rest_seconds", "ladder_for(track.training_goal)", "TrainingGoal(value)",
         "goal_value(row.get('training_goal'))"),
    ),
    "objetivo_treino_filtro_sql": (
        ("WHERE t.user_id = %s AND t.training_goal = %s", "ON g.training_goal = t.training_goal",
         "AND e2.training_goal=%(goal)s"),
        ("sql_goal_is('t')", "sql_goal_join('g', 't')", "if track.training_goal == goal:",
         "if a.training_goal != b.training_goal:", "training_goal = %s"),
    ),
    "objetivo_treino_literal": (
        ("if row['training_goal'] == 'strength':", 'goal = "hypertrophy"', "WHERE e.training_goal = 'strength'",
         "training_goal: str = 'hypertrophy'", "AND t.training_goal IN ('hypertrophy', 'strength')"),
        ("sql_goal_is('e')", "TrainingGoal.STRENGTH", "goal_value(row.get('training_goal'))",
         '"strength_patterns"', "max_strength = 3", "'strengthtraining'"),
    ),
    "epley": (
        ("return load_kg * (1.0 + (reps + rir) / 30.0)", "MAX(es.weight_kg * (1 + (es.reps + 0) / 30.0))", "x / 30"),
        ("days / 300", "ratio = a / 3"),
    ),
    "rir": (
        ("max(0.0, 10.0 - float(rpe))", "ELSE 10 - es.rpe END", "rir = 10 - set_row['rpe']"),
        ("10 - reps", "x = 100 - rpe_total"),
    ),
    "dose_sem_rpe": (
        ("reps_for_dose = int(reps_raw) if reps_raw is not None else 8",
         "rpe_for_dose = float(rpe_raw) if rpe_raw is not None else 7.0",
         "SUM(es.weight_kg * COALESCE(es.rpe, 7.0))", "const rpe = set.rpe ?? 7;"),
        ("COALESCE(es.rpe, 0)", "x if x is not None else 8", "reps if reps is not None else 80",
         "COALESCE(es.rpe, {float(e1rm.DOSE_RPE_WHEN_MISSING)})", "e1rm.dose_inputs(reps, rpe)"),
    ),
    "dose_reduzida": (
        ('REDUCED_DOSE_MULTIPLIER = Decimal("0.75")', "budget * 0.75"),
        ("0.755", "10.75", "1.75"),
    ),
    "dose_reduzida_texto": (
        ("NO MÁXIMO 1-2 séries diretas totais", "Dose reduzida (1-2 séries, RPE máx. 8-9, sem falha)",
         "sem falha, sem progressão, RPE máx. 8-9.", "NO MAXIMO 1-2 series diretas"),
        ("1 a 2 séries leves de aquecimento", "1-2 séries leves", "RPE máx 9", "reduzir 1–2 séries de Peito ou espaçar"),
    ),
    "sinais_expiram": (
        ("PERFORMANCE_SIGNALS_RESET_HOURS = 72.0", '"performance_signals_reset": True'),
        ("signals_policy", "performance_signals"),
    ),
    "defaults_prescricao": (
        ('reps_target = "8-12"', "rpe_target = '8-9'", "placeholder={x ? 'a' : '8-12'}"),
        ('"8-10"', '"18-12"', "faixa 8-12 em texto sem aspas"),
    ),
    "descanso_default": (
        ("    min_rest_seconds: int = 30,", "max_rest_seconds=90",
         "template_rest_seconds = round((min_rest_seconds + max_rest_seconds) / 2)",
         "const DEFAULT_REST_SECONDS = 60;", "min_rest_seconds: form.minRestSeconds ?? 30,", "useState<number>(30);",
         "useState<number | null>(90);"),
        ("min_rest_seconds: int = DEFAULT_MIN_REST_SECONDS,", "midpoint_rest_seconds(lo, hi)",
         "useState<number>(DEFAULT_MIN_REST_SECONDS)", "rest_seconds = 60", "round((a + b) / 2)"),
    ),
    "tetos_sessao": (
        ("SESSION_EXERCISES_MAX = 12", "MAX_EXERCISES_PER_SLOT = 9", "TARGET_MAX_EXERCISES_PER_DAY = 12",
         "MAX_SETS_PER_EXERCISE_REINFORCEMENT = 9", "SLOT_TIME_LIMIT_SECONDS = 90 * 60", "MAX_PRIORITIZED_PER_SLOT = 3",
         "if prioritized_count > 3:", "if (prioritizedCount > 3) {", "COMPLEMENT_DEFAULT_REST_SECONDS = 90",
         "MAX_EXERCISES_PER_GROUP = 3"),
        ("MAX_EXERCISES_PER_STATUS = 5", "MAX_EXERCISES_PER_SLOT = COMPLEMENT_MAX_EXERCISES_PER_SLOT",
         "if prioritized_count > MAX_PRIORITIZED_PER_SLOT:", "MAX_NEW_EXERCISES_PER_GROUP = 3"),
    ),
    "premissas_tempo": (
        ("EXEC_SECONDS_PER_REP = 3", "export const DEFAULT_ESTIMATED_REPS = 10;",
         "REST_WHEN_UNDEFINED_SECONDS: Final[int] = 0"),
        ("export const EXEC_SECONDS_PER_REP: number = PREMISES.exec_seconds_per_rep;",
         "per_rep = EXEC_SECONDS_PER_REP + x"),
    ),
    "numeros_prompt_volume": (
        ("aproximadamente 9 a 10 séries semanais", "**9 a 10 séries semanais**", "12-24 series por semana",
         "perto de 100% (entre 90 e 110);", "se a celula do macro-grupo em 1x pede 4-5 exercicios e 15-20 series",
         "tipicamente {ex_target_min}-9, e mais"),
        ("Peitoral: 9 a 10 séries diretas.", "**4 a 5 exercícios por sessão**",
         "tipicamente {ex_target_min}-{ex_typical_max}", "entre {coverage_min} e {coverage_max}",
         "15 a 20 séries válidas"),
    ),
    "limiar_fadiga": (
        ("FATIGUE_RATIO_WARNING = 0.92", "ratio < 0.85"),
        ("0.925", "10.85"),
    ),
    "meta_fallback": (
        ("max_sets = 27.0", "fallback = (6, 9, 27)"),
        ("0.27", "migration_027", "x27", "270"),
    ),
    "peso_secundario": (
        ("export const SECONDARY_FACTOR = 0.5;", "weight = float(secondary_participation) if ok else 0.50",
         "if (role === 'SECONDARY') return { stimulus: 0.5, fatigue: 0.5 };"),
        ("secondary_participation = row.get('stimulus_weight')", "primary = 0.5"),
    ),
    "trained_at": (
        ("COALESCE(a.started_at, a.ended_at, a.created_at) AS trained_at",
         "COALESCE(started_at, ended_at, created_at)"),
        ("COALESCE(a.ended_at, a.started_at)", "a.started_at"),
    ),
    "normalizacao_semanal": (
        ("value * 7.0 / float(cycle)", "weekly_factor = 7.0 / days", "days = max(7, cycle)",
         "days = max(WEEKLY_WINDOW_DAYS, cycle)"),
        ("x * 70 / y", "17.0 / 2", "max(70, x)"),
    ),
    "motor_legado_carga": (
        ("progression = suggest_load_and_reps(name, reps, logs)", "from .progression_service import suggest_load_and_reps"),
        ("suggest_load_and_reps_v2 = 1", "suggest_for_pair(conn, 1, 2)"),
    ),
    "carga_percentual": (
        ("return round(current_load * (1 + pct), 2)", "suggested_load_kg = _adjust(0.05)", "carga * 1.05",
         "new_load = load_kg * 1.025", "target = executed_load * (1 + signed_percent / 100)",
         "const next = load * (1 - pct / 100);", "peso * (1 + p / 100)", "weight_kg * (1 + percent/100)"),
        ("limit * 1.05", "fator = 1.05", "load * 1.0", "load_kg * 2", "steps * (1 + n / 100)",
         "ratio = weight * (1 + x / 1000)", "load / 100", "const pct = magnitude.value;"),
    ),
    "rpe_texto_paralelo": (
        ("cap_rpe_target_for_reduced_dose(exercises, groups)", "rpe = _clamp_rpe_text(rpe, 9) or rpe",
         "ei.clamp_rpe_target_text('9-10', 9)"),
        ("clamp_rpe_text(rpe, 9)", "parse_rpe_ramp('8-9-10')"),
    ),
    "percentual_de_carga": (
        ("Próximo treino: aumentar carga (+5%) e voltar ao piso", "incrementos de 2,5%-5% para compostos",
         "reduzir carga entre 2,5% e 5%", "a redução de -5% por RPE", "2,5% a 5%"),
        ("65% a 75% da FCmáx", "- 20% a 50% da carga de trabalho", "21.1%", "redução de 10%",
         "transform: 'translateX(-50%)'", "- 60% de 1RM;"),
    ),
    "relogio_sem_fuso": (
        ("today = date.today()", "now = datetime.now()", "WHERE local_date >= CURDATE() - INTERVAL 7 DAY"),
        ("datetime.now(timezone.utc)", "user_today(tz)", "update_date.today_value()"),
    ),
    "fatores_carga_sessao": (
        ("return roundToStep(referenceWorkingLoad * 0.2);", "referenceWorkingLoad * Math.pow(0.8, applicationIndex)",
         "const raw = referenceWorkingLoad * (0.5 + 0.2 * (feelerSetIndex - 1));", "base * 0.8 ** index",
         "weight_kg * 0.4"),
        ("opacity: 0.8", "scale * 0.2", "anchorKg * factor", "load_kg * 0.25", "weight * 0.85",
         "FACTORS.drop_set_load_factor ** applicationIndex"),
    ),
    "set_type_paralelo": (
        ("return value === 'dropset' ? 'drop_set' : value;",
         "_SET_TYPE_CASE = \"CASE LOWER(TRIM(COALESCE(NULLIF(es.set_type, ''), 'working')))\"",
         "{serie.set_type !== 'working' && (", "if (normalizeSetType(set.set_type) === 'working') {",
         "PREPARATORY = new Set(['warmup', 'feeler'])"),
        ("advancedStage?.kind === 'warmup'", "if (setType === 'drop_set') return 'Drop';", '"warmup": False,',
         "isWorkingSetType(set.set_type)", 'TECHNIQUE_PRIORITY = ("rest_pause", "drop_set")'),
    ),
    "recorde_paralelo": (
        ("MAX(e.best_weight_kg) AS best_weight_kg", "self.progression_service.get_recent_progression(limit=8)"),
        ("e.best_weight_kg, e.avg_rpe", "get_recent_records(user_id=1)", "max(best_weight_kg, x)"),
    ),
    "e1rm_confiavel_paralelo": (
        ("return int(reps) <= int(THRESHOLDS[\"e1rm_max_reliable_reps\"])", "AND es.reps <= {int(E1RM_MAX_RELIABLE_REPS)}",
         "ok = reps <= E1RM_MAX_RELIABLE_REPS", "reps <= e1rm.E1RM_MAX_RELIABLE_REPS"),
        ("0 < parsed <= int(max_reps)", "max_reps: int = E1RM_MAX_RELIABLE_REPS", "reps <= 12",
         "e1rm.is_reliable_reps(reps)", "if reps > E1RM_MAX_RELIABLE_REPS:"),
    ),
    "payload_ativacao_sem_tecnica": (
        ('[{"mapping_id": ex.get("mapping_id"), "sets_count": ex.get("sets_count")} for ex in xs]',
         ".map((ex) => ({ mapping_id: ex.mapping_id ?? null, sets_count: ex.sets_count ?? null })),",
         'return {"mapping_id": item.mapping_id, "sets_count": planned_item_sets(item, catalog)}'),
        ('{"mapping_id": m, "sets_count": 0, "stimulus_equivalent_sets": 0.0}',
         "exercises: Array<{ mapping_id: number | null; sets_count: number | null }>;",
         '{"mapping_id": 1, "sets_count": 4, "advanced_technique": "rest_pause"}',
         "activation_payload(ex)"),
    ),
    "limiar_ativacao_fe": (
        ("if (ratio >= 0.9) {", "if (ratio >= 0.5) {", "if (totalEquivalent < 6) return red;",
         "if (row.total_equivalent < 9) return yellow;"),
        ("ratio >= 0.95", "opacity < 0.9", "ratio < 0.5 ? a : b", "totalEquivalent < 60"),
    ),
    "muito_abaixo_planejado": (
        ("FAR_BELOW_PLANNED_RATIO = 0.5", "const FAR_BELOW_RATIO = 0.5;", "if actual < planned * 0.5:",
         "if (actual < targetSets * 0.5) return red;", "realizado < 0.5 * alvo"),
        ("ratio < 0.5 ? a : b", "actual < planned * FAR_BELOW_PLANNED_RATIO", "planned * 0.55",
         "opacity: 0.5", "FAR_BELOW_PLANNED_RATIO = FAR"),
    ),
    "metodologia_no_fe": (
        ("classifyAgainstRange(direct[index], sessionRange)", "const bucket = frequencyBucketForDays(daysTrained);",
         "weeklyRangeFor(volumeClass)", "const resolve = makeBlockResolver(groups);"),
        ("toRangeStatus(item.weekly_status)", "formatRange(row.weeklyRange)", "frequency_bucket_for(x)"),
    ),
    "fadiga_previa": (
        ("PRIOR_FATIGUE_SATURATION_SETS: Final[float] = 9.0", "const PRIOR_FATIGUE_HEAVY = 6;",
         "if (item.prior_fatigue.level >= 0.66) return 'red';", "priorFatigue?.level > 0.3",
         "prior_sets >= 6", "fadiga_previa / 9", "GROUP_FATIGUE_FATIGUED_MIN_SETS: Final[float] = 4.0",
         "if (set.group_fatigue?.level >= 0.5) return 'red';", "groupFatigue.prior_sets > 3"),
        ("PRIOR_FATIGUE_BAND_LABELS[band]", "prior_fatigue = None", "priorFatigue.level * hue",
         "prior_sets >= PRIOR_FATIGUE_HEAVILY_FATIGUED_MIN_SETS", "prior_fatigue_for_sequence(items)",
         "set.group_fatigue?.band_label", "group_fatigue_for_sequence(items)"),
    ),
    "trilha_sem_contexto": (
        ("track = tracks.get((exercise_id, equipment_id))", "tracks[(int(ex), int(eq))].working_load_kg",
         "found = tracks.get((query.exercise_id, eq))"),
        ("tracks.get(track_key(ex, eq, ctx))", "resolve_track(tracks, ex, eq, ctx)",
         "tracks.get(request.key)", "tracks[(ex, eq, ctx)]"),
    ),
    "adocao_carga_abaixo_do_piso": (
        ("return all(item.reps is not None and int(item.reps) < int(reps_min) for item in working)",
         "below = all(s.reps < group.reps_min for s in sets)",
         "const below = sets.every((s) => s.reps < repsMin);"),
        ("below_floor_load_is_working(working, reps_min)", "all_below_reps_min(working, reps_min)",
         "any(s.reps >= reps_min for s in sets)"),
    ),
    "mesmas_reps_rpe_maior": (
        ("if paired.previous_reps != paired.current_reps:", "same = prev_reps == curr_reps",
         "const same = previousReps === currentReps;", "if (currentReps !== pair.previousReps) return;"),
        ("paired.previous_reps", "reasons.includes('same_reps_higher_rpe')", "previous_sum == current_sum",
         "REASON_SAME_REPS_HIGHER_RPE"),
    ),
    "dose_exigida_series": (
        ("if len(working) < int(track.sets_count):", "executed_sets = clamp_sets_count(len(classification.working))",
         "if len(working_sets) >= track.sets_count:"),
        ("working_sets=len(working),",
         "clamp_sets_count(execution.prescribed.sets)", "len(sets) > 0"),
    ),
    "ficha_congelada_sql": (
        ("JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.effective_rpe_ceiling'))",
         "JSON_EXTRACT(p.prescription_json, '$.failure_allowed')", "'$.effective_rpe_target'"),
        ("JSON_EXTRACT(wpe.prescription_json, '$.sets_count')", "shown.get(\"effective_rpe_ceiling\")",
         "frozen.failure_allowed"),
    ),
    "queda_reps_entre_series": (
        ('drop = 1.0 - (float(last_set["reps"]) / float(first_set["reps"]))',
         "return 1 - last_reps / first_reps > 0.3",
         "const drop = 1 - (lastSet.reps / firstSet.reps);",
         "REP_DROP_MIN_FRACTION = 0.30", "rep_drop_threshold = 0.3"),
        ("item['excessive_rep_drop']", "drop > REP_DROP_MIN_FRACTION", "ratio = 1 - x / y",
         "rep_drop_details = []", "1 - lastRpe"),
    ),
    "qualificacao_melhor_serie": (
        ("if any(item.reps is None or int(item.reps) < reps_max for item in working):",
         "ok = all(s.reps >= group.reps_max for s in sets)", "if int(best.reps) < reps_max:",
         "const ok = sets.every((s) => s.reps >= repsMax);"),
        ("if any(item.reps is None or int(item.reps) < int(reps_min) for item in working):",
         "reps_max=12,", "best = best_set(top_load_sets(working, load))", "suggested_reps_max=reps_max"),
    ),
    "excesso_reps_sobe_carga": (
        ("return int(best.reps) >= int(reps_max) + EXCESS_REPS_FAST_TRACK", "if reps > reps_max + 3:",
         "excess = best >= repsMax + 3"),
        ("reps_max + delta_kg", "reps_max - 1", "reps_max = reps_min + range_width", "reps_max_text"),
    ),
    "plato_por_trilha": (
        ("if len(run) >= PLATEAU_MIN_SESSIONS:", "span >= PLATEAU_MIN_SPAN_DAYS",
         "return applied / total > 0.5", "done_tracks / total_tracks > GROUP_PHASE_ADVANCE_TRACK_SHARE"),
        ("THRESHOLDS['plateau_min_exposures']", "PLATEAU_SUSPECTED", "if plateau.confirmed:",
         "applied_count = len(applied)"),
    ),
    "carga_topo_repetida": (
        ("need = math.ceil(len(working) / 2)", "const need = Math.ceil(sets.length / 2);",
         "k = ceil(len(loaded) * 0.5)", "share = TOP_LOAD_MIN_SHARE_OF_WORKING_SETS"),
        ("top_load_min_sets(len(ranked))", "math.ceil(total_kg / step)", "Math.ceil(minutes)",
         "session_working_load(working, reps_min)"),
    ),
    "vocabulario_ia_serie": (
        ("'APRIMORAR TECNICA': 'yellow',", 'SET_EVALUATION_CATEGORIES = ("PROGREDIR", "MANTER")',
         "if category == 'REGREDIR':", '"REDUZIR",'),
        ("Reduzir carga", "DECREASE_LOAD", "PROGREDIR_CARGA = 1", "categoria proibida: REDUZIR"),
    ),
    "limiar_dor_muscular": (
        ("if muscle_pain > 6:", "row.muscle_pain >= 7", "float(muscle_pain) > 6", "musclePain >= 4 ?"),
        ("muscle_pain > MUSCLE_PAIN_BLOCK_ABOVE", "muscle_pain > 0", "muscle_pain_blocks_direct_work(x)",
         "Dor muscular reportada: {muscle_pain}/10"),
    ),
    "rotulo_status_serie": (
        ('REACHED: "Alvo atingido",', "label: 'Esforço atingido'", '"Reps abaixo da faixa"',
         "'Esforço abaixo do alvo'"),
        ("INTENSITY_STATUS_LABELS[status]", "reps_position_label(position)", "Esforço do dia",
         '"faixa de 12 a 15 reps"'),
    ),
    "carga_proxima_serie": (
        ("if reps > 15:", "reps >= 12 and rpe < 9", "if rpe >= 10 and reps < 6:", "const leve = set.reps > 16;",
         "mais de 15 repetições: suba a carga", "RPE 10 antes de 6 repetições", "NEXT_SET_LOW_RPE_BELOW = 9",
         "DEFAULT_RULESET = RuleSet(", "const DEFAULT_RULESET = { reps_floor: 6 };",
         "padrao = RuleSet(reps_floor=6, reps_ceiling=12, rules=())"),
        ("reps <= 12", "reps > reps_max", "reps > NEXT_SET_INCREASE_ABOVE_REPS", "15 repetições ou mais",
         "NEXT_SET_FAILURE_RPE: Final[int] = FAILURE_RPE",
         "mais de {NEXT_SET_INCREASE_ABOVE_REPS} repetições", "ruleset = ruleset or DEFAULT_RULESET",
         "ruleset=DEFAULT_RULESET,", "RuleSet(reps_floor=reps_floor, reps_ceiling=reps_ceiling, rules=rules)",
         "ruleset_from_dict(data)"),
    ),
    "regra_carga_cadastrada": (
        ("if reps_condition and reps_condition.op == '>' and reps > 12:",
         "matched = reps_condition.value <= facts.reps", "const ok = rule.rpe.op === '<' && set.rpe < threshold;",
         "if regra.reps.value != facts.reps:", "const fator = 1 + magnitude.value / 100;",
         "load = executed * (1 + rule.magnitude.value / 100)"),
        ("reps_condition = rule.reps", "describe(rule.reps)", "<Select value={rule.reps?.op} />",
         "const value = magnitude.value;", "items.map((reps_condition) => reps_condition.label)",
         "ruleset.reps_floor >= 1", "rule.magnitude.value / 2", "label: rule.reps.op,",
         "value={rule.reps.value}"),
    ),
    "intensidade_densidade": (
        ("export const REPS_DENSITY_RANGE = { min: 6, max: 15 } as const;", "{ minRpe: 10, points: 1.0 },",
         "export const BALANCED_BAND = 0.1;", "REST_DENSITY_LONGEST_SECONDS: Final[int] = 180",
         "linear(rest, REST_DENSITY_RANGE.longest, 45) || linear(rest, 180, 45)",
         "RPE_INTENSITY_POINTS = [{ min: 10, points: 1 }]"),
        ("export const REPS_DENSITY_RANGE: Readonly<{ min: number; max: number }> = RULE.reps_density_range;",
         "RULE.rpe_intensity_points.map((threshold) => ({ minRpe: threshold.min_rpe, points: threshold.points }))",
         "linear(reps, REPS_DENSITY_RANGE.min, REPS_DENSITY_RANGE.max)", "_linear(rest, REST_DENSITY_LONGEST_SECONDS, x)",
         "BALANCED_BAND: number = RULE.balanced_band"),
    ),
    "modo_contagem_volume": (
        ('if mode == "primary_secondary":', "WHERE counting_mode = 'primary_secondary'",
         "SECONDARY_SESSION_MIN_SETS = 1", "SECONDARY_SESSION_MIN_SETS: Final[int] = 1",
         # Lote 3: FE escrevendo o modo à mão
         "const [mode, setMode] = useState('primary_only');",
         "if (evaluation.counting_mode === 'primary_secondary') {"),
        ("mv.VolumeCountingMode.PRIMARY_SECONDARY", ">= SECONDARY_SESSION_MIN_SETS", "primary_secondary_total = 0",
         "const mode = DEFAULT_COUNTING_MODE;", "COUNTING_MODES.find((m) => m.value === evaluation.counting_mode)"),
    ),
    "modo_contagem_da_sessao": (
        ("SELECT v.volume_counting_mode FROM workout_plan_template_versions v",
         'mode = plan.get("volume_counting_mode")'),
        ("counting_mode = load_counting_mode_at(cur, user_id, day)", "session_modes.get(activity_id)",
         "volume_counting_modes = {}"),
    ),
    "faixas_sem_modo": (
        ('"FROM muscle_volume_class_mode_ranges"',
         "SELECT * FROM muscle_volume_class_mode_ranges WHERE class_id = %s",
         "LEFT JOIN muscle_volume_class_weekly_targets w ON w.class_id = c.id",
         # citar a coluna sem filtrar por ela nao basta
         "FROM muscle_volume_class_mode_ranges ORDER BY counting_mode"),
        ('"FROM muscle_volume_class_mode_ranges WHERE counting_mode = %s"',
         "JOIN muscle_volume_class_weekly_targets w ON w.class_id = c.id AND w.counting_mode = %s",
         "INSERT INTO muscle_volume_class_mode_ranges (class_id, counting_mode)",
         "FROM muscle_volume_class_ranges", "FROM muscle_volume_classes"),
    ),
    "pesos_tecnica": (
        ("  forced_reps: 1.0,", '"partial_reps": 0.5,', "'cheat_reps': 0.5", "export const FULL_EFFORT_RPE = 9.0;",
         "DEFAULT_MAX_EQUIVALENT: float = 1.0"),
        ("BEYOND_FAILURE_WEIGHTS = RULE.beyond_failure_weights", "fallbackItem('forced_reps', 'Repetições forçadas'",
         '"forced_reps": {"activation_set_equivalent": 0.25}', "FULL_EFFORT_RPE: number = RULE.full_effort_rpe",
         "if app >= FULL_EFFORT_RPE:"),
    ),
    "pontos_esforco": (
        ("  [6.5, 0.15],", "(10.0, 1.05),", "const clamped = Math.min(10, Math.max(6.5, rpe));",
         "clamp(float(rpe), 6.5, 10.0)"),
        ("Math.min(EFFORT_RPE_MAX, Math.max(EFFORT_RPE_MIN, rpe))", "RULE.effort_points.map(([rpe, points]) => [rpe, points])",
         "[6.5, 0.2]", "rpe >= 6.5"),
    ),
    "tmb_formula": (
        ("bmr = 370 + 21.6 * ffm", "const tmb = 500 + 22 * mlg;", "10 * peso + 6.25 * altura - 5 * idade + 5",
         "hb = 88.362 + 13.397 * kg", "447.593 + 9.247 * kg", "TMB = 370 + 21,6 × MLG",
         "MIFFLIN_HEIGHT_COEF = 6.25", "KATCH_MCARDLE_INTERCEPT: float = 370.0", "MIFFLIN_FEMALE_CONSTANT = -161"),
        ("bmr.katch_mcardle(ffm)", "MIFFLIN_HEIGHT_COEF * altura", "16.25 * x", "1370 + 21.6", "6.25%",
         "x = 2500 + 220"),
    ),
    "fator_atividade": (
        ("tdee = bmr * 1.55", "'light': 1.375,", "very: 1.725", "ACTIVITY_FACTORS = {",
         "NON_EXERCISE_ACTIVITY_FACTORS: dict[str, float] = {",
         # tabela por nível (revisão do #557)
         "moderate: 1.4,", "'very_active': 1.6", "ActivityLevel.SEDENTARY: 1.2,", "FACTORS['LIGHT']: 1.3",
         "sedentário: fator 1,2 → sedentary: 1,2"),
        ("anthropometrics.ACTIVITY_FACTORS[level]", "bmr * fator", "lineHeight: 1.5", "11.55", "1.555",
         "NON_EXERCISE_ACTIVITY_FACTORS.get(level)", "moderate: 2", "light: 0.5", "lightness: 1.5x",
         "{ value: 'moderate', label: ACTIVITY_LEVEL_LABELS.moderate }"),
    ),
    "kcal_por_grama": (
        ("kcal = p * 4 + c * 4 + f * 9", "gordura_g = kcal * 0.25 / 9", "carbs: rest / 4.0",
         "KCAL_PER_G_FAT = 9", "const KCAL_PER_G_PROTEIN = 4;", "fat × 9",
         # número antes do operador e tabela por macro (revisão do #557)
         "kcal = 4 * protein + 4 * carbs + 9 * fat", "9 × (gordura)", "{ protein: 4, carbs: 4, fat: 9 }",
         "'alcohol_g': 7,", "proteína: 4", "carb_kcal_per_g: 4.0", "Proteína e carbo: 4 kcal/g"),
        ("valor * KJ_PER_KCAL", "x * 4.184", "kcal / 1000", "dias * 7", "p * 40",
         "proteina * mc.KCAL_PER_G_PROTEIN", "gridColumn: '1 / 4'", "14 * x", "0.4 * peso",
         "protein_g: 4.5,", "fat_g: 12", "{ protein: KCAL.protein }", "carbs: 40", "14 kcal/g"),
    ),
    "met_compendium": (
        ("met * 3.5 * kg / 200 * minutes", "kcal = MET * 3.5 * weight / 200.0", "MET_STRENGTH_CIRCUIT = 5.8",
         "const MET_KCAL_DIVISOR = 200;", "kcal = met * 0.0175 * kg * min", "MET × 0,0175 × kg"),
        ("ee.training_kcal_met(peso, minutos)", "x / 2000", "status === 200", "MET_KCAL_DIVISOR * x",
         "ee.MET_STRENGTH_VIGOROUS", "10.0175", "0.01750"),
    ),
    "navy_gordura": (
        ("86.010 * Math.log10(abd - neck)", "163.205 * log10(c + q - p)", "NAVY_MALE = (86.010, 70.041, 36.76)",
         "%G = 97,684·log10(altura)",
         # forma métrica (revisão do #557)
         "d = 1.0324 - 0.19077 * log10(c - p) + 0.15456 * log10(h)", "1,29579 − 0,35004·log10(c + q − p)"),
        ("bc.navy_body_fat(sexo, altura, pescoco, cintura)", "186.0100", "NAVY_MALE[0] * x", "11.0324",
         "1.03240", "0.190770"),
    ),
    "densidade_energetica": (
        ("delta_kg * 7700", "7.700 kcal por kg", "kcal / 9400.0", "FAT_TISSUE_KCAL_PER_KG = 9400",
         "LEAN_TISSUE_KCAL_PER_KG: float = 1800"),
        ("ed.tissue_energy_density(mg)", "port 77001", "97700", "R$ 9.400,00", "7.7001"),
    ),
    "faixa_proteina": (
        ("1,6–2,2 g/kg de peso", "2.6 g/kg MLG", "proteina = 2.0 g / kg", "2,0 g por kg",
         "BULK_PROTEIN_G_PER_KG_DEFAULT = 2.0", "PROTEIN_PER_MEAL_G_PER_KG_RANGE = (0.40, 0.55)",
         # inteiro e chaves das faixas do formulário (revisão do #557)
         "2 g/kg", "mínimo de 3 g por kg", "protein_g_per_kg: 2.6,", '"fat_kcal_share": [0.15, 0.3]',
         "surplus_share_default = 0.1", "weekly_rate_share=(0.005, 0.01)", "carb_min_g_per_kg: 3",
         "max_deficit_share: 0.25", "min_intake_bmr_multiple = 1.0"),
        ("params.protein_g_per_kg * base_kg", "4.5 g/kg", "1.5 kg", "MIN_LOW_CARB_G_PER_KG = 1.0",
         "st.CUT_PROTEIN_G_PER_KG_FFM_RANGE", "5 g/kg", "12 g/kg", "protein_g_per_kg: Optional[float] = None",
         '"protein_g_per_kg": _range(st.CUT_PROTEIN_G_PER_KG_FFM_RANGE),', "weekly_rate_share=CUT_WEEKLY_RATE_SHARE_DEFAULT",
         "fat_kcal_share: float"),
    ),
    "ffmi_normalizado": (
        ("ffmi + 6.1 * (1.8 - h)", "FFMI + 6,1·(1,8 − altura)", "FFMI_NORMALIZATION_SLOPE = 6.1"),
        ("bc.ffmi_normalized(mlg, alt)", "16.1 * (1.8 - h)", "FFMI_NORMALIZATION_SLOPE * (x)"),
    ),
    "pausa_dieta": (
        ("MATADOR (2 on/2 off)", "pausa 3+1", "ICECAP 3:1", "2 semanas de déficit / 2 semanas de manutenção",
         "3 semanas on e 1 semana off", "MATADOR_ON_WEEKS = 2", "icecap_off_weeks = 1"),
        ("propose_calendar(Strategy.CUTTING, inicio, 12, BreakProtocol.MATADOR)", "a cada 3 semanas",
         "3 + 10", "13:15", "ph.MATADOR_ON_WEEKS"),
    ),
    "rotulo_estrategia": (
        ("label: 'Cutting'", 'STRATEGY = "Manutenção"', "`Dia alto em carbo`", "'Pausa de dieta'", '"Bulking"',
         "'Manutencao'",
         # texto JSX (revisão do #557)
         "<Badge color='orange'>Cutting</Badge>", "<Text> Dia baixo em carbo </Text>", "<span>Manutenção</span>"),
        ("'Cutting (definição)'", "STRATEGY_LABELS[s]", "'cutting'", "Manutenção da intensidade",
         "nutrition.strategy_labels[code]", "<Badge>{strategyLabel}</Badge>", "<Text>Cutting (definição)</Text>",
         "<Text>Fase de Bulking</Text>"),
    ),
    "kcal_treino_prioridade": (
        ("kcal = activity.calories_kcal or estimativa_met", "const kcal = session.calories_kcal ?? estimate.kcal;",
         "x.calories_kcal || metKcal", 'row.get("calories_kcal") or met_kcal', "TRAINING_KCAL_NATURE_PRIORITY = (",
         # ternário Python (revisão do #557)
         "kcal = row.calories_kcal if row.calories_kcal is not None else met_kcal",
         "calories_kcal if calories_kcal else estimate.kcal"),
        ("{session.calories_kcal ?? '—'}", "calories_kcal or 0", "calories_kcal ?? null", "calories_kcal or None",
         "ee.resolve_training_kcal(reported_kcal=row['calories_kcal'])", "COALESCE(a.calories_kcal, l.calories_kcal)",
         "ee.TRAINING_KCAL_NATURE_PRIORITY", "kcal = calories_kcal if calories_kcal else None",
         "calories_kcal if ok else 0", "calories_kcal_if_any = x"),
    ),
    "exposicoes_minimas": (
        ("suspeita de platô (3 ou mais exposições comparáveis)", "mínimo de 3 exposições", "4 OU MAIS EXPOSICOES"),
        ("({PLATEAU_MIN_EXPOSURES} ou mais exposições", "exposições comparáveis insuficientes", "3 ou mais sessões"),
    ),
    "fator_carga_trabalho_texto": (
        ("com carga muito leve (30% a 40% da carga de trabalho)", "aproximadamente **70% da carga de trabalho**",
         "20% e 40% da carga de trabalho", "2,5% da carga de trabalho"),
        ("suportar 100% da carga de trabalho na primeira série", "bem abaixo da carga de trabalho",
         "85% a 100% de 1RM", "50% da carga máxima"),
    ),
    "faixa_reps_prosa": (
        ("Faixa prioritária de **8 a 12 repetições**", "Hipertrofia (8–12 reps)", "séries pesadas com 10 a 12 repetições",
         "Força (1-5 reps)"),
        ("parar cerca de 1 a 2 repetições antes de perder a técnica", "faixas de 15+ repetições",
         "apenas **6 repetições**", "2 séries de aproximadamente 20 repetições", "3 a 4 séries"),
    ),
    "numeros_prompt_cardio": (
        ("4. PAI — Manter acima de 75.", "2. 20 minutos Z2; 3. 30 minutos Z2", "Zona alvo: 60-70% VO2max",
         "Modelo preferencial: 5x [2 minutos intensidade alta", "(mínimo de 3 ocorrências semelhantes)",
         "considerar 20-40 minutos Z2", "40 minutos em Z2 no dia de recuperação"),
        ("PAI — Manter acima de {cardio_policy.PAI_TARGET_MIN}.", "{m} minutos Z2", "PAIR = 49",
         "Histórico de 14 dias", "PAI adequado", "intensidade alta / {HIIT_RECOVERY_MINUTES} minutos"),
    ),
    "tonelagem_convertida": (
        ("escrevendo 16,3 toneladas totais", "f'{volume / 1000:.1f} t' + ' Toneladas'", "1 tonelada"),
        ("Volume total (tonelagem): {format_tonnage(v)}", "16.285 kg", "tonelagem semanal"),
    ),
    "rotulo_site_medida": (
        ("waist: 'Cintura',", '"chest": "Peitoral",', 'title="Cintura"', "label: 'Panturrilha direita',",
         '"neck": "Pescoço",', "forearm_l: 'Antebraço esquerdo',", "label: `Pescoço`,", "chest: `Peitoral`",
         "<Text>Cintura</Text>", "<td> Coxa esquerda </td>"),
        ("muscle_group_name: 'Peitoral'", '"cintura do dia"', "'Peitoral superior'", 'label="Cintura (cm)"',
         "SITE_LABELS[WAIST_SITE]", "measurementSiteLabel(site)", "<Text>Cintura (cm)</Text>",
         "`Cintura ${data}`", "<Text>{MEASUREMENT_SITE_LABELS[WAIST_SITE]}</Text>"),
    ),
    "origem_kcal": (
        ("activity.calories_source === 'manual'", "if (a.calories_source !== `intervals`) {",
         'if activity["calories_source"] == "intervals":', "'manual' === activity.calories_source",
         "caloriesSource === 'intervals'", "UPDATE activities SET calories_source = 'intervals' WHERE id = %s",
         'updates["calories_source"] = CALORIES_SOURCE_MANUAL if kcal is not None else None',
         "WHERE a.calories_kcal = ail.calories_kcal", "ail.calories_kcal <=> a.calories_kcal",
         'activity["calories_kcal"] == link["calories_kcal"]', "a.calories_kcal === link.calories_kcal"),
        ("calories_kcal = COALESCE(calories_kcal, %s)", "calories_kcal = VALUES(calories_kcal),",
         "SET calories_kcal = %s, calories_source = %s", "ail.calories_kcal AS intervals_calories_kcal",
         "resolve_calories_source(calories_kcal=calories_kcal, stored_source=x)",
         "{activity.calories_source_label && (",
         'updates["calories_source"] = source_on_manual_write(kcal, link["calories_kcal"] if link else None)',
         "intervals_calories_kcal=intervals_kcal,", "wellness_source = 'manual'"),
    ),
    "vocabulario_sexo": (
        ("{ value: 'M', label: 'Masculino' }", '{ value: "female", label: x }', "label: 'Feminino'",
         '"masculino": Sex.MALE,', "Sex.FEMALE: \"Feminino\",", "<Text>Masculino</Text>", "<td> feminina </td>",
         "if (user.sex === 'F') {", 'row["sex"] == "male"', "sex: 'M',", "item.sex !== `other`", "`Feminina`",
         "value: `male`",
         # revisão do guardião (2026-10-17): `)`, `=` simples, `<>`, IN e campo opcional TS
         'if row.get("sex") == "M":', "UPDATE users SET sex = 'male' WHERE sex = 'M'", "WHERE sex <> 'F'",
         "WHERE u.sex IN ('M', 'F')", 'if sex in ("m", "f"):', "sex?: 'male' | 'female';", "sex NOT IN ('other')"),
        ("{ value: 'm', label: 'Médio' }", "SEX_OPTIONS", "nutritionLabel(SEX_OPTIONS, user.sex)",
         'result["sex"] = anthro.sex_code(row.get("sex"))', 'MALE = "male"', "'Masculino e feminino'",
         "Sex.MALE", "{ value: 'other', label: 'Outra refeição' }", "<Text>{sexLabel}</Text>", "value: 'Fixed'",
         "sex: Optional[str] = Field(default=None, max_length=20)", "sexOptions.map((o) => o.value)",
         "label: 'Masculinidade'", "sex = COALESCE(%s, sex),", '"sex": anthro.sex_code(row.get("sex")),',
         "sex: editSex || null,", "sex = _validate_sex(item.get('sex'))", "if sex in SEXES:"),
    ),
    "nivel_atividade_e_altura": (
        ("if (user.activity_level === 'active') {", 'row.get("activity_level") == "sedentary"',
         "WHERE activity_level IN ('light', 'moderate')", "activityLevel: 'very_active',",
         "{ value: 'sedentary', label: x }", "label: 'Sedentário'", '"sedentario": ActivityLevel.SEDENTARY,',
         "<Text>Muito ativo</Text>", "if height_cm < 100:", "heightCm >= 250 ? x : y", 'row["height_cm"] <= 250',
         "HEIGHT_CM_RANGE: Final[tuple[float, float]] = (100.0, 250.0)", "const HEIGHT_CM_RANGE = [100, 250];"),
        ("activity_level = COALESCE(%s, activity_level),", "{ value: 'light', label: 'Claro' }",
         "label: 'Ativo'", "'Leve'", "activity_level: activityLevel || null,", "min={HEIGHT_MIN_CM}",
         "const [HEIGHT_MIN_CM, HEIGHT_MAX_CM] = NUTRITION_FORM_RANGES.height_cm;",
         "low, high = anthro.HEIGHT_CM_RANGE", "height_cm = _validate_height_cm(item.get('height_cm'))",
         "height < 100", "'Sedentarismo'", "activity_level: Optional[str] = Field(default=None, max_length=32)"),
    ),
    "catalogo_alimentos": (
        ("MATCH_MIN_SCORE = 0.6", "MATCH_AUTO_ACCEPT_SCORE: Final[float] = 0.85", "const SEARCH_CANDIDATE_POOL = 60;",
         "PORTION_AMOUNT_MAX: Final[float] = 2000.0", "QUERY_COVERAGE_WEIGHT = (0.3)",
         "export const SEARCH_RESULT_LIMIT_MAX = 50;"),
        ("if nota >= food_text.MATCH_MIN_SCORE:", "params.append(food_text.SEARCH_CANDIDATE_POOL)",
         "assert ft.MATCH_MIN_SCORE == 0.6", "limite = SEARCH_RESULT_LIMIT_MAX", "amount <= catalog.PORTION_AMOUNT_MAX",
         "for alias in SYNONYMS:", "palavras = [p for p in texto.split() if p not in STOPWORDS]"),
    ),
    "nota_matching": (
        ("if match.score >= 0.6:", "aceita = nota > .85", "item.score >= 0.85 ? 'ok' : 'x'", 'if m["score"] < 0.6:'),
        ("if match.score >= food_text.MATCH_MIN_SCORE:", "aceita = match.auto_accept", "score: m.score",
         "sorted(itens, key=lambda m: -m.score)"),
    ),
    "fontes_externas_alimentos": (
        ('kcal = nutrientes["energy-kcal"]["value"]', "nutrients.get('saturated-fat')", 'x = n["sodium_100g"] * 1000',
         "product.nutriments['energy-kcal_100g']", "for n in food['foodNutrients']:", "if n['nutrientId'] == 1003:",
         'agregado = produto["nutrition"]["aggregated_set"]', "proteina = valores.get(1003)",
         "ENERGIA_IDS = (1008, 2047)", "const SODIO = 1093;", "added = n[`added-sugars`]",
         # revisão do guardião (2026-10-17): payload cru, literais, constantes redefinidas e EAN à mão
         'if food["dataType"] == "Foundation":', "tipos = ['Foundation', 'SR Legacy']", 'if s == "as_sold":',
         'total = payload["totalHits"]', "kcal = n.value_computed", "x = produto.nutriments",
         "OFF_LOOKUPS_PER_USER_DAY = 30", "const USDA_SEARCH_PAGE_SIZE = 25;", "GTIN_LENGTHS: Final = (8, 13)",
         "OFF_NUTRIENT_KEYS = {", "OFF_LOOKUP_COUNTED_SOURCE = FoodSource.OFF", "if (!/^\\d{13}$/.test(ean))",
         "ok = soma % 10 == 0", "const re = /\\d{12,14}/;"),
        ("PORTION_AMOUNT_MAX: Final[float] = 2000.0", "alimento = fontes.map_usda_food(detalhe, fdc_id)",
         "fields=fontes.OFF_PRODUCT_FIELDS", "per_100['energy_kcal']", "'saturated_fat_g'", "kcal 2000.5",
         "id = 10030", "fontes.usda_search_items(payload)", "energy_kcal: 2_000", "limite = 2000_0",
         "setTimeout(fechar, 2000)", "if not fontes.off_lookup_allowed(usadas):",
         "limite=fontes.OFF_LOOKUPS_PER_USER_DAY", "mutationFn: (usdaFoodId: number) => x",
         "label: 'SR Legacy (Standard Reference)'", "pagina % 100", "x % 10.5", "codigo = fontes.normalize_gtin(ean)"),
    ),
    "bom_para_paralelo": (
        ("ready = now + timedelta(hours=item['remaining_hours'])",
         "now_local + timedelta(hours=float(row.get('estimated_recovery_hours') or 0))",
         "trained + timedelta(hours=estimated_hours)",
         "new Date(Date.now() + group.remaining_hours.value * 3600 * 1000)",
         "const ms = (grupo.remaining_hours.value ?? 0) * 36e5;",
         "Date.now() + remaining_hours * 60 * 60 * 1000",
         "agora + timedelta(minutes=remaining_hours * 60)",
         "timedelta(seconds=item['estimated_recovery_hours'] * 3600)",
         "const ms = 3600 * grupo.remaining_hours.value * 1000;",
         "new Date(now + 3_600_000 * g.remaining_hours.value)",
         "Date.now() + g.remaining_hours.value * 3600e3"),
        ("timedelta(hours=DENSITY_WINDOW_HOURS)", "fmtWeekdayDateTime(group.ready_at, timeZone)",
         "remaining_hours = round(max(estimated_hours - elapsed_hours, 0.0), 2)",
         "<MetricValue metric={grupo.remaining_hours} />", "remaining_hours * 24", "timedelta(minutes=steps)",
         "remaining_hours * 60", "x = 3600 * horas", "13600 * remaining_hours_count", "remaining_hours * 36000"),
    ),
    "kj_kcal": (
        ("kcal = kj / 4.184", "KJ_PER_KCAL = 4.184", "const kcal = kj / 4,184;", "KJ_PER_KCAL: Final[float] = 4.2",
         "x = 4.184 * kcal"),
        ("kcal = meal_math.kj_to_kcal(kj)", "valor / KJ_PER_KCAL", "versao 14.1845", "4.1840001x", "id = 41845",
         "1.4184"),
    ),
    "recencia_planejada_fora_do_dono": (
        ("load_user_recency(cur, user_id, gym_id, include_planned=True)", "rank(rows, includePlanned: true)"),
        ("load_user_recency(cur, user_id, gym_id)", "planned_sets = 3", "is_planned = True"),
    ),
    "exercicio_livre_fora_do_dono": (
        ("def _is_free_exercise_item(item):", "not item.mapping_id and not item.equipment_id",
         "return bool(g) and not item.get(\"mapping_id\") and not item.get(\"equipment_id\")"),
        ("is_free_exercise_item(item, require_kind=False)", "if not mapping_id and not equipment_id:",
         "not item.mapping_id and item.muscle_group_id"),
    ),
    "linhagem_modelo_fora_do_dono": (
        ("JOIN workout_plan_templates o ON o.id = t.source_template_id", "parent = row['source_template_id']",
         "const origem = modelo.sourceTemplateId;"),
        ("lineage_template_id = slot.lineage_id", "template_id = row['template_id']", "source_template = x",
         "resolve_lineage_roots(ids, parents)"),
    ),
    "chave_slot_fora_do_dono": (
        ("key: `slot:${link.template_id}:${link.template_slot_sequence}`,", 'key = f"slot:{template_id}:{seq}"',
         "const k = 'slot:{id}';"),
        ("key: info.key,", "slot_group_key(slot)", '"slot:" + texto', "`slot ${grupo.slotSequence}`"),
    ),
    "slot_por_nome": (
        ('if row["slot_name"] == other["slot_name"]:', "if (a.slot_name === b.slot_name) {",
         "WHERE wpts.slot_name = 'Treino A' OR x <> wpts.slot_name", "previous.slotName !== current.slotName"),
        ('"slot_name": self.slot_name,', "label: row.slot?.label ?? row.slot_name,", "other.key == slot.key",
         "slot_names = [s.name for s in slots]"),
    ),
    "rotulo_sem_macro_grupo": (
        ("className: group.volume_class_name || 'Sem macro-grupo',", 'MACRO_GROUP_UNCLASSIFIED = "Sem macro-grupo"',
         "return owner.get(group.id)?.name ?? 'Sem macro-grupo';", "Sem macro-grupo ({unassigned.length})",
         "label: `Sem macro-grupo`"),
        ("item.volume_class_name", "MACRO_GROUP_UNCLASSIFIED = VOLUME_CLASS_UNCLASSIFIED_LABEL",
         "grupos sem macro-grupo definido", "'Sem macro-grupos'", "label: 'Macro-grupo'"),
    ),
    "diario_nutricao": (
        ("MEAL_MAX_ITEMS = 30", "const HUNGER_SCALE = [1, 5];", "WATER_ML_RANGE: Final[tuple] = (0.0, 10000.0)",
         "TARGET_FREEZE_LAG_DAYS = 2", "ATWATER_TOLERANCE_SHARE: float = 0.15", "export const PER_100G_REFERENCE = 100;",
         "ADHERENCE_PROTEIN_MIN_SHARE = 0.9", "{ colher_sopa: 15, xicara: 240 }", "'colher_cha': 5,",
         "GENERIC_PORTIONS = {", "const ITEM_AMOUNT_MAX = 2000;"),
        ("ITEM_AMOUNT_MAX: Final[float] = PORTION_AMOUNT_MAX", "max={NUTRITION.meal_max_items}",
         "if resolution.amount > ITEM_AMOUNT_MAX:", "GENERIC_PORTIONS.map((p) => p.code)", "unit: 'colher_sopa'",
         "dt_mod.TARGET_FREEZE_LAG_DAYS - 1", "const [WATER_MIN, WATER_MAX] = NUTRITION.water_ml_range;",
         "colher_sopa: GENERIC_PORTIONS[0]"),
    ),
    "soma_nutrientes": (
        ("total = sum(item['energy_kcal'] for item in itens)", "SELECT SUM(m.protein_g) FROM meals m",
         "meals.reduce((acc, m) => acc + (m.totals.nutrients.energy_kcal ?? 0), 0)", "soma += item.fiber_g",
         "totals.energy_kcal += item.energy_kcal", "kcal = valor_por_100 * gramas / 100",
         "const kcal = per100 * grams / 100.0;", "fator = gramas * 0.01", "sum(r['kcal_target'] for r in semana)",
         "total = total + item.energy_kcal", "acc = acc + (m.totals.nutrients.protein_g ?? 0)",
         "soma = soma + row['sodium_mg']"),
        ("totals = meal_math.sum_items(itens)", "sum(parte.item_count for parte in parts)",
         "shareToPercent(row.share)", "nutrients.energy_kcal", "valor / PERCENT", "amount / PER_100G_REFERENCE",
         "1 / 1000", "x * 0.015", "sum(float(d.carb_g) for d in dias)", "width: 100%"),
    ),
    "cota_ia_nutricao": (
        ("MEAL_PARSE_DAILY_QUOTA = 30", "LABEL_PHOTO_DAILY_QUOTA: Final[int] = 10",
         '"claude-haiku-5-5": (100_000, 50),', "HAIKU_LONG_PROMPT_THRESHOLD_TOKENS = 100_000",
         "MODEL_LONG_PROMPT_PRICES = {",
         "DAILY_QUOTA = {AiFeature.MEAL_PARSE: 30}", "const MEAL_PARSE_DAILY_QUOTA = 30;",
         "TOKENS_PER_PRICE_UNIT = 1_000_000", "MODEL_PRICES_USD_CENTS_PER_MTOKEN = {",
         "FEATURE_JOB_TYPE: Mapping[str, str] = MappingProxyType({",
         'async_jobs.create_job("nutrition-meal-parse", request=r, owner_user_id=u)',
         "if job_kind == 'nutrition-label-ocr':", "if cota_usada >= 30:", "remaining_quota < 1",
         'row["quota"] == 10', "if (remainingQuota === 0) {", "quota !== 30", "quota: int = 10",
         '{"meal_parse_quota": 30}', '"gpt-5.4-mini": (75, 450),', "PRECO = {'gpt-4o-mini': {",
         "preco = ModelPrice(Decimal(1), Decimal(1), Decimal(1))"),
        ("if not ai_usage.quota_status(feature, usadas).allowed:", "limite = ai_usage.DAILY_QUOTA[feature]",
         "async_jobs.create_job(MEAL_PARSE_JOB_TYPE, request=r, owner_user_id=u)", "status.used >= status.limit",
         'LABEL_PHOTO = "label_photo"', 'PROMPT_VERSION = "nutrition-parse-v1"', "raise quota_exceeded_error(status)",
         "cotacao_dolar = 5", "{QUOTA.remaining}", "_AI_QUOTA_NOUNS: Final[Mapping[AiFeature, str]] = {",
         "const ok = quota => quota.allowed;", 'model = "gpt-5.4-mini"', '("gpt-image-1.5", "low", "1024x1536"): x',
         "quota ? 1 : 0", "{ remainingQuota: usage.remaining > 0 }", '{"quota_used": 0}',
         "useState({ usedQuota: 0 })"),
    ),
    "cota_ia_copia_nutricao": (
        ("MEAL_PARSE_DAILY_LIMIT = 30", "max_per_day: int = 30", "LIMITE_DIARIO_IA = 10",
         'int(os.environ.get("NUTRITION_AI_MAX_PER_DAY", "30"))', 'os.getenv("MEAL_PARSE_QUOTA")',
         'os.environ.get("OPENAI_NUTRITION_PRICE_IN")', "const limite = process.env.NEXT_PUBLIC_AI_DAILY;",
         'price = _price("OPENAI_PRICE_IN_PER_MTOKEN")', "from .periodization.logs import estimate_cost_usd",
         "custo = periodization.logs.estimate_cost_usd(a, b)"),
        ("OFF_LOOKUPS_PER_USER_DAY: Final[int] = 30", "limite = ai_usage.DAILY_QUOTA[feature]",
         'os.environ.get("USDA_API_KEY")', "custo = ai_usage.estimate_cost_usd(modelo, a, b)",
         "per_day = ai_usage.daily_quota(feature)", "OPENAI_NUTRITION_PARSE_MODEL",
         "MAX_LOSS_KG_PER_DAY = 1", "slope_kg_per_day = 0.1", "KCAL_PER_DAY = 2000"),
    ),
    "leitura_refeicao_ia": (
        ("TEXT_MAX_CHARS = 1000", "CANDIDATES_PER_ITEM: Final[int] = 5", "const TEXT_MAX_CHARS = 500;",
         "export const MEAL_PARSE_TEXT_MAX_CHARS = 1000;", "const AI_ESTIMATE_BASIS = 'g';",
         "UNSTATED_QUANTITY_UNIT = 'g'",
         "CONFIDENCE_RANGE = (0.0, 1.0)", "AI_UNITS = ('g', 'ml')", 'ESTIMATE_FIELDS = ["energy_kcal"]',
         'ESTIMATE_KEY = "estimated_per_100g"', 'ESTIMATE_KEY = f"estimated_per_{n}g"',
         'item.get("estimated_per_100g")', "const k = 'estimated_per_100g';",
         "CATALOG_CANDIDATE_STATUSES = frozenset({MatchStatus.ESTIMATED})"),
        ("limite = meal_parse.TEXT_MAX_CHARS", "candidatos = ranked[:CANDIDATES_PER_ITEM]",
         "item.get(meal_parse.ESTIMATE_KEY)", "[u.value for u in meal_parse.AI_UNITS]",
         "maxLength={MEAL_PARSE_TEXT_MAX_CHARS}", 'MODEL_ENV_VAR = "OPENAI_NUTRITION_PARSE_MODEL"',
         "estimated_per_kg", 'ESTIMATE_REQUIRED_FIELDS = catalog.REQUIRED_PER_100_FIELDS',
         "export const MEAL_PARSE_TEXT_MAX_CHARS: number = MEAL_PARSE.text_max_chars;",
         "export const AI_ESTIMATE_BASIS: NutritionCode = MEAL_PARSE.estimate_basis;",
         "UNSTATED_QUANTITY_UNIT: Final[Unit] = Unit.GRAM"),
    ),
    "foto_rotulo_nutricao": (
        ("LABEL_PHOTO_MAX_BYTES = 1572864", "LABEL_PHOTO_MAX_BYTES: Final[int] = int(1.5 * 1024 * 1024)",
         'LABEL_PHOTO_S3_PREFIX = "nutrition-labels"', "RESIZE_MAX_EDGE_PX = 1280",
         "const RESIZE_JPEG_QUALITY = 0.85;", "LABEL_PHOTO_ACCEPTED_CONTENT_TYPES = frozenset({'image/jpeg'})",
         "LABEL_PHOTO_RETENTION_DAYS: int = 30", 'LABEL_IMAGE_DETAIL = "high"',
         'key = f"nutrition-labels/{user_id}/{uuid4().hex}.jpg"', "const prefixo = `nutrition-labels/${id}`;",
         'PREFIX = "nutrition-labels"', "MAX_UPLOAD_BYTES = 1.5 * 1024 * 1024", "if (blob.size > 1.5 * 1024 * 1024)",
         "const LIMITE = 1_572_864;", "teto = 1024 * 1024 * 1.5", "limite = 1536 * 1024",
         "LABEL_PHOTO_MAX_BASE64_CHARS = 2097152"),
        ("chave = label_photo.label_photo_key(usuario, uuid.uuid4().hex)",
         "if dados > label_photo.LABEL_PHOTO_MAX_BYTES:", "maxEdge: LABEL_PHOTO.resize_max_edge_px,",
         'LABEL_PHOTO_JOB_TYPE = "nutrition-label-ocr"', 'prefix = "exercise-card-drafts/"',
         "ContentType=label_photo.LABEL_PHOTO_CONTENT_TYPE", 'id = "nutrition-labels-expire-30d"',
         "MAX_PHOTO_BYTES = 8 * 1024 * 1024", "size = 1024 * 1024"),
    ),
    "foto_rotulo_numeros": (
        ("resizeImageForUpload(file, { maxEdge: 1280, quality: 0.85 })", "const MAX_EDGE = 1280;",
         "canvas.toBlob(resolve, 'image/jpeg', 0.85)", "canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.85)",
         "quality = 0.85", "max_edge: int = 1280", '{"maxEdge": 2048}', "quality: 85", "jpegQuality = 85",
         "qualidade = 0.85", "const q = .85;", 'ImageInput(img, "image/jpeg", "high")', 'ContentType="image/jpeg"',
         "detail: 'high'", '{"type": "input_image", "detail": "high"}', "JPEG_QUALITY = 85", "imageQuality: 0.9",
         "MAX_BODY_CHARS = 2_097_152"),
        ("maxEdge: LABEL_PHOTO.resize_max_edge_px", "quality: LABEL_PHOTO.resize_quality",
         "canvas.toBlob(resolve, options.contentType, options.quality)", "DataQuality.HIGH",
         "quality=DataQuality.LOW", "12800 kcal", "x1280", "width: 128", "0.855", "10.85",
         "ImageInput(imagem, label_photo.LABEL_PHOTO_CONTENT_TYPE, label_photo.LABEL_IMAGE_DETAIL)",
         "DayKind.HIGH", "contentType: LABEL_PHOTO.content_type", "MIN_DATA_QUALITY = 0.5",
         '"data_quality": 0.6', "DataQuality.MEDIUM: 0.5", "20971520"),
    ),
    "fallback_registro_serie": (
        ("const workingSetsTarget = adjusted ?? planExercise.sets_count ?? 1;",
         "advancedMode ? planExercise.warmup_sets_count || 1 : 0", "pe.feeler_set_sets_count || 2",
         ": (parseNumericTarget(effectiveRepsTarget) ?? 10);", "parseNumericTarget(suggested?.load_target) ?? 20)",
         "SET_LOGGER_FALLBACK_REPS: Final[int] = 10", "SET_LOGGER_FALLBACK_LOAD_KG = 20",
         "parseNumericTarget(String(x)) ?? 10", "item.sets_count or 1",
         "sets_count: Math.max(1, remainingSets),", "Math.max(1, current.targetCount - current.loggedCount)",
         "max(1, sets_target)", "Math.max(remainingSets, 1)", "remainingSets || 1", "workingTarget ?? 1"),
        ("Math.max(MIN_SETS_COUNT, remainingSets)", "Math.max(1, orderedExercises.length)",
         "Math.max(0, current.targetCount - current.loggedWorkingCount)", "Math.max(remainingSets, MIN_SETS_COUNT)",
         "remainingSets ?? 0", "rpe_target ?? null",
         "(ex.sets_count ?? 0) > 0", "acc + (left.byWeek[key]?.sets_count ?? 0)", "Number(item.sets_count || 0)",
         "planExercise.sets_count ?? MIN_SETS_COUNT", "parseNumericTarget(effectiveRpeTarget),",
         "parseNumericTarget(effectiveLoadTarget) ?? SET_LOGGER_FALLBACK_LOAD_KG",
         "SET_LOGGER_FALLBACK_REPS: number = PRESCRIPTION.set_logger_fallback_reps;", "item.sets_count or 0"),
    ),
    "modo_treino_pela_ficha": (
        ("ex.advanced_technique != null || ex.warmup_reps != null || ex.feeler_set_reps != null",
         "(e) => e.warmup_reps !== null || e.advanced_technique !== undefined",
         "planExercise.feeler_set_reps != null || planExercise.warmup_reps != null",
         "apiFetch('/workout-plans?status=planned&has_advanced_technique=true&limit=50')"),
        ("advancedMode && planExercise.warmup_reps != null ? planExercise.warmup_sets_count || MIN_SETS_COUNT : 0",
         "? current.planExercise.warmup_reps != null",
         "requiredTechnique={current.planExercise.advanced_technique ?? null}",
         "resolveWorkoutModeFromPlan(plan.exercises) === 'advanced'"),
    ),
    "fallback_registro_serie_cadeia": (
        ("lastSet?.reps ?? 10", "suggested?.suggested_load_kg ?? 20", "set.weight_kg ?? 20"),
        ("lastSet?.reps ?? SET_LOGGER_FALLBACK_REPS", "set.reps ?? 0", "x.weight_kg ?? null", "anchorRpe ?? 10"),
    ),
    "rotulo_progressao": (
        ("STABLE: { label: 'Estável', color: 'blue' }", '"PLATEAU_SUSPECTED": "Suspeita de plato",',
         "return { kind: 'not_comparable', label: 'Não comparável', tooltip };", 'x = "Progredindo"'),
        ("Suspeita de platô não é diagnóstico", "label: PL.STABLE", "'Progrediu'", "'Dados insuficientes'",
         "Não comparável · {motivo}"),
    ),
    "medida_serie": (
        ("if set.measure == 'duration':", "measure === 'max_reps'", "WHERE es.measure = 'duration'",
         "const timed = item.measure !== 'reps' && item.measure === 'duration';", "measure = \"max_reps\"",
         "'max_reps' in allowed", "if (measure === 'reps') {", 'row["measure"] != "reps"'),
        ("isRepBased(set.measure)", "sql_is_rep_based('es.measure')", "MEASURE_LABELS[SetMeasure.DURATION]",
         "duration_seconds = 60", '"duration_minutes": 30', "cardio_duration_minutes === null",
         "const { measure, reps } = set;", "normalize_measure(row.get('measure'))", "DEFAULT_MEASURE.value"),
    ),
    "rpe_alvo_protocolo": (
        ("rpe_target_effective = 10", "params['target_rpe'] = 9", '"target_rpe": 10', "targetRpe: 9",
         "if (targetRpe === 10) {", "PROTOCOL_TARGET_RPE_WITHOUT_FAILURE = 9"),
        ("rpe_target_effective = protocol_target_rpe(teto)", "target_rpe = 8", "rpe_target = 9",
         "targetRpe: params.target_rpe", "DEFAULT_TARGET_RPE = 8", "target_rpe: 100"),
    ),
    "serie_pendente_a_mao": (
        ("WHERE es.reps_pending = 0", "AND reps_pending=1", "if row['reps_pending'] == 1:", "s.`reps_pending` = 0",
         "set.reps_pending === 1"),
        ("reps_pending = %s", "f\"{sql_set_confirmed('es.reps_pending')}\"", "reps_pending: bool = False",
         "is_pending_set(row)", "reps_pending = 10"),
    ),
    "padrao_laboral": (
        ("ec.laboral_default_sets AS sets", "row['laboral_default_reps'] or 15", "laboral_default_load_kg = 2",
         "AND ec.is_laboral = 0", "WHERE is_laboral=1"),
        ("laboral_domain.DEFAULT_SETS_FIELD", "laboral_prescription(defaults, row)", "is_laboral = is_laboral_flag(x)",
         "laboral_defaults_sets"),
    ),
    "pausa_padrao_rest_pause": (
        ("REST_PAUSE_DEFAULT_PAUSE_SECONDS = 15", "pause_seconds = 15", "technique?.pause_seconds ?? 15",
         "params['pause_seconds'] or 15", "techniqueDurationSeconds: 15", "advanced_technique_duration_seconds || 15"),
        ("pause_seconds = None", "pause_seconds: Any = None", "pause_seconds = 150", "pauseSeconds: 5",
         "techniqueDurationSeconds: planTechniquePauseSeconds(current.planExercise)"),
    ),
    "motivo_manter_carga": (
        ("reason: 'Repetições e RPE dentro do esperado: carga adequada'",
         'f"{rotulo}: mantenha a carga e busque o RPE-alvo"', "'Teto: mantenha a carga e pare antes da falha'"),
        ("next.reason_label", "NEXT_SET_REASON_LABELS[next.reason]", '"Estado dentro do esperado: dizer isso"',
         "mantenha a carga executada ou reduza-a"),
    ),
}


# Exemplos da `regex_md` (só para padrões que a definem).
EXEMPLOS_MD: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "kcal_por_grama": (
        ("Proteína e carboidrato têm 4 kcal/g; gordura, 9 kcal por g.", "álcool: 7 kcal/g",
         "Carboidrato: 4 kcal por grama", "9 kcal por gramas de gordura"),
        ("3 × 4 séries de 8 a 12", "divida o prato em 4 / 9 partes", "14 kcal/g", "kcal/g do alimento",
         "4 kcal por gramado", "fat: 9"),
    ),
}


@pytest.mark.parametrize("padrao", PADROES, ids=[p.chave for p in PADROES])
def test_regex_md_pega_o_padrao_e_nao_pega_o_vizinho(padrao: Padrao):
    if padrao.regex_md is None:
        assert padrao.chave not in EXEMPLOS_MD
        return
    assert "md" in padrao.linguagens, f"[{padrao.chave}] regex_md sem `md` nas linguagens"
    positivos, negativos = EXEMPLOS_MD[padrao.chave]
    for exemplo in positivos:
        assert padrao.regex_md.search(exemplo), f"[{padrao.chave}] (md) deveria casar: {exemplo!r}"
    for exemplo in negativos:
        assert not padrao.regex_md.search(exemplo), f"[{padrao.chave}] (md) não deveria casar: {exemplo!r}"


def test_md_usa_a_regex_propria_quando_ha(tmp_path: Path):
    padrao = PADROES_POR_CHAVE["kcal_por_grama"]
    rag = tmp_path / "api/privado/rag/markdown/NUTRICAO/macros.md"
    rag.parent.mkdir(parents=True)
    rag.write_text("Faça 3 × 4 séries.\nProteína tem 4 kcal/g.\n", encoding="utf-8")
    codigo = tmp_path / "api/app/nutrition_meals.py"
    codigo.parent.mkdir(parents=True)
    codigo.write_text("kcal = p * 4\n", encoding="utf-8")
    assert ocorrencias(padrao, tmp_path) == {
        "api/app/nutrition_meals.py": [(1, "kcal = p * 4")],
        "api/privado/rag/markdown/NUTRICAO/macros.md": [(2, "Proteína tem 4 kcal/g.")],
    }


@pytest.mark.parametrize("padrao", PADROES, ids=[p.chave for p in PADROES])
def test_regex_pega_o_padrao_e_nao_pega_o_vizinho(padrao: Padrao):
    positivos, negativos = EXEMPLOS[padrao.chave]
    for exemplo in positivos:
        assert padrao.regex.search(exemplo), f"[{padrao.chave}] deveria casar: {exemplo!r}"
    for exemplo in negativos:
        assert not padrao.regex.search(exemplo), f"[{padrao.chave}] não deveria casar: {exemplo!r}"


def test_dono_e_ignorado_e_copia_e_contada(tmp_path: Path):
    """Repo de mentira: o dono pode ter a regra; a cópia em outro arquivo conta."""
    padrao = PADROES_POR_CHAVE["epley"]
    dono = tmp_path / padrao.donos[0]
    dono.parent.mkdir(parents=True)
    dono.write_text("def e1rm(l, r, rir):\n    return l * (1.0 + (r + rir) / 30.0)\n", encoding="utf-8")
    copia = tmp_path / "api/app/tela.py"
    copia.write_text("x = carga * (1 + (reps + rir) / 30)\n", encoding="utf-8")
    teste = tmp_path / "frontend/src/lib/e1rm.test.ts"
    teste.parent.mkdir(parents=True)
    teste.write_text("expect(100 * (1 + 10 / 30)).toBe(133.3);\n", encoding="utf-8")

    assert contagem(padrao, tmp_path) == {"api/app/tela.py": 1}


def test_escopo_restringe_aos_modulos_de_motor(tmp_path: Path):
    padrao = PADROES_POR_CHAVE["relogio_sem_fuso"]
    for rel in ("api/app/generation/x.py", "api/app/auth.py", padrao.donos[0]):
        caminho = tmp_path / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("from datetime import date\nhoje = date.today()\n", encoding="utf-8")
    assert contagem(padrao, tmp_path) == {"api/app/generation/x.py": 1}


def test_excluir_tira_a_tela_de_cadastro_do_escopo(tmp_path: Path):
    """Regras de carga (R1): a tela `/regras-carga` e o seu helper só montam
    Selects com o vocabulário do dono e ficam fora da varredura; a mesma
    comparação em qualquer outro arquivo do FE ou da API conta."""
    padrao = PADROES_POR_CHAVE["regra_carga_cadastrada"]
    fora = (
        "frontend/src/app/(app)/regras-carga/RegrasCargaClient.tsx",
        "frontend/src/lib/next-set-load-rules.ts",
        "frontend/src/lib/next-set-load-rules-form.ts",
    )
    dentro = ("frontend/src/lib/set-engine-status.ts", "api/app/workout_sessions.py")
    for rel in fora + dentro + (padrao.donos[0],):
        caminho = tmp_path / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("const ok = rule.rpe.op === '<';\n", encoding="utf-8")
    assert contagem(padrao, tmp_path) == {rel: 1 for rel in dentro}


def test_escopo_aceita_glob_para_arquivos_de_lotes_futuros(tmp_path: Path):
    """Nutrição (L1): `api/app/nutrition_*.py` e `frontend/src/lib/nutrition-*`
    entram no escopo antes de existirem; o vizinho fora do glob fica de fora."""
    padrao = PADROES_POR_CHAVE["fator_atividade"]
    copias = (
        "api/app/nutrition_targets_service.py",
        "frontend/src/lib/nutrition-api.ts",
        "frontend/src/components/nutrition/PlanForm.tsx",
        "api/app/domain/nutrition/outro.py",
    )
    fora = ("api/app/nutritionist.py", "frontend/src/lib/volume.ts", "api/app/analytics.py")
    for rel in copias + fora + (padrao.donos[0],):
        caminho = tmp_path / rel
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("tdee = bmr * 1.55\n", encoding="utf-8")
    assert contagem(padrao, tmp_path) == {rel: 1 for rel in copias}


@pytest.mark.parametrize(
    "rel",
    [
        "api/app/nutrition_foods.py",
        "api/app/domain/nutrition/catalog.py",
        "frontend/src/app/(app)/nutricao/alimentos/AlimentosClient.tsx",
        "frontend/src/components/nutrition/FoodForm.tsx",
        "frontend/src/components/nutrition/FoodSearch.tsx",
        "frontend/src/components/nutrition/PortionsEditor.tsx",
        "frontend/src/components/nutrition/FieldSourcesNote.tsx",  # M85: nota da origem por campo (084)
        "frontend/src/lib/nutrition-food-form.ts",
    ],
)
def test_arquivos_do_catalogo_de_alimentos_estao_no_escopo_de_nutricao(rel: str):
    """Nutrição L3a: os arquivos novos do catálogo ficam sob `ESCOPO_NUTRICAO`
    (os padrões com número genérico, como `kcal_por_grama`, valem neles)."""
    assert (RAIZ / rel).is_file(), rel
    assert any(_casa_escopo(item, rel) for item in ESCOPO_NUTRICAO), rel


@pytest.mark.parametrize(
    "rel",
    [
        "api/app/external_food_sources.py",
        "api/app/nutrition_external_foods.py",
        "api/app/domain/nutrition/external_sources.py",
        "frontend/src/components/nutrition/BarcodeLookup.tsx",
        "frontend/src/components/nutrition/UsdaSearch.tsx",
    ],
)
def test_arquivos_das_fontes_externas_estao_no_escopo_de_nutricao(rel: str):
    """Nutrição L3b: cliente HTTP, rotas, motor e telas das fontes externas ficam
    sob `ESCOPO_NUTRICAO` (onde `fontes_externas_alimentos` e os padrões com
    número genérico valem)."""
    assert (RAIZ / rel).is_file(), rel
    assert any(_casa_escopo(item, rel) for item in ESCOPO_NUTRICAO), rel


@pytest.mark.parametrize(
    "rel",
    [
        "api/app/nutrition_meals.py",
        "api/app/nutrition_days.py",
        "api/app/domain/nutrition/diary.py",
        "frontend/src/app/(app)/nutricao/NutricaoClient.tsx",
        "frontend/src/components/nutrition/DayHeader.tsx",
        "frontend/src/components/nutrition/MealCard.tsx",
        "frontend/src/components/nutrition/MealEditor.tsx",
        "frontend/src/components/nutrition/MealList.tsx",
        "frontend/src/components/nutrition/TargetVsIntakeCard.tsx",
        "frontend/src/components/nutrition/TrainingKcalRow.tsx",
        "frontend/src/components/nutrition/WaterHungerInputs.tsx",
        "frontend/src/components/nutrition/WeekStrip.tsx",
        "frontend/src/lib/nutrition-meal-form.ts",
    ],
)
def test_arquivos_do_diario_estao_no_escopo_de_nutricao(rel: str):
    """Nutrição L4: os arquivos do diário ficam sob `ESCOPO_NUTRICAO` (vale
    `soma_nutrientes`, `kcal_por_grama`...); `nutrition_days.py` decide "hoje"
    e está em `MODULOS_DE_MOTOR` (`relogio_sem_fuso`)."""
    assert (RAIZ / rel).is_file(), rel
    assert any(_casa_escopo(item, rel) for item in ESCOPO_NUTRICAO), rel
    assert "api/app/nutrition_days.py" in MODULOS_DE_MOTOR


@pytest.mark.parametrize("rel", ["api/app/nutrition_ai_quota.py", "api/app/domain/nutrition/ai_usage.py"])
def test_arquivos_do_uso_de_ia_estao_no_escopo_de_nutricao(rel: str):
    """Nutrição L5a: o serviço do `ai_usage_log` e o dono ficam sob
    `ESCOPO_NUTRICAO`; o serviço decide "hoje" do usuário e está em
    `MODULOS_DE_MOTOR` (`relogio_sem_fuso`)."""
    assert (RAIZ / rel).is_file(), rel
    assert any(_casa_escopo(item, rel) for item in ESCOPO_NUTRICAO), rel
    assert "api/app/nutrition_ai_quota.py" in MODULOS_DE_MOTOR


@pytest.mark.parametrize(
    "rel",
    [
        "api/app/domain/nutrition/meal_parse.py",
        "api/app/nutrition_meal_parse.py",
        "api/app/nutrition_meal_parse_ai.py",
        "api/app/nutrition_meal_parse_worker.py",
        "frontend/src/components/nutrition/MealParseModal.tsx",
        "frontend/src/lib/nutrition-meal-parse.ts",
    ],
)
def test_arquivos_da_leitura_por_ia_estao_no_escopo_de_nutricao(rel: str):
    """Nutrição L5b: a rota, o prompt, o worker, o dono e a tela da leitura da
    refeição por IA ficam sob `ESCOPO_NUTRICAO` (vale `nota_matching`,
    `soma_nutrientes`, `cota_ia_copia_nutricao`...)."""
    assert (RAIZ / rel).is_file(), rel
    assert any(_casa_escopo(item, rel) for item in ESCOPO_NUTRICAO), rel


@pytest.mark.parametrize(
    "rel",
    [
        "api/app/domain/nutrition/label_photo.py",
        "api/app/nutrition_label_ai.py",
        "api/app/nutrition_label_photo.py",
        "api/app/nutrition_label_storage.py",
        "frontend/src/components/nutrition/LabelPhotoUpload.tsx",
    ],
)
def test_arquivos_da_foto_do_rotulo_estao_no_escopo_de_nutricao(rel: str):
    """Nutrição L6: motor, rota, armazenamento, worker e tela da foto do rótulo
    ficam sob `ESCOPO_NUTRICAO` (valem `foto_rotulo_numeros`, `soma_nutrientes`,
    `kcal_por_grama`...); o helper genérico `image-resize.ts` entra só no escopo
    de `foto_rotulo_numeros`."""
    assert (RAIZ / rel).is_file(), rel
    assert any(_casa_escopo(item, rel) for item in ESCOPO_NUTRICAO), rel
    assert "frontend/src/lib/image-resize.ts" in PADROES_POR_CHAVE["foto_rotulo_numeros"].escopo


def test_comentario_e_docstring_nao_contam_mas_sql_e_prompt_contam():
    py = (
        '"""Epley: carga x (1 + (reps + RIR)/30)."""\n'
        "# 10 - rpe aqui é prosa\n"
        "def f():\n"
        '    """date.today() na docstring."""\n'
        '    sql = "SELECT COALESCE(a.started_at, a.ended_at, a.created_at)"  # 0.75\n'
        "    return 1\n"
    )
    linhas = codigo_python(py)
    assert "30" not in linhas[0] and "rpe" not in linhas[1] and "today" not in linhas[3]
    assert "COALESCE(a.started_at" in linhas[4] and "0.75" not in linhas[4]

    ts = (
        "/** SECONDARY_FACTOR = 0.5 no doc */\n"
        "const url = 'https://x/y'; // 7.0 / dias\n"
        '<input accept="image/*" />\n'
        "{/* '8-12' em JSX */}\n"
        "const r = '8-12';\n"
    )
    linhas_ts = codigo_ts(ts)
    assert "SECONDARY" not in linhas_ts[0]
    assert "https://x/y" in linhas_ts[1] and "7.0" not in linhas_ts[1]
    assert 'accept="image/*"' in linhas_ts[2]
    assert "8-12" not in linhas_ts[3] and "8-12" in linhas_ts[4]


def test_violacoes_cobrem_entrada_nova_subida_e_queda():
    padrao = PADROES_POR_CHAVE["epley"]
    erros = violacoes(padrao, {"a.py": 1, "b.py": 3, "c.py": 1}, {"b.py": 2, "c.py": 2, "d.py": 1})
    texto = "\n".join(erros)
    assert "a.py: 1 ocorrência(s) nova(s)" in texto
    assert "b.py: subiu de 2 para 3" in texto
    assert "c.py: caiu de 2 para 1" in texto and "Reduza a allowlist para 1" in texto
    assert "d.py: caiu de 1 para 0" in texto and "remova a entrada" in texto
    assert DOC in texto and padrao.donos[0] in texto
    assert violacoes(padrao, {"b.py": 2}, {"b.py": 2}) == []


def test_allowlist_e_donos_coerentes():
    assert set(ALLOWLIST) == set(PADROES_POR_CHAVE), "toda regra tem allowlist (mesmo vazia) e vice-versa"
    for padrao in PADROES:
        assert padrao.donos or padrao.dono_pendente, f"[{padrao.chave}] sem dono nem lote que cria o dono"
        for dono in padrao.donos:
            assert (RAIZ / dono).is_file(), f"[{padrao.chave}] dono inexistente: {dono}"
        for rel, n in ALLOWLIST[padrao.chave].items():
            assert (RAIZ / rel).is_file(), f"[{padrao.chave}] arquivo da allowlist sumiu: {rel} (remova a entrada)"
            assert rel not in padrao.donos, f"[{padrao.chave}] dono não entra na allowlist: {rel}"
            assert n > 0, f"[{padrao.chave}] contagem zero: remova a entrada {rel}"
    for item in MODULOS_DE_MOTOR:
        assert (RAIZ / item).exists(), f"módulo de motor inexistente: {item}"


def test_a_guarda_enxerga_a_regra_no_dono():
    """Se a regex parar de casar, a guarda passaria por não ver nada: o dono
    TEM a regra hoje (exceto onde o dono ainda vai nascer)."""
    sem_regra_no_dono_hoje = {
        "numeros_prompt_volume",  # proibição pura: o dono interpola a tabela, sem faixa escrita
        "cardio_padrao_sem_modo",  # proibição pura: o dono junta o modo (`default_prescription`), sem `describe(DEFAULTS[`
        "zonas_fc_paralelas",  # M98 C0: o dono guarda os percentuais em tupla nomeada, sem "N% da FCmáx"
        "cardio_paralelo",  # M98 C2: proibição pura; o dono tem `CARDIO_PATTERNS`/`is_cardio`, sem as cópias
        "trained_at",  # trained_at_sql() nasce no Lote 2
        "normalizacao_semanal",  # weekly_normalization() nasce no Lote 4
        "relogio_sem_fuso",  # o dono usa datetime.now(timezone.utc), que a regex não pega de propósito
        "motor_legado_carga",  # proibição pura: o dono (prescription_service) é o motor v2, sem o nome legado
        # `progression_engine` soma um incremento em kg no passo; o percentual de uma regra cadastrada
        # vira kg só em `next_set_load._percent_load` (a forma `load * (1 ± x / 100)` está lá).
        "carga_percentual",
        "rpe_texto_paralelo",  # proibição pura: o dono é `clamp_rpe_text`, os nomes antigos não voltam
        "slot_por_nome",  # proibição pura: o dono casa por `SessionSlot.key` (Modelo + posição), nunca por nome
        "percentual_de_carga",  # o dono trabalha em kg (passo do equipamento), nunca em percentual
        # proibição pura: o dono compara pela função genérica (`_compare`), sem `rule.reps.op ==` nem
        # `magnitude.value / 100` escritos à mão fora dele.
        "regra_carga_cadastrada",
        "fatores_carga_sessao",  # o dono usa constantes nomeadas (WARMUP_LOAD_FACTORS, DROP_SET_LOAD_FACTOR...)
        "dose_reduzida_texto",  # o dono GERA o texto (reduction_policy/reduced_dose_prompt_rule), sem "1-2"
        "sinais_expiram",  # proibição pura: o dono tem SIGNALS_POLICY, sem reset
        "dose_sem_rpe",  # o dono usa constantes nomeadas (DOSE_REPS_WHEN_MISSING, DOSE_RPE_WHEN_MISSING)
        "set_type_paralelo",  # o dono gera o CASE (`sql_canonical_set_type_case`) e o FE lê o JSON exportado
        "recorde_paralelo",  # proibição pura: o dono é `record_events`, os critérios antigos não voltam
        "payload_ativacao_sem_tecnica",  # o dono monta o payload pelos campos (ACTIVATION_PAYLOAD_FIELDS)
        "trilha_sem_contexto",  # proibição pura: o dono resolve por `track_key`/`resolve_track`
        "e1rm_confiavel_paralelo",  # proibição pura: o dono compara com o `max_reps` do argumento
        "exposicoes_minimas",  # o dono guarda o número (`plateau_min_exposures`), sem a frase
        "fator_carga_trabalho_texto",  # o dono usa constantes (WARMUP_LOAD_FACTORS, FEELER_*), sem prosa
        "faixa_reps_prosa",  # o dono é Python (`DEFAULT_REPS_TARGET`); a regex só varre o RAG geral
        "numeros_prompt_cardio",  # o dono usa constantes nomeadas e GERA o texto (helpers de `cardio_policy`)
        "numeros_rag_cardio",  # M98 C3a: proibição pura no Markdown; o dono só mapeia placeholder -> constante
        "tonelagem_convertida",  # proibição pura: o dono formata em kg (`format_tonnage`), nunca em toneladas
        "vocabulario_ia_serie",  # proibição pura: o dono usa os códigos do motor (HOLD/INCREASE_LOAD/DECREASE_LOAD)
        "limiar_dor_muscular",  # o dono compara com a constante nomeada (MUSCLE_PAIN_BLOCK_ABOVE), nunca com literal
        "faixas_sem_modo",  # proibição pura: o loader dono sempre filtra pelo modo
        "nota_matching",  # proibição pura: o dono compara com constantes nomeadas (MATCH_MIN_SCORE...)
        "bom_para_paralelo",  # proibição pura: o dono busca o 1º minuto READY pela projeção, sem somar horas
        "soma_nutrientes",  # o dono soma por `NUTRIENT_FIELDS` e escala por `PER_100G_REFERENCE`, sem o literal
        "cota_ia_copia_nutricao",  # proibição pura: o dono tem `DAILY_QUOTA` e a tabela de preços, sem env
        "fallback_registro_serie_cadeia",  # só TS do Modo Treino; o dono é Python (`SET_LOGGER_FALLBACK_*`)
        "objetivo_treino_filtro_sql",  # o dono monta o fragmento pela constante TRAINING_GOAL_COLUMN, sem o nome literal
        "deload_filtro_sql",  # o dono monta o fragmento pela constante DELOAD_SOURCE_COLUMN, sem o nome literal
        "medida_serie",  # o dono Python casa pelo Enum (`DURATION = "duration"`); o espelho TS lê o JSON e compara por `MEASURE_VALUES`, sem literal
    }
    for padrao in PADROES:
        if padrao.chave in sem_regra_no_dono_hoje:
            continue
        for dono in padrao.donos:
            linhas = codigo_python((RAIZ / dono).read_text(encoding="utf-8"))
            assert any(padrao.regex.search(linha) for linha in linhas), f"[{padrao.chave}] regex não vê a regra no dono {dono}"


def test_o_mapa_cita_cada_padrao():
    texto = (RAIZ / DOC).read_text(encoding="utf-8")
    faltando = [p.chave for p in PADROES if f"`{p.chave}`" not in texto]
    assert not faltando, f"{DOC} precisa descrever os padrões: {faltando}"


def test_guarda_varre_o_rag_do_submodulo_privado():
    """M103 P0: o RAG mora no submódulo `api/privado` (muscle_fit_privado).
    Sem o submódulo a varredura pularia o RAG em silêncio (`pasta.exists()`)
    e a guarda ficaria verde sem olhar prompt nenhum. Falha alto."""
    md = [rel for rel, linguagem, _ in arquivos_varridos() if linguagem == "md"]
    assert md, "a guarda não achou o RAG: rode `submodule update --init api/privado`"
    assert all(rel.startswith("api/privado/rag/markdown/") for rel in md)


def test_guarda_varre_os_prompts_do_submodulo_privado():
    """M103 P1: a prosa dos prompts saiu dos literais `.py` para
    `api/privado/prompts/**`. Sem o submódulo a guarda pularia os templates em
    silêncio; com ele, todo template entra como linguagem "prompt" (o README não)."""
    prompts = [rel for rel, linguagem, _ in arquivos_varridos() if linguagem == "prompt"]
    assert prompts, "a guarda não achou os prompts: rode `submodule update --init api/privado`"
    assert all(rel.startswith(f"{PROMPTS_PRIVADOS}/") and not rel.endswith("/README.md") for rel in prompts)


def test_todo_template_de_prompt_tem_consumidor():
    """O escopo de um padrão vale para o template pelo módulo que o consome:
    pasta nova em `api/privado/prompts/` exige registrar o consumidor em
    `CONSUMIDOR_DO_PROMPT` (e o módulo tem de existir)."""
    sem_consumidor = sorted(
        rel for rel, linguagem, _ in arquivos_varridos() if linguagem == "prompt" and consumidor_do_prompt(rel) is None
    )
    assert not sem_consumidor, sem_consumidor
    assert all((RAIZ / modulo).is_file() for modulo in CONSUMIDOR_DO_PROMPT.values())


def test_template_de_prompt_e_varrido_como_o_literal_do_consumidor(tmp_path: Path):
    """Mesmo rigor dos literais `.py`: a `regex` de código (não a do RAG), o
    escopo do módulo consumidor, e o template nunca é dono."""
    cardio = tmp_path / "api/privado/prompts/cardio_agent/cardio-agent-v9.md"
    cardio.parent.mkdir(parents=True)
    cardio.write_text("Prefira 30 minutos em Z2.\nPAI acima de {{pai_meta_min}}.\n", encoding="utf-8")
    assert ocorrencias(PADROES_POR_CHAVE["numeros_prompt_cardio"], tmp_path) == {
        "api/privado/prompts/cardio_agent/cardio-agent-v9.md": [(1, "Prefira 30 minutos em Z2.")],
    }
    # Escopo da nutrição: o template da leitura da refeição herda o escopo de
    # `api/app/nutrition_meal_parse_ai.py`; o da descrição de execução, não.
    # Outra raiz: a listagem de arquivos é cacheada por raiz.
    padrao, raiz = PADROES_POR_CHAVE["kcal_por_grama"], tmp_path / "nutricao"
    for pasta in ("nutrition_meal_parse", "exercise_description"):
        destino = raiz / f"api/privado/prompts/{pasta}/v1.md"
        destino.parent.mkdir(parents=True)
        destino.write_text("kcal = p * 4\n", encoding="utf-8")
    assert ocorrencias(padrao, raiz) == {"api/privado/prompts/nutrition_meal_parse/v1.md": [(1, "kcal = p * 4")]}

