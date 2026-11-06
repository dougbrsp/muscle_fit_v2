import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Checkin } from '@/lib/nutrition-checkin';
import { theme } from '@/theme';
import { CheckinCard, CheckinCardView } from './CheckinCard';
import { CheckinHistoryTable } from './CheckinHistory';
import { CheckinReview } from './CheckinReview';

// Card, revisão e histórico do check-in (N7) por SSR (sem jsdom): só o que a API devolve, nenhum cálculo.
function envolver(el: ReturnType<typeof createElement>): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, el));
}

const MACROS_ANTES = { kcal: 2400, protein_g: 190, carb_g: 260, fat_g: 70 };
const MACROS_DEPOIS = { kcal: 2300, protein_g: 190, carb_g: 235, fat_g: 66 };

function checkin(extra: Partial<Checkin> = {}): Checkin {
  return {
    id: 11,
    user_id: 2,
    week_start: '2026-10-26',
    week_end: '2026-11-01',
    due_date: '2026-11-02',
    status: 'proposed',
    status_label: 'Aguardando sua resposta',
    verdict: 'slow',
    verdict_label: 'Abaixo do ritmo esperado',
    can_respond: true,
    show_card: true,
    expires_at: '2026-11-05T23:59:59',
    expires_on: '2026-11-05',
    adjustment_kcal: -100,
    missing_items: [],
    reason_items: [{ code: 'checkin_lean_loss', label: 'A massa magra caiu acima do limite.' }],
    proposal: {
      before: MACROS_ANTES,
      after: MACROS_DEPOIS,
      delta_kcal: -100,
      changes: {},
      change_items: [{ field: 'weekly_rate_share', label: 'Ritmo de perda semanal', unit: 'share', before: 0.007, after: 0.0075 }],
    },
    engine_version: 'nutrition-v1',
    source_plan_id: 1,
    generated_plan_id: null,
    responded_at: null,
    created_at: '2026-11-02T08:00:00',
    summary: {
      complete_days: 6,
      mean_tdee_kcal: 2800,
      mean_intake_kcal: 2290,
      mean_target_kcal: 2300,
      kcal_share: 0.996,
      weight_week_mean_kg: 80.1,
      weight_weekly_change_kg: -0.1,
      expected_weekly_change_kg: -0.5,
      body_fat_percent: 18.5,
      muscle_mass_kg: 36,
      observed_tdee: { kcal: 2750, quality: 'high' },
      lean_trend: { weekly_change: -0.3, readings: 4, span_days: 20, mean: 36, valid_for_bia: true },
    },
    goal: { goal_weight_kg: null, goal_body_fat_percent: 12 },
    ...extra,
  };
}

describe('CheckinCardView', () => {
  it('sem check-in ou com show_card falso não renderiza nada', () => {
    expect(envolver(createElement(CheckinCardView, { checkin: null }))).not.toContain('Check-in semanal');
    expect(envolver(createElement(CheckinCardView, { checkin: undefined }))).not.toContain('Check-in semanal');
    expect(envolver(createElement(CheckinCardView, { checkin: checkin({ show_card: false, status: 'accepted' }) }))).not.toContain('Check-in semanal');
  });

  it('com proposta: veredito, ajuste do motor, validade e as três ações com alvo de toque', () => {
    const html = envolver(createElement(CheckinCardView, { checkin: checkin() }));
    expect(html).toContain('Check-in semanal');
    expect(html).toContain('Abaixo do ritmo esperado');
    expect(html).toContain('26/10 a 01/11');
    expect(html).toMatch(/-100 kcal por dia/);
    expect(html).toContain('Vale até 05/11');
    expect(html).toContain('Aceitar as novas metas');
    expect(html).toContain('Manter as atuais');
    expect(html).toContain('href="/nutricao/checkin"');
    expect(html).toContain('min-height:44px');
  });

  it('sem proposta (informativo): mostra o que faltou ou o motivo e só o link', () => {
    const html = envolver(
      createElement(CheckinCardView, {
        checkin: checkin({
          status: 'informative',
          can_respond: false,
          proposal: null,
          adjustment_kcal: null,
          verdict: 'inconclusive',
          verdict_label: 'Inconclusivo',
          missing_items: [{ code: 'checkin_few_complete_days', label: 'Menos de 5 dias da semana completos.' }],
        }),
      }),
    );
    expect(html).toContain('Inconclusivo');
    expect(html).toContain('Menos de 5 dias da semana completos.');
    expect(html).not.toContain('Aceitar as novas metas');
    expect(html).toContain('Ver a semana');
  });

  it('plano adaptativo (informative + checkin_adaptive_informative, can_respond false): rótulo do backend e sem aceitar', () => {
    const adaptativo = checkin({
      status: 'informative',
      can_respond: false, // payload real: o backend só libera responder em `proposed`
      proposal: null,
      adjustment_kcal: null,
      reason_items: [{ code: 'checkin_adaptive_informative', label: 'No plano adaptativo o gasto se corrige sozinho; o check-in só informa.' }],
    });
    const card = envolver(createElement(CheckinCardView, { checkin: adaptativo }));
    expect(card).toContain('No plano adaptativo o gasto se corrige sozinho; o check-in só informa.');
    expect(card).not.toContain('Aceitar as novas metas');
    expect(card).not.toContain('Manter as atuais');
    const revisao = envolver(createElement(CheckinReview, { checkin: adaptativo }));
    expect(revisao).toContain('No plano adaptativo o gasto se corrige sozinho; o check-in só informa.');
    expect(revisao).not.toContain('Aceitar as novas metas');
  });

  it('erro da mutação aparece como alerta', () => {
    const html = envolver(createElement(CheckinCardView, { checkin: checkin(), error: 'A proposta expirou.' }));
    expect(html).toContain('A proposta expirou.');
    expect(html).toContain('role="alert"');
  });

  it('o card conectado renderiza vazio enquanto carrega (sem rede nem dado)', () => {
    const qc = new QueryClient();
    const html = envolver(createElement(QueryClientProvider, { client: qc }, createElement(CheckinCard)));
    expect(html).not.toContain('Check-in semanal');
  });
});

