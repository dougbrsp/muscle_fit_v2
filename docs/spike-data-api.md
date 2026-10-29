# Spike: shim PyMySQL -> RDS Data API

> Executado em 2026-09-03 contra o cluster real `musclefit-db` (`sa-east-1`),
> já com os dados da Fase 1 migrados. Código do spike em
> `spikes/data-api-shim/` (`rds_data_shim.py` + `test_spike.py`).

## Resultado direto

**7/7 testes passaram contra o Aurora real.** Um shim central em `db.py`
**é viável** para a maioria dos call-sites do backend de referência — com
duas ressalvas importantes detalhadas abaixo (função `%%`/`%s` e a lista de
7 arquivos que já abrem conexão própria, fora de `db.py`).

## O que foi testado

`test_spike.py` cobre 7 cenários, escolhidos para representar a
complexidade real de `services.py` (recuperação muscular) e `analytics.py`
(volume/analytics) do backend de referência:

| # | Cenário | Padrão de referência | Resultado |
|---|---|---|---|
| 1 | `SELECT` simples com `WHERE` + 1 parâmetro | `services.py:source_id` (linhas 71-76) | OK |
| 2 | `SELECT` com 2 `LEFT JOIN`, `GROUP BY`, `DATE_FORMAT(..., '%%Y-%%m-%%d')`, `LIKE '%%muscul%%'` | `analytics.py:compute_training_trend`/`compute_rolling_weekly_trend` (linhas 212-238, 265-291) | OK |
| 3 | `INSERT` com retorno de id via `lastrowid` (2 inserts sequenciais, valida auto_increment) | `services.py:source_id` (linhas 78-82) | OK |
| 4 | `UPDATE` + `rowcount` | padrão comum em updates parciais (`PUT /exercises/logs/{id}` etc.) | OK |
| 5a | Transação com 2 `INSERT`s relacionados (padrão activity→exercise) + commit real, validado em conexão separada | equivalente a `transaction()` em `db.py` | OK |
| 5b | Transação com exceção no meio → rollback, validado que nada persiste | equivalente ao `except: conn.rollback()` de `db.py` | OK |
| 6 | Parâmetro >65536 chars via `parameters` tipados (SQL literal pequeno) | mitigação já usada na migração de dados da Fase 1 para `context_snapshot`/`recommendation_markdown` | OK |

Escrita ficou **inteiramente restrita** a uma tabela dedicada
(`spike_test_table`), criada e derrubada pelo próprio script — nenhuma
tabela real (`activities`, `exercises`, `exercise_sets`, `sources`) sofreu
`INSERT`/`UPDATE`/`DELETE`. Confirmado ao final que a tabela de teste não
sobrou no cluster (`SHOW TABLES LIKE 'spike%'` retornou vazio).

## O que funcionou como drop-in

- **Interface de cursor**: `cursor.execute(sql, params)`, `fetchone()`,
  `fetchall()` retornando `dict` (equivalente ao `DictCursor`), `lastrowid`,
  `rowcount` — tudo replicável fielmente por cima do Data API, sem exigir
  mudança de assinatura nos call-sites que só usam esse subconjunto.
- **JOINs, agregações, `GROUP BY`, funções de data (`DATE_FORMAT`,
  `DATE_SUB`, `CURDATE`)** — nenhuma limitação encontrada; o Data API só
  encaminha o SQL para o motor Aurora MySQL por baixo, então qualquer SQL
  válido para o MySQL roda igual.
- **Parâmetros tipados automáticos**: `str`, `int`, `float`, `bool`, `None`,
  `Decimal`, `datetime`/`date`, `bytes` — mapeamento direto para
  `stringValue`/`longValue`/`doubleValue`/`booleanValue`/`isNull`/`blobValue`
  + `typeHint` (`DECIMAL`, `DATE`, `TIMESTAMP`) quando necessário.
