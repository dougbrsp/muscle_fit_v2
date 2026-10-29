import { describe, expect, it } from 'vitest';
import motorConstants from './motor-constants.generated.json';
import {
  isMeasurementSite,
  MEASUREMENT_SITE_LABELS,
  MEASUREMENT_SITE_OPTIONS,
  MEASUREMENT_SITE_SIDES,
  MEASUREMENT_SITES,
  measurementSiteLabel,
  WAIST_SITE,
} from './body-measurement-sites';

// Paridade com o dono `api/app/domain/body_measurement_sites.py`: a lib só
// repassa o bloco gerado (`body_measurement_sites`), sem texto próprio. O
// Python (`test_m76_lote1b_espelhos_fe.py`) garante que o JSON é o do dono.
const BLOCK = motorConstants.body_measurement_sites;

describe('locais de medida por fita (espelho do dono)', () => {
  it('lista, rótulos, lados e cintura são exatamente os do JSON gerado', () => {
    expect([...MEASUREMENT_SITES]).toEqual(BLOCK.sites);
    expect(MEASUREMENT_SITE_LABELS).toEqual(BLOCK.labels);
    expect(MEASUREMENT_SITE_SIDES).toEqual(BLOCK.sides);
    expect(WAIST_SITE).toBe(BLOCK.waist_site);
  });

  it('todo local tem rótulo único e as opções seguem a ordem do ENUM', () => {
    expect(MEASUREMENT_SITES.length).toBeGreaterThanOrEqual(12);
    expect(new Set(MEASUREMENT_SITES).size).toBe(MEASUREMENT_SITES.length);
    for (const site of MEASUREMENT_SITES) {
      expect(MEASUREMENT_SITE_LABELS[site]).toBeTruthy();
    }
    expect(new Set(Object.values(MEASUREMENT_SITE_LABELS)).size).toBe(MEASUREMENT_SITES.length);
    expect(MEASUREMENT_SITE_OPTIONS.map((o) => o.value)).toEqual([...MEASUREMENT_SITES]);
    expect(MEASUREMENT_SITE_OPTIONS[0]).toEqual({ value: WAIST_SITE, label: MEASUREMENT_SITE_LABELS[WAIST_SITE] });
  });

  it('o lado acompanha o sufixo _l/_r e os centrais não têm lado', () => {
    for (const site of MEASUREMENT_SITES) {
      if (site.endsWith('_l')) expect(MEASUREMENT_SITE_SIDES[site]).toBe('left');
      else if (site.endsWith('_r')) expect(MEASUREMENT_SITE_SIDES[site]).toBe('right');
      else expect(MEASUREMENT_SITE_SIDES[site]).toBeUndefined();
    }
  });

  it('os locais da 075 (pescoço e antebraços) entram no fim, depois dos da 047', () => {
    const added = ['neck', 'forearm_l', 'forearm_r'];
    expect(MEASUREMENT_SITES.slice(-3)).toEqual(added);
    for (const site of added) expect(isMeasurementSite(site)).toBe(true);
  });

  it('valida e rotula sem lançar', () => {
    expect(isMeasurementSite('calf_r')).toBe(true);
    expect(isMeasurementSite('belly')).toBe(false);
    expect(isMeasurementSite(null)).toBe(false);
    expect(measurementSiteLabel('neck')).toBe(BLOCK.labels.neck);
    expect(measurementSiteLabel('belly')).toBe('belly');
  });
});
