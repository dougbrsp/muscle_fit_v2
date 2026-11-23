# ADR 0029: Ciclo de carboidratos com compensação em gordura (issue #242)

- **Status**: Aceita. Branch `feat/ciclo-compensacao-gordura-242`. Não publicada nem promovida a produção. Trechos superados em 22/11/2026 pela [ADR 0033](0033-politica-do-ciclo-em-coluna-propria-e-lembrada-com-o-ciclo-desligado.md), marcados no corpo com **[Superado]**.
- **Data**: 2026-11-20 (verificação complementar em 2026-11-21)
- **Complementa**: ADR 0007 (gordura segue a fração configurada no ciclo, que o modo padrão preserva) e ADR 0009 (proteína do corte). Nenhuma decisão anterior é substituída.

## Contexto

O ciclo de carboidratos (alto, normal e baixo por dia da semana) já existe. O dono é `api/app/domain/nutrition/carb_cycling.py`, e `daily_target.compute_daily_target` o aplica como último passo (L478–L496), sobre a base do plano sem treino (`_cycle_base`, ou `metas_repouso` quando existe). Hoje a gordura segue a regra da ADR 0007 (`keep_fat_share=True` sem gramas manuais): o delta de carbo vira delta de kcal e a gordura acompanha a fração do plano. Com gramas manuais, a gordura fica em gramas e o carbo absorve o delta.

A issue #242 pede um modo opcional em que o carbo varia e a gordura compensa a energia, preservando kcal e proteína do alvo pré-ciclo de cada data. Ex. da issue: mantendo proteína e energia, −45 g de carbo permitem +20 g de gordura (−180 kcal = +20 g × 9 kcal).

Restrições reais:

- o plano guarda o ciclo em `nutrition_plans.carb_cycling_json` (mapa dia 0..6 → alto/normal/baixo, validado por `validate_cycle_days`). Não há coluna para a política; **[Superado: desde a migração 123 há a coluna `nutrition_plans.carb_cycle_policy`, ADR 0033.]**
- há planos em produção com ciclo (medido em 20/11/2026, abaixo), e o comportamento deles não pode mudar;
- os pisos e tetos das gramas já têm dono: `manual_targets.check_manual_targets` (L249), usado por `plan.check_cycle_floors` (L905) e pelas gramas manuais;
- as kcal por grama moram em `macros.py` (`KCAL_PER_G_*`, `kcal_from_macros`, tolerância `ATWATER_CLOSE_REL_TOL`). Nenhum `* 4` ou `/ 9` pode existir fora dele;
- regra permanente de motores (`CLAUDE.md`, mapa `docs/motores/FONTES_DA_VERDADE.md`): nenhuma conta de compensação fora do dono; frontend só exibe.

## Alternativas consideradas

- **Envelope versionado `{"version": 2, "days": {...}}` em `carb_cycling_json`.** Recusada: muda o formato de todo plano com ciclo e quebra os leitores que indexam os dias do mapa diretamente. O formato antigo teria de ser lido em dois lugares para sempre.
- **Coluna nova `carb_cycle_policy` com migração 122.** Recusada: aumenta os pontos de leitura e escrita (`nutrition_plans`, `nutrition_targets_service`, check-in), e a promoção passa a exigir `RUN_DB_MIGRATIONS=true`. O rollback fica mais limpo, mas o custo de operação no lote não compensa para um modo opcional. **[Superado pela ADR 0033: a coluna foi escolhida pelo usuário em 22/11/2026 e entrou pela migração 123.]**
- **Calcular a compensação no frontend, ou ler a gordura da tela.** Recusada pela regra de motores: a conta mora no dono e a tela só mostra o valor pronto.
- **Chave irmã `policy` dentro de `carb_cycling_json`, gravada só fora do padrão (escolhida).** Segue o precedente de `training_mode` dentro de `manual_targets_json`. Nenhuma migração, e o plano no padrão grava exatamente o mesmo JSON de antes. **[Superado em parte pela ADR 0033: a chave segue gravada como reserva da leitura, mas a política lida vem da coluna.]**

## Decisão

1. **Política do ciclo** `CarbCyclePolicy` em `carb_cycling.py`: `vary_kcal` (padrão, comportamento atual, bit a bit) e `fat_compensation` (compensação). Rótulos e dicas em `labels.py` (`CARB_CYCLE_POLICY_LABELS`, `CARB_CYCLE_POLICY_HINTS`), exportados pelo espelho gerado (`motor-constants.generated.json`).

2. **Conta da compensação** (`_day_with_fat_compensation`, chamada por `apply_cycle_over_base(policy=FAT_COMPENSATION)`):
   - energia de referência = Atwater do alvo pré-ciclo da data (`kcal_from_macros` do `target`);
   - carbo do dia = carbo do alvo pré-ciclo + o mesmo delta de hoje: `base.carb_g × (multiplicador do tipo − 1)`, com a base sem treino, como no modo atual;
   - proteína inalterada;
   - gordura = (energia − proteína × 4 − carbo × 4) ÷ 9;
   - sem arredondamento no motor; a kcal final é recalculada por Atwater.

   O ciclo continua sendo o último passo, depois de estratégia e fase, prioridade, gramas manuais e atividade. A atividade não é recontada: o alvo pré-ciclo já a contém.

