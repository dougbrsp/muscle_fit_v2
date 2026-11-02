// Eco da série gravada pelo servidor (criação e replay por `client_key`). Os
// campos podem faltar em servidor mais antigo: ausência NUNCA vira conflito.
// Só compara; não interpreta nem recalcula nada.

export type SetEcho = {
  duplicate?: boolean;
  reps?: number | null;
  weight_kg?: number | string | null;
  set_type?: string | null;
  rpe?: number | string | null;
  joint_pain?: boolean | number | null;
  muscle_pain?: number | null;
};

const COMPARED: ReadonlyArray<{ key: 'reps' | 'weight_kg' | 'set_type' | 'rpe'; label: string }> = [
  { key: 'reps', label: 'repetições' },
  { key: 'weight_kg', label: 'carga' },
  { key: 'set_type', label: 'tipo da série' },
  { key: 'rpe', label: 'RPE' },
];

function same(a: unknown, b: unknown): boolean {
  const aNull = a === null || a === undefined || a === '';
  const bNull = b === null || b === undefined || b === '';
  if (aNull || bNull) return aNull === bNull;
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) return Math.abs(na - nb) < 1e-9;
  return String(a) === String(b);
}

/**
 * Num replay (`duplicate: true`) o servidor devolve os valores GRAVADOS. Se
 * algum campo presente no eco e no envio difere, a chave já existia com outro
 * conteúdo: devolve o texto do conflito; senão `null`.
 */
export function describeEchoMismatch(payload: Record<string, unknown>, response: unknown): string | null {
  const echo = response as (SetEcho & Record<string, unknown>) | null;
  if (!echo || typeof echo !== 'object' || echo.duplicate !== true) return null;
  const diffs: string[] = [];
  for (const { key, label } of COMPARED) {
    if (!(key in echo) || echo[key] === undefined) continue;
    if (payload[key] === undefined) continue;
    if (!same(payload[key], echo[key])) diffs.push(`${label}: aqui ${String(payload[key])}, no servidor ${String(echo[key])}`);
  }
  return diffs.length > 0 ? `Esta série já existe no servidor com valores diferentes (${diffs.join('; ')}).` : null;
}
