import type { InsufficientTargetVolumeGroup } from '@/lib/workout-plans-types';

/**
 * Formatacao dos avisos de geracao (`GenerationWarnings.tsx`).
 *
 * Mora em `lib/` e nao dentro do componente por um motivo unico: e a camada
 * que NAO pode confiar no que o servidor mandou, entao precisa de teste
 * (vitest roda em `environment: "node"`, sem DOM — so lib pura).
 *
 * Regra desta camada: nenhum `.toFixed()` direto em valor vindo do servidor.
 * Campo ausente/nulo/NaN nao derruba a tela — no maximo o numero some.
 */

/** `toFixed` que nunca lanca: devolve null para tudo que nao for numero finito. */
export function formatSets(value: unknown, digits = 1): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return value.toFixed(digits);
}

/**
 * Uma linha de "Volume-alvo nao atingido". Nunca lanca e nunca devolve string
 * vazia: no pior caso sobra so o nome do grupo.
 *
 * Ordem de preferencia dos numeros:
 *  1. `achieved`/`target` (contrato completo — legado e mode_a atual);
 *  2. `deficit_equivalent_sets` (fichas geradas pelo mode_a no periodo em que
 *     ele so emitia o deficit);
 *  3. nenhum numero.
 */
export function formatInsufficientVolumeGroup(group: InsufficientTargetVolumeGroup | null | undefined): string {
  const name = (group?.muscle_primary ?? '').trim() || 'Grupo muscular';
  const achieved = formatSets(group?.equivalent_sets_achieved);
  const target = formatSets(group?.equivalent_sets_target);
  const deficit = formatSets(group?.deficit_equivalent_sets);
  const reason = (group?.reason ?? '').trim();

  let detail: string | null = null;
  if (achieved !== null && target !== null) {
    detail = `${achieved}/${target} séries equivalentes`;
  } else if (achieved !== null) {
    detail = `${achieved} séries equivalentes`;
  } else if (deficit !== null) {
    detail = `déficit de ${deficit} séries equivalentes`;
  }

  const parts = [name, detail].filter((part): part is string => Boolean(part));
  const head = parts.length > 1 ? `${parts[0]}: ${parts[1]}` : parts[0];
  return reason ? `${head} — ${reason}` : head;
}