3. **Inviabilidade.** Os códigos são `fat_compensation_infeasible` (carbo ou gordura negativos) e `fat_compensation_target_not_closed` (alvo que não fecha por Atwater, ou macro nulo). Nos dois casos o dia sai SEM ciclo, com a limitação; nunca há recorte silencioso.
   - `plan.check_cycle_floors`, no modo `fat_compensation`, passa os macros finais de cada tipo de dia por `manual_targets.check_manual_targets`. Bloqueio vira erro (impede salvar, 400 `invalid_plan`, e publicar, `can_publish: false` na prévia); faixa da estratégia vira aviso;
   - política `fat_compensation` sem ciclo: `param_not_applicable:carb_cycle_policy` (recusada na validação de `build_plan_settings`). **[Superado pela ADR 0033: a política é aceita e guardada sem ciclo.]**

4. **Persistência sem migração.** `carb_cycling_json` passa a ter a chave irmã `policy`, gravada só quando a política não é a padrão (`join_stored_cycle`). A leitura separa as duas partes (`split_stored_cycle`). Sem a chave, o valor é legado. **[Superado em parte pela ADR 0033: há a migração 123 e a política também fica em `nutrition_plans.carb_cycle_policy`; a chave continua como reserva.]** Desde o lote 33 (23/11/2026) a política vive só na coluna (ADR 0033), e a chave `policy` não é mais gravada nem lida.

5. **Compatibilidade.** A chave `carb_cycle_policy` só entra em `PlanSettings.to_dict` e `fingerprint_dict` (`plan.py`), e em `DailyTarget.to_dict` (`daily_target.py`), fora do padrão. **[Superado em parte pela ADR 0033: no `fingerprint_dict` a política só entra com ciclo.]** Planos legados mantêm resultado e hash. `ENGINE_VERSION` não muda nesta entrega. O teste de ouro `api/tests/unit/test_nutrition_modos_legados_ouro.py` não foi editado.

6. **Edição** (`nutrition_plans._validate_payload`, PUT de substituição total): campo omitido herda a política gravada somente quando o payload traz um ciclo que existe (`validate_cycle_days(...).cycles`, dono `carb_cycling.py`); `null` explícito volta ao padrão; valor explícito vale. Ciclo vazio ou só com dias normais não herda: salva com a política padrão. **[Superado pela ADR 0033: a herança vale com ou sem ciclo; `null` explícito volta ao padrão.]** Na criação, campo ausente = padrão. `update_plan` lê a linha antes de validar o payload, então plano inexistente com payload inválido devolve 404 antes de 400 (mudança lateral).

7. **Leituras.** O plano devolve `carb_cycle_policy` (`plan_public`, sempre presente). Cada dia da prévia (`day_public`) e cada meta por data (`target_row_public`) devolve `carb_cycle_policy` (a aplicada no dia) e `day_kind`. Dia antigo sai `vary_kcal`. A meta materializada lê a política de `inputs_json` pelo alias `carb_cycle_policy_json` (`_TARGET_JSON_COLUMNS`, em `nutrition_targets_service.py`). O check-in semanal preserva a política (`weekly_checkin._builder_args`).

8. **Frontend.** `frontend/src/lib/nutrition-cycle-policy.ts` só transporta e exibe: seletor no editor junto do ciclo (só com ciclo), resumo do plano, tabela da semana e diário, web e mobile. Nenhum cálculo e nenhum rótulo de política escritos à mão.

## Trade-offs

- Ganha: o modo novo sem migração, sem mudança de formato para quem não o usa e sem perda de dados.
- Ganha: um dono só para a conta, os códigos de limitação e o formato gravado.
- Paga: `carb_cycling_json` passa a ter dois formatos (mapa puro e mapa com `policy`). Todo leitor deve usar `split_stored_cycle` (hoje `stored_cycle_days`). **[Lote 33 (23/11/2026): a chave não é mais gravada nem lida; a política vive só na coluna (ADR 0033).]**
- Paga, **risco de rollback**: código anterior a esta entrega lê a chave `policy` como dia inválido. Um plano salvo com a política nova perde o ciclo, sem aviso, se voltar ao código antigo. Só afeta planos que optaram pelo modo novo. Com coluna nova, o rollback levaria o plano apenas ao modo legado, sem perda de ciclo. **[Superado em parte pela ADR 0033: a coluna existe desde a migração 123; o rollback do código sem reverter a migração perde só a política lembrada com o ciclo desligado.]** Desde o lote 33 a chave não é mais gravada nem lida (ADR 0033): planos salvos depois dele não a carregam, então esse risco vale só para os gravados antes.

