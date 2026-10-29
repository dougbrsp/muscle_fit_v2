import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  buildMethodologyVolumeQueryString,
  buildSessionRows,
  describeIntentDeclaration,
  normalizeIntentDeclaration,
  sameIntentDeclaration,
} from '@/lib/methodology-volume';
import { FIXTURE_EVALUATION, FIXTURE_GROUP_WITHIN } from '@/lib/methodology-volume-fixtures';
import type { MethodologyIntentDeclaration } from '@/lib/methodology-volume-types';
import { theme } from '@/theme';
import { VolumeDiagnosticView, type VolumeDiagnosticViewProps } from './VolumeDiagnosticView';

/**
 * Render mínimo da tela de diagnóstico (Fase 4, PR G), no mesmo molde do PR F:
 * `renderToStaticMarkup` + `MantineProvider`, sem jsdom. Cobre a linha
 * expandida por sessão e a distinção "salvo no Modelo" × "simulação".
 */

const PERSISTED: MethodologyIntentDeclaration = { intent: 'specialization', target_group_ids: [4] };
const MUSCLE_GROUPS = [
  { value: '4', label: 'Quadríceps' },
  { value: '52', label: 'Peitoral' },
];

function render(overrides: Partial<VolumeDiagnosticViewProps> = {}): string {
  const noop = () => {};
  const props: VolumeDiagnosticViewProps = {
    evaluation: { ...FIXTURE_EVALUATION, template_id: 12, template_name: 'PPL 10 dias' },
    persistedIntent: PERSISTED,
    persistedIntentError: null,
    draft: PERSISTED,
    isSimulating: false,
    muscleGroupOptions: MUSCLE_GROUPS,
    onDraftChange: noop,
    onSave: noop,
    onDiscardSimulation: noop,
    isSaving: false,
    saveError: null,
    isRefreshing: false,
    refreshError: null,
    expandedKeys: new Set<number>(),
    onToggleGroup: noop,
    ...overrides,
  };
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(VolumeDiagnosticView, props)));
}

describe('VolumeDiagnosticView (SSR)', () => {
  it('cabeçalho: nome do Modelo, modo, ciclo e nota de normalização de frequência', () => {
    const html = render();
    expect(html).toContain('Diagnóstico de volume — PPL 10 dias');
    expect(html).toContain('Modo B (por equipamento)');
    expect(html).toContain('ciclo de 10 dias · 7 sessões de treino');
    expect(html).toContain('frequência e volume são contados em 7 dias a partir do primeiro slot de treino');
    // Resumo da tabela (mesmo texto do builder) e link de volta ao builder.
    expect(html).toContain('ciclo de 10 dias (7 sessões de treino)');
    expect(html).toContain('href="/modelos-treino/12"');
  });

  it('intenção salva: sem simulação, mostra o que está gravado e o botão de salvar desabilitado', () => {
    const html = render();
    expect(html).toContain('data-intent-mode="persisted"');
    expect(html).not.toContain('data-intent-mode="simulated"');
    expect(html).toContain('Salvo no Modelo:');
    expect(html).toContain('Especialização (alvo: Quadríceps)');
    expect(html).not.toContain('Simulando com:');
    const at = html.indexOf('Salvar intenção no Modelo');
    const start = html.lastIndexOf('<button', at);
    expect(html.slice(start, html.indexOf('>', start))).toContain('disabled');
  });

  it('intenção simulada: deixa claro o que está salvo e o que é "e se"', () => {
    const html = render({ draft: { intent: 'maintenance', target_group_ids: [] }, isSimulating: true });
    expect(html).toContain('data-intent-mode="simulated"');
    expect(html).toContain('Simulação — não salva no Modelo');
    // O salvo continua visível ao lado da simulação.
    expect(html).toContain('Especialização (alvo: Quadríceps)');
    expect(html).toContain('Simulando com:');
    expect(html).toContain('Manutenção');
    expect(html).toContain('Descartar simulação');
  });

  it('linha expandida mostra a contribuição por sessão com unidade no cabeçalho', () => {
    const collapsed = render();
    expect(collapsed).toContain('aria-label="Mostrar sessões de Peitoral"');
    expect(collapsed).not.toContain('data-session-detail="52"');

    const html = render({ expandedKeys: new Set([52]) });
    expect(html).toContain('aria-label="Ocultar sessões de Peitoral"');
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('data-session-detail="52"');
    expect(html).toContain('Sessão (slot do ciclo)');
    expect(html).toContain('Exercícios diretos na sessão (nº de exercícios)');
    expect(html).toContain('Séries diretas na sessão (nº de séries)');
    // Ordenado por sequência do slot (a fixture vem fora de ordem).
    expect(html.indexOf('Sessão 1 · Peito A')).toBeLessThan(html.indexOf('Sessão 4 · Peito B'));
    expect(html).toContain('6 exercícios diretos · 24 séries diretas no ciclo');
    // Só o bloco expandido abre.
    expect(html).not.toContain('data-session-detail="4"');
  });

  it('rodapé de qualidade de dado lista os itens com link para o domínio de exercícios', () => {
    const html = render();
    expect(html).toContain('Qualidade do dado');
    expect(html).toContain('data-quality-item="items_without_primary_contribution"');
    expect(html).toContain('2 itens sem contribuição PRIMARY cadastrada');
    expect(html).toContain('1 item sem exercício canônico');
    expect(html).toContain('href="/dominio-exercicios"');
    // Contadores zerados não aparecem.
    expect(html).not.toContain('data-quality-item="items_without_sets_count"');
  });

  it('cabeçalhos da tabela principal escrevem a unidade', () => {
    const html = render();
    expect(html).toContain('Exercícios por sessão (valor · faixa · origem)');
    expect(html).toContain('Séries diretas por sessão (valor · faixa · origem)');
    expect(html).toContain('Frequência semanal');
    // Fase 6: macro-grupo no lugar do porte, e o eixo semanal com meta.
    expect(html).toContain('Séries diretas por semana (valor · meta · origem)');
    expect(html).toContain('comparados com a faixa do macro-grupo × frequência');
    expect(html).not.toContain('porte ×');
  });
});

