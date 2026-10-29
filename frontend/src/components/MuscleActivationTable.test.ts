import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { MuscleActivationAssessment, MuscleActivationRow } from '@/lib/analytics-types';
import { plannedSeverityVisual, statusVisual } from '@/lib/dashboard-status';
import { theme } from '@/theme';
import { MuscleActivationTable } from './MuscleActivationTable';

/**
 * Tabela "Ativação muscular" de `/registro-exercicios/[activityId]`: só desenha
 * o `assessment` do backend. "Muito abaixo do planejado" (2026-10-07) vem como
 * `severity = 'FAR_BELOW'` — o componente não tem limiar nenhum.
 */

function row(group: string, total: number, assessment: MuscleActivationAssessment): MuscleActivationRow {
  return {
    muscle_group: group,
    primary_series: 2,
    primary_equivalent: total,
    secondary_series: 0,
    secondary_equivalent: 0,
    stabilizer_series: 0,
    stabilizer_equivalent: 0,
    total_equivalent: total,
    assessment,
  } as MuscleActivationRow;
}

function slot(status: string, severity: MuscleActivationAssessment['severity']): MuscleActivationAssessment {
  return {
    status,
    status_domain: 'plannedVolume',
    target_source: 'template_slot',
    target_sets: 10,
    max_sets: null,
    severity,
  };
}

function render(rows: MuscleActivationRow[]): string {
  return renderToStaticMarkup(
    createElement(MantineProvider, { theme }, createElement(MuscleActivationTable, { rows })),
  );
}

describe('plannedSeverityVisual', () => {
  it('FAR_BELOW é vermelho com o rótulo "Muito abaixo do planejado"', () => {
    const visual = plannedSeverityVisual('FAR_BELOW');
    expect(visual?.label).toBe('Muito abaixo do planejado');
    expect(visual?.color).toBe('red');
  });

  it('sem gravidade (ou desconhecida) devolve null e o status normal vale', () => {
    expect(plannedSeverityVisual(null)).toBeNull();
    expect(plannedSeverityVisual(undefined)).toBeNull();
    expect(plannedSeverityVisual('OUTRA')).toBeNull();
  });
});

describe('MuscleActivationTable', () => {
  it('pinta "Muito abaixo do planejado" quando o backend manda a gravidade', () => {
    const html = render([row('Peitoral', 4.9, slot('BELOW_TARGET', 'FAR_BELOW'))]);
    expect(html).toContain('aria-label="Muito abaixo do planejado"');
    expect(html).toContain('data-severity="FAR_BELOW"');
    expect(html).toContain('data-status="BELOW_TARGET"');
  });

  it('abaixo sem gravidade é amarelo "Abaixo do planejado" (o FE não recalcula a razão)', () => {
    // 1 de 10 sem gravidade: o FE não decide sozinho que é "muito abaixo".
    const html = render([row('Peitoral', 1, slot('BELOW_TARGET', null))]);
    expect(html).toContain(`aria-label="${statusVisual('plannedVolume', 'BELOW_TARGET').label}"`);
    expect(html).toContain('--mantine-color-yellow');
    expect(html).not.toContain('--mantine-color-red');
    expect(html).not.toContain('Muito abaixo');
    expect(html).not.toContain('data-severity');
  });

  it('muito abaixo é vermelho', () => {
    const html = render([row('Lombar', 1.2, slot('BELOW_TARGET', 'FAR_BELOW'))]);
    expect(html).toContain('--mantine-color-red');
    expect(html).not.toContain('--mantine-color-yellow');
  });

  it('a cor amarela é só desta tabela: o visual compartilhado de BELOW_TARGET continua vermelho', () => {
    expect(statusVisual('plannedVolume', 'BELOW_TARGET').color).toBe('red');
  });

  it('BELOW_MIN da meta do tipo de treino continua vermelho', () => {
    const html = render([
      row('Peitoral', 1, {
        status: 'BELOW_MIN',
        status_domain: 'weeklyVolume',
        target_source: 'training_type',
        target_sets: 12,
        max_sets: 18,
        severity: null,
      }),
    ]);
    expect(html).toContain('--mantine-color-red');
  });

  it('payload antigo sem o campo severity continua funcionando', () => {
    const { severity: _omit, ...semCampo } = slot('ON_TARGET', null);
    const html = render([row('Costas', 10, semCampo as MuscleActivationAssessment)]);
    expect(html).toContain('aria-label="No planejado"');
  });
});
