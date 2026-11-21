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

## Trade-offs

- Ganha: um erro de tela móvel deixa de derrubar o app inteiro; o defeito de digitação tem teste que reproduz o relato em WebKit com perfil de iPhone e em Chromium a 390 px.
- Ganha: a regra de reset do formulário fica numa função pura, testada sem React.
- Paga: o padrão `e.currentTarget` lido dentro do atualizador continua em outras telas (ver Limites). O limite de erro cobre só a tela do registry, não o `children`.
- Paga: o teste novo roda só localmente e em WebKit com Playwright; não entra no CI.

## Consequências

- **Dados.** Nenhuma migração. Nenhum dado gravado muda.
- **Contrato.** Nenhuma mudança de API. O texto do formulário e as mensagens de validação não mudam; a mensagem de erro de tela ganha um texto novo ("Algo deu errado nesta tela.") no lugar do fallback global, só para erro de renderização de tela móvel.
- **Operação.** Só frontend. O defeito existe na produção atual (o código envolvido é antigo e não mudou nos lotes recentes) e deixa de ocorrer lá somente com a promoção.
- **Próximas decisões.** O padrão corrigido deve ser replicado nas telas da lista de Limites (lote seguinte).
- **Impacto em produção.** Só frontend, sem migração e sem mudança de API. Não medido: quantas vezes o erro ocorreu em produção, porque o frontend não registra erros no servidor.

## Limites e pendências

- (a) O mesmo padrão (`e.currentTarget` lido dentro do atualizador) existe em telas ainda não corrigidas, para o lote seguinte: `frontend/src/app/(app)/dashboard/checkin/DashboardCheckinClient.tsx`, `frontend/src/mobile/screens/evolucao/ContextoScreen.tsx`, `frontend/src/app/(app)/dashboard/contexto/DashboardContextoClient.tsx` e `frontend/src/app/(app)/dominio-exercicios/components/ExternalMappingsPanel.tsx`.
- (b) Nas telas web, o erro de renderização cai no `error.tsx` do segmento, não no global. O `ScreenErrorBoundary` só envolve as telas do registry; rotas que caem em `children` do `MobileShell` seguem sem esse limite.
- (c) Observado no teste: com o teclado aberto, o botão de salvar muda de posição quando a navegação inferior reaparece, e um toque pode se perder. Não alterado.
- (d) Não validado em iPhone real. A suíte de e2e mobile não roda no CI.

## Verificação

Resultados reais informados na entrega; nenhum outro foi medido:

- Código antigo, spec nova: falhou nos dois perfis logo no texto curto, com o fallback global e `TypeError: null is not an object (evaluating 'e.currentTarget.value')` no WebKit e `Cannot read properties of null (reading 'value')` no Chromium.
- Código corrigido, spec nova: 6 passaram (3 cenários × 2 perfis).
- Spec antiga `remount` no projeto `default`: 1 passou.
- Vitest: 127 passaram.
- `tsc`: sem erro.
- Não verificado: CI (a suíte de e2e mobile não roda nele); iPhone real; produção.
