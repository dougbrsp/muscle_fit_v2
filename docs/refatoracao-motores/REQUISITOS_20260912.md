# PROMPT PARA CLAUDE — REFATORAÇÃO DOS MOTORES MUSCLEFIT (2026-09-12)

Quero que você atue como **arquiteto de software sênior e tech lead**, fazendo uma análise de impacto completa do projeto MuscleFit antes de qualquer implementação.

Seu trabalho neste momento é **PLANEJAR a refatoração**, não alterar código.

A principal fonte de contexto arquitetural é o arquivo:

- `MUSCLE_FIT.md`

Ele descreve o estado da aplicação na AWS e o código do repositório em 2026-09-12, incluindo backend, frontend, banco, infraestrutura, motores, fluxos assíncronos e integrações.

Também use como fontes funcionais, quando necessário:

- `TECNICAS_AVANCADAS_RAG.md`
- `HABILIDADES_TREINO_RAG.md`
- `TREINO_A_RAG.md`
- `TREINO_B_RAG.md`
- `TREINO_C_RAG.md`
- `COMO_MONTAR_ABC_RAG.md`
- `TREINO_ABC_PERFEITO.md`
- `CARDIO_RAG.md`
- `AGACHAMENTO_RAG.md`
- `TRAPEZIO_RAG.md`
- `PANTURRILHA_RAG.md`
- `ABDOMEN_RAG.md`
- `POSTERIOR_COXA_RAG.md`
- `PERNA_RAG.md`
- `TRICEPS_RAG.md`
- `TREINO_BICEPS_RAG.md`
- `COSTAS_RAG.md`
- `PEITO_RAG.md`
- demais arquivos RAG disponíveis no repositório, especialmente `PROGRESSAO.md`, se presente.

## Regra de trabalho

1. Leia primeiro `MUSCLE_FIT.md`.
2. Depois confronte a documentação com o **código atual do repositório**. Se houver divergência, considere o código atual como fonte de verdade e registre a divergência.
3. Use `rg`/busca global para localizar todos os call sites, tipos, endpoints, queries, tabelas, componentes, prompts e regras antigas afetadas.
4. Não assuma que um comportamento está isolado em um único arquivo.
5. Não implemente nada.
6. Não faça uma proposta genérica. Quero um plano referenciado em **arquivos, módulos, funções, endpoints, tabelas e componentes reais**.
7. Sempre que uma regra abaixo tiver ambiguidade, não a esconda. Identifique a lacuna, explique por que ela é relevante, proponha uma definição determinística e marque se a decisão precisa ser confirmada antes da implementação.
8. Procure explicitamente por lógica duplicada, fallback legado, caminhos alternativos, regras recalculadas na leitura e prompts de LLM que possam continuar aplicando regras antigas depois da refatoração.
9. O resultado deve ser um plano de implementação que outro engenheiro consiga executar em etapas pequenas, testáveis e reversíveis.
10. Priorize lógica determinística para motores de execução. LLM deve ser usada somente onde a especificação realmente pede geração criativa/assistida.

---

# 1. OBJETIVO DA REFATORAÇÃO

Quero refatorar principalmente três áreas:

1. **Motor de Progressão**
2. **Motor de Geração de Ficha de Treino**
3. **Novo Motor de Periodização de Treino**

Como parte dessa refatoração, o domínio de equipamentos também será enriquecido com três dimensões distintas que devem permanecer conceitualmente separadas:

- **preferência do usuário** por estrelas;
- **compatibilidade com técnicas avançadas**;
- **compatibilidade com RPE 10 / falha concêntrica**.

A regra arquitetural central passa a ser:

> **Uma ficha diária de treino nunca deve decidir uma divisão de treino. A divisão, os grupos musculares e a estrutura da sessão vêm exclusivamente de um Modelo de Treino previamente salvo.**

ABC, Push/Pull/Legs, Full Body ou qualquer outra divisão são apenas formas possíveis de um **modelo de treino**, e não devem existir como algoritmos especiais da geração da ficha diária.

---

# 2. MOTOR DE PROGRESSÃO — NOVO COMPORTAMENTO

## 2.1 Dados de entrada

O motor de progressão deve receber/obter os dados dos **últimos 21 treinos concluídos do usuário**.

Para esses treinos, devem estar disponíveis, no mínimo:

- data do treino;
- grupo muscular;
- exercício/mapping canônico, se necessário para manter identidade histórica;
- equipamento utilizado;
- número de séries;
- repetições por série;
- carga em kg por série;
- RPE por série;
- técnicas avançadas executadas, se houver;
- dor muscular reportada;
- dor articular reportada;
- vínculo com o exercício planejado (`workout_plan_exercise_id`) quando existir;
- data/hora pretendida do próximo treino.

O motor também deve receber do **Motor de Recuperação Muscular** o estado de prontidão/readiness de cada grupo muscular projetado para a data/hora pretendida do próximo treino.

Além disso, antes de avaliar a progressão, o motor deve resolver as **capacidades e restrições de segurança do equipamento/exercício** aplicáveis àquela prescrição, incluindo no mínimo:

- se aquela combinação permite atingir **RPE 10 / falha concêntrica** com segurança;
- qual é o teto efetivo de RPE quando RPE 10 não é permitido;
- quais técnicas avançadas são compatíveis com o equipamento naquela utilização;
- se alguma técnica compatível exige ou pressupõe falha e, portanto, fica automaticamente proibida quando RPE 10 não for permitido.

Esses dados são diferentes da preferência por estrelas do usuário: estrelas representam preferência; compatibilidade com técnica e falha representam **capacidade/safety constraint do catálogo** e nunca podem ser sobrepostas pela preferência.

Analise se o contrato deve usar:

- um readiness numérico;
- `READY | REDUCED_DOSE | NO_DIRECT_WORK`;
- ambos;
- e quais outros sinais do motor de recuperação devem ser transportados para a decisão de progressão.

## 2.2 Avaliação por fases

A progressão será avaliada em fases.

### Fase 1

Quando:

- o usuário executa as séries planejadas na faixa de **6–8 repetições** com a carga proposta;
- a última série chega a **RPE 9–10**;
- não existe dor muscular ou articular impeditiva reportada ao motor de recuperação;
- nenhuma série anterior à última chegou a RPE 10;

Então:

- **sem progressão**.

### Fase 2

Quando:

- por mais de uma semana o usuário atinge o teto de **8 repetições** com a carga proposta;
- a última série chega a RPE 9–10;
- não existe dor muscular ou articular impeditiva;
- nenhuma série anterior à última chegou a RPE 10;

Então:

- alterar a meta para **10–12 repetições**, mantendo a carga.

### Fase 3

Quando:

- por mais de uma semana o usuário atinge o teto de **12 repetições** com a carga proposta;
- a última série chega a RPE 9–10;
- não existe dor muscular ou articular impeditiva;
- nenhuma série anterior à última chegou a RPE 10;

Então:

- aumentar a carga em **2,5 kg a 5 kg**.

### Fase 4

Quando:

- por mais de uma semana o usuário atinge o teto de **12 repetições** com a carga proposta;
- a última série chega a RPE 9–10;
- não existe dor muscular ou articular impeditiva;
- nenhuma série anterior à última chegou a RPE 10;

