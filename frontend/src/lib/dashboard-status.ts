import {
  IconAlertTriangle,
  IconArrowUpRight,
  IconArrowsExchange,
  IconBan,
  IconBarbell,
  IconBarbellOff,
  IconBed,
  IconBook,
  IconCalculator,
  IconCircleCheck,
  IconCircleDashed,
  IconCirclePlus,
  IconFlame,
  IconHelpCircle,
  IconInfoCircle,
  IconMinus,
  IconPlayerPause,
  IconPlayerPlay,
  IconTrendingDown,
  IconTrendingUp,
  IconUserEdit,
} from '@tabler/icons-react';
import type { Icon as TablerIcon } from '@tabler/icons-react';
import { BRAND } from '@/theme';
import motorConstants from './motor-constants.generated.json';

/**
 * FONTE ÚNICA de rótulo, cor e ícone de TODO enum de status do dashboard
 * (plano §11 e critério de aceite da F0.4).
 *
 * Antes desta etapa os mesmos estados tinham textos diferentes em 6 telas
 * (`MuscleStatusBadge`, `TREND_META`, `CLASSIFICATION_META`, `STATUS_META`,
 * `muscle-readiness.ts`, `/treinos`, `/recuperacao-muscular`). Aqui eles moram
 * uma vez só; as telas importam e NUNCA redefinem.
 *
 * Regras que este módulo materializa:
 * - a CLASSIFICAÇÃO é sempre do backend; aqui só há apresentação;
 * - cor nunca é o único sinal (§14 do plano): todo estado tem `label` e
 *   `Icon`, e os componentes renderizam os dois;
 * - `hex` existe porque as mesmas cores alimentam SVG (`recharts` e o
 *   `MuscleBodyMap`, que tinge a arte do corpo a partir do hex), onde token
 *   `var(--mantine-color-*)` não serve.
 */
export type StatusVisual = {
  /** Rótulo em pt-BR exibido ao usuário. */
  label: string;
  /** Cor Mantine (`Badge color=`). */
  color: string;
  /** Mesma cor em hex, para marcas SVG. */
  hex: string;
  Icon: TablerIcon;
  /** Frase curta do que o estado significa (tooltip/legenda). */
  description?: string;
};

const GREEN = '#2f9e44';
const TEAL = '#0ca678';
const YELLOW = '#f08c00';
const GRAY = '#868e96';
const DEEP_ORANGE = '#f76707';

export const UNKNOWN_STATUS: StatusVisual = {
  label: 'Desconhecido',
  color: 'gray',
  hex: GRAY,
  Icon: IconHelpCircle,
  description: 'Estado não previsto no contrato desta versão do frontend.',
};

// ---------------------------------------------------------------------------
// Volume semanal — `classify_weekly_volume_status` (5 estados)
// ---------------------------------------------------------------------------

export const WEEKLY_VOLUME_STATUS_ORDER = [
  'BELOW_MIN',
  'BETWEEN_MIN_TARGET',
  'ON_TARGET',
  'BETWEEN_TARGET_MAX',
  'ABOVE_MAX',
] as const;

export type WeeklyVolumeStatusCode = (typeof WEEKLY_VOLUME_STATUS_ORDER)[number];

/**
 * `BETWEEN_MIN_TARGET` e `ABOVE_MAX` compartilham a família laranja (hex
 * distintos: #f08c00 e #f76707). Como a diferença de matiz não é legível num
 * badge pequeno, os ÍCONES os separam: seta de aproximação vs. alerta.
 */
const WEEKLY_VOLUME_STATUS: Record<WeeklyVolumeStatusCode, StatusVisual> = {
  BELOW_MIN: {
    label: 'Abaixo do mínimo',
    color: 'red',
    hex: '#e03131',
    Icon: IconTrendingDown,
    description: 'Volume semanal abaixo do mínimo da Meta de Ativação.',
  },
  BETWEEN_MIN_TARGET: {
    label: 'Aproximando da meta',
    color: 'orange',
    hex: YELLOW,
    Icon: IconArrowUpRight,
    description: 'Entre o mínimo e o alvo.',
  },
  ON_TARGET: {
    label: 'Na meta',
    color: 'green',
    hex: GREEN,
    Icon: IconCircleCheck,
    description: 'Dentro da tolerância do alvo.',
  },
  BETWEEN_TARGET_MAX: {
    label: 'Acima da meta',
    color: 'teal',
    hex: TEAL,
    Icon: IconTrendingUp,
    description: 'Entre o alvo e o máximo.',
  },
  ABOVE_MAX: {
    label: 'Acima do máximo',
    color: 'orange',
    hex: DEEP_ORANGE,
    Icon: IconAlertTriangle,
    description: 'Volume semanal acima do máximo da Meta de Ativação.',
  },
};

// ---------------------------------------------------------------------------
// Volume planejado x realizado — `classify_planned_volume_status` (3 estados)
// ---------------------------------------------------------------------------

export const PLANNED_VOLUME_STATUS_ORDER = ['BELOW_TARGET', 'ON_TARGET', 'ABOVE_TARGET'] as const;

export type PlannedVolumeStatusCode = (typeof PLANNED_VOLUME_STATUS_ORDER)[number];

