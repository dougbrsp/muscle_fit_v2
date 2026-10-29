// Locais de medida por fita métrica — ESPELHO do dono
// `api/app/domain/body_measurement_sites.py` (nutrição L0, 2026-10-15).
//
// Nenhum texto à mão: a lista (ordem do ENUM das migrações 047 + 075), os
// rótulos pt-BR e o lado dos locais bilaterais vêm do bloco
// `body_measurement_sites` de `motor-constants.generated.json`, gerado por
// `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`. Local ou rótulo
// novo muda NO DONO e se regenera; a guarda `rotulo_site_medida` recusa rótulo
// de local escrito à mão em qualquer .ts/.tsx fora deste JSON.

import motorConstants from './motor-constants.generated.json';

const BLOCK = motorConstants.body_measurement_sites;

/** Código de um local (`waist`, `hip`, ..., `forearm_r`). */
export type MeasurementSite = string;

export type MeasurementSide = 'left' | 'right';

/** Locais na ordem canônica do ENUM (a 075 acrescentou os três últimos no fim). */
export const MEASUREMENT_SITES: readonly MeasurementSite[] = BLOCK.sites;

export const MEASUREMENT_SITE_LABELS: Readonly<Record<MeasurementSite, string>> = BLOCK.labels;

/** Lado dos locais bilaterais (`*_l`/`*_r`); locais centrais não aparecem. */
export const MEASUREMENT_SITE_SIDES: Readonly<Record<MeasurementSite, MeasurementSide>> =
  BLOCK.sides as Readonly<Record<MeasurementSite, MeasurementSide>>;

/** Local com papel próprio nos motores (sinal de recomposição). */
export const WAIST_SITE: MeasurementSite = BLOCK.waist_site;

export const MEASUREMENT_SITE_OPTIONS: ReadonlyArray<{ value: MeasurementSite; label: string }> =
  MEASUREMENT_SITES.map((site) => ({ value: site, label: MEASUREMENT_SITE_LABELS[site] ?? site }));

export function isMeasurementSite(value: string | null | undefined): value is MeasurementSite {
  return value != null && MEASUREMENT_SITES.includes(value);
}

/** Rótulo do local; o próprio código quando desconhecido (nunca lança). */
export function measurementSiteLabel(site: string): string {
  return MEASUREMENT_SITE_LABELS[site] ?? site;
}
