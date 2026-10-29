import { describe, expect, it } from 'vitest';
import type {
  CardioResponse,
  CardioSession,
  CardioWeek,
  SessionHeartRate,
} from './dashboard-cardio-types';
import {
  adherenceStatusColor,
  collectCardioLimitations,
  groupSessionsByDay,
  hasAnythingToShow,
  hasZoneData,
  matchBadge,
  matchDeltaLabel,
  modeChartSummary,
  paiRows,
  sportLabel,
  weeklyModeRows,
  weeklyZoneRows,
  wellnessRows,
  zoneChartSummary,
  zoneSourceLabel,
} from './dashboard-cardio';

function sessao(over: Partial<CardioSession> = {}): CardioSession {
  return {
    intervals_activity_id: 1,
    external_id: 'i-1',
    activity_date: '2026-09-20',
    start_time: '2026-09-20T06:30:00',
    name: 'Corrida matinal',
    sport_type: 'Run',
    kind: 'cardio',
    minutes: 30,
    distance_km: 5,
    calories_kcal: 320,
    avg_hr_bpm: 148,
    max_hr_bpm: 171,
    training_load: 45,
    zone_seconds: null,
    zone_minutes: null,
    high_intensity: null,
    ...over,
  };
}

function fc(over: Partial<SessionHeartRate> = {}): SessionHeartRate {
  return {
    activity_id: 10,
    local_date: '2026-09-21',
    name: 'Treino A',
    started_at: '2026-09-21T19:00:00',
    minutes: 60,
    avg_hr_bpm: 118,
    max_hr_bpm: 155,
    hr_source: 'intervals_link',
    calories_kcal: 410,
    training_load: 38,
    match_status: 'MATCHED',
    match_delta_seconds: 120,
    candidates_count: 1,
    has_heart_rate: true,
    ...over,
  };
}

describe('sportLabel', () => {
  it('traduz as modalidades conhecidas', () => {
    expect(sportLabel('Run')).toBe('Corrida');
    expect(sportLabel('VirtualRide')).toBe('Pedal virtual');
  });

  it('devolve o tipo cru quando não conhece — é por ele que o usuário acha a atividade no Intervals', () => {
    expect(sportLabel('Kitesurf')).toBe('Kitesurf');
  });

  it('não inventa modalidade para atividade sem tipo', () => {
    expect(sportLabel(null)).toBe('Sem modalidade');
  });
});

describe('matchBadge', () => {
  it('marca a FC ambígua como ambígua, nunca como certeza', () => {
    const badge = matchBadge(fc({ match_status: 'MATCHED_AMBIGUOUS', candidates_count: 3 }));
    expect(badge.label).toBe('FC ambígua');
    expect(badge.hint).toContain('3 atividades');
  });

  it('distingue casada de sem FC nenhuma', () => {
    expect(matchBadge(fc()).label).toBe('FC associada');
    expect(matchBadge(fc({ match_status: null, hr_source: null })).label).toBe('Sem FC');
  });

  it('a FC medida no próprio treino não carrega a ressalva do casamento', () => {
    const badge = matchBadge(
      fc({ hr_source: 'activity', match_status: 'MATCHED_AMBIGUOUS', candidates_count: 2 }),
    );
    expect(badge.label).toBe('FC do treino');
    expect(badge.hint).not.toContain('tolerância');
  });
});

describe('matchDeltaLabel', () => {
  it('arredonda para minutos e trata o caso de menos de um minuto', () => {
    expect(matchDeltaLabel(120)).toBe('2 min de diferença');
    expect(matchDeltaLabel(20)).toBe('menos de 1 min de diferença');
  });

  it('é nulo sem casamento', () => {
    expect(matchDeltaLabel(null)).toBeNull();
  });
});

describe('groupSessionsByDay', () => {
  it('agrupa preservando a ordem do backend', () => {
    const dias = groupSessionsByDay([
      sessao({ activity_date: '2026-09-20', external_id: 'a' }),
      sessao({ activity_date: '2026-09-20', external_id: 'b' }),
      sessao({ activity_date: '2026-09-18', external_id: 'c' }),
    ]);
    expect(dias.map((d) => d.activity_date)).toEqual(['2026-09-20', '2026-09-18']);
    expect(dias[0].sessions.map((s) => s.external_id)).toEqual(['a', 'b']);
  });

  it('não perde atividade sem data', () => {
    const dias = groupSessionsByDay([sessao({ activity_date: null })]);
    expect(dias).toHaveLength(1);
    expect(dias[0].sessions).toHaveLength(1);
  });
});

function payload(over: Partial<CardioResponse> = {}): CardioResponse {
  return {
    window: { kind: '28d', date_from: '2026-08-26', date_to: '2026-09-22', timezone: 'America/Sao_Paulo', days: 28 },
    metric_versions: { cardio_sessions: 'cardio-sessions-v1', session_heart_rate: 'session-hr-v1' },
    data_quality: 'HIGH',
    limitations: [],
    nature: 'OBSERVED',
    intervals_configured: true,
    last_synced_at: '2026-09-22T05:00:00',
    totals: {
      sessions: 0,
      sessions_with_hr: 0,
      minutes: 0,
      distance_km: null,
      calories_kcal: null,
      training_load: null,
      avg_hr_bpm: null,
      max_hr_bpm: null,
    },
    by_sport: [],
    sessions: [],
    strength_activities_total: 0,
    unmatched_strength_activities_total: 0,
    other_activities_total: 0,
    strength_heart_rate: {
      sessions_total: 0,
      sessions_with_hr: 0,
      coverage_pct: null,
      ambiguous_total: 0,
      ambiguous_in_use_total: 0,
      from_activity_total: 0,
      from_link_total: 0,
      avg_hr_bpm: null,
      max_hr_bpm: null,
      items: [],
    },
    weeks: [],
    adherence: null,
    wellness: {
      points: [],
      latest: {
        ctl: null,
        ctl_date: null,
        atl: null,
        atl_date: null,
        tsb: null,
        tsb_date: null,
        hrv_ms: null,
        hrv_ms_date: null,
        resting_hr_bpm: null,
        resting_hr_bpm_date: null,
      },
    },
    pai: { points: [], target_min: 75, latest: null, latest_date: null, latest_below_target: null },
    next_session: null,
    zones: null,
    ...over,
  };
}