/**
 * Meta vinda do PRÓPRIO Modelo por equipamento (`target_source = 'template_plan'`).
 * Um plano fixo não tem mínimo nem máximo, então só existem três respostas:
 * abaixo, dentro da tolerância ou acima do que foi planejado. É um domínio
 * PRÓPRIO, não uma extensão dos 5 estados — a legenda do mapa corporal e a
 * paridade com o backend continuam falando de 5.
 */
const PLANNED_VOLUME_STATUS: Record<PlannedVolumeStatusCode, StatusVisual> = {
  BELOW_TARGET: {
    label: 'Abaixo do planejado',
    color: 'red',
    hex: '#e03131',
    Icon: IconTrendingDown,
    description: 'Volume semanal abaixo do que o seu Modelo planeja para este grupo.',
  },
  ON_TARGET: {
    label: 'No planejado',
    color: 'green',
    hex: GREEN,
    Icon: IconCircleCheck,
    description: 'Dentro da tolerância do que o seu Modelo planeja.',
  },
  ABOVE_TARGET: {
    label: 'Acima do planejado',
    color: 'teal',
    hex: TEAL,
    Icon: IconTrendingUp,
    description: 'Volume semanal acima do que o seu Modelo planeja — trabalho extra.',
  },
};

/**
 * Qual domínio classifica o `status` de um grupo: com meta do próprio Modelo
 * (`template_plan`) são os 3 estados de `plannedVolume`; nos demais, os 5 de
 * `weeklyVolume`. Decidido pela ORIGEM da meta, nunca adivinhando pelo código.
 */
export function volumeStatusDomain(
  targetSource: string | null | undefined,
): 'plannedVolume' | 'weeklyVolume' {
  return targetSource === 'template_plan' ? 'plannedVolume' : 'weeklyVolume';
}

/**
 * Gravidade ADITIVA do `BELOW_TARGET` planejado
 * (`volume_service.planned_volume_severity`, decisão de 2026-10-07): o
 * backend decide quando o realizado está "muito abaixo do planejado"; aqui só
 * há o visual. Não é um quarto estado de `plannedVolume` — a legenda e a
 * paridade dos 3 estados não mudam.
 */
export type PlannedVolumeSeverity = 'FAR_BELOW';

const PLANNED_VOLUME_SEVERITY: Record<PlannedVolumeSeverity, StatusVisual> = {
  FAR_BELOW: {
    label: 'Muito abaixo do planejado',
    color: 'red',
    hex: '#e03131',
    Icon: IconAlertTriangle,
    description: 'Realizado bem abaixo do que o seu Modelo planeja para este grupo.',
  },
};

/** Visual da gravidade, ou `null` sem gravidade (o status normal vale). */
export function plannedSeverityVisual(severity: string | null | undefined): StatusVisual | null {
  if (!severity) return null;
  return (PLANNED_VOLUME_SEVERITY as Record<string, StatusVisual | undefined>)[severity] ?? null;
}

// ---------------------------------------------------------------------------
// Progressão por exercício — `ExerciseProgressionStatus` (F0.2, 6 estados)
// ---------------------------------------------------------------------------

export const EXERCISE_PROGRESSION_ORDER = [
  'PROGRESSING',
  'STABLE',
  'PLATEAU_SUSPECTED',
  'REGRESSING',
  'INSUFFICIENT_DATA',
  'NOT_COMPARABLE',
] as const;

export type ExerciseProgressionCode = (typeof EXERCISE_PROGRESSION_ORDER)[number];

// M76 Lote 3c (C13): o rótulo de cada código vem do dono no backend
// (`api/app/domain/progression_status_labels.py`), exportado para
// `motor-constants.generated.json`; aqui só cor, ícone e descrição.
export const EXERCISE_PROGRESSION_LABELS: Readonly<Record<ExerciseProgressionCode, string>> =
  motorConstants.exercise_progression_labels;
const PL = EXERCISE_PROGRESSION_LABELS;

const EXERCISE_PROGRESSION: Record<ExerciseProgressionCode, StatusVisual> = {
  PROGRESSING: { label: PL.PROGRESSING, color: 'green', hex: GREEN, Icon: IconTrendingUp },
  STABLE: { label: PL.STABLE, color: 'blue', hex: '#1971c2', Icon: IconMinus },
  PLATEAU_SUSPECTED: {
    label: PL.PLATEAU_SUSPECTED,
    color: 'yellow',
    hex: YELLOW,
    Icon: IconAlertTriangle,
    description: 'Suspeita, não diagnóstico: sem progressão nem regressão nas últimas exposições.',
  },
  REGRESSING: { label: PL.REGRESSING, color: 'red', hex: BRAND.red, Icon: IconTrendingDown },
  INSUFFICIENT_DATA: {
    label: PL.INSUFFICIENT_DATA,
    color: 'gray',
    hex: GRAY,
    Icon: IconHelpCircle,
    description: 'Menos de 2 exposições comparáveis na janela.',
  },
  NOT_COMPARABLE: {
    label: PL.NOT_COMPARABLE,
    color: 'gray',
    hex: GRAY,
    Icon: IconBan,
    description: 'A última exposição mudou de equipamento/convenção de carga.',
  },
};

// ---------------------------------------------------------------------------
// Progressão por grupo muscular (v2) e tendência por exercício (v2)
// ---------------------------------------------------------------------------

