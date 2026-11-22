# ADR 0030: RPE esperado × realizado no relatório em Markdown (issue #241)

- **Status**: Aceita. Branch `feat/rpe-relatorio-241`. Não publicada nem promovida a produção. Atualizada em 22/11/2026: o detalhe do treino passou a usar o mesmo critério de série de trabalho (ver "Atualização de 22/11/2026").
- **Data**: 2026-11-20
- **Complementa**: ADR 0026, limite (a), e ADR 0013, pendência 1 de "Fora desta entrega". Nenhuma decisão anterior é substituída.

## Contexto

O ADR 0026 (PR #240) gravou o RPE esperado de cada série (`exercise_sets.rpe_target_planned`) e mostrou a comparação esperado × realizado no detalhe do treino e no histórico de progressão. O relatório em Markdown ficou de fora: `api/app/intelligence/report/daily_breakdown.py` lia só agregados por SQL, e contar a comparação por série exigia ler as séries ou comparar em SQL. As duas coisas fogem do dono (`effective_intensity.rpe_vs_expected`), e a regra dos motores proíbe (`docs/motores/FONTES_DA_VERDADE.md`; guarda em `api/tests/unit/test_guarda_fontes_da_verdade.py`).

Restrições reais:

- a linha do tempo do relatório já é por sessão (`daily_breakdown.build_cycle_timeline`, chamada por `markdown_report_service._collect_cycles`), então o nível natural é a sessão;
- o histórico de progressão conta só séries de trabalho (`api/app/analytics.py`, L1310). O relatório precisa dar a mesma contagem para as mesmas séries;
- o frontend não pode ter regra de contagem nem rótulo próprio (`frontend/src/lib/rpe-expected-vs-actual.ts`);
- a coluna `rpe_target_planned` só existe depois da migração 121, que ainda não foi promovida.

## Alternativas consideradas

- **Contar em SQL na consulta do relatório** (`SUM(CASE WHEN es.rpe < es.rpe_target_planned ...)`). Recusada: duplicaria a comparação em SQL, fora do dono.
- **Contar no renderizador.** Recusada: `markdown_renderer.py` é função pura que só formata (cabeçalho do módulo). A contagem ficaria em dois lugares.
- **Uma consulta por sessão ou por dia.** Recusada: o relatório segue uma consulta por fonte e por janela (cabeçalho de `daily_breakdown.py`).
- **Listar as séries no relatório.** Recusada: o relatório é cortado por dia e por sessão. Uma linha por série tornaria a seção ilegível e não traz decisão nova.
- **Incluir backoff e técnica na contagem, como o detalhe.** Recusada: relatório e histórico contam só séries de trabalho (`is_working_set`), para dar o mesmo número. Na data desta decisão o detalhe ainda comparava backoff e técnica; desde 22/11/2026 ele também não compara (ver Atualização).

## Decisão

1. **Leitura e contagem.** `daily_breakdown.SESSION_SET_RPE_SQL` (L197-209) lê, numa consulta só para a janela e o usuário (`load_session_set_rpe`, L818), o RPE realizado e o esperado das séries confirmadas e não preparatórias. `build_cycle_timeline` (L690) mantém só as séries de trabalho (`is_working_set`, L703) e conta com `effective_intensity.rpe_vs_expected_counts` (L453). Nada de comparação em SQL nem no renderizador.
2. **Nível: sessão.** Cada linha da linha do tempo ganha `rpe_vs_expected_counts` (L730), nulo quando nenhuma série da sessão tem esperado gravado. Sessão sem esperado não mostra a linha.
3. **Rótulos do dono.** `RPE_VS_LABELS` (`effective_intensity.py`, L425) é a fonte. A API do histórico passa a mandar `rpe_vs_expected_labels` (`analytics.py` L1376, campo aditivo). `rpeExpectedCountsText` (`rpe-expected-vs-actual.ts`, L40) recebe os rótulos e usa uma reserva de texto só para resposta antiga em cache.
4. **Tela web de progressão** mostra a contagem (`DashboardProgressaoClient.tsx`, L407-412), como a tela mobile já mostrava (`ProgressaoScreen.tsx`, L588-590).
5. **Limpeza.** `rpeExpectedReadonly` foi removida de `rpe-expected-vs-actual.ts`; não tinha uso desde o ADR 0026. O esperado NÃO foi levado à tela de edição da série.
6. **Guarda.** O padrão `rpe_alvo_protocolo` de `test_guarda_fontes_da_verdade.py` (L1747) passa a cobrir `rpe_target_planned`, junto de `target_rpe` e `rpe_target_effective`: 9 ou 10 atribuídos ali fora de `training_safety.py` viram violação. A allowlist não muda.
7. **Migração 121.** A docstring de `api/db/migrations/121_rpe_alvo_planejado_da_serie.py` (L6) continua chamando `rpe_target_effective` de "alvo do exercício", embora a coluna nova seja por série. O arquivo não foi editado por esse motivo: o executor de migrações (`api/app/migrate.py`, `compute_checksum` e `_check_consistency`) compara o checksum do arquivo inteiro e recusa, com `MigrationChecksumError`, migração já aplicada que tenha sido alterada. O plano da migração não muda.

Itens da #241 descartados neste lote, com o motivo (ficam para issue):

- (a) série enviada fora de ordem pela fila offline: deixou de ser pendente. A correção entrou nesta #241; ver "Revisão pela #241" no ADR 0026;
- (b) o detalhe do treino comparava toda série com esperado gravado, inclusive backoff e técnica; a contagem do histórico e do relatório conta só séries de trabalho. RESOLVIDO em 22/11/2026 (#241, escolha do usuário): o detalhe também compara só série de trabalho (ver Atualização);
- (c) série com repetições pendentes, medida que não é por repetições ou marcada como técnica grava o alvo do exercício ou do protocolo: não conferido na tela;
- (d) trava no pipeline de produção contra publicar código que depende de migração com `RUN_DB_MIGRATIONS` desligado: é mudança de workflow de deploy, fora deste lote.

## Trade-offs

- Ganha: uma única regra de contagem (`rpe_vs_expected_counts`) para histórico e relatório; uma consulta por geração de relatório; nenhuma coluna nova e nenhuma tabela nova.
- Paga: a linha "RPE realizado x esperado por série" depende da coluna da migração 121. Sem ela, essa linha some e o resto da seção de ciclos segue (ver Consequências).
- Paga (resolvida em 22/11/2026): relatório e detalhe do treino davam números diferentes com backoff ou técnica com esperado gravado (item (b)). Hoje os dois usam o mesmo critério no dono.
- Paga: uma consulta a mais por geração, limitada à janela.

## Consequências

- **Dados.** Nenhuma migração nova. Nenhum dado gravado muda.
- **Contrato.** O relatório em Markdown ganha, na seção 6 (ciclos), o parágrafo "RPE realizado x esperado por série" com uma linha por sessão que tem esperado gravado. A API do histórico ganha `rpe_vs_expected_labels` (aditivo).
- **Operação.** Este código vai para produção junto da migração 121 (PR #240) ou depois dela. Sem a coluna, a leitura nova (`daily.load_session_set_rpe`) falha com erro 1054. `markdown_report_service._collect_cycles` captura a falha (bloco `try` em L788-792), registra aviso e segue com a linha do tempo e a aderência; só a linha "RPE realizado x esperado por série" some. O histórico de progressão já depende da coluna desde o PR #240 (`analytics.py`, L1261).
- **Mapa.** `docs/motores/FONTES_DA_VERDADE.md`: a linha do esperado gravado da série lista o relatório e a tela web como consumidores e proíbe contagem em SQL ou no renderizador; a linha de RPE-alvo (§3.1) deixa de dizer que o relatório fica fora; bloco "Lote 13 de 20/11/2026 (#241)".
- **Impacto em produção (informado pelo Tech Lead em 20/11/2026; não reexecutado por este documento).** Sem migração; nenhum dado gravado muda. O relatório ganha uma consulta por geração, limitada à janela e ao usuário. A coluna `rpe_target_planned` só existe depois da migração 121 (PR #240, ainda não promovido). Em produção hoje nenhuma série tem esperado gravado, então a linha nova só aparece em treinos feitos depois da promoção. Se a coluna faltar, só essa linha some e a seção de ciclos continua. Não medido: tempo da consulta nova na RDS Data API.

## Limites e pendências

- (a) O relatório mostra a comparação por sessão, não por série.
- (b) RESOLVIDO em 22/11/2026: o detalhe e o relatório/histórico usam o mesmo critério de série de trabalho (`effective_intensity.set_rpe_vs_expected`); ver ADR 0026, limite (h), e a Atualização abaixo.
- (c) Sem teste de renderização da tela web. Nenhuma tela foi vista em navegador nem em iPhone.
- (d) O SQL novo foi exercitado só com banco simulado antes do CI (`ci-completo`).
- (e) O teste de integração do relatório não ganhou caso com esperado gravado.
- (f) O esperado não aparece na tela de edição da série (ADR 0026, limite (c)).
- (g) A docstring da migração 121 (L6) continua chamando `rpe_target_effective` de "alvo do exercício", embora a coluna seja por série. O texto permanece no arquivo, porque a migração não pode ser editada depois de aplicada (Decisão, 7).

## Atualização de 22/11/2026 (#241, detalhe do treino)

Decisão do usuário de 22/11/2026: o detalhe do treino compara só séries de trabalho, como o histórico e o relatório. Mudança no dono: `effective_intensity.set_rpe_vs_expected` (usa `is_working_set`) é o único filtro; `rpe_vs_expected_counts` passou a usar essa função; `services.activity_detail` a chama por série. O relatório e o histórico não mudaram de número, porque já filtravam antes de contar. Limite (b) fechado. Impacto medido e limites: ver ADR 0026, "Atualização de 22/11/2026".

## Verificação

- Bateria dirigida do backend: 755 passaram.
- Bateria dirigida depois das correções: 67 passaram.
- Endpoint do relatório: 15 passaram.
- `ruff`: sem erro.
- Vitest do lote: 48 passaram; `tsc`: sem erro.
- Testes novos em `api/tests/unit/test_relatorio_rpe_esperado_x_realizado.py`, incluindo `test_mesmo_criterio_do_historico`, que passa as mesmas séries ao histórico e ao relatório e compara as contagens.
- Não verificado: integração contra MySQL real (roda só no CI, com o rótulo `ci-completo`); tempo da consulta na RDS Data API; navegador e iPhone.
