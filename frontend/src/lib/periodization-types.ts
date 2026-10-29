// Tipos do Motor de Periodização (PR-17 — frontend; PLANO_20260912.md §7.1 e
// §7.2). Contratos confirmados lendo o backend já mergeado no PR-16:
//
//   - pedido: `PeriodizationDraftIn` em api/app/models.py;
//   - endpoint: `POST /workout-plan-templates/periodization-drafts`
//     (api/app/workout_plan_templates.py) — 202 com `{job_id, status,
//     generation_log_id}`, restrito a admin/PT (`require_admin_or_pt`, D17);
//   - resultado: `run_periodization_draft` em api/app/periodization/core.py,
//     entregue por `GET /jobs/{job_id}` no campo `result`;
//   - relatório: `ValidationReport` em api/app/periodization/validation.py.
//
// NADA neste fluxo grava modelo: o rascunho vive no job (DynamoDB com TTL) e
// só existe como modelo depois do `POST /workout-plan-templates` explícito
// (aí sim com `generation_log_id`, que é o que marca `origin='periodization'`).

import motorConstants from './motor-constants.generated.json';
import type { VolumeMode, WorkoutPlanTemplateSlot } from './workout-plan-templates-types';

// Tetos do complemento de volume (M76 Lote 4c, E9): dono
// `api/app/periodization/limits.py`, exportado em `motor-constants.generated.json`.
const LIMITS = motorConstants.periodization_limits;

// --- Pedido -----------------------------------------------------------------

// Espelha `PeriodizationDraftIn`. `rag_sources` são nomes de subpastas de
// `rag/markdown/TIPOS_TREINO` (GET /training-types/rag-folders); o backend
// recusa fonte inexistente com 422.
export type PeriodizationDraftRequest = {
  rag_sources: string[];
  training_type_id: number | null;
  volume_mode: VolumeMode;
  /** Lote 3/5: modo de contagem do volume (valor de `VolumeCountingMode`); só no Modo B.
   * Backend sem o campo o ignora (Pydantic com `extra` default). */
  volume_counting_mode?: string;
  gym_id: number | null;
  days_per_cycle: number;
  instructions: string | null;
  /** M97 F5: objetivo do Modelo (valor de `GET /training-goals`); ausente = default. */
  training_goal?: string;
  /** M97 F5: objetivo de cada dia (um por dia do ciclo); ausente = todos no do Modelo. */
  day_goals?: string[];
  /** M98 C5: `true` = o Modelo leva cardio, `false` = nunca; ausente = o backend decide pelas
   * preferências (dono do gatilho: `api/app/periodization/cardio_plan.py`). */
  include_cardio?: boolean;
};

/** M98: escolha do cardio no modal. `auto` não envia `include_cardio` (o backend decide). */
export type PeriodizationCardioChoice = 'auto' | 'include' | 'exclude';

// Estado do formulário do modal (ids como string por causa dos `Select` do
// Mantine, convertidos para número só na montagem do pedido).
export type PeriodizationFormState = {
  ragSources: string[];
  trainingTypeId: string | null;
  volumeMode: VolumeMode;
  /** Modo de contagem do volume (só vale no Modo B). */
  countingMode: string;
  gymId: string | null;
  daysPerCycle: number;
  instructions: string;
  /** M97 F5: objetivo do Modelo; `null` = default do dono (o pedido não leva o campo). */
  trainingGoal: string | null;
  /** M97 F5: objetivo por dia (posição = dia - 1); `null` = herda o do Modelo. */
  dayGoals: (string | null)[];
  /** M98: cardio no Modelo; `auto` (padrão) = o pedido não leva `include_cardio`. */
  cardio: PeriodizationCardioChoice;
};

// --- Resposta 202 -----------------------------------------------------------

export type PeriodizationDraftAccepted = {
  job_id: string;
  status: string;
  // Linha de auditoria (`template_generation_logs`) criada ANTES da Lambda —
  // é ela que conta o limite diário e que, reenviada no save, fecha a
  // rastreabilidade do modelo.
  generation_log_id: number | null;
};

// --- Relatório de validação -------------------------------------------------

// Uma entrada do relatório sempre tem `code`; os demais campos variam por
// código (ver `validation.py`). Mantido aberto de propósito: um código novo no
// backend não deve quebrar a tela, só cair no texto genérico.
export type PeriodizationReportEntry = {
  code: string;
  [field: string]: unknown;
};

export type PeriodizationValidationReport = {
  errors: PeriodizationReportEntry[];
  warnings: PeriodizationReportEntry[];
  auto_fixes: PeriodizationReportEntry[];
};

