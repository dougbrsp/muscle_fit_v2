# ADR 0002: Persistência offline em IndexedDB por usuário e fila idempotente por `client_key`

- **Status**: Aceita
- **Data**: 2026-11-02

## Contexto

O Modo Treino precisa sobreviver a interrupções de interface, perda temporária de conexão e reabertura do navegador, sem perder série (`docs/mobile/MOBILE_IMPLEMENTATION_PLAN.md`, seção 3.5). Antes desta frente, a sessão ficava em `localStorage` com chaves únicas, sem escopo de usuário e sem apagar no logout (`frontend/src/lib/workout-mode-storage.ts`). Isso permitia que outra conta no mesmo aparelho visse o treino alheio.

O backend já deduplica as duas escritas que importam para a fila: `POST .../exercises` e `POST .../sets`, pela `client_key` com UNIQUE (`api/app/workout_sessions.py`, linhas 14, 353–380 e 1117–1170). Replay devolve o registro gravado com `duplicate: true`. `start`, `finish` e `PATCH` de série não têm chave de idempotência.

iOS não oferece Background Sync, então a sincronização precisa acontecer com a aplicação ativa.

## Alternativas consideradas

1. **Manter `localStorage`.** Rejeitada. É síncrono, com cota pequena, sem partição por usuário e sem versionamento de esquema. Fica apenas como fallback degradado.
2. **Cache Storage para dados de treino.** Rejeitada. Cache Storage é para respostas HTTP, e o plano veta armazenar dado autenticado nele (ADR 0003).
3. **Biblioteca `idb`.** Não adotada. O wrapper próprio em `frontend/src/lib/offline/backend.ts` cabe em um arquivo e evita dependência nova; o plano admite `idb` só se justificado.
4. **Background Sync para reenviar.** Rejeitada. Não existe no iOS.
5. **Fila offline também para `start`, `finish` e `PATCH`.** Rejeitada para o MVP. Sem chave de idempotência no servidor, um reenvio poderia criar segunda sessão ou finalizar duas vezes. Essas operações exigem rede e mostram erro recuperável.
6. **Sincronizar apenas online, sem fila.** Rejeitada. Viola "sem perda silenciosa de dados".

## Decisão

- **Banco**: IndexedDB `musclefit`, versão 1, com as stores `workout_session`, `op_queue` e `meta` (`frontend/src/lib/offline/keys.ts`). Migrações indexadas pela versão de destino em `frontend/src/lib/offline/backend.ts`. Migração publicada não é editada.
- **Partição**: toda chave é prefixada pelo `userId` (`makeKey`). Troca de conta confirmada zera o espelho e apaga a partição do usuário anterior (`frontend/src/lib/offline/session-store.ts`).
- **Fallback**: IndexedDB, depois `localStorage` com chaves escopadas, depois memória. Qualquer modo que não seja IndexedDB marca `degraded` e a UI avisa.
- **Fila**: só `addExercise` e `addSet` são enfileiráveis (`frontend/src/lib/offline/op-queue.ts`, cabeçalho). Cada op tem `opId` (UUID), `clientKey`, `userId`, `status` e FIFO por exercício.
- **Estados por série**: `saved_local` → `syncing` → `synced` | `error` | `conflict` (`frontend/src/lib/offline/op-types.ts:6`). `synced` só existe depois de 2xx do servidor.
- **Classificação de falha** (`op-queue.ts`, `classifyFailure`): rede, 408, 429 e 5xx viram `error` com backoff de 2 s a 60 s; 401 interrompe sem confirmar nada; demais 4xx viram `conflict`.
- **Eco**: replay com `duplicate: true` e valores diferentes do enviado vira `conflict` com `conflictKind: echo_mismatch` (`frontend/src/lib/offline/echo.ts`). Nunca vira `synced`.
- **409 `set_number_conflict`** vira `conflict` com `conflictKind: set_number`. A ação "reenviar com outro número" deixa o servidor escolher o próximo número livre.
- **Retenção**: op `synced` há mais de 24 h é removida na reabertura; qualquer op com mais de 30 dias (`OP_TTL_MS`) também é removida. "Último usuário conhecido" vale para abrir offline só por 7 dias após `/auth/me` 200 (`frontend/src/lib/offline/scope.ts:13`).
- **Duas abas**: Web Locks por usuário (`musclefit-offline-<id>`, `frontend/src/lib/offline/tab-lock.ts`). A primeira aba é dona e grava; a segunda vira seguidora.
- **Gatilhos de sincronização**: `online`, `pageshow`, `focus`, `visibilitychange` para visível, e um tick de 20 s com a aba visível (`op-queue.ts`, `start()`).

## Trade-offs

- **Ganho**: nenhum registro é marcado como sincronizado sem resposta 2xx; reenvio não duplica série, porque a chave é a mesma; logout e troca de conta não expõem dado alheio.
- **Custo**: `PATCH` de série offline fica de fora (lacuna L2 no plano). A edição de uma série ainda pendente é feita na própria fila (`editPendingSet`), e depois de enviada exige rede.
- **Custo**: IndexedDB pode ser apagado pelo sistema em pressão de armazenamento e, no Safari, após período sem uso. `navigator.storage.persist()` é pedido quando disponível, mas não há garantia.
- **Custo**: a troca de conta confirmada apaga inclusive séries não sincronizadas da conta anterior (`session-store.ts`, linhas 157–163). É uma decisão do código e precisa de aval do usuário antes de produção (ver o guia operacional, seção "Pendências").

## Consequências

- O backend precisa continuar deduplicando por `client_key`. Mudar essa regra exige revisar a fila.
- Quem for adicionar uma escrita à fila precisa provar que o servidor a deduplica. Sem isso, a escrita fica de fora.
- O espelho em memória é único por página (`session-store.ts`). Rotação e resize não reidratam nem reiniciam a sessão.
- Nenhum número de treino entra no módulo offline. Ele só guarda o que a tela montou e o que o servidor devolveu.
- Validação do formato do erro UNIQUE na RDS Data API e do fluxo offline real em iPhone continuam pendentes (guia operacional, seção "Pendências").
