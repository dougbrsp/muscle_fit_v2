'use client';

import { Alert, Drawer, Group, Loader, Text, UnstyledButton } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconAdjustments,
  IconAlertTriangle,
  IconArrowsExchange,
  IconBodyScan,
  IconBolt,
  IconChartBar,
  IconChevronRight,
  IconChevronUp,
  IconClipboardList,
  IconFeather,
  IconHistory,
  IconInfoCircle,
  IconPlayerPlay,
  IconPlayerTrackNext,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { apiFetch } from '@/lib/api';
import { getAdvancedTechniqueDescription, getAdvancedTechniqueLabel } from '@/lib/advanced-techniques';
import {
  adjustmentLabel,
  buildBriefingSections,
  formatExpectedLoad,
  formatStrengthLoadRange,
  formatExpectedRpe,
  formatLocalDate,
  formatSetLine,
  formatSetsAndReps,
  groupMusclesByRole,
  NO_COMPARISON_LABEL,
  parseExecutionDescription,
  performanceBadge,
  performanceExplanation,
  summarizeLastExecution,
  type BriefingSection,
  type BriefingSectionKey,
} from '@/lib/exercise-briefing';
import { deloadLoadOrigin, deloadPrescriptionText } from '@/lib/deload';
import type { ExerciseBriefingResponse } from '@/lib/exercise-briefing-types';
import { recoveryCeilingText } from '@/lib/intensity-status';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import type { WeightUnit } from '@/lib/weight-units';
import type { GroupFatigueReading } from '@/lib/workout-mode-types';
import type { ExerciseCardSummary } from '@/lib/workout-plans-types';
import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';
import { ExerciseCardViewer } from './ExerciseCardViewer';
import { ClampText } from './ui/ClampText';
import { ExerciseTitleRow } from './ui/ExerciseTitleRow';
import type { WorkoutIconAction } from './ui/IconAction';
import { MetricTile } from './ui/MetricTile';
import { MuscleFatigueChip } from './ui/MuscleFatigueChip';
import { NextExerciseLine } from './ui/NextExerciseLine';
import { SectionAccordion } from './ui/SectionAccordion';
import { StatusChip } from './ui/StatusChip';
import { WorkoutActionBar } from './ui/WorkoutActionBar';
import { WORKOUT_MODE_CSS_VARS } from './ui/workout-mode-css-vars';
import styles from './workout-mode.module.css';

/**
 * Exercício efetivo quando ele foi TROCADO nesta sessão (override de
 * equipamento/exercício antes da 1ª série). O briefing do backend descreve o
 * item ORIGINAL da ficha (`.../exercises/{wpe}/briefing`): com a troca, o
 * título e as métricas passam a mostrar o exercício efetivo (os mesmos alvos
 * que a execução usa), e comparação, ajuste do motor e última execução — que
 * são do par original — não aparecem.
 */
export type BriefingSubstitution = {
  exerciseName: string;
  equipmentName: string | null;
  equipmentPhotoUrl: string | null;
  /** Mesmo exercício canônico (só o equipamento mudou): técnica e músculos valem. */
  sameExercise: boolean;
  setsCount: number | null;
  repsTarget: string | null;
  loadTarget: string | null;
  rpeTarget: string | null;
};

export type BriefingNextExercise = { name: string; muscle?: string | null };