export const MUSCLE_GROUP_PROGRESSION_ORDER = [
  'PROGREDINDO',
  'ESTAVEL',
  'SUSPEITA_PLATO',
  'REGREDINDO',
  'DADOS_INSUFICIENTES',
  'NOVO',
  'SEM_DADOS',
] as const;

export type MuscleGroupProgressionCode = (typeof MUSCLE_GROUP_PROGRESSION_ORDER)[number];

/**
 * Volume (tonelagem) por grupo — rótulos NEUTROS (2026-10-02): reduzir volume
 * de propósito não é regressão, então nada aqui é verde/vermelho. A leitura de
 * desempenho é o domínio `muscleGroupProgression`.
 */
export const MUSCLE_GROUP_VOLUME_ORDER = [
  'VOLUME_UP',
  'VOLUME_STABLE',
  'VOLUME_DOWN',
  'DADOS_INSUFICIENTES',
  'NOVO',
  'SEM_DADOS',
] as const;

export type MuscleGroupVolumeCode = (typeof MUSCLE_GROUP_VOLUME_ORDER)[number];

export const PROGRESSION_TREND_ORDER = [
  'PROGREDINDO',
  'ESTAVEL',
  'ESTAGNADO',
  'REGREDINDO',
  'DADOS_INSUFICIENTES',
] as const;

export type ProgressionTrendCode = (typeof PROGRESSION_TREND_ORDER)[number];

/** Base compartilhada pelos dois enums pt-BR: o que é igual não pode divergir. */
const PT_PROGRESSION_SHARED = {
  PROGREDINDO: EXERCISE_PROGRESSION.PROGRESSING,
  ESTAVEL: EXERCISE_PROGRESSION.STABLE,
  REGREDINDO: EXERCISE_PROGRESSION.REGRESSING,
  DADOS_INSUFICIENTES: EXERCISE_PROGRESSION.INSUFFICIENT_DATA,
} as const;

const MUSCLE_GROUP_PROGRESSION: Record<MuscleGroupProgressionCode, StatusVisual> = {
  ...PT_PROGRESSION_SHARED,
  SUSPEITA_PLATO: EXERCISE_PROGRESSION.PLATEAU_SUSPECTED,
  NOVO: {
    label: 'Sem período anterior',
    color: 'gray',
    hex: GRAY,
    Icon: IconCircleDashed,
    description: 'Não há janela anterior de mesmo tamanho para comparar.',
  },
  SEM_DADOS: { label: 'Sem dados', color: 'gray', hex: GRAY, Icon: IconHelpCircle },
};

const MUSCLE_GROUP_VOLUME: Record<MuscleGroupVolumeCode, StatusVisual> = {
  VOLUME_UP: {
    label: 'Volume maior',
    color: 'indigo',
    hex: '#4263eb',
    Icon: IconTrendingUp,
    description: 'Mais tonelagem por sessão que no período anterior. Não é, sozinho, sinal de progresso.',
  },
  VOLUME_STABLE: { label: 'Volume estável', color: 'gray', hex: GRAY, Icon: IconMinus },
  VOLUME_DOWN: {
    label: 'Volume menor',
    color: 'violet',
    hex: '#7048e8',
    Icon: IconTrendingDown,
    description: 'Menos tonelagem por sessão — pode ser planejado (menos séries). Não é regressão.',
  },
  DADOS_INSUFICIENTES: MUSCLE_GROUP_PROGRESSION.DADOS_INSUFICIENTES,
  NOVO: MUSCLE_GROUP_PROGRESSION.NOVO,
  SEM_DADOS: MUSCLE_GROUP_PROGRESSION.SEM_DADOS,
};

const PROGRESSION_TREND: Record<ProgressionTrendCode, StatusVisual> = {
  ...PT_PROGRESSION_SHARED,
  ESTAGNADO: EXERCISE_PROGRESSION.PLATEAU_SUSPECTED,
};

// ---------------------------------------------------------------------------
// Prontidão muscular — 3 estados, e os DOIS apelidos que o backend emite
// ---------------------------------------------------------------------------

export const READINESS_ORDER = ['READY', 'REDUCED_DOSE', 'NO_DIRECT_WORK'] as const;
export type ReadinessCode = (typeof READINESS_ORDER)[number];

const READINESS: Record<ReadinessCode, StatusVisual> = {
  READY: {
    label: 'Pronto',
    color: 'green',
    hex: GREEN,
    Icon: IconBarbell,
    description: 'Trabalho direto liberado, com progressão.',
  },
  REDUCED_DOSE: {
    label: 'Dose reduzida',
    color: 'yellow',
    hex: BRAND.orange,
    Icon: IconBarbellOff,
    description: 'Trabalho direto em volume/RPE reduzidos, sem progressão.',
  },
  NO_DIRECT_WORK: {
    label: 'Evitar trabalho direto',
    color: 'red',
    hex: BRAND.red,
    Icon: IconBed,
    description: 'Sem séries diretas; trabalho indireto segue permitido.',
  },
};

/**
 * `status` (`green|yellow|red`) e `guidance` (`ideal|recovering|avoid`) são o
 * MESMO estado de `readiness_status`: `MuscleRecoveryService._STATUS_META`
 * mapeia 1:1 os três. Por isso são apelidos do mesmo objeto — assim é
 * impossível o rótulo divergir de novo entre as telas.
 */
