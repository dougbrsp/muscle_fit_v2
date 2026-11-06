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
| [0005](0005-gasto-energetico-adaptativo-quarto-modo-em-sombra.md) | Gasto energético adaptativo como 4º modo, em sombra primeiro | Proposta | 2026-11-05 |

Documentação operacional da frente mobile: [docs/mobile/README.md](../mobile/README.md). Plano e status: [docs/mobile/MOBILE_IMPLEMENTATION_PLAN.md](../mobile/MOBILE_IMPLEMENTATION_PLAN.md).