type SharedProps = {
  exerciseName: string;
  onStart: () => void;
  // Card do exercício EFETIVO quando quem chama sabe que ele difere do item
  // da ficha (troca de exercício antes do briefing). Ausente (undefined) =
  // usa o `exercise.exercise_card` do próprio briefing.
  exerciseCard?: ExerciseCardSummary | null;
  weightUnit?: WeightUnit;
  /** Leitura de fadiga já carregada pela tela (sem request novo). `undefined` = ainda carregando: chip oculto. */
  groupFatigue?: GroupFatigueReading | null;
  /** Mesmo fluxo da execução: `EquipmentSubstituteModal`. Ausente = ação indisponível. */
  onSwap?: () => void;
  /** Mesmo fluxo da execução: `SkipExerciseModal`. Ausente = ação indisponível. */
  onSkip?: () => void;
  /** Próximo exercício da fila; `null` = este é o último; `undefined` = não mostrar. */
  nextExercise?: BriefingNextExercise | null;
  substitution?: BriefingSubstitution | null;
  /** M97 F3: badge do objetivo de treino do item (rótulo da API). */
  goalBadge?: ReactNode;
  /** M97 F3: o objetivo do item não permite a técnica (o Modo Treino não a oferece). */
  hideTechnique?: boolean;
};

/**
 * Briefing do exercício (M4; novo visual no PR1/3 do redesign; "uma tela" no
 * PR4): a tela que aparece ENTRE a lista da ficha e a execução, antes da
 * primeira série. Cabe numa viewport de celular sem rolar: título compacto,
 * métricas numa linha, resumos de 1 linha e as instruções técnicas numa
 * gaveta ("Como executar"), com o alerta de Atenção visível em 1 linha.
 *
 * Tudo vem de UMA chamada (`.../briefing`), montada no backend — ver
 * `lib/exercise-briefing-types.ts` para o porquê. A fadiga do grupo vem da
 * consulta que a tela de execução JÁ faz (`last-session-sets`), repassada
 * por props: nenhum request novo.
 *
 * Nada aqui pode impedir o usuário de treinar: se a chamada falhar, o botão
 * "Iniciar exercício" continua lá, e a tela diz que o briefing não carregou.
 */
export function ExerciseBriefingScreen({
  planId,
  workoutPlanExerciseId,
  sessionId,
  ...props
}: SharedProps & { planId: number; workoutPlanExerciseId: number; sessionId?: number | null }) {
  const query = useQuery({
    queryKey:
      sessionId != null
        ? ['exercise-briefing', planId, workoutPlanExerciseId, sessionId]
        : ['exercise-briefing', planId, workoutPlanExerciseId],
    // M97 F3: com a sessão, o bloco `deload` é o gravado nela (avulso ou período).
    queryFn: () =>
      apiFetch<ExerciseBriefingResponse>(
        `/workout-plans/${planId}/exercises/${workoutPlanExerciseId}/briefing` +
          (sessionId != null ? `?session_id=${sessionId}` : ''),
      ),
    staleTime: 5 * 60 * 1000,
  });

  // Garante que o catalogo de tecnicas esteja carregado antes de rotular a
  // tecnica avancada (o hook re-renderiza quando ele chega).
  useAdvancedTechniques();

  return <ExerciseBriefingView {...props} data={query.data} isLoading={query.isLoading} isError={query.isError} />;
}

const SECTION_ICONS: Record<BriefingSectionKey, ReactNode> = {
  preparation: <IconClipboardList size={18} />,
  execution: <IconPlayerPlay size={18} />,
  attention: <IconAlertTriangle size={18} />,
  other: <IconInfoCircle size={18} />,
};

/**
 * Conteúdo da gaveta "Como executar": SÓ as instruções técnicas do briefing
 * (Preparação, Execução, Atenção e Músculos), íntegras e abertas, e o card
 * ilustrado do exercício quando houver. Apresentação pura (sem fetch).
 */