Então:

- aplicar **técnicas avançadas**.

### Fase 5

Quando:

- por mais de uma semana o usuário atinge o teto de **12 repetições** com a carga proposta;
- a última série chega a RPE 9–10;
- não existe dor muscular ou articular impeditiva;
- nenhuma série anterior à última chegou a RPE 10;
- técnicas avançadas já estão sendo aplicadas;

Então:

- aumentar **uma série** no exercício, com teto de **4 séries**.

### Fase 6

Quando:

- por mais de uma semana o usuário atinge o teto de **12 repetições** com a carga proposta;
- a última série chega a RPE 9–10;
- não existe dor muscular ou articular impeditiva;
- nenhuma série anterior à última chegou a RPE 10;
- técnicas avançadas já estão sendo aplicadas;
- o exercício já utiliza 4 séries;

Então:

- aumentar a carga em **2,5 kg a 5 kg a cada 2 semanas**.

## 2.2.1 Regra transversal de segurança para RPE 10 / falha

Todas as fases acima precisam ser reinterpretadas à luz da capacidade de segurança do exercício/equipamento.

Quando a combinação utilizada **não permitir RPE 10 / falha concêntrica**:

- a ficha nunca deve prescrever RPE 10 para aquele exercício;
- o Motor de Progressão deve trabalhar com um `effective_rpe_ceiling` seguro, provavelmente RPE 9 salvo regra mais restritiva;
- atingir o teto seguro deve contar como atingimento da intensidade esperada para fins de progressão;
- o usuário **não pode ser penalizado, classificado como abaixo da meta ou impedido de progredir** por não atingir um RPE que o próprio sistema proíbe por segurança;
- regras como “última série em RPE 9–10” devem ser avaliadas contra o alvo/teto seguro efetivamente prescrito, não contra RPE 10 de forma cega;
- RPE 10 observado historicamente em uma combinação marcada como incompatível deve ser tratado como um possível evento de segurança/dado inconsistente, nunca como requisito desejável para repetir;
- qualquer técnica avançada que dependa de falha deve ser eliminada dos candidatos antes da prescrição.

Peça para o plano definir uma função/contrato único para calcular algo como `effective_target_rpe`, `effective_rpe_ceiling` e `failure_allowed`, para que essa regra não seja reimplementada de formas diferentes no gerador, progressão, UI e analytics.

## 2.3 O que o plano deve definir para o motor de progressão

Não basta substituir `suggest_load_and_reps()` por outro `if/else`.

Quero que você determine:

- qual é a **unidade de estado** da progressão:
  - usuário + grupo muscular?
  - usuário + exercício canônico?
  - usuário + exercício + equipamento?
  - outra combinação?
- se as fases devem ser:
  - inferidas integralmente do histórico;
  - persistidas explicitamente;
  - ou inferidas + persistência de eventos/transições;
- como registrar:
  - fase atual;
  - data de entrada na fase;
  - última mudança de carga;
  - última mudança de faixa de repetições;
  - aplicação de técnica avançada;
  - aumento de séries;
  - próxima data em que uma progressão da Fase 6 está liberada;
- como o motor se comporta quando os critérios de progressão **não** são satisfeitos;
- como reage a:
  - RPE 10 antes da última série;
  - dor muscular;
  - dor articular;
  - readiness reduzido;
  - `NO_DIRECT_WORK`;
  - ausência de RPE;
  - ausência de carga;
  - exercício novo;
  - troca de equipamento;
  - troca de exercício;
  - treino avulso sem plano anterior;
  - séries de aquecimento;
  - séries de ajuste;
  - drop-set/rest-pause/forced reps e outros `set_type`;
  - equipamento/exercício incompatível com RPE 10;
  - plano legado que ainda possua `rpe_target=10` em equipamento incompatível;
- como diferenciar séries de trabalho normais de aplicações de técnicas avançadas;
- como usar os RAGs de técnicas avançadas com segurança;
- como impedir que técnica avançada seja aplicada em situação incompatível com dor, recuperação, experiência do usuário ou risco do exercício;
- como cruzar **técnica requerida × técnicas compatíveis com o equipamento × permissão de falha/RPE 10**;
- como considerar como sucesso para progressão o atingimento do **RPE seguro prescrito**, sem exigir RPE 10 quando ele for proibido;
- como respeitar o menor incremento realmente disponível do equipamento quando 2,5 kg ou 5 kg não forem possíveis;
- se a carga deve ser prescrita por exercício/equipamento mesmo que a **fase** seja por grupo muscular.

O retorno do motor deve ser estruturado e auditável. Proponha um contrato semelhante a:

- `phase_before`;
- `phase_after`;
- `decision`;
- `suggested_sets`;
- `suggested_reps_min`;
- `suggested_reps_max`;
- `suggested_load_kg`;
- `advanced_technique`;
- `advanced_technique_parameters`;
- `failure_allowed`;
- `effective_rpe_target`;
- `effective_rpe_ceiling`;
- `safety_constraints`;
- `can_progress`;
- `blocked_by`;
- `evidence`;
- `next_eligible_progression_at`;
- `reason`.

Não precisa usar exatamente esses nomes, mas preciso de uma decisão rastreável.

---

# 3. MOTOR DE GERAÇÃO DE FICHA — NOVO MODELO

Passarão a existir **dois modos de montagem de ficha**, e ambos dependem de um **Modelo de Treino salvo**.

A geração diária não escolhe mais ABC, PPL, Full Body etc.

## 3.1 Modo A — Modelo por Grupos Musculares

O motor deve:

1. Escolher o slot:
   - pela sequência do modelo;
   - ou pelo slot explicitamente escolhido pelo usuário.

2. Ler no slot:
   - grupos musculares;
   - `%min`;
   - `%max`;
   - prioridade, se aplicável.

3. As faixas `%min/%max` são relativas às **metas de ativação semanal** do perfil associado ao modelo.

4. Antes de montar a sessão atual, ler os mesmos parâmetros de **todos os outros slots do ciclo**, para entender a distribuição de ativação ao longo da semana/ciclo.

5. Calcular ativação usando:
   - `exercise_muscle_contributions.role`;
   - `stimulus_weight`;
   - participação `PRIMARY`, `SECONDARY` e `STABILIZER`.

6. A sessão deve respeitar a faixa de ativação prevista para o grupo no slot sem fazer a semana/ciclo ultrapassar ou ficar abaixo das metas previstas.

7. Se houver **subativação** projetada:
   - avaliar aplicação de técnica avançada;
   - ou pico de contração;
   - mas somente se isso puder ser quantificado/representado de maneira coerente no motor de ativação e não conflitar com recuperação/progressão.