## Consequências

- **Dados.** Nenhuma coluna nova, nenhuma migração. `RUN_DB_MIGRATIONS` continua `false`. **[Superado em 22/11/2026 pela ADR 0033: a migração 123 adiciona a coluna, e a promoção exige `RUN_DB_MIGRATIONS=true`.]**
- **Contrato.** O plano, a prévia e as metas por data ganham `carb_cycle_policy` (campo aditivo).
- **Erros.** Política inválida ou sem ciclo: 400 `invalid_plan` com `invalid_code:carb_cycle_policy` ou `param_not_applicable:carb_cycle_policy`. **[Superado em parte pela ADR 0033: `param_not_applicable:carb_cycle_policy` saiu; a política sem ciclo é aceita.]**
- **Mapa.** `docs/motores/FONTES_DA_VERDADE.md` (§3.7, linhas do ciclo, da meta diária e do plano) e o bloco "Lote 12 de 20/11/2026 (#242)", com o bloco "Lote 18 de 21/11/2026 (#248, #246)" para a constante e a herança. `docs/nutricao/CALCULOS_NUTRI.md` ganha a subseção da conta.
- **Impacto em produção (medido por leitura em 20/11/2026, só SELECT).** `nutrition_plans`: 6 planos de 1 usuário; 5 com ciclo de carboidratos (4 publicados e 1 rascunho); nenhum com ciclo e gramas manuais ao mesmo tempo. Nenhum plano muda: todos ficam no padrão `vary_kcal`, com o mesmo JSON gravado, o mesmo resultado e o mesmo hash.
- **Não medido.** Quantos planos adotarão o modo novo (opção nova, sem uso ainda).

## Limites e pendências

- (a) **Rollback** (ver Trade-offs): código anterior lê `policy` como dia inválido e o plano perde o ciclo sem aviso.
- (b) **Valor desconhecido já gravado** na chave `policy`: `_enum_or_error` devolve o padrão e registra erro, e `settings_from_row` ignora erros de validação na leitura. O plano é lido no padrão e mantém os dias. Não há aviso na tela. Nota: desde o lote 33 a chave `policy` não é mais lida; valor desconhecido na COLUNA é lido como a política padrão, com aviso em log uma vez por plano (ver ADR 0033).
- (c) **Dia real inviável** sai sem ciclo com a limitação. O bloqueio ao salvar avalia os tipos de dia num dia sem treino, então um dia com treino pode sair sem ciclo sem que o bloqueio o tenha visto.
- (d) A soma semanal de kcal é conservada, mas a soma semanal de gramas de carbo e de gordura não é conservada.
- (e) O SQL do alias `carb_cycle_policy_json` foi exercitado só com banco simulado antes do CI (`ci-completo`).
- (f) Sem teste de integração de ida e volta da política (gravar, reler e publicar).
- (g) Nenhuma tela foi vista em navegador ou celular. Sem caso novo na prévia do frontend.
- (h) Rótulos de campo (não de política) seguem escritos na tela.

## Verificação

- Suíte unitária inteira do backend, local: 17.970 passaram e 2 falharam. `test_sem_mojibake` falha por arquivos locais não versionados, que não existem no CI. `test_nutrition_report_window` falhou por banco simulado sem a coluna do alias novo; foi corrigido em seguida, e a bateria dirigida depois da correção passou em 155 testes.
- Testes novos (entrega de 20/11): 24 do motor (`api/tests/unit/test_nutrition_ciclo_compensacao_gordura.py`) e 11 de persistência e rotas (`api/tests/unit/test_nutrition_ciclo_politica_persistencia.py`).
- Em 21/11/2026, testes dos cenários de Intervals, adaptativo, fases e refeed, semana parcial com peso variável, futuro desconhecido, dia isolado e meta congelada, em `test_nutrition_ciclo_compensacao_gordura.py` (`test_gasto_automatico_do_intervals_com_compensacao_igual_ao_pre_ciclo`, `test_modo_adaptativo_com_compensacao_igual_ao_pre_ciclo`, `test_fases_e_refeed_com_compensacao_igual_ao_pre_ciclo_da_mesma_fase`, `test_semana_parcial_peso_variavel_futuro_desconhecido_e_dia_isolado_com_compensacao`): todos passaram sem alteração no motor. Os testes conferem kcal e proteína do dia iguais às do alvo pré-ciclo, atividade sem dupla contagem, soma semanal de kcal e meta congelada não regravada.
- Em 21/11/2026, bateria dirigida do backend: 894 passaram.
- Baterias dirigidas do backend (entrega de 20/11): 1.044 e 971 passaram.
- Frontend: Vitest da nutrição, 242 passaram; `tsc` sem erro.
- Guarda dos motores (`api/tests/unit/test_guarda_fontes_da_verdade.py`): 485 passaram.
- Não verificado: integração contra MySQL real (roda só no CI, com o rótulo `ci-completo`), celular real e navegador.
