/**
 * Fuso horário do usuário (`users.timezone`, migração 044 — Novo Dashboard F0.1).
 *
 * Lista curta e intencional: os fusos do Brasil + UTC. A validação real do
 * nome (qualquer IANA) é do backend (`api/app/timezones.py`); aqui só
 * oferecemos as opções do `Select` de `/perfil` e um fallback seguro para
 * renderizar um valor que a API nunca devolve vazio, mas que um estado
 * local ainda não carregado pode ter.
 */

export const DEFAULT_TIMEZONE = 'America/Sao_Paulo';

export const TIMEZONE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'America/Sao_Paulo', label: 'Brasília (America/Sao_Paulo)' },
  { value: 'America/Manaus', label: 'Manaus (America/Manaus)' },
  { value: 'America/Belem', label: 'Belém (America/Belem)' },
  { value: 'America/Fortaleza', label: 'Fortaleza (America/Fortaleza)' },
  { value: 'America/Recife', label: 'Recife (America/Recife)' },
  { value: 'America/Bahia', label: 'Bahia (America/Bahia)' },
  { value: 'America/Cuiaba', label: 'Cuiabá (America/Cuiaba)' },
  { value: 'America/Rio_Branco', label: 'Rio Branco (America/Rio_Branco)' },
  { value: 'America/Noronha', label: 'Fernando de Noronha (America/Noronha)' },
  { value: 'UTC', label: 'UTC' },
];

const KNOWN = new Set(TIMEZONE_OPTIONS.map((option) => option.value));

/** `true` se `value` está na lista curta oferecida pelo `Select`. */
export function isKnownTimezone(value: unknown): value is string {
  return typeof value === 'string' && KNOWN.has(value);
}

/**
 * Valor sempre renderizável pelo `Select`: mantém um fuso da lista; qualquer
 * outra coisa (null, vazio, fuso fora da lista curta) cai no default.
 * Um fuso válido mas fora da lista (ex.: gravado por script) é preservado
 * apenas se `keepUnknown` for true — o `/perfil` prefere o default para não
 * mostrar um `Select` sem opção selecionada.
 */
export function normalizeTimezone(value: unknown, keepUnknown = false): string {
  if (isKnownTimezone(value)) return value;
  if (keepUnknown && typeof value === 'string' && value.trim()) return value.trim();
  return DEFAULT_TIMEZONE;
}
