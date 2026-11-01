import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ExerciseProgressionSummary } from '@/lib/dashboard-v2-types';
import { theme } from '@/theme';
import { ExerciseProgressionSummaryCard } from './ExerciseProgressionSummaryCard';

const SUMMARY: ExerciseProgressionSummary = {
  window_days: 30,
  period_start: '2026-10-02',
  period_end: '2026-11-01',
  counts: {
    progressing: 18,
    stable: 7,
    plateau_suspected: 4,
    regressing: 9,
    comparable_total: 40,
    not_comparable: 2,
    insufficient_data: 5,
  },
  answer: '18 de 40 exercícios comparáveis progredindo; 4 com suspeita de platô e 9 regredindo.',
};

function render(summary: ExerciseProgressionSummary | null | undefined) {
  // Tira os <style> injetados pelo MantineProvider: sobra só o markup do card.
  return renderToStaticMarkup(
    createElement(MantineProvider, { theme }, createElement(ExerciseProgressionSummaryCard, { summary })),
  ).replace(/<style[\s\S]*?<\/style>/g, '');
}

describe('ExerciseProgressionSummaryCard', () => {
  it('mostra a frase e as contagens exatamente como o backend enviou', () => {
    const html = render(SUMMARY);
    expect(html).toContain(SUMMARY.answer);
    expect(html).toMatch(/Progredindo[^<]*18/);
    expect(html).toMatch(/Estável[^<]*7/);
    expect(html).toMatch(/Suspeita de platô[^<]*4/);
    expect(html).toMatch(/Regredindo[^<]*9/);
    expect(html).toContain('40 exercícios comparáveis');
  });

  it('não conta nada por conta própria: números vêm só do payload', () => {
    const html = render({ ...SUMMARY, counts: { ...SUMMARY.counts, progressing: 123, comparable_total: 456 } });
    expect(html).toMatch(/Progredindo[^<]*123/);
    expect(html).toContain('456 exercícios comparáveis');
  });

  it('avisa leitura parcial só quando truncated=true', () => {
    expect(render({ ...SUMMARY, truncated: true })).toContain('Leitura parcial: janela muito longa; considere uma janela menor');
    expect(render({ ...SUMMARY, truncated: false })).not.toContain('Leitura parcial');
    expect(render(SUMMARY)).not.toContain('Leitura parcial');
  });

  it('sem o campo, não renderiza nada nem inventa contagem', () => {
    expect(render(undefined)).toBe('');
    expect(render(null)).toBe('');
  });
});

describe('Painel por grupo e home: sem contagem própria de progressão', () => {
  const lerFonte = (relativo: string) => readFileSync(resolve(__dirname, relativo), 'utf8');

  it('o card e o painel não filtram/contam status no cliente', () => {
    const card = lerFonte('./ExerciseProgressionSummaryCard.tsx');
    const painel = lerFonte('./MuscleGroupProgressionPanel.tsx');
    expect(card).not.toMatch(/\.filter\(|\.reduce\(|\.length/);
    expect(painel).not.toMatch(/performance_status\s*===/);
    expect(painel).not.toContain('PerformanceSummary');
    expect(painel).toMatch(/<ExerciseProgressionSummaryCard summary=\{data\?\.degraded \? null : data\?\.exercise_progression\}/);
  });
});
