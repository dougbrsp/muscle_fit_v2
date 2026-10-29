// Sincronia do mapa corporal v1: o módulo TS versionado tem que ser exatamente
// o que o gerador produz a partir de public/bodymap/v1/regions.json, e os
// assets públicos têm que ser os bytes aprovados (sha256 do manifest).
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import {
  OUTPUT_FILE,
  SOURCE_FILES,
  buildRegionsModule,
  generate,
  splitByView,
  splitSubpaths,
  viewForBox,
} from './generate-bodymap-regions.mjs';

const manifest = JSON.parse(readFileSync(SOURCE_FILES.manifest, 'utf8'));
const regions = JSON.parse(readFileSync(SOURCE_FILES.regions, 'utf8'));

function sha256File(relativeToPublic) {
  const file = fileURLToPath(new URL(`../public/bodymap/v1/${relativeToPublic}`, import.meta.url));
  return createHash('sha256').update(readFileSync(file)).digest('hex');
}

describe('regions.v1.ts', () => {
  it('está em sincronia com regions.json (rode o gerador se falhar)', () => {
    const committed = readFileSync(OUTPUT_FILE, 'utf8').replace(/\r\n/g, '\n');
    expect(committed).toBe(generate());
  });

  it('separar por vista não perde nem duplica subpath', () => {
    for (const [slug, d] of Object.entries(regions.regions)) {
      const byView = splitByView(d, manifest.views);
      const pieces = Object.values(byView).flatMap((part) => splitSubpaths(part));
      expect(pieces.slice().sort(), slug).toEqual(splitSubpaths(d).slice().sort());
    }
  });

  it('cada slug fica na vista certa (frente x costas)', () => {
    const views = (slug) => Object.keys(splitByView(regions.regions[slug], manifest.views)).sort();
    expect(views('chest')).toEqual(['front']);
    expect(views('abs')).toEqual(['front']);
    expect(views('gluteal')).toEqual(['back']);
    expect(views('upper-back')).toEqual(['back']);
    expect(views('triceps')).toEqual(['back', 'front']);
  });

  it('caixa fora das duas vistas vai para a de centro mais próximo', () => {
    expect(viewForBox({ minX: 0, maxX: 10, minY: 0, maxY: 10 }, manifest.views)).toBe('front');
    expect(viewForBox({ minX: 1500, maxX: 1530, minY: 0, maxY: 10 }, manifest.views)).toBe('back');
  });

  it('recusa path com comando relativo (a caixa depende de coordenadas absolutas)', () => {
    expect(() => splitSubpaths('M0 0l10 10Z')).toThrow(/não suportado/);
  });

  it('recusa alias com path diferente do alvo', () => {
    const text = JSON.stringify({
      ...regions,
      regions: { ...regions.regions, abductors: 'M1 1L2 2Z' },
    });
    expect(() => buildRegionsModule(text, manifest)).toThrow(/alias abductors/);
  });
});

describe('assets públicos do bodymap v1', () => {
  it.each([
    ['bodymap-base.webp', 'bodymap-base.webp'],
    ['bodymap-base@768.webp', 'bodymap-base@768.webp'],
    ['regions.json', 'bodymap-regions.json'],
  ])('%s tem os bytes aprovados (sha256 do manifest)', (publicName, manifestName) => {
    expect(sha256File(publicName)).toBe(manifest.files[manifestName].sha256);
  });

  it('todos os slugs do banco e o pescoço têm região; aliases e "sem região" documentados', () => {
    for (const slug of [...manifest.slugs_db, ...manifest.slugs_extra]) {
      expect(regions.regions[slug], slug).toBeTypeOf('string');
    }
    expect(manifest.aliases).toEqual({ abductors: 'adductor' });
    expect(Object.keys(manifest.not_mapped).sort()).toEqual(['head', 'knees']);
  });
});
