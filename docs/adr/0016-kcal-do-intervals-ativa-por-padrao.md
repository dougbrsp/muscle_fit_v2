# ADR 0016: A kcal informada do Intervals é calorie ATIVA por padrão (issue #138)

- **Status**: Aceita (decisão do usuário, 17/11/2026). Substitui em parte a [ADR 0008](0008-gasto-explicito-desconta-repouso-da-sessao.md) (desconto do repouso para a kcal INFORMADA) e a decisão D4 da [ADR 0005](0005-gasto-energetico-adaptativo-quarto-modo-em-sombra.md) (padrão do modo da kcal).
- **Data**: 2026-11-17

## Contexto

- ADR 0005, decisão D4: o modo da kcal do relógio (`active`, `gross`, `unknown`) é parâmetro do plano adaptativo (`watch_calorie_mode`); `unknown`, o padrão, era tratado como bruto (desconta o repouso da janela) com a marca `reduced_precision`.
- ADR 0008: nos modos explícito e automático sincronizado, a kcal de cada sessão do app e de cada atividade do Intervals entrava LÍQUIDA do repouso (`kcal − TMB/1440 × minutos`, piso 0), e o modo do relógio desses dois modos era `DEFAULT_TDEE_CALORIE_MODE = unknown`. A kcal por MET é sempre bruta e sempre desconta.
- Limitação registrada na 0008: as correções manuais de atividade (`activity_energy_corrections`) só valiam no adaptativo.
- Pedido do usuário na issue #138: toda kcal INFORMADA que vem do Intervals é caloria ativa. Motivo dado: o relógio já separa o consumo em descanso do consumo da atividade, e pelo Intervals só sobem as atividades que ele gravou; descontar o repouso de novo seria tirar duas vezes.

## Alternativas consideradas

1. **Manter `unknown` como padrão e só trocar o modo no plano.** Descartada: o usuário quer o padrão ativo para todos, sem ação por plano.
2. **Padrão ativo só para atividades importadas, mantendo o desconto na kcal informada do treino do app vinculado ao Intervals.** Descartada: a kcal do treino vinculado vem da mesma atividade do relógio; dois critérios para o mesmo número.
3. **Migrar os planos e as atividades antigas.** Descartada: a regra é padrão do motor; plano sem `energy_params` passa a significar ativa e nada é gravado em dado antigo.
4. **Medir o impacto em produção antes de aceitar.** Dispensada pelo usuário (ver "Impacto em produção").

## Decisão

