# Cálculos de nutrição: como a meta do dia é montada

- **Data de referência**: 08/11/2026.
- **Motor**: `api/app/domain/nutrition/`, `ENGINE_VERSION = "nutrition-v3"` (`daily_target.py`, L85).
- **Código lido**: worktree `agent-a9b5f1502049a983d`, branch `fix/tdee-desconta-repouso-sessao`. A leitura não usou git, então o commit `a091ddb3` informado pelo líder não foi conferido aqui. As linhas citadas foram conferidas nesta leitura e podem deslocar com novos commits; onde não há linha, a referência é o nome da função.
- **Fontes**: ADR 0005 (gasto adaptativo), ADR 0007 (gordura pela fração configurada), ADR 0008 (desconto do repouso, atividades do Intervals e dedup), `docs/motores/FONTES_DA_VERDADE.md` (§3.7 e §100).
- **Convenção**: todo número abaixo vem de uma constante nomeada ou de uma fórmula do código, com o arquivo indicado. Os valores do exemplo da seção 6 são os medidos em produção pela ADR 0008 (só leitura), salvo quando marcados como derivados.

## 1. Visão geral do caminho

O motor calcula um dia por vez. A função de entrada é `daily_target.compute_daily_target`. A ordem é esta:

1. **Perfil e composição.** O sujeito traz sexo, idade, altura, peso, %G, massa livre de gordura (MLG) e nível de atividade (`anthropometrics.Subject`). A MLG vem da bioimpedância ou de peso × (1 − %G) (`body_composition.resolved_ffm_kg`). A massa gorda vem de peso × %G ou de peso − MLG (`resolved_fat_mass_kg`).
2. **TMB.** `bmr.bmr` escolhe a fórmula e calcula o gasto basal (seção 2).
3. **Gasto de atividade do dia.** `energy_expenditure.resolve_sessions_kcal` soma as sessões do app. `extra_activity_kcal` soma as atividades do Intervals sem vínculo, depois de deduplicar e de tirar as duplicatas contra as sessões (seção 4). No modo explícito e no automático sincronizado, as sessões e as atividades saem líquidas do repouso.
4. **TDEE.** `effective_tdee_basis` decide o modo que vale no dia. `energy_expenditure.tdee` (ou `adaptive_tdee_result`, no adaptativo) monta o gasto total do dia (seção 3).
5. **Meta de kcal.** `strategy.kcal_target` aplica a estratégia (cutting, bulking ou manutenção) sobre o TDEE, com pisos e tetos (seção 3.5).
6. **Macros.** `macros.macro_split_for_priority` reparte a meta em proteína, gordura e carboidrato. A gordura segue a fração configurada sobre as kcal finais do dia (`macros.py`).
7. **Ciclo de carbo.** `carb_cycling.apply_cycle_over_base` aplica o multiplicador do tipo de dia sobre a base do plano. A gordura acompanha a kcal final, pela regra da ADR 0007 (`_day_with_fat_share`).
8. **Fibra e refeições.** A fibra é 14 g por 1.000 kcal (`macros.fiber_target_g`). A proteína por refeição divide a proteína pelo número de refeições (`macros.protein_per_meal`).
9. **Congelamento e regravação.** `nutrition_targets_service.inputs_fingerprint` gera o hash das entradas do dia, com a versão do motor dentro dele. `_unchanged` só deixa a meta como está quando o plano, a versão e o hash coincidem. Dia congelado nunca é escrito.

Não existe meta de água no motor. A água é um campo do diário (`nutrition_days.py`, `PUT /days/{date}`), não entra em macros nem em kcal.

## 2. TMB (taxa metabólica basal)

Dono: `api/app/domain/nutrition/bmr.py`.

| Fórmula | Equação | Constantes | Uso |
|---|---|---|---|
| Mifflin-St Jeor (1990) | 10·kg + 6,25·cm − 5·idade + 5 (homem) ou − 161 (mulher) | `MIFFLIN_*` (L37 a L41) | Padrão sem MLG recente |
| Katch-McArdle | 370 + 21,6·MLG | `KATCH_MCARDLE_INTERCEPT` (L44), `KATCH_MCARDLE_FFM_COEF` (L45) | Padrão com MLG recente |
| Cunningham (1980) | 500 + 22·MLG | `CUNNINGHAM_*` (L48, L49) | Só por escolha explícita |
| Harris-Benedict revisada | coeficientes em `HARRIS_BENEDICT_MALE/FEMALE` (L52, L53) | não | Só comparação, nunca automática |

**Seleção automática** (`bmr.select_formula`):

- Se a MLG existe **e** é recente, usa Katch-McArdle.
- Caso contrário, usa Mifflin-St Jeor.
- "Recente" quer dizer até `FFM_FRESHNESS_MAX_DAYS = 60` dias (L56). MLG sem data conta como recente (`ffm_is_fresh`).
- Sexo "outro" sem MLG não calcula TMB, porque Mifflin exige sexo biológico. Com MLG, usa Katch-McArdle, que não depende do sexo.

**Comparação com a balança.** O `bmr_kcal` da balança nunca é dono da TMB. `bmr.compare_with_scale` calcula o desvio relativo. Se o desvio passa de `SCALE_BMR_DEVIATION_WARN_SHARE = 0,15` (L59), a comparação vira aviso.