export const RECOVERY_STATUS_ORDER = ['green', 'yellow', 'red'] as const;
export type RecoveryStatusCode = (typeof RECOVERY_STATUS_ORDER)[number];

const RECOVERY_STATUS: Record<RecoveryStatusCode, StatusVisual> = {
  green: READINESS.READY,
  yellow: READINESS.REDUCED_DOSE,
  red: READINESS.NO_DIRECT_WORK,
};

export const RECOVERY_GUIDANCE_ORDER = ['ideal', 'recovering', 'avoid'] as const;
export type RecoveryGuidanceCode = (typeof RECOVERY_GUIDANCE_ORDER)[number];

const RECOVERY_GUIDANCE: Record<RecoveryGuidanceCode, StatusVisual> = {
  ideal: READINESS.READY,
  recovering: READINESS.REDUCED_DOSE,
  avoid: READINESS.NO_DIRECT_WORK,
};

// ---------------------------------------------------------------------------
// Slot do ciclo, severidade e qualidade dos dados
// ---------------------------------------------------------------------------

export const CYCLE_SLOT_ORDER = ['done', 'next', 'pending'] as const;
export type CycleSlotCode = (typeof CYCLE_SLOT_ORDER)[number];

const CYCLE_SLOT: Record<CycleSlotCode, StatusVisual> = {
  done: { label: 'Concluído', color: 'green', hex: GREEN, Icon: IconCircleCheck },
  next: { label: 'Próximo', color: 'orange', hex: BRAND.orange, Icon: IconPlayerPlay },
  pending: { label: 'Pendente', color: 'gray', hex: GRAY, Icon: IconCircleDashed },
};

export const SEVERITY_ORDER = ['ACTION', 'ATTENTION', 'INFO'] as const;
export type SeverityCode = (typeof SEVERITY_ORDER)[number];

const SEVERITY: Record<SeverityCode, StatusVisual> = {
  ACTION: {
    label: 'Precisa de ação',
    color: 'red',
    hex: BRAND.red,
    Icon: IconAlertTriangle,
    description: 'Sem resolver, estes itens impedem conclusões sobre volume e progressão.',
  },
  ATTENTION: {
    label: 'Atenção',
    color: 'orange',
    hex: BRAND.orange,
    Icon: IconAlertTriangle,
    description: 'Estes itens distorcem parte das leituras; vale corrigir quando der.',
  },
  INFO: {
    label: 'Informativo',
    color: 'gray',
    hex: GRAY,
    Icon: IconInfoCircle,
    description: 'Apenas registro do que não foi coletado — nada aqui invalida a leitura.',
  },
};

export const DATA_QUALITY_ORDER = ['HIGH', 'MEDIUM', 'LOW', 'INSUFFICIENT'] as const;
export type DataQualityCode = (typeof DATA_QUALITY_ORDER)[number];

const DATA_QUALITY: Record<DataQualityCode, StatusVisual> = {
  HIGH: { label: 'Alta', color: 'green', hex: GREEN, Icon: IconCircleCheck },
  MEDIUM: { label: 'Média', color: 'orange', hex: BRAND.orange, Icon: IconAlertTriangle },
  LOW: { label: 'Baixa', color: 'red', hex: BRAND.red, Icon: IconAlertTriangle },
  INSUFFICIENT: { label: 'Insuficiente', color: 'gray', hex: GRAY, Icon: IconHelpCircle },
};

// ---------------------------------------------------------------------------
// Natureza do dado (§8.4) — usada pelo `MetricValue`
// ---------------------------------------------------------------------------

export const DATA_NATURE_ORDER = [
  'OBSERVED',
  'DERIVED',
  'PLANNED',
  'PROJECTED',
  'ESTIMATED',
  'INFERRED',
] as const;

export type DataNatureCode = (typeof DATA_NATURE_ORDER)[number];

const DATA_NATURE: Record<DataNatureCode, StatusVisual> = {
  OBSERVED: {
    label: 'Observado',
    color: 'gray',
    hex: GRAY,
    Icon: IconCircleCheck,
    description: 'Lido do que foi registrado, sem transformação.',
  },
  DERIVED: {
    label: 'Derivado',
    color: 'gray',
    hex: GRAY,
    Icon: IconCircleCheck,
    description: 'Cálculo determinístico sobre dados observados.',
  },
  PLANNED: {
    label: 'Planejado',
    color: 'blue',
    hex: '#1971c2',
    Icon: IconCircleDashed,
    description: 'Prescrito na ficha ou no modelo, ainda não executado.',
  },
  PROJECTED: {
    label: 'Projetado',
    color: 'blue',
    hex: '#1971c2',
    Icon: IconArrowUpRight,
    description: 'Estimativa do que falta no ciclo; não é resultado.',
  },
  ESTIMATED: {
    label: 'Estimado',
    color: 'yellow',
    hex: YELLOW,
    Icon: IconAlertTriangle,
    description: 'Modelo com erro conhecido (ex.: 1RM estimado, bioimpedância).',
  },
  INFERRED: {
    label: 'Inferido',
    color: 'yellow',
    hex: YELLOW,
    Icon: IconHelpCircle,
    description: 'Conclusão heurística; associação, nunca causa.',
  },
};