8. Para cada grupo muscular, escolher equipamento disponível na academia atual com prioridade:

   1. maior número de **estrelas de preferência do usuário** para aquele equipamento;
   2. entre equipamentos com a mesma preferência, favorecer variabilidade:
      - primeiro variar entre os de maior preferência;
      - depois favorecer o equipamento que está há mais dias sem ser utilizado;
   3. depois considerar os demais equipamentos válidos para o mesmo grupo muscular.

   A preferência é um critério de ranking, **nunca um override de segurança**. Antes de ranquear ou confirmar um candidato, o motor deve conhecer as capacidades/restrições aplicáveis àquela combinação exercício/equipamento:

   - técnicas avançadas compatíveis;
   - permissão ou proibição de RPE 10/falha concêntrica;
   - teto seguro de RPE derivado dessa configuração;
   - eventual override específico da associação exercício ↔ equipamento.

   Se a progressão ou a correção de subativação já exigir uma técnica específica, equipamentos incompatíveis com essa técnica devem ser removidos dos candidatos **antes** do desempate por estrelas/recência.

   Se a técnica exigir falha e o equipamento não permitir RPE 10/falha, a combinação deve ser considerada inválida mesmo que a técnica esteja marcada incorretamente como compatível. A validação deve ser defensiva.

9. A escolha precisa continuar respeitando:
   - exercício canônico;
   - grupo muscular;
   - contribuições musculares;
   - disponibilidade real na academia;
   - compatibilidade exercício ↔ equipamento;
   - compatibilidade equipamento/exercício ↔ técnica avançada;
   - restrição de RPE máximo/falha;
   - segurança antes de preferência/variabilidade.

10. Séries, repetições, carga e limites de intensidade devem ser obtidos dos motores responsáveis.

   **ATENÇÃO:** a especificação original diz que “número de séries, repetições e carga” vêm do motor de recuperação, mas o desenho atual e o novo Motor de Progressão sugerem responsabilidades diferentes. Você deve identificar essa ambiguidade e propor um ownership claro, preferencialmente separando:
   - recuperação = prontidão/limites de dose/intensidade;
   - progressão = séries/reps/carga/técnica quando aplicável;
   - modelo = intenção estrutural da sessão.

   Não altere silenciosamente a regra: explique e proponha o contrato final.

11. Ordenar os exercícios para evitar fadiga excessiva em sequência sobre os mesmos grupos musculares.

   Analise se `fatigue_weight` e as contribuições secundárias/estabilizadoras já permitem montar um algoritmo determinístico de ordenação.

## 3.2 Modo B — Modelo por Ficha Pronta

O motor deve:

1. Escolher o slot pela sequência ou escolha explícita do usuário.

2. Montar a ficha conforme o slot, preservando:
   - grupo muscular;
   - exercício/mapping;
   - equipamento;
   - número de séries;
   - intervalo entre séries;
   - técnica avançada e seus parâmetros;
   - séries de aquecimento:
     - quantidade;
     - repetições;
     - intervalo;
   - séries de ajuste:
     - quantidade;
     - repetições;
     - intervalo;
   - pico de contração.

3. Consultar o Motor de Progressão para definir:
   - repetições;
   - carga;
   - e qualquer ajuste que o contrato de progressão realmente autorize.

4. Definir explicitamente a precedência quando:
   - o modelo já fixa uma técnica avançada;
   - e a fase de progressão também quer aplicar uma técnica avançada.

   Independentemente da precedência, validar a técnica contra a lista de técnicas compatíveis com o equipamento/exercício. Se o modelo antigo/fixo pedir uma técnica incompatível, não executar silenciosamente: definir erro, warning, necessidade de edição ou substituição segura.

5. Validar também `rpe_target` e qualquer orientação de falha contra a capacidade do equipamento/exercício. Um modelo salvo com RPE 10 não deve obrigar o usuário a falhar em uma combinação marcada como insegura. Defina se o sistema:
   - faz clamp para o teto seguro e registra warning/auditoria;
   - bloqueia o modelo até correção;
   - ou aplica outra estratégia explicitamente definida.

6. Definir também o comportamento se o equipamento fixado no modelo não estiver disponível na academia selecionada:
   - erro bloqueante;
   - warning e necessidade de intervenção do usuário;
   - substituição automática;
   - ou política configurável.

Não assuma que o comportamento atual de substituição automática deve continuar sem análise.

---

# 4. MUDANÇA DE CONCEITO — “METAS DE ATIVAÇÃO”

Hoje existe o conceito de `training_types` com:

- nome;
- descrição;
- `rag_folder`;
- metas por grupo muscular:
  - `min_weekly_sets`;
  - `target_weekly_sets`;
  - `max_weekly_sets`.

Quero que as metas usadas na montagem do modelo/ficha passem a ser tratadas como **Metas de Ativação**.

Analise cuidadosamente se isso significa:

1. apenas renomear textos/UI;
2. renomear API e domínio;
3. migrar tabelas/entidades;
4. ou separar em duas entidades diferentes.

Considere que hoje `training_types` mistura duas responsabilidades:

- perfil de metas semanais;
- referência a uma pasta RAG.

Com o novo Motor de Periodização, talvez seja melhor separar:

- **perfil de metas de ativação**;
- **fontes/presets de periodização/RAG**.

Não faça a separação só porque parece elegante. Analise dependências reais e diga se vale a pena.

---

# 5. REMOVER DIVISÕES ESPECIAIS DA GERAÇÃO DIÁRIA

Esta é uma regra obrigatória.

As divisões de treino, como:

- ABC;
- Push/Pull/Legs;
- Full Body;
- ou qualquer outra;

**não podem mais fazer parte da lógica especial do Motor de Geração de Ficha.**

Portanto, procure e planeje a remoção/desativação de:

- templates ABC hardcoded;
- branches especiais `plan_type == "abc"`;
- `split_day`;
- seleção automática de A/B/C;
- geração Full Body que escolhe grupos fora de um modelo salvo;
- prompts que pedem para uma LLM escolher uma divisão;
- opções de UI que gerem ficha por “tipo de plano” fora do modelo;
- fallback que possa reintroduzir a divisão;
- dashboards/labels que dependam de `plan_type`/`split_day` como comportamento;
- testes e tipos antigos associados;
- código morto decorrente disso.

No estado documentado, examine com atenção, entre outros:

- `abc_workout_template.py`;
- `abc_workout_ai.py`;
- `workout_plan_ai.py`;
- `plan_generation_core.py`;
- `workout_plan_templates_engine.py`;
- `workout_plan_templates_engine_v2.py`;
- `workout_plan_templates_engine_equipment.py`;
- `template_ai_selector_v2.py`;
- `services.py`;
- `models.py`;
- `workout_plans.py`;
- frontend de `/planos-treino`;
- `GenerateDraftModal`;
- `workout-plans-types.ts`;
- `PLAN_TYPE_OPTIONS`;
- `SPLIT_DAY_OPTIONS`.

Também avalie se `template_type = full_body|abc` continua tendo alguma função real ou se deve virar apenas metadado, ser removido, ou migrado.

## Regra nova

> Para gerar uma ficha diária, deve existir um `template_id`/Modelo de Treino válido. O motor lê o slot e executa o que o modelo determinou.

Se existir algum fluxo atual que ainda permita gerar ficha sem modelo, identifique-o e proponha como será encerrado ou migrado.

---

# 6. NOVO MOTOR DE PERIODIZAÇÃO DE TREINO

As divisões de treino passam para um novo motor chamado **Motor de Periodização de Treino**.

Ele não gera a ficha diária final.

Ele gera um **rascunho de Modelo de Treino**.

## Fluxo desejado

