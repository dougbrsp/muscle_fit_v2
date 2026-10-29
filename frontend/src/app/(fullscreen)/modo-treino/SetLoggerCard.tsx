'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Card,
  Drawer,
  Group,
  Loader,
  NumberInput,
  Stack,
  Switch,
  Text,
  Textarea,
  UnstyledButton,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconArrowDown,
  IconArrowUp,
  IconArrowUpRight,
  IconArrowsExchange,
  IconBolt,
  IconBulb,
  IconCheck,
  IconChevronRight,
  IconClockHour4,
  IconFlame,
  IconHistory,
  IconInfoCircle,
  IconMinus,
  IconNotes,
  IconPlayerStop,
  IconPlayerTrackNext,
  IconPlus,
  IconRepeat,
  IconSparkles,
  IconTool,
} from '@tabler/icons-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import {
  getAdvancedTechniqueLabel,
  isExtraRepsTechnique,
  type AdvancedTechniqueValue,
} from '@/lib/advanced-techniques';
import {
  DEFAULT_WEIGHT_UNIT,
  DISPLAY_DECIMALS,
  formatLoadTarget,
  weightFieldFromKg,
  weightFieldOnInput,
  weightFieldOnUnitChange,
  loadStepperStep,
  WEIGHT_STEP,
  WEIGHT_UNITS,
  type WeightUnit,
} from '@/lib/weight-units';
import { ExerciseCardViewer } from './ExerciseCardViewer';
import { MuscleMapDisclosure } from './MuscleMapDisclosure';
import { PhotoLightbox, usePhotoLightbox } from './PhotoLightbox';
import {
  formatPreviousSessionRecordLines,
  formatPreviousSessionSets,
  type PreviousSessionRecordSummary,
} from '@/lib/workout-mode-previous-session';
import { formatEquivalent } from '@/lib/technique-accounting';
import {
  engineDecisionText,
  loadAdjustmentText,
  nextSetLoadView,
  savedSetStatusViews,
  setEvaluationCategoryView,
  type SavedSetIntensity,
} from '@/lib/set-engine-status';
import {
  buildMuscleMapItems,
  type ExerciseSummaryResult,
  type GroupFatigueReading,
  type PreviousSessionSet,
  type SetEvaluationResult,
} from '@/lib/workout-mode-types';
import type { ExerciseCardSummary } from '@/lib/workout-plans-types';
import { MEASURE_LIMITS, normalizeMeasure } from '@/lib/set-measures';
import { setTypeTakesSides, type SetSides } from '@/lib/set-sides';
import {
  executionTimerSpec,
  isDurationSecondsValid,
  isMaxRepsValid,
  measureLoggerDefaults,
  measureTargetText,
} from '@/lib/workout-mode-measures';
import {
  aiTipView,
  fieldsToRefresh,
  setTargetLine,
  type AiTipView,
  type LoggerField,
} from '@/lib/workout-mode-execution-view';
import { BRAND } from '@/theme';
import { ExecutionTimer } from './ExecutionTimer';
import { ClampText } from './ui/ClampText';
import { ExerciseTitleRow } from './ui/ExerciseTitleRow';
import type { WorkoutIconAction } from './ui/IconAction';
import { MuscleFatigueChip } from './ui/MuscleFatigueChip';
import { SectionAccordion } from './ui/SectionAccordion';
import { WorkoutActionBar, type WorkoutSecondaryAction } from './ui/WorkoutActionBar';
import { WORKOUT_MODE_CSS_VARS } from './ui/workout-mode-css-vars';
import styles from './workout-mode.module.css';

type SteppedFieldProps = {
  label: string;
  // Nome usado nos botões ("Diminuir Carga (kg)"); padrão = `label`.
  accessibleName?: string;
  // Depois do rótulo, na mesma linha (ex.: unidade tocável, "opcional").
  labelSuffix?: ReactNode;
  value: number | null;
  onChange: (value: number | null) => void;
  step: number;
  min?: number;
  max?: number;
  decimalScale?: number;
  suffix?: string;
  placeholder?: string;
  full?: boolean;
};

// Campo numérico do card de série. PR4 ("uma tela"): em 3 colunas, valor em
// cima e – / + embaixo (todos ≥ 44 px); abaixo de 360 px vira uma linha com
// o rótulo à esquerda (ver `.fieldGrid` no CSS). Valor editável direto no
// input. Passos, limites e precisão chegam prontos de quem chama — nada muda
// aqui.
function SteppedField({
  label,
  accessibleName,
  labelSuffix,
  value,
  onChange,
  step,
  min = 0,
  max,
  decimalScale = 0,
  suffix,
  placeholder,
  full,
}: SteppedFieldProps) {
  const inputId = useId();
  const name = accessibleName ?? label;
  return (
    <div className={full ? `${styles.field} ${styles.fieldFull}` : styles.field}>
      <div className={styles.fieldLabelRow}>
        <label className={styles.fieldLabel} htmlFor={inputId}>
          {label}
        </label>
        {labelSuffix}
      </div>
      <div className={styles.fieldRow}>
        <ActionIcon
          size={44}
          radius="xl"
          variant="light"
          color="brand"
          className={`${styles.stepButton} ${styles.fieldMinus}`}
          aria-label={`Diminuir ${name}`}
          onClick={() => onChange(Math.max(min, Number(((value ?? 0) - step).toFixed(2))))}
        >
          <IconMinus size={18} />
        </ActionIcon>
        <NumberInput
          id={inputId}
          className={styles.fieldInput}
          value={value ?? undefined}
          onChange={(v) => onChange(v === '' ? null : Number(v))}
          min={min}
          max={max}
          step={step}
          decimalScale={decimalScale}
          hideControls
          placeholder={placeholder}
          styles={{ input: { textAlign: 'center', fontSize: 18, fontWeight: 800, height: 44, paddingInline: 4 } }}
          suffix={suffix}
        />
        <ActionIcon
          size={44}
          radius="xl"
          variant="light"
          color="brand"
          className={`${styles.stepButton} ${styles.fieldPlus}`}
          aria-label={`Aumentar ${name}`}
          onClick={() => {
            const next = Number(((value ?? 0) + step).toFixed(2));
            onChange(max != null ? Math.min(max, next) : next);
          }}
        >
          <IconPlus size={18} />
        </ActionIcon>
      </div>
    </div>
  );
}

// Unidade kg/lb como SUFIXO TOCÁVEL do rótulo "Carga" (M3; PR4 tirou o
// alternador de linha própria). Fica NO CARD DE SÉRIE, e não só no perfil,
// porque a unidade muda com o EQUIPAMENTO que o usuário tem pela frente
// (halteres em libras numa academia, barra em quilos na outra). O toque
// alterna para a outra unidade, pelo mesmo `onChange` de antes; sem
// `onChange`, a unidade aparece só como texto.
function WeightUnitSuffix({ unit, onChange }: { unit: WeightUnit; onChange?: (unit: WeightUnit) => void }) {
  if (!onChange) return <span className={styles.fieldHint}>{unit}</span>;
  const other = WEIGHT_UNITS.find((option) => option !== unit) ?? unit;
  const label = `Unidade da carga: ${unit}. Tocar para anotar em ${other}`;
  return (
    <button
      type="button"
      className={styles.unitSuffix}
      aria-label={label}
      title={label}
      onClick={() => onChange(other)}
      data-testid="weight-unit-toggle"
    >
      {unit}
      <IconArrowsExchange size={10} aria-hidden="true" />
    </button>
  );
}

