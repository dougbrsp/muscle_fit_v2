import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Checkin } from '@/lib/nutrition-checkin';
import { theme } from '@/theme';
import { CheckinReview } from './CheckinReview';

// Tela de revisão do check-in (N7) por SSR (sem jsdom): só o que a API devolve, nenhum cálculo no frontend.
function render(checkin: Checkin, extra: Record<string, unknown> = {}): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(CheckinReview, { checkin, ...extra })));
}

const MACROS = { kcal: 2400, protein_g: 190, carb_g: 260, fat_g: 70 };

function checkin(extra: Partial<Checkin> = {}): Checkin {
  return {
    id: 7,
    user_id: 2,
    week_start: '2026-10-26',
    week_end: '2026-11-01',
    due_date: '2026-11-02',
    status: 'proposed',
    status_label: 'Aguardando sua resposta',
    verdict: 'above_band',
    verdict_label: 'Peso acima da banda de manutenção',
    can_respond: true,
    show_card: true,
    expires_at: null,
    expires_on: '2026-11-05',
    adjustment_kcal: -120,
    missing_items: [],
    reason_items: [{ code: 'checkin_bia_short_trend', label: 'A bioimpedância tem menos de 2 semanas de tendência.' }],
    proposal: {
      before: MACROS,
      after: { ...MACROS, kcal: 2280, carb_g: 230 },
      delta_kcal: -120,
      changes: {},
      change_items: [{ field: 'maintenance_kcal_adjust', label: 'Ajuste da manutenção (kcal por dia)', unit: 'kcal', before: 0, after: -120 }],
      scope: 'cycle_average',
      scope_label: 'Média dos 7 dias do ciclo',
    },
    engine_version: 'nutrition-v1',
    source_plan_id: 1,
    generated_plan_id: null,
    responded_at: null,
    created_at: null,
    summary: { complete_days: 6, mean_tdee_kcal: 2800, lean_trend: { weekly_change: null, readings: 1, span_days: 0, mean: null, valid_for_bia: false } },
    goal: null,
    ...extra,
  };
}

describe('CheckinReview', () => {
  it('a barra de ações sobe acima da navegação do celular e da área segura e empilha os botões', () => {
    const html = render(checkin());
    expect(html).toContain('bottom:calc(var(--m-bottom-nav, 0px) + env(safe-area-inset-bottom))');
    expect(html).toContain('position:sticky');
    expect(html).toContain('Aceitar as novas metas');
    expect(html).toContain('Manter as atuais');
    expect(html).not.toContain('mantine-Group-root mantine-Group-grow'); // sem os dois botões lado a lado
    expect((html.match(/data-block="true"/g) ?? []).length).toBeGreaterThanOrEqual(2); // fullWidth nos dois
  });

  it('o cabeçalho da tabela diz o que as metas representam (média do ciclo) e o ajuste da manutenção sai em kcal', () => {
    const html = render(checkin());
    expect(html).toContain('Média dos 7 dias do ciclo');
    expect(html).toContain('Ajuste da manutenção (kcal por dia)');
    expect(html).toContain('0 kcal → -120 kcal');
    expect(html).toContain('-120 kcal por dia');
    expect(render(checkin({ proposal: { ...checkin().proposal!, scope: undefined, scope_label: undefined } }))).toContain('Dia sem treino');
  });

  it('a tendência que falta não fixa "2 semanas" na tela: o motivo vem da API', () => {
    const html = render(checkin());
    expect(html).toContain('sem tendência (veja os motivos)');
    expect(html).not.toContain('sem tendência de 2 semanas');
    expect(html).toContain('A bioimpedância tem menos de 2 semanas de tendência.'); // texto da API, com o número do motor
  });

  it('proposta já respondida some com a barra e mostra a situação', () => {
    const html = render(checkin({ status: 'accepted', status_label: 'Aceito', can_respond: false }));
    expect(html).toContain('Aceito');
    expect(html).not.toContain('Aceitar as novas metas');
    expect(html).not.toContain('--m-bottom-nav');
  });

  it('erro da resposta aparece como alerta acessível', () => {
    const html = render(checkin(), { error: 'O plano mudou depois desta proposta.' });
    expect(html).toContain('role="alert"');
    expect(html).toContain('O plano mudou depois desta proposta.');
  });
});