// ---------------------------------------------------------------------------
// Origem da meta — `contracts.TargetSource` (F1.2)
// ---------------------------------------------------------------------------

export const TARGET_SOURCE_ORDER = [
  'explicit',
  'assigned_template',
  'template_plan',
  'fallback_generic',
] as const;

export type TargetSourceCode = (typeof TARGET_SOURCE_ORDER)[number];

/**
 * De onde veio o mínimo/alvo/máximo do grupo. Existe como status visível
 * porque muda o que a comparação significa: contra o fallback genérico,
 * "abaixo" e "acima" não falam do objetivo de ninguém — e a tela precisa
 * dizer isso em vez de exibir um veredito que parece calibrado.
 */
const TARGET_SOURCE: Record<TargetSourceCode, StatusVisual> = {
  explicit: {
    label: 'Meta escolhida',
    color: 'teal',
    hex: TEAL,
    Icon: IconCircleCheck,
    description: 'Meta de Ativação informada na consulta.',
  },
  assigned_template: {
    label: 'Meta do seu modelo',
    color: 'teal',
    hex: TEAL,
    Icon: IconCircleCheck,
    description: 'Meta de Ativação do Modelo de Ficha atribuído a você.',
  },
  template_plan: {
    label: 'Planejado pelo modelo',
    color: 'teal',
    hex: TEAL,
    Icon: IconBarbell,
    description:
      'Seu Modelo é por equipamento: a meta é a ativação que ele planeja, por semana. Não há mínimo nem máximo.',
  },
  fallback_generic: {
    label: 'Meta genérica',
    color: 'yellow',
    hex: YELLOW,
    Icon: IconHelpCircle,
    description:
      'Nenhuma meta cadastrada para este grupo: a comparação usa limiares genéricos e não reflete seu objetivo.',
  },
};

// ---------------------------------------------------------------------------
// Papel do grupo no exercício — `exercise_muscle_contributions.role` (F1.2)
// ---------------------------------------------------------------------------

export const MUSCLE_ROLE_ORDER = ['PRIMARY', 'SECONDARY', 'STABILIZER'] as const;

export type MuscleRoleCode = (typeof MUSCLE_ROLE_ORDER)[number];

/** `STABILIZER` nunca é fundido com `SECONDARY`: segurar a carga não é o
 * mesmo que ser trabalhado como músculo auxiliar. */
const MUSCLE_ROLE: Record<MuscleRoleCode, StatusVisual> = {
  PRIMARY: {
    label: 'Direto',
    color: 'teal',
    hex: TEAL,
    Icon: IconBarbell,
    description: 'O grupo é o alvo principal do exercício.',
  },
  SECONDARY: {
    label: 'Indireto',
    color: 'blue',
    hex: '#1971c2',
    Icon: IconArrowUpRight,
    description: 'O grupo participa como auxiliar do movimento.',
  },
  STABILIZER: {
    label: 'Estabilizador',
    color: 'gray',
    hex: GRAY,
    Icon: IconCircleDashed,
    description: 'O grupo apenas estabiliza a carga — não é trabalho direto.',
  },
};


// ---------------------------------------------------------------------------
// Status DERIVADO de item da ficha e de sessão (F1.1, plano §5)
// ---------------------------------------------------------------------------

export const PLAN_ITEM_STATUS_ORDER = [
  'COMPLETED',
  'INTERRUPTED',
  'SKIPPED',
  'SUBSTITUTED',
  'EXTRA',
] as const;

export type PlanItemStatusCode = (typeof PLAN_ITEM_STATUS_ORDER)[number];

/**
 * Nenhum destes estados existe como coluna: são derivados do vínculo
 * `exercises.workout_plan_exercise_id` e das séries, a cada consulta
 * (`adherence_service`). A tela só apresenta.
 */
const PLAN_ITEM_STATUS: Record<PlanItemStatusCode, StatusVisual> = {
  COMPLETED: {
    label: 'Concluído',
    color: 'green',
    hex: GREEN,
    Icon: IconCircleCheck,
    description: 'Séries de trabalho iguais ou acima do prescrito na ficha.',
  },
  INTERRUPTED: {
    label: 'Interrompido',
    color: 'orange',
    hex: YELLOW,
    Icon: IconPlayerPause,
    description: 'Começou, mas ficou abaixo das séries prescritas.',
  },
  SKIPPED: {
    label: 'Pulado',
    color: 'red',
    hex: BRAND.red,
    Icon: IconBan,
    description: 'Item da ficha sem nenhuma série de trabalho na sessão.',
  },
  SUBSTITUTED: {
    label: 'Substituído',
    color: 'blue',
    hex: '#1971c2',
    Icon: IconArrowsExchange,
    description: 'O exercício executado não é o que estava prescrito.',
  },
  EXTRA: {
    label: 'Extra',
    color: 'gray',
    hex: GRAY,
    Icon: IconCirclePlus,
    description: 'Exercício sem vínculo com a ficha: avulso ou adicionado na hora.',
  },
};

export const SESSION_STATUS_ORDER = ['DONE', 'IN_PROGRESS', 'IMPORTED'] as const;

export type SessionStatusCode = (typeof SESSION_STATUS_ORDER)[number];

