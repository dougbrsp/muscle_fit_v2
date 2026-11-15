# ADR 0012: Escopo das leituras de progressão (issue #152)

- **Status**: Aceita (implementada na `develop` em PR aberto pela issue #152; não promovida a produção).
- **Data**: 2026-11-15

## Contexto

As telas mostravam o mesmo selo "PROGREDINDO" / "REGREDINDO" para leituras diferentes, sem dizer o que cada uma compara. Relato de 13/11/2026: "Agachamento livre" aparecia REGREDINDO no Início e no detalhe do exercício, e "Quadríceps" aparecia PROGREDINDO na evolução por grupo, enquanto o e1RM do agachamento subia 37,5% no detalhe do grupo.

Diagnóstico: não há cálculo duplicado nem errado. São quatro leituras legítimas, cada uma com comparador próprio:

- status do exercício: última exposição vs. anterior (`intelligence/dashboard/comparability.classify_exercise_progression`);
- status do grupo: agregação ponderada dos status dos exercícios comparáveis (`comparability.aggregate_group_progression`; pesos = `stimulus_weight` de `exercise_muscle_contributions`);
- volume do grupo: tonelagem média por sessão, janela atual vs. anterior (`intelligence/dashboard/muscle_group_progression.py`);
- e1RM do grupo: melhor e1RM por exercício, janela atual vs. anterior, somente informativo (`intelligence/dashboard/muscle_group_performance.py`).

O problema era de nome e de escopo, não de número: a tela não tinha como dizer qual das quatro leituras estava exibindo.

## Alternativas consideradas

- **Renomear os status por tela** (cada tela escreve o seu texto). Descartada: duplica texto fora do dono, reabre o problema da C13 (rótulos divergentes) e continua sem ligar o selo ao comparador real.
- **Criar um status único combinado** (somar as quatro leituras num veredito só). Descartada: mistura comparadores diferentes e mudaria o significado de status que já estão em produção; a issue pede explicação, não novo status.
- **Calcular o escopo no frontend** (a tela decide o texto a partir do tipo de dado). Descartada: viola a regra de consumidor que não recalcula (`docs/motores/FONTES_DA_VERDADE.md` §1, item 2) e deixaria o texto sem fonte única.

## Decisão

Descritores de escopo moram no dono dos rótulos de progressão, `api/app/domain/progression_status_labels.py` (`PROGRESSION_SCOPES` e `progression_scope(code)`). Cada resposta da API que traz um status de progressão carrega o descritor da leitura que ele representa. As telas exibem o descritor pronto e não recalculam nem reclassificam nada.

Os campos novos no frontend são opcionais: sem eles o escopo simplesmente não aparece.

## Contrato

Descritor (6 chaves, iguais em todas as respostas): `code`, `entity`, `metric`, `comparator`, `label`, `description`. Modelo Pydantic: `ProgressionScopeOut` em `api/app/intelligence/dashboard/contracts.py`.

Os quatro códigos são `exercise_last_vs_previous_exposure`, `group_weighted_exercise_status`, `group_volume_current_vs_previous_window` e `e1rm_current_vs_previous_window`.

Chaves novas por rota:

- `GET /analytics/dashboard/overview`, bloco `progression` (`ProgressionBlockOut`): `status_scope`, `rule_version`, `window`. Dono: `overview_service.progression_block`.
- Detalhe do exercício (`ExerciseProgressResponse`): `status_scope`.
- `GET /analytics/dashboard-progression-trend`: `status_scope`, vindo de `exposure_service.progression_verdict`.
- `GET /analytics/dashboard-progression-by-muscle-group`: no topo, `performance_status_scope`, `volume_status_scope`, `performance_change_scope`, `exercise_status_scope` e `metric_versions`; em cada item de `performance_exercises`, o `status` do exercício, repassado do lote sem reclassificar. O `progression_counts` por grupo já existia e passou a ser exibido.

Não mudaram: o lote `exposure_service.get_exercises_progression_batch` e os consumidores de IA (`report_ai.py`, `progression_service.py`).

Frontend: `frontend/src/lib/progression-scope.ts` (formatação pura: `scopeLabel`, `scopeDescription`, `scopeWindowText`, `scopeFootnote`, `groupCompositionText`) e o componente `ScopeCaption` em `frontend/src/components/dashboard/StatusBadge.tsx`, usados em `DashboardOverviewClient.tsx`, `progressao/ExerciseProgressionSummaryCard.tsx`, `progressao/DashboardProgressaoClient.tsx`, `progressao/MuscleGroupProgressionPanel.tsx`, `exercicios/[exerciseId]/ExerciseProgressClient.tsx` e, no mobile, `evolucao/ProgressaoScreen.tsx` e `evolucao/ExercicioScreen.tsx`.

## Trade-offs

- Ganha: cada número na tela diz o que foi comparado, sem mudar nenhum status, contagem ou percentual.
- Paga: o texto do escopo vive no backend, então corrigir uma descrição exige deploy da API. O frontend fica dependente do servidor para ver o escopo; sem as chaves novas, a tela volta ao comportamento anterior, sem erro.
- Paga: as respostas ficam um pouco maiores (um descritor de seis campos por leitura).

## Consequências

- Mesmo status com escopos diferentes passa a ser distinguível pelo texto: o selo do exercício não se confunde com o do grupo.
- Nenhum consumidor novo de regra: a guarda de motores não ganha padrão novo. O mapa cobre a regra por teste de comportamento (ver `docs/motores/FONTES_DA_VERDADE.md`, linha "Escopo das leituras de progressão").
- Qualquer tela nova que mostre status de progressão deve anexar o escopo da resposta. Isso vira requisito de revisão.
- O "Impacto em produção" é só de metadados: nenhum status, contagem ou percentual muda, conforme a issue. A verificação é feita pelos testes `api/tests/unit/test_escopo_leituras_progressao.py` e `api/tests/unit/test_progressao_tela_mesma_regra.py` (mesmo status e mesmo escopo no Início, no detalhe e na tendência) e por `frontend/src/lib/progression-scope.test.ts`.

## Fora desta decisão

1. Separar, no detalhe do exercício, "última exposição vs. anterior" de "tendência da janela". Hoje só existe a primeira leitura. Criar a tendência seria uma regra nova de motor, não feita aqui.
2. `frontend/src/app/(app)/dashboard/treinos/AnaliseIaSnapshot.tsx` e `frontend/src/mobile/screens/evolucao/AnaliseIaSnapshotMobile.tsx`: o selo do exercício vem do snapshot da análise de IA, outro contrato, ainda sem escopo.
3. Card mobile do grupo: o percentual em destaque não tem rótulo próprio; a explicação fica no rodapé e no detalhe.