// Rascunho = dump de `WorkoutPlanTemplateIn`, o MESMO modelo do
// `POST /workout-plan-templates` (`validation.py::_to_template_in`).
export type PeriodizationDraftTemplate = {
  name: string;
  description: string | null;
  training_type_id: number | null;
  volume_mode: VolumeMode;
  /** Lote 5 (#550): modo de contagem do rascunho; ausente em job antigo. */
  volume_counting_mode?: string | null;
  /** M97 F5: objetivo do Modelo do rascunho; ausente em job antigo. */
  training_goal?: string | null;
  is_active: boolean;
  generation_log_id: number | null;
  // Grade sugerida pelo backend (`validation.py::_suggest_grid`, migração 062).
  // Opcionais: um job antigo pode não trazê-las.
  grid_rows?: number | null;
  grid_cols?: number | null;
  slots: WorkoutPlanTemplateSlot[];
};

// `result` do job quando ele termina `done`.
export type PeriodizationJobResult = {
  draft: PeriodizationDraftTemplate;
  validation_report: PeriodizationValidationReport;
  rationale: string;
  source_citations: string[];
  generation_log_id: number | null;
  model: string;
  prompt_version: string;
};

// --- Falha ------------------------------------------------------------------

// Erro estrutural termina o job em `failed` com o relatório serializado em
// JSON dentro de `job.error` (`PeriodizationError.detail`). Falha de
// configuração (sem `OPENAI_MODEL_PERIODIZATION`) ou da OpenAI vem como texto
// simples no mesmo campo.
export type PeriodizationFailure = {
  message: string;
  validationReport: PeriodizationValidationReport | null;
  generationLogId: number | null;
  model: string | null;
  promptVersion: string | null;
};

// --- Rascunho pronto para o builder ----------------------------------------

// O que o builder precisa para abrir em modo "rascunho não salvo". Guardado em
// `sessionStorage` (não em banco) entre a listagem e o builder.
export type PeriodizationSeed = {
  header: {
    name: string;
    description: string;
    trainingTypeId: string | null;
    volumeMode: VolumeMode;
    /** Modo de contagem do rascunho; ausente = default. */
    countingMode?: string;
    /** M97 F5: objetivo do Modelo do rascunho; ausente/`null` = default. */
    trainingGoal?: string | null;
    isActive: boolean;
  };
  slots: WorkoutPlanTemplateSlot[];
  // Grade sugerida no rascunho (`grid_rows`/`grid_cols`). `null` = o builder
  // calcula a padrão. Opcional porque um rascunho gravado na sessão antes
  // deste campo existir não o tem.
  grid?: { rows: number; cols: number } | null;
  report: PeriodizationValidationReport;
  rationale: string;
  sourceCitations: string[];
  generationLogId: number | null;
  model: string;
  promptVersion: string;
};

// --- Rótulos ----------------------------------------------------------------