const SESSION_STATUS: Record<SessionStatusCode, StatusVisual> = {
  DONE: {
    label: 'Concluída',
    color: 'green',
    hex: GREEN,
    Icon: IconCircleCheck,
    description: 'Treino finalizado no app.',
  },
  IN_PROGRESS: {
    label: 'Em andamento',
    color: 'orange',
    hex: BRAND.orange,
    Icon: IconPlayerPlay,
    description: 'Sessão aberta: os itens ainda não executados não contam como pulados.',
  },
  IMPORTED: {
    label: 'Importada',
    color: 'gray',
    hex: GRAY,
    Icon: IconCircleDashed,
    description: 'Sessão sem status final: as séries existem, mas ninguém finalizou o treino.',
  },
};

// ---------------------------------------------------------------------------
// Sinais de recuperação (F1.5) — `recovery-signals-v1`
// ---------------------------------------------------------------------------

/**
 * O que o sinal diz sobre recuperação. NÃO é a direção: HRV abaixo da
 * baseline e FC de repouso abaixo da baseline apontam para o mesmo lado do
 * gráfico e para lados OPOSTOS da recuperação — quem traduz é o backend
 * (`classify_reading`), aqui só há apresentação.
 */
export const SIGNAL_READING_ORDER = ['UNFAVORABLE', 'NEUTRAL', 'FAVORABLE', 'UNKNOWN'] as const;

export type SignalReadingCode = (typeof SIGNAL_READING_ORDER)[number];

const SIGNAL_READING: Record<SignalReadingCode, StatusVisual> = {
  UNFAVORABLE: {
    label: 'Pior que o habitual',
    color: 'orange',
    hex: YELLOW,
    Icon: IconTrendingDown,
    description: 'Fora da sua baseline pessoal, no sentido que atrapalha a recuperação.',
  },
  NEUTRAL: {
    label: 'Em linha',
    color: 'blue',
    hex: '#1971c2',
    Icon: IconMinus,
    description: 'Dentro da faixa habitual: nada a concluir a partir dele.',
  },
  FAVORABLE: {
    label: 'Melhor que o habitual',
    color: 'green',
    hex: GREEN,
    Icon: IconTrendingUp,
    description: 'Fora da sua baseline pessoal, no sentido que favorece a recuperação.',
  },
  UNKNOWN: {
    label: 'Sem baseline',
    color: 'gray',
    hex: GRAY,
    Icon: IconHelpCircle,
    description: 'Pontos insuficientes na janela de referência: não há com o que comparar.',
  },
};

/** Se o sinal CONCORDA ou DIVERGE da leitura de prontidão do modelo. */
export const SIGNAL_AGREEMENT_ORDER = ['AGREES', 'DIVERGES', 'NEUTRAL', 'NO_BASELINE'] as const;

export type SignalAgreementCode = (typeof SIGNAL_AGREEMENT_ORDER)[number];

const SIGNAL_AGREEMENT: Record<SignalAgreementCode, StatusVisual> = {
  AGREES: {
    label: 'Concorda',
    color: 'blue',
    hex: '#1c7ed6',
    Icon: IconCircleCheck,
    description: 'Aponta no mesmo sentido da prontidão calculada pelo modelo.',
  },
  DIVERGES: {
    label: 'Diverge',
    color: 'grape',
    hex: '#9c36b5',
    Icon: IconArrowsExchange,
    description: 'Aponta no sentido oposto da prontidão. Os dois ficam visíveis — nenhum é descartado.',
  },
  NEUTRAL: {
    label: 'Sem posição',
    color: 'gray',
    hex: GRAY,
    Icon: IconMinus,
    description: 'Dentro da faixa habitual: não concorda nem diverge.',
  },
  NO_BASELINE: {
    label: 'Sem baseline',
    color: 'gray',
    hex: GRAY,
    Icon: IconHelpCircle,
    description: 'Sem pontos suficientes para comparar com a sua própria referência.',
  },
};

// ---------------------------------------------------------------------------
// Mapa de calor por grupo muscular — `classify_heatmap_band` (F2.5 / M34)
// ---------------------------------------------------------------------------

export const MUSCLE_HEATMAP_ORDER = ['NONE', 'LOW', 'MODERATE', 'HIGH', 'VERY_HIGH'] as const;

export type MuscleHeatmapCode = (typeof MUSCLE_HEATMAP_ORDER)[number];

/**
 * O mapa de calor legado (`/volume-grupamentos`) interpolava um gradiente
 * contínuo vermelho→amarelo→verde no cliente. Duas coisas erradas nisso: a
 * classificação vivia no frontend, e a cor era o ÚNICO sinal — um gradiente
 * não tem rótulo, então quem não distingue as matizes não lia nada.
 *
 * Aqui a faixa é DISCRETA e vem classificada do backend, com `label` e `Icon`
 * próprios. A escala é sequencial (uma grandeza só, do menos ao mais), não
 * divergente: "muito alto" não é bom nem ruim por si — é só mais estímulo.
 */
