# onprem-sync — espelhamento de dados onpremise → Aurora

Ferramenta versionada e replicável que **espelha exatamente** o MySQL
onpremise de produção (`fitness`, container `fitness-mysql`) no Aurora MySQL
da AWS (`musclefit`, cluster `musclefit-db`).

O fluxo é **unidirecional**: o onpremise é fonte de verdade absoluta e
**nunca** é modificado. Em conflito de conteúdo, o onprem sempre vence.
Linhas que existem só no Aurora são **removidas** — é um espelho, não um
merge. Os **IDs do onprem são preservados**: o id do onprem *é* o id do
Aurora, o que mantém todas as FKs válidas por construção e dispensa qualquer
tabela de correlação.

---

## Sumário

- [Pré-requisitos](#pré-requisitos)
- [Como rodar](#como-rodar)
- [Tabelas excluídas do sync](#tabelas-excluídas-do-sync)
- [`users` É sincronizada — leia isto](#users-é-sincronizada--leia-isto)
- [Backup e restauração](#backup-e-restauração)
- [Como a ferramenta funciona](#como-a-ferramenta-funciona)
- [Verificações que o relatório final apresenta](#verificações-que-o-relatório-final-apresenta)
- [Auto-teste](#auto-teste)
- [Estrutura dos arquivos](#estrutura-dos-arquivos)
- [Limitações e riscos conhecidos](#limitações-e-riscos-conhecidos)

---

## Pré-requisitos

1. **Container onpremise em execução.** `docker ps` precisa listar
   `fitness-mysql`. A ferramenta lê via `docker exec`.
   **Nunca** use `docker compose` para subir isso: o compose recria os
   containers de produção. Se estiver parado, use os scripts do repositório
   onpremise.
2. **Sessão AWS válida**, **no perfil certo**. Esta máquina **não tem perfil
   `default`** — usar `boto3` sem apontar perfil falha com
   `Unable to locate credentials` mesmo com o login perfeitamente válido.

   O default da ferramenta é **`musclefit-tf`**, e isso é deliberado: o perfil
   `musclefit` guarda a sessão via provider `login_session`, que exige a
   dependência extra `botocore[crt]`. O `musclefit-tf` lê a **mesma sessão**
   por `credential_process` (delegando ao AWS CLI, que já traz o CRT), sem
   instalar nada.

   Precedência: `--aws-profile` > `AWS_PROFILE` > `aurora.profile` do config.

   Permissões necessárias:
   - `rds-data:ExecuteStatement`, `rds-data:BatchExecuteStatement`,
     `rds-data:BeginTransaction`, `rds-data:CommitTransaction`,
     `rds-data:RollbackTransaction`;
   - `secretsmanager:GetSecretValue` no secret master do cluster;
   - `rds:DescribeDBClusters` (só para descobrir o ARN do secret; dá para
     evitar preenchendo `aurora.secret_arn` em `sync_config.json`).
3. **Python 3.11+** com `boto3`. Nenhuma outra dependência.
   (`pymysql` é usado **apenas** pelo auto-teste, nunca pela ferramenta.)
4. **Schema em paridade.** O Aurora precisa estar com as mesmas migrations do
   onprem. A ferramenta sincroniza *dados*, não schema — e aborta no preflight
   se faltar tabela ou coluna.

> Não existe conexão TCP direta com o Aurora a partir da máquina do
> desenvolvedor: o cluster está em subnets privadas, sem NAT/IGW, sem
> bastion e sem VPN. O **RDS Data API é o único caminho de escrita**.

---

## Como rodar

Sempre a partir de `tools/onprem-sync/`.

### 1. Dry-run (padrão — não escreve nada)

```bash
python sync.py
```

Extrai o onprem, lê o Aurora, compara e imprime o plano completo: por tabela,
quantos INSERT, UPDATE e DELETE seriam feitos, quais `AUTO_INCREMENT`
precisam de realinhamento e quais colunas mais divergem. **Este é o modo
padrão**; `--apply` é a única forma de escrever.

### 2. Inventário sem tocar na AWS

```bash
python sync.py --onprem-only
```

Mostra escopo, ordem topológica derivada das FKs e prévia dos statements.
Não usa nenhuma credencial AWS — útil quando a sessão está expirada.

### 3. Aplicar

```bash
python sync.py --apply
```

Sequência obrigatória, nesta ordem:

1. preflight de paridade de schema;
2. leitura dos dois lados e cálculo do plano;
3. **backup completo do estado atual do Aurora** em `backups/<timestamp>/`
   (se o backup falhar, o `--apply` aborta — sem exceção);
4. **confirmação interativa digitada**: é preciso digitar exatamente
   `ESPELHAR musclefit`. Uma flag não basta, e sem terminal interativo o
   comando aborta;
5. escrita dentro de **uma única transação** do Data API, com rollback total
   em caso de erro;
6. realinhamento de `AUTO_INCREMENT` (fora da transação — `ALTER TABLE` é DDL
   e provoca commit implícito);
7. verificação pós-sync e relatório tabela a tabela.

### 4. Restringir a um subconjunto (retomar de falha)

```bash
python sync.py --tables activities,exercises,exercise_sets
python sync.py --tables activities,exercises,exercise_sets --apply
```

### 5. Só backup

```bash
python sync.py --backup-only
```

### Outras opções

| Flag | Efeito |
|---|---|
| `--config PATH` | usa outro `sync_config.json` |
| `--backup-dir PATH` | muda a raiz dos backups (padrão: `./backups`) |
| `--restore-from DIR` | restaura um backup no Aurora |
| `--skip-verify` | pula a verificação pós-sync (**não recomendado**) |
| `--aws-profile NOME` | perfil do AWS CLI; precede `AWS_PROFILE` e o config |

**Código de saída:** `0` sucesso, `1` erro, `2` sync aplicado mas com
divergência na verificação final, `3` sessão AWS ausente ou expirada
(neste caso nada é lido nem escrito no Aurora, e o onpremise não é tocado —
use `--onprem-only` enquanto não autenticar).

---

## Tabelas excluídas do sync

Configuráveis em `sync_config.json` → `excluded_tables`. Defaults:

| Tabela | Por que está fora |
|---|---|
| `schema_migrations` | Controle de migração **por instância**. Replicar sobrescreveria o histórico de migrations do Aurora com o do onprem, quebrando o rastreio de qual instância aplicou o quê. |
| `exercise_mappings` | Tabela legada e vazia no onprem. Não há o que espelhar. |
| `exercise_mappings_backup_20260714` | Backup ad-hoc criado à mão. Lixo operacional, não dado de produto. **Removida da Aurora pela migração 030** (2026-09-14); continua na lista porque ainda existe no onprem — sem a entrada, entraria no escopo e o preflight abortaria com "Tabelas ausentes no Aurora". |
| `exercises_backup_before_mapping_sync` | Idem. |
| `user_sessions` | Tokens de sessão. Espelhar derrubaria/renovaria sessões ativas do app AWS sem nenhum benefício. **Excluída por default, mas configurável** — basta removê-la do arquivo para passar a sincronizar. |

Tudo o mais entra: **41 tabelas, 5.088 linhas** na medição atual.

### Tabelas legadas (sincronizadas, com data para sair)

`sync_config.json` → `legacy_tables` lista tabelas que **continuam no
escopo** (diferente de `excluded_tables`), mas que estão marcadas para
remoção. O sync apenas as sinaliza no relatório (`! LEGADA (...)`) e **não
avisa** quando elas deixam de existir no schema, para que o `DROP` futuro
não dependa de mexer no sync antes. Uma mesma tabela em `legacy_tables` e
`excluded_tables` é recusada no carregamento da config.

| Tabela | Situação |
|---|---|
| `equipment_exercise_mappings` | Espelho legado de `equipment_exercises` (canônica desde o PR #56, que também é sincronizada). Continua no sync só enquanto a API espelha escritas nela e os endpoints `/equipment-exercise-mappings` existem. Sai na **fase 2** — ver `docs/refatoracao-motores/PLANO_20260912.md`, seção "Remoção do legado de vínculos equipamento ↔ exercício". |

---

## `users` É sincronizada — leia isto

A tabela `users` **faz parte do sync**, incluindo a coluna de **hash de
senha** e o `role`.

Consequência prática: **depois de um `--apply`, as credenciais de login do
app na AWS passam a ser as credenciais do onpremise.** Qualquer usuário
criado só na AWS é **apagado** (regra de espelho exato), e qualquer troca de
senha feita só na AWS é revertida para o hash do onprem.

Se isso não for desejado num cenário específico, adicione `users` a
`excluded_tables` — mas atenção: sem `users`, as FKs `user_id` das demais
tabelas podem apontar para usuários inexistentes no Aurora, e a verificação
de integridade referencial vai acusar.

---

## Backup e restauração

Todo `--apply` gera antes um snapshot em `backups/<timestamp-UTC>/`:

```
backups/20260912T143055Z/
├── manifest.json        metadados, escopo e contagem por tabela
├── data/<tabela>.json    linhas na forma canônica  <-- fonte oficial de restauração
└── restore.sql           plano B legível (DELETE + INSERT por tabela)
```

### Restaurar

```bash
python sync.py --restore-from backups/20260912T143055Z
```

Calcula o diff entre o backup e o estado atual do Aurora, imprime o plano,
pede confirmação digitada (`RESTAURAR musclefit`) e aplica pelo **mesmo
caminho transacional** do sync. É o caminho oficial, porque daqui não existe
conexão TCP com o cluster.

O `restore.sql` existe para quem tiver acesso direto ao banco (bastion,
DBeaver) e quiser conferir ou aplicar à mão. Ele **não** é aplicável via Data
API sem um executor de script.

> Os diretórios de backup **não são versionados** (ver `.gitignore`): contêm
> dados pessoais reais, incluindo hashes de senha.

---

## Como a ferramenta funciona

### Forma canônica

O maior risco de uma comparação entre dois motores diferentes (MySQL 8.4
onprem × Aurora MySQL 8.0) é o **falso positivo**: uma diferença de
formatação que faz o sync "atualizar" para sempre a mesma linha e nunca
convergir. A solução aqui é ler os **dois lados com a mesma lista de
expressões SQL**, convertendo todo valor para texto (`CAST(col AS CHAR)`), e
comparar em Python. Isso elimina diferença de renderização de `DECIMAL`,
diferença de tipagem entre drivers e diferença de fração de segundo.

Duas armadilhas sobraram e são tratadas explicitamente:

- **`TIMESTAMP` depende do `time_zone` da sessão.** Sem fixar o mesmo fuso
  dos dois lados, o mesmo instante vira string diferente e *toda* coluna
  `TIMESTAMP` aparece como divergência. A ferramenta fixa `+00:00` em ambos
  e **verifica a variável de volta** antes de prosseguir.
- **`JSON` é re-canonicalizado pelo servidor ao gravar**, e a forma canônica
  pode variar entre versões de engine. Colunas JSON passam por parse +
  serialização com chaves ordenadas em Python antes de comparar.

### Ordem de operações

A ordem topológica é **derivada em runtime** de
`information_schema.KEY_COLUMN_USAGE` (não é lista fixa), então acompanha a
evolução do schema. O programa faz uma checagem dura: para cada uma das 56
FKs, o pai precisa vir antes do filho, senão aborta antes de ler qualquer
dado.

- **DELETE** em ordem topológica **inversa** (filhos antes de pais);
- **UPDATE** e **INSERT** em ordem **direta** (pais antes de filhos), com
  UPDATE antes de INSERT dentro de cada tabela, para liberar chaves únicas
  que tenham "andado" de linha.

`FOREIGN_KEY_CHECKS = 0` fica ativo durante a transação. Não é atalho: num
espelho exato existem estados intermediários legítimos que violam FK (um pai
já apagado enquanto o filho ainda não foi). A checagem é **reativada e
verificada** antes do commit, e a integridade é **provada depois**,
explicitamente, consultando cada relação.

### Batching por bytes

O RDS Data API tem limite de ~1 MB por chamada, de ida e de volta. A maior
linha do onprem é `reports.context_snapshot`, com ~316 KB — um lote de
contagem fixa estouraria ali e desperdiçaria capacidade em `exercise_sets`.
Os lotes são dimensionados pelo **tamanho real medido** das linhas.

A leitura é feita em **duas fases**: primeiro só as PKs, ordenadas; depois as
linhas completas em lotes filtrados por essas PKs, **sem `ORDER BY`**. O
`ORDER BY ... LIMIT/OFFSET` ingênuo impede a leitura index-only e força um
filesort que carrega as colunas largas no sort buffer — no auto-teste isso
estourou de verdade com `Out of sort memory`.

### Salvaguardas contra escrita no onprem

O leitor do onpremise **recusa** qualquer statement que não seja
`SELECT`/`SET`/`SHOW`/`USE`. É um cinto de segurança contra alguém evoluir o
código e acidentalmente escrever na origem.

---

## Verificações que o relatório final apresenta

1. **Contagem por tabela**: onprem N linhas × aurora N linhas.
2. **Checksum de conteúdo**: SHA-256 sobre o conteúdo canônico ordenado por
   PK, calculado nos dois lados. Status `OK` ou `DIVERGENTE` por tabela.
   (Não se usa `CHECKSUM TABLE`: ele depende do formato físico do storage
   engine e não é comparável entre MySQL 8.4 e Aurora MySQL 8.0. O hash aqui
   é sobre o mesmo texto que alimenta o diff — "checksum igual" significa
   exatamente "o plano da próxima execução será vazio".)
3. **Integridade referencial**: para cada uma das 56 FKs, uma consulta que
   procura órfãs no Aurora. O relatório prova que não há vínculo quebrado.
   O join usa só colunas de id (numéricas), o que o torna imune à divergência
   de collation das tabelas `intervals_*`.
4. **Encoding**, em dois níveis:
   - **agregado**: soma de `OCTET_LENGTH` e `CHAR_LENGTH` de *toda* coluna
     textual de *toda* tabela, comparada entre os dois bancos. Double-encoding
     ("Estação" → "EstaÃ§Ã£o") aumenta os bytes sem mudar os caracteres, então
     esse par detecta corrupção em qualquer linha, não só nas sondas;
   - **sondas nomeadas** (`encoding_probes` no config), comparando `HEX()`
     byte a byte.

> **Nunca valide acentuação pelo texto exibido no terminal.** Use `HEX()` /
> `OCTET_LENGTH()` / `CHAR_LENGTH()`, que é o que a ferramenta faz.

Uma das sondas é deliberadamente uma linha **já corrompida na origem**
(`gyms.id = 11`, gravada como `GaviÃµes`). Um espelho exato precisa
**reproduzir o defeito byte a byte** — se "consertar" ou piorar, não é
espelho.

---

## Auto-teste

```bash
python tests/selftest.py          # sobe, testa e destrói o container
python tests/selftest.py --keep   # mantém o container para inspeção
```

Roda o sync ponta a ponta **sem tocar a AWS e sem tocar produção**. Ele sobe
um container MySQL efêmero de nome próprio (`onprem-sync-selftest-mysql`, via
`docker run` — nunca `docker compose`), carrega nele o schema real extraído
do onprem com `mysqldump --no-data`, e troca o client `rds-data` por um dublê
apoiado em PyMySQL. O dublê reproduz de propósito a característica do Data
API que mais gera bug sutil: **estado de sessão só sobrevive dentro de uma
transação**.

Cobre 8 cenários: carga inicial, **idempotência** (segunda
execução com plano vazio), reconciliação de linha apagada/editada/intrusa,
`AUTO_INCREMENT`, backup + restore, as salvaguardas (recusa de escrita no
onprem, abort com confirmação errada), as tabelas legadas (`legacy_tables`:
continuam no escopo, são sinalizadas, podem sumir do schema sem aviso e não
podem ser excluídas ao mesmo tempo) e as tabelas de backup do onprem
ausentes no destino (migração 030: presentes no onprem, excluídas por
config, o dry-run termina OK).

O auto-teste é a única parte do projeto que usa `pymysql`, e apenas como
dependência de teste.

---

## Estrutura dos arquivos

```
tools/onprem-sync/
├── sync.py                  CLI e orquestração
├── sync_config.json         configuração declarativa (exclusões, sondas, lotes)
├── README.md
├── syncmod/
│   ├── config.py            carga e validação da configuração
│   ├── schema.py            modelo de schema, ordem topológica, forma canônica
│   ├── onprem.py            leitura somente-leitura via docker exec
│   ├── aurora.py            cliente RDS Data API (transação, paginação, retry)
│   ├── planner.py           diff e cálculo do plano
│   ├── applier.py           execução transacional do plano
│   ├── backup.py            snapshot e restauração
│   └── verify.py            contagem, checksum, FK órfã e encoding
└── tests/
    ├── fake_data_api.py     dublê do client rds-data sobre MySQL local
    └── selftest.py          auto-teste ponta a ponta
```

---

## Limitações e riscos conhecidos

- **O `--apply` nunca foi executado contra o Aurora real.** O `--dry-run`
  já rodou contra o cluster de verdade (leitura, preflight de schema e plano
  completo), mas nenhuma escrita foi feita ainda. O caminho de escrita está
  exercitado ponta a ponta pelo auto-teste contra um MySQL local; o
  comportamento do serviço sob escrita real — limites de tamanho de lote,
  cold start do Serverless v2 durante uma transação longa — só se confirma no
  primeiro apply. **Revise o plano do dry-run antes.**
- **A identidade em uso é a root da conta** (`arn:aws:iam::930413537709:root`).
  Funciona, mas é credencial de root escrevendo em produção. Vale trocar por
  um usuário/role dedicado com apenas as permissões listadas acima.
- **Sincroniza dados, não schema.** Se o Aurora estiver fora de paridade de
  migrations, o preflight aborta. Rode as migrations primeiro.
- **Não há travamento da origem.** Se alguém registrar um treino no onprem
  durante o sync, a foto sai ligeiramente inconsistente. Dado o uso
  mono-usuário, o risco é baixo; se importar, rode fora de horário de treino.
- **Chave única que "anda" de linha.** Os DELETEs vêm antes dos UPDATEs e
  INSERTs, o que cobre o caso normal. Um caso patológico — duas linhas
  trocando entre si o valor de uma chave única, ambas permanecendo — falharia
  com `Duplicate entry` e faria rollback total. Nada seria corrompido, mas o
  sync precisaria de intervenção manual.
- **Collation divergente.** `intervals_activities` e `intervals_wellness`
  usam `utf8mb4_unicode_ci` enquanto o
  resto usa `utf8mb4_0900_ai_ci`. Isso não afeta o sync (a comparação é feita
  em Python sobre bytes, e as verificações usam só colunas numéricas), mas
  pode causar `Illegal mix of collations` em consultas do **aplicativo**. O
  preflight avisa se a divergência não for idêntica nos dois lados.
- **`daily_biometrics`, `weekly_summaries`, `activity_hr_zones`,
  `muscle_recovery_coefficients`** estão vazias no onprem. Se houver dado
  nelas só no Aurora, ele **será apagado** — é o comportamento correto de
  espelho, mas vale saber.
