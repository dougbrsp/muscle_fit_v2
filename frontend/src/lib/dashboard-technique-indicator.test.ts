import { describe, expect, it } from 'vitest';

import { textoTecnicaAplicada } from './dashboard-technique-indicator';

describe('textoTecnicaAplicada', () => {
  it('mostra X/N e o equivalente somado ao volume', () => {
    expect(
      textoTecnicaAplicada({ code: 'rest_pause', planned_applications: 3, performed_applications: 2, equivalent_sets: 0.75 }),
    ).toBe('Rest-pause: 2/3 aplicação(ões) · +0,75 série(s) equivalente(s)');
  });

  it('sem N (FST-7/GVT) e sem equivalente mostra só o registrado', () => {
    expect(
      textoTecnicaAplicada({ code: 'fst7', planned_applications: null, performed_applications: 0, equivalent_sets: null }),
    ).toMatch(/: 0 aplicação\(ões\)$/);
  });
});
