# ADR 0022: GVT com as reps do protocolo, técnica só de séries mantida sem trabalho direto, trava de grupo na criação da ficha e vínculo da geração (issues #224 e #178)

- **Status**: Aceita. Branch `fix/gvt-reps-e-grupo-na-criacao-224-178`. Não publicada nem promovida a produção.
- **Data**: 2026-11-19
- **Complementa**: ADR 0013 (RPE esperado por série) e ADR 0015 (progressão com fonte única no motor). Nenhuma decisão anterior é substituída.

## Contexto

Quatro defeitos, dois por issue e dois que apareceram na medição:

1. **#224, reps do GVT.** O GVT (10 séries de 10 repetições) saía com a faixa digitada no item do Modelo (ex.: '6-12'). A tela e o congelamento mostravam uma faixa para uma técnica que tem protocolo fixo.
2. **#224, grupo sem trabalho direto.** Quando a recuperação marca o grupo como `NO_DIRECT_WORK`, o motor devolve `SKIP` e a linha sai sem técnica nem séries. Medido em produção em 19/11/2026: a linha "Supino inclinado com halteres" da ficha de 19/11/2026 (linha 1481) tem `sets_count` 10, `reps_target` '6-12', `rpe_target` 8, `advanced_technique` nulo e decisão `SKIP`; a mesma linha na ficha de 12/11/2026 (linha 1394) era `gvt`, '8-12', RPE 10, decisão `HOLD`. Em linhas de fichas desde 01/11/2026 há 7 `SKIP` com técnica nula e 1 com técnica.
3. **#178, trava de grupo na criação.** A trava do grupamento muscular (`muscle_group_locked`, 422) só valia na edição (`services._replace_plan_exercises`, linha com linha salva). Uma linha gerada que a tela trocava por exercício de outro grupo PRIMARY entrava na criação sem a trava.
4. **Vínculo da geração (achado da medição).** `generation_runs` tem 70 execuções (de 28/09/2026 a 19/11/2026), todas com `workout_plan_id` nulo. Das 85 fichas, nenhuma tem `generation_audit` no nível de cima do `raw_payload`; 52 têm a auditoria só em `generation_context.generation_audit`. O servidor lia só o nível de cima (`extract_audit` antes desta entrega), então a ficha nunca se ligava ao rascunho. O painel do motor (`intelligence/dashboard/engine_service.py`, SQL em L218-229) também lê só o nível de cima.

Restrições: `generation_runs` não pode mudar a forma do `generation_audit` (M16, `api/app/generation/generation_runs.py` L41-48, o congelamento do Modo B lê `raw_payload.generation_audit.substitutions[]`); a mudança é sem migração; o CI roda a integração, não o checkout local.

## Alternativas consideradas

- **Reps do GVT na tela ou no frontend.** Descartada: é regra de prescrição, tem de morar no motor dono (`docs/motores/FONTES_DA_VERDADE.md`, regra "Regras só nos motores"). A tela só lê `target_reps`.
- **FST-7 com reps fixas.** Descartada: o protocolo do FST-7 é o topo da faixa do item; não há número de protocolo que o justifique. Só o GVT entra em `PROTOCOL_REPS_BY_CODE`.
- **Para `NO_DIRECT_WORK` com técnica só de séries: tirar a técnica e as séries dela.** Recusada pelo usuário em 19/11/2026. Apaga o protocolo que o usuário escolheu, sem trocar o grupo.
- **Para `NO_DIRECT_WORK` com técnica só de séries: manter sem técnica, corrigindo só as reps.** Recusada pelo usuário em 19/11/2026. Mantém o número de séries de um protocolo que não é executado como protocolo.
- **Trava de grupo na criação como aviso.** Descartada pelo usuário: a rejeição é 422, no mesmo formato da edição.
- **Carimbar `generation_run_id` dentro de `generation_audit`.** Descartada: mudaria a forma do bloco, o que o M16 proíbe no mesmo PR. A identidade continua sendo o `audit_hash`.
- **Ler a auditoria só do nível de cima e mudar o frontend.** Descartada: as 52 fichas já salvas têm a auditoria em `generation_context`; o servidor passa a aceitar os dois lugares.

