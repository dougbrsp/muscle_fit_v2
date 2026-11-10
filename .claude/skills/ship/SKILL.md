---
name: ship
description: Planeja e executa entregas MuscleFit com agentes especializados, grafo local, revisão independente e testes direcionados.
argument-hint: "[plano | retomar | cancelar | objetivo]"
---

Use na sessão principal `tech-lead` configurada para o projeto. Se o agente principal for outro, peça para abrir uma sessão com o Tech Lead. Execute as ferramentas internas pelo usuário. Leia [contrato do grafo](../../harness/OPERACAO.md) e `.claude/CLAUDE.md`. Entrada do usuário:

$ARGUMENTS

## Planejar

- Identifique o objetivo, o comportamento atual, critérios de aceite, riscos e restrições. Use Explore em Haiku para buscas delimitadas quando poupar contexto. Nunca delegue a decisão arquitetural final.
- Monte de 1 a 6 tarefas, cada uma com agente local, caminhos exatos exclusivos, critério e dependências. Use só o suficiente para verificar a entrega. Identifique testes, riscos semânticos e dependências de produto. Para mudanças nos motores, carregue o mapa e a guarda antes de repartir tarefas.
- `plano` é somente leitura. Entregue o grafo, contratos, validação e as decisões humanas reais. Para execução autorizada, use uma branch de trabalho limpa de `origin/develop`, com o harness instalado. Use o checkout atual quando ele atender a esses critérios. Ao iniciar um novo objetivo, arquive a execução anterior se estiver em estado terminal.
- `retomar` carrega `status` e segue o estado salvo. Com um autor ativo, procure primeiro o resultado da mesma sessão em `/tasks` ou na transcrição. Prossiga somente após confirmar que o autor terminou. Se houver trabalho parcial sem evidência, preserve e encerre o run com o usuário.
- Se o usuário pedir para cancelar, pare os agentes ativos e registre o resultado do autor interrompido. Quando `confirmations.cancel` estiver disponível, apresente-o com `AskUserQuestion`. Arquive a execução após confirmar o estado terminal, preservando o diff.

## Executar

1. Consulte `ready_tasks` e use `claim <id>`. Invoque o agente nomeado com objetivo, aceite, base, checkout, ownership e decisões já registradas. Use as famílias do catálogo. Uma escrita por vez e somente no checkout atual. O autor devolve um resultado com evidência. Em tarefa que altera submódulo, o autor o deixa numa branch de trabalho própria, sem commitar; o `report` faz o commit local dos submódulos da tarefa (mensagem em `submodule_commit_message` do resultado). O `report` só commita submódulo já registrado na base e leva tudo que não estiver no `.gitignore` dele (`git add -A`), então deixe nele só o que deve ser publicado; o push da branch do submódulo acontece na publicação. Registre-o em `report`; nunca declare sucesso só porque a chamada terminou.
2. Se houver escolha genuína do usuário, registre o gate inicial no plano ou use `gate <arquivo>` entre tarefas. Explique impacto e recomendação. Envie `confirmations.gate` do último `status` exatamente como veio para a ferramenta `AskUserQuestion`. Faça o mesmo ao encontrar uma decisão pendente em `retomar`. A escolha é registrada pelo hook. Confira o `status` e prossiga na própria conversa quando o estado voltar a `execute`. Se o formulário for dispensado ou a resposta chegar em texto livre, esclareça e reapresente o formulário quando o usuário estiver pronto. Se o escopo mudou, preserve o diff e replaneje.
3. Execute `review`. Solicite os revisores de `required` em sessões novas, até três em paralelo, com base, revisão e caminho do diff congelado. Passe critérios e evidência relevante do produto, sem passar a conclusão do autor. Cada parecer tem `revision`, `status` e `evidence`. Peça linhas, impacto e correção mínima para achados, e cobertura examinada para aprovações. Registre `verdict` somente depois de obter o parecer do agente indicado. Acrescente o `agent_id` do recibo exibido em `status`, sem preencher pareceres que o agente não entregou. P0/P1 e P2 reproduzível em requisito, segurança ou compatibilidade exigem correção; P3 ou sugestões fora do escopo ficam no relatório.
4. Para achados, registre `rework` nos IDs dos autores afetados e devolva evidências a cada um. Refaça a revisão inteira do novo diff. Cinco rodadas de correção por execução. Para falhas de ambiente, preserve a execução e relate a ação concreta que falta. Não reinicie para apagar o histórico.
5. Após todos os pareceres aprovarem, use `verify` com o manifesto dirigido do QA. Em falha de código, volte ao autor via `rework`. Após resolver uma falha de ambiente ou de manifesto sem mudar o diff, use `verify` mais uma vez na rodada. Em sucesso, use `ready`. Só abra PR se a solicitação incluir publicação ou houver autorização anterior aplicável. Antes de publicar, após o commit, cheque que o checkout está limpo e repita `ready` para conferir o conteúdo do commit. PR com base `develop`, políticas do CI e publicação da evidência. Promoção e merge ficam fora do /ship.
6. Entregue um resumo único com status, branch, PR se houver, pareceres, revisão e testes. Durante o fluxo, interrompa o usuário somente por decisão humana pendente ou bloqueio que ele precise destravar. Respeite prompts nativos de permissão do Claude Code.
