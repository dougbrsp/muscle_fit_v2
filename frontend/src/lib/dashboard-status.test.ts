import { describe, expect, it } from 'vitest';
import {
  DATA_NATURE_ORDER,
  DATA_QUALITY_ORDER,
  EXERCISE_PROGRESSION_ORDER,
  METHODOLOGY_RANGE_SOURCE_ORDER,
  METHODOLOGY_VOLUME_STATUS_ORDER,
  MUSCLE_GROUP_PROGRESSION_ORDER,
  MUSCLE_GROUP_VOLUME_ORDER,
  PLANNED_VOLUME_STATUS_ORDER,
  PROGRESSION_TREND_ORDER,
  READINESS_ORDER,
  RECOVERY_GUIDANCE_ORDER,
  RECOVERY_STATUS_ORDER,
  SEVERITY_ORDER,
  STATUS_DOMAIN_ORDER,
  STATUS_REGISTRY,
  TARGET_SOURCE_ORDER,
  UNKNOWN_STATUS,
  WEEKLY_VOLUME_STATUS_ORDER,
  statusHexScale,
  statusLegend,
  statusVisual,
  volumeStatusDomain,
  type StatusDomain,
} from './dashboard-status';

const DOMAINS = Object.keys(STATUS_REGISTRY) as StatusDomain[];