export type SetLoggerValues = {
  // `null` só fora de repetições (Lote T5): série por tempo não tem reps.
  reps: number | null;
  // `null` só fora de repetições: a carga é opcional em tempo/máximo de reps.
  weight_kg: number | null;
  rpe: number | null;
  // Lote T5 (medidas de série): presentes só quando a série NÃO é de
  // repetições (`measure` ausente = reps, payload igual ao de antes). Em
  // `duration`, os segundos executados; em `max_reps`, a janela usada (ou null).
  measure?: string;
  duration_seconds?: number | null;
  muscle_pain: number | null;
  joint_pain: boolean;
  notes: string | null;
  // Gravado nesta série quando o card é usado numa sub-etapa não padrão
  // (ex.: aquecimento persistido de propósito). `undefined` preserva o
  // default "working" do backend (`POST /workout-sessions/{id}/exercises/{id}/sets`,
  // `SessionSetIn.set_type`), mesmo comportamento do fluxo normal do modo
  // treino.
  set_type?: string;
  // Fase 5: vínculo da aplicação de técnica (mini-bloco, drop) com a série de
  // trabalho âncora (`set_number` FÍSICO gravado). Ausentes = o backend deriva.
  technique_code?: string | null;
  anchor_set_number?: number | null;
  // Fase 5: reps extras (família além da falha) lançadas no card da última
  // série de trabalho; quem chama grava uma linha `forced_rep` ligada a ela.
  extra_reps?: number | null;
  // M102: reps/RPE por lado (série de trabalho/backoff de exercício
  // unilateral). Presentes = `reps`/`rpe` vão nulos e o SERVIDOR grava os
  // oficiais (pior lado, `domain/set_sides.py`); a tela não os calcula.
  reps_left?: number | null;
  reps_right?: number | null;
  rpe_left?: number | null;
  rpe_right?: number | null;
};

// Feedback ao vivo de uma aplicação de técnica (`technique-accounting.ts`).
export type TechniqueEquivalentPreview = {
  application: number;
  block: number;
  cap: number;
  capped: boolean;
};

type Props = {
  exerciseName: string;
  description?: string | null;
  equipmentName?: string | null;
  equipmentPhotoUrl?: string | null;
  muscleGroup?: string | null;
  muscleSecondaryGroup?: string | null;
  setLabel: string;
  repsTarget?: string | null;
  loadTarget?: string | null;
  // Fix M97: faixa de carga de força por %1RM já formatada
  // (`formatStrengthLoadRange` sobre `prescription.strength_load`); ausente = nada.
  loadRangeText?: string | null;
  rpeTarget?: string | null;
  // `null` = campo vazio (aplicação de técnica: a meta é "até a falha", sem reps sugeridas).
  defaultReps: number | null;
  // `null` = campo de carga vazio (permitido só fora de repetições).
  defaultWeight: number | null;
  defaultRpe: number | null;
  // Lote T5 (medidas de série): medida do item (`reps` | `duration` |
  // `max_reps`, espelho `set-measures`) e os alvos de tempo. Ausente = reps:
  // o card de sempre. Fora de reps (só nas variantes `default`/`advancedWorking`)
  // o card mostra o cronômetro de execução (`executionTimerSpec`) e os campos
  // de `measureLoggerDefaults` (segundos em tempo; reps obrigatórias no
  // máximo; carga opcional nos dois).
  measure?: string | null;
  durationSecondsTarget?: number | null;
  timeWindowSeconds?: number | null;
  // Valor inicial do campo "Segundos" (série por tempo).
  defaultDurationSeconds?: number | null;
  // M21: a sessão anterior INTEIRA deste exercício (só séries de trabalho, em
  // ordem de execução), não apenas a série equivalente — o usuário compara o
  // treino de hoje com o de antes olhando a progressão da sessão toda.
  // Quem seleciona/ordena é `selectPreviousSessionWorkingSets`.
  // `null`/ausente = card "Treino anterior" escondido (variantes avançadas,
  // histórico ainda carregando); lista vazia = "Ainda não há execução
  // comparável".
  previousSessionSets?: ReadonlyArray<PreviousSessionSet> | null;
  // Posição (1-based) da série que está sendo registrada agora, destacada na
  // lista acima. `null`/fora da lista = nada destacado.
  previousSessionHighlight?: number | null;
  // Recorde e e1RM do histórico nesse equipamento (mesma resposta de
  // `/exercises/last-session-sets`), no rodapé do card "Treino anterior".
  // `null` = sem histórico: nada aparece. Quem seleciona é
  // `selectPreviousSessionRecord`.
  previousSessionRecord?: PreviousSessionRecordSummary | null;
  // M79: fadiga atual do grupo (`current_group_fatigue` de
  // `/exercises/last-session-sets`, faixa e rótulo prontos do backend), num
  // chip ÚNICO no hero. `undefined` = sem chip (consulta carregando);
  // `null` = "Fadiga indisponível".
  currentGroupFatigue?: GroupFatigueReading | null;
  // Unidade em que o usuario ANOTA e LE a carga nesta tela (M3). O contrato de
  // `onSave` nao muda: `SetLoggerValues.weight_kg` sai SEMPRE em quilos, ja
  // convertido aqui — quem chama continua enviando kg ao backend sem saber que
  // libras existem. `onWeightUnitChange` ausente esconde o alternador.
  weightUnit?: WeightUnit;
  onWeightUnitChange?: (unit: WeightUnit) => void;
  // Passo de carga do equipamento (`load_increment_kg` da API, em kg). Os
  // botões +/- do modo simples andam nele; ausente = passo padrão da unidade.
  loadIncrementKg?: number | null;
  progression?: 'increased' | 'decreased' | 'same' | null;
  progressionNote?: string | null;
  saving: boolean;
  saveLabel?: string;
  onSave: (values: SetLoggerValues) => void;
  // Erro da última tentativa de salvar, mostrado junto do CTA (os valores
  // digitados continuam no card para tentar de novo).
  saveError?: string | null;
  onSubstituteClick?: () => void;
  // "Pular" do hero: abre o fluxo real de pular exercício (o
  // `SkipExerciseModal` de quem chama, com a confirmação dele). Ausente =
  // sem botão (ex.: treino livre).
  onSkipExercise?: () => void;
  // Abre o modal de "Encerrar exercício" — visível tanto antes da primeira
  // série quanto com séries já logadas.
  onFinishExercise?: () => void;
  // Quando presente, embutido em `SetLoggerValues.set_type` ao salvar esta
  // série. `undefined` (padrão) preserva o fluxo normal (backend usa o
  // default "working").
  setType?: string;
  // Pico de contração (`workout_plan_exercises.peak_contraction_seconds`):
  // quando presente, exibe um lembrete visual para segurar a contração na
  // última repetição desta série de TRABALHO. Quem decide se este card
  // representa uma série de trabalho é o chamador (`PlanExecutionScreen`).
  peakContractionSeconds?: number | null;
  // Texto descritivo curto da técnica avançada (`getAdvancedTechniqueDescription`),
  // exibido só quando este card representa uma sub-etapa de aplicação da
  // técnica — quem decide isso é o chamador (`PlanExecutionScreen`).
  techniqueDescription?: string | null;
  // Visual das sub-etapas do modo "Fazer treino avançado": mesmo hero e
  // mesmo card de série; o corpo e os botões (Concluir/Pular) mudam.
  // Default 'default' preserva 100% o comportamento atual (fluxo normal).
  // `techniqueApplication` (Fase 5, modo normal E avançado): card de UMA
  // aplicação da técnica (mini-bloco de rest-pause, drop) com reps/RPE/carga
  // editáveis e o equivalente em séries ao vivo.
  variant?: 'default' | 'advancedWarmup' | 'advancedFeeler' | 'techniqueApplication' | 'advancedWorking';
  // Só usado com variant="advancedWarmup"/"advancedFeeler"/"techniqueApplication"
  // — botão "Pular série"/"Pular" (a SÉRIE, não o exercício).
  onSkip?: () => void;
  // Só usado com variant="techniqueApplication": duração da pausa da técnica
  // (`advanced_technique_duration_seconds`), exibida como lembrete — o
  // cronômetro em si é o descanso entre as aplicações.
  staticRestSeconds?: number | null;
  // Só usado com variant="techniqueApplication": meta exibida ("até a falha
  // (RPE 10)") — NUNCA as reps da série de trabalho.
  techniqueGoalLabel?: string | null;
  // Só usado com variant="techniqueApplication": equivalente da aplicação e
  // do bloco para as reps/RPE digitados (null = sem regra para esta técnica).
  techniqueEquivalent?: (reps: number | null, rpe: number | null) => TechniqueEquivalentPreview | null;
  // Só usado com variant="techniqueApplication": aplicações já lançadas desta
  // âncora, exibidas como subitens ("Mini-bloco 1: 6 reps · RPE 10").
  techniqueAppliedItems?: ReadonlyArray<string>;
  // Só usado com variant="techniqueApplication": "Encerrar técnica" (faz
  // menos aplicações que o prescrito). Ausente esconde o botão.
  onEndTechnique?: () => void;
  // Técnica configurada no exercício (para sinalizar no card da série de
  // trabalho o que vem depois), ou null se nenhuma.
  upcomingTechnique?: AdvancedTechniqueValue | null;
  // Com upcomingTechnique de drop_set/rest_pause: nº de aplicações
  // configuradas (nº de drops/mini-blocos); família além da falha: reps extras
  // prescritas.
  upcomingTechniqueApplications?: number | null;
  // Família além da falha, só na ÚLTIMA série de trabalho: exibe o campo
  // "reps extras", devolvido em `SetLoggerValues.extra_reps`.
  extraRepsEnabled?: boolean;
  // Mapa "Conecte-se com os seus músculos" no fim do card (depois do botão
  // salvar). `PlanExecutionScreen` passa false e desenha UM mapa só no fim da
  // tela (abaixo de todos os botões); o Freeform usa o padrão.
  showMuscleMap?: boolean;
  // Card ilustrado PUBLICADO do exercício EFETIVO (cards, PR5). Presente =>
  // ação "Info" no hero, que abre o `ExerciseCardViewer`.
  // Ausente/null => nenhum botão nem espaço reservado (fallback silencioso).
  exerciseCard?: ExerciseCardSummary | null;
  // RPE 10 Lote 6/P9 ("uma voz"): decisão do motor congelada na ficha
  // (`prescription.decision`), exibida como "Ajuste de hoje: Manter".
  engineDecision?: string | null;
  // Status do motor da série ANTERIOR deste exercício (resposta do salvamento).
  lastSetStatus?: SavedSetIntensity | null;
  // Card "Dica da IA": estado REAL do toggle "IA por série" e a avaliação da
  // série anterior (`/exercises/evaluate-set`). Toggle desligado ou sem
  // avaliação com texto = nenhum card (ver `aiTipView`).
  perSetAiEnabled?: boolean;
  aiEvaluation?: SetEvaluationResult | null;
  // Conteúdo de quem chama logo ANTES do rodapé de ação (ex.: a linha
  // "Próximo: …" da ficha): o CTA fica sempre no fim do fluxo da tela.
  beforeActions?: ReactNode;
  // M97 F3: badge do objetivo de treino do item (rótulo da API), no título.
  goalBadge?: ReactNode;
  // M102: exercício unilateral (`is_unilateral` do catálogo). Na série de
  // trabalho/backoff do card padrão, reps e RPE viram duas colunas (E/D), com a
  // carga única. Técnicas avançadas e o Modo Intensivo ficam com valor único (v1).
  unilateral?: boolean;
};

