// Prescrição de cardio do item do Modelo (M98 C0) — ESPELHO do dono no
// backend, `api/app/domain/cardio_prescription.py`. Sem constante própria:
// unidades, modos, padrões e tetos vêm de `motor-constants.generated.json`
// (seção `cardio_prescription`; zonas em `hr_zones`), e o tempo total roda
// contra `template-time-estimate.parity.json`; o tempo, o texto canônico e o
// resumo do item recolhido (M98 C1) contra `cardio-prescription.parity.json`
// (gerados pelo dono, `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`).
//
//   Normal: aquecimento (min) + tempo de cardio (min) + recuperação (min)
//   HIIT:   aquecimento (min) + séries × (zona alta (s) + zona baixa (s)) + recuperação (min)
//
// Item antigo (só `cardio_duration_minutes`) = Normal sem aquecimento nem
// recuperação: o tempo é o de sempre. Só exibição: a validação é do backend.

import motorConstants from './motor-constants.generated.json';

const CARDIO = motorConstants.cardio_prescription;
const ZONES = motorConstants.hr_zones;

export type CardioMode = 'continuous' | 'hiit';

/** Campos da prescrição no item do Modelo (os novos são opcionais: cliente/fixture antigo não os tem). */
export type CardioPrescriptionFields = {
  cardio_duration_minutes: number | null;
  cardio_mode?: string | null;
  cardio_warmup_minutes?: number | null;
  cardio_cooldown_minutes?: number | null;
  cardio_target_zone?: number | null;
  hiit_low_seconds?: number | null;
  hiit_high_seconds?: number | null;
  hiit_low_zone?: number | null;
  hiit_high_zone?: number | null;
  hiit_rounds?: number | null;
};

/** Valores iniciais do editor por modo (dono: `cardio_prescription.DEFAULTS`). */
export const CARDIO_DEFAULTS = CARDIO.defaults;

/** Mínimo e teto de cada campo (dono: `cardio_prescription.FIELD_LIMITS`). */
export const CARDIO_LIMITS = CARDIO.limits;

/** Campo da prescrição que tem mínimo/teto no dono. */
export type CardioLimitedField = keyof typeof CARDIO.limits;

/** Rótulo de cada campo (dono: `cardio_prescription.FIELD_LABELS`). */
export const CARDIO_FIELD_LABELS = CARDIO.field_labels;

/** Campos de cada modo, na ordem do dono (`cardio_prescription.MODE_FIELDS`). */
export function cardioModeFields(mode: CardioMode): CardioLimitedField[] {
  return CARDIO.mode_fields[mode] as CardioLimitedField[];
}

/** Opções do seletor Normal / HIIT (modos e rótulos do dono). */
export const CARDIO_MODE_OPTIONS: Array<{ value: CardioMode; label: string }> = (CARDIO.modes as CardioMode[]).map(
  (mode) => ({ value: mode, label: CARDIO.mode_labels[mode] }),
);

/** Campos de zona (Z1–Z5, vocabulário de `hr_zones`; lista do dono). */
const ZONE_FIELDS: ReadonlySet<string> = new Set(CARDIO.zone_fields);

export function isCardioZoneField(field: string): boolean {
  return ZONE_FIELDS.has(field);
}

/** Zonas do app (Z1..Z5) com o rótulo do dono `hr_zones`. */
export const HR_ZONE_OPTIONS: Array<{ zone: number; label: string }> = ZONES.zone_numbers.map((zone) => ({
  zone,
  label: ZONES.zone_labels[String(zone) as keyof typeof ZONES.zone_labels],
}));

/** Faixa aceita da FCmáx manual do perfil (dono: `hr_zones.MANUAL_MAX_HR_*`). */
export const MANUAL_MAX_HR_LIMITS = ZONES.manual_max_hr_bpm;

/** Todos os campos de valor da prescrição (os dos dois modos, sem repetir). */
const ALL_FIELDS: ReadonlyArray<CardioLimitedField> = Array.from(
  new Set(Object.values(CARDIO.mode_fields).flat() as CardioLimitedField[]),
);

/**
 * Patch do item ao trocar o modo no editor: os campos do novo modo com os
 * padrões do dono (`cardio_prescription.DEFAULTS`) e os do outro modo vazios
 * (o backend os descartaria de qualquer jeito).
 */
export function cardioModeChangePatch(mode: CardioMode): Required<CardioPrescriptionFields> {
  const patch = Object.fromEntries(ALL_FIELDS.map((field) => [field, null])) as Required<CardioPrescriptionFields>;
  return { ...patch, ...(CARDIO.defaults[mode] as Partial<CardioPrescriptionFields>), cardio_mode: mode };
}

/** Prescrição de um item de cardio novo: o modo padrão do dono com os seus padrões. */
export function newCardioPrescription(): Required<CardioPrescriptionFields> {
  return cardioModeChangePatch(CARDIO.default_mode as CardioMode);
}

function positive(value: number | null | undefined): number {
  return value != null && Number.isFinite(value) && value > 0 ? value : 0;
}

