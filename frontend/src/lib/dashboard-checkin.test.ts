import { describe, expect, it } from 'vitest';
import {
  answeredDays,
  answeredFields,
  checkinForDate,
  cooldownBadge,
  emptyCheckinForm,
  formFromCheckin,
  isScoreInRange,
  isSuppressed,
  scaleHint,
  toCheckinInput,
  validateCheckinForm,
  type CheckinFormState,
} from './dashboard-checkin';
import { CHECKIN_SCORE_FIELDS } from './dashboard-checkin-types';
import type { DailyCheckin } from './dashboard-checkin-types';
import type { InsightCooldown } from './dashboard-overview-types';

/**
 * A regra que estes testes protegem na borda: **escore em branco é ausência, nunca
 * 0**. O `<SegmentedControl>`/`<NumberInput>` devolve `''` quando nada está
 * marcado, e um `Number('')` descuidado viraria 0 — um valor fora da escala 1..5
 * que a baseline pessoal do backend trataria como medida.
 */

const HOJE = '2026-09-25';

function form(over: Partial<CheckinFormState> = {}): CheckinFormState {
  return { ...emptyCheckinForm(HOJE), ...over };
}

function checkin(over: Partial<DailyCheckin> = {}): DailyCheckin {
  return {
    id: 1,
    local_date: HOJE,
    energy: 4,
    soreness: 2,
    stress: null,
    mood: 3,
    notes: null,
    created_at: null,
    updated_at: null,
    answered: ['energy', 'soreness', 'mood'],
    score_labels: {},
    ...over,
  };
}

describe('formulário', () => {
  it('nasce com todos os escores em branco (sem valor neutro pré-selecionado)', () => {
    const vazio = emptyCheckinForm(HOJE);
    for (const field of CHECKIN_SCORE_FIELDS) expect(vazio[field]).toBe('');
    expect(answeredFields(vazio)).toEqual([]);
  });

  it('aceita resposta parcial', () => {
    expect(validateCheckinForm(form({ soreness: 4 }))).toEqual({});
    expect(answeredFields(form({ soreness: 4 }))).toEqual(['soreness']);
  });

  it('recusa formulário sem nenhuma resposta e sem nota', () => {
    expect(validateCheckinForm(form()).energy).toBeTruthy();
  });

  it('aceita nota sozinha como check-in', () => {
    expect(validateCheckinForm(form({ notes: 'joelho incomodou' }))).toEqual({});
  });

  it('recusa escore fora da escala', () => {
    expect(isScoreInRange(0)).toBe(false);
    expect(isScoreInRange(6)).toBe(false);
    expect(isScoreInRange(3)).toBe(true);
    expect(isScoreInRange('')).toBe(true);
    expect(validateCheckinForm(form({ energy: 0 })).energy).toBeTruthy();
  });

  it('recusa nota acima de 255 caracteres', () => {
    expect(validateCheckinForm(form({ energy: 3, notes: 'x'.repeat(256) })).notes).toBeTruthy();
  });

  it('exige a data', () => {
    expect(validateCheckinForm(form({ local_date: '', energy: 3 })).local_date).toBeTruthy();
  });

  it('pré-carrega o check-in já gravado do dia (corrigir, não recomeçar)', () => {
    const preenchido = formFromCheckin(checkin({ notes: 'ok' }));
    expect(preenchido.energy).toBe(4);
    // Escore ausente volta a "em branco", NÃO a 0.
    expect(preenchido.stress).toBe('');
    expect(preenchido.notes).toBe('ok');
  });

  it('a escala tem as duas pontas nomeadas', () => {
    expect(scaleHint('soreness')).toBe('1 = sem dor, 5 = muita dor');
    expect(scaleHint('energy')).toContain('energia');
  });
});

describe('payload do POST', () => {
  it('omite o campo em branco em vez de enviar null', () => {
    // O upsert do backend só atualiza as colunas ENVIADAS: omitir preserva o
    // que já estava gravado (responder só a dor à tarde não apaga a disposição
    // da manhã). Enviar `null` APAGARIA.
    const payload = toCheckinInput(form({ soreness: 5 }));
    expect(payload).toEqual({ local_date: HOJE, soreness: 5 });
    expect('energy' in payload).toBe(false);
  });

  it('nunca transforma branco em zero', () => {
    const payload = toCheckinInput(form({ energy: 1 }));
    expect(Object.values(payload)).not.toContain(0);
  });

  it('descarta nota só de espaços', () => {
    expect(toCheckinInput(form({ energy: 3, notes: '   ' }))).toEqual({
      local_date: HOJE,
      energy: 3,
    });
  });
});

describe('leitura', () => {
  it('acha o check-in de um dia e devolve null quando não existe', () => {
    const itens = [checkin({ id: 1, local_date: HOJE }), checkin({ id: 2, local_date: '2026-09-24' })];
    expect(checkinForDate(itens, HOJE)?.id).toBe(1);
    expect(checkinForDate(itens, '2026-01-01')).toBeNull();
  });

  it('conta só os dias em que ESTE escore foi respondido', () => {
    const itens = [checkin({ stress: null }), checkin({ id: 2, stress: 3 })];
    expect(answeredDays(itens, 'stress')).toBe(1);
    expect(answeredDays(itens, 'energy')).toBe(2);
  });
});

describe('selo de cooldown', () => {
  function cooldown(over: Partial<InsightCooldown> = {}): InsightCooldown {
    return {
      alert_key: 'k',
      reason: 'first_seen',
      suppressed: false,
      first_seen_at: null,
      last_emitted_at: null,
      emitted_count: 1,
      suppressed_count: 0,
      snoozed_until: null,
      metric_version: 'alert-cooldown-v1',
      ...over,
    };
  }

  it('sem decisão de cooldown não há selo (antes da migração 051)', () => {
    expect(cooldownBadge(null)).toBeNull();
    expect(cooldownBadge(undefined)).toBeNull();
  });

  it('o alerta que piorou tem o selo mais forte', () => {
    expect(cooldownBadge(cooldown({ reason: 'escalated' }))).toEqual({ label: 'Piorou', color: 'red' });
  });

  it('cada motivo tem rótulo em português', () => {
    const reasons: InsightCooldown['reason'][] = [
      'first_seen',
      'new_episode',
      'escalated',
      'cooldown_expired',
      'cooldown_active',
    ];
    for (const reason of reasons) {
      const selo = cooldownBadge(cooldown({ reason }));
      expect(selo?.label).toBeTruthy();
      expect(selo?.label).not.toBe(reason);
    }
  });

  it('só o cartão que o backend silenciou conta como silenciado', () => {
    expect(isSuppressed({ cooldown: cooldown({ reason: 'cooldown_active', suppressed: true }) })).toBe(true);
    expect(isSuppressed({ cooldown: cooldown() })).toBe(false);
    expect(isSuppressed({ cooldown: null })).toBe(false);
  });
});
