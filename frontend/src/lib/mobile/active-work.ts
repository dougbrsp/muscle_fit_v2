/**
 * Contrato `hasActiveWork()` (plano mobile, Frente C): "há sessão de treino
 * ativa ou operações offline pendentes?". O Service Worker/atualização do PWA
 * (frente C) só aplica uma nova versão quando devolve `false`.
 *
 * - Quem IMPLEMENTA (frente B: persistência/fila e quem mais tiver trabalho a
 *   proteger) registra provedores com `registerActiveWorkProvider`.
 * - Quem CONSOME é a frente C (`hasActiveWork()`).
 * - Vários provedores: o resultado é o OR de todos.
 * - Sem provedor registrado, devolve `false`: por isso a atualização do SW não
 *   pode ser ligada em produção antes de a frente B registrar o provedor.
 * - Falha ou demora de qualquer provedor é tratada como "há trabalho" (`true`):
 *   na dúvida, não recarregar a página e arriscar perder dados.
 */

export type ActiveWorkProvider = () => boolean | Promise<boolean>;

/** Valor de UX (não é regra de negócio): quanto esperar um provedor antes de assumir "há trabalho". */
export const ACTIVE_WORK_PROVIDER_TIMEOUT_MS = 2000;

const providers = new Set<ActiveWorkProvider>();

/** Registra um provedor; devolve a função que o remove. */
export function registerActiveWorkProvider(fn: ActiveWorkProvider): () => void {
  providers.add(fn);
  return () => {
    providers.delete(fn);
  };
}

/** Há ao menos um provedor registrado? Quem precisa de "sem provedor = desconhecido" (ex.: atualização do SW) usa isto. */
export function hasActiveWorkProviders(): boolean {
  return providers.size > 0;
}

async function askProvider(fn: ActiveWorkProvider): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<boolean>((resolve) => {
    timer = setTimeout(() => resolve(true), ACTIVE_WORK_PROVIDER_TIMEOUT_MS);
  });
  try {
    const answer = Promise.resolve().then(fn).then((v) => v === true);
    return await Promise.race([answer, timeout]);
  } catch {
    return true;
  } finally {
    clearTimeout(timer);
  }
}

export async function hasActiveWork(): Promise<boolean> {
  const current = Array.from(providers);
  if (current.length === 0) return false;
  const answers = await Promise.all(current.map(askProvider));
  return answers.some(Boolean);
}
