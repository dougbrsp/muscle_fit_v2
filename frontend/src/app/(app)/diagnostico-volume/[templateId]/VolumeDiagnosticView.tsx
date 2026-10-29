'use client';

import { Alert, Anchor, Badge, Button, Group, MultiSelect, Paper, Select, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconArrowLeft, IconDeviceFloppy, IconInfoCircle, IconRotate } from '@tabler/icons-react';
import Link from 'next/link';
import { useMemo } from 'react';
import {
  METHODOLOGY_INTENT_OPTIONS,
  buildDataQualityItems,
  buildEvaluationSummary,
  buildMethodologyRows,
  describeIntentDeclaration,
  intentDeclarationValidationMessage,
  normalizeIntentDeclaration,
} from '@/lib/methodology-volume';
import type {
  CountingModeComparison,
  MethodologyIntentDeclaration,
  MethodologyIntentKind,
  MethodologyVolumeEvaluation,
} from '@/lib/methodology-volume-types';
import { countingModeOption } from '@/lib/volume-counting-mode';
import { BRAND } from '@/theme';
import { MethodologyVolumeTable } from '../../modelos-treino/MethodologyVolumeTable';
import { CountingModeComparisonSection } from './CountingModeComparisonSection';

/**
 * Tela de diagnóstico de volume por metodologia de um Modelo salvo (Fase 4,
 * PR G) — parte PURA: recebe a avaliação já carregada e o estado da intenção
 * (persistida × rascunho) e só desenha. Quem consulta a API e guarda estado é
 * `VolumeDiagnosticClient`; este componente é o que o teste de render cobre.
 *
 * Duas coisas ficam sempre explícitas na tela:
 * - o que está SALVO no Modelo (intenção persistida, PR H) e o que é SIMULAÇÃO
 *   local ("e se": recalcula via `intent=` na query, sem persistir);
 * - a unidade de cada número (cabeçalho de toda coluna numérica).
 */

const INTENT_SELECT_DATA = METHODOLOGY_INTENT_OPTIONS.map((option) => ({ value: option.value, label: option.label }));

function isIntentKind(value: string | null): value is MethodologyIntentKind {
  return METHODOLOGY_INTENT_OPTIONS.some((option) => option.value === value);
}

export type VolumeDiagnosticViewProps = {
  evaluation: MethodologyVolumeEvaluation;
  /** Intenção gravada no Modelo; null quando não foi possível ler (mensagem em `persistedIntentError`). */
  persistedIntent: MethodologyIntentDeclaration | null;
  persistedIntentError: string | null;
  /** Seleção atual da tela: igual à persistida quando não há "e se". */
  draft: MethodologyIntentDeclaration;
  /** `true` quando a tabela mostra uma intenção diferente da salva. */
  isSimulating: boolean;
  muscleGroupOptions: Array<{ value: string; label: string }>;
  onDraftChange: (draft: MethodologyIntentDeclaration) => void;
  onSave: () => void;
  onDiscardSimulation: () => void;
  isSaving: boolean;
  saveError: string | null;
  /** Recalculando com outra intenção (a tabela anterior continua visível). */
  isRefreshing: boolean;
  refreshError: string | null;
  expandedKeys: ReadonlySet<number>;
  onToggleGroup: (key: number) => void;
  /** Lote 3: o Modelo avaliado em cada modo de contagem (só Modo B). */
  comparison?: CountingModeComparison | null;
  comparisonPending?: boolean;
  comparisonError?: string | null;
};