1. Usuário escolhe uma opção de criação automática de Modelo de Treino.
2. Usuário escolhe:
   - RAG/preset/fonte de conhecimento disponível;
   - eventualmente mais de uma fonte, se o desenho permitir;
   - perfil de Metas de Ativação;
   - academia, se isso fizer sentido para o modelo;
   - instruções livres adicionais.
3. Backend reúne contexto.
4. Chama a API da OpenAI.
5. A OpenAI devolve um **rascunho estruturado de Modelo de Treino**, nunca uma ficha diária.
6. O rascunho passa por validação determinística.
7. O usuário abre o builder atual, edita o rascunho.
8. O modelo só é salvo/ativado após ação explícita do usuário.

## O motor deve poder gerar, por exemplo

- um modelo ABC;
- Push/Pull/Legs;
- Upper/Lower;
- Full Body;
- outra periodização sugerida pelos RAGs;

mas o resultado sempre é traduzido para a mesma estrutura genérica de:

- slots;
- grupos/equipamentos;
- percentuais;
- parâmetros;
- descanso;
- técnicas;
- demais configurações do Modelo de Treino.

Depois de salvo, a geração diária não deve saber nem precisar saber se aquilo originalmente era “ABC” ou “PPL”.

## Planejar

- novo endpoint;
- payload de entrada;
- JSON Schema de saída da LLM;
- validação;
- integração com OpenAI;
- seleção de RAG;
- estratégia de prompt;
- async job ou chamada síncrona;
- frontend;
- botão/fluxo de “Gerar modelo automaticamente”;
- preview;
- edição;
- salvamento;
- auditoria;
- tratamento de erro;
- segurança contra saída inválida da LLM;
- custos/timeouts;
- compatibilidade com `musclefit-plan-generator` ou necessidade de um job/função separado.

---

# 7. PONTOS DO ESTADO ATUAL QUE PRECISAM SER VERIFICADOS

A documentação atual sugere alguns pontos de risco. Confirme todos no código atual.

## 7.1 Existem múltiplas “progressões”

Hoje parecem existir pelo menos:

- `progression_service.py` com `suggest_load_and_reps`;
- `intelligence/progression_service.py` com classificação de tendência;
- `services.suggested_progression_for_mapping`;
- `assign_progression_indicators`;
- `_enrich_plan_exercises_with_progression`;
- endpoint `/exercises/suggested-progression`;
- dashboards de progressão;
- avaliação por IA durante séries.

Mapeie quais são:

- motor de decisão;
- analytics;
- UI helper;
- IA;
- recalculo de leitura;
- legado.

A nova regra de progressão deve ter **uma única fonte de verdade para prescrição**.

## 7.2 A leitura de plano recalcula progressão

Hoje `get_workout_plan()` aparentemente recalcula progressão ao ler o plano.

Analise se isso deve continuar.

Uma ficha já gerada para uma data específica provavelmente deve ter:

- prescrição versionada/congelada;
- ou recalculo controlado;

mas não quero mudança silenciosa de carga/reps só porque o usuário abriu a tela novamente.

Proponha a semântica correta.

## 7.3 Recuperação projetada limpa sinais de dor no futuro

O fluxo atual de projeção de recuperação parece zerar/ignorar:

- `muscle_pain`;
- `joint_pain`;
- alguns sinais de performance;

quando a data alvo está no futuro.

Isso pode conflitar diretamente com a nova progressão, que depende da ausência de dor.

Confirme no código e proponha uma política coerente.

## 7.4 Preferência por estrelas não existe

No estado documentado, `/gym-equipment/recommend` e `/recommend-all` ordenam principalmente por:

- nunca usado;
- mais tempo desde o último uso.

Não aparece persistência de estrelas/preferência por usuário.

Planeje, se confirmado:

- tabela;
- chave única;
- escala de estrelas;
- default;
- API CRUD;
- frontend;
- inclusão no recomendador;
- índices;
- regra de desempate;
- migração;
- autorização por usuário.

## 7.5 Compatibilidade de técnicas avançadas por equipamento não existe

O estado atual possui associações entre equipamento e exercício (`equipment_exercises` e `equipment_exercise_mappings`), e técnicas avançadas aparecem nos itens de modelo/plano, mas não há no estado documentado uma matriz explícita dizendo **quais técnicas são válidas para qual equipamento ou combinação exercício/equipamento**.

Planeje, se confirmado:

- um catálogo canônico de técnicas avançadas/códigos ou outra fonte única de verdade para os valores hoje gravados em `advanced_technique`;
- relacionamento de compatibilidade com equipamento;
- análise da granularidade correta: equipamento global, associação exercício ↔ equipamento, ou default global com override por associação;
- API administrativa para leitura/escrita;
- UI no cadastro/associação de equipamentos;
- validação ao salvar Modelo de Treino;
- validação ao gerar ficha;
- validação no Motor de Progressão/Fase 4;
- validação de técnicas que pressupõem falha;
- migração/default dos equipamentos existentes;
- comportamento quando a compatibilidade estiver `NULL`/desconhecida;
- auditoria indicando por que determinada técnica foi aceita ou descartada.

**Não coloque essa informação automaticamente em `gym_equipment` sem analisar o domínio.** `gym_equipment` é um cache materializado por academia/mapping/equipamento. Compatibilidade de segurança tende a pertencer ao catálogo canônico ou à relação canônica exercício ↔ equipamento, salvo se existir motivo real para variar por unidade física da academia.

Também investigue a sobreposição atual entre `equipment_exercises` e `equipment_exercise_mappings` antes de escolher onde persistir o novo dado. Não crie uma terceira fonte concorrente sem resolver ou documentar a autoridade das relações existentes.

## 7.6 Compatibilidade com RPE 10 / falha não existe

Preciso poder marcar se um equipamento — ou, se arquiteturalmente mais correto, uma combinação exercício/equipamento — é compatível com **RPE 10 / repetições até a falha**.

Exemplo de motivação: não quero que o sistema proponha falha concêntrica em um supino com barra quando isso elevar desnecessariamente o risco ao usuário.

Planeje, se confirmado:

- persistência do atributo (`allows_rpe_10`, `failure_allowed`, `max_safe_rpe` ou contrato equivalente);
- granularidade correta e possibilidade de override;
- default seguro para dados legados/desconhecidos;
- administração desse dado no frontend;
- propagação para recomendação de equipamento, geração de ficha e progressão;
- clamp/normalização do `rpe_target`;
- tratamento de planos/modelos legados que já contenham RPE 10;
- tratamento de histórico real em que o usuário registrou RPE 10 num item hoje marcado como incompatível;
- relação com `users.is_advanced_user`: ser avançado **não pode** ignorar uma restrição explícita de segurança do equipamento;
- relação com avaliação de série por IA e qualquer sugestão de “próxima série” que possa elevar RPE/carga;
- relação com `exercise_set_evaluation_ai`, `SetEvaluationResult.suggested_next_rpe` e o modo treino ao aplicar sugestões; qualquer sugestão de IA deve sofrer clamp/rejeição determinística se ultrapassar o limite seguro;
- impacto em analytics que hoje classificam RPE 10 precoce ou usam RPE alvo do plano;
- impacto em `progression_service.py`, `intelligence/progression_service.py`, `VolumeService`, dashboards de progressão e qualquer lógica de `rpe_anticipated`;
- logs/auditoria de toda redução de RPE motivada por segurança.

