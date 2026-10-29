import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { MethodologyRealizedResponse, MuscleRecoveryConsecutiveCheckItem } from '@/lib/analytics-types';
import realizedFixture from '@/lib/methodology-realized.fixture.json';
import {
  consecutivePairsByBlock,
  dayTooltip,
  insufficientPairByDay,
  perSessionLabel,
  sessionTooltip,
  toActivationRows,
  toRangeStatus,
} from '@/lib/muscle-activation-7d';
import { theme } from '@/theme';
import { ACTIVATION_QUERY_KEY, MuscleActivation7d, RECOVERY_CHECK_QUERY_KEY } from './MuscleActivation7d';

/**
 * Tabela "Ativação muscular (últimos 7 dias)" de /dashboard/musculos (M76 E6):
 * a metodologia vem PRONTA de `GET /analytics/methodology-realized`. O payload
 * abaixo é a saída do dono para o cenário de
 * `api/tests/unit/test_m76_lote4b_ativacao_e_status.py` (fixture gerada; o
 * pytest falha se divergir). Aqui só se testa a tradução para o desenho e o
 * render SSR com cache pré-carregado (sem jsdom).
 */

const RESPONSE = realizedFixture as unknown as MethodologyRealizedResponse;
const DAYS = RESPONSE.days;
const rows = toActivationRows(RESPONSE.items);
const byName = new Map(rows.map((row) => [row.group, row]));

describe('tradução dos códigos do motor', () => {
  it('na faixa / abaixo / acima / sem faixa / sem treino', () => {
    expect(toRangeStatus('WITHIN_RANGE')).toBe('WITHIN');
    expect(toRangeStatus('BELOW_RANGE')).toBe('BELOW');
    expect(toRangeStatus('ABOVE_RANGE')).toBe('ABOVE');
    expect(toRangeStatus('NOT_EVALUABLE')).toBe('NO_TARGET');
    expect(toRangeStatus('NOT_DIRECTLY_TRAINED')).toBe('NO_TARGET');
    expect(toRangeStatus(null)).toBe('NO_TARGET');
  });

  it('linhas em ordem alfabética, sem fatias', () => {
    expect(rows.map((row) => row.group)).toEqual(['Antebraço', 'Bíceps', 'Costas', 'Peitoral', 'Quadríceps']);
  });
});

describe('grade grupo × dia (status do backend)', () => {
  it('3x+: cada dia com o status do dia; a média por treino é a leitura da metodologia', () => {
    const peitoral = byName.get('Peitoral')!;
    expect(peitoral.bucket).toBe('3x_plus');
    expect(peitoral.days.map((cell) => cell.status)).toEqual(['WITHIN', null, 'BELOW', null, 'ABOVE', null, null]);
    expect(peitoral.perSession).toBe(4);
    expect(peitoral.sessionStatus).toBe('WITHIN');
    expect(perSessionLabel(peitoral)).toBe('média 4/treino');
    expect(sessionTooltip(peitoral)).toContain('na faixa');
    expect(peitoral.weeklyTotal).toBe(12);
    expect(peitoral.weeklyStatus).toBe('WITHIN');
  });

  it('fatia e neto somam no bloco (cadeia inteira, no backend)', () => {
    const costas = byName.get('Costas')!;
    expect(costas.days[1]).toMatchObject({ directSets: 10, status: 'WITHIN' });
    expect(costas.memberGroupIds.sort()).toEqual([2, 6, 7]);
  });

  it('frequência sem faixa cadastrada fica sem cor, mas a semana é avaliada', () => {
    const biceps = byName.get('Bíceps')!;
    expect(biceps.sessionRange).toBeNull();
    expect(biceps.days[3].status).toBe('NO_TARGET');
    expect(dayTooltip(biceps.days[3], biceps)).toContain('sem meta');
    expect(biceps.weeklyStatus).toBe('BELOW');
  });

  it('grupo sem macro-grupo: número sem cor', () => {
    const antebraco = byName.get('Antebraço')!;
    expect(antebraco.days[6].status).toBe('NO_TARGET');
    expect(antebraco.weeklyStatus).toBe('NO_TARGET');
    expect(dayTooltip(antebraco.days[6], antebraco)).toContain('sem macro-grupo');
  });

  it('grupo com meta semanal que não treinou aparece como insuficiente', () => {
    const quadriceps = byName.get('Quadríceps')!;
    expect(quadriceps.daysTrained).toBe(0);
    expect(quadriceps.weeklyStatus).toBe('BELOW');
    expect(perSessionLabel(quadriceps)).toBe('');
  });

  it('dia só com trabalho indireto é neutro', () => {
    const peitoral = byName.get('Peitoral')!;
    expect(peitoral.days[6]).toMatchObject({ directSets: 0, effectiveSets: 2, status: null });
  });

  it('tooltip traz séries, faixa e frequência', () => {
    const peitoral = byName.get('Peitoral')!;
    expect(dayTooltip(peitoral.days[0], peitoral)).toMatch(/^4 séries diretas · meta 3–5 \(freq 3x\+\)/);
  });
});

describe('recuperação entre dias consecutivos', () => {
  const pair = (muscle_group: string, muscle_group_id: number | null): MuscleRecoveryConsecutiveCheckItem => ({
    muscle_group,
    muscle_group_id,
    date_from: DAYS[0],
    date_to: DAYS[1],
    elapsed_hours: 20,
    required_hours: 48,
    insufficient_recovery: true,
    timestamp_precision: 'exact',
  });

  it('par da fatia (e do neto) cai na linha do bloco e marca os dois dias', () => {
    const pairs = consecutivePairsByBlock([pair('Dorsal alto', 6), pair('Romboides', null)], DAYS, rows).get('Costas')!;
    expect(pairs).toHaveLength(2);
    expect([...insufficientPairByDay(pairs).keys()]).toEqual([0, 1]);
  });
});

describe('render', () => {
  function render(): string {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(ACTIVATION_QUERY_KEY, RESPONSE);
    client.setQueryData(RECOVERY_CHECK_QUERY_KEY, { days: 7, items: [] });
    return renderToStaticMarkup(
      createElement(QueryClientProvider, { client }, createElement(MantineProvider, { theme }, createElement(MuscleActivation7d))),
    );
  }

  it('mostra título, dias, coluna semanal, cores, média por treino e legenda', () => {
    const html = render();
    expect(html).toContain('Ativação muscular (últimos 7 dias)');
    expect(html).toContain('Semana');
    expect(html).toContain('Estímulo suficiente');
    expect(html).toContain('Estímulo insuficiente');
    expect(html).toContain('data-status="WITHIN"');
    expect(html).toContain('data-status="BELOW"');
    expect(html).toContain('data-status="ABOVE"');
    expect(html).toContain('data-status="NO_TARGET"');
    expect(html).toContain('data-session-status="WITHIN"');
    expect(html).toContain('média 4/treino');
    expect(html).toContain('acima do máximo');
    expect(html).toContain('Quadríceps');
    expect(html).not.toContain('Dorsal alto');
  });
});