describe('cobertura do registro', () => {
  it('todo domínio declarado tem ordem canônica', () => {
    expect(Object.keys(STATUS_DOMAIN_ORDER).sort()).toEqual([...DOMAINS].sort());
  });

  it('a ordem canônica cobre exatamente as chaves do domínio', () => {
    for (const domain of DOMAINS) {
      expect([...STATUS_DOMAIN_ORDER[domain]].sort()).toEqual(Object.keys(STATUS_REGISTRY[domain]).sort());
    }
  });

  it('todo valor de todo enum tem rótulo, cor, hex e ícone', () => {
    for (const domain of DOMAINS) {
      for (const code of STATUS_DOMAIN_ORDER[domain]) {
        const visual = statusVisual(domain, code);
        expect(visual.label, `${domain}.${code}`).toBeTruthy();
        expect(visual.color, `${domain}.${code}`).toBeTruthy();
        expect(visual.hex, `${domain}.${code}`).toMatch(/^#[0-9a-fA-F]{6}$/);
        expect(typeof visual.Icon, `${domain}.${code}`).toBe('object');
      }
    }
  });

  it('nenhum enum de status do dashboard ficou de fora', () => {
    // Guarda-chuva da F0.4: os enums listados no plano §11 têm de existir.
    expect(DOMAINS).toEqual(
      expect.arrayContaining([
        'weeklyVolume',
        'exerciseProgression',
        'muscleGroupProgression',
        // 2026-10-02: volume por grupo tem domínio próprio, com rótulos neutros.
        'muscleGroupVolume',
        'progressionTrend',
        'readiness',
        'recoveryStatus',
        // F2.5 / M34 — no mapa de calor a cor nunca é o único sinal: a faixa
        // é classificada pelo backend e passa por aqui para virar rótulo.
        'muscleHeatmap',
        'recoveryGuidance',
        'cycleSlot',
        'severity',
        'dataQuality',
        'dataNature',
        // Fase 4 — metodologia de volume por grupo: domínio próprio, em outra
        // unidade (séries diretas), nunca reaproveita weeklyVolume.
        'methodologyVolume',
        'methodologyRangeSource',
      ]),
    );
  });
});

describe('valores de cada enum (espelho do backend)', () => {
  it('volume semanal tem os 5 estados de classify_weekly_volume_status', () => {
    expect(WEEKLY_VOLUME_STATUS_ORDER).toEqual([
      'BELOW_MIN',
      'BETWEEN_MIN_TARGET',
      'ON_TARGET',
      'BETWEEN_TARGET_MAX',
      'ABOVE_MAX',
    ]);
  });

  it('volume planejado tem os 3 estados de classify_planned_volume_status', () => {
    expect(PLANNED_VOLUME_STATUS_ORDER).toEqual(['BELOW_TARGET', 'ON_TARGET', 'ABOVE_TARGET']);
  });

  it('origem da meta inclui o Modelo por equipamento', () => {
    expect(TARGET_SOURCE_ORDER).toEqual(['explicit', 'assigned_template', 'template_plan', 'fallback_generic']);
    expect(statusVisual('targetSource', 'template_plan').label).toBe('Planejado pelo modelo');
  });

  it('o domínio do status é decidido pela origem da meta', () => {
    expect(volumeStatusDomain('template_plan')).toBe('plannedVolume');
    expect(volumeStatusDomain('assigned_template')).toBe('weeklyVolume');
    expect(volumeStatusDomain('fallback_generic')).toBe('weeklyVolume');
    expect(volumeStatusDomain(undefined)).toBe('weeklyVolume');
    expect(statusVisual('plannedVolume', 'BELOW_TARGET').label).toBe('Abaixo do planejado');
  });

  it('metodologia de volume espelha METHODOLOGY_VOLUME_STATUS_CODES (methodology_volume.py)', () => {
    // Mesma ordem da enum `MethodologyVolumeStatus` do motor: se o backend
    // ganhar ou perder um estado, este teste quebra antes da tela.
    expect(METHODOLOGY_VOLUME_STATUS_ORDER).toEqual([
      'WITHIN_RANGE',
      'BELOW_RANGE',
      'ABOVE_RANGE',
      'NOT_DIRECTLY_TRAINED',
      'NOT_EVALUABLE',
    ]);
    expect(METHODOLOGY_RANGE_SOURCE_ORDER).toEqual(['methodology', 'derived', 'admin']);
  });

  it('paleta unificada (revista 2026-10-02): abaixo laranja claro, na faixa verde, acima laranja escuro', () => {
    // Vale para o painel do builder, a tela de diagnóstico e a linha por card
    // (M43). Abaixo e acima dividem o badge laranja; hex e ícone os separam.
    const below = statusVisual('methodologyVolume', 'BELOW_RANGE');
    const within = statusVisual('methodologyVolume', 'WITHIN_RANGE');
    const above = statusVisual('methodologyVolume', 'ABOVE_RANGE');
    expect(below).toMatchObject({ color: 'orange', hex: '#f08c00', label: 'Abaixo da faixa' });
    expect(within).toMatchObject({ color: 'green', label: 'Na faixa' });
    expect(above).toMatchObject({ color: 'orange', label: 'Acima da faixa' });
    expect(below.color).not.toBe('red');
    expect(below.hex).not.toBe('#e03131');
    expect(below.hex).not.toBe(above.hex);
    expect(below.Icon).not.toBe(above.Icon);
  });

  it('NOT_DIRECTLY_TRAINED e NOT_EVALUABLE seguem neutros e distintos de BELOW_RANGE por rótulo e ícone', () => {
    const neutral = statusVisual('methodologyVolume', 'NOT_DIRECTLY_TRAINED');
    const below = statusVisual('methodologyVolume', 'BELOW_RANGE');
    const notEvaluable = statusVisual('methodologyVolume', 'NOT_EVALUABLE');
    expect(neutral.color).toBe('gray');
    expect(notEvaluable.color).toBe('blue');
    // Rótulo + ícone distintos (cor nunca é o único sinal).
    expect(neutral.label).not.toBe(below.label);
    expect(neutral.Icon).not.toBe(below.Icon);
    expect(statusVisual('methodologyRangeSource', 'derived').label).toBe('Derivada');
    expect(statusVisual('methodologyRangeSource', 'admin').label).toBe('Admin');
  });

  it('progressão por exercício tem os 6 estados de ExerciseProgressionStatus', () => {
    expect(EXERCISE_PROGRESSION_ORDER).toEqual([
      'PROGRESSING',
      'STABLE',
      'PLATEAU_SUSPECTED',
      'REGRESSING',
      'INSUFFICIENT_DATA',
      'NOT_COMPARABLE',
    ]);
  });

  it('progressão por grupo e tendência v2 mantêm os códigos pt-BR', () => {
    expect(MUSCLE_GROUP_PROGRESSION_ORDER).toEqual([
      'PROGREDINDO',
      'ESTAVEL',
      'REGREDINDO',
      // M50: janela atual com poucas sessões não vira "regredindo".
      'DADOS_INSUFICIENTES',
      'NOVO',
      'SEM_DADOS',
    ]);
    expect(PROGRESSION_TREND_ORDER).toEqual([
      'PROGREDINDO',
      'ESTAVEL',
      'ESTAGNADO',
      'REGREDINDO',
      'DADOS_INSUFICIENTES',
    ]);
  });

  it('volume por grupo é neutro: nunca "regredindo", nunca verde/vermelho', () => {
    expect(MUSCLE_GROUP_VOLUME_ORDER).toEqual([
      'VOLUME_UP',
      'VOLUME_STABLE',
      'VOLUME_DOWN',
      'DADOS_INSUFICIENTES',
      'NOVO',
      'SEM_DADOS',
    ]);
    expect(statusVisual('muscleGroupVolume', 'VOLUME_DOWN').label).toBe('Volume menor');
    for (const code of MUSCLE_GROUP_VOLUME_ORDER) {
      const visual = statusVisual('muscleGroupVolume', code);
      expect(visual.label).not.toMatch(/regredindo|progredindo/i);
      expect(['green', 'red']).not.toContain(visual.color);
    }
  });

  it('prontidão, status de cor e orientação', () => {
    expect(READINESS_ORDER).toEqual(['READY', 'REDUCED_DOSE', 'NO_DIRECT_WORK']);
    expect(RECOVERY_STATUS_ORDER).toEqual(['green', 'yellow', 'red']);
    expect(RECOVERY_GUIDANCE_ORDER).toEqual(['ideal', 'recovering', 'avoid']);
  });

  it('severidade, qualidade e natureza do dado', () => {
    expect(SEVERITY_ORDER).toEqual(['ACTION', 'ATTENTION', 'INFO']);
    expect(DATA_QUALITY_ORDER).toEqual(['HIGH', 'MEDIUM', 'LOW', 'INSUFFICIENT']);
    expect(DATA_NATURE_ORDER).toEqual([
      'OBSERVED',
      'DERIVED',
      'PLANNED',
      'PROJECTED',
      'ESTIMATED',
      'INFERRED',
    ]);
  });
});

describe('divergências unificadas', () => {
  it('status de cor e orientação são o MESMO objeto da prontidão', () => {
    expect(statusVisual('recoveryStatus', 'green')).toBe(statusVisual('readiness', 'READY'));
    expect(statusVisual('recoveryGuidance', 'avoid')).toBe(statusVisual('readiness', 'NO_DIRECT_WORK'));
    expect(statusVisual('recoveryStatus', 'yellow').label).toBe('Dose reduzida');
  });

  it('NO_DIRECT_WORK usa o rótulo acionável, não o descritivo', () => {
    expect(statusVisual('readiness', 'NO_DIRECT_WORK').label).toBe('Evitar trabalho direto');
  });

  it('os códigos comuns dos dois enums pt-BR compartilham a apresentação', () => {
    for (const code of ['PROGREDINDO', 'ESTAVEL', 'REGREDINDO', 'DADOS_INSUFICIENTES']) {
      expect(statusVisual('muscleGroupProgression', code)).toBe(statusVisual('progressionTrend', code));
    }
  });

  it('os dois laranjas do volume semanal se distinguem por ícone e hex', () => {
    const aproximando = statusVisual('weeklyVolume', 'BETWEEN_MIN_TARGET');
    const acimaDoMaximo = statusVisual('weeklyVolume', 'ABOVE_MAX');
    expect(aproximando.color).toBe(acimaDoMaximo.color);
    expect(aproximando.hex).not.toBe(acimaDoMaximo.hex);
    expect(aproximando.Icon).not.toBe(acimaDoMaximo.Icon);
  });
});

describe('lookup tolerante', () => {
  it('código fora do contrato não quebra a tela', () => {
    const visual = statusVisual('weeklyVolume', 'ALGO_NOVO');
    expect(visual.label).toBe('ALGO_NOVO');
    expect(visual.color).toBe(UNKNOWN_STATUS.color);
  });

  it('null/undefined viram o estado desconhecido', () => {
    expect(statusVisual('readiness', null)).toBe(UNKNOWN_STATUS);
    expect(statusVisual('readiness', undefined)).toBe(UNKNOWN_STATUS);
  });
});

describe('legenda e paleta', () => {
  it('a legenda segue a ordem canônica e usa hex', () => {
    expect(statusLegend('weeklyVolume').map((entry) => entry.label)).toEqual([
      'Abaixo do mínimo',
      'Aproximando da meta',
      'Na meta',
      'Acima da meta',
      'Acima do máximo',
    ]);
  });

  it('a escala hex tem um item por estado', () => {
    for (const domain of DOMAINS) {
      expect(statusHexScale(domain)).toHaveLength(STATUS_DOMAIN_ORDER[domain].length);
    }
  });
});

describe('leitura do sinal (recuperação)', () => {
  it('"Em linha" (NEUTRAL) é azul, distinto do cinza de "Sem baseline"', () => {
    const neutral = statusVisual('signalReading', 'NEUTRAL');
    expect(neutral.label).toBe('Em linha');
    expect(neutral.color).toBe('blue');
    expect(neutral.hex).toBe('#1971c2');
    expect(statusVisual('signalReading', 'UNKNOWN').color).toBe('gray');
  });
});
