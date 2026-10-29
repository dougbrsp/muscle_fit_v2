# Rotação de segredos: `API_TOKEN` e chave da OpenAI

Runbook manual (SEG-04 / AWS-16, 2026-10-09). Rotação automática do Secrets Manager
**não** é usada: nenhum destes segredos tem Lambda de rotação, e a da
`secret-encryption-key` seria destrutiva (ver o fim). Cadência sugerida: semestral, e
imediatamente diante de suspeita de vazamento (log, print, máquina de dev, chat).

Quem executa é uma pessoa com credencial de administrador da conta, no terminal dela.
Os agentes do projeto não rodam estes comandos (regra: nada manual na AWS por agente).

## Quem lê cada segredo

| Segredo (Secrets Manager) | Env na Lambda | Lambdas que leem |
|---|---|---|
| `musclefit/api-token` | `API_TOKEN_SECRET_ARN` → `API_TOKEN` | `musclefit-api-core`, `musclefit-engines`, `musclefit-workout-ai` |
| `musclefit/openai-api-key` | `OPENAI_API_KEY_SECRET_ARN` → `OPENAI_API_KEY` | `musclefit-api-core`, `musclefit-plan-generator`, `musclefit-cardio-agent`, `musclefit-workout-ai` |

Desde o SEG-04, `plan-generator` e `cardio-agent` não leem mais o `api-token`: a geração
de ficha lê academias e catálogo direto do banco, sem chamar a própria API.

## O valor não passa pelo Terraform (AWS-03, etapa 5)

Desde a etapa 5 do AWS-03 (2026-10-10), `infra/secrets.tf` gerencia **só o container** de
cada segredo (nome, descrição, tags). Não existe `aws_secretsmanager_secret_version` no
repositório: os 3 que nasceram com o placeholder `replace_me` + `ignore_changes` saíram do
state por blocos `removed { lifecycle { destroy = false } }`, sem tocar em nenhuma versão na
AWS. O valor é posto **só** com `put-secret-value`, pelo dono da conta. Consequências:

- um `put-secret-value` não gera diff no plan, e o plan não lê valor nenhum (a role
  `musclefit-gha-plan` não tem `secretsmanager:GetSecretValue` a partir da v3 da
  `musclefit-gha-plan-leitura`);
- não recoloque um `aws_secretsmanager_secret_version` nem um `random_password` para gerar
  valor: a guarda `scripts/test_iam_gha_plan.py` falha, e o motivo está no topo de
  `infra/secrets.tf`;
- as Lambdas continuam recebendo só o ARN do container (`*_SECRET_ARN`) e leem o
  `AWSCURRENT` no cold start.

### Segredo novo ou ambiente recriado (bootstrap, DR)

Se o Terraform criar um container novo (um segredo novo, ou a conta recriada do zero), ele
nasce **sem versão**. Até o `put-secret-value`, o `GetSecretValue` das Lambdas falha,
`load_secrets()` ignora a falha e a env fica vazia: a API responde `500 API_TOKEN não
configurado` nas rotas com token mestre (falha fechada) e a IA responde "não configurada".
Logo depois do apply que cria o container, o dono da conta grava o valor com o mesmo
comando das seções abaixo (`put-secret-value --secret-string file://...`) e força o cold
start.

Para a `musclefit/secret-encryption-key`, um valor **novo** num ambiente recriado não lê os
dados cifrados com a chave antiga (ver o fim deste runbook). Numa recuperação de desastre,
o valor tem de ser o original, guardado fora da conta.

## Como as Lambdas releem o valor

`api/app/secrets_bootstrap.py::load_secrets()` roda **só no cold start** e não sobrescreve
uma env já preenchida. Um ambiente de execução quente continua com o valor antigo até
ser reciclado, o que a AWS faz sem prazo garantido (em geral minutos a horas).

Para todas as Lambdas passarem a usar o valor novo de imediato, force ambientes novos com
um redeploy da mesma imagem:

- preferido: rodar o workflow **Deploy produção** (`.github/workflows/deploy-producao.yml`,
  `workflow_dispatch` na `main`), que faz `update-function-code` das 7 Lambdas;
- alternativa pontual, por Lambda (mantém a imagem atual, sem drift no Terraform):

