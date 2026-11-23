# Registros de decisão arquitetural (ADR)

Decisões de arquitetura e de contrato do MuscleFit ficam aqui, uma por arquivo, numeradas em sequência (`NNNN-slug.md`).

## Formato

Cada ADR tem estas seções, nesta ordem:

- **Contexto**: o problema e as restrições reais (com arquivos e linhas verificáveis).
- **Alternativas consideradas**: o que foi avaliado e por que não foi escolhido.
- **Decisão**: o que foi decidido, em uma frase quando possível.
- **Trade-offs**: o que se ganha e o que se paga.
- **Consequências**: efeitos práticos para o código, a operação e as próximas decisões.
- **Status**: `Proposta`, `Aceita`, `Substituída por NNNN` ou `Revogada`.
- **Data**.

Uma ADR não é reescrita para mudar de decisão. Se a decisão mudar, cria-se uma nova ADR que substitui a anterior, e a antiga recebe o status `Substituída por NNNN`.

## Índice

| Nº | Título | Status | Data |
|---|---|---|---|
| [0001](0001-selecao-layout-mobile-por-viewport.md) | Seleção de layout mobile por viewport, com feature flag e shell carregado sob demanda | Aceita | 2026-11-02 |
| [0002](0002-persistencia-offline-indexeddb-fila-client-key.md) | Persistência offline em IndexedDB por usuário e fila idempotente por `client_key` | Aceita | 2026-11-02 |
| [0003](0003-service-worker-manual-allowlist-atualizacao-segura.md) | Service worker escrito à mão, com allowlist de cache e atualização segura | Aceita | 2026-11-02 |
| [0004](0004-navegacao-iphone-e-secoes-so-desktop.md) | Navegação do iPhone (tab bar de 5 abas, hub Evolução) e seções só do computador | Aceita | 2026-11-03 |
| [0005](0005-gasto-energetico-adaptativo-quarto-modo-em-sombra.md) | Gasto energético adaptativo como 4º modo, em sombra primeiro | Aceita | 2026-11-05 |
| [0006](0006-cards-fotorrealistas-v3.md) | Cards de exercício fotorrealistas (V3), com duas cenas e fotos de referência do admin | Aceita | 2026-11-06 |
| [0007](0007-gordura-percentual-configurado-no-ciclo.md) | Gordura segue a fração configurada das kcal finais do dia (inclusive no ciclo de carbo) | Aceita | 2026-11-08 |
| [0008](0008-gasto-explicito-desconta-repouso-da-sessao.md) | O gasto explícito/automático desconta o repouso da janela do treino | Aceita | 2026-11-08 |
| [0009](0009-proteina-do-corte-sobre-o-peso-total.md) | Proteína do corte em g/kg de peso total (nutrition-v4) | Aceita | 2026-11-08 |
| [0010](0010-regras-de-carga-como-formula-com-condicionais.md) | Regras de carga da próxima série como fórmula com condições E/OU (next-set-load-rules-v2) | Aceita | 2026-11-08 |
| [0011](0011-permissoes-por-grupos-rbac.md) | Permissões por grupos (RBAC) como fundação da monetização: concessões aditivas de recurso × ação × escopo | Aceita | 2026-11-10 |
| [0012](0012-escopo-das-leituras-de-progressao.md) | Escopo das leituras de progressão (issue #152) | Aceita | 2026-11-15 |
| [0013](0013-rpe-esperado-por-serie.md) | RPE esperado por série no Modelo de treino (issue #146) | Aceita | 2026-11-15 |
| [0014](0014-contexto-de-equipamento-e-calibracao.md) | Contexto de equipamento (máquina por academia) e calibração após troca (issue #188) | Aceita | 2026-11-16 |
| [0015](0015-progressao-fonte-unica-no-motor.md) | Progressão com fonte única no motor (issue #188) | Aceita | 2026-11-16 |
| [0016](0016-kcal-do-intervals-ativa-por-padrao.md) | A kcal informada do Intervals é calorie ativa por padrão (nutrition-v5, issue #138) | Aceita | 2026-11-17 |
| [0017](0017-aprimoramento-de-tecnica-fora-da-progressao.md) | Execução marcada como aprimoramento de técnica fica fora de progressão, calibração, recorde, e1RM e referência de carga; conta para volume, recuperação e tempo (issue #196) | Aceita | 2026-11-18 |
| [0018](0018-edicao-de-treino-encerrado-e-reavaliacao.md) | Edição de treino encerrado pelo Registro e reavaliação da progressão (issues #215, #216 e #219) | Aceita | 2026-11-19 |
| [0019](0019-campos-do-treino-editaveis-depois-do-treino.md) | Tipo de série, medida, dor por série e objetivo editáveis depois do treino pelo Registro (issue #219, lote 2) | Aceita | 2026-11-19 |
| [0020](0020-tecnica-ate-a-serie-de-trabalho-e-encerrar-treino.md) | Marca de técnica até a 1ª série de trabalho, encerrar treino entre séries e dor zero sem dor (issues #213 e #211) | Aceita | 2026-11-19 |
| [0021](0021-continuacao-por-troca-de-equipamento-cria-exercicio-proprio.md) | Continuação por troca de equipamento cria exercício próprio (issues #225 e #204) | Aceita | 2026-11-19 |
| [0022](0022-gvt-reps-do-protocolo-e-grupo-travado-na-criacao-da-ficha.md) | GVT com as reps do protocolo, técnica só de séries mantida sem trabalho direto, trava de grupo na criação da ficha e vínculo da geração (issues #224 e #178) | Aceita | 2026-11-19 |
| [0023](0023-pendencias-de-nutricao-208-202-200.md) | Pendências de nutrição: busca por marca e listagem por letra, voltar do cabeçalho mobile, guarda do modo de caloria e mensagem do 422 na tela web (issues #208, #202, #200 e #229) | Aceita | 2026-11-20 |
| [0024](0024-rpe-por-serie-saneamento-da-ia-e-acabamentos.md) | RPE por série com saneamento da saída da IA e acabamentos da ficha e do Modelo (issues #183 e #184) | Aceita | 2026-11-20 |
| [0025](0025-tendencia-da-janela-e-escopo-na-analise-por-ia.md) | Tendência da janela do exercício (segunda leitura, metades do e1RM) e escopo do snapshot da análise por IA (issues #176 e #177) | Aceita | 2026-11-20 |
| [0026](0026-rpe-esperado-gravado-na-serie-e-comparacao.md) | RPE esperado gravado na série e comparação com o realizado (issue #182) | Aceita | 2026-11-20 |
| [0027](0027-seletor-de-equipamento-no-modo-treino.md) | Seletor de equipamento no Modo Treino, validação do equipamento da sessão e convenção de carga nas opções (issue #163, primeira parte) | Aceita | 2026-11-20 |
| [0028](0028-catalogo-global-no-seletor-de-equipamento.md) | Catálogo global no seletor de equipamento e acréscimo à academia com permissão (issue #163, segunda parte) | Aceita | 2026-11-20 |
| [0029](0029-ciclo-de-carboidratos-com-compensacao-em-gordura.md) | Ciclo de carboidratos com compensação em gordura, política opcional sem migração (issue #242) | Aceita | 2026-11-20 |
| [0030](0030-rpe-esperado-x-realizado-no-relatorio.md) | RPE esperado × realizado no relatório em Markdown, por sessão, com a contagem do dono (issue #241) | Aceita | 2026-11-20 |
| [0031](0031-erro-de-tela-movel-nao-derruba-o-app.md) | Erro de tela móvel não derruba o app e digitação no check-in não dispara erro (issue #250) | Aceita | 2026-11-21 |
| [0032](0032-permissao-propria-para-acrescentar-equipamento-a-academia.md) | Permissão própria para acrescentar equipamento à academia, com migração 122 de concessões (issue #246) | Aceita | 2026-11-22 |
| [0033](0033-politica-do-ciclo-em-coluna-propria-e-lembrada-com-o-ciclo-desligado.md) | Política do ciclo em coluna própria (migração 123) e lembrada com o ciclo desligado; supera partes da 0029 (issue #248) | Aceita | 2026-11-22 |
| [0034](0034-prioridade-do-dia-no-diario-da-nutricao.md) | Prioridade nutricional do dia no diário da nutrição, no dono do ciclo; `nutrition-v6` (issue #272) | Aceita | 2026-11-22 |
| [0035](0035-receitas-como-alimento-composto.md) | Receitas como alimento composto (`source='recipe'`), com motor próprio e migração 125; primeira fatia do backend (issue #286) | Aceita | 2026-11-23 |
| [0036](0036-perfis-de-resistencia-do-par-exercicio-equipamento.md) | Perfis de resistência do par exercício+equipamento, com vocabulário controlado e revisão humana; fatia 1, parte A (migração 126, issue #288) | Aceita | 2026-11-23 |

Documentação operacional da frente mobile: [docs/mobile/README.md](../mobile/README.md). Plano e status: [docs/mobile/MOBILE_IMPLEMENTATION_PLAN.md](../mobile/MOBILE_IMPLEMENTATION_PLAN.md).