const MUSCLE_HEATMAP: Record<MuscleHeatmapCode, StatusVisual> = {
  NONE: {
    label: 'Sem estímulo',
    color: 'gray',
    hex: '#343a40',
    Icon: IconBarbellOff,
    description: 'O grupo não recebeu nenhuma série de trabalho nesta sessão.',
  },
  LOW: {
    label: 'Baixo',
    color: 'blue',
    hex: '#4dabf7',
    Icon: IconCircleDashed,
    description: 'Até 25% do estímulo do grupo mais trabalhado da sessão.',
  },
  MODERATE: {
    label: 'Moderado',
    color: 'cyan',
    hex: '#22b8cf',
    Icon: IconMinus,
    description: 'Entre 25% e 50% do estímulo do grupo mais trabalhado da sessão.',
  },
  HIGH: {
    label: 'Alto',
    color: 'teal',
    hex: TEAL,
    Icon: IconArrowUpRight,
    description: 'Entre 50% e 75% do estímulo do grupo mais trabalhado da sessão.',
  },
  VERY_HIGH: {
    label: 'Muito alto',
    color: 'orange',
    hex: DEEP_ORANGE,
    Icon: IconFlame,
    description: 'Acima de 75% do estímulo do grupo mais trabalhado da sessão.',
  },
};

// ---------------------------------------------------------------------------
// Metodologia de volume por grupo — `MethodologyVolumeStatus` (Fase 4, 5 estados)
// ---------------------------------------------------------------------------

/**
 * Espelho de `METHODOLOGY_VOLUME_STATUS_CODES` em
 * `api/app/intelligence/methodology_volume.py`. Domínio PRÓPRIO, separado de
 * `weeklyVolume`/`plannedVolume`: a unidade aqui é séries DIRETAS e
 * exercícios por sessão (sem ponderação), comparados com a faixa do
 * macro-grupo × frequência, e séries diretas por semana contra a meta semanal
 * do macro-grupo (Fase 6). O mesmo domínio vale por eixo e para o status
 * COMBINADO do grupo. Não há tolerância de ±5% — a faixa já é um intervalo.
 *
 * PALETA UNIFICADA (decisão do usuário em 2026-09-27, M43; revista em
 * 2026-10-02): `BELOW_RANGE` é LARANJA claro (#f08c00), `WITHIN_RANGE` verde
 * e `ABOVE_RANGE` laranja escuro (#f76707) — em TODO lugar: painel do builder,
 * tela de diagnóstico, a linha de participação por séries em cada card e a
 * barra de séries semanais. Abaixo × acima dividem o badge `orange` e se
 * distinguem por rótulo e ícone (cor nunca é o único sinal). A `severity`
 * (warning/informational) do payload continua existindo e pode mudar
 * texto/tooltip, mas a COR vem só daqui. `NOT_DIRECTLY_TRAINED` segue cinza.
 */
export const METHODOLOGY_VOLUME_STATUS_ORDER = [
  'WITHIN_RANGE',
  'BELOW_RANGE',
  'ABOVE_RANGE',
  'NOT_DIRECTLY_TRAINED',
  'NOT_EVALUABLE',
] as const;

export type MethodologyVolumeStatusCode = (typeof METHODOLOGY_VOLUME_STATUS_ORDER)[number];

const METHODOLOGY_VOLUME_STATUS: Record<MethodologyVolumeStatusCode, StatusVisual> = {
  WITHIN_RANGE: {
    label: 'Na faixa',
    color: 'green',
    hex: GREEN,
    Icon: IconCircleCheck,
    description: 'Dentro da faixa do macro-grupo para esta frequência (e da meta semanal, quando há).',
  },
  // Laranja (2026-10-02, pedido do usuário): substitui o cinza de 2026-09-27.
  // Hex mais claro (#f08c00) que o de ABOVE_RANGE (#f76707) para as marcas SVG;
  // nos badges os dois são `orange` e o ÍCONE os separa (seta p/ baixo × p/ cima),
  // mesmo padrão de BETWEEN_MIN_TARGET × ABOVE_MAX no volume semanal.
  BELOW_RANGE: {
    label: 'Abaixo da faixa',
    color: 'orange',
    hex: YELLOW,
    Icon: IconTrendingDown,
    description: 'Menos trabalho direto do que a faixa do macro-grupo prevê.',
  },
  ABOVE_RANGE: {
    label: 'Acima da faixa',
    color: 'orange',
    hex: DEEP_ORANGE,
    Icon: IconTrendingUp,
    description: 'Mais trabalho direto do que a faixa do macro-grupo prevê.',
  },
  // Terminal e NEUTRO (EXG-025): músculo sem trabalho direto no ciclo não é
  // "abaixo da faixa" — pode ser escolha deliberada. Nunca cor de alerta.
  NOT_DIRECTLY_TRAINED: {
    label: 'Sem trabalho direto',
    color: 'gray',
    hex: GRAY,
    Icon: IconMinus,
    description: 'Nenhum exercício do ciclo tem este grupo como primário. O estímulo indireto aparece na ativação ponderada.',
  },
  NOT_EVALUABLE: {
    label: 'Não avaliável',
    color: 'blue',
    hex: '#1c7ed6',
    Icon: IconCircleDashed,
    description: 'A metodologia não consegue avaliar este eixo; o motivo vem junto.',
  },
};

/**
 * Origem de cada faixa comparada: número do autor (com o EXG-NNN de
 * evidência), número NOSSO derivado por regra escrita, ou número editado pelo
 * admin na aba "Macro-grupos de volume" (Fase 6). A UI marca sempre — o
 * usuário precisa saber de quem é o número.
 */
