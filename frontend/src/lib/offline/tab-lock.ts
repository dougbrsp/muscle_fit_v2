// Um único escritor por usuário entre abas (Web Locks). Duas abas do Modo Treino
// gravando o mesmo espelho/fila se sobrescreveriam: a primeira vira "dona" e a
// segunda só avisa e espera. Sem suporte a Web Locks (navegadores antigos) não há
// como garantir isso: o app segue como dono, sem proteção entre abas (limite
// documentado; não é regra de treino).

export type WriterRole = 'owner' | 'follower';

export type WriterLock = {
  role: WriterRole;
  /** Resolve quando esta aba se torna dona (já é dona = resolvida). */
  whenOwner: Promise<void>;
};

type LocksApi = {
  request: (
    name: string,
    options: { ifAvailable: true } | Record<string, never>,
    callback: (lock: unknown) => Promise<unknown> | unknown,
  ) => Promise<unknown>;
};

let heldFor: number | null = null;

function locksApi(): LocksApi | null {
  if (typeof navigator === 'undefined') return null;
  const api = (navigator as unknown as { locks?: LocksApi }).locks;
  return api && typeof api.request === 'function' ? api : null;
}

export const lockNameFor = (userId: number) => `musclefit-offline-${userId}`;

/** Tenta ser o escritor do usuário; o lock é mantido até a aba fechar. */
export function acquireWriterLock(userId: number): Promise<WriterLock> {
  const locks = locksApi();
  if (!locks || heldFor === userId) return Promise.resolve({ role: 'owner', whenOwner: Promise.resolve() });
  const name = lockNameFor(userId);
  return new Promise((resolve) => {
    void locks.request(name, { ifAvailable: true }, (lock) => {
      if (!lock) {
        const whenOwner = new Promise<void>((becameOwner) => {
          void locks.request(name, {}, () => {
            heldFor = userId;
            becameOwner();
            return new Promise<never>(() => undefined);
          });
        });
        resolve({ role: 'follower', whenOwner });
        return undefined;
      }
      heldFor = userId;
      resolve({ role: 'owner', whenOwner: Promise.resolve() });
      return new Promise<never>(() => undefined); // segura o lock até a aba fechar
    });
  });
}

/** Só para testes. */
export function resetWriterLockForTests(): void {
  heldFor = null;
}