/** Modo na leitura: ausente ou desconhecido = o padrão do dono (Normal). */
export function cardioMode(item: Pick<CardioPrescriptionFields, 'cardio_mode'>): CardioMode {
  const text = item.cardio_mode == null ? '' : String(item.cardio_mode).trim().toLowerCase();
  return (CARDIO.modes.includes(text) ? text : CARDIO.default_mode) as CardioMode;
}

/** Tempo total da prescrição em segundos, sem arredondar (`total_seconds` do dono). */
export function cardioTotalSeconds(item: CardioPrescriptionFields): number {
  const perMinute = CARDIO.seconds_per_minute;
  const warmup = positive(item.cardio_warmup_minutes) * perMinute;
  const cooldown = positive(item.cardio_cooldown_minutes) * perMinute;
  const main =
    cardioMode(item) === 'hiit'
      ? positive(item.hiit_rounds) * (positive(item.hiit_high_seconds) + positive(item.hiit_low_seconds))
      : positive(item.cardio_duration_minutes) * perMinute;
  return warmup + main + cooldown;
}

// --- Texto (espelho de `describe` / `describe_short`) ----------------------

function zoneNumber(value: number | null | undefined): number | null {
  return value != null && Number.isInteger(value) && ZONES.zone_numbers.includes(value) ? value : null;
}

function zoneSuffix(value: number | null | undefined): string {
  const zone = zoneNumber(value);
  return zone != null ? ` ${ZONES.zone_labels[String(zone) as keyof typeof ZONES.zone_labels]}` : '';
}

/** Número como o dono escreve: inteiro sem casas; fração com vírgula (`:g` do Python). */
function fmt(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(6))).replace('.', ',');
}

/**
 * Texto canônico (`describe` do dono): "5 min aquec. + 20 min Z2 + 5 min
 * recuperação" ou "5 min aquec. + 5× [120 s Z4 / 120 s Z2] + 5 min recuperação".
 */
export function cardioDescribe(item: CardioPrescriptionFields): string {
  const parts: string[] = [];
  const warmup = positive(item.cardio_warmup_minutes);
  if (warmup) parts.push(`${fmt(warmup)} min aquec.`);
  if (cardioMode(item) === 'hiit') {
    parts.push(
      `${fmt(positive(item.hiit_rounds))}× [${fmt(positive(item.hiit_high_seconds))} s${zoneSuffix(item.hiit_high_zone)}` +
        ` / ${fmt(positive(item.hiit_low_seconds))} s${zoneSuffix(item.hiit_low_zone)}]`,
    );
  } else {
    const duration = positive(item.cardio_duration_minutes);
    if (duration) parts.push(`${fmt(duration)} min${zoneSuffix(item.cardio_target_zone)}`);
  }
  const cooldown = positive(item.cardio_cooldown_minutes);
  if (cooldown) parts.push(`${fmt(cooldown)} min recuperação`);
  return parts.join(' + ');
}

/**
 * Resumo do item recolhido (`describe_short` do dono):
 * "Normal 20 min Z2 · 5+5 min" ou "HIIT 8× 30 s Z4 / 90 s Z2 · 5+5 min".
 */
export function cardioShortDescribe(item: CardioPrescriptionFields): string {
  const mode = cardioMode(item);
  const main =
    mode === 'hiit'
      ? `${fmt(positive(item.hiit_rounds))}× ${fmt(positive(item.hiit_high_seconds))} s${zoneSuffix(item.hiit_high_zone)}` +
        ` / ${fmt(positive(item.hiit_low_seconds))} s${zoneSuffix(item.hiit_low_zone)}`
      : `${fmt(positive(item.cardio_duration_minutes))} min${zoneSuffix(item.cardio_target_zone)}`;
  let text = `${CARDIO.mode_labels[mode]} ${main}`;
  const warmup = positive(item.cardio_warmup_minutes);
  const cooldown = positive(item.cardio_cooldown_minutes);
  if (warmup || cooldown) text += ` · ${fmt(warmup)}+${fmt(cooldown)} min`;
  return text;
}

/** Campos NOVOS da prescrição (migração 095): tudo menos a duração da 007. */
export type CardioPrescriptionExtras = Omit<CardioPrescriptionFields, 'cardio_duration_minutes'>;

/**
 * Campos novos do item, com `null` no que falta — o PUT do Modelo é
 * replace-all dos itens: sem repassar estes campos, um save do editor atual
 * apagaria a prescrição gravada (aquecimento, zona, HIIT).
 */
export function pickCardioExtras(item: CardioPrescriptionExtras): Required<CardioPrescriptionExtras> {
  return {
    cardio_mode: item.cardio_mode ?? null,
    cardio_warmup_minutes: item.cardio_warmup_minutes ?? null,
    cardio_cooldown_minutes: item.cardio_cooldown_minutes ?? null,
    cardio_target_zone: item.cardio_target_zone ?? null,
    hiit_low_seconds: item.hiit_low_seconds ?? null,
    hiit_high_seconds: item.hiit_high_seconds ?? null,
    hiit_low_zone: item.hiit_low_zone ?? null,
    hiit_high_zone: item.hiit_high_zone ?? null,
    hiit_rounds: item.hiit_rounds ?? null,
  };
}