**Frescor da massa magra.** No automático, a MLG com mais de 60 dias sai da seleção e a TMB passa a ser de Mifflin, sem limitação. Só a escolha explícita de Katch-McArdle com MLG antiga grava `ffm_stale` (`bmr.bmr`, L197 a L198).

**Exemplo do usuário 1, plano 10.** MLG 76,4 kg, Katch-McArdle: 370 + 21,6 × 76,4 = **2.020,24 kcal**.

## 3. Os quatro modos de gasto

O modo fica em `PlanParams.tdee_mode` (`energy_expenditure.TdeeMode`). O padrão é `explicit_training` (`DEFAULT_TDEE_MODE`).

**Regras que valem em todos os modos:**

- Atividade do Intervals que não é treino do app não faz o dia virar dia de treino. `is_training_day` e o tipo de dia do ciclo de carbo não mudam.
- Dia de treino é definido pela existência de sessão concluída do app, não pela kcal líquida. Uma sessão com kcal líquida igual a zero continua sendo dia de treino (`training_kcal_of_day` trata zero como valor conhecido).
- `ENGINE_VERSION` é `nutrition-v3`.
- A meta de hoje e a dos dias não congelados recalcula quando chega atividade nova, porque o `inputs_hash` inclui as atividades do contexto do dia.

### 3.1 Modo explícito (`explicit_training`)

**Fórmula**: TDEE = TMB × fator de não exercício + treino líquido do app + extras líquidos do Intervals.

- Fator: `NON_EXERCISE_ACTIVITY_FACTORS` (`anthropometrics.py`, L68): sedentário 1,2; leve 1,3; moderado 1,4; ativo 1,5; muito ativo 1,6. Decisão D-A do plano, sem fonte canônica única.
- **Treino do app**: `resolve_sessions_kcal` soma por sessão. Cada sessão sai líquida do repouso: kcal − TMB ÷ 1.440 × minutos, com piso zero. A kcal informada é o número do relógio. Sessão sem kcal usa MET.
- **Extras do Intervals**: `extra_activity_kcal` soma toda atividade sem vínculo com sessão, líquida do repouso da janela dela (seção 4).
- Só soma treino e extras quando o dia é conhecido (hoje ou passado). Em dia futuro o treino fica como desconhecido (`LIMIT_TRAINING_UNKNOWN_FUTURE_DAY`).
- **Fallback**: se faltar TMB ou nível de atividade, o TDEE sai `None` e a limitação correspondente aparece (`missing_bmr`, `missing_activity_level`).

### 3.2 Modo embutido no fator (`included_in_factor`)

**Fórmula**: TDEE = TMB × fator com exercício.

- Fator: `ACTIVITY_FACTORS` (`anthropometrics.py`, L59): sedentário 1,2; leve 1,375; moderado 1,55; ativo 1,725; muito ativo 1,9. TEF já embutido.
- Treino e atividades do Intervals são ignorados no gasto, porque já estão no fator.
- O `training_kcal` gravado na meta fica **bruto**, como antes desta mudança. A tela mostra a base sem parcela de treino (`tdee_breakdown`, nota `training_included_in_factor`).

### 3.3 Modo automático pelo Intervals (`auto_intervals`)

Este modo é uma escolha de dois caminhos, decidida dia a dia (`effective_tdee_basis`).

- **Sincronizado** (`intervals_in_sync`): a conta do Intervals está conectada **e** a última sincronização tem até `INTERVALS_SYNC_MAX_AGE_MINUTES = 36 × 60` minutos. O dia vale como explícito, com o nível de **rotina** do plano (`routine_level`). Se o plano não tem rotina, vale `ROUTINE_ACTIVITY_LEVEL_DEFAULT = LIGHT`. Treino líquido do app e extras líquidos entram, com as mesmas regras do explícito.
- **Sem sincronização** (sem conexão ou sync parado há mais de 36 h): o dia vale como embutido, com o fator com exercício do **perfil**. Treino e atividades são ignorados no gasto. O `training_kcal` gravado fica **bruto**.
- O plano automático sem nível de atividade no perfil é bloqueado para salvar e publicar (`LIMIT_AUTO_REQUIRES_PROFILE_LEVEL`).
- Janela de frescor: a Lambda de sync roda 1 vez por dia às 05:45 BRT. As 36 h são um ciclo mais 12 h de folga.

### 3.4 Modo adaptativo (`adaptive_balance`)

Este é o quarto modo, criado pela ADR 0005. Ele não usa fator × TMB. O gasto do dia vem de `adaptive_energy.estimate_for_day`.

- **Sem desconto de repouso do explícito.** O treino do app não entra no gasto do dia. `compute_daily_target` ignora a kcal da sessão no modo adaptativo. O `training_kcal` gravado fica bruto.
- O desconto do repouso das atividades é próprio deste modo, pelo `watch_calorie_mode` do plano (`activity_energy`, seção 4).
- Atividades do Intervals entram pelo histórico (`load_history`, não auditado nesta leitura), com a deduplicação do `activity_energy`.