export function BriefingInstructions({
  sections,
  muscleGroups,
  exerciseCard,
  exerciseName,
  onOpenCard,
}: {
  sections: BriefingSection[];
  muscleGroups: { role: string; label: string; names: string[] }[];
  exerciseCard?: ExerciseCardSummary | null;
  exerciseName: string;
  onOpenCard?: () => void;
}) {
  return (
    <div className={styles.drawerBody} data-testid="briefing-instructions">
      {sections.map((section, index) => (
        <section
          key={`${section.key}-${index}`}
          className={section.key === 'attention' ? `${styles.drawerSection} ${styles.drawerWarning}` : styles.drawerSection}
          data-testid={`briefing-section-${section.key}`}
        >
          <div className={styles.drawerSectionTitle}>
            <span aria-hidden="true">{SECTION_ICONS[section.key]}</span>
            <span>{section.title}</span>
          </div>
          <ul className={styles.stepList}>
            {section.steps.map((step, stepIndex) => (
              <li key={stepIndex}>{step}</li>
            ))}
          </ul>
        </section>
      ))}

      {muscleGroups.length > 0 && (
        <section className={styles.drawerSection} data-testid="briefing-section-muscles">
          <div className={styles.drawerSectionTitle}>
            <span aria-hidden="true">
              <IconBodyScan size={18} />
            </span>
            <span>Músculos trabalhados</span>
          </div>
          <ul className={styles.stepList}>
            {muscleGroups.map((group) => (
              <li key={group.role}>
                <strong>{group.label}:</strong> {group.names.join(', ')}
              </li>
            ))}
          </ul>
        </section>
      )}

      {exerciseCard && (
        <UnstyledButton
          onClick={onOpenCard}
          aria-label="Ampliar o card de como executar"
          data-testid="briefing-exercise-card"
          className={styles.linkRow}
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
          <span className={styles.linkRowText}>
            Card de como executar
            <span style={{ display: 'block', color: BRAND.textMuted, fontSize: 12, fontWeight: 600 }}>
              Toque para ampliar
            </span>
          </span>
          <IconChevronRight size={18} color={BRAND.textMuted} aria-hidden="true" />
        </UnstyledButton>
      )}
    </div>
  );
}

