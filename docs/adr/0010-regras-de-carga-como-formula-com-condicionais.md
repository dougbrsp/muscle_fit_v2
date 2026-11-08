# ADR 0010: Regras de carga da próxima série como fórmula com condições E/OU (next-set-load-rules-v2)

- **Status**: Aceita (decisão do usuário, 08/11/2026: construtor visual de fórmula em `/regras-carga`, condições combinadas em E/OU com parênteses; G1 e G2 abaixo).
- **Data**: 2026-11-08

## Contexto

As regras cadastradas de carga da próxima série (§59, R1–R4 do mapa `docs/motores/FONTES_DA_VERDADE.md`) tinham forma fixa: `Rule(position, set_position, fatigue, reps, rpe, respect_planned_ramp, action, magnitude)`, com uma condição de reps e uma de RPE por regra, combinadas sempre em E. Não havia OU, parênteses, comparação com a série anterior nem com o alvo do motor. A tela de cadastro (R4) só montava Selects desses campos fixos.

A regra publicada em produção (conjunto `published` versão 2, 11 regras) cabe nessa forma, mas não expressa, por exemplo, "reps acima do teto OU (reps no teto E RPE abaixo do máximo)" nem "carga do motor ± 1 passo".

Estado lido em produção em 08/11/2026 (só SELECT em `next_set_load_rule_sets`): id 1 (versão 1, 2 regras) substituída em 2026-10-21 21:44 UTC; id 2 (versão 2, publicada em 2026-10-21 21:44:37 UTC, 11 regras, piso 6, teto 12) vigente; ambas com `engine_version` = `next-set-load-rules-v1`; nenhum rascunho.

## Decisão

- **Forma de regra v2** (dono `api/app/domain/next_set_load.py`): `Rule(position, when, action, magnitude, reason_code=None)`, com `when` = `ConditionGroup(logic: and|or, children)` ou `ConditionLeaf(variable, scope, op, value, value_to?, ramp_cap?)`. Variáveis: `set_index` (só escopo `current`), `fatigue_band` (`=`/`≠`, só `current`), `reps` e `rpe` (escopos `current` e `previous`). Tokens: `floor`/`ceiling` para reps; `max` (teto efetivo da série) e `target` (RPE alvo efetivo da série) para RPE.
- **`ramp_cap`** só em folha de RPE com `<`, `≤` ou `entre`, no escopo `current`: o limiar superior vira `min(limiar, RPE planejado)`.
- **`ENGINE_TARGET`**: nova ação; carga = `engine_target_kg` (prescrita pelo motor para o par na sessão), magnitude opcional COM SINAL (± n passos ou ± p %; a validação de forma usa o módulo), arredondada ao passo e limitada por `cap_load_to_ceiling`. Sem alvo do motor a regra não casa e a próxima é avaliada (`_rule_outcome`, antes das folhas).
- **G1 = "executada"** (decisão do usuário): `set_index`, `reps` e `rpe` sem qualificador referem-se à série recém-registrada; `previous` é a série de trabalho anterior a ela; o resultado (HOLD/INCREASE/DECREASE/ENGINE_TARGET) é sempre a carga da SEGUINTE.
- **G2 = "alvo"** (decisão do usuário): o token `target` é o RPE alvo efetivo da série; não existe piso global de RPE. Sem alvo, a folha não casa.
- **Decisões do Tech Lead**: profundidade máxima 3 níveis (raiz = 1) e 8 folhas por regra (`RULESET_MAX_DEPTH`, `RULESET_MAX_LEAVES_PER_RULE`); "carga" só como resultado, nunca como condição; a dor articular/muscular continua vencendo antes de qualquer regra; o prompt `set-eval-v8` não recebe bump (só o texto interpolado muda, §59.3).
- **Conversão e versão**: `ruleset_from_dict` lê v1 (campos fixos → `and` de folhas; `respect_planned_ramp` → `ramp_cap` SÓ quando o operador da folha de RPE admite cap, `<`, `≤` ou `entre`; com `>`, `≥`, `=` ou `≠` a rampa é ignorada) e v2 (`when`); `ruleset_to_dict` escreve v2; `RULESET_ENGINE_VERSION` = `next-set-load-rules-v2`. O loader `api/app/next_set_load_rules.py` aceita `when` e não aceita `reason_code`. Sem migração: `rules_json` é JSON.
- **Fatos novos** (só montados pelos chamadores; nenhuma regra nos consumidores): `previous` (série de trabalho CONFIRMADA anterior, `sql_set_confirmed('reps_pending')` do dono `set_types`; lida só quando `ruleset_uses_previous_set`), `engine_target_kg` e `rpe_target` (`engine_decision_context.next_set_load_for`, `workout_sessions._evaluate_set`, `workout_ai_generation_core.dispatch_evaluate_set`).
- **Conjunto por objetivo** (`ruleset_for_goal`): hipertrofia usa o publicado (ou o padrão); força usa `STRENGTH_RULESET`, do dono, fora do cadastro.
- **Espelho do FE**: `export_for_frontend` ganha `rule_vocabulary`, `rule_limits` e `default_ruleset` (árvore). O construtor (`frontend/src/app/(app)/regras-carga/`: `RuleCard`, `ConditionGroup`, `ConditionLeaf`, `FormulaPreview`) só monta Selects e mostra o `text` devolvido pelo servidor. A tela NÃO converte v1 (`fromV1`/`V1_SET_POSITION_LEAF` removidos): aceita só payload v2 (`when`) e lança erro explícito se o servidor devolver regra sem `when`; o servidor sempre devolve v2 (`ruleset_to_dict`).