**Fórmula do dia**: estimativa = base + ajuste.

- **Base**: `base = média_bootstrap + (adaptativo − média_bootstrap) × confiança` (`adaptive_energy._advance`). Sem gasto adaptativo ainda, a base é a estimativa inicial.
- **Estimativa inicial (bootstrap)**: TMB × `BOOTSTRAP_ACTIVITY_FACTOR` (= fator sedentário 1,2), somada à atividade do dia ou à média da janela.
- **Gasto observado**: `observado = média da ingestão (dias completos) − inclinação do peso (kg/dia) × densidade (kcal/kg)`.
- **Densidade**: `tissue_energy_density` (`energy_density.py`). Fração de gordura = MG / (MG + 10,4). Densidade = fração × 9.400 + (1 − fração) × 1.800 (`FAT_TISSUE_KCAL_PER_KG`, `LEAN_TISSUE_KCAL_PER_KG`).
- **Suavização**: `EXPENDITURE_ALPHA = 0,20`. A variação por atualização é limitada a `EXPENDITURE_MAX_STEP_KCAL = 75` kcal.
- **Ajuste do dia**: `(atividade ativa do dia − média da atividade ativa da janela) × 0,60`, limitado entre −250 e +300 kcal (`ACTIVITY_ADJUSTMENT_*`). Só vale em dia passado com atividade confirmada. Hoje e dia futuro não recebem ajuste (`day_open`).
- **Confiança**: `clamp((dias válidos − 7) / 14, 0, 1)`. Nível baixo abaixo de 0,40, médio de 0,40 a 0,80, alto acima de 0,80 (`confidence_level`).
- **Peso de tendência**: EMA de meia-vida de 7 dias, `WEIGHT_ALPHA = 1 − 2^(−1/7) ≈ 0,0943` (`weight_trend.py`, L54 e L55). Janela de 21 dias (`TREND_WINDOW_DAYS`). A inclinação vem de regressão sobre a janela (`assess_window`).

**Suficiência e suspensão**: a atualização só ocorre se a janela estiver suficiente. Os critérios estão nas constantes de `adaptive_energy.py`:

- pelo menos 14 dias completos na janela (`MIN_COMPLETE_DAYS_IN_WINDOW`);
- pelo menos 14 dias de histórico de peso (`MIN_HISTORY_DAYS`, em `weight_trend.py`);
- pelo menos 2 pesagens por semana (`MIN_WEIGHINS_PER_WEEK`);
- inclinação plausível: |inclinação| × 7 ≤ 1,5 kg por semana (`MAX_PLAUSIBLE_WEEKLY_CHANGE_KG`);
- gasto observado entre 0,9 e 3,0 × TMB (`OBSERVED_BMR_SHARE_RANGE`);
- massa gorda conhecida (senão `fat_mass_missing`).

Quando há suspensão, a confiança é limitada a 0,5 (`SUSPENDED_CONFIDENCE_CAP`) e o gasto adaptativo não muda naquele dia.

**Ingestão válida**: um dia só conta como completo se a ingestão chega a 70% da TMB (`COMPLETE_MIN_INTAKE_BMR_SHARE`). Dia parcial fica fora da média.

**Histórico**: o replay cobre 90 dias (`REPLAY_HISTORY_DAYS`). O estado é recalculado por `replay`, sem tabela persistida (decisão D5 da ADR 0005).

**Sem TMB**: o TDEE é `None` (`LIMIT_ADAPTIVE_ESTIMATE_MISSING`).

**Leitura do histórico falhou**: o motor usa a estimativa inicial marcada com `history_unavailable`, e o serviço não sobrescreve a meta já gravada.

### 3.5 Meta de kcal (estratégia, ritmo, pisos e tetos)

Dono: `api/app/domain/nutrition/strategy.py`. Entrada: `kcal_target(params, tdee, bmr, peso, massa_gorda)`.

**Cutting**:

- Ritmo: `CUT_WEEKLY_RATE_SHARE_DEFAULT = 0,007` do peso por semana (L67). Faixa 0,5% a 1,0% (L66).
- Déficit diário: `daily_kcal_for_weekly_rate(peso × ritmo, massa gorda)` (`energy_density.py`, L50). Em termos práticos, **déficit (kcal/dia) = peso (kg) × densidade (kcal/kg) ÷ 1.000**, porque 0,007 ÷ 7 = 0,001.
- Teto do déficit: `CUT_MAX_DEFICIT_SHARE = 0,25` do TDEE (L68). Clamp `max_deficit_share`.
- Piso de ingestão: `CUT_MIN_INTAKE_BMR_MULTIPLE = 1,0` × TMB (L69). Clamp `min_intake_bmr_multiple`.
- Sem massa gorda, o alvo do cutting não é calculável (`missing_fat_mass`). Não existe densidade fixa de 7.700 kcal/kg (ADR 0005, alternativa 3).

**Bulking**:

- Superávit: 10% do TDEE por padrão (`BULK_SURPLUS_SHARE_DEFAULT = 0,10`, L77). Avançado: 5% (L79).
- Piso de carboidrato: `BULK_CARB_MIN_G_PER_KG = 3,0` (L97). É só um aviso (`carb_below_min_g_per_kg`), não um clamp.