describe('CheckinReview', () => {
  it('semana × alvo, o que muda, por quê e a barra fixa com a área segura', () => {
    const html = envolver(createElement(CheckinReview, { checkin: checkin() }));
    expect(html).toContain('Check-in da semana de 26/10 a 01/11');
    expect(html).toContain('Semana × alvo');
    for (const rotulo of ['Gasto (kcal por dia)', 'Consumo (kcal por dia)', 'Peso', '% de gordura (bioimpedância)', 'Massa muscular (bioimpedância)']) {
      expect(html).toContain(rotulo);
    }
    expect(html).toContain('2.800 kcal');
    expect(html).toContain('alvo: 12');
    expect(html).toContain('Por que o motor decidiu assim');
    expect(html).toContain('A massa magra caiu acima do limite.');
    expect(html).toContain('Ritmo de perda semanal');
    expect(html).toContain('0,7% → 0,75%');
    expect(html).toContain('Dia sem treino');
    expect(html).toContain('safe-area-inset-bottom');
    expect(html).toContain('Aceitar as novas metas');
  });

  it('inconclusivo lista o que faltou e não oferece resposta', () => {
    const html = envolver(
      createElement(CheckinReview, {
        checkin: checkin({
          status: 'informative',
          can_respond: false,
          proposal: null,
          verdict: 'inconclusive',
          verdict_label: 'Inconclusivo',
          missing_items: [{ code: 'checkin_few_weighings', label: 'Pesagens insuficientes.' }],
          summary: { complete_days: 2 },
        }),
      }),
    );
    expect(html).toContain('O que faltou para avaliar a semana');
    expect(html).toContain('Pesagens insuficientes.');
    expect(html).toContain('Nenhuma mudança nas metas nesta semana.');
    expect(html).not.toContain('Aceitar as novas metas');
    expect(html).not.toContain('safe-area-inset-bottom');
  });
});

describe('CheckinHistoryTable', () => {
  it('lista semana, veredito, ajuste e situação; vazio explica', () => {
    const html = envolver(
      createElement(CheckinHistoryTable, {
        items: [checkin(), checkin({ id: 10, week_start: '2026-10-19', week_end: '2026-10-25', status: 'declined', status_label: 'Recusado', verdict: 'on_track', verdict_label: 'No ritmo', adjustment_kcal: null })],
      }),
    );
    expect(html).toContain('26/10 a 01/11');
    expect(html).toContain('19/10 a 25/10');
    expect(html).toContain('-100 kcal');
    expect(html).toContain('sem mudança');
    expect(html).toContain('Recusado');
    expect(envolver(createElement(CheckinHistoryTable, { items: [] }))).toContain('Nenhum check-in ainda');
  });
});