## Consequências

- Uma regra pode combinar posição, fadiga, reps, RPE, série anterior e alvo do motor, em E/OU aninhados, sem que a tela ou o prompt avaliem condição.
- A validação semântica fica no dono (`validate_ruleset`, códigos `RULE_*`); a tela faz só validação de forma com `rule_limits`.
- Conjuntos v1 já gravados continuam válidos e são lidos em memória; a próxima publicação grava v2.
- Custo por série salva: a leitura da série anterior confirmada só ocorre quando alguma regra a exige; o padrão e o conjunto publicado atual não a exigem.
- `describe_rule` mudou de formato ("SE … ENTÃO …"); o texto que a IA recebe (§59.3) é gerado pelo mesmo dono.

## Impacto em produção

- **MUDA**: (1) o texto da regra interpolado no prompt `set-eval-v8` (§59.3) passa ao formato "SE … ENTÃO …", sem bump da versão do prompt; (2) a tela `/regras-carga` é nova (construtor de fórmula), com menu só admin.
- **NÃO MUDA**: a carga sugerida. Replay: 0 diferenças nos dois conjuntos (detalhes abaixo).
- **Prova**: replay no DEV carregado com os dados de produção até 28/10 e SELECT em produção (`next_set_load_rule_sets`). Smoke no artefato real fica para a promoção.

Medido no DEV com os dados do usuário 1. Instrumento: `api/scripts/replay_next_set_load.py` (só leitura; `--base-ruleset-json` aplica o mesmo conjunto aos dois lados). Referência = worktree de origin/develop 1df38c7f (`C:\muscle_fit\.claude\worktrees\base-develop\api`); atual = este checkout (`C:\muscle_fit\api`). Em ambos: 1.866 séries `working` com carga, 85 atividades. **Diferenças: 0 nos dois replays.**

Replay com o conjunto publicado v2 (`C:\muscle_fit\.claude\local-state\ship-input\conjunto-producao-v2.json`, lido de produção):

| direção | referência | atual | delta |
|---|---:|---:|---:|
| DECREASE_LOAD | 27 | 27 | +0 |
| FORA_DA_REGRA | 8 | 8 | +0 |
| HOLD | 1.246 | 1.246 | +0 |
| INCREASE_LOAD | 585 | 585 | +0 |

| motivo | referência | atual | delta |
|---|---:|---:|---:|
| DECREASE_LOAD NEXT_SET_PAIN | 2 | 2 | +0 |
| DECREASE_LOAD NEXT_SET_RULE_MATCHED | 25 | 25 | +0 |
| FORA_DA_REGRA - | 8 | 8 | +0 |
| HOLD NEXT_SET_WITHIN_RULE | 1.246 | 1.246 | +0 |
| INCREASE_LOAD NEXT_SET_RULE_MATCHED | 585 | 585 | +0 |