/**
 * "Ajuste de hoje: Manter" + o status do motor da última série salva
 * ("Esforço atingido" + "Reps abaixo da faixa") + a carga da PRÓXIMA série
 * pelo motor ("Próxima série: Aumentar carga → 22,5 kg", com o motivo).
 * Rótulos do backend; nenhuma regra aqui.
 */
export function EngineVoiceLine({
  engineDecision,
  lastSetStatus,
  lastSetPrefix = 'Série anterior',
}: {
  engineDecision?: string | null;
  lastSetStatus?: SavedSetIntensity | null;
  lastSetPrefix?: string;
}) {
  const decisionText = engineDecisionText(engineDecision);
  const statusViews = savedSetStatusViews(lastSetStatus);
  const nextSet = nextSetLoadView(lastSetStatus?.next_set_load);
  if (!decisionText && statusViews.length === 0 && !nextSet) return null;
  return (
    <Stack gap={4} data-testid="engine-voice">
      <Group gap={6} wrap="wrap" data-testid="engine-voice-line">
        {decisionText && (
          <Text size="xs" fw={700} c={BRAND.textPrimary}>
            {decisionText}
          </Text>
        )}
        {statusViews.length > 0 && (
          <Text size="xs" c={BRAND.textMuted}>
            {lastSetPrefix}:
          </Text>
        )}
        {statusViews.map((view) => (
          <Badge key={view.label} size="sm" variant="light" color={view.color}>
            {view.label}
          </Badge>
        ))}
      </Group>
      {nextSet && (
        <Stack gap={0} data-testid="next-set-load">
          <Text size="xs" fw={700} c={`${nextSet.badge.color}.6`}>
            {nextSet.text}
          </Text>
          {nextSet.reason && (
            <Text size="xs" c={BRAND.textMuted}>
              {nextSet.reason}
            </Text>
          )}
        </Stack>
      )}
    </Stack>
  );
}

function TechniqueEquivalentLine({ preview }: { preview: TechniqueEquivalentPreview | null }) {
  if (!preview) return null;
  return (
    <Text size="sm" fw={700} ta="center" c={preview.capped ? 'orange.6' : 'teal.6'}>
      = {formatEquivalent(preview.application)} série equivalente · bloco: {formatEquivalent(preview.block)}/
      {formatEquivalent(preview.cap)}
      {preview.capped ? ' (teto)' : ''}
    </Text>
  );
}

/** "Dica da IA" numa linha, com "ver mais" inline (ver `aiTipView`). */
function AiTipCard({ tip }: { tip: AiTipView | null }) {
  if (!tip) return null;
  return (
    <section className={`${styles.tipRow} ${styles.tipRowAi}`} aria-label="Dica da IA" data-testid="ai-tip">
      <span className={styles.tipIcon} aria-hidden="true">
        <IconBulb size={18} />
      </span>
      <div className={styles.tipBody}>
        <ClampText lead={<span className={styles.tipTitle}>Dica da IA:</span>} text={tip.note} />
        {tip.realignedByEngine && (
          <p className={styles.aiTipFootnote} data-testid="ai-tip-engine-note">
            A carga da próxima série segue a regra do motor.
          </p>
        )}
      </div>
    </section>
  );
}

