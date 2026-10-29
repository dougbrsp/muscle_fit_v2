# Dados pessoais enviados à OpenAI: minimização e retenção (IA-17)

Data: 2026-10-10. Origem: auditoria de 2026-10-08 (arquivo privado), IA-17.
Todas as chamadas de texto passam pelo cliente único `api/app/openai_client.py` (endpoint Responses). As imagens (cards ilustrados) passam por `generate_image`.

## Regra

1. **Nenhum identificador direto entra no prompt.** Nome, e-mail, username, data de nascimento e sexo não são lidos por nenhum montador de prompt. O `user_id` vai só para o evento de log `llm.usage` e nunca para o corpo da requisição.
2. **Só entra o que a tarefa usa.** Um dado que nenhuma instrução do prompt usa sai do prompt. Um dado que pode mudar a resposta útil, como sono, HRV ou composição corporal no cardio, fica, e o motivo é registrado na tabela abaixo. Removê-lo exige o eval pago da feature (`docs/ia/EVALS.md`).
3. **`store: false` em toda chamada de texto** (`openai_client.LLM_STORE_RESPONSES`). Nenhuma feature reabre uma resposta pela API (não usamos `previous_response_id`, `GET /responses/{id}` nem `background`). O resultado útil é gravado no nosso banco.
4. **Nada de prompt nem de evento inteiro em log.** O `llm.usage`, o `llm.error` e o `llm.output_incomplete` levam só metadados. Os handlers das Lambdas assíncronas logam só as chaves do evento quando falta `job_id`.

## Inventário por feature

| Feature | Dado pessoal ou de saúde que entra | Por quê | O que saiu (IA-17) |
|---|---|---|---|
| `set_evaluation` (série ao vivo) | Reps, carga, RPE, dor muscular e articular, nota da série (sanitizada, delimitada) | O prompt manda considerar dor e nota para ajustar a próxima série | Nada |
| `exercise_summary` | Séries, dor, notas do exercício | Dor e fadiga são parte do resumo | Nada |
| `workout_summary` (fim de treino) | Nome do treino, data, duração, séries, RPE, dor, notas | Interpretação do treino e sinais de dor ou fadiga | **Nome da academia** (`workout-summary-v3`) |
| `insights` | Peso, % de gordura, massa muscular, cintura, eventos de contexto (doença, lesão, viagem) com nota cortada, pergunta livre | O pacote de métricas é a base da resposta; o contexto explica as associações | Nada |
| `workouts_analysis` (tela /treinos) | Sessões, recordes, `intervals_correlation` (HRV, FC de repouso, sono, FC média e máxima, calorias) | A seção "Pontos de Atenção" pode usar recuperação; removê-la pode mudar a resposta | **`intervals_external_id`**, o id da atividade na conta Intervals.icu do usuário (`workouts-analysis-v4`) |
| `cardio_agent` | Composição corporal de 30 dias, FC de repouso, HRV, sono, readiness, CTL/ATL/TSB, PAI, notas de sono e esforço, treinos de 14 dias, dor por grupo | O prompt declara decidir com base nesses dados (seção "Entradas") | Nada. Candidatos à remoção depois de um eval: `metabolic_age`, proteína, gordura subcutânea e as notas livres de sono e esforço |
| `periodization` | Catálogo da academia (sem nome), instruções livres (sanitizadas) | Geração do Modelo | Nada |
| Ficha por Modelo v1 (`workout_plan_templates_engine`, legado com fallback esperado zero) | Histórico de cargas, séries de RPE alto, recuperação, wellness (sem HRV, sono ou FC), nome da academia do pedido | Prescrição a partir do histórico | **Lista de todas as academias cadastradas** (de todos os usuários, com notas e horários), que só servia para resolver o nome |
| Seletor v2 | Matriz de candidatos com estrelas e data de último uso | Variedade | Nada |
| `exercise_description` e cards (imagem) | Só o catálogo de exercícios; no card, a instrução do revisor admin | Nenhum dado de usuário | Nada |

**A idade não entra em nenhum prompt.** Por isso não houve troca de idade exata por faixa.

## Retenção

| Onde | O quê | Quanto tempo |
|---|---|---|
| OpenAI, objeto de resposta | Com `store: false`, a resposta não fica disponível para recuperação pela API | Não é guardada para recuperação. O SDK `openai` 3.28.0 documenta que, com `store` verdadeiro (o default), ela fica guardada por pelo menos 30 dias |
| OpenAI, monitoramento de abuso | Entradas e saídas podem ser retidas pela OpenAI para monitoramento de abuso, independentemente de `store` | **A confirmar** na conta (painel Data controls) e no contrato. Retenção zero só existe por acordo com a OpenAI. O uso para treino de modelos, que a API não faz por padrão, também está **a confirmar** no painel |
| DynamoDB `async_jobs` | `request` (payload do job: notas, pergunta) e `result` | TTL de 6 h (`async_jobs._JOB_TTL_SECONDS`) |
| `cardio_recommendations.context_snapshot` | Contexto completo do cardio | Indefinido (decisão pendente em SEG-15/LGPD) |
| `template_generation_logs.request_json` | Pedido da Periodização com as instruções livres | Indefinido (idem) |
| `activities.ai_summary` | Resumo gerado e, desde o IA-14, `ai_summary_input_sha256`, um hash sem dado legível | Vida da atividade |

## Pendências fora deste PR

- Confirmar no painel da OpenAI a retenção de abuso e o uso para treino, e citar isso na política de privacidade.
- Notas livres que entram sem `sanitize_user_text`: resumo de exercício, resumo de treino e notas do cardio. É um tema de prompt injection, não de retenção.
- Remover campos do cardio sem uso declarado depois do eval da feature.