## Decisão

1. **Dono das reps do protocolo.** `api/app/domain/technique_accounting.py`: `GVT_PROTOCOL_REPS = 10`, `PROTOCOL_REPS_BY_CODE = {"gvt": GVT_PROTOCOL_REPS}` e `protocol_reps(code)` (devolve None para as demais técnicas).
2. **Motor exporta e aplica.** `api/app/domain/progression_engine.py::technique_parameters` grava `target_reps = protocol_reps(code)` nos parâmetros de técnica só de séries. No caminho normal de `prescribe` (L3088-3091), com `target_reps`, a faixa mín/máx do grupo vira o valor do protocolo.
3. **Consumidores só leem.** `api/app/domain/prescription.py::build_prescription` (L271, usado em `reps_target` e `suggested_reps` do snapshot, L291 e L321); `api/app/generation/deterministic_prescription.py::PairPrescription.reps_target` (L112-116); `api/app/generation/mode_b.py::item_measure_and_reps` (L181-185, sem decisão, o protocolo do item); `api/app/services.py::_replace_plan_exercises` via `_protocol_reps_text` (L3212-3214, usado em L3470), na criação e na edição, inclusive ficha manual. No frontend, `frontend/src/lib/workout-mode-intensive.ts` (`AdvancedTechniqueParameters.target_reps`) só recebe o valor pronto.
4. **Técnica só de séries mantida no `NO_DIRECT_WORK`.** Resposta do usuário de 19/11/2026: "manter o GVT 10×10". Ressalva: o FST-7 não é mantido, porque exige falha e a falha está negada nesses dias (a alternativa seria a ficha exibi-lo como técnica inválida pedindo escolha). `api/app/domain/progression_engine.py::prescribe`, ramo `readiness.status == NO_DIRECT_WORK` (L2849-2902):
   - a técnica é resolvida por `resolve_technique_authority(..., composed_failure_allowed=composition.failure_allowed)` (L2855-2859), que mantém a hierarquia Modelo > progressão e remove a técnica que a segurança invalida com a falha composta do dia;
   - se `is_sets_only_technique(kept)` (GVT ou FST-7), o snapshot leva a técnica, as séries (`track.sets_count`, limitada por `ladder.max_sets_count` quando não há série fixa do Modelo), os parâmetros (`technique_parameters`, com as reps do protocolo) e `suggested_reps_min = suggested_reps_max = target_reps` quando houver;
   - o RPE do protocolo (`target_rpe`) fica limitado a `composition.ceiling` quando há teto;
   - a decisão continua `SKIP`, com `blocked_by=(NO_DIRECT_WORK,)`.
   - Validação: `training_safety.validate_technique(..., failure_allowed=composition.failure_allowed)` (L521) vale a falha só se o par e a composta a permitirem. Só `requires_failure` e `beyond_failure` dependem dela (`FAILURE_REQUIREMENTS_NEEDING_FAILURE`, L63). Com recuperação que nega a falha, o GVT segue mantido e o FST-7 cai, sem técnica e com as séries e a faixa do Modelo na linha (comportamento anterior à entrega). Assim a técnica mantida nunca chega à ficha marcada como inválida (`domain/prescription.build_prescription` valida com a mesma falha composta).
   - Outras técnicas: nenhum campo de técnica no `SKIP`, como antes.
   - Pelo mesmo motivo, o RPE mantido não pode passar do teto do dia: o `target_rpe` do protocolo é limitado por `composition.ceiling`.
   - A proibição por objetivo de força (`training_safety.STRENGTH_DISALLOWED_TECHNIQUES`, L104) não foi alterada.