A regra central deve ser:

> Se RPE 10 não é permitido, o Motor de Progressão deve avaliar o usuário contra a intensidade segura que foi realmente prescrita. O fato de ele não atingir RPE 10 não pode ser interpretado como insuficiência para progredir.

## 7.7 Técnicas avançadas e ativação

Hoje `stimulus_weight` calcula ativação por séries, mas técnicas avançadas e `peak_contraction_seconds` aparentemente não alteram diretamente a ativação equivalente.

Se o novo motor usar técnica avançada/pico de contração para corrigir subativação, precisamos de uma regra quantitativa ou de uma redefinição do que “corrigir subativação” significa.

Não aceite “aplicar técnica avançada” como ajuste matemático se o motor de ativação continuar contabilizando exatamente a mesma coisa.

## 7.8 Técnicas avançadas têm múltiplos donos

Hoje técnicas podem existir:

- no modelo pronto;
- no plano;
- no modo avançado;
- nos `set_type`;
- e agora poderão ser adicionadas pela progressão e talvez pelo gerador por subativação.

Defina uma hierarquia de autoridade para impedir duas técnicas simultâneas ou configurações incompatíveis. A hierarquia deve sempre terminar numa validação de segurança contra:

- técnicas permitidas para o equipamento/exercício;
- permissão de RPE 10/falha;
- readiness/dor;
- experiência/flags do usuário;
- requisitos intrínsecos da técnica.

## 7.9 Persistência incompleta no frontend

A documentação indica que os tipos do plano possuem campos avançados, mas o payload de `POST/PUT /workout-plans` enviado pelas telas pode não incluir todos eles.

Confirme.

Se for verdadeiro, isso é um bug de round-trip e deve entrar no plano.

## 7.10 Aquecimento e série de ajuste

No modo de treino atual, parte do aquecimento/série de ajuste parece existir apenas em estado local e nem sempre ser persistida.

Avalie como isso afeta:

- histórico dos 21 treinos;
- RPE;
- contagem de séries;
- detecção de técnica;
- recuperação;
- progressão.

Defina quais séries contam para progressão e quais contam para recuperação.

## 7.11 Sem suíte de testes de aplicação

A documentação indica ausência de suíte de testes frontend e poucos testes gerais fora de ferramentas auxiliares.

Confirme.

Esta refatoração deve incluir uma estratégia séria de testes automatizados antes de remover os caminhos legados.

## 7.12 Migrações de banco

O projeto aparentemente não possui um diretório/ferramenta de migrations versionadas convencional, apesar da tabela `schema_migrations` e de alterações `_ensure_*` em runtime.

Avalie como fazer as novas mudanças de schema de forma segura e reproduzível.

Não quero espalhar novos `ALTER TABLE` oportunistas em cold start se houver alternativa melhor.

---

# 8. AMBIGUIDADES QUE VOCÊ DEVE RESOLVER NO PLANO

Não ignore estes pontos.

## A. Fases 3 e 4 têm o mesmo gatilho

Se as fases forem avaliadas apenas por condições atuais, Fase 3 e Fase 4 disparam juntas.

Logo, é necessário definir uma máquina de estados/transição ou outra evidência de que a Fase 3 já foi completada antes de entrar na Fase 4.

Proponha a regra.

## B. “Por mais de uma semana”

Defina de forma determinística.

Exemplos que você deve avaliar:

- duas ou mais sessões separadas por pelo menos 7 dias;
- critério mantido em todas as sessões relevantes durante uma janela >= 7 dias;
- primeira ocorrência + nova ocorrência depois de 7 dias;
- outro modelo.

Explique por que escolheu.

## C. Fase por grupo vs carga por exercício

Carga e repetições são específicas do exercício/equipamento, mas a especificação fala em avaliação “por grupo muscular”.

Defina claramente:

- o que pertence ao estado do grupo;
- o que pertence ao exercício;
- o que pertence ao equipamento.

## D. Troca de equipamento e exercício

Não trate automaticamente:

- supino máquina;
- supino com halteres;
- peck deck;

como uma única série histórica de carga.

Determine quando histórico pode ou não ser compartilhado.

## E. Dor

Defina:

- “não recebeu informação de dor” significa `NULL` ou significa valor 0/false?
- qual limiar de dor muscular bloqueia progressão?
- dor articular `true` sempre bloqueia?
- dor reportada numa sessão passada continua bloqueando a sessão futura por quanto tempo?
- o readiness do motor de recuperação já deve encapsular essa decisão ou a progressão deve conferir o sinal bruto?

## F. Readiness

A progressão deve:

- impedir qualquer progressão em `REDUCED_DOSE`?
- apenas limitar?
- impedir em `NO_DIRECT_WORK`?
- retornar prescrição mesmo que o exercício não deva ser executado?

Defina.

## G. Incremento de carga

A regra é 2,5–5 kg.

Defina:

- escolha entre 2,5 e 5;
- compostos vs isoladores;
- incremento absoluto vs percentual;
- halteres por mão vs carga total;
- máquinas com stack;
- equipamento sem incremento cadastrado.

Se o catálogo não armazena incremento mínimo, diga se precisamos armazenar.

## H. Aumento de séries na Fase 5

“Adicionar uma série ao exercício, máximo 4” precisa definir o que acontece quando um grupo tem 2 ou 3 exercícios.

Quem recebe a série extra?

Proponha uma regra determinística.

## I. Técnica avançada na Fase 4

Defina como escolher entre:

- rest-pause;
- drop-set;
- FST-7;
- GVT;
- forced reps;
- negativas;
- parciais;
- cheat reps;
- outras disponíveis.

Use as regras de segurança dos RAGs.

Nem todas são equivalentes e algumas não deveriam ser aplicadas automaticamente.

## J. Conflito entre técnica do modelo e técnica da progressão

Defina precedência e comportamento.

## K. Metas semanais vs ciclo do modelo

Hoje alguns cálculos assumem 7 dias e alguns trabalham com ciclo de slots.

Defina como interpretar “ativação semanal” quando o modelo tiver:

- 3 slots;
- 4 slots;
- 5 slots;
- dias de descanso;
- ciclo maior ou menor que 7 dias;
- slots sem `weekday`.

## L. Ativação real + ativação planejada

Ao gerar o slot atual, diga se o cálculo deve usar:

- apenas o modelo;
- o que já foi executado na semana + o que ainda está planejado;
- ou uma combinação.

Minha preferência é que o plano considere o que **realmente aconteceu** quando houver histórico, mas quero sua análise arquitetural.

## M. Modo “Ficha pronta” e equipamento indisponível

Defina a política.

## N. Nome/semântica de `training_types`

Defina se vira:

- `activation_goals`;
- `activation_profiles`;
- apenas um label novo;
- ou outra entidade.

## O. Geração por LLM no motor v2 atual

Hoje existe IA para selecionar exercícios em alguns caminhos de template.

O novo Modo por Grupos possui regras determinísticas explícitas de prioridade de equipamentos.