Replay com `DEFAULT_RULESET` (padrão em código, versão 0):

| direção | referência | atual | delta |
|---|---:|---:|---:|
| DECREASE_LOAD | 9 | 9 | +0 |
| FORA_DA_REGRA | 8 | 8 | +0 |
| HOLD | 1.214 | 1.214 | +0 |
| INCREASE_LOAD | 635 | 635 | +0 |

| motivo | referência | atual | delta |
|---|---:|---:|---:|
| DECREASE_LOAD NEXT_SET_FAILURE_BEFORE_MIN_REPS | 7 | 7 | +0 |
| DECREASE_LOAD NEXT_SET_PAIN | 2 | 2 | +0 |
| FORA_DA_REGRA - | 8 | 8 | +0 |
| HOLD NEXT_SET_WITHIN_PLANNED_RAMP | 85 | 85 | +0 |
| HOLD NEXT_SET_WITHIN_RULE | 1.129 | 1.129 | +0 |
| INCREASE_LOAD NEXT_SET_LOW_RPE_AT_REPS | 422 | 422 | +0 |
| INCREASE_LOAD NEXT_SET_REPS_ABOVE_LIMIT | 213 | 213 | +0 |

Leitura de produção (08/11/2026, só SELECT): a versão 2 publicada continua vigente e, após o deploy, a leitura a devolve convertida em memória, sem regravar; a primeira gravação posterior (novo rascunho e publicação) passa a usar `next-set-load-rules-v2`. Nenhum comportamento muda até um admin publicar um conjunto novo: publicar é global e afeta a próxima série salva de todos os usuários (§59.2).

## Rollback

- O leitor ANTIGO (`ruleset_from_dict` da imagem em produção hoje) não conhece `when`. Se um conjunto v2 for publicado e a Lambda da API voltar à imagem anterior, a regra v2 seria lida SEM condição (`set_position` e `fatigue` = any, `reps` e `rpe` None) com a `action` mantida: a regra "sempre casa" (ex.: INCREASE_LOAD em toda série), caso a validação antiga aceite. Risco nulo enquanto a linha publicada seguir v1 (hoje: id 2, v2, com 11 regras v1).
- Procedimento antes de voltar a imagem da API: republicar um conjunto no formato v1 OU despublicar o conjunto v2 (sem linha publicada, `resolve_published` cai em `DEFAULT_RULESET`, `next_set_load_rules.py`).
- Limite atual: não há rota de despublicação nem gravação de v1 na API (grep em `api/app`: nenhuma), e a tela só grava v2. Portanto a reversão exige escrita manual em `next_set_load_rule_sets` em produção, que precisa de autorização específica do usuário. Sem isso, não reverter a imagem.
- Mitigação futura (fora deste lote): o leitor recusar regra com chave desconhecida (ex.: `when` num leitor antigo).

## Limitações

- O replay mede o conjunto publicado e o padrão do usuário 1 (hipertrofia). O conjunto de força (`STRENGTH_RULESET`) não tem replay próprio neste PR.
- Os padrões da guarda de `regra_carga_cadastrada` citam a forma v1 (`rule.reps.op`, `reps_condition`); uma condição avaliada fora do dono na forma v2 (`leaf.op`) não seria detectada. Allowlist segue vazia.
- Uma regra v1 com `respect_planned_ramp` e sem condição de RPE vira folha sem valor e é recusada pelo validador (`RULE_RAMP_WITHOUT_RPE`); o conjunto publicado atual não tem esse caso.
- Limite de 20 regras (`RULESET_MAX_RULES`) e 8 folhas por regra; a árvore é limitada a 3 níveis.
- Alvo do motor ausente (sem `engine_target_kg`) faz a regra `ENGINE_TARGET` não casar; a carga cai para a próxima regra ou para manter.
- A caixa "Limitar pelo RPE planejado" aparece só para a folha de RPE no escopo `current` (`supportsRampCap`, `ConditionLeaf.tsx`); o operador é validado só no servidor (422 `RULE_RAMP_CAP_INVALID`). Melhoria futura, não feita: o dono exportar `ramp_cap_ops` em `rule_vocabulary` para a tela filtrar por operador sem regra local.