- **Transação real por "conexão lógica"**: `begin_transaction` no
  construtor, `commit_transaction`/`rollback_transaction` em `commit()`/
  `rollback()` — reproduz o padrão já validado na migração de dados da
  Fase 1 (necessário porque cada `execute_statement` isolado, sem
  transação, não compartilha estado entre chamadas).

## O que exigiu adaptação (achado novo deste spike)

**Tradução `%s` -> `:p0` não é 1:1 — precisa desfazer o escape `%%` do
PyMySQL.** Este é o achado principal, não coberto pelos dois problemas já
conhecidos da migração de dados. O backend de referência usa `%%` como
escape de `%` literal dentro do SQL sempre que a query tem parâmetros —
exemplos reais achados em `analytics.py`:

```python
# analytics.py:88, 145-151, 229-232 etc.
"DATE_FORMAT(a.local_date, '%%Y-%%m-%%d')"
"LOWER(a.activity_type) LIKE '%%muscul%%'"
```

Isso existe porque o PyMySQL faz a substituição de parâmetros via
`%`-formatting estilo Python (paramstyle `"format"`), então qualquer `%`
literal no SQL precisa estar duplicado para sobreviver a esse passo interno
do driver. **O Data API não tem esse conceito** — usa parâmetros nomeados
de verdade (`:p0`), sem passar o SQL por nenhuma substituição `%`-style.
Se o shim só trocasse `%s` por `:pN` e deixasse o resto do texto intacto,
todo `DATE_FORMAT`/`LIKE` com `%%` viraria SQL inválido no Aurora (`%%Y`
não é um formato válido de `DATE_FORMAT`).

`_translate_sql` no shim resolve isso fazendo as duas transformações no
mesmo passe: `%%` → `%` (desfaz o escape do PyMySQL) e `%s` → `:pN`
sequencial (não confundindo um `%s` real com o `s` de dentro de um `%%`
adjacente). Testado explicitamente no cenário 2 acima — passou.

**Efeito prático**: nenhuma reescrita de SQL é necessária nos call-sites —
o texto das queries (com `%%`) pode ser copiado exatamente como está hoje.
A adaptação fica inteiramente contida no shim, não vaza para os ~30
arquivos que usam esse padrão.

**Detalhe secundário confirmado na prática**: colunas `TEXT` (limite de
65.535 bytes) truncam/rejeitam antes mesmo do parâmetro grande chegar perto
do limite de 65.536 caracteres do *texto do statement* do Data API — dois
limites numericamente próximos mas de origens diferentes (um é do tipo de
coluna MySQL, o outro é do protocolo do Data API). Isso não é uma limitação
do shim; é preciso ter isso em mente ao migrar colunas hoje `TEXT` que
recebem payloads grandes (ex.: JSON), preferindo `MEDIUMTEXT`/`LONGTEXT`
como já é o caso de `cardio_recommendations.context_snapshot` no schema
migrado.

## Limitações conhecidas (já documentadas na migração de dados da Fase 1 — não redescobertas aqui, só reafirmadas)

- **Limite de 65.536 caracteres de texto SQL literal** por
  `execute_statement`. Mitigação: parâmetros tipados (`parameters`) em vez
  de SQL literal para valores grandes — o shim já usa esse caminho por
  padrão (todo parâmetro sempre vai via `parameters`, nunca é interpolado
  no texto do SQL), então esse limite só volta a ser um risco se algum
  call-site montar uma cláusula `IN (...)` gigante via f-string em vez de
  parâmetros — não observado no backend de referência.
- **`SET`/estado de sessão não persiste fora de uma transação explícita**
  do Data API. Mitigação: o shim sempre abre uma transação real
  (`begin_transaction`) no construtor da "conexão lógica" e só a
  fecha em `commit()`/`rollback()`/`close()` — qualquer `SET
  FOREIGN_KEY_CHECKS=0` ou similar dentro do mesmo `with transaction():`
  funciona; fora disso, não.

