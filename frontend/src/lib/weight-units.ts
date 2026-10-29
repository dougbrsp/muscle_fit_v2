// Unidade de carga: quilos (canônico) x libras (entrada/exibição opcional).
//
// REGRA ÚNICA DO SISTEMA: o banco guarda SEMPRE quilos. `exercise_sets.weight_kg`
// é `DECIMAL(8,2)` em KG e continua sendo carga TOTAL (decisão D12). A
// preferência por libras (`users.preferred_weight_unit`, migração 040) é só de
// ENTRADA e EXIBIÇÃO — nenhum endpoint recebe libras e nenhum motor
// (progressão, volume, relatórios, IA) conhece a unidade.
//
// A conversão acontece NESTA BORDA. Módulo puro e sem dependências de propósito:
// pode ser importado por qualquer tela (SetLoggerCard, briefing pré-exercício,
// histórico) sem arrastar React, react-query ou tipos de domínio junto.
// Espelho servidor: `api/app/weight_units.py` (mesmo fator, mesmas casas).
//
// ----------------------------------------------------------------------------
// ARREDONDAMENTO — a decisão que evita "22.6796 lb" na tela
// ----------------------------------------------------------------------------
// - kg: 2 casas. É exatamente a precisão da coluna `DECIMAL(8,2)`; mandar mais
//   casas seria mentira, porque o banco truncaria.
// - lb: 1 casa. NÃO é estética, é o que torna o IDA-E-VOLTA ESTÁVEL:
//     erro máximo de quantizar kg em 2 casas = 0.005 kg = 0.011 lb
//     meia casa que decide o arredondamento em 1 casa em lb = 0.05 lb
//     0.011 < 0.05  =>  o valor digitado em lb volta IDÊNTICO da ida ao banco.
//   Logo um mesmo peso pode ser salvo e reeditado infinitas vezes sem derivar
//   (ver `weight-units.test.ts`, que prova isso exaustivamente). Com 2 casas em
//   lb a garantia cairia (0.011 > 0.005) e "1.00 lb" viraria "0.99 lb" já na
//   primeira reedição.
//
// Observação honesta sobre o caminho inverso: um peso que nasceu em kg e é
// MOSTRADO em lb encosta na grade de 1 casa de lb (ex.: 22.70 kg -> 50.0 lb ->
// 22.68 kg se for regravado). Isso é quantização de exibição, inerente a
// qualquer conversão, e fica abaixo de 0.03 kg — menos que o menor incremento
// de anilha existente. O que NÃO pode acontecer, e não acontece, é o valor
// andar um pouco a cada edição.

export type WeightUnit = 'kg' | 'lb';

export const WEIGHT_UNITS: readonly WeightUnit[] = ['kg', 'lb'] as const;
export const DEFAULT_WEIGHT_UNIT: WeightUnit = 'kg';

// 1 lb = 0.45359237 kg — exato por definição internacional da libra
// avoirdupois (1959), não é aproximação.
export const LB_IN_KG = 0.45359237;

export const DISPLAY_DECIMALS: Record<WeightUnit, number> = { kg: 2, lb: 1 };

// Incrementos dos botões +/- do card de série, por unidade, quando o
// equipamento NÃO informa o passo. Em lb são valores NATIVOS da unidade
// (5 lb / 0.5 lb), não a conversão de 2.5 kg / 0.25 kg. `fine` é o passo das
// sub-etapas do modo avançado (ajuste fino de UX, não regra de carga),
// `coarse` é o do modo simples. Com o passo do equipamento vindo da API, o
// modo simples usa ele (ver `loadStepperStep`).
export const WEIGHT_STEP: Record<WeightUnit, { coarse: number; fine: number }> = {
  kg: { coarse: 2.5, fine: 0.25 },
  lb: { coarse: 5, fine: 0.5 },
};

/**
 * Passo do stepper de carga do modo simples (M76, C7): o passo REAL do
 * equipamento (`load_increment_kg`, em kg, vindo da API), convertido para a
 * unidade de exibição. Em lb ele é o equivalente do passo do equipamento
 * (2,5 kg -> 5,5 lb; 2,27 kg -> 5 lb): é o que a anilha daquele aparelho
 * realmente soma. Sem passo informado (API antiga, treino sem equipamento),
 * vale o `coarse` da unidade.
 */
export function loadStepperStep(unit: WeightUnit, loadIncrementKg?: number | null): number {
  const target = normalizeWeightUnit(unit);
  if (loadIncrementKg == null || !Number.isFinite(loadIncrementKg) || loadIncrementKg <= 0) {
    return WEIGHT_STEP[target].coarse;
  }
  const step = fromKg(loadIncrementKg, target);
  return step > 0 ? step : WEIGHT_STEP[target].coarse;
}

export const WEIGHT_UNIT_LABEL: Record<WeightUnit, string> = { kg: 'kg', lb: 'lb' };

export function isWeightUnit(value: unknown): value is WeightUnit {
  return typeof value === 'string' && (WEIGHT_UNITS as readonly string[]).includes(value.trim().toLowerCase());
}

/** Unidade válida a partir de qualquer entrada (API, localStorage, querystring).
 *  Desconhecido/ausente -> 'kg', o default da coluna 040. */
export function normalizeWeightUnit(value: unknown): WeightUnit {
  return isWeightUnit(value) ? (value.trim().toLowerCase() as WeightUnit) : DEFAULT_WEIGHT_UNIT;
}