// Títulos curtos por código, para o painel lateral. Código desconhecido cai no
// próprio código (nunca em texto inventado).
export const PERIODIZATION_REPORT_CODE_LABEL: Record<string, string> = {
  SCHEMA_INVALID: 'Formato inválido na resposta do modelo',
  VOLUME_MODE_MISMATCH: 'Modo de volume divergente do pedido',
  MUSCLE_GROUP_NOT_FOUND: 'Grupo muscular inexistente',
  EQUIPMENT_NOT_IN_GYM: 'Equipamento fora da academia',
  GYM_REQUIRED: 'Academia obrigatória',
  TEMPLATE_INVALID: 'Modelo recusado pelas regras de save',
  SLOT_COUNT_MISMATCH: 'Número de dias diferente do pedido',
  ACTIVATION_BELOW_MIN: 'Ativação abaixo da meta',
  ACTIVATION_ABOVE_MAX: 'Ativação acima da meta',
  ACTIVATION_COVERAGE: 'Cobertura da meta de ativação',
  METHODOLOGY_BELOW_RANGE: 'Séries diretas abaixo da faixa da metodologia',
  METHODOLOGY_ABOVE_RANGE: 'Séries diretas acima da faixa da metodologia',
  // Fase 6: eixo SEMANAL do macro-grupo.
  METHODOLOGY_WEEKLY_BELOW_RANGE: 'Séries diretas por semana abaixo da meta do macro-grupo',
  METHODOLOGY_WEEKLY_ABOVE_RANGE: 'Séries diretas por semana acima da meta do macro-grupo',
  SLOT_TOO_FEW_EXERCISES: 'Sessão com poucos exercícios',
  RPE_CLAMPED: 'RPE limitado por segurança',
  TECHNIQUE_REMOVED: 'Técnica avançada removida',
  // Auto-fixes estruturais (confiabilidade da geração, 2026-09-28): o que
  // antes matava o rascunho inteiro agora chega consertado e auditável.
  EQUIPMENT_REPLACED: 'Equipamento trocado por um da academia',
  // 2026-10-16: par da academia rotulado com o grupo pai/irmão volta para o
  // grupo sob o qual o catálogo o lista (só o agrupamento muda, o volume não).
  MUSCLE_GROUP_RELABELED: 'Grupo muscular do exercício corrigido',
  // 2026-10-16: 0 em campo opcional (aquecimento, feeler, pausa...) vira vazio.
  OPTIONAL_ZERO_NULLED: 'Campo opcional com zero ficou vazio',
  SLOT_REMOVED: 'Dia extra removido do ciclo',
  SLOT_ADDED: 'Dia de descanso acrescentado ao ciclo',
  SLOT_SEQUENCE_RENUMBERED: 'Ordem dos dias renumerada',
  SLOT_TYPE_FIXED: 'Descanso com exercícios virou dia de treino',
  PRIORITIZE_UNMARKED: 'Prioridade extra desmarcada',
  SETS_COUNT_FIXED: 'Número de séries corrigido',
  ITEM_REMOVED: 'Item incompleto removido',
  // Fase 5 (PR 5): FST-7/GVT só preenchem o número de séries.
  TECHNIQUE_FIELDS_CLEARED: 'Aplicações/pausa zeradas (FST-7/GVT)',
  // M46: complemento determinístico de volume (validation.py ->
  // volume_complement.py), depois da segurança e antes da metodologia.
  VOLUME_SETS_ADDED: 'Série acrescentada para fechar a faixa',
  VOLUME_EXERCISE_ADDED: 'Exercício acrescentado para fechar a faixa',
  VOLUME_TECHNIQUE_ADDED: 'Técnica acrescentada para fechar a faixa',
  VOLUME_SETS_ABOVE_IDEAL: `Exercício passou de ${LIMITS.sets_per_exercise_ideal_max} séries para fechar a faixa`,
  VOLUME_GAP_UNRESOLVED: 'Volume abaixo da faixa sem complemento possível',
  // 2026-10-16: corte de séries de grupo acima da faixa, depois do complemento.
  VOLUME_REDUCED: 'Série retirada para entrar na faixa',
  // M97 F5: objetivo por dia (`periodization/goal_plan.py`).
  ISOLATION_GOAL_OVERRIDE: 'Isolador do dia em faixa moderada',
  GOAL_REPS_FIXED: 'Repetições ajustadas à faixa do objetivo',
  DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE: 'Compostos pesados do dia fora da faixa do objetivo',
};

// Motivos de `VOLUME_GAP_UNRESOLVED.reasons` (volume_complement.py).
export const VOLUME_GAP_REASON_LABEL: Record<string, string> = {
  NO_SLOT_IN_WINDOW: 'nenhum dia da janela de 7 dias treina o grupo',
  EXERCISE_CAP: `dia já com ${LIMITS.complement_max_exercises_per_slot} exercícios`,
  TIME_LIMIT: `passaria de ${LIMITS.slot_time_limit_minutes} min no dia`,
  RANGE_LIMIT: 'passaria da faixa deste ou de outro grupo',
  NO_CATALOG_PAIR: 'nenhum exercício novo do grupo na academia',
  NO_TECHNIQUE: 'nenhum drop set / rest-pause permitido pela segurança do equipamento',
  MAX_SETS_PER_EXERCISE: `exercícios do grupo já com ${LIMITS.complement_max_sets_per_exercise} séries`,
};

export const PERIODIZATION_VOLUME_MODE_HINT: Record<VolumeMode, string> = {
  percent_budget:
    'O modelo prescreve grupos musculares com faixa % da meta semanal; a academia é opcional (só refina o contexto).',
  equipment_series:
    'O modelo escolhe equipamento, exercício e séries fixas — por isso a academia é obrigatória neste modo.',
};

// M98: opções do cardio no modal. Só rótulo e texto de tela: QUANDO o Modelo leva
// cardio no `auto` é decidido no backend (`cardio_plan.cardio_requested`).
export const PERIODIZATION_CARDIO_OPTIONS: { value: PeriodizationCardioChoice; label: string; description: string }[] = [
  {
    value: 'auto',
    label: 'Automático (pelas preferências)',
    description: 'O Modelo leva cardio só se as instruções livres falarem de cardio.',
  },
  {
    value: 'include',
    label: 'Incluir cardio',
    description: 'O Modelo leva sessões de cardio, ajustadas aos dias de perna e à sua recuperação.',
  },
  { value: 'exclude', label: 'Sem cardio', description: 'O Modelo não leva cardio, mesmo que as instruções falem dele.' },
];
