# ADR 0031: Erro de tela móvel não derruba o app e digitação no check-in não dispara erro (issue #250)

- **Status**: Aceita. Branch `fix/checkin-comentario-250`. Não publicada nem promovida a produção.
- **Data**: 2026-11-21
- **Relaciona-se a**: ADR 0001 (shell mobile carregado sob demanda). Nenhuma decisão anterior é substituída.

## Contexto

Relato do usuário em 21/11/2026, no iPhone: digitar qualquer comentário no check-in diário móvel (`/dashboard/checkin`) substituía o app inteiro pela tela global "Algo deu errado. Um erro inesperado impediu o MuscleFit de carregar."

Dois defeitos somados:

1. **Gatilho.** O `onChange` do campo de nota lia `e.currentTarget.value` dentro do atualizador funcional de `setForm`. O React pode executar o atualizador depois que o handler terminou; nesse momento `e.currentTarget` já é nulo e a leitura lança `TypeError`. Por isso o erro aparecia só depois de alguns caracteres, quando havia renderização pendente.
2. **Amplificador.** As telas móveis são desenhadas pelo `MobileShell` (`frontend/src/mobile/layout/MobileShell.tsx`), que fica dentro do layout do segmento `(app)`. No Next, o `error.tsx` de um segmento envolve a página, não o layout do mesmo segmento. Assim, o erro de qualquer tela móvel subia até `global-error.tsx`. O `ChunkBoundary` de `frontend/src/mobile/screens/lazy.tsx` só trata falha de carregamento de chunk e relança o resto.

Restrições reais:

- o formulário do check-in é guardado em `CheckinScreen.tsx` e precisa sobreviver a uma recarga dos dados (`useQuery` com `staleTime` de 60 s, em `CheckinScreen.tsx`);
- a regra de reset do formulário e a validação ficam em `frontend/src/lib/dashboard-checkin.ts`, que é testável sem React.

## Alternativas consideradas

- **Corrigir só o gatilho.** Resolve o caso relatado, mas deixa qualquer erro futuro de tela móvel derrubando o app inteiro. Recusada como solução única.
- **Corrigir só o limite de erro.** Esconderia o defeito sem corrigir a digitação: o campo continuaria perdendo o texto e o erro continuaria no console. Recusada.
- **Mover as telas móveis para o slot da página (`children`) do segmento.** Mudança estrutural do shell, com efeito em todas as rotas móveis. Fica fora do tamanho deste conserto.
- **Reset do formulário a cada mudança de `doDia`.** Recusada: um refetch com o mesmo registro entrega objeto novo e apagaria o texto em digitação.

## Decisão

1. **Gatilho.** O `onChange` do comentário lê `e.currentTarget.value` antes de chamar `setForm` (`CheckinScreen.tsx`, L196-199). O atualizador usa só o valor já lido.
2. **Limite de erro na área de conteúdo.** `ScreenErrorBoundary` (`frontend/src/mobile/screens/ScreenErrorBoundary.tsx`) envolve a tela móvel no `MobileShell` (L214-216). Um erro de renderização fica dentro de `<main>`: mostra "Algo deu errado nesta tela." com "Tentar de novo"; cabeçalho e navegação inferior continuam. O limite é reiniciado quando a rota ou a query muda (`resetKey` = `pathname?search`). Erro de chunk segue o tratamento existente: `ChunkBoundary` mostra "Sem conexão" e relança o que não for chunk, e o `ScreenErrorBoundary` trata o que sobe. O erro é registrado no console sem conteúdo digitado (`console.error(error)`, só o erro técnico).
3. **Formulário refeito só quando muda o dia ou o registro.** `shouldResetCheckinForm` (`dashboard-checkin.ts`, L75-77) compara `day` e `id`. O efeito de `CheckinScreen.tsx` (L74-80) só refaz o formulário quando essa função devolve `true`. Recarga com o mesmo registro não apaga o texto.
4. **Limite em constante única.** `CHECKIN_NOTES_MAX = 255` (`dashboard-checkin.ts`, L66) é usado pela validação, pelo `maxLength` do campo e pela mensagem de erro. O valor é igual ao da API e ao da coluna.
5. **Teste de ponta a ponta novo.** `frontend/e2e/mobile-layout/checkin-digitacao.spec.ts` cobre três cenários: digitação progressiva (texto curto, acentos, emoji, perto do limite, apagar, colar); recarga dos dados durante a digitação; envio com 422 e depois 500, com o texto preservado. Roda em WebKit com perfil de iPhone e em Chromium a 390 px. O `playwright.mobile.config.ts` ganhou três projetos, e as specs antigas ficam no projeto `default`.
6. **Mesmo padrão nas outras telas.** O `e.currentTarget` lido dentro de atualizador foi removido de quatro telas: `DashboardCheckinClient.tsx` (1 handler), `DashboardContextoClient.tsx` (3), `ExternalMappingsPanel.tsx` (5) e `ContextoScreen.tsx` (5), 14 handlers no total. Cada handler lê o valor antes de chamar o setter. O check-in web passou a usar a constante `CHECKIN_NOTES_MAX`. O teste de varredura `frontend/src/lib/event-current-target-guard.test.ts` percorre `frontend/src` e falha, com arquivo e linha, quando há `currentTarget` lido dentro de atualizador funcional de setter; a lista de exceções está vazia.

