'use client';

import { Select, Stack } from '@mantine/core';
import { useState } from 'react';
import { BioimpedanciaSection } from '@/app/(app)/indicadores-fisicos/BioimpedanciaSection';
import { EffortMetricsSection } from '@/app/(app)/indicadores-fisicos/EffortMetricsSection';
import { MedidasFitaSection } from '@/app/(app)/indicadores-fisicos/MedidasFitaSection';
import { RecoveryMetricsSection } from '@/app/(app)/indicadores-fisicos/RecoveryMetricsSection';
import { SleepMetricsSection } from '@/app/(app)/indicadores-fisicos/SleepMetricsSection';
import { ScreenHeader } from '../../ui';
import { MobileRecordsPresentation } from './MobileRecordsPresentation';

const SECTIONS = [
  { value: 'bio', label: 'Bioimpedância' },
  { value: 'fita', label: 'Medidas por fita' },
  { value: 'recovery', label: 'Recuperação (Intervals.icu)' },
  { value: 'sleep', label: 'Sono' },
  { value: 'effort', label: 'Esforço (EPOC / PAI)' },
] as const;

type Section = (typeof SECTIONS)[number]['value'];

/**
 * Indicadores físicos (celular): uma seção por vez (escolhida no seletor), cada
 * uma com as mesmas queries, formulários e mutações do desktop; as tabelas viram lista.
 */
export function IndicadoresScreen() {
  const [section, setSection] = useState<Section>('bio');
  return (
    <MobileRecordsPresentation>
      <ScreenHeader title="Indicadores físicos" subtitle="Bioimpedância, medidas, recuperação, sono e esforço estimado" />
      <Stack gap="md">
        <Select
          size="md"
          label="Seção"
          data={SECTIONS as unknown as { value: string; label: string }[]}
          value={section}
          onChange={(v) => setSection((v as Section) || 'bio')}
          allowDeselect={false}
        />
        {section === 'bio' ? <BioimpedanciaSection /> : null}
        {section === 'fita' ? <MedidasFitaSection /> : null}
        {section === 'recovery' ? <RecoveryMetricsSection /> : null}
        {section === 'sleep' ? <SleepMetricsSection /> : null}
        {section === 'effort' ? <EffortMetricsSection /> : null}
      </Stack>
    </MobileRecordsPresentation>
  );
}