**Manutenção**:

- Ajuste em kcal/dia, padrão zero, faixa ±500 (`MAINT_KCAL_ADJUST_RANGE`, L108).
- Com ajuste negativo, o piso é TMB × 1,0 e o bloqueio mínimo `KCAL_BLOCK_MIN = 800` (`manual_targets.py`, L72). Os dois pisos ficam em `clamped_by` (`_maintenance_with_cut`).

**Fases**: `DIET_BREAK` e `REFEED` levam a estratégia à manutenção (`MAINTENANCE_KCAL_PHASES`, `phases.py`). A proteína e a gordura do plano são mantidas.

### 3.6 Ciclo de carbo

Dono: `api/app/domain/nutrition/carb_cycling.py`. Vale quando o plano tem `cycle_kinds`, inclusive com gramas manuais. A gordura acompanha a kcal final do dia só sem gramas manuais (regra da ADR 0007).

- Multiplicadores: alto 1,25, normal 1,0, baixo 0,75 (`CARB_CYCLE_SWING_SHARE = 0,25`).
- Se a semana não tem a mesma quantidade de dias altos e baixos, os multiplicadores são reescalados para somar 7 (`week_multipliers`).
- **Piso do dia baixo**: 1 g/kg de peso (`MIN_LOW_CARB_G_PER_KG`). Se o piso prende, o dia alto absorve o resto. Se nem assim cabe, a semana volta a plana com `carb_floor_prevents_cycle`.
- **Base**: o delta do dia é `carbo da base × (multiplicador − 1)`. A base é a meta do mesmo dia sem o treino explícito (`_cycle_base`). Por isso a soma semanal do ciclo é zero, qualquer que seja o treino de cada dia.
- **Gordura**: com `keep_fat_share=True`, a kcal final é a kcal do dia mais 4 × delta de carbo. A gordura recebe a fração efetiva do dia sobre essa kcal final, e o carbo fica com o resto (`_day_with_fat_share`).
- **Dias do usuário**: o mapa é `"0"` a `"6"`, com `"0"` = segunda-feira. O código usa `day.weekday()`. `plan.cycle_days` confirma a convenção.

### 3.7 Macros

Dono: `api/app/domain/nutrition/macros.py`.

- Atwater: proteína 4 kcal/g, carboidrato 4 kcal/g, gordura 9 kcal/g.
- **Proteína**: g/kg × base. A base é a MLG no cutting (`FAT_FREE_MASS`) e o peso nas demais estratégias (`ProteinBasis`). Padrão do cutting: 2,6 g/kg de MLG (`CUT_PROTEIN_G_PER_KG_FFM_DEFAULT`, `strategy.py`), faixa 2,3 a 3,1.
- **Gordura**: fração configurada das kcal finais do dia. Em `fixed_protein` e `balanced`, vale `params.fat_kcal_share`. Em `high_carb`, vale o piso da faixa da estratégia. Em `high_fat`, vale o teto, sem deixar o carboidrato abaixo do piso (`_fat_share_for_priority`).
- **Carboidrato**: resto das kcal, nunca negativo. Se faltar energia, zera e marca `kcal_below_protein_and_fat`.
- **Fibra**: 14 g por 1.000 kcal.
- **Água**: não é calculada pelo motor de meta.

### 3.8 Congelamento e regravação

Dono: `daily_target.py` (`TARGET_FREEZE_LAG_DAYS`) e `nutrition_targets_service.py`.

- `is_target_frozen(dia, hoje) = hoje ≥ dia + 2`. Na data de 08/11, estão congelados até 06/11. Ontem (07/11), hoje (08/11) e os dias futuros ainda mudam.
- `inputs_fingerprint` gera o hash com `plan_id`, `engine_version`, sujeito, parâmetros do plano e contexto do dia.
- `_unchanged` compara plano, versão e hash. Trocar `ENGINE_VERSION` faz as metas não congeladas serem regravadas na próxima leitura (ADR 0007 e ADR 0008).
- **Atividade nova no contexto**: o contexto do dia agora inclui o início e o id externo de cada atividade do Intervals. Isso muda o hash de todo dia que tem atividade extra, e esses dias são regravados uma vez na próxima leitura, mesmo sem mudança de versão.
- Dia congelado sem meta ganha uma meta única, marcada como `target_backfilled` (`backfill_marker`).

## 4. Regras de atividade

### 4.1 Prioridade da kcal do treino

`TRAINING_KCAL_NATURE_PRIORITY` (`energy_expenditure.py`) define a ordem: primeiro `reported` (kcal informada), depois `met` (estimativa). A ordem vale por sessão. Sessão sem kcal usa MET e as demais usam a kcal informada.

**Caveat**: a estimativa por MET usa o tipo da sessão, mas nenhum caminho de produção preenche `session_kind`. Ver seção 8.

### 4.2 Constantes MET

| Código Compendium | Tipo | MET | Constante |
|---|---|---|---|
| 02050 | vigoroso | 6,0 | `MET_STRENGTH_VIGOROUS` (L62) |
| 02052 | agachamento e levantamento terra | 5,0 | `MET_STRENGTH_SQUAT_DEADLIFT` (L63) |
| 02054 | múltiplos exercícios 8 a 15 reps | 3,5 | `MET_STRENGTH_MULTIPLE_EXERCISES` (L64), padrão |
| 02055 | circuito | 5,8 | `MET_STRENGTH_CIRCUIT` (L65) |

