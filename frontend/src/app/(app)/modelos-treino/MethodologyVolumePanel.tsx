'use client';

import { Alert, Group, Loader, MultiSelect, Select, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react';
import Link from 'next/link';
import { memo, useMemo } from 'react';
import { ApiError } from '@/lib/api';
import {
  METHODOLOGY_INTENT_OPTIONS,
  buildDataQualityItems,
  buildEvaluationSummary,
  buildMethodologyRows,
} from '@/lib/methodology-volume';
import type { MethodologyIntentKind } from '@/lib/methodology-volume-types';
import { BRAND } from '@/theme';
import { MethodologyVolumeTable } from './MethodologyVolumeTable';
import type { useMethodologyVolumeQuery } from './useMethodologyVolumeQuery';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

const INTENT_SELECT_DATA = METHODOLOGY_INTENT_OPTIONS.map((option) => ({
  value: option.value,
  label: option.label,
}));

function isIntentKind(value: string | null): value is MethodologyIntentKind {
  return METHODOLOGY_INTENT_OPTIONS.some((option) => option.value === value);
}

type Props = {
  /** Resultado de `useMethodologyVolumeQuery`, chamado por quem monta o builder (M43: compartilhado com os cards). */
  query: ReturnType<typeof useMethodologyVolumeQuery>['query'];
  hasAnyFilledSlot: boolean;
  intent: MethodologyIntentKind;
  onIntentChange: (intent: MethodologyIntentKind) => void;
  /** Ids como string (valor do MultiSelect). */
  targetGroupIds: string[];
  onTargetGroupIdsChange: (ids: string[]) => void;
  /** Grupos musculares do catálogo (mesma lista do builder) para o alvo da especialização. */
  muscleGroupOptions: Array<{ value: string; label: string }>;
};

/**
 * Painel de metodologia de volume por grupo no builder (Fase 4, PR F) —
 * SEGUNDA leitura do Modelo, em OUTRA unidade da ativação ponderada ao lado:
 * séries DIRETAS e exercícios por sessão, comparados com a faixa do
 * macro-grupo × frequência, e séries por semana contra a meta semanal do
 * macro-grupo (Fase 6). Só lê: nada aqui altera o Modelo, e a
 * intenção declarada é parâmetro da consulta (persistência é o PR H).
 *
 * Desde a M43 o painel é CONTROLADO: a intenção e a query moram no builder,
 * porque a mesma avaliação alimenta a linha de séries diretas de cada card
 * (uma requisição só, mesma queryKey).
 *
 * O painel nunca bloqueia o builder: sem slot preenchido mostra um texto,
 * erro de rede vira texto (não modal), e o resultado anterior fica na tela
 * enquanto o novo carrega.
 */
function MethodologyVolumePanelImpl({
  query,
  hasAnyFilledSlot,
  intent,
  onIntentChange,
  targetGroupIds,
  onTargetGroupIdsChange,
  muscleGroupOptions,
}: Props) {
  const rows = useMemo(() => (query.data ? buildMethodologyRows(query.data) : []), [query.data]);
  const qualityItems = useMemo(() => buildDataQualityItems(query.data?.data_quality), [query.data]);
  const intentDescription = METHODOLOGY_INTENT_OPTIONS.find((option) => option.value === intent)?.description;

  return (
    <Stack gap="sm">
      <Group align="flex-end" gap="sm" wrap="wrap">
        <Select
          label="Intenção declarada"
          description={intentDescription}
          data={INTENT_SELECT_DATA}
          value={intent}
          onChange={(value) => {
            if (isIntentKind(value)) onIntentChange(value);
          }}
          allowDeselect={false}
          size="xs"
          w={220}
        />
        {intent === 'specialization' && (
          <MultiSelect
            label="Grupos-alvo da especialização"
            description="Fatia ou grupo-pai; o motor resolve para o bloco."
            data={muscleGroupOptions}
            value={targetGroupIds}
            onChange={onTargetGroupIdsChange}
            searchable
            clearable
            size="xs"
            w={320}
            placeholder={targetGroupIds.length === 0 ? 'Selecione ao menos um grupo' : undefined}
          />
        )}
        <Text size="xs" c={BRAND.textMuted}>
          <IconInfoCircle size={12} style={{ verticalAlign: 'middle' }} /> A intenção não muda o status; só marca
          como esperado o desvio que ela prevê. Nada é salvo (parâmetro da consulta).
        </Text>
      </Group>

      {!hasAnyFilledSlot && (
        <Text c={BRAND.textMuted} size="sm">
          Preencha ao menos um slot para ver a leitura da metodologia.
        </Text>
      )}

      {hasAnyFilledSlot && query.isPending && (
        <Stack align="center" py="md">
          <Loader size="sm" color="brand" />
        </Stack>
      )}

      {hasAnyFilledSlot && query.isError && !query.data && (
        <Text size="sm" c={BRAND.textMuted}>
          <IconAlertTriangle size={14} style={{ verticalAlign: 'middle' }} />{' '}
          {errorMessage(query.error, 'Não foi possível avaliar o Modelo pela metodologia agora.')} O builder segue
          normalmente; a leitura volta na próxima alteração.
        </Text>
      )}

      {hasAnyFilledSlot && query.data && (
        <>
          {query.isError && (
            <Text size="xs" c={BRAND.textMuted}>
              Mostrando a última avaliação válida — a mais recente falhou (
              {errorMessage(query.error, 'erro de rede')}).
            </Text>
          )}
          <MethodologyVolumeTable
            rows={rows}
            summary={buildEvaluationSummary(query.data)}
            countingMode={query.data.counting_mode}
          />
          {qualityItems.length > 0 && (
            <Alert
              variant="light"
              color="yellow"
              icon={<IconAlertTriangle size={16} />}
              title="Qualidade do dado: itens que a metodologia não enxerga por completo"
            >
              <Stack gap={4}>
                {qualityItems.map((item) => (
                  <Text key={item.key} size="xs">
                    {item.text}
                    {item.fixHref && item.fixLabel && (
                      <>
                        {' '}
                        <Text component={Link} href={item.fixHref} size="xs" fw={600} c={BRAND.orange} span>
                          {item.fixLabel}
                        </Text>
                      </>
                    )}
                  </Text>
                ))}
              </Stack>
            </Alert>
          )}
        </>
      )}
    </Stack>
  );
}

export const MethodologyVolumePanel = memo(MethodologyVolumePanelImpl);
