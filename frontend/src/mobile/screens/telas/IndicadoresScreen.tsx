'use client';

import { Select, Stack } from '@mantine/core';
import { useState, type ReactNode } from 'react';
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
  // Seção já visitada continua montada (só escondida): o que foi digitado nela não se perde ao trocar.
  const [visited, setVisited] = useState<ReadonlySet<Section>>(new Set<Section>(['bio']));
  const pane = (id: Section, node: ReactNode) =>
    visited.has(id) ? (
      <div key={id} hidden={section !== id}>
        {node}
      </div>
    ) : null;
  return (
    <MobileRecordsPresentation>
      <ScreenHeader title="Indicadores físicos" subtitle="Bioimpedância, medidas, recuperação, sono e esforço estimado" />
      <Stack gap="md">
        <Select
          size="md"
          label="Seção"
          data={SECTIONS as unknown as { value: string; label: string }[]}
          value={section}
          onChange={(v) => {
            const next = (v as Section) || 'bio';
            setSection(next);
            setVisited((current) => new Set([...current, next]));
          }}
          allowDeselect={false}
        />
        {pane('bio', <BioimpedanciaSection />)}
        {pane('fita', <MedidasFitaSection />)}
        {pane('recovery', <RecoveryMetricsSection />)}
        {pane('sleep', <SleepMetricsSection />)}
        {pane('effort', <EffortMetricsSection />)}
      </Stack>
    </MobileRecordsPresentation>
  );
}