1. Toda kcal INFORMADA que vem do Intervals é ATIVA por padrão: as atividades importadas e a kcal informada do treino do app vinculado ao Intervals. O dono do padrão é `activity_energy.DEFAULT_REPORTED_CALORIE_MODE`; `plan.DEFAULT_WATCH_CALORIE_MODE` e `energy_expenditure.DEFAULT_TDEE_CALORIE_MODE` derivam dele. `normalize_calorie_mode`: modo ausente ou vazio vale o padrão (ativa); o texto `unknown` explícito continua `unknown`.
2. Vale também para as atividades antigas (é o padrão do motor); sem migração de dados.
3. A kcal estimada por MET continua bruta, com desconto do repouso (`MET_KCAL_CALORIE_MODE`).
4. Correção manual de atividade do Intervals passa a valer também nos modos explícito e automático: `extra_activity_kcal` aplica `activity_energy.apply_activity_corrections` antes de normalizar e deduplicar. Para a atividade VINCULADA a um treino do app, o serviço lê a correção pela coluna `intervals_activity_id` de `activity_intervals_link` (`_SESSIONS_SQL`, `nutrition_targets_service.py`) e o motor usa só `reclassify` (`reclassified_calorie_mode`, em `resolve_sessions_kcal`); `exclude` é ignorado para a sessão vinculada, porque a exclusão nunca apaga o treino (a sessão continua contando). Decisão do Tech Lead no mesmo lote.
5. Precedência do modo da kcal informada: correção manual (`reclassify`) > modo explícito gravado no plano adaptativo (`energy_params.watch_calorie_mode`) > padrão ativa.
6. Leitura de refeição por IA (issue #131, mesmo lote, sem relação com a energia): item lido com confiança abaixo de `meal_parse.LOW_CONFIDENCE_BELOW` é marcado pelo motor (`is_low_confidence`, chave pública `low_confidence`) e a tela o destaca como "Estimativa a conferir". A tela só lê a marca.

## Trade-offs

- Ganha: o gasto de quem usa relógio com kcal ativa deixa de ser descontado duas vezes; um único padrão, com um dono, para os três consumidores.
- Paga: quem registra kcal TOTAL (com o repouso dentro) e não escolheu o modo passa a ter o gasto superestimado até escolher `gross` (só no adaptativo; nos modos explícito e automático o plano não guarda o modo).
- Paga: `unknown` deixa de ser o padrão e passa a ser uma escolha gravada de forma explícita.

## Consequências

- `ENGINE_VERSION` passa a `nutrition-v5` (`daily_target.py`). A versão entra no hash das entradas, então as metas NÃO congeladas são regravadas uma vez; dias congelados não mudam. Sem migração.
- Plano sem `energy_params` passa a significar ativa, inclusive os que escolheram "Não sei" antes, que eram gravados sem valor (era o padrão). `EnergyParams.to_dict`/hash só carrega o modo quando sai do padrão; `unknown` passa a ser gravado de forma explícita e continua descontando o repouso com a marca `reduced_precision`.
- Plano não adaptativo com `energy_params` fora do padrão continua recusado com `param_not_applicable:energy_params` (`plan.py`): nesses modos vale sempre o padrão.
- A correção manual passa a valer também nos modos explícito e automático (decisão 4); `EXCLUDED_ACTIVITY_REASON_LABELS` (`labels.py`) tem o rótulo de `EXCLUDED_BY_USER` ("Excluída por você: não somou"). O hint de `watch_calorie_mode` e `CALORIE_MODE_LABELS` (`labels.py`) descrevem ativa como padrão e `unknown` como "Não sei (tratado como total)".
- Testes-ouro de `api/tests/unit/test_nutrition_modos_legados_ouro.py` regravados de propósito nos cenários `explicit_treino` e `auto_sync_extras` (os demais só mudam o hash).

### Limitações (acrescentado em 2026-11-18)

- A correção manual não recalcula dia com meta congelada: a meta congela quando `hoje >= dia + TARGET_FREEZE_LAG_DAYS` (2) e `days_to_materialize` não escreve dia congelado (`daily_target.py`). A correção vale para hoje e para os dias ainda não congelados.
- A correção manual de atividade do Intervals vinculada a um treino vale para a kcal da sessão mesmo quando essa kcal foi digitada à mão (`activities.calories_source = manual`, migração 076): o motor aplica o `reclassify` a toda kcal informada da sessão, sem olhar a origem.

## O que NÃO muda

- A kcal por MET é bruta e desconta o repouso.
- `gross` e `unknown` explícitos continuam bruta: descontam o repouso; `unknown` mantém `reduced_precision`. Tratar `unknown` como ativa continua proibido (mapa `docs/motores/FONTES_DA_VERDADE.md`, §95).
- Sem duração, a kcal de modo que desconta entra como está, com `LIMIT_REST_NOT_DISCOUNTED`.
- A deduplicação com a sessão do app (vínculo `activity_intervals_link` e casamento por horário) e o teto `MAX_PLAUSIBLE_ACTIVITY_KCAL`.
- Embutido, fallback do automático e a regra de dia de treino.

## Impacto em produção

A medição de produção foi DISPENSADA pelo usuário em 17/11/2026 para esta mudança. É uma exceção pontual e não é precedente: a regra do projeto segue exigindo "Impacto em produção" medido para mudança de semântica.

Nada foi medido em produção. O efeito esperado, conferido nos testes-ouro (cenário de teste, não medição), é que o gasto sobe pelo repouso que deixa de ser descontado da kcal informada: em `explicit_treino`, a kcal do treino no gasto vai de 353,83 para 428; em `auto_sync_extras`, de 673,83 para 748 (428 + 200 + 120; os extras do cenário não têm duração e já não descontavam). Nesses cenários mudam gasto, meta e macros, e o hash de todos os cenários (versão do motor no hash). Em produção, as metas não congeladas dos planos explícito e automático com kcal informada sobem; no adaptativo, o plano sem o modo gravado passa de bruta a ativa. Rollback: reverter o código; o motor regrava as metas não congeladas.