export const METHODOLOGY_RANGE_SOURCE_ORDER = ['methodology', 'derived', 'admin'] as const;

export type MethodologyRangeSourceCode = (typeof METHODOLOGY_RANGE_SOURCE_ORDER)[number];

const METHODOLOGY_RANGE_SOURCE: Record<MethodologyRangeSourceCode, StatusVisual> = {
  methodology: {
    label: 'Metodologia',
    color: 'brand',
    hex: '#e8590c',
    Icon: IconBook,
    description: 'Faixa afirmada pelo autor da metodologia.',
  },
  derived: {
    label: 'Derivada',
    color: 'gray',
    hex: GRAY,
    Icon: IconCalculator,
    description: 'Faixa calculada por nós a partir da metodologia; não é número do autor.',
  },
  admin: {
    label: 'Admin',
    color: 'violet',
    hex: '#7048e8',
    Icon: IconUserEdit,
    description: 'Faixa editada por um administrador na aba Macro-grupos de volume.',
  },
};

// ---------------------------------------------------------------------------
// Registro e lookup
// ---------------------------------------------------------------------------

export const STATUS_REGISTRY = {
  signalReading: SIGNAL_READING,
  signalAgreement: SIGNAL_AGREEMENT,
  weeklyVolume: WEEKLY_VOLUME_STATUS,
  plannedVolume: PLANNED_VOLUME_STATUS,
  exerciseProgression: EXERCISE_PROGRESSION,
  muscleGroupProgression: MUSCLE_GROUP_PROGRESSION,
  muscleGroupVolume: MUSCLE_GROUP_VOLUME,
  progressionTrend: PROGRESSION_TREND,
  readiness: READINESS,
  recoveryStatus: RECOVERY_STATUS,
  recoveryGuidance: RECOVERY_GUIDANCE,
  cycleSlot: CYCLE_SLOT,
  severity: SEVERITY,
  dataQuality: DATA_QUALITY,
  dataNature: DATA_NATURE,
  targetSource: TARGET_SOURCE,
  muscleRole: MUSCLE_ROLE,
  muscleHeatmap: MUSCLE_HEATMAP,
  planItemStatus: PLAN_ITEM_STATUS,
  sessionStatus: SESSION_STATUS,
  methodologyVolume: METHODOLOGY_VOLUME_STATUS,
  methodologyRangeSource: METHODOLOGY_RANGE_SOURCE,
} as const;

export type StatusDomain = keyof typeof STATUS_REGISTRY;

/** Ordem canônica de cada domínio — usada por legendas e agrupamentos. */
export const STATUS_DOMAIN_ORDER: Record<StatusDomain, readonly string[]> = {
  signalReading: SIGNAL_READING_ORDER,
  signalAgreement: SIGNAL_AGREEMENT_ORDER,
  weeklyVolume: WEEKLY_VOLUME_STATUS_ORDER,
  plannedVolume: PLANNED_VOLUME_STATUS_ORDER,
  exerciseProgression: EXERCISE_PROGRESSION_ORDER,
  muscleGroupProgression: MUSCLE_GROUP_PROGRESSION_ORDER,
  muscleGroupVolume: MUSCLE_GROUP_VOLUME_ORDER,
  progressionTrend: PROGRESSION_TREND_ORDER,
  readiness: READINESS_ORDER,
  recoveryStatus: RECOVERY_STATUS_ORDER,
  recoveryGuidance: RECOVERY_GUIDANCE_ORDER,
  cycleSlot: CYCLE_SLOT_ORDER,
  severity: SEVERITY_ORDER,
  dataQuality: DATA_QUALITY_ORDER,
  dataNature: DATA_NATURE_ORDER,
  targetSource: TARGET_SOURCE_ORDER,
  muscleRole: MUSCLE_ROLE_ORDER,
  muscleHeatmap: MUSCLE_HEATMAP_ORDER,
  planItemStatus: PLAN_ITEM_STATUS_ORDER,
  sessionStatus: SESSION_STATUS_ORDER,
  methodologyVolume: METHODOLOGY_VOLUME_STATUS_ORDER,
  methodologyRangeSource: METHODOLOGY_RANGE_SOURCE_ORDER,
};

/**
 * Apresentação de um código de status. Código fora do contrato NÃO quebra a
 * tela: devolve `UNKNOWN_STATUS` com o próprio código como rótulo, para o
 * problema ficar visível sem derrubar a renderização.
 */
export function statusVisual(domain: StatusDomain, code: string | null | undefined): StatusVisual {
  if (!code) return UNKNOWN_STATUS;
  const entry = (STATUS_REGISTRY[domain] as Record<string, StatusVisual | undefined>)[code];
  return entry ?? { ...UNKNOWN_STATUS, label: code };
}

/** Legenda `{ color, label }` de um domínio (mapa corporal, gráficos). */
export function statusLegend(domain: StatusDomain): Array<{ color: string; label: string }> {
  return STATUS_DOMAIN_ORDER[domain].map((code) => {
    const visual = statusVisual(domain, code);
    return { color: visual.hex, label: visual.label };
  });
}

/** Paleta hex na ordem canônica (o mapa corporal tinge a arte a partir do hex). */
export function statusHexScale(domain: StatusDomain): string[] {
  return STATUS_DOMAIN_ORDER[domain].map((code) => statusVisual(domain, code).hex);
}