function roundTo(value: number, decimals: number): number {
  if (!Number.isFinite(value)) return 0;
  const factor = 10 ** decimals;
  // `Math.round` puro erra em casos como 1.005 por representação binária; o
  // epsilon relativo corrige sem afetar valores exatos.
  return Math.round((value + Number.EPSILON * Math.abs(value)) * factor) / factor;
}

/** Valor digitado NA UNIDADE `unit` -> quilos, já quantizado em 2 casas (o que
 *  `exercise_sets.weight_kg` aceita). ÚNICA função que produz o número enviado
 *  ao backend. */
export function toKg(value: number, unit: WeightUnit): number {
  const amount = normalizeWeightUnit(unit) === 'lb' ? value * LB_IN_KG : value;
  return roundTo(amount, DISPLAY_DECIMALS.kg);
}

/** Quilos vindos do backend -> número para MOSTRAR/EDITAR na unidade `unit`,
 *  já arredondado para as casas daquela unidade (kg: 2, lb: 1). */
export function fromKg(weightKg: number, unit: WeightUnit): number {
  const target = normalizeWeightUnit(unit);
  const amount = target === 'lb' ? weightKg / LB_IN_KG : weightKg;
  return roundTo(amount, DISPLAY_DECIMALS[target]);
}

/** Número já arredondado, sem zeros à direita ("22.5", "50"). */
export function formatWeightValue(weightKg: number, unit: WeightUnit): string {
  const target = normalizeWeightUnit(unit);
  return String(fromKg(weightKg, target));
}

/** Texto curto para a tela: "22.5 kg" / "50 lb". `space: false` -> "22.5kg",
 *  formato já usado no modo treino. */
export function formatWeight(weightKg: number, unit: WeightUnit, options?: { space?: boolean }): string {
  const target = normalizeWeightUnit(unit);
  const separator = options?.space === false ? '' : ' ';
  return `${formatWeightValue(weightKg, target)}${separator}${WEIGHT_UNIT_LABEL[target]}`;
}

// ----------------------------------------------------------------------------
// Estado do campo de carga (o modelo que o `SetLoggerCard` usa)
// ----------------------------------------------------------------------------
// O campo guarda DUAS coisas: o `display`, que é o número na unidade atual (o
// que o usuário digita e lê), e o `kg`, que é o valor CANÔNICO — o único que
// chega ao backend.
//
// Por que um kg âncora em vez de reconverter o `display` a cada troca de
// unidade: 2.5 kg -> 5.5 lb -> 2.49 kg. Reconverter faria o número "andar"
// só de o usuário espiar a outra unidade e voltar. Com a âncora, trocar a
// unidade é ESTRITAMENTE uma mudança de exibição: o peso não muda, e alternar
// mil vezes devolve exatamente o valor original.
//
// O `kg` só é recalculado quando o usuário DIGITA — aí o que ele digitou é a
// verdade, e a âncora passa a ser `toKg(display, unit)`.

export type WeightFieldState = {
  /** Valor canônico em kg — o que `onSave` envia. `null` = campo vazio. */
  kg: number | null;
  /** Valor exibido/editado, na unidade `unit`. */
  display: number | null;
  unit: WeightUnit;
};

/** Campo iniciado a partir de um valor do backend (sempre kg). */
export function weightFieldFromKg(kg: number | null, unit: WeightUnit): WeightFieldState {
  const target = normalizeWeightUnit(unit);
  if (kg == null || !Number.isFinite(kg)) return { kg: null, display: null, unit: target };
  const canonical = toKg(kg, 'kg');
  return { kg: canonical, display: fromKg(canonical, target), unit: target };
}

/** O usuário digitou: o número digitado vira a verdade e reancora o kg. */
export function weightFieldOnInput(state: WeightFieldState, display: number | null): WeightFieldState {
  if (display == null || !Number.isFinite(display)) return { ...state, kg: null, display: null };
  return { ...state, display, kg: toKg(display, state.unit) };
}

/** O usuário (ou a preferência vinda do servidor) trocou a unidade: só a
 *  EXIBIÇÃO muda; o kg canônico fica intacto. */
export function weightFieldOnUnitChange(state: WeightFieldState, unit: WeightUnit): WeightFieldState {
  const target = normalizeWeightUnit(unit);
  if (target === state.unit) return state;
  return { kg: state.kg, display: state.kg == null ? null : fromKg(state.kg, target), unit: target };
}

/** Alvo de carga da prescrição (`load_target`, texto livre do tipo "40kg" ou
 *  "40-45") reescrito na unidade preferida. Só reescreve quando o texto é UM
 *  número (com sufixo de unidade opcional); qualquer outra coisa — faixa,
 *  "corporal", vazio — volta como veio, porque adivinhar seria pior que não
 *  converter. `null` de entrada continua `null`. */
export function formatLoadTarget(loadTarget: string | null | undefined, unit: WeightUnit): string | null {
  if (loadTarget == null) return null;
  const raw = String(loadTarget).trim();
  if (!raw) return raw;
  const match = raw.replace(',', '.').match(/^(\d+(?:\.\d+)?)\s*(kg|lb)?$/i);
  if (!match) return raw;
  const sourceUnit = normalizeWeightUnit(match[2]);
  const kg = toKg(Number(match[1]), sourceUnit);
  return formatWeight(kg, unit, { space: false });
}