Fórmula: kcal = MET × 3,5 × kg ÷ 200 × minutos (`training_kcal_met`). Por kg e por minuto, a conta dá 3,5 × 3,5 ÷ 200 = **0,06125 kcal**. Decisão D-K: não há fórmula por frequência cardíaca (Keytel).

### 4.3 Desconto do repouso

Fórmula da janela: `repouso = TMB ÷ 1.440 × minutos` (`activity_energy.resting_kcal`). Para o plano 10, isso dá 2.020,24 ÷ 1.440 = **1,403 kcal por minuto**.

Regra única (`activity_energy.net_of_resting_kcal`):

- **Modo `active`**: a kcal entra como veio.
- **Modo `gross`** (total, com o repouso dentro): líquida = max(0; kcal − repouso).
- **Modo `unknown`**: tratada como bruta, desconta o repouso, e no adaptativo recebe a marca `reduced_precision`.
- **MET**: sempre bruta (`MET_KCAL_CALORIE_MODE = GROSS`), sempre desconta.
- **Modo padrão do relógio** no explícito e automático: `unknown` (`DEFAULT_TDEE_CALORIE_MODE`). Ou seja, a kcal do relógio é descontada.
- **Onde se aplica**: explícito e automático sincronizado. No embutido, no fallback do automático e no adaptativo a kcal do treino não é descontada pelo modo explícito (o adaptativo tem o próprio desconto).
- **Sem duração**: a kcal entra como está e sai `training_rest_not_discounted` (`LIMIT_REST_NOT_DISCOUNTED`).
- **Sem TMB**: a kcal entra como está, sem aviso de desconto (o gasto já fica desconhecido por outra limitação).
- O tempo de desconto é o **tempo decorrido** (`elapsed_time_seconds`), com o tempo em movimento só como segunda opção (`activity_duration_seconds`).

### 4.4 Vínculo app × Intervals e casamento por horário

A consulta de atividades do Intervals (`EXTRA_ACTIVITIES_SQL`, `nutrition_targets_service.py`) faz `LEFT JOIN` com `activity_intervals_link` e pega só as linhas sem vínculo (`l.id IS NULL`).

- **Vinculada**: a atividade do Intervals ligada a uma sessão do app soma **uma vez**, pela sessão (kcal e duração da sessão). O vínculo é criado no sync.
- **Não vinculada, mas casada por horário**: mesmo sem vínculo, a atividade do Intervals que é possível duplicata de uma sessão do app de força (início a até 10 minutos e duração dentro de 20%) não soma. A sessão é a que conta.
- **Não vinculada e sem casamento**: soma como atividade extra, líquida do repouso.
- **Dependência**: a proteção contra dupla contagem depende do vínculo e, na falta dele, do horário. A sessão sem `started_at` não entra no casamento por horário. Nesse caso, a musculação do Intervals soma além da sessão até o vínculo existir.

### 4.5 Deduplicação

A deduplicação é a do dono (`activity_energy.deduplicate`), reaproveitada pelos modos explícito e automático. Entram na lista as atividades do Intervals sem vínculo e as sessões do app com horário, como atividades de força.

**Regras**, em ordem:

1. **Mesmo id externo**: duplicata certa. Não soma (`duplicate`).
2. **Mesma impressão** (fonte, início ao minuto, duração ao minuto, tipo e kcal de 5 em 5): duplicata certa, desde que os dois não tenham ids externos distintos. Não soma (`duplicate`).
3. **Possível duplicata contra sessão do app** (mesmo tipo, início a até 10 minutos, duração dentro de 20%): não soma. A sessão conta uma vez (`duplicate`).
4. **Possível duplicata entre atividades do Intervals** (ids distintos): **as duas contam** e a linha recebe o aviso `possible_double_count`. Excluir sem certeza esconderia treino real.
5. **Acima de `MAX_PLAUSIBLE_ACTIVITY_KCAL` (5.000 kcal)**: não soma (`above_ceiling`).
6. **Sem kcal informada**: não soma e não é estimada (`no_kcal`, `extra_activity_without_kcal`).

**Aviso na tela**: o rótulo de `possible_double_count` diz "Possível treino em duplicidade: os dois entraram no gasto; exclua um se for o mesmo" (`labels.py`).

**Limite**: a correção manual de atividade (`activity_energy_corrections`) não se aplica ao gasto explícito e automático (ADR 0008). Por isso, o usuário não consegue excluir a duplicata pela tela nesses modos.

**Adaptativo**: usa o mesmo tratamento de duplicata e de possível duplicata, e é o único modo que aceita a correção manual de atividade.

### 4.6 Quem soma em cada modo

