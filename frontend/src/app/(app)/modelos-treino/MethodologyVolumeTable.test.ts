import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { statusVisual } from '@/lib/dashboard-status';
import { buildEvaluationSummary, buildMethodologyRows } from '@/lib/methodology-volume';
import { FIXTURE_EVALUATION } from '@/lib/methodology-volume-fixtures';
import { theme } from '@/theme';
import { MethodologyVolumeTable } from './MethodologyVolumeTable';

/**
 * Teste de render do painel de metodologia (Fase 4, PR F). O repo não tem
 * jsdom nem testing-library: renderiza pelo `react-dom/server` (o mesmo
 * caminho do SSR do Next) e afirma sobre o HTML — rótulo + ícone + valor +
 * `aria-label`, faixa derivada marcada, `NOT_DIRECTLY_TRAINED` sem a cor de
 * alerta. Sem interação (tooltips só existem ao abrir).
 */

/** Tag de abertura do elemento que carrega `marker` (do último `<` antes até o `>` depois). */
function openingTagAround(markup: string, marker: string): string {
  const at = markup.indexOf(marker);
  expect(at, marker).toBeGreaterThan(-1);
  const start = markup.lastIndexOf('<', at);
  const end = markup.indexOf('>', at);
  return markup.slice(start, end + 1);
}

function render(): string {
  return renderToStaticMarkup(
    createElement(
      MantineProvider,
      { theme },
      createElement(MethodologyVolumeTable, {
        rows: buildMethodologyRows(FIXTURE_EVALUATION),
        summary: buildEvaluationSummary(FIXTURE_EVALUATION),
      }),
    ),
  );
}

describe('MethodologyVolumeTable (SSR)', () => {
  const html = render();

  it('escreve a unidade nos cabeçalhos e o resumo do ciclo', () => {
    expect(html).toContain('Exercícios por sessão (valor · faixa · origem)');
    expect(html).toContain('Séries diretas por sessão (valor · faixa · origem)');
    expect(html).toContain('ciclo de 10 dias (7 sessões de treino)');
  });

  it('status sai com rótulo, ícone e aria-label (cor nunca é o único sinal)', () => {
    expect(html).toContain('Na faixa');
    expect(html).toContain('data-status="WITHIN_RANGE"');
    expect(html).toContain('aria-label="séries diretas por sessão: Na faixa; 12 séries diretas por sessão; faixa 9–16"');
    // Ícone Tabler renderizado dentro do badge (svg com a classe da lib).
    expect(html).toContain('tabler-icon-circle-check');
    expect(html).toContain('tabler-icon-trending-down');
  });

  it('faixa derivada fica visivelmente marcada', () => {
    expect(html).toContain('data-range-source="derived"');
    expect(html).toContain('(derivada)');
    expect(html).toContain('faixa 1–2');
    // A faixa do autor é marcada como metodologia, não como derivada.
    expect(html).toContain('data-range-source="methodology"');
    expect(html).toContain('(metodologia)');
  });

  it('Fase 6: coluna Macro-grupo, eixo semanal com status e origem admin, status combinado do grupo', () => {
    expect(html).toContain('>Macro-grupo<');
    expect(html).not.toContain('>Porte<');
    expect(html).toContain('Grandes');
    expect(html).toContain('Pequenos');
    expect(html).toContain('Séries diretas por semana (valor · meta · origem)');
    expect(html).toContain('aria-label="séries diretas por semana: Na faixa; 16,8 séries diretas por semana; meta 12,5–20"');
    expect(html).toContain('meta 12,5–20');
    expect(html).toContain('data-range-source="admin"');
    expect(html).toContain('(admin)');
    expect(html).toContain('data-overall-status="WITHIN_RANGE"');
    expect(html).toContain('data-overall-status="NOT_DIRECTLY_TRAINED"');
    expect(html).not.toContain('projeção derivada');
  });

  it('frequência normalizada de ciclo de N dias é escrita ao lado do valor', () => {
    expect(html).toContain('3,5x/sem');
    expect(html).toContain('contada em 7 dias a partir do 1º treino (ciclo de 10 dias)');
  });

  it('paleta unificada (revista 2026-10-02): BELOW_RANGE laranja (não vermelho), WITHIN verde, ABOVE laranja', () => {
    const neutral = statusVisual('methodologyVolume', 'NOT_DIRECTLY_TRAINED');
    const below = statusVisual('methodologyVolume', 'BELOW_RANGE');
    expect(html).toContain('Sem trabalho direto');
    expect(html).toContain('data-status="NOT_DIRECTLY_TRAINED"');
    // Mantine grava a cor do Badge em `--badge-color`/`--badge-bg`. Abaixo da
    // faixa é laranja (2026-10-02), nunca vermelho; a distinção do neutro
    // "sem trabalho direto" é rótulo + ícone (trending-down × minus).
    const neutralBadge = openingTagAround(html, 'data-status="NOT_DIRECTLY_TRAINED"');
    expect(neutralBadge).toContain(`--mantine-color-${neutral.color}-`);
    const belowBadge = openingTagAround(html, 'data-status="BELOW_RANGE"');
    expect(belowBadge).toContain(`--mantine-color-${below.color}-`);
    expect(belowBadge).toContain('--mantine-color-orange-');
    expect(belowBadge).not.toContain('--mantine-color-red-');
    expect(html).toContain('tabler-icon-minus');
    expect(openingTagAround(html, 'data-status="WITHIN_RANGE"')).toContain('--mantine-color-green-');
    expect(openingTagAround(html, 'data-status="ABOVE_RANGE"')).toContain('--mantine-color-orange-');
  });

  it('NOT_EVALUABLE mostra a reason traduzida e o grupo-alvo é marcado', () => {
    expect(html).toContain('Modo por grupo não define exercícios');
    expect(html).toContain('Não avaliável');
    expect(html).toContain('aria-label="Grupo-alvo da intenção declarada"');
    expect(html).toContain('Acima da faixa (esperado)');
    expect(html).toContain('soma de 2 fatias');
  });
});