describe('collectCardioLimitations', () => {
  it('não repete a mesma frase', () => {
    expect(collectCardioLimitations(payload({ limitations: ['a', 'a', 'b'] }))).toEqual(['a', 'b']);
  });
});

describe('hasAnythingToShow', () => {
  it('é falso na janela sem cardio e sem FC de sessão', () => {
    expect(hasAnythingToShow(payload())).toBe(false);
  });

  it('a FC de sessão sozinha já é conteúdo — ela vem do histórico associado', () => {
    const p = payload();
    expect(
      hasAnythingToShow({
        ...p,
        strength_heart_rate: { ...p.strength_heart_rate, sessions_total: 2, sessions_with_hr: 2 },
      }),
    ).toBe(true);
  });

  it('cardio sozinho também é conteúdo', () => {
    const p = payload();
    expect(hasAnythingToShow({ ...p, totals: { ...p.totals, sessions: 3 } })).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// M98 C4 — só forma dados prontos; nenhum número é recalculado
// ---------------------------------------------------------------------------

function semana(over: Partial<CardioWeek> = {}): CardioWeek {
  return {
    week_start: '2026-09-14',
    sessions: 2,
    minutes: 70,
    calories_kcal: 600,
    sessions_with_zones: 1,
    zone_minutes: { Z1: 5, Z2: 20, Z3: 4, Z4: 1, Z5: 0 },
    continuous_sessions: 1,
    continuous_minutes: 30,
    high_intensity_sessions: 0,
    high_intensity_minutes: 0,
    unclassified_sessions: 1,
    unclassified_minutes: 40,
    ...over,
  };
}

describe('acompanhamento por semana', () => {
  it('passa os minutos por zona do backend sem somar nem arredondar', () => {
    const [linha, vazia] = weeklyZoneRows([semana(), semana({ week_start: '2026-09-21', zone_minutes: null })]);
    expect(linha).toMatchObject({ semana: '14/09', Z1: 5, Z2: 20, Z3: 4, Z4: 1, Z5: 0 });
    // Semana sem tempo em zona: lacuna, nunca zero inventado.
    expect(vazia.Z2).toBeUndefined();
  });

  it('contínuo × intensa × sem classe vêm como o backend classificou', () => {
    const [linha] = weeklyModeRows([semana()]);
    expect(linha).toMatchObject({ continuo: 30, intensa: 0, semClasse: 40, sessoesSemClasse: 1 });
  });

  it('sabe quando não há tempo em zona na janela', () => {
    expect(hasZoneData([semana({ zone_minutes: null })])).toBe(false);
    expect(hasZoneData([semana()])).toBe(true);
  });

  it('descreve os gráficos em texto (acessibilidade)', () => {
    expect(zoneChartSummary([semana()])).toContain('Z2 20 min');
    expect(zoneChartSummary([])).toContain('sem tempo em zona');
    expect(modeChartSummary([semana()])).toContain('1 contínua(s) (30 min)');
  });
});

describe('tendência, PAI, status e zonas', () => {
  it('não recalcula TSB: usa o que veio', () => {
    const [linha] = wellnessRows([
      { date: '2026-09-20', ctl: 40, atl: 48, tsb: -8, hrv_ms: 45, resting_hr_bpm: null },
    ]);
    expect(linha).toEqual({ data: '20/09', ctl: 40, atl: 48, tsb: -8, hrv: 45, fcRepouso: null });
  });

  it('linhas de PAI preservam os valores', () => {
    const p = payload().pai;
    expect(paiRows({ ...p, points: [{ date: '2026-09-21', pai: 60 }] })).toEqual([{ data: '21/09', pai: 60 }]);
  });

  it('cor por status e rótulo da origem das zonas', () => {
    expect(adherenceStatusColor('adherent')).toBe('teal');
    expect(adherenceStatusColor('not_done')).toBe('red');
    expect(adherenceStatusColor('desconhecido')).toBe('gray');
    expect(zoneSourceLabel('intervals')).toBe('Intervals.icu');
    expect(zoneSourceLabel('manual_max_hr')).toBe('FCmáx do Perfil');
    expect(zoneSourceLabel('none')).toBe('Sem zonas definidas');
  });

  it('a próxima sessão recomendada sozinha já é conteúdo', () => {
    expect(
      hasAnythingToShow(
        payload({
          next_session: {
            recommendation_id: 1,
            generated_at: '2026-09-21T08:00:00',
            prompt_version: 'cardio-agent-v3',
            date: '2026-09-23',
            date_label: '23/09 (qua)',
            prescription_text: '30 min Z2',
            prescription_short: 'Z2',
            total_minutes_label: '30 min',
            rationale: null,
            signals: [],
            adjustment_labels: [],
            slot_name: null,
            plan_action: null,
          },
        }),
      ),
    ).toBe(true);
  });
});
