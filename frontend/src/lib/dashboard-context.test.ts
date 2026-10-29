import { describe, expect, it } from 'vitest';
import {
  CONTEXT_EVENT_TYPE_OPTIONS,
  collectContextLimitations,
  contextEventLabel,
  emptyContextForm,
  eventDurationDays,
  eventPeriodLabel,
  groupByDayDescending,
  isContextEventType,
  sourceBadge,
  toContextEventInput,
  validateContextForm,
} from './dashboard-context';
import type { ContextEvent, ContextResponse } from './dashboard-context-types';

function evento(over: Partial<ContextEvent> = {}): ContextEvent {
  return {
    id: 1,
    event_type: 'alcohol',
    event_type_label: 'Álcool',
    local_date: '2026-09-20',
    end_local_date: null,
    intensity: 3,
    notes: null,
    source: 'manual',
    derived: false,
    editable: true,
    ...over,
  };
}

describe('rótulos e origem', () => {
  it('usa o rótulo do backend e cai no vocabulário local quando ele falta', () => {
    expect(contextEventLabel(evento())).toBe('Álcool');
    expect(contextEventLabel({ event_type: 'travel', event_type_label: null })).toBe('Viagem');
    expect(contextEventLabel({ event_type: 'inedito', event_type_label: null })).toBe('inedito');
  });

  it('distingue registrado de derivado — é o que diz o que dá para editar', () => {
    expect(sourceBadge({ derived: false }).label).toBe('Registrado');
    expect(sourceBadge({ derived: true }).label).toBe('Derivado');
  });

  it('oferece todos os tipos do ENUM da 048 no formulário', () => {
    expect(CONTEXT_EVENT_TYPE_OPTIONS).toHaveLength(10);
    expect(isContextEventType('injury_pain')).toBe(true);
    expect(isContextEventType('ressaca')).toBe(false);
  });
});

describe('timeline', () => {
  it('agrupa por dia sem reordenar dentro do dia e mostra o mais recente primeiro', () => {
    const dias = groupByDayDescending([
      evento({ id: 1, local_date: '2026-09-10', event_type: 'travel' }),
      evento({ id: 2, local_date: '2026-09-20', event_type: 'alcohol' }),
      evento({ id: null, local_date: '2026-09-20', event_type: 'plan_change', derived: true, editable: false }),
    ]);
    expect(dias.map((dia) => dia.local_date)).toEqual(['2026-09-20', '2026-09-10']);
    expect(dias[0].events.map((item) => item.event_type)).toEqual(['alcohol', 'plan_change']);
  });

  it('evento de um dia mostra uma data; com duração mostra o intervalo', () => {
    expect(eventPeriodLabel({ local_date: '2026-09-20', end_local_date: null })).toBe('20/09/2026');
    expect(eventPeriodLabel({ local_date: '2026-09-20', end_local_date: '2026-09-20' })).toBe('20/09/2026');
    expect(eventPeriodLabel({ local_date: '2026-09-10', end_local_date: '2026-09-20' })).toBe(
      '10/09/2026 → 20/09/2026',
    );
  });

  it('duração é inclusiva e nunca menor que um dia', () => {
    expect(eventDurationDays({ local_date: '2026-09-20', end_local_date: null })).toBe(1);
    expect(eventDurationDays({ local_date: '2026-09-10', end_local_date: '2026-09-12' })).toBe(3);
    // Fim antes do início não existe (o backend recusa); a tela não quebra.
    expect(eventDurationDays({ local_date: '2026-09-12', end_local_date: '2026-09-10' })).toBe(1);
  });

  it('não repete limitação', () => {
    const payload = { limitations: ['a', 'a', 'b'] } as ContextResponse;
    expect(collectContextLimitations(payload)).toEqual(['a', 'b']);
  });
});

describe('formulário', () => {
  it('nasce vazio, sem gradação e sem data final', () => {
    const form = emptyContextForm('2026-09-22');
    expect(form).toMatchObject({ local_date: '2026-09-22', intensity: '', end_local_date: '' });
    expect(validateContextForm(form)).toEqual({});
  });

  it('recusa data inválida, fim antes do início e intensidade fora de 1..5', () => {
    expect(validateContextForm({ ...emptyContextForm('22/09/2026') }).local_date).toBeDefined();
    expect(
      validateContextForm({ ...emptyContextForm('2026-09-22'), end_local_date: '2026-09-20' }).end_local_date,
    ).toBeDefined();
    expect(validateContextForm({ ...emptyContextForm('2026-09-22'), intensity: 0 }).intensity).toBeDefined();
    expect(validateContextForm({ ...emptyContextForm('2026-09-22'), intensity: 6 }).intensity).toBeDefined();
    expect(validateContextForm({ ...emptyContextForm('2026-09-22'), notes: 'x'.repeat(256) }).notes).toBeDefined();
  });

  it('converte para o payload do POST mantendo ausência como null, nunca 0', () => {
    expect(toContextEventInput(emptyContextForm('2026-09-22'))).toEqual({
      event_type: 'alcohol',
      local_date: '2026-09-22',
      end_local_date: null,
      intensity: null,
      notes: null,
    });
    expect(
      toContextEventInput({
        event_type: 'travel',
        local_date: '2026-09-10',
        end_local_date: '2026-09-20',
        intensity: 2,
        notes: '  congresso  ',
      }),
    ).toEqual({
      event_type: 'travel',
      local_date: '2026-09-10',
      end_local_date: '2026-09-20',
      intensity: 2,
      notes: 'congresso',
    });
  });
});