/** Apresentação pura do briefing (sem fetch): é o que o teste renderiza. */
export function ExerciseBriefingView({
  data,
  isLoading = false,
  isError = false,
  exerciseName,
  onStart,
  exerciseCard: effectiveExerciseCard,
  weightUnit = 'kg',
  groupFatigue,
  onSwap,
  onSkip,
  nextExercise,
  substitution = null,
  goalBadge = null,
  hideTechnique = false,
}: SharedProps & { data?: ExerciseBriefingResponse; isLoading?: boolean; isError?: boolean }) {
  const [cardViewerOpened, { open: openCardViewer, close: closeCardViewer }] = useDisclosure(false);
  const [instructionsOpened, { open: openInstructions, close: closeInstructions }] = useDisclosure(false);

  const prescription = data?.prescription;
  const technique = hideTechnique ? null : prescription?.advanced_technique || null;
  const showExerciseContent = !substitution || substitution.sameExercise;
  const sections = showExerciseContent ? buildBriefingSections(parseExecutionDescription(data?.exercise.execution_description)) : [];
  const muscleGroups = showExerciseContent ? groupMusclesByRole(data?.muscles || []) : [];
  const exerciseCard =
    effectiveExerciseCard !== undefined ? effectiveExerciseCard : (data?.exercise.exercise_card ?? null);
  const displayName = substitution?.exerciseName || data?.exercise.name || exerciseName;
  const equipmentName = substitution ? substitution.equipmentName : (data?.equipment?.name ?? null);
  const photoUrl = substitution ? substitution.equipmentPhotoUrl : (data?.equipment?.photo_url ?? null);
  const badge = performanceBadge(data?.performance);
  const comparable = Boolean(data?.performance?.comparable);
  const adjustment = substitution ? null : data?.adjustment;
  const adjustmentValue = adjustmentLabel(adjustment);
  const recoveryText = recoveryCeilingText(adjustment?.effective_rpe_ceiling, adjustment?.recovery_signals);
  const lastExecution = substitution ? null : data?.last_execution;
  const lastSummary = summarizeLastExecution(lastExecution, weightUnit);
  // Cuidado de segurança: o 1º enunciado da Atenção fica SEMPRE na tela (1
  // linha); o texto íntegro está na gaveta.
  const attention = sections.find((section) => section.key === 'attention') ?? null;
  const hasInstructions = sections.length > 0 || muscleGroups.length > 0 || Boolean(exerciseCard);
  const instructionsSummary = [
    ...sections.map((section) => section.title),
    ...(muscleGroups.length > 0 ? ['Músculos'] : []),
  ].join(' · ');

  const metrics = substitution
    ? {
        setsReps: formatSetsAndReps({ sets_count: substitution.setsCount, reps_target: substitution.repsTarget }),
        load: formatExpectedLoad({ suggested_load_kg: null, load_target: substitution.loadTarget }, weightUnit),
        rpe: substitution.rpeTarget ? `RPE ${substitution.rpeTarget}` : '—',
      }
    : prescription
      ? {
          setsReps: formatSetsAndReps(prescription),
          load: formatExpectedLoad(prescription, weightUnit),
          rpe: formatExpectedRpe(prescription),
        }
      : null;
  const restSeconds = prescription?.rest_seconds;
  // Fix M97: faixa de força por %1RM, pronta do backend (só sem troca de par).
  const strengthLoadText = substitution ? null : formatStrengthLoadRange(prescription?.strength_load, weightUnit);
  const deloadBlock = substitution ? null : data?.deload;
  const deloadText = deloadBlock?.active ? deloadPrescriptionText(deloadBlock.prescription, weightUnit) : null;
  const deloadOrigin = deloadBlock?.active ? deloadLoadOrigin(deloadBlock.prescription, weightUnit) : null;

  const iconActions: WorkoutIconAction[] = [];
  if (onSwap) {
    iconActions.push({
      key: 'swap',
      label: 'Trocar exercício',
      caption: 'Trocar',
      icon: <IconArrowsExchange size={20} />,
      onClick: onSwap,
      testId: 'briefing-swap',
    });
  }
  if (onSkip) {
    iconActions.push({
      key: 'skip',
      label: 'Pular exercício',
      caption: 'Pular',
      icon: <IconPlayerTrackNext size={20} />,
      onClick: onSkip,
      testId: 'briefing-skip',
    });
  }

  const performanceChip =
    !data || substitution ? null : comparable ? (
      <StatusChip
        icon={<IconChartBar size={12} />}
        label={badge.label}
        color={badge.hex || BRAND.textMuted}
        testId="briefing-performance"
        size="sm"
      />
    ) : (
      <StatusChip
        icon={<IconChartBar size={12} />}
        label={NO_COMPARISON_LABEL}
        color={BRAND.textMuted}
        testId="briefing-no-comparison"
        size="sm"
      />
    );

  return (
    <div className={styles.screen} data-testid="exercise-briefing">
      {/* ── Título compacto ───────────────────────────────────────────── */}
      <ExerciseTitleRow
        name={displayName}
        equipmentName={equipmentName || (isLoading ? null : 'Equipamento não definido')}
        photoUrl={photoUrl}
        photoAlt={equipmentName || displayName}
        meta={
          <>
            {goalBadge}
            {data?.exercise.is_unilateral && showExerciseContent && (
              <StatusChip label="Unilateral" color={WORKOUT_MODE_TOKENS.orangeSoft} size="sm" />
            )}
            {groupFatigue !== undefined && <MuscleFatigueChip reading={groupFatigue} />}
          </>
        }
        testId="briefing-title"
      />

      {isLoading && (
        <Group gap="xs">
          <Loader size="xs" color="brand" />
          <Text size="xs" c={BRAND.textMuted}>
            Carregando o briefing deste exercício...
          </Text>
        </Group>
      )}

      {isError && (
        <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />} py={6}>
          <Text size="xs">
            Não foi possível carregar o briefing deste exercício. Você pode iniciar assim mesmo — a execução não
            depende desta tela.
          </Text>
        </Alert>
      )}

      {/* ── Prescrição: 4 métricas numa linha ─────────────────────────── */}
      {metrics && (
        <div className={styles.metricGrid} data-testid="briefing-metrics">
          <MetricTile label="Séries x reps" value={metrics.setsReps} />
          <MetricTile label="Carga" value={metrics.load} testId="briefing-load" />
          <MetricTile label="Esforço" value={metrics.rpe} />
          <MetricTile label="Descanso" value={restSeconds != null ? `${restSeconds}s` : '—'} />
        </div>
      )}
      {metrics && strengthLoadText && (
        <Text size="xs" c={BRAND.textMuted} data-testid="briefing-strength-load">
          Faixa de carga: {strengthLoadText}
        </Text>
      )}

      {/* M97 F3: sessão de deload — a prescrição de deload do item vem pronta
          do dono (`deload.prescription` do briefing). */}
      {deloadText && (
        <Alert color="cyan" variant="light" py={6} icon={<IconFeather size={16} />} data-testid="briefing-deload">
          <Text size="xs" fw={700}>
            Deload hoje: {deloadText}
          </Text>
          {deloadOrigin && (
            <Text size="xs" c={BRAND.textMuted}>
              {deloadOrigin}
            </Text>
          )}
        </Alert>
      )}

      {/* ── Explicação da recomendação ────────────────────────────────── */}
      {adjustmentValue &&
        (adjustment?.reason || recoveryText ? (
          <SectionAccordion
            variant="accent"
            layout="inline"
            icon={<IconAdjustments size={18} />}
            title="Ajuste de hoje"
            summary={<strong>{adjustmentValue}</strong>}
            testId="briefing-adjustment"
          >
            {adjustment?.reason && <p style={{ margin: 0 }}>{adjustment.reason}</p>}
            {/* RPE 10 Lote 6/P9: qual sinal da recuperação cortou o teto (texto do backend). */}
            {recoveryText && (
              <p style={{ margin: adjustment?.reason ? '6px 0 0' : 0 }} data-testid="briefing-recovery-signals">
                {recoveryText}
              </p>
            )}
          </SectionAccordion>
        ) : (
          <div className={`${styles.accordion} ${styles.accordionAccent}`} data-testid="briefing-adjustment">
            <div className={styles.accordionHeader} style={{ cursor: 'default' }}>
              <span className={styles.accordionIcon} aria-hidden="true">
                <IconAdjustments size={18} />
              </span>
              <span className={`${styles.accordionText} ${styles.accordionInline}`}>
                <span className={styles.accordionTitle}>Ajuste de hoje:</span>
                <span className={styles.accordionInlineSummary}>
                  <strong>{adjustmentValue}</strong>
                </span>
              </span>
            </div>
          </div>
        ))}

      {substitution && (
        <Alert color="gray" variant="light" icon={<IconArrowsExchange size={16} />} py={6} data-testid="briefing-substitution">
          <ClampText
            lines={2}
            text="Exercício trocado nesta sessão. Comparação, ajuste do motor e última execução são do item original da ficha e não aparecem aqui; as séries usam os alvos do exercício escolhido."
          />
        </Alert>
      )}

      {technique && (
        <Alert color="grape" variant="light" py={6} icon={<IconBolt size={16} />}>
          <Text size="xs" fw={700}>
            Técnica avançada: {getAdvancedTechniqueLabel(technique) || technique}
            {prescription?.advanced_technique_applications ? ` (${prescription.advanced_technique_applications}x)` : ''}
          </Text>
          <Text size="xs" c={BRAND.textMuted} lineClamp={2}>
            {getAdvancedTechniqueDescription(technique)}
          </Text>
        </Alert>
      )}

      {/* ── Como executar (gaveta) + Atenção em 1 linha ───────────────── */}
      {hasInstructions && (
        <section className={styles.howTo} data-testid="briefing-how-to">
          <button
            type="button"
            className={styles.howToButton}
            onClick={openInstructions}
            aria-haspopup="dialog"
            data-testid="briefing-instructions-open"
          >
            {exerciseCard ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                className={styles.howToThumb}
                src={exerciseCard.thumbnail_url}
                alt=""
                width={24}
                height={36}
                decoding="async"
              />
            ) : (
              <span className={styles.accordionIcon} aria-hidden="true">
                <IconClipboardList size={18} />
              </span>
            )}
            <span className={styles.accordionText}>
              <span className={styles.accordionTitle}>Como executar</span>
              {instructionsSummary && <span className={styles.accordionSummary}>{instructionsSummary}</span>}
            </span>
            <span className={styles.accordionChevron} aria-hidden="true">
              <IconChevronUp size={18} />
            </span>
          </button>
          {attention && (
            <div className={styles.attentionLine} role="note" data-testid="briefing-attention-line">
              <IconAlertTriangle size={16} aria-hidden="true" />
              <span className={styles.attentionText} title={attention.summary}>
                <span className={styles.srOnly}>Atenção: </span>
                {attention.summary}
              </span>
            </div>
          )}
        </section>
      )}

      {/* ── Como estamos + Treino anterior ────────────────────────────── */}
      {data && !substitution && (
        <div className={styles.rowGroup}>
          <SectionAccordion
            layout="inline"
            icon={<IconChartBar size={18} />}
            title="Como estamos"
            summary={performanceChip}
            testId="briefing-how-we-are"
          >
            <p style={{ margin: 0 }}>
              {performanceExplanation(data.performance)}
              {!lastExecution ? ' Primeira vez que você faz este exercício por aqui.' : ''}
            </p>
            {data.track?.phase && (
              <p style={{ margin: '4px 0 0', color: BRAND.textMuted }}>
                Fase da trilha: {data.track.phase}
                {data.track.reps_min != null && data.track.reps_max != null
                  ? ` · faixa ${data.track.reps_min}-${data.track.reps_max} reps`
                  : ''}
              </p>
            )}
          </SectionAccordion>

          {lastExecution && (
            <SectionAccordion
              layout="inline"
              icon={<IconHistory size={18} />}
              title="Treino anterior"
              summary={`${formatLocalDate(lastExecution.local_date) || 'Sessão anterior'} — ${lastSummary}`}
              testId="briefing-last-execution"
            >
              <ul className={styles.stepList}>
                {lastExecution.sets.map((set) => (
                  <li key={`${set.set_number}-${set.set_type}`}>{formatSetLine(set, weightUnit)}</li>
                ))}
              </ul>
            </SectionAccordion>
          )}
        </div>
      )}

      {nextExercise !== undefined && (
        <NextExerciseLine name={nextExercise?.name ?? null} muscle={nextExercise?.muscle} testId="briefing-next" />
      )}

      {/* ── Ações: CTA + Trocar / Pular na mesma linha ────────────────── */}
      <WorkoutActionBar
        primaryLabel="Iniciar exercício"
        primaryIcon={<IconPlayerPlay size={20} />}
        onPrimary={onStart}
        primaryTestId="briefing-start"
        iconActions={iconActions}
      />

      {hasInstructions && (
        <Drawer
          opened={instructionsOpened}
          onClose={closeInstructions}
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
          <div className={styles.root} style={WORKOUT_MODE_CSS_VARS}>
            <BriefingInstructions
              sections={sections}
              muscleGroups={muscleGroups}
              exerciseCard={exerciseCard}
              exerciseName={displayName}
              onOpenCard={() => {
                closeInstructions();
                openCardViewer();
              }}
            />
          </div>
        </Drawer>
      )}

      {exerciseCard && (
        <ExerciseCardViewer card={exerciseCard} opened={cardViewerOpened} onClose={closeCardViewer} exerciseName={displayName} />
      )}
    </div>
  );
}