/**
 * M21 — "Treino anterior" numa linha: a série equivalente à atual (ou a 1ª)
 * da sessão anterior. "ver mais" abre, no lugar, a sessão INTEIRA (uma série
 * por linha, a equivalente destacada) e o recorde/e1RM do histórico nesse
 * equipamento, quando o backend os manda. Só séries de TRABALHO entram (ver
 * `workout-mode-previous-session.ts`). Sem nada: "Ainda não há execução
 * comparável" — nunca desempenho inventado.
 */
function PreviousSessionSummary({
  sets,
  record,
  highlight,
  weightUnit,
}: {
  sets: ReadonlyArray<PreviousSessionSet>;
  record?: PreviousSessionRecordSummary | null;
  highlight: number | null;
  weightUnit: WeightUnit;
}) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const entries = formatPreviousSessionSets(sets, weightUnit);
  const recordLines = formatPreviousSessionRecordLines(record, weightUnit);
  const empty = entries.length === 0 && recordLines.length === 0;
  const current = entries.find((entry) => entry.position === highlight) ?? entries[0] ?? null;
  const summary = current ? current.text : recordLines[0];
  const expandable = entries.length > 1 || recordLines.length > 0;
  const lead = <strong style={{ color: BRAND.textPrimary }}>Treino anterior:</strong>;

  return (
    <section className={styles.tipRow} aria-label="Treino anterior" data-testid="previous-session">
      <span className={styles.tipIcon} aria-hidden="true">
        <IconHistory size={18} />
      </span>
      <div className={styles.tipBody}>
        {empty ? (
          <p className={styles.clampText} data-testid="previous-session-empty">
            {lead} Ainda não há execução comparável
          </p>
        ) : (
          <>
            <div className={styles.clampRow}>
              <p className={styles.clampText} style={{ color: BRAND.textMuted }}>
                {lead} {summary}
              </p>
              {expandable && (
                <button
                  type="button"
                  className={styles.textToggle}
                  onClick={() => setExpanded((value) => !value)}
                  aria-expanded={expanded}
                  aria-controls={listId}
                >
                  {expanded ? 'ver menos' : 'ver mais'}
                </button>
              )}
            </div>
            <div id={listId} hidden={!expanded}>
              {entries.length > 0 && (
                <ul className={styles.historyList}>
                  {entries.map((entry) => (
                    <li
                      key={entry.position}
                      className={entry.position === highlight ? styles.historyCurrent : undefined}
                      aria-current={entry.position === highlight ? 'true' : undefined}
                    >
                      {entry.text}
                    </li>
                  ))}
                </ul>
              )}
              {recordLines.length > 0 && (
                <div className={styles.historyRecords} data-testid="previous-session-records">
                  {recordLines.map((line) => (
                    <span key={line}>{line}</span>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

/**
 * Card de registro de uma série (peso/reps/RPE + dor/anotações opcionais) —
 * peça central da execução do treino (Parte B: `PlanExecutionScreen`/
 * `FreeformScreen`). Componente de apresentação/formulário puro: quem chama
 * decide o exercício/série atual e trata o `onSave`.
 *
 * Novo visual (PR2/3): hero do exercício + card da série (badge "SÉRIE X DE
 * Y", alvo, campos em grade) + "Dica da IA" + "Treino anterior" + "Mais
 * opções" + CTA no rodapé sticky (`WorkoutActionBar`). Nenhum indicador de
 * progressão POR SÉRIE (rejeitado na spec): só o rótulo.
 */
export function SetLoggerCard({
  exerciseName,
  description,
  equipmentName,
  equipmentPhotoUrl,
  muscleGroup,
  muscleSecondaryGroup,
  setLabel,
  repsTarget,
  loadTarget,
  loadRangeText,
  rpeTarget,
  defaultReps,
  defaultWeight,
  defaultRpe,
  previousSessionSets,
  previousSessionHighlight,
  previousSessionRecord,
  currentGroupFatigue,
  weightUnit = DEFAULT_WEIGHT_UNIT,
  onWeightUnitChange,
  loadIncrementKg = null,
  progression,
  progressionNote,
  saving,
  saveLabel = 'Salvar série',
  onSave,
  saveError = null,
  onSubstituteClick,
  onSkipExercise,
  onFinishExercise,
  setType,
  peakContractionSeconds,
  techniqueDescription,
  variant = 'default',
  onSkip,
  staticRestSeconds,
  techniqueGoalLabel,
  techniqueEquivalent,
  techniqueAppliedItems,
  onEndTechnique,
  upcomingTechnique,
  upcomingTechniqueApplications,
  extraRepsEnabled = false,
  showMuscleMap = true,
  exerciseCard = null,
  engineDecision = null,
  lastSetStatus = null,
  measure = null,
  durationSecondsTarget = null,
  timeWindowSeconds = null,
  defaultDurationSeconds = null,
  perSetAiEnabled = false,
  aiEvaluation = null,
  beforeActions = null,
  goalBadge = null,
  unilateral = false,
}: Props) {
  // Lote T5: formato do card pela medida (núcleo do Modo Treino). Em reps,
  // `loadOptional` é false e nada abaixo muda.
  const measureShape = measureLoggerDefaults({ measure, durationSecondsTarget, timeWindowSeconds });
  const timerSpec = executionTimerSpec({ measure, durationSecondsTarget, timeWindowSeconds });
  const measuredSet = measureShape.loadOptional;
  const measureTarget = measureTargetText({ measure, durationSecondsTarget, timeWindowSeconds });
  const [durationSeconds, setDurationSeconds] = useState<number | null>(defaultDurationSeconds);
  const [reps, setReps] = useState<number | null>(defaultReps);
  // Campo de carga: `display` é o número na unidade escolhida (o que o usuário
  // digita e lê) e `kg` é o valor canônico que vai em `onSave`. Ver
  // `weight-units.ts` — é a âncora em kg que faz alternar kg<->lb ser uma
  // mudança só de exibição, sem o número "andar".
  const [weightField, setWeightField] = useState(() => weightFieldFromKg(defaultWeight, weightUnit));
  const [rpe, setRpe] = useState<number | null>(defaultRpe);
  // M102: lados (E/D) da série unilateral; começam pelos sugeridos da série.
  const [sides, setSides] = useState<Required<SetSides>>(() => ({
    reps_left: defaultReps,
    reps_right: defaultReps,
    rpe_left: defaultRpe,
    rpe_right: defaultRpe,
  }));
  const [musclePain, setMusclePain] = useState<number | null>(null);
  const [jointPain, setJointPain] = useState(false);
  const [notes, setNotes] = useState('');
  const [extraReps, setExtraReps] = useState<number | null>(null);
  const lightbox = usePhotoLightbox();
  const [cardViewerOpened, { open: openCardViewer, close: closeCardViewer }] = useDisclosure(false);
  const [infoOpened, { open: openInfo, close: closeInfo }] = useDisclosure(false);
  const descriptionText = (description || '').trim();
  const muscleMapItems = buildMuscleMapItems(muscleGroup, muscleSecondaryGroup);

  // Campos editados À MÃO nesta série: um valor sugerido que muda depois
  // (avaliação da IA chegando, refetch) não apaga o que o usuário digitou.
  // Trocar de série/exercício/equipamento reinicia tudo pelos sugeridos.
  const setKey = `${exerciseName}|${equipmentName ?? ''}|${setLabel}`;
  const lastSetKeyRef = useRef(setKey);
  const editedRef = useRef<Set<LoggerField>>(new Set());
  const markEdited = (field: LoggerField) => editedRef.current.add(field);

  // Repopula os valores editáveis sempre que a série/exercício mudar (ou o
  // sugerido mudar num campo ainda não editado). Dor/anotação são sempre por
  // série — nunca herdam da série anterior.
  useEffect(() => {
    const keyChanged = lastSetKeyRef.current !== setKey;
    lastSetKeyRef.current = setKey;
    if (keyChanged) editedRef.current = new Set();
    const refresh = fieldsToRefresh(keyChanged, editedRef.current);
    if (refresh.has('reps')) {
      setReps(defaultReps);
      setSides((current) => ({ ...current, reps_left: defaultReps, reps_right: defaultReps }));
    }
    if (refresh.has('weight')) setWeightField((current) => weightFieldFromKg(defaultWeight, current.unit));
    if (refresh.has('rpe')) {
      setRpe(defaultRpe);
      setSides((current) => ({ ...current, rpe_left: defaultRpe, rpe_right: defaultRpe }));
    }
    if (refresh.has('duration')) setDurationSeconds(defaultDurationSeconds);
    if (keyChanged) {
      setMusclePain(null);
      setJointPain(false);
      setNotes('');
      setExtraReps(null);
    }
  }, [setKey, defaultReps, defaultWeight, defaultRpe, defaultDurationSeconds]);

  const editReps = (value: number | null) => {
    markEdited('reps');
    setReps(value);
  };
  const editRpe = (value: number | null) => {
    markEdited('rpe');
    setRpe(value);
  };
  const editSide = (field: keyof SetSides, value: number | null) => {
    markEdited(field.startsWith('reps') ? 'reps' : 'rpe');
    setSides((current) => ({ ...current, [field]: value }));
  };
  const editDuration = (value: number | null) => {
    markEdited('duration');
    setDurationSeconds(value);
  };
  const editWeight = (value: number | null) => {
    markEdited('weight');
    setWeightField((current) => weightFieldOnInput(current, value));
  };

  // Botão de salvar: os campos que a medida exige preenchidos (reps em
  // reps/máximo; segundos dentro dos limites do dono em tempo; carga só em reps).
  const measuredSaveBlocked =
    (measureShape.repsRequired && !isMaxRepsValid(reps)) ||
    (measureShape.durationRequired && !isDurationSecondsValid(durationSeconds)) ||
    (!measureShape.loadOptional && weightField.display == null);
  const measuredValues = (): Pick<SetLoggerValues, 'reps' | 'weight_kg' | 'measure' | 'duration_seconds'> => ({
    reps: measureShape.repsRequired ? reps : null,
    weight_kg: weightField.kg,
    measure: normalizeMeasure(measure),
    duration_seconds: measureShape.durationRequired ? durationSeconds : measureShape.windowSeconds,
  });

  // Troca de unidade (pelo alternador ou pela preferência chegando do
  // servidor depois do primeiro render): reescreve o valor JÁ DIGITADO
  // passando pelo kg canônico, nunca o descarta nem o reinterpreta. É a
  // garantia de que alternar kg<->lb não corrompe o que está na tela.
  useEffect(() => {
    setWeightField((current) => weightFieldOnUnitChange(current, weightUnit));
  }, [weightUnit]);

  // Ações do exercício, explícitas no rodapé, ao lado do CTA (ícone +
  // legenda; rótulo completo no aria-label/tooltip). "Info" fica no título.
  const exerciseActions: WorkoutIconAction[] = [];
  if (onSubstituteClick) {
    exerciseActions.push({
      key: 'swap',
      label: 'Trocar equipamento/exercício',
      caption: 'Trocar',
      icon: <IconRepeat size={20} />,
      onClick: onSubstituteClick,
      testId: 'execution-swap',
    });
  }
  if (onSkipExercise) {
    exerciseActions.push({
      key: 'skip',
      label: 'Pular exercício',
      caption: 'Pular',
      icon: <IconPlayerTrackNext size={20} />,
      onClick: onSkipExercise,
      testId: 'execution-skip',
    });
  }
  if (onFinishExercise) {
    exerciseActions.push({
      key: 'finish',
      label: 'Encerrar exercício',
      caption: 'Encerrar',
      icon: <IconPlayerStop size={20} />,
      onClick: onFinishExercise,
      testId: 'execution-finish-exercise',
    });
  }

  // "Info: como executar": com descrição, abre a gaveta (descrição + card);
  // só com o card, abre o card direto (como antes). Sem nenhum dos dois, nada.
  const infoButton =
    exerciseCard || descriptionText ? (
      <button
        type="button"
        className={styles.iconButton}
        onClick={descriptionText ? openInfo : openCardViewer}
        aria-label="Info: como executar"
        title="Info: como executar"
        aria-haspopup="dialog"
        data-testid="exercise-card-open"
      >
        <IconInfoCircle size={22} aria-hidden="true" />
      </button>
    ) : null;

  const notesValue = notes.trim() ? notes.trim() : null;
  const unitSuffix = <WeightUnitSuffix unit={weightUnit} onChange={onWeightUnitChange} />;
  const errorNotice = saveError ? (
    <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} py={6} role="alert">
      <Text size="sm">{saveError}</Text>
    </Alert>
  ) : null;

  // Corpo do card de série + rodapé de ação, por variante. Todas as variantes
  // usam o MESMO invólucro (hero, card da série, rodapé sticky); a lógica de
  // cada uma (campos, passos, o que vai em `onSave`) é a de sempre.
  let badgeIcon: ReactNode = null;
  let setBody: ReactNode;
  let afterSetCard: ReactNode = null;
  let actionBar: ReactNode;
  let targetLine: string | null = null;

  if (variant === 'advancedWarmup' || variant === 'advancedFeeler') {
    badgeIcon = variant === 'advancedWarmup' ? <IconFlame size={18} /> : <IconTool size={18} />;
    setBody = (
      <div className={`${styles.fieldGrid} ${styles.fieldGrid2}`}>
        <SteppedField label="Repetições" value={reps} onChange={editReps} step={1} min={1} decimalScale={0} />
        <SteppedField
          // unidade como sufixo tocável do rótulo (o sufixo no input estreito era cortado)
          label="Carga"
          accessibleName={`Carga (${weightUnit})`}
          labelSuffix={unitSuffix}
          value={weightField.display}
          onChange={editWeight}
          step={WEIGHT_STEP[weightUnit].fine}
          min={0}
          decimalScale={DISPLAY_DECIMALS[weightUnit]}
          suffix=""
        />
      </div>
    );
    actionBar = (
      <WorkoutActionBar
        primaryLabel="Concluir"
        primaryIcon={<IconCheck size={20} />}
        primaryLoading={saving}
        primaryTestId="set-save"
        notice={errorNotice}
        iconActions={exerciseActions}
        onPrimary={() =>
          onSave({
            reps: reps ?? 0,
            weight_kg: weightField.kg ?? 0,
            rpe: null,
            muscle_pain: musclePain,
            joint_pain: jointPain,
            notes: notesValue,
            set_type: setType,
          })
        }
        secondary={[{ key: 'skip-set', label: 'Pular série', onClick: () => onSkip?.(), testId: 'set-skip' }]}
      />
    );
  } else if (variant === 'techniqueApplication') {
    badgeIcon = <IconArrowUpRight size={18} />;
    const techniqueSaveBlocked = reps == null || weightField.display == null;
    setBody = (
      <>
        {techniqueDescription && (
          <Alert color="indigo" variant="light" icon={<IconInfoCircle size={16} />} py={6}>
            <Text size="xs">{techniqueDescription}</Text>
          </Alert>
        )}

        <Group justify="space-between" wrap="wrap" gap={8}>
          <Text size="sm" fw={700} c={BRAND.textPrimary}>
            Meta: {techniqueGoalLabel || 'até a falha'}
          </Text>
          {staticRestSeconds != null && staticRestSeconds > 0 && (
            <Badge variant="light" color="gray" leftSection={<IconClockHour4 size={12} />}>
              Pausa {staticRestSeconds}s
            </Badge>
          )}
        </Group>

        {techniqueAppliedItems && techniqueAppliedItems.length > 0 && (
          <Stack gap={2}>
            {techniqueAppliedItems.map((item) => (
              <Text key={item} size="xs" c={BRAND.textMuted}>
                ↳ {item}
              </Text>
            ))}
          </Stack>
        )}

        <div className={styles.fieldGrid}>
          <SteppedField label="Reps" value={reps} onChange={editReps} step={1} min={0} decimalScale={0} />
          <SteppedField label="RPE" value={rpe} onChange={editRpe} step={0.5} min={0} decimalScale={1} />
          <SteppedField
            label="Carga"
            accessibleName={`Carga (${weightUnit})`}
            labelSuffix={unitSuffix}
            value={weightField.display}
            onChange={editWeight}
            step={WEIGHT_STEP[weightUnit].fine}
            min={0}
            decimalScale={DISPLAY_DECIMALS[weightUnit]}
            suffix=""
          />
        </div>

        <TechniqueEquivalentLine preview={techniqueEquivalent ? techniqueEquivalent(reps, rpe) : null} />
      </>
    );
    const secondary: WorkoutSecondaryAction[] = [
      {
        key: 'skip-application',
        label: 'Pular aplicação',
        onClick: () => onSkip?.(),
        disabled: saving,
        testId: 'set-skip',
      },
    ];
    if (onEndTechnique) {
      secondary.push({ key: 'end-technique', label: 'Encerrar técnica', onClick: onEndTechnique, disabled: saving });
    }
    actionBar = (
      <WorkoutActionBar
        primaryLabel="Concluir"
        primaryIcon={<IconCheck size={20} />}
        primaryLoading={saving}
        primaryDisabled={techniqueSaveBlocked}
        primaryTestId="set-save"
        notice={errorNotice}
        iconActions={exerciseActions}
        onPrimary={() =>
          onSave({
            reps: reps ?? 0,
            weight_kg: weightField.kg ?? 0,
            rpe,
            muscle_pain: musclePain,
            joint_pain: jointPain,
            notes: notesValue,
            set_type: setType,
          })
        }
        secondary={secondary}
      />
    );
  } else {
    targetLine = setTargetLine({
      measured: measuredSet,
      measureTarget,
      repsTarget,
      loadText: formatLoadTarget(loadTarget, weightUnit),
      rpeTarget,
    });
    // M102: E/D só na série de trabalho/backoff do card padrão (v1).
    const sidesMode = unilateral && variant === 'default' && !measuredSet && setTypeTakesSides(setType);
    const repsMissing = sidesMode ? sides.reps_left == null && sides.reps_right == null : reps == null;
    const saveBlocked = measuredSet ? measuredSaveBlocked : repsMissing || weightField.display == null;
    setBody = (
      <>
        <EngineVoiceLine engineDecision={engineDecision} lastSetStatus={lastSetStatus} />

        {(upcomingTechnique === 'drop_set' || upcomingTechnique === 'rest_pause') && (
          <Badge variant="light" color="green" leftSection={<IconArrowUpRight size={14} />} style={{ alignSelf: 'flex-start' }}>
            Ao final:{' '}
            {upcomingTechnique === 'drop_set'
              ? `Drop-set (${upcomingTechniqueApplications} drops)`
              : `Rest-pause (${upcomingTechniqueApplications} mini-blocos)`}
          </Badge>
        )}

        {isExtraRepsTechnique(upcomingTechnique) && (
          <Badge variant="filled" color="orange" leftSection={<IconBolt size={14} />} style={{ alignSelf: 'flex-start' }}>
            Após a falha: {upcomingTechniqueApplications} repetições (
            {getAdvancedTechniqueLabel(upcomingTechnique)?.toLowerCase()})
          </Badge>
        )}

        {peakContractionSeconds != null && (
          <Alert color="violet" variant="light" icon={<IconClockHour4 size={16} />} py={6}>
            <Text size="xs" fw={variant === 'advancedWorking' ? 700 : undefined}>
              Segure a contração de pico por {peakContractionSeconds}s na última repetição.
            </Text>
          </Alert>
        )}

        {techniqueDescription && variant !== 'advancedWorking' && (
          <Alert color="indigo" variant="light" icon={<IconInfoCircle size={16} />} py={6}>
            <Text size="xs">{techniqueDescription}</Text>
          </Alert>
        )}

        {(progression === 'increased' || progression === 'decreased') && (
          <Alert
            color={progression === 'increased' ? 'green' : 'red'}
            variant="light"
            icon={progression === 'increased' ? <IconArrowUp size={16} /> : <IconArrowDown size={16} />}
            py={6}
          >
            <Text size="xs">
              {progression === 'increased'
                ? 'Carga ajustada para cima em relação à última vez.'
                : 'Carga ajustada para baixo em relação à última vez.'}
              {progressionNote ? ` ${progressionNote}` : ''}
            </Text>
          </Alert>
        )}

        {measuredSet ? (
          // Lote T5: série por tempo (cronômetro + "Segundos") ou de máximo
          // de reps (janela/cronômetro livre + reps obrigatórias); carga
          // opcional nas duas.
          <div className={styles.setBody} style={{ marginTop: 0 }} data-testid="measured-set-fields">
            <div className={styles.innerPanel}>
              <ExecutionTimer
                spec={timerSpec}
                resetKey={`${exerciseName}-${setLabel}`}
                onStop={(seconds) => {
                  if (measureShape.durationRequired) editDuration(seconds);
                }}
              />
            </div>
            <div className={styles.fieldGrid}>
              {measureShape.durationRequired && (
                <SteppedField
                  label="Segundos"
                  value={durationSeconds}
                  onChange={editDuration}
                  step={1}
                  min={MEASURE_LIMITS.durationSecondsMin}
                  max={MEASURE_LIMITS.durationSecondsMax}
                  decimalScale={0}
                />
              )}
              {measureShape.repsRequired && (
                <SteppedField
                  label="Reps"
                  value={reps}
                  onChange={editReps}
                  step={1}
                  min={MEASURE_LIMITS.maxRepsMinReps}
                  decimalScale={0}
                />
              )}
              <SteppedField
                label="Carga"
                accessibleName={`Carga (${weightUnit}, opcional)`}
                labelSuffix={unitSuffix}
                placeholder="opcional"
                value={weightField.display}
                onChange={editWeight}
                step={loadStepperStep(weightUnit, loadIncrementKg)}
                min={0}
                decimalScale={DISPLAY_DECIMALS[weightUnit]}
                suffix=""
              />
              <SteppedField label="RPE" value={rpe} onChange={editRpe} step={0.5} min={0} decimalScale={1} />
            </div>
          </div>
        ) : sidesMode ? (
          // M102: série unilateral — reps e RPE por lado, carga única (total).
          <div className={styles.fieldGrid} data-testid="unilateral-set-fields">
            <SteppedField
              label="Reps E"
              accessibleName="Repetições do lado esquerdo"
              value={sides.reps_left}
              onChange={(value) => editSide('reps_left', value)}
              step={1}
              min={0}
              decimalScale={0}
            />
            <SteppedField
              label="Reps D"
              accessibleName="Repetições do lado direito"
              value={sides.reps_right}
              onChange={(value) => editSide('reps_right', value)}
              step={1}
              min={0}
              decimalScale={0}
            />
            <SteppedField
              label="RPE E"
              accessibleName="RPE do lado esquerdo"
              value={sides.rpe_left}
              onChange={(value) => editSide('rpe_left', value)}
              step={0.5}
              min={0}
              decimalScale={1}
            />
            <SteppedField
              label="RPE D"
              accessibleName="RPE do lado direito"
              value={sides.rpe_right}
              onChange={(value) => editSide('rpe_right', value)}
              step={0.5}
              min={0}
              decimalScale={1}
            />
            <SteppedField
              label="Carga"
              accessibleName={`Carga (${weightUnit})`}
              labelSuffix={unitSuffix}
              value={weightField.display}
              onChange={editWeight}
              step={loadStepperStep(weightUnit, loadIncrementKg)}
              min={0}
              decimalScale={DISPLAY_DECIMALS[weightUnit]}
              suffix=""
              full
            />
          </div>
        ) : (
          <div className={styles.fieldGrid}>
            <SteppedField label="Reps" value={reps} onChange={editReps} step={1} min={0} decimalScale={0} />
            <SteppedField
              label="Carga"
              accessibleName={`Carga (${weightUnit})`}
              labelSuffix={unitSuffix}
              value={weightField.display}
              onChange={editWeight}
              step={variant === 'advancedWorking' ? WEIGHT_STEP[weightUnit].fine : loadStepperStep(weightUnit, loadIncrementKg)}
              min={0}
              decimalScale={DISPLAY_DECIMALS[weightUnit]}
              suffix=""
            />
            <SteppedField label="RPE" value={rpe} onChange={editRpe} step={0.5} min={0} decimalScale={1} />
            {extraRepsEnabled && (
              <SteppedField
                label={`Reps extras (${getAdvancedTechniqueLabel(upcomingTechnique)?.toLowerCase() ?? 'além da falha'})`}
                value={extraReps}
                onChange={setExtraReps}
                step={1}
                min={0}
                decimalScale={0}
                full
              />
            )}
          </div>
        )}
      </>
    );

    afterSetCard = (
      <>
        <AiTipCard tip={aiTipView(perSetAiEnabled, aiEvaluation)} />

        {previousSessionSets != null && (
          <PreviousSessionSummary
            sets={previousSessionSets}
            record={previousSessionRecord}
            highlight={previousSessionHighlight ?? null}
            weightUnit={weightUnit}
          />
        )}

        {/* "Mais opções" (dor / anotações) e o mapa muscular lado a lado em
            linhas de 44 px; a que abrir ocupa a largura toda, no lugar. */}
        <div className={styles.pairRow}>
          <SectionAccordion
            icon={<IconNotes size={18} />}
            title="Mais opções"
            summary="Dor / anotações"
            openClassName={styles.pairOpen}
            testId="more-options"
          >
            <Stack gap="sm">
              <NumberInput
                label="Dor muscular (0-10)"
                placeholder="Sem dor reportada"
                value={musclePain ?? undefined}
                onChange={(v) => setMusclePain(v === '' ? null : Number(v))}
                min={0}
                max={10}
                step={1}
              />
              <Switch
                label="Dor articular nesta série"
                checked={jointPain}
                onChange={(event) => setJointPain(event.currentTarget.checked)}
              />
              <Textarea
                label="Anotações da série"
                placeholder="Ex: técnica comprometida na última rep, ombro incomodando..."
                value={notes}
                onChange={(event) => setNotes(event.currentTarget.value)}
                autosize
                minRows={2}
              />
            </Stack>
          </SectionAccordion>
          {showMuscleMap && (
            <MuscleMapDisclosure
              items={muscleMapItems}
              title="Músculos"
              subject={muscleGroup}
              openClassName={styles.pairOpen}
            />
          )}
        </div>
      </>
    );

    actionBar = (
      <WorkoutActionBar
        primaryLabel={saveLabel}
        primaryIcon={<IconCheck size={20} />}
        primaryLoading={saving}
        primaryDisabled={saveBlocked}
        primaryTestId="set-save"
        iconActions={exerciseActions}
        notice={
          errorNotice ??
          (saveBlocked && !saving ? (
            <Text size="sm" c={BRAND.textMuted} data-testid="set-save-blocked">
              Preencha os campos da série para finalizar.
            </Text>
          ) : null)
        }
        onPrimary={() =>
          onSave(
            measuredSet
              ? {
                  ...measuredValues(),
                  rpe,
                  muscle_pain: musclePain,
                  joint_pain: jointPain,
                  notes: notesValue,
                  set_type: setType,
                }
              : sidesMode
                ? {
                    // M102: o servidor grava reps/RPE oficiais (pior lado).
                    reps: null,
                    weight_kg: weightField.kg ?? 0,
                    rpe: null,
                    ...sides,
                    muscle_pain: musclePain,
                    joint_pain: jointPain,
                    notes: notesValue,
                    set_type: setType,
                    ...(extraRepsEnabled ? { extra_reps: extraReps } : {}),
                  }
                : {
                    reps: reps ?? 0,
                    weight_kg: weightField.kg ?? 0,
                    rpe,
                    muscle_pain: musclePain,
                    joint_pain: jointPain,
                    notes: notesValue,
                    set_type: setType,
                    ...(extraRepsEnabled ? { extra_reps: extraReps } : {}),
                  },
          )
        }
      />
    );
  }

  return (
    <div className={`${styles.root} ${styles.loggerRoot}`} style={WORKOUT_MODE_CSS_VARS} data-testid="set-logger">
      {/* Título compacto: foto, nome (2 linhas), equipamento · fadiga (chip
          único da tela) e Info. Trocar / Pular / Encerrar ficam no rodapé. */}
      <ExerciseTitleRow
        name={exerciseName}
        equipmentName={equipmentName}
        photoUrl={equipmentPhotoUrl}
        onPhotoClick={() => {
          if (equipmentPhotoUrl) lightbox.open({ src: equipmentPhotoUrl, alt: equipmentName || exerciseName });
        }}
        meta={
          goalBadge || currentGroupFatigue !== undefined ? (
            <>
              {goalBadge}
              {currentGroupFatigue !== undefined ? <MuscleFatigueChip reading={currentGroupFatigue} /> : null}
            </>
          ) : null
        }
        trailing={infoButton}
        testId="execution-hero"
      />

      <section className={styles.setCard} aria-label={setLabel} data-testid="set-card">
        <div className={styles.setHeader}>
          <span className={styles.setBadge} data-testid="set-badge">
            {badgeIcon && (
              <span aria-hidden="true" style={{ display: 'inline-flex' }}>
                {badgeIcon}
              </span>
            )}
            {setLabel.toLocaleUpperCase('pt-BR')}
          </span>
          {targetLine && (
            <span className={styles.targetLine} data-testid="set-target">
              {targetLine}
            </span>
          )}
          {loadRangeText && (
            <span className={styles.targetLine} data-testid="set-strength-load">
              Faixa: {loadRangeText}
            </span>
          )}
        </div>
        <div className={styles.setBody}>{setBody}</div>
      </section>

      {afterSetCard}

      {beforeActions}

      {actionBar}

      <PhotoLightbox photo={lightbox.photo} onClose={lightbox.close} />
      {descriptionText && (
        <Drawer
          opened={infoOpened}
          onClose={closeInfo}
          position="bottom"
          size="auto"
          title="Como executar"
          styles={{
            content: {
              maxHeight: '88dvh',
              background: BRAND.bgElevated,
              borderTopLeftRadius: 16,
              borderTopRightRadius: 16,
            },
            header: { background: BRAND.bgElevated },
            title: { fontWeight: 800, color: BRAND.textPrimary },
          }}
        >
          <div className={`${styles.root} ${styles.drawerBody}`} style={WORKOUT_MODE_CSS_VARS} data-testid="execution-info">
            <p style={{ margin: 0 }}>{descriptionText}</p>
            {exerciseCard && (
              <UnstyledButton
                className={styles.linkRow}
                onClick={() => {
                  closeInfo();
                  openCardViewer();
                }}
                aria-label="Ampliar o card de como executar"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={exerciseCard.thumbnail_url}
                  alt={`Como executar: ${exerciseName}`}
                  width={32}
                  height={48}
                  decoding="async"
                  style={{ borderRadius: 6, objectFit: 'cover', flexShrink: 0, background: BRAND.bgSurface }}
                />
                <span className={styles.linkRowText}>Card de como executar</span>
                <IconChevronRight size={18} color={BRAND.textMuted} aria-hidden="true" />
              </UnstyledButton>
            )}
          </div>
        </Drawer>
      )}
      {exerciseCard && (
        <ExerciseCardViewer
          card={exerciseCard}
          opened={cardViewerOpened}
          onClose={closeCardViewer}
          exerciseName={exerciseName}
        />
      )}
    </div>
  );
}


type EvaluationProps = {
  loading: boolean;
  error: boolean;
  result: SetEvaluationResult | null;
};

// Card de avaliação de IA, exibido assim que `POST /exercises/evaluate-set`
// responde — chamado em paralelo com o início do `RestTimer`, nunca bloqueia
// o fluxo do treino (loading discreto, erro tratado como "indisponível").
export function SetEvaluationCard({ loading, error, result }: EvaluationProps) {
  if (!loading && !error && !result) return null;
  // set-eval-v4: categoria = ação do motor (rótulo/direção do JSON gerado);
  // categoria antiga em cache vira selo neutro.
  const category = result ? setEvaluationCategoryView(result.category, result.category_label) : null;
  const loadNotes = (result?.load_adjustments || [])
    .map((adjustment) => loadAdjustmentText(adjustment))
    .filter((text): text is string => Boolean(text));

  return (
    <Card withBorder radius="xl" p="md" style={{ background: BRAND.bgSurface, borderColor: BRAND.border }}>
      {loading && (
        <Group gap="xs">
          <Loader size="xs" color="brand" />
          <Text size="sm" c={BRAND.textMuted}>
            Avaliando série com IA...
          </Text>
        </Group>
      )}
      {!loading && error && (
        <Alert color="gray" variant="light" icon={<IconAlertTriangle size={16} />} py={6}>
          <Text size="xs" c={BRAND.textMuted}>
            Avaliação de IA indisponível para esta série.
          </Text>
        </Alert>
      )}
      {!loading && !error && result && (
        <Group align="flex-start" gap="sm" wrap="nowrap">
          <IconSparkles size={20} color={BRAND.orange} style={{ flexShrink: 0, marginTop: 2 }} />
          <div>
            {category && (
              <Badge size="sm" color={category.color} variant="light" mb={4}>
                {category.label}
              </Badge>
            )}
            <Text size="sm" c={BRAND.textPrimary}>
              {result.note}
            </Text>
            {/* PR-14 (§5.7): a sugestão numérica da IA passa pelo teto seguro do
                par no SERVIDOR. Quando o corte acontece, o usuário vê por quê —
                senão o número "mudaria sozinho" sem explicação. */}
            {(result.clamps || []).map((clamp) => (
              <Badge
                key={`${clamp.field}-${clamp.from}-${clamp.to}`}
                size="sm"
                color="orange"
                variant="light"
                mt={6}
                leftSection={<IconAlertTriangle size={12} />}
              >
                RPE sugerido limitado por segurança: {clamp.from} → {clamp.to}
              </Badge>
            ))}
            {/* M76 Lote 1c / Lote 6: corte de CARGA (passo do equipamento ou
                alvo do motor), separado do corte de RPE acima. */}
            {loadNotes.map((text) => (
              <Badge key={text} size="sm" color="blue" variant="light" mt={6}>
                {text}
              </Badge>
            ))}
          </div>
        </Group>
      )}
    </Card>
  );
}

type ExerciseSummaryProps = {
  loading: boolean;
  error: boolean;
  result: ExerciseSummaryResult | null;
};

// Card de resumo de fim de EXERCÍCIO, exibido assim que
// `POST /exercises/{id}/summarize` responde — disparado incondicionalmente
// quando a última série de um exercício é salva. Nunca bloqueia o fluxo do
// treino: loading discreto, erro tratado como "indisponível".
export function ExerciseSummaryCard({ loading, error, result }: ExerciseSummaryProps) {
  if (!loading && !error && !result) return null;

  return (
    <Card withBorder radius="xl" p="md" style={{ background: BRAND.bgSurface, borderColor: BRAND.border }}>
      {loading && (
        <Group gap="xs">
          <Loader size="xs" color="brand" />
          <Text size="sm" c={BRAND.textMuted}>
            Resumindo exercício com IA...
          </Text>
        </Group>
      )}
      {!loading && error && (
        <Alert color="gray" variant="light" icon={<IconAlertTriangle size={16} />} py={6}>
          <Text size="xs" c={BRAND.textMuted}>
            Resumo de IA indisponível para este exercício.
          </Text>
        </Alert>
      )}
      {!loading && !error && result && (
        <Group align="flex-start" gap="sm" wrap="nowrap">
          <IconSparkles size={20} color={BRAND.orange} style={{ flexShrink: 0, marginTop: 2 }} />
          <div>
            <Text size="xs" fw={700} c={BRAND.orange} mb={2}>
              Resumo do exercício
            </Text>
            <Text size="sm" c={BRAND.textPrimary}>
              {result.summary}
            </Text>
          </div>
        </Group>
      )}
    </Card>
  );
}