Avalie se a LLM deve sair completamente da geração diária.

Minha expectativa é que a geração diária seja **determinística e reproduzível**, e que a LLM fique concentrada no novo Motor de Periodização.

Se discordar, justifique com um caso em que a LLM seja realmente necessária.

## P. Granularidade das capacidades de segurança do equipamento

O requisito foi descrito como atributo do “equipamento”, mas a segurança pode depender da combinação de exercício e equipamento.

Exemplo: uma barra não tem o mesmo perfil de risco em Rosca Direta, Supino com Barra e Agachamento Livre.

Analise e recomende entre:

- atributo global em `equipment`;
- atributo na associação `equipment_exercises`/`equipment_exercise_mappings`;
- default global no equipamento + override por exercício/equipamento;
- outra modelagem.

A recomendação deve minimizar duplicação e representar corretamente o domínio.

## Q. Semântica de RPE 10 proibido

Defina exatamente como a progressão interpreta uma sessão em equipamento com falha proibida.

Quero que o plano responda:

- qual passa a ser o `rpe_target`;
- qual é o `effective_rpe_ceiling`;
- se RPE 9 satisfaz integralmente o critério “RPE 9–10” das fases;
- como lidar com RPE 8 quando o alvo seguro era 9;
- como lidar com RPE 10 registrado mesmo sendo proibido;
- como analytics e dashboards devem apresentar a diferença entre “não chegou à falha” e “falha não era permitida”;
- como impedir que o usuário seja penalizado por obedecer ao limite de segurança.

## R. Técnica compatível × técnica que exige falha

Uma técnica pode estar marcada como compatível mecanicamente com um equipamento e ainda ser incompatível porque seu protocolo exige falha.

Defina uma taxonomia mínima para técnicas avançadas, por exemplo:

- `requires_failure`;
- `may_reach_failure`;
- `failure_not_required`;
- outras restrições relevantes.

Use os RAGs como fonte funcional, mas transforme a decisão final do motor em dados/regras determinísticas.

O conjunto final de técnicas elegíveis deve ser uma interseção entre:

1. técnicas permitidas pelo exercício/equipamento;
2. técnicas compatíveis com a permissão de falha/RPE;
3. técnicas autorizadas pela fase de progressão/modelo;
4. técnicas compatíveis com recovery/dor;
5. técnicas adequadas ao usuário;
6. técnicas que não conflitem com outra técnica já fixada.

Defina também o que acontece na Fase 4 quando essa interseção for vazia.

---

# 9. ARQUITETURA DESEJADA — PRINCÍPIO DE RESPONSABILIDADE

Use este princípio como hipótese e valide contra o código:

## Modelo de Treino

Responsável por:

- estrutura do ciclo;
- slots;
- grupos;
- faixas de alocação;
- exercícios/equipamentos fixos no modo ficha pronta;
- descanso;
- aquecimento;
- série de ajuste;
- técnica explícita;
- pico de contração;
- intenção do treino.

## Motor de Ativação/Volume

Responsável por:

- calcular contribuição PRIMARY/SECONDARY/STABILIZER;
- aplicar `stimulus_weight`;
- consolidar ativação real e projetada;
- validar faixa semanal/ciclo;
- reportar déficit/excesso.

## Motor de Recuperação

Responsável por:

- readiness atual/projetado;
- dor/sinais de recuperação;
- dose permitida;
- limites de RPE/falha;
- restrições como `READY`, `REDUCED_DOSE`, `NO_DIRECT_WORK`.

## Motor de Progressão

Responsável por:

- fase;
- reps;
- carga;
- evolução de séries;
- técnica introduzida pela progressão;
- cadência temporal das transições;
- justificativa auditável.

## Motor de Seleção de Exercício/Equipamento

Responsável por:

- disponibilidade na academia;
- preferência por estrelas;
- variabilidade;
- recência de uso;
- compatibilidade exercício/equipamento/grupo;
- técnicas avançadas compatíveis;
- permissão de RPE 10/falha e teto seguro de RPE;
- filtragem de segurança antes do ranking de preferência;
- deduplicação.

## Motor de Geração de Ficha

Responsável por orquestrar os motores acima para um slot já decidido pelo Modelo de Treino.

Não deve inventar divisão.

## Motor de Periodização

Responsável por usar RAG + instruções + OpenAI para criar **rascunho editável de Modelo de Treino**.

---

# 10. BANCO DE DADOS — O QUE ANALISAR

Mapeie impacto sobre as tabelas atuais, incluindo no mínimo:

- `activities`;
- `exercises`;
- `exercise_sets`;
- `exercise_catalog`;
- `exercise_muscle_contributions`;
- `muscle_groups`;
- `muscle_recovery_coefficients`;
- `equipment`;
- `equipment_exercises`;
- `equipment_exercise_mappings`;
- `gym_equipment`;
- `gym_equipment_catalog`;
- `training_types`;
- `training_type_muscle_group_targets`;
- `workout_plan_templates`;
- `workout_plan_template_slots`;
- `workout_plan_template_slot_items`;
- `workout_plan_template_assignments`;
- `workout_plans`;
- `workout_plan_exercises`;
- `users`.

Proponha somente tabelas novas que sejam justificadas.

Antes de criar tabela nova, investigue especificamente que `equipment_exercises` já possui a relação equipamento ↔ exercício canônico e que `equipment_exercise_mappings` também mantém relação equipamento ↔ `mapping_id`. Determine qual delas é a fonte canônica real e se uma delas pode ser consolidada no futuro. Compatibilidade de técnica e falha não deve ser duplicada cegamente nas duas.

Avalie a necessidade de algo como:

- preferências de equipamento por usuário;
- catálogo canônico de técnicas avançadas, caso hoje só existam strings/constantes dispersas;
- matriz de compatibilidade técnica ↔ equipamento ou técnica ↔ exercício/equipamento;
- capacidade `allows_rpe_10`/`max_safe_rpe` global e/ou por associação exercício/equipamento;
- metadados de segurança/observação e origem da regra;
- estado/eventos de progressão;
- histórico de decisão do motor;
- fontes RAG associadas a periodização;
- drafts de modelos gerados por IA;
- versão do motor/regra usada numa prescrição.

Para cada alteração de schema, diga:

- finalidade;
- colunas;
- PK/FK;
- unique keys;
- índices;
- política de migração;
- dados legados;
- rollback.

---

# 11. API E CONTRATOS

Mapeie endpoints atuais afetados e proponha os novos contratos.

Inclua:

- geração de ficha;
- progressão;
- recuperação;
- recomendação de equipamento;
- preferência por estrelas;
- capacidades/restrições de segurança do equipamento;
- compatibilidade de técnicas avançadas por equipamento/exercício;
- modelos;
- metas de ativação;
- periodização;
- async jobs;
- dashboards.

Verifique também impacto no API Gateway/Terraform caso uma rota nova seja direcionada para `musclefit-engines`, pois os paths de engines são explicitamente roteados na infraestrutura.

Quero exemplos de request/response JSON dos contratos novos mais importantes.

Nos contratos de recomendação de equipamento (`/gym-equipment/recommend`, `/gym-equipment/recommend-all` ou seus substitutos), analise a inclusão explícita de metadados suficientes para que consumidores não precisem refazer consultas ou adivinhar regras, por exemplo:

- `user_preference_stars`;
- `allowed_advanced_techniques`;
- `failure_allowed`;
- `max_safe_rpe`/`effective_rpe_ceiling`;
- escopo/origem da regra (`equipment_default`, `exercise_equipment_override` etc.);
- motivo de exclusão quando um candidato foi descartado por segurança.

Não exponha dados redundantes sem necessidade, mas garanta que o Motor de Geração de Ficha receba as restrições no mesmo fluxo em que recebe os candidatos.

---

# 12. FRONTEND

Analise impacto nas telas e tipos reais.

No mínimo:

- `/planos-treino`;
- `GenerateDraftModal`;
- `ExercisesTable`;
- `/modo-treino`;
- `/modelos-treino`;
- `WorkoutPlanTemplateBuilderClient`;
- `WeeklyActivationPreviewTable`;
- `/tipos-treinamento`;
- dashboards de progressão;
- dashboards de recuperação;
- `/academias`;
- `/equipamentos-exercicios`;
- `/dominio-exercicios`, especialmente `EquipmentLinksSection`;
- tipos em `gyms-types.ts`;
- tipos em `workout-plans-types.ts`;
- tipos em `workout-plan-templates-types.ts`;
- tipos de academia/equipamento;
- menu/permissões.

Planeje UI para:

- estrelas/preferência por equipamento;
- administração das técnicas avançadas compatíveis com cada equipamento ou associação exercício/equipamento;
- flag/controle de `RPE 10 / falha permitida`, com descrição clara de segurança;
- eventual `max_safe_rpe` se essa modelagem for superior a um booleano;
- indicação na ficha/modo treino quando o RPE foi limitado por segurança;
- warning ao editar um modelo que fixa técnica ou RPE incompatível com o equipamento;
- novo nome “Metas de Ativação”;
- geração automática de modelo por Periodização;
- escolha de RAG/preset;
- instruções livres;
- preview do rascunho;
- edição antes de salvar;
- visualização da fase de progressão e motivo da decisão, se fizer sentido.

---

# 13. HISTÓRICO E COMPATIBILIDADE

Não quero quebrar histórico antigo.

Planeje como manter leitura de:

- planos `plan_type='abc'`;
- `split_day`;
- templates antigos;
- `template_type='abc'|'full_body'`;
- planos já concluídos;
- planos planejados antes da migração;
- `raw_payload` antigo;
- dashboards que mostram dados históricos.

Diferencie:

- campos legados que continuam legíveis;
- campos que deixam de ser gravados;
- campos que podem ser migrados;
- código que pode ser apagado somente depois de uma janela de compatibilidade.

---

# 14. TESTES OBRIGATÓRIOS

Proponha uma suíte automatizada.

## Progressão

Crie matriz de cenários cobrindo todas as fases e bordas:

- F1;
- F2;
- F3;
- F4;
- F5;
- F6;
- RPE 10 apenas na última;
- RPE 10 antes da última;
- equipamento/exercício com RPE 10 permitido;
- equipamento/exercício com RPE 10 proibido e última série em RPE 9 → deve poder satisfazer o critério de intensidade;
- RPE 10 proibido e usuário registra RPE 10 mesmo assim → não transformar isso em meta futura;
- plano legado com `rpe_target=10` em equipamento incompatível;
- `users.is_advanced_user=true` em equipamento incompatível → restrição de segurança continua valendo;
- ausência de RPE;
- dor muscular;
- dor articular;
- READY;
- REDUCED_DOSE;
- NO_DIRECT_WORK;
- exercício novo;
- troca de equipamento;
- técnica já existente;
- técnica candidata compatível com equipamento;
- técnica candidata incompatível com equipamento;
- técnica mecanicamente compatível mas que exige falha em equipamento com falha proibida;
- nenhuma técnica elegível na Fase 4;
- 4 séries já existentes;
- janela < 7 dias;
- janela > 7 dias;
- janela < 14 dias;
- janela >= 14 dias;
- treino sem plano;
- técnica avançada gravada como set_type.

## Geração por grupos

Cobrir:

- slot automático;
- slot explícito;
- ativação PRIMARY;
- SECONDARY;
- STABILIZER;
- déficit;
- excesso;
- empate de estrelas;
- recência;
- equipamento preferido por estrelas, mas incompatível com a técnica requerida → não pode vencer o ranking;
- equipamento menos preferido, mas único compatível com a técnica requerida;
- técnica avançada filtrada pela matriz de compatibilidade;
- equipamento com RPE 10 proibido gerando alvo seguro;
- academia sem equipamento;
- múltiplos equipamentos;
- fadiga compartilhada;
- recovery reduzido;
- recovery bloqueado;
- progressão retornando mudança de carga/reps.

## Ficha pronta

Cobrir:

- preservação integral dos parâmetros;
- equipamento ausente;
- técnica fixa;
- técnica fixa incompatível com o equipamento;
- técnica fixa que exige falha em equipamento com RPE 10 proibido;
- `rpe_target=10` fixo em equipamento com falha proibida;
- conflito técnica fixa × progressão;
- round-trip API/frontend de todos os campos avançados.

## Periodização

Cobrir:

- JSON válido;
- JSON inválido;
- slot inválido;
- grupo inexistente;
- equipamento inexistente;
- percentuais inválidos;
- meta incompatível;
- RAG inexistente;
- timeout OpenAI;
- usuário edita antes de salvar;
- nenhuma gravação automática antes de confirmação.

## Compatibilidade

Cobrir leitura de planos/templates legados.

---

# 15. OBSERVABILIDADE E AUDITORIA

Proponha logs/métricas úteis.

Quero conseguir responder:

- por que o motor escolheu este equipamento?
- quais equipamentos foram descartados por incompatibilidade de técnica?
- o equipamento permite RPE 10/falha?
- por que o RPE foi limitado/capado?
- qual regra/override de segurança foi aplicada?
- por que entrou na Fase 4?
- qual histórico sustentou a decisão?
- por que progressão foi bloqueada?
- qual readiness foi usado?
- qual versão do algoritmo tomou a decisão?
- por que a ativação ficou abaixo/acima da meta?
- a ficha foi gerada por qual slot/modelo?
- houve fallback?
- uma LLM participou da geração diária? Idealmente não.

Evite logar dados sensíveis desnecessários.

---

# 16. ORDEM DE EXECUÇÃO DA REFATORAÇÃO

Proponha uma sequência que reduza risco.

Exemplo de formato esperado:

1. consolidar contratos e testes de caracterização;
2. criar schema novo;
3. implementar preferências e capacidades de segurança/compatibilidade de equipamentos;
4. implementar novo motor de progressão atrás de feature flag;
5. adaptar geração por modelo;
6. remover IA da seleção diária;
7. implementar periodização;
8. migrar frontend;
9. migrar dados;
10. desativar ABC/Full Body legados;
11. remover código morto;
12. habilitar em produção;
13. observar métricas;
14. remover compatibilidade temporária.

Não copie essa ordem automaticamente. Revise e proponha a melhor.

Para cada etapa:

- arquivos;
- banco;
- API;
- frontend;
- testes;
- dependências;
- risco;
- critério de aceite;
- rollback.