5. **Trava de grupo na criação (#178).** `api/app/models.py::WorkoutPlanExerciseIn.generated_mapping_id` (L252, opcional, não persistido). `services._replace_plan_exercises` (L3431-3434): linha sem linha salva e com o campo passa por `_assert_exercise_keeps_primary_group` (L3217), o mesmo predicado da edição (`api/app/workout_plan_edit_rules.py::assert_same_primary_group`, L113-124, 422 `muscle_group_locked`). Linha sem o campo (manual) e cliente antigo ficam livres. `api/app/workout_plans.py::create_plan` (L63-67) responde o 422 no mesmo formato da edição (`detail.reason` e `detail.message`).
6. **Vínculo da geração.** `api/app/generation/generation_runs.py::extract_audit` (L150-163; a forma SQL é `audit_sql_expr`, L166-175) lê `raw_payload.generation_audit` e, na falta, `raw_payload.generation_context.generation_audit`. `link_run_to_plan` (L225), chamado por `services.create_workout_plan` (L3832-3834), usa a mesma leitura. `frontend/src/lib/workout-plan-payload.ts::buildDraftRawPayload` (L93) envia a auditoria também no nível de cima, sem alterar o conteúdo (o hash continua o mesmo).
7. **Frontend sem regra.** `frontend/src/lib/exercise-swap-options.ts::stampGeneratedRows` (L61-63) carimba `generated_mapping_id` na linha gerada; `frontend/src/lib/workout-plan-payload.ts` (L54) envia o campo quando existe; `frontend/src/lib/plan-save-error.ts::planSaveErrorMessage` (L6-10) mostra a mensagem do servidor na tela mobile de geração (`GerarPlanoView.tsx`, L219).
8. **Reps do protocolo só em linha medida por repetições.** `api/app/services.py::_replace_plan_exercises` (L3470) e `api/app/domain/prescription.py::build_prescription` (L272) aplicam `target_reps` só quando `set_measures.is_rep_based` vale, o mesmo predicado de `generation/mode_b.item_measure_and_reps`. Linha GVT medida por tempo mantém o alvo enviado.

## Trade-offs

- Ganha: o GVT grava 10 reps em todas as rotas que criam linha (geração, congelamento, criação e edição); o número de protocolo mora num único lugar.
- Ganha: a linha de `NO_DIRECT_WORK` mantém o protocolo escolhido pelo usuário, sem mudar a decisão de segurança (`SKIP`).
- Ganha: a ficha passa a se ligar à geração que a originou, sem mudar a forma de `generation_audit`.
- Paga: a linha com técnica e decisão `SKIP` é um estado novo que quem lê o congelamento precisa conhecer; a decisão e a técnica deixam de dizer a mesma coisa.
- Paga: a auditoria fica gravada duas vezes (nível de cima e em `generation_context`), como antes; o caminho de leitura agora aceita os dois.
- Paga: `generated_mapping_id` é fornecido pelo cliente; a trava protege contra cliente com defeito, não é fronteira de segurança (qualquer usuário cria ficha manual com qualquer exercício).

## Consequências

- **Dados.** Sem migração. Nenhum dado gravado muda no deploy. Fichas já congeladas mantêm a faixa antiga até serem geradas de novo. A partir do deploy, linha GVT gerada, congelada ou gravada sai com 10, e o GVT passa a ser mantido no `SKIP`; o FST-7 segue sem técnica no `SKIP`. Fichas salvas a partir do deploy passam a ter o vínculo da geração gravado. As 70 execuções antigas não são religadas.
- **Medição de 20/11/2026 (leitura, só SELECT).** As 11 linhas de ficha com `gvt` ou `fst7` têm medida `reps` e estão todas em fichas `done`; nenhuma está em ficha `planned`. Fichas por status: 80 `done` e 5 `planned`. Itens de Modelo (`workout_plan_template_slot_items`): 28 com `gvt` e 34 com `fst7`. Portanto nenhuma linha de ficha editável é regravada com 10 no deploy.
- **Operação.** Criação de ficha com linha gerada trocada por outro grupo recebe 422 com a mensagem do servidor. A ligação rascunho → ficha passa a funcionar para as fichas novas.
- **Próximas decisões.** Reps de protocolo de outra técnica entram em `PROTOCOL_REPS_BY_CODE`, nunca em tela, SQL, geração ou prompt. Mudar a decisão de `NO_DIRECT_WORK` para outras técnicas é decisão nova do usuário.
- **Mapa.** `docs/motores/FONTES_DA_VERDADE.md` registra as duas linhas novas da §64, a ressalva na §61 (criação e linha nova do PUT, `generated_mapping_id`), a §61.1 (vínculo da geração) e o bloco "Lote 5 de 19/11/2026 (#224 e #178)" em "Impacto em produção".

## Limites conhecidos e pendências

- (a) A tela web de geração (`frontend/src/app/(app)/planos-treino/GenerateDraftModal.tsx`) ainda mostra a mensagem genérica do 422; só a tela mobile usa `planSaveErrorMessage`.
- (b) Resolvido em 21/11/2026 (ver Atualização #230): o painel (`api/app/intelligence/dashboard/engine_service.py`) usa a expressão do dono (`audit_sql_expr`) e enxerga as fichas com a auditoria em `generation_context`.
- (c) As 70 execuções antigas de `generation_runs` não são religadas a fichas.
- (d) `frontend/src/lib/advanced-techniques.ts` mantém um fallback escrito à mão para GVT e FST-7 (`fallbackItem('gvt', …)`, L108-111), alcançado só se o catálogo não trouxer o valor. Desde 21/11/2026 os números de séries deste fallback vêm de `SETS_ONLY_DEFAULT_SETS` (ver Atualização).
- (e) O `rpe_target` da linha continua o do item do Modelo; o RPE do protocolo vai nos parâmetros da técnica (`target_rpe`).
- (f) Fichas já congeladas não mudam.
- (g) `generated_mapping_id` vem do cliente.
- (h) Comentários de `frontend/src/lib/exercise-swap-options.ts` (L55-57 e L147) dizem que o campo não entra no payload de save; `workout-plan-payload.ts` (L54) passa a enviá-lo. O comentário está desatualizado.
- (i) Para um item FST-7 do Modelo em dia sem trabalho direto, a decisão sai sem a técnica e com as séries e a faixa do Modelo (comportamento anterior à entrega); o snapshot da ficha, porém, mantém a técnica (ver Atualização).

## Verificação

- Revisão independente em duas rodadas. Na primeira, o revisor de código achou que o FST-7 mantido pelo motor em dia sem trabalho direto chegaria à ficha marcado como técnica inválida; corrigido (item 4). Na revisão final (`967d7ce1`), o `code-reviewer` e o `guardiao-motores` aprovaram, sem achado bloqueante.
- Suíte unitária inteira do backend, local, depois da última correção: 17.821 passaram; 1 falhou (`test_sem_mojibake`, por arquivos locais não versionados que não existem no CI).
- Verificação dirigida: guarda dos motores, testes de backend e de frontend do lote, `ruff` e `tsc`.
- Lido em produção em 20/11/2026: no catálogo `advanced_techniques`, `fst7` é `requires_failure` e `gvt` é `failure_not_required`.
- Não verificado: nenhuma tela foi vista em navegador nem em iPhone; o 422 da criação e o vínculo da geração não foram exercitados contra banco real (a integração roda no CI); o hash da auditoria não foi comparado com um `audit_hash` real; a coerência entre o motor e a ficha congelada para o GVT mantido foi provada pela mesma validação (`validate_technique`), com teste que passa por `build_prescription` desde 21/11/2026 (ver Atualização).

## Atualização de 21/11/2026 (#231)

**Número padrão de séries no dono.** `api/app/domain/technique_accounting.py::SETS_ONLY_DEFAULT_SETS` (L73, `{"fst7": 7, "gvt": 10}`) é a fonte das séries das técnicas só de séries (FST-7 e GVT). Teste de paridade com a semeadura da migração 060 em `api/tests/unit/test_technique_accounting.py` (L121-128). A migração 060 não foi editada: migração aplicada não se edita, e o executor compara o checksum do arquivo.

**Espelho.** `export_for_frontend` (L546-547) exporta `sets_only_default_sets` e `protocol_reps_by_code` junto de `PROTOCOL_REPS_BY_CODE`, no `frontend/src/lib/motor-constants.generated.json`. `frontend/src/lib/technique-accounting.ts` (L39-40) os lê de `RULE`. `frontend/src/lib/advanced-techniques.ts` deixou de ter 7 e 10 escritos à mão: lê `SETS_ONLY_DEFAULT_SETS` nos valores e no texto de ajuda (L257-258). O fallback do item (d) continua na estrutura, com as séries vindas do espelho.

**Faixa com mínimo igual ao máximo.** `format_reps_range` (`technique_accounting.py` L76-78) devolve "10" em vez de "10-10". É usada nos dois pontos que montavam a faixa: `api/app/domain/prescription_service.py` (L146) e `api/app/generation/deterministic_prescription.py` (L120).

**Teste pelo caminho real.** `api/tests/unit/test_tecnica_de_series_sem_trabalho_direto.py::test_snapshot_do_gvt_mantido_sem_trabalho_direto_nao_marca_tecnica_invalida` passa por `build_prescription` (`_snapshot_do_motor`). A ficha com GVT mantido em dia sem trabalho direto não recebe `WARNING_MODEL_TECHNIQUE_INVALID`.

**FST-7 em dia sem trabalho direto (limite conhecido, fixado em teste).** `test_snapshot_do_fst7_sem_trabalho_direto_registra_comportamento_atual` fixa o comportamento atual. A decisão do motor retira o FST-7 (`advanced_technique` nulo, `SKIP`), mas o snapshot da ficha sai sem aviso (`warnings == []`) e mantém `advanced_technique == "fst7"`, copiado do exercício do Modelo. Ficha e decisão divergem nesse caso. O usuário foi informado em 20/11/2026 e não pediu mudança. Esta divergência é o limite (j).

**Guarda dos motores.** Padrão `reps_protocolo_tecnica` em `api/tests/unit/test_guarda_fontes_da_verdade.py` (L2079-2093). Ele acusa a definição de `GVT_PROTOCOL_REPS`, `PROTOCOL_REPS_BY_CODE` ou `SETS_ONLY_DEFAULT_SETS` fora do dono, e número atrelado ao código `gvt` ou `fst7` escrito à mão. Atribuição cujo valor vem de `RULE` (espelho gerado) é reconhecida como espelho. A allowlist nasce vazia (L2611).

**Verificação (resultados reportados na entrega; não reexecutados por este documento).**
- Bateria dirigida do backend com a guarda: 643 passaram.
- Suíte unitária inteira do backend, local: 18.015 passaram e 1 falhou (`test_sem_mojibake`, por arquivos locais não versionados que não existem no CI).
- Guarda dos motores depois dos padrões novos: 491 passaram.
- Vitest do lote: 254 passaram; `tsc` sem erro.

**Limite (j).** Ficha e decisão do motor divergem para FST-7 em dia sem trabalho direto (ver acima). Mudar isso é decisão do usuário.

## Atualização de 21/11/2026 (#230)

**Painel do motor pelo dono.** O painel (`engine_service.py`, lista e cabeçalho do detalhe) lê a auditoria pela expressão `audit_sql_expr` de `api/app/generation/generation_runs.py`, a mesma regra de `extract_audit`. Com isso ele enxerga as fichas com a auditoria só em `generation_context`, e o limite (b) está resolvido. Nenhum consumidor escreve o caminho JSON à mão.

**Medição (leitura, banco de desenvolvimento, cópia de produção).** 45 fichas com a auditoria só em `generation_context`; a expressão nova encontra as 45. 58 execuções sem ficha ligada (o texto acima diz 70, número de outra medição); 53 casam com alguma ficha pelo `audit_hash`, mas só 20 fichas casam com exatamente uma execução.

**Escolha: não religar as execuções antigas.** Nenhuma migração liga as execuções antigas às fichas. A religação cobriria menos da metade das fichas, com risco de ligar o par errado, e escreveria em dados de produção. As execuções seguem como rascunho não salvo. O usuário foi informado em 21/11/2026 e pode pedir a religação dos 20 pares inequívocos num lote próprio, com decisão nova.

Efeito para o usuário: as fichas antigas passam a aparecer no painel com a limitação de leitura de `raw_payload`; antes não apareciam na lista. Detalhe em `docs/motores/FONTES_DA_VERDADE.md` (Lote 21 de 21/11/2026).