```bash
FN=musclefit-api-core
URI=$(aws lambda get-function --function-name "$FN" --query 'Code.ImageUri' --output text)
aws lambda update-function-code --function-name "$FN" --image-uri "$URI"
aws lambda wait function-updated-v2 --function-name "$FN"
```

Enquanto isso não acontece, o token antigo **continua aceito** pelos ambientes quentes.
Numa rotação por vazamento, o redeploy faz parte da rotação, não é opcional.

## Rotação do `API_TOKEN` (token mestre)

1. Gerar o valor novo num arquivo temporário, fora do histórico do shell:

   ```bash
   umask 077
   python -c "import secrets; print(secrets.token_urlsafe(32), end='')" > /tmp/api-token.txt
   ```

2. Gravar a nova versão:

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id musclefit/api-token \
     --secret-string file:///tmp/api-token.txt
   ```

   O Secrets Manager move `AWSCURRENT` para a versão nova e deixa a anterior em
   `AWSPREVIOUS` (útil para reverter com `update-secret-version-stage`).

3. Forçar cold start (seção anterior).

4. Verificar, sem imprimir o token:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Token: $(cat /tmp/api-token.txt)" https://api.musclefit.com.br/auth/me   # 200
   curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Token: <token antigo>" https://api.musclefit.com.br/auth/me             # 401
   ```

5. Atualizar quem usa o token mestre e apagar o arquivo: `shred -u /tmp/api-token.txt`.

**Impacto:**

- **App do relógio (Zepp):** o relógio autentica com o **token de dispositivo** (tabela
  `devices`, hash SHA-256), que **não muda** com a rotação. Desde o M40, parear
  (`POST /devices/pair`), listar e revogar (`/devices`) é feito pelo próprio usuário, logado,
  na tela de perfil (Dispositivos): o token mestre não participa. Se a configuração do app do relógio (Side Service ou
  Settings App, código fora deste repositório) guardar o token mestre em vez do de
  dispositivo, ela para de autenticar e precisa ser atualizada.
- **MCP/Telegram (OpenClaw):** o MCP on-premise aponta para a API on-premise, não para a
  AWS (`docs/aws-migration-plan.md`, "Fora de escopo"). Se alguma instância dele, ou
  algum script, foi apontada para `https://api.musclefit.com.br` com o token mestre,
  atualizar o token nela.
- **Scripts:** `scripts/smoke-async-jobs.sh` lê `API_TOKEN` do ambiente de quem roda.
- **Frontend web:** não usa o token mestre (cookie de sessão). Sem impacto.
- **DEV local:** `API_TOKEN` do `.env` é independente de produção. Sem impacto.

## Rotação da chave da OpenAI

Sem janela de indisponibilidade: a chave antiga continua válida até ser revogada no
provedor.

1. No painel da OpenAI (projeto do MuscleFit), criar uma chave nova. Salvar em
   `/tmp/openai-key.txt` (`umask 077`, sem quebra de linha no final).
2. Gravar:

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id musclefit/openai-api-key \
     --secret-string file:///tmp/openai-key.txt
   ```

3. Forçar cold start (seção "Como as Lambdas releem o valor").
4. Verificar com uma geração real: gerar uma ficha no app e conferir o job `done`
   (`GET /jobs/{id}`); nos logs da `musclefit-plan-generator`, nenhum erro de autenticação
   da OpenAI (`401`/`invalid_api_key`).
5. Só então revogar a chave antiga no painel da OpenAI e `shred -u /tmp/openai-key.txt`.
   Revogar antes do passo 3 derruba as gerações nos ambientes ainda quentes.

## `musclefit/secret-encryption-key`: NÃO rotacionar sem plano de recifragem

Esta chave (`api/app/crypto_utils.py`) cifra dados de usuário guardados no banco, por
exemplo a API key do Intervals.icu de cada usuário. Trocar o valor no Secrets Manager
deixa **ilegível** tudo o que foi cifrado com a chave antiga: a sincronização do
Intervals.icu quebra para todos, sem volta, a menos que a versão `AWSPREVIOUS` seja
restaurada.

A rotação só pode acontecer com um plano próprio, em PR: leitura com as duas chaves
(nova e antiga), migração que recifra cada linha, verificação da contagem recifrada e só
então a remoção da chave antiga. Até lá, não rodar `put-secret-value` neste segredo.
