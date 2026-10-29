import { describe, expect, it } from 'vitest';

import {
  CARDIO_DEFAULTS,
  CARDIO_LIMITS,
  CARDIO_MODE_OPTIONS,
  cardioDescribe,
  cardioMode,
  cardioModeChangePatch,
  cardioModeFields,
  cardioShortDescribe,
  cardioTotalSeconds,
  isCardioZoneField,
  newCardioPrescription,
  pickCardioExtras,
  type CardioPrescriptionFields,
} from './cardio-prescription';
import parity from './cardio-prescription.parity.json';
import motorConstants from './motor-constants.generated.json';

// M98 C1: tempo, texto canônico e resumo do item recolhido contra os casos
// gerados pelo dono (`test_m76_lote1b_espelhos_fe.py --gravar`).
describe('paridade com o dono (cardio-prescription.parity.json)', () => {
  it.each(parity.items.map((c) => [c.name, c] as const))('%s', (_name, caso) => {
    const item = caso.item as CardioPrescriptionFields;
    expect(cardioMode(item)).toBe(caso.expected.mode);
    expect(cardioTotalSeconds(item)).toBeCloseTo(caso.expected.total_seconds, 9);
    expect(cardioDescribe(item)).toBe(caso.expected.text);
    expect(cardioShortDescribe(item)).toBe(caso.expected.short_text);
  });
});

describe('editor: modos e padrões do dono', () => {
  it('opções Normal / HIIT e campos por modo vêm do JSON', () => {
    expect(CARDIO_MODE_OPTIONS.map((o) => o.value)).toEqual(motorConstants.cardio_prescription.modes);
    expect(CARDIO_MODE_OPTIONS.map((o) => o.label)).toEqual(['Normal', 'HIIT']);
    expect(cardioModeFields('hiit')).toEqual(motorConstants.cardio_prescription.mode_fields.hiit);
    expect(cardioModeFields('continuous')).not.toContain('hiit_rounds');
    expect(isCardioZoneField('hiit_high_zone')).toBe(true);
    expect(isCardioZoneField('hiit_high_seconds')).toBe(false);
  });

  it('trocar o modo preenche os padrões do novo modo e esvazia o outro', () => {
    const hiit = cardioModeChangePatch('hiit');
    expect(hiit.cardio_mode).toBe('hiit');
    expect(hiit).toMatchObject(CARDIO_DEFAULTS.hiit);
    expect(hiit.cardio_duration_minutes).toBeNull();
    expect(hiit.cardio_target_zone).toBeNull();
    const normal = cardioModeChangePatch('continuous');
    expect(normal).toMatchObject(CARDIO_DEFAULTS.continuous);
    expect(normal.hiit_rounds).toBeNull();
    expect(normal.hiit_low_zone).toBeNull();
  });

  it('cardio novo nasce no modo padrão do dono', () => {
    expect(newCardioPrescription()).toEqual(
      cardioModeChangePatch(motorConstants.cardio_prescription.default_mode as 'continuous'),
    );
  });
});

// Espelho do dono `api/app/domain/cardio_prescription.py` (M98 C0). O tempo
// total roda contra `template-time-estimate.parity.json` (casos de cardio em
// `template-time-estimate.test.ts`); aqui, só o que é deste arquivo.
describe('espelho da prescrição de cardio', () => {
  it('não tem constante própria: padrões e tetos vêm do JSON gerado', () => {
    expect(CARDIO_DEFAULTS).toBe(motorConstants.cardio_prescription.defaults);
    expect(CARDIO_LIMITS).toBe(motorConstants.cardio_prescription.limits);
  });

  it('modo ausente ou desconhecido é o padrão do dono', () => {
    expect(cardioMode({})).toBe(motorConstants.cardio_prescription.default_mode);
    expect(cardioMode({ cardio_mode: 'fartlek' })).toBe(motorConstants.cardio_prescription.default_mode);
    expect(cardioMode({ cardio_mode: ' HIIT ' })).toBe('hiit');
  });

  it('item antigo (só duração) dá o tempo de sempre', () => {
    expect(cardioTotalSeconds({ cardio_duration_minutes: 20 })).toBe(20 * motorConstants.cardio_prescription.seconds_per_minute);
  });

  it('pickCardioExtras completa com null', () => {
    expect(pickCardioExtras({ cardio_mode: 'hiit', hiit_rounds: 8 })).toEqual({
      cardio_mode: 'hiit',
      cardio_warmup_minutes: null,
      cardio_cooldown_minutes: null,
      cardio_target_zone: null,
      hiit_low_seconds: null,
      hiit_high_seconds: null,
      hiit_low_zone: null,
      hiit_high_zone: null,
      hiit_rounds: 8,
    });
  });
});