## Trade-offs

- Ganha: um erro de tela móvel deixa de derrubar o app inteiro; o defeito de digitação tem teste que reproduz o relato em WebKit com perfil de iPhone e em Chromium a 390 px.
- Ganha: a regra de reset do formulário fica numa função pura, testada sem React.
- Paga: o teste de varredura só cobre setters `setX(...)`; outras formas de atualizador ficam fora (ver Limites). O limite de erro cobre só a tela do registry, não o `children`.
- Paga: o teste novo roda só localmente e em WebKit com Playwright; não entra no CI.

## Consequências

- **Dados.** Nenhuma migração. Nenhum dado gravado muda.
- **Contrato.** Nenhuma mudança de API. O texto do formulário e as mensagens de validação não mudam; a mensagem de erro de tela ganha um texto novo ("Algo deu errado nesta tela.") no lugar do fallback global, só para erro de renderização de tela móvel.
- **Operação.** Só frontend. O defeito existe na produção atual (o código envolvido é antigo e não mudou nos lotes recentes) e deixa de ocorrer lá somente com a promoção.
- **Próximas decisões.** As quatro telas listadas na Decisão (item 6) já usam o padrão corrigido; a varredura guarda os setters `setX(...)` contra regressão.
- **Impacto em produção.** Só frontend, sem migração e sem mudança de API. Não medido: quantas vezes o erro ocorreu em produção, porque o frontend não registra erros no servidor.

## Limites e pendências

- (a) Nas telas web, o erro de renderização cai no `error.tsx` do segmento, não no global. O `ScreenErrorBoundary` só envolve as telas do registry; rotas que caem em `children` do `MobileShell` seguem sem esse limite.
- (b) Observado no teste: com o teclado aberto, o botão de salvar muda de posição quando a navegação inferior reaparece, e um toque pode se perder. Não alterado.
- (c) Não validado em iPhone real. A suíte de e2e mobile não roda no CI.
- (d) O teste de varredura só enxerga setters com nome `setX(...)`; atualizadores via `dispatch` ou função com outro nome não são cobertos.
- (e) Uma edição do mesmo check-in feita em outro aparelho não aparece no formulário aberto até trocar de dia (consequência da proteção do texto em digitação).

## Verificação

Resultados reais informados na entrega; nenhum outro foi medido:

- Código antigo, spec nova: falhou nos dois perfis logo no texto curto, com o fallback global e `TypeError: null is not an object (evaluating 'e.currentTarget.value')` no WebKit e `Cannot read properties of null (reading 'value')` no Chromium.
- Código corrigido, spec nova: 6 passaram (3 cenários × 2 perfis).
- Spec antiga `remount` no projeto `default`: 1 passou.
- Vitest (antes da correção das quatro telas): 127 passaram.
- Varredura `event-current-target-guard.test.ts` contra o código anterior: acusou exatamente as 14 ocorrências.
- Varredura depois da correção: sem nenhuma ocorrência.
- Vitest do lote: 215 passaram.
- `tsc`: sem erro.
- As quatro telas corrigidas (check-in web, contexto web, painel de mapeamentos externos e contexto móvel) não foram exercitadas em navegador.
- Não verificado: CI (a suíte de e2e mobile não roda nele); iPhone real; produção.

## Atualização de 23/11/2026 (lote 35)

- **Limite também no conteúdo da página (item a, parte mobile resolvida).** `frontend/src/mobile/layout/MobileShell.tsx` envolve com `ScreenErrorBoundary` também o conteúdo vindo da página (`children`) e a tela "só no desktop" (`DesktopOnlyScreen`), com `resetKey` por caminho e query (L211-213). Rotas que caíam em `children` agora têm o limite. Teste: `frontend/src/mobile/layout/MobileShell.erro.test.ts` (1 caso: filho que lança é contido; cabeçalho e navegação seguem na tela). A parte web do item (a) não mudou: nas telas web, o erro ainda cai no `error.tsx` do segmento.
- **Erros de navegação do Next são relançados.** Em `frontend/src/mobile/screens/ScreenErrorBoundary.tsx`, `isNextNavigationError` reconhece os digests `NEXT_REDIRECT` e `NEXT_NOT_FOUND`, e o limite os relança (L47) em vez de mostrar tela de erro.
- **Erro de carregamento de parte da aplicação (chunk) recarrega a página.** Quando `isChunkLoadError` é verdadeiro, o estado mostra `OfflineState` e "Tentar de novo" chama `window.location.reload()` (L42, L48). Nos demais erros, "Tentar de novo" só reabre o estado, sem recarregar. Teste: `frontend/src/mobile/screens/ScreenErrorBoundary.recarga.test.ts` (4 casos: 2 de digests de navegação via `it.each`, 1 de chunk que recarrega, 1 de erro comum que não recarrega).
- **Escolhas do usuário de 22/11/2026, mantidas.** Não reportar erros de tela ao servidor, e não rodar a suíte de ponta a ponta do celular no CI. Por isso o item (c) segue como está.
- **Continua dependendo do usuário.** Validação em iPhone real: toque em Salvar com o teclado aberto (item b) e dica de instalação. Nenhuma dessas validações foi feita.
- **Verificação.** Os testes novos estão listados acima. Esta atualização é só documental: nenhum resultado de execução dos testes foi informado nesta tarefa.
