/**
 * Contrato `hasActiveWork()` (plano mobile, Frente C): "há sessão de treino
 * ativa ou operações offline pendentes?". O Service Worker/atualização do PWA
 * (frente C) só aplica uma nova versão quando devolve `false`.
 *
 * - Quem IMPLEMENTA é a frente B (persistência/fila), registrando um provedor.
 * - Quem CONSOME é a frente C (`hasActiveWork()`).
 * - Sem provedor registrado, devolve `false`: por isso a atualização do SW não
 *   pode ser ligada em produção antes de a frente B registrar o provedor.
 * - Falha do provedor é tratada como "há trabalho" (`true`): na dúvida, não
 *   recarregar a página e arriscar perder dados.
 */

export type ActiveWorkProvider = () => boolean | Promise<boolean>;

let provider: ActiveWorkProvider | null = null;

/** Registra o provedor; devolve a função que o remove (só se ainda for o atual). */
export function registerActiveWorkProvider(fn: ActiveWorkProvider): () => void {
  provider = fn;
  return () => {
    if (provider === fn) provider = null;
  };
}

export async function hasActiveWork(): Promise<boolean> {
  const current = provider;
  if (!current) return false;
  try {
    return (await current()) === true;
  } catch {
    return true;
  }
}
