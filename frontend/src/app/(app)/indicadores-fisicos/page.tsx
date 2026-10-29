'use client';

import { Divider, Stack, Text, Title } from '@mantine/core';
import { BioimpedanciaSection } from './BioimpedanciaSection';
import { EffortMetricsSection } from './EffortMetricsSection';
import { MedidasFitaSection } from './MedidasFitaSection';
import { RecoveryMetricsSection } from './RecoveryMetricsSection';
import { SleepMetricsSection } from './SleepMetricsSection';
import { BRAND } from '@/theme';

export default function IndicadoresFisicosPage() {
  return (
    <Stack gap="xl">
      <div>
        <Title order={1} c={BRAND.textPrimary}>
          Indicadores Físicos
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Bioimpedância, medidas por fita, recuperação (Intervals.icu), sono e esforço estimado (EPOC/PAI de 7 dias).
        </Text>
      </div>

      <BioimpedanciaSection />

      <Divider color={BRAND.border} />

      <MedidasFitaSection />

      <Divider color={BRAND.border} />

      <RecoveryMetricsSection />

      <Divider color={BRAND.border} />

      <SleepMetricsSection />

      <Divider color={BRAND.border} />

      <EffortMetricsSection />
    </Stack>
  );
}