| Item | Explícito | Embutido | Automático sincronizado | Automático fallback | Adaptativo |
|---|---|---|---|---|---|
| Sessão do app (kcal informada) | soma, líquida | não | soma, líquida | não | não entra no gasto |
| Sessão do app sem kcal | MET, líquida | não | MET, líquida | não | não entra no gasto |
| Atividade do Intervals sem vínculo, sem casamento | soma, líquida | não | soma, líquida | não | entra pelo histórico |
| Atividade casada por horário com sessão do app | não soma (a sessão conta) | não | não soma | não | conforme o `activity_energy` |
| Duplicata certa (id externo ou impressão) | não soma | não | não soma | não | não conta no total |
| Possível duplicata entre Intervals (ids distintos) | soma as duas, com aviso | não | soma as duas, com aviso | não | conta no total, com aviso |
| Atividade sem kcal | não soma, não estima | não | não soma | não | não conta no total |
| Atividade acima de 5.000 kcal | não soma | não | não soma | não | não conta no total |
| Atividade sem duração | soma bruta, com limitação | não | soma bruta, com limitação | não | fora do total se a kcal for bruta (`missing_duration`); entra se for ativa |
| Dia futuro | não soma treino nem extras | não | não soma | não | só a base, sem ajuste |

## 5. Fontes de erro e sensibilidade

Os valores abaixo são calculados com as constantes do código. Nenhum é medido em produção, exceto onde indicado.

| Fonte de erro | Variação | Efeito no plano 10 |
|---|---|---|
| MLG de bioimpedância | ±1 kg | TMB ±21,6 kcal; gasto ±28,1 kcal (× 1,3); proteína ±2,6 g (cutting) |
| %G quando a MLG vem de peso × (1 − %G) | ±1 ponto percentual | MLG ±0,01 × peso. TMB ±0,216 kcal por kg de peso corporal; por exemplo, 80 kg dão ±17 kcal de TMB e ±22 kcal de gasto |
| Fator de rotina | leve 1,3 para moderado 1,4 | +202 kcal/dia (0,1 × TMB) |
| Kcal do relógio em musculação | cada kcal | 1 para 1, depois de descontar 1,403 kcal/min do repouso |
| Estimativa MET (sem kcal do relógio) | por kg de peso | 0,06125 kcal por kg e por minuto. Em 107 min, 6,6 kcal por kg |
| Desconto do repouso | por minuto | 1,403 kcal/min; a sessão de 107 min perde 150 kcal |
| **Vínculo app × Intervals falhou** | sessão sem `started_at`, ou horário e duração fora da tolerância | a musculação do Intervals soma de novo, além da sessão. No dia 07/11 isso poderia somar até a kcal líquida da sessão, 723,6 kcal |
| **Id externo ausente ou diferente (Zepp + Strava)** | mesmo treino com ids distintos e kcal que não bate na impressão | a dedup não marca duplicata certa. Se início e duração batem, as duas contam e sai `possible_double_count`. O treino entra duas vezes na kcal líquida |
| Déficit do cutting | peso × densidade ÷ 1.000 | depende do peso e da massa gorda, que não estão nos documentos do repositório |
| Atividade do Intervals sem kcal | ausente | zero (nenhuma estimativa) |
| Ciclo de carbo | ±25% do carbo da base | ±213 kcal/dia (carbo base de 213 g) |
| Ingestão subregistrada (adaptativo) | −10% de 2.500 kcal | erro de 250 kcal no gasto observado, com sinal conforme o caso |
| Peso de tendência (adaptativo) | erro de 0,1 kg/semana com densidade de 6.800 kcal/kg | 97 kcal/dia no gasto observado |

Densidade por massa gorda (`tissue_energy_density`): MG 10 kg dá 5.526 kcal/kg; MG 15 kg dá 6.289; MG 20 kg dá 6.800; MG 30 kg dá 7.444.

Registro incompleto de ingestão afeta só o adaptativo: dia parcial sai da média, e a confiança cai com menos dias completos.

## 6. Exemplo numérico: plano 10, usuário 1

### 6.1 Entradas

- Plano 10: cutting, `fixed_protein`, proteína 2,6 g/kg de MLG, gordura 20% das kcal, ciclo por dias, modo explícito, nível leve (fator 1,3).
- MLG 76,4 kg, TMB 2.020,24 (Katch-McArdle).
- Ritmo 0,7% do peso por semana.
- Ciclo: `{"0":"normal","1":"high","2":"low","3":"normal","4":"normal","5":"high","6":"low"}`. Ou seja: segunda normal, terça alta, quarta baixa, quinta normal, sexta normal, sábado alto, domingo baixo. Ver seção 8 sobre a descrição divergente.

### 6.2 Déficit derivado

O déficit do cutting depende do peso e da massa gorda do usuário, que não estão nos documentos. Por isso, o valor usado aqui é **derivado** das metas gravadas em produção pela versão nutrition-v1, na conta reversa com o ciclo (ADR 0007):

- sábado 07/11 gravado: 3.093,55 kcal;
- domingo 08/11 gravado: 1.896,52 kcal;
- os dois fecham com um déficit de **≈ 568 kcal/dia**. A regra de déficit não mudou entre v1 e v3.
- Teto de 25% do TDEE e piso de TMB não atuaram nesses dias.

### 6.3 Base sem treino (usada pelo ciclo)

