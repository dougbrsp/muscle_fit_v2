// Modo de contagem do volume por Modelo (migração 073, Lote 3 — telas).
//
// Os VALORES, rótulos e a régua de cada modo NÃO são escritos aqui. O dono é
// `api/app/intelligence/methodology_volume.py` (`VolumeCountingMode`,
// `VOLUME_COUNTING_MODE_LABELS`, `COUNTED_SETS_UNIT_LABELS`,
// `counting_mode_description`), exportado para `motor-constants.generated.json`
// por `api/tests/unit/test_m76_lote1b_espelhos_fe.py`. A guarda
// (`modo_contagem_volume`) recusa o literal do modo em .ts/.tsx: a tela usa
// `DEFAULT_COUNTING_MODE`/`COUNTING_MODES` daqui ou o `counting_mode` do payload.

import motorConstants from './motor-constants.generated.json';

/** Valor de `VolumeCountingMode` do motor (a API valida; o FE não enumera). */
export type VolumeCountingMode = string;

export type CountingModeOption = {
  value: VolumeCountingMode;
  label: string;
  /** A régua do modo em uma frase (limiar interpolado no backend). */
  description: string;
  /** "séries diretas" / "séries ponderadas": a unidade do número contado. */
  sets_unit_label: string;
  sets_unit_label_singular: string;
};

const SECTION = motorConstants.methodology_volume;

export const COUNTING_MODES: ReadonlyArray<CountingModeOption> = SECTION.counting_modes;
export const DEFAULT_COUNTING_MODE: VolumeCountingMode = SECTION.default_counting_mode;

/**
 * Rótulo do balde dos grupos sem macro-grupo resolvido. Dono:
 * `methodology_volume.VOLUME_CLASS_UNCLASSIFIED_LABEL` (guarda `rotulo_sem_macro_grupo`).
 */
export const UNCLASSIFIED_VOLUME_CLASS_LABEL: string = SECTION.unclassified_volume_class_label;

const DEFAULT_OPTION = COUNTING_MODES.find((mode) => mode.value === DEFAULT_COUNTING_MODE) ?? COUNTING_MODES[0];

/** Opção do modo; ausente/desconhecido (payload anterior à v5) = o default. */
export function countingModeOption(value: string | null | undefined): CountingModeOption {
  return COUNTING_MODES.find((mode) => mode.value === value) ?? DEFAULT_OPTION;
}

export function isDefaultCountingMode(value: string | null | undefined): boolean {
  return countingModeOption(value).value === DEFAULT_COUNTING_MODE;
}

/** Normaliza o que veio da API/rascunho para um valor conhecido (default se ausente). */
export function normalizeCountingMode(value: string | null | undefined): VolumeCountingMode {
  return countingModeOption(value).value;
}

/** Unidade das séries do modo: plural ("séries ponderadas") e singular. */
export function setsUnitOf(value: string | null | undefined): { plural: string; singular: string } {
  const option = countingModeOption(value);
  return { plural: option.sets_unit_label, singular: option.sets_unit_label_singular };
}

/** `data` do Select do editor e da aba admin. */
export const COUNTING_MODE_SELECT_DATA: ReadonlyArray<{ value: string; label: string }> = COUNTING_MODES.map(
  (mode) => ({ value: mode.value, label: mode.label }),
);