---

# 17. FORMATO OBRIGATÓRIO DA SUA RESPOSTA

Entregue um documento técnico com estas seções, nesta ordem:

## 1. Resumo executivo

Explique em poucas linhas qual é a refatoração real e quais são os maiores riscos.

## 2. Mapa do estado atual

Liste os componentes existentes que participam da progressão, recuperação, geração, templates, equipamentos e IA.

Inclua arquivos/funções/endpoints/tabelas.

## 3. Gaps e inconsistências encontradas

Separe em:

- requisito ambíguo;
- comportamento atual incompatível;
- dado inexistente;
- contrato duplicado;
- risco de migração;
- bug atual descoberto.

Classifique cada item:

- `BLOCKER`;
- `DECISION`;
- `IMPORTANT`;
- `CLEANUP`.

## 4. Arquitetura alvo

Mostre responsabilidades e fluxo entre motores.

Inclua um diagrama Mermaid.

## 5. Novo Motor de Progressão

Defina:

- entradas;
- estado;
- algoritmo;
- máquina de fases;
- persistência;
- saída;
- tratamento de dor/recovery;
- técnicas;
- eventos;
- pseudocódigo.

Inclua uma tabela completa Fase → pré-condições → ação → transição → bloqueios.

## 6. Novo Motor de Geração de Ficha

Separar:

- Modelo por Grupos Musculares;
- Modelo por Ficha Pronta.

Detalhar algoritmo determinístico.

## 7. Novo Motor de Periodização

Detalhar:

- fluxo;
- prompt;
- RAG;
- contrato;
- validação;
- async;
- UI;
- persistência.

## 8. Modelo de dados e migrações

Tabela por tabela.

Inclua DDL conceitual ou esquema proposto, sem executar.

## 9. API

Endpoint por endpoint.

Inclua requests/responses exemplificativos.

## 10. Frontend

Tela/componente/tipo por tela.

## 11. Código legado a remover ou desativar

Liste arquivos, branches, constantes, opções de UI, prompts e fallbacks.

Não diga apenas “remover ABC”; diga exatamente onde.

## 12. Compatibilidade e migração de dados

Explique como preservar histórico e planos existentes.

## 13. Plano de testes

Backend, frontend e integração.

## 14. Observabilidade

Logs, métricas, auditoria.

## 15. Plano de implementação por PRs

Quero uma sequência de PRs pequenos.

Para cada PR:

- objetivo;
- arquivos principais;
- schema;
- API;
- frontend;
- testes;
- dependência;
- rollback;
- critério de aceite.

## 16. Decisões que precisam de confirmação

Somente as decisões realmente abertas.

Para cada uma:

- pergunta;
- opções;
- recomendação;
- consequência de cada opção.

## 17. Checklist final de completude

Confirme explicitamente que você verificou:

- progressão;
- recuperação;
- 21 treinos;
- dor;
- RPE;
- técnicas avançadas;
- catálogo/códigos canônicos de técnicas;
- compatibilidade técnica ↔ equipamento/exercício;
- RPE 10/falha permitida por equipamento/exercício;
- normalização do critério de progressão quando falha é proibida;
- impacto em `users.is_advanced_user`;
- estrelas;
- variabilidade;
- `stimulus_weight`;
- `fatigue_weight`;
- metas de ativação;
- slots;
- geração por grupos;
- ficha pronta;
- periodização;
- OpenAI;
- RAG;
- banco;
- API Gateway;
- Lambdas;
- frontend;
- dashboards;
- histórico;
- migrações;
- testes;
- observabilidade;
- código ABC/Full Body legado;
- todos os call sites de `plan_type` e `split_day`;
- todos os call sites de `suggest_load_and_reps`;
- todos os pontos que recalculam progressão durante leitura;
- todos os lugares que salvam/descartam campos avançados;
- todos os lugares que prescrevem, alteram, avaliam ou exibem RPE;
- todos os lugares que selecionam ou validam técnicas avançadas;
- todos os lugares que recomendam/substituem equipamentos;
- todos os lugares que usam `users.is_advanced_user` para relaxar regra de RPE/falha;
- todos os payloads de recomendação (`RecommendItem`, `GymEquipmentRecommendItem` e equivalentes) e consumidores internos que precisarão transportar estrelas + capacidades de segurança.

---

# 18. RESTRIÇÕES

- Não implemente código.
- Não altere arquivos.
- Não proponha “reescrever tudo”.
- Não use LLM para uma decisão que pode ser uma regra determinística clara.
- Não mantenha lógica ABC/PPL/Full Body escondida em fallback.
- Não crie schema sem estratégia de migração.
- Não dependa apenas de `MUSCLE_FIT.md`; confirme no repositório.
- Não silencie ambiguidades.
- Não introduza nova duplicação de regra entre backend, frontend e prompts.
- Não deixe progressão ser decidida simultaneamente por algoritmo determinístico e LLM.
- Não deixe uma ficha salva mudar silenciosamente apenas porque foi lida novamente.
- Não considere técnica avançada “mais ativação” sem definir como isso entra no cálculo.
- Não use preferência por estrelas sem persistência por usuário.
- Não permita que estrelas/preferência sobreponham restrições de segurança do equipamento.
- Não proponha técnica avançada sem validar compatibilidade com o exercício/equipamento.
- Não prescreva RPE 10/falha quando a combinação estiver marcada como incompatível.
- Não considere ausência de RPE 10 como falha de progressão quando RPE 10 estiver proibido.
- Não deixe `is_advanced_user` ignorar uma restrição explícita de segurança do equipamento.
- Não confie em prompt de LLM para aplicar essas restrições: qualquer saída de `exercise_set_evaluation_ai` ou outra IA que sugira RPE/carga/técnica deve passar por validação determinística server-side antes de chegar ao usuário.
- Não presuma presença de spotter, safety arms, gaiola ou outro mecanismo externo de proteção se essa condição não estiver explicitamente modelada.
- Não remova suporte de leitura ao histórico legado sem plano explícito.

---

# 19. CRITÉRIO DE QUALIDADE

Vou considerar a análise boa se, ao final, eu conseguir transformar sua resposta em issues/PRs sem precisar descobrir depois que:

- havia outro caminho de geração usando ABC;
- outro endpoint ainda aplicava a progressão antiga;
- a UI descartava campos avançados;
- o recomendador não tinha onde salvar estrelas;
- não existia onde salvar/consultar técnicas compatíveis por equipamento;
- uma técnica que exige falha era sugerida num equipamento que proíbe RPE 10;
- o usuário era considerado abaixo da meta porque não atingiu uma falha que o sistema proibiu;
- algum caminho de IA/analytics ainda sugeria ou premiava RPE 10 em equipamento inseguro;
- o recovery projetado apagava a dor;
- o motor de ativação não contabilizava a técnica usada para “corrigir” déficit;
- um plano salvo mudava ao ser relido;
- os dados dos 21 treinos não distinguiam série de trabalho de técnica/aquecimento;
- a migração quebrava planos históricos;
- ou a LLM ainda decidia a divisão fora do Motor de Periodização.

Faça uma análise de repositório, não uma resposta conceitual.