- TMB × 1,3 = 2.626,31 kcal.
- Alvo base = 2.626,31 − 568 = **2.058,3 kcal**.
- Proteína 198,64 g (794,56 kcal). Gordura 20%: 411,7 kcal = 45,7 g. Carbo = (2.058,3 − 794,56 − 411,7) ÷ 4 = **213,0 g**.

### 6.4 Sábado 07/11 (alto, multiplicador 1,25)

Gasto do dia:

- Sessão do app: 6.433 s = 107,2 min; kcal informada 874; repouso 1,403 × 107,2 = 150,4; líquida **723,6**.
- Walk 718 s, 36 kcal: repouso 16,8; líquida 19,2.
- Walk 782 s, 73 kcal: repouso 18,3; líquida 54,7.
- Extras líquidos: **73,9**.
- TDEE = 2.626,31 + 723,6 + 73,9 = **3.423,8 kcal**.

As duas caminhadas não casam com a sessão de força (tipo diferente), então nenhuma sai pela dedup contra a sessão. Os horários dos dois Walks não foram verificados. Se estiverem a até 10 minutos um do outro, o aviso `possible_double_count` aparece, mas a kcal não muda, porque as duas já contam.

Meta antes do ciclo:

- Alvo = 3.423,8 − 568 = **2.855,8 kcal**.
- Proteína 198,64 g; gordura 20% = 63,5 g; carbo = (2.855,8 − 794,56 − 571,2) ÷ 4 = **372,5 g**.

Ciclo alto:

- Delta de carbo = 213,0 × 0,25 = 53,3 g, ou 213 kcal.
- kcal final = 2.855,8 + 213,0 = **3.068,8 kcal**.
- Gordura = 0,20 × 3.068,8 ÷ 9 = **68,2 g** (20,0%).
- Carbo = (3.068,8 − 794,56 − 613,8) ÷ 4 = **415,1 g**.
- Fibra = 14 × 3.068,8 ÷ 1.000 = 43,0 g.

**Resultado v3: 3.068,8 kcal, P 198,6 g, C 415,1 g, G 68,2 g.** Gravado em v1: 3.093,6 kcal, C 354,8 g, G 97,7 g (28,4%).

### 6.5 Domingo 08/11 (baixo, multiplicador 0,75)

Gasto do dia (sem sessão do app):

- VirtualRun 2.601 s, 436 kcal: repouso 1,403 × 43,35 = 60,8; líquida **375,2**. Não há sessão de força no dia, então não há casamento.
- TDEE = 2.626,31 + 375,2 = **3.001,5 kcal**.

Meta antes do ciclo:

- Alvo = 3.001,5 − 568 = **2.433,5 kcal**.
- Proteína 198,64 g; gordura 20% = 54,1 g; carbo = (2.433,5 − 794,56 − 486,7) ÷ 4 = **288,1 g**.

Ciclo baixo:

- Delta de carbo = 213,0 × (0,75 − 1) = −53,3 g, ou −213 kcal.
- kcal final = 2.433,5 − 213,0 = **2.220,5 kcal**.
- Gordura = 0,20 × 2.220,5 ÷ 9 = **49,3 g** (20,0%).
- Carbo = (2.220,5 − 794,56 − 444,1) ÷ 4 = **245,5 g**.
- Fibra = 31,1 g.
- O piso de 1 g/kg só prenderia se o peso passasse de 0,75 × 213,0 = cerca de 160 kg. Não prende.

**Resultado v3: 2.220,5 kcal, P 198,6 g, C 245,5 g, G 49,3 g.** Gravado em v1: 1.896,5 kcal, C 121,1 g, G 68,6 g (32,6%).

### 6.6 Tabela de impacto (ADR 0008, versão atual)

Os valores são o gasto total do dia, em kcal. "Antes" é o motor sem o desconto do repouso e sem os extras no explícito. "Depois" é o motor da versão nutrition-v3. Medido em produção em 08/11/2026, só leitura. A revisão desta versão conferiu que a dedup não muda estes números: nenhuma atividade da tabela casa com a sessão de força do dia, e a regra de desconto explícito é a mesma.

| dia | TMB | sessão do app (s / kcal) | repouso da sessão | sessão líquida | atividades do Intervals sem vínculo (s / kcal) | extras líquidos | gasto antes | gasto depois | variação |
|---|---|---|---|---|---|---|---|---|---|
| 02/11 | 2020,24 | 7943 / 763 | 185,7 | 577,3 | VirtualRun 604/36; RockClimbing 1201/206 | 199,8 | 3.389 | 3.403 | +14 |
| 03/11 | 2015,92 | 6357 / 707 | 148,3 | 558,7 | Walk 680/74; Walk 678/56 | 98,3 | 3.328 | 3.278 | −50 |
| 05/11 | 2020,24 | 4859 / 428 | 113,6 | 314,4 | Walk 749/51; VirtualRun 1446/265; Walk 815/79 | 324,6 | 3.054 | 3.265 | +211 |
| 06/11 | 2014,00 | 5304 / 508 | 123,6 | 384,4 | Elliptical 310/25; VirtualRun 1360/233 | 219,1 | 3.126 | 3.222 | +95 |
| 07/11 | 2020,24 | 6433 / 874 | 150,4 | 723,6 | Walk 718/36; Walk 782/73 | 73,9 | 3.500 | 3.424 | −76 |
| 08/11 | 2020,24 | sem sessão | 0 | 0 | VirtualRun 2601/436 | 375,2 | 2.626 | 3.001 | +375 |