## Não coberto por este spike (fora de escopo, ver docstring de `rds_data_shim.py`)

- `cursor.executemany()` — não usado no backend de referência (confirmado
  por grep).
- Streaming de result sets grandes — Data API sempre materializa a resposta
  inteira; aceitável no volume deste projeto (uso pessoal/pequena escala).
- Qualquer padrão de acesso a dados fora do subconjunto
  `execute`/`fetchone`/`fetchall`/`lastrowid`/`rowcount`/`commit`/`rollback`.

## O achado que já estava previsto no plano: 7 arquivos fora de `db.py`

Confirmado por grep antes do spike (não é uma descoberta nova, mas registro
formal aqui): pelo menos 7 arquivos do backend de referência abrem conexão
própria via `pymysql.connect(**_db_config())`, **ignorando** o módulo
central `db.py`:

- `analytics.py`
- `exercise_catalog_admin.py`
- `exercise_mapping_canonical.py`
- `exercise_mapping_catalog.py`
- `gyms.py`
- `muscle_group_equipment.py`
- `intervals_integration.py`

Um shim central em `db.py` **não cobre esses 7 arquivos automaticamente** —
cada um precisa ser ajustado manualmente para importar de `db.py` (ou
receber o mesmo tratamento de shim isoladamente). Isso não invalida a
viabilidade do shim central; apenas define que o trabalho de porte real
tem duas frentes: (1) trocar o *conteúdo* de `db.py` pelo shim — baixo
esforço, interface já provada aqui — e (2) fazer esses 7 arquivos passarem
a importar de `db.py` em vez de abrir conexão própria — esforço pequeno
mas manual, arquivo por arquivo.

## Estimativa de esforço para portar o backend inteiro

| Etapa | Esforço estimado |
|---|---|
| Promover `rds_data_shim.py` de spike para `app/db.py` real (adaptar para ler `resourceArn`/`secretArn`/`database` de env vars, mantendo a assinatura atual `get_conn()`/`transaction()` sem argumentos — os call-sites não mudam) | 0,5 dia |
| Ajustar os 7 arquivos que abrem conexão própria para importar de `db.py` | 0,5-1 dia (mecânico, mas precisa rodar os endpoints afetados um a um) |
| Rodar smoke test nos ~40 endpoints do backend contra o shim (Fase 3 do plano já prevê isso) | já orçado na Fase 3, sem custo adicional por causa deste spike |
| Ajustes pontuais esperados (queries com padrões não cobertos por este spike, ex. algum `executemany` esquecido, alguma `IN (...)` montada via f-string) | folga de 0,5-1 dia, contingência |

**Total estimado**: 1,5-2,5 dias — bem dentro do orçamento de 3-5 dias já
previsto na Fase 2 do plano para este spike (que incluía tanto a validação
quanto a decisão go/no-go).

## Recomendação final

**Shim central em `db.py` é viável — recomendado seguir com essa
abordagem**, sem necessidade de reescrever os call-sites individualmente.
A interface usada de fato pelo backend (`execute`/`fetchone`/`fetchall`/
`lastrowid`/`rowcount`/`commit`/`rollback`/context manager de cursor e de
`transaction()`) foi replicada fielmente e validada com queries de
complexidade real (múltiplos JOINs, agregação, `GROUP BY`, `DATE_FORMAT`
com escape `%%`, parâmetros tipados incluindo `Decimal`/`datetime`/`None`,
transação com commit e com rollback) contra o cluster de produção migrado.

A única reescrita necessária é fora de `db.py`: os 7 arquivos que abrem
conexão própria precisam passar a usar `db.py` — trabalho mecânico, não um
motivo para abandonar a abordagem de shim central.

Não há indício, neste spike, de que a paridade exigiria reescrita
excessiva de SQL nos call-sites — portanto o critério do plano para cair no
"Plano B" (reescrever com `boto3 rds-data` direto, sem shim genérico) **não
se aplica**.