export function VolumeDiagnosticView({
  evaluation,
  persistedIntent,
  persistedIntentError,
  draft,
  isSimulating,
  muscleGroupOptions,
  onDraftChange,
  onSave,
  onDiscardSimulation,
  isSaving,
  saveError,
  isRefreshing,
  refreshError,
  expandedKeys,
  onToggleGroup,
  comparison = null,
  comparisonPending = false,
  comparisonError = null,
}: VolumeDiagnosticViewProps) {
  const rows = useMemo(() => buildMethodologyRows(evaluation), [evaluation]);
  const qualityItems = useMemo(() => buildDataQualityItems(evaluation.data_quality), [evaluation]);
  const groupNameById = useMemo(
    () => new Map(muscleGroupOptions.map((option) => [Number(option.value), option.label] as const)),
    [muscleGroupOptions],
  );
  const templateId = evaluation.template_id;
  const templateName = evaluation.template_name ?? `Modelo #${templateId ?? '?'}`;
  const validationMessage = intentDeclarationValidationMessage(draft);
  const intentDescription = METHODOLOGY_INTENT_OPTIONS.find((option) => option.value === draft.intent)?.description;
  const modeText = evaluation.volume_mode === 'equipment_series' ? 'Modo B (por equipamento)' : 'Modo A (por grupo)';
  const isModeB = evaluation.volume_mode === 'equipment_series';
  // Lote 3: a régua e a unidade das séries são as do modo de contagem do Modelo (texto do motor).
  const counting = countingModeOption(evaluation.counting_mode);

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Anchor component={Link} href={templateId ? `/modelos-treino/${templateId}` : '/modelos-treino'} size="sm" c={BRAND.orange}>
            <IconArrowLeft size={14} style={{ verticalAlign: 'middle' }} />{' '}
            {templateId ? 'Abrir o Modelo no builder' : 'Voltar para modelos de treino'}
          </Anchor>
          <Title order={1} c={BRAND.textPrimary}>
            Diagnóstico de volume — {templateName}
          </Title>
          <Group gap="xs" mt={4} wrap="wrap">
            <Badge variant="light" color={evaluation.volume_mode === 'equipment_series' ? 'teal' : 'gray'}>
              {modeText}
            </Badge>
            {isModeB && (
              <Badge variant="light" color="brand" data-counting-mode={counting.value}>
                Contagem: {counting.label}
              </Badge>
            )}
            <Badge variant="light" color="gray">
              ciclo de {evaluation.cycle_days} {evaluation.cycle_days === 1 ? 'dia' : 'dias'} · {evaluation.training_sessions}{' '}
              {evaluation.training_sessions === 1 ? 'sessão' : 'sessões'} de treino
            </Badge>
          </Group>
          <Text c={BRAND.textMuted} size="sm" mt={6}>
            {counting.sets_unit_label.charAt(0).toUpperCase()}
            {counting.sets_unit_label.slice(1)} e exercícios por sessão de cada grupo/bloco, comparados com a faixa do
            macro-grupo × frequência, e {counting.sets_unit_label} por semana contra a meta semanal do macro-grupo. É
            outra unidade da ativação ponderada do builder.
            {isModeB ? ` ${counting.description}` : ''}
          </Text>
          <Text c={BRAND.textMuted} size="xs" mt={2} data-frequency-note>
            {evaluation.frequency_is_normalized
              ? `Ciclo de ${evaluation.cycle_days} dias: frequência e volume são contados em 7 dias a partir do primeiro slot de treino (o ciclo dá a volta se for mais curto; slots depois do 7º dia ficam de fora), porque as faixas da metodologia são semanais.`
              : 'Ciclo de 7 dias: a frequência no ciclo já é a frequência semanal (sem normalização).'}
          </Text>
        </div>
      </Group>

      {isModeB && (
        <CountingModeComparisonSection comparison={comparison} isPending={comparisonPending} error={comparisonError} />
      )}

      <Paper withBorder p="md" radius="md" style={{ background: BRAND.bgElevated }}>
        <Stack gap="sm">
          <Group justify="space-between" align="flex-start" wrap="wrap">
            <div>
              <Title order={3} c={BRAND.textPrimary}>
                Intenção declarada
              </Title>
              <Text size="sm" c={BRAND.textMuted}>
                A intenção não muda o status; só marca como esperado o desvio que ela prevê.
              </Text>
            </div>
            {isSimulating ? (
              <Badge color="orange" variant="filled" size="lg" data-intent-mode="simulated">
                Simulação — não salva no Modelo
              </Badge>
            ) : (
              <Badge color="gray" variant="light" size="lg" data-intent-mode="persisted">
                Lendo a intenção salva no Modelo
              </Badge>
            )}
          </Group>

          <Text size="sm" c={BRAND.textPrimary} data-persisted-intent>
            <b>Salvo no Modelo:</b>{' '}
            {persistedIntent ? (
              describeIntentDeclaration(persistedIntent, groupNameById)
            ) : (
              <Text component="span" size="sm" c={BRAND.textMuted}>
                não foi possível ler ({persistedIntentError ?? 'erro desconhecido'}); o servidor avalia com a intenção
                gravada ou, sem gravação, com &quot;Padrão&quot;.
              </Text>
            )}
          </Text>
          {isSimulating && (
            <Text size="sm" c={BRAND.textPrimary} data-simulated-intent>
              <b>Simulando com:</b> {describeIntentDeclaration(draft, groupNameById)} — a tabela abaixo usa esta intenção;
              nada foi gravado.
            </Text>
          )}

          <Group align="flex-end" gap="sm" wrap="wrap">
            <Select
              label="Intenção"
              description={intentDescription}
              data={INTENT_SELECT_DATA}
              value={draft.intent}
              onChange={(value) => {
                if (isIntentKind(value)) onDraftChange(normalizeIntentDeclaration(value, draft.target_group_ids));
              }}
              allowDeselect={false}
              size="xs"
              w={220}
            />
            {draft.intent === 'specialization' && (
              <MultiSelect
                label="Grupos-alvo da especialização"
                description="Fatia ou grupo-pai; o motor resolve para o bloco."
                data={muscleGroupOptions}
                value={draft.target_group_ids.map(String)}
                onChange={(values) => onDraftChange(normalizeIntentDeclaration('specialization', values))}
                searchable
                clearable
                size="xs"
                w={320}
                error={validationMessage ?? undefined}
                placeholder={draft.target_group_ids.length === 0 ? 'Selecione ao menos um grupo' : undefined}
              />
            )}
            <Button
              size="xs"
              color="brand"
              leftSection={<IconDeviceFloppy size={14} />}
              onClick={onSave}
              loading={isSaving}
              disabled={!isSimulating || !!validationMessage}
            >
              Salvar intenção no Modelo
            </Button>
            {isSimulating && (
              <Button size="xs" variant="subtle" color="gray" leftSection={<IconRotate size={14} />} onClick={onDiscardSimulation}>
                Descartar simulação
              </Button>
            )}
          </Group>
          {saveError && (
            <Text size="xs" c={BRAND.red}>
              <IconAlertTriangle size={12} style={{ verticalAlign: 'middle' }} /> {saveError}
            </Text>
          )}
          <Text size="xs" c={BRAND.textMuted}>
            <IconInfoCircle size={12} style={{ verticalAlign: 'middle' }} /> Trocar a intenção recalcula a tabela na hora
            (consulta com <code>intent=</code>), sem gravar. Só &quot;Salvar intenção no Modelo&quot; persiste — e passa a
            valer também para os avisos do builder.
          </Text>
        </Stack>
      </Paper>

      <Stack gap="xs">
        <Group justify="space-between" align="flex-end" wrap="wrap">
          <Title order={3} c={BRAND.textPrimary}>
            Por grupo / bloco
          </Title>
          <Text size="xs" c={BRAND.textMuted}>
            Expanda uma linha para ver o que cada sessão do ciclo contribui.
          </Text>
        </Group>
        {isRefreshing && (
          <Text size="xs" c={BRAND.textMuted}>
            Recalculando com a intenção selecionada… (mostrando a avaliação anterior)
          </Text>
        )}
        {refreshError && (
          <Text size="xs" c={BRAND.textMuted}>
            <IconAlertTriangle size={12} style={{ verticalAlign: 'middle' }} /> A avaliação mais recente falhou (
            {refreshError}); mostrando a última válida.
          </Text>
        )}
        <MethodologyVolumeTable
          rows={rows}
          summary={buildEvaluationSummary(evaluation)}
          expansion={{ expandedKeys, onToggle: onToggleGroup }}
          countingMode={evaluation.counting_mode}
        />
      </Stack>

      <Stack gap="xs" data-data-quality>
        <Title order={3} c={BRAND.textPrimary}>
          Qualidade do dado
        </Title>
        {qualityItems.length === 0 ? (
          <Text size="sm" c={BRAND.textMuted}>
            Nenhum item invisível para a metodologia: todos os itens têm mapeamento, exercício canônico, contribuição
            PRIMARY e número de séries.
          </Text>
        ) : (
          <Alert
            variant="light"
            color="yellow"
            icon={<IconAlertTriangle size={16} />}
            title="Itens que a metodologia não enxerga por completo"
          >
            <Stack gap={4}>
              {qualityItems.map((item) => (
                <Text key={item.key} size="xs" data-quality-item={item.key}>
                  {item.text}
                  {item.fixHref && item.fixLabel && (
                    <>
                      {' '}
                      <Anchor component={Link} href={item.fixHref} size="xs" fw={600} c={BRAND.orange}>
                        {item.fixLabel}
                      </Anchor>
                    </>
                  )}
                </Text>
              ))}
            </Stack>
          </Alert>
        )}
      </Stack>
    </Stack>
  );
}