Só 07/11 e 08/11 são regravados. Os dias de 02/11 a 06/11 estão congelados e não mudam.

### 6.7 Mudança das metas de 07/11 e 08/11

| dia | v1 gravado (kcal / P / C / G / % gordura) | v3 calculado (kcal / P / C / G / % gordura) | variação kcal |
|---|---|---|---|
| 07/11 (sáb, alto) | 3.093,6 / 198,6 / 354,8 / 97,7 / 28,4% | 3.068,8 / 198,6 / 415,1 / 68,2 / 20,0% | −24,7 |
| 08/11 (dom, baixo) | 1.896,5 / 198,6 / 121,1 / 68,6 / 32,6% | 2.220,5 / 198,6 / 245,5 / 49,3 / 20,0% | +323,9 |

Em 07/11, o gasto cai 76 kcal, e a meta cai as mesmas 76 kcal antes do ciclo. A fração de gordura sai de 28,4% para 20,0%, e o carbo ganha o que a gordura perdeu. Em 08/11, a maior parte da mudança vem do VirtualRun de 375 kcal, que entra no alvo do próprio dia.

## 7. Pontos em aberto e decisões pendentes

Os itens abaixo vêm dos ADRs e do mapa, e foram verificados no código.

- **Duplicata que não se resolve pelo usuário no explícito**: a possível duplicata entre atividades do Intervals (Zepp + Strava, ids distintos) conta as duas e avisa, mas a correção manual não se aplica ao explícito e ao automático (ADR 0008, limitação 2). Hoje, a única saída é desvincular ou corrigir no Intervals, ou mudar para o adaptativo. Decisão pendente: liberar a exclusão manual nesses modos.
- **Fator de não exercício sem fonte única** (decisão D-A, `anthropometrics.py`): os valores 1,2 a 1,6 foram adotados com o adaptativo ao lado. Não há citação bibliográfica própria.
- **Defaults de desenho sem validação** do adaptativo: fração 0,70 da TMB, plausibilidade de 1,5 kg/semana, teto de 0,5 em suspensão, limiares de confiança, histórico de 90 dias e sequência máxima de 7 dias de descanso confirmado (ADR 0005).
- **Decisões D2 e D5 da ADR 0005**: resolvidas no Lote C. O adaptativo governa a meta só nos planos que o escolhem, e o estado é recalculado, sem tabela persistida.
- **Lote D (explicabilidade)** e **Lote E (robustez)** do adaptativo: pendentes (ADR 0005). O Lote E inclui fuso do usuário e reclassificação manual mais completa.
- **Musculação ainda não vinculada e sem casamento por horário** soma em dobro até o vínculo existir no sync (ADR 0008, limitação 1).
- **Modo do relógio** no explícito e automático fixo em `unknown` (`DEFAULT_TDEE_CALORIE_MODE`). Se o relógio for de kcal ativa, o desconto do repouso fica a mais. Não há campo no plano para isso fora do adaptativo.
- **Faixas de bulking em planos com carbo baixo**: planos `fixed_protein` em bulking podem sair abaixo de 3 g/kg de carbo, com aviso apenas (ADR 0007, nota 2).
- **Dias sem atividade com relógio parado**: com registro de execuções, a trava de sequência vale também dentro da janela de 30 dias. Sem registro (dias antigos), a trava vale só fora dela (`activity_energy.py`). Até 7 dias seguidos sem atividade contam como descanso confirmado, mesmo que o relógio esteja parado.

## 8. Divergências entre código e documentação

- **Descrição do ciclo no briefing**: o pedido descrevia segunda alta, terça baixa, quarta, quinta e domingo normais, sexta alta e sábado baixo. O JSON do plano difere nesses dias: sábado alto e domingo baixo. Este relatório usa o JSON, que bate com a ADR 0007 (sábado alto, domingo baixo) e com os valores medidos.
- **Água como meta**: o briefing listava água entre os itens das macros. O motor de meta não calcula água. Ela existe só como campo do diário (`nutrition_days.py`).
- **MET por tipo de sessão é caminho morto em produção**: `met_for_session` aceita o tipo (02050, 02052, 02055), mas `day_context_for` nunca preenche `session_kind`. Só o teste unitário define o tipo (`test_nutrition_daily_target.py`). Em produção, toda sessão sem kcal usa MET 3,5 (`nutrition_days.py` chama `met_for_session(None)`).
- **Exemplo da ADR 0007**: a ADR diz "pré-ciclo 2.940 kcal e base sem treino ~2.037 kcal". Os valores gravados do usuário 1 dão pré-ciclo ≈ 2.932 e base ≈ 2.058. A diferença sugere outro cenário no exemplo. A ADR não diz qual.
- **Dedup e manutenção manual**: a ADR 0008 diz que a correção manual não se aplica ao explícito, mas a deduplicação agora faz parte do gasto desses modos. Um caso de duplicata que o motor não resolve fica sem saída pela tela. Está registrado na seção 7.