describe('helpers puros da intenção (PR G)', () => {
  it('normaliza: alvo só com especialização, sem repetição e ordenado', () => {
    expect(normalizeIntentDeclaration('specialization', ['52', 4, '4', 'x'])).toEqual({
      intent: 'specialization',
      target_group_ids: [4, 52],
    });
    expect(normalizeIntentDeclaration('maintenance', [4])).toEqual({ intent: 'maintenance', target_group_ids: [] });
  });

  it('compara declarações ignorando ordem e repetição de alvos', () => {
    expect(sameIntentDeclaration(PERSISTED, { intent: 'specialization', target_group_ids: [4, 4] })).toBe(true);
    expect(sameIntentDeclaration(PERSISTED, { intent: 'specialization', target_group_ids: [4, 52] })).toBe(false);
    expect(sameIntentDeclaration({ intent: 'standard', target_group_ids: [] }, { intent: 'cutting', target_group_ids: [] })).toBe(false);
  });

  it('query string: vazia ao seguir a persistida; repete target_group_ids no "e se"', () => {
    expect(buildMethodologyVolumeQueryString(null)).toBe('');
    expect(buildMethodologyVolumeQueryString({ intent: 'specialization', target_group_ids: [4, 52] })).toBe(
      '?intent=specialization&target_group_ids=4&target_group_ids=52',
    );
    expect(buildMethodologyVolumeQueryString({ intent: 'cutting', target_group_ids: [] })).toBe('?intent=cutting');
  });

  it('descreve a intenção com o nome do grupo (ou o id quando desconhecido)', () => {
    const names = new Map([[4, 'Quadríceps']]);
    expect(describeIntentDeclaration(PERSISTED, names)).toBe('Especialização (alvo: Quadríceps)');
    expect(describeIntentDeclaration({ intent: 'specialization', target_group_ids: [99] }, names)).toBe(
      'Especialização (alvo: grupo #99)',
    );
    expect(describeIntentDeclaration({ intent: 'standard', target_group_ids: [] }, names)).toBe('Padrão');
  });

  it('sessões: ordenadas por sequência, rótulo com ou sem nome de slot', () => {
    const rows = buildSessionRows(FIXTURE_GROUP_WITHIN.sessions);
    expect(rows.map((row) => row.label)).toEqual(['Sessão 1 · Peito A', 'Sessão 4 · Peito B']);
    expect(buildSessionRows([{ slot_sequence: 2, slot_name: '', direct_sets: 20, direct_exercises: 0 }])[0].label).toBe(
      'Sessão 2',
    );
  });
});
