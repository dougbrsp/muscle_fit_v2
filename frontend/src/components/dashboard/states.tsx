'use client';

import { Alert, Paper, Skeleton, Stack, Text } from '@mantine/core';
import {
  IconAlertTriangle,
  IconInfoCircle,
  IconMoodEmpty,
  IconShieldCheck,
} from '@tabler/icons-react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import type { DataQuality } from '@/lib/dashboard-v3-types';
import { BRAND } from '@/theme';
import { statusVisual } from '@/lib/dashboard-status';

/**
 * Os quatro estados não-felizes de um bloco do dashboard (plano §11):
 * carregando, vazio, erro e PARCIAL (dado existe mas a qualidade é degradada
 * ou uma regra não pôde ser avaliada).
 *
 * Antes da F0.4 o hub inteiro usava um `<Loader size="sm" />` de 24px como
 * único estado de carregamento — o layout colapsava e voltava a crescer a cada
 * navegação. O skeleton reserva a altura real do bloco.
 */

/**
 * Esqueleto com a forma do conteúdo que vai chegar. `rows` deve aproximar a
 * altura final do bloco para o layout não saltar quando os dados chegarem.
 */
export function DashboardSkeleton({
  rows = 5,
  height = 28,
  withHeader = true,
  'aria-label': ariaLabel = 'Carregando dados',
}: {
  rows?: number;
  height?: number;
  withHeader?: boolean;
  'aria-label'?: string;
}) {
  return (
    <Stack gap="xs" aria-busy role="status" aria-label={ariaLabel}>
      {withHeader && <Skeleton height={height} width="40%" radius="sm" />}
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} height={height} radius="sm" />
      ))}
    </Stack>
  );
}

/** Skeleton de um cartão inteiro (borda + fundo do hub). */
export function DashboardCardSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <DashboardSkeleton rows={rows} />
    </Paper>
  );
}

/**
 * "Não há o que mostrar" — e o que fazer a respeito. Estado VAZIO nunca é o
 * mesmo que erro: aqui a consulta funcionou e a resposta foi zero linhas.
 */
export function EmptyState({
  title = 'Sem dados no período selecionado',
  description,
  actionHref,
  actionLabel,
  children,
}: {
  title?: string;
  description?: ReactNode;
  actionHref?: string;
  actionLabel?: string;
  children?: ReactNode;
}) {
  return (
    <Alert color="gray" variant="light" icon={<IconMoodEmpty size={18} />} title={title}>
      {description && (
        <Text size="sm" c={BRAND.textMuted}>
          {description}
        </Text>
      )}
      {children}
      {actionHref && actionLabel && (
        <Text component={Link} href={actionHref} size="sm" fw={600} c={BRAND.orange} mt={4} span>
          {actionLabel}
        </Text>
      )}
    </Alert>
  );
}

/** Falha de consulta. Mostra a mensagem real quando houver uma utilizável. */
export function ErrorState({
  title = 'Não foi possível carregar este bloco',
  error,
  description,
}: {
  title?: string;
  error?: unknown;
  description?: ReactNode;
}) {
  const detail =
    description ?? (error instanceof Error && error.message ? error.message : 'Tente novamente em instantes.');
  return (
    <Alert color="red" variant="light" icon={<IconAlertTriangle size={18} />} title={title}>
      <Text size="sm">{detail}</Text>
    </Alert>
  );
}

/**
 * Estado PARCIAL: veio resposta, mas a confiança está degradada
 * (`data_quality` ≠ `HIGH`) ou alguma regra não pôde ser avaliada
 * (`limitations[]`). Não é erro nem vazio — é "leia com ressalva", e a
 * ressalva vem escrita do backend.
 */
export function PartialState({
  quality,
  limitations = [],
  title = 'Leitura parcial',
  fixHref = '/dashboard/data-quality',
  fixLabel = 'Ver qualidade dos dados',
}: {
  quality?: DataQuality | null;
  limitations?: string[];
  title?: string;
  fixHref?: string | null;
  fixLabel?: string;
}) {
  const degraded = quality != null && quality !== 'HIGH';
  if (!degraded && limitations.length === 0) return null;

  const visual = quality ? statusVisual('dataQuality', quality) : null;

  return (
    <Alert
      color={visual?.color ?? 'gray'}
      variant="light"
      icon={<IconInfoCircle size={18} />}
      title={visual ? `${title} · confiança ${visual.label.toLowerCase()}` : title}
    >
      <Stack gap={2}>
        {limitations.map((limitation) => (
          <Text key={limitation} size="sm">
            {limitation}
          </Text>
        ))}
        {fixHref && (
          <Text component={Link} href={fixHref} size="sm" fw={600} c={BRAND.orange} span>
            {fixLabel}
          </Text>
        )}
      </Stack>
    </Alert>
  );
}

/** Confirmação positiva ("nada a corrigir"), par do `PartialState`. */
export function AllClearState({ children }: { children: ReactNode }) {
  return (
    <Alert color="green" variant="light" icon={<IconShieldCheck size={18} />}>
      {children}
    </Alert>
  );
}
