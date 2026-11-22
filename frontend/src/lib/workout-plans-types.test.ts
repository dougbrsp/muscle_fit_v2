import { describe, expect, it } from 'vitest';
import {
  PRESCRIPTION_WARNING_COLORS,
  PRESCRIPTION_WARNING_LABELS,
  PRESCRIPTION_WARNING_TOOLTIPS,
  type PrescriptionWarning,
} from './workout-plans-types';

describe('avisos da prescrição', () => {
  it('TECHNIQUE_REMOVED_NO_DIRECT_WORK tem rótulo, tooltip e cor próprios', () => {
    const code = 'TECHNIQUE_REMOVED_NO_DIRECT_WORK' as PrescriptionWarning;
    expect(PRESCRIPTION_WARNING_LABELS[code]).toBe('Técnica retirada hoje');
    expect(PRESCRIPTION_WARNING_TOOLTIPS[code]).toContain('retirada');
    expect(PRESCRIPTION_WARNING_COLORS[code]).toBe('blue');
  });

  it('todo código tem rótulo, tooltip e cor, e os mapas têm as mesmas chaves', () => {
    const codes = Object.keys(PRESCRIPTION_WARNING_LABELS).sort();
    expect(Object.keys(PRESCRIPTION_WARNING_TOOLTIPS).sort()).toEqual(codes);
    expect(Object.keys(PRESCRIPTION_WARNING_COLORS).sort()).toEqual(codes);
    for (const c of codes) {
      expect(PRESCRIPTION_WARNING_LABELS[c as PrescriptionWarning]).not.toBe(c);
    }
  });
});
