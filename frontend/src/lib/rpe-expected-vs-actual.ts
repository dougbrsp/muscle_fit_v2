// Apresentação do RPE esperado x realizado (#182). A comparação (status, rótulo,
// diferença, contagens) vem PRONTA da API; aqui só se monta texto.

export type RpeVsExpected = {
  status: 'below' | 'on_target' | 'above';
  label: string;
  delta: number;
} | null;

export type RpeVsExpectedCounts = {
  below: number;
  on_target: number;
  above: number;
  compared: number;
} | null;

const fmt = (value: number | string) => Number(value).toLocaleString('pt-BR', { maximumFractionDigits: 1 });

/** Sufixo da linha de resumo: " (esperado 9 · Abaixo do esperado)"; "" sem esperado. */
export function rpeExpectedSuffix(set: {
  rpe_target_planned?: number | string | null;
  rpe_vs_expected?: RpeVsExpected | undefined;
}): string {
  if (set.rpe_target_planned == null) return '';
  const label = set.rpe_vs_expected?.label;
  return ` (esperado ${fmt(set.rpe_target_planned)}${label ? ` · ${label}` : ''})`;
}

/** Rótulos prontos da API (`rpe_vs_expected_labels`); ausentes em resposta antiga em cache. */
export type RpeVsExpectedLabels = { below: string; on_target: string; above: string } | null;

// Reserva para resposta antiga sem `rpe_vs_expected_labels`.
const COUNT_LABELS: ReadonlyArray<readonly [keyof NonNullable<RpeVsExpectedLabels>, string]> = [
  ['below', 'abaixo'],
  ['on_target', 'no esperado'],
  ['above', 'acima'],
];

/** "2 séries comparadas: 1 no esperado, 1 acima"; null sem contagem ou sem série comparada. */
export function rpeExpectedCountsText(
  counts: RpeVsExpectedCounts | undefined,
  labels?: RpeVsExpectedLabels,
): string | null {
  if (!counts || !counts.compared) return null;
  const parts = COUNT_LABELS.filter(([key]) => counts[key] > 0).map(
    ([key, fallback]) => `${counts[key]} ${labels?.[key]?.toLowerCase() ?? fallback}`,
  );
  const noun = counts.compared === 1 ? 'série comparada' : 'séries comparadas';
  return `${counts.compared} ${noun}${parts.length ? `: ${parts.join(', ')}` : ''}`;
}
