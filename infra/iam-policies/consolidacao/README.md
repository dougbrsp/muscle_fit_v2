# Consolidação das policies da role `musclefit-gha-deploy` (AWS-13)

**APLICADA: passos 0 a 6 do runbook em 2026-10-09; passo 7 (apagar as 9 managed antigas) em 2026-10-29.** A role de deploy não gerencia a si mesma (sem `iam:CreatePolicy`, e `iam:AttachRolePolicy` só vale para `role/musclefit-*-exec`), então a troca foi feita **à mão pelo dono da conta**, seguindo o runbook abaixo, que fica como registro.

## Estado na conta (lido em 2026-10-10, só leitura)

- `list-attached-role-policies` da `musclefit-gha-deploy`: exatamente as 5 `musclefit-gha-deploy-v2-{compute,iam,observabilidade,rede-dados,s3-borda}`, todas na versão `v1`, criadas em 2026-10-10 00:27 UTC (21:27 BRT de 2026-10-09). `list-role-policies`: `[]`.
- Documento `v1` de cada uma é **idêntico** ao JSON versionado aqui (comparação canônica, chaves ordenadas).
- As 9 managed antigas (`fase5-rag`, `fase5-planreadonly`, `fase6-migrations`, `fase7-eventbridge`, `fase8-ses`, `m44-route53`, `cards-cloudfront`, `m69-m70`, `lote1-observabilidade`) ficaram na conta com `AttachmentCount 0` até o passo 7 e foram **apagadas em 2026-10-29** (ver a seção seguinte).

## Estado na conta (lido em 2026-10-29, só leitura)

- Passo 7 aplicado pelo dono da conta em 2026-10-29, depois de confirmar `AttachmentCount 0` nas 9. `aws iam get-policy` das antigas responde `NoSuchEntity`.
- `list-policies --scope Local` tem só 6 policies, todas anexadas (`AttachmentCount 1`): `musclefit-gha-deploy-v2-compute` (v5), `v2-iam` (v4), `v2-observabilidade` (v1), `v2-rede-dados` (v3), `v2-s3-borda` (v2) e `musclefit-gha-plan-leitura` (v5).
- `list-attached-role-policies` da `musclefit-gha-deploy`: as 5 `v2-*`.
- Os JSONs das policies antigas em `infra/iam-policies/*.json` ficam no repositório só como histórico: **nenhum deles existe mais como policy na conta** (as 9 managed foram apagadas no passo 7; as demais nunca foram managed, eram inline removidas no passo 5 ou avulsas que não chegaram a ser criadas; ver "Arquivos" e a lista de PRs abaixo).
- Primeiro `Deploy produção` com a role nova: **verde** em 2026-10-10 (runs [38041882892](https://github.com/dougbrsp/muscle_fit/actions/runs/38041882892) e [38043337951](https://github.com/dougbrsp/muscle_fit/actions/runs/38043337951)), validando leituras e escritas do apply.
- `SimulatePrincipalPolicy` em 2026-10-10: as ações novas (#438, #455, #456) dão `allowed`.

**Como ampliar a role daqui em diante:** editar o JSON `v2-*` do tema, acrescentar a ampliação em `ACOES_NOVAS` de `scripts/iam_consolidacao.py` com o PR de origem, manter `verificar` e os testes verdes, e o dono da conta publica com `aws iam create-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-deploy-v2-<tema> --policy-document file://infra/iam-policies/consolidacao/musclefit-gha-deploy-v2-<tema>.json --set-as-default`. Uma managed policy guarda no máximo **5 versões**: na 6ª, apagar antes a versão não padrão mais antiga (`aws iam delete-policy-version`). Nada de policy avulsa nova.

**Role de plan separada (AWS-03):** o `terraform plan` de PR vai deixar de usar esta role. A role só leitura `musclefit-gha-plan`, o runbook e a restrição da trust desta role a `main`/`environment:production` estão em `../gha-plan/README.md`. Nada ali amplia as policies `v2-*`.

Origem: AWS-13 do relatório da auditoria de 2026-10-08 (arquivo privado) (policies fora do repositório, exceções pontuais acumuladas) e três necessidades de permissão nova, que entram aqui em vez de virar policy própria:

| PR | Ações | Escopo | Documento de origem |
|---|---|---|---|
| #438 (concorrência reservada, AWS-01) | `lambda:PutFunctionConcurrency`, `DeleteFunctionConcurrency`, `GetFunctionConcurrency` | `function:musclefit-*` | statement do próprio #438 |
| #455 (pull autenticado no ECR Public, AWS-12, já mesclado) | `ecr-public:GetAuthorizationToken`; `sts:GetServiceBearerToken` | `*` (as duas não aceitam recurso); a do STS só com `sts:AWSServiceName = ecr-public.amazonaws.com` | `infra/iam-policies/musclefit-gha-deploy-aws12-ecr-public-pull.json` |
| #456 (tags imutáveis no ECR, AWS-14) | `ecr:PutImageTagMutability`; `ecr:DeleteLifecyclePolicy` | `PutImageTagMutability` em `repository/musclefit-{api-core,engines,ai-generators}`; `DeleteLifecyclePolicy` em `repository/musclefit-api-core` e `repository/musclefit-ai-generators` (em `musclefit-engines` a role já tem) | `infra/iam-policies/musclefit-gha-deploy-aws14-ecr-tags-imutaveis.json` (branch do #456) |

Nenhuma outra ampliação: o `verificar` falha se aparecer qualquer tupla fora do retrato que não seja uma dessas (lista `ACOES_NOVAS` do script).

### Ampliações depois da consolidação (nova versão da v2, nunca policy avulsa)

| PR | Ações | Escopo | Policy / versão |
|---|---|---|---|
| #471 (FIN-04, TLS-only e expiração de versões em S3) | `s3:PutBucketPolicy`, `s3:PutLifecycleConfiguration` | `arn:aws:s3:::musclefit-frontend-assets` (as duas); `arn:aws:s3:::musclefit-rag` (só `PutLifecycleConfiguration`; `PutBucketPolicy` ela já tinha) | `musclefit-gha-deploy-v2-s3-borda` **v2** (statements `S3MusclefitFrontendAssets` e `S3MusclefitRag`) |
| #471 (AWS-18, retenção do log de erro do Aurora) | `logs:ListTagsForResource`, `logs:PutRetentionPolicy`, `logs:TagResource` | `arn:aws:logs:sa-east-1:930413537709:log-group:/aws/rds/cluster/musclefit-db/*` | `musclefit-gha-deploy-v2-compute` **v2** (statement novo `LogsMusclefitDb`) |
| alarme root (`feat/alarme-login-root`, `infra/alerta_root.tf`) | `events:DeleteRule`, `DescribeRule`, `ListTagsForResource`, `ListTargetsByRule`, `PutRule`, `PutTargets`, `RemoveTargets`, `TagResource`, `UntagResource` | `arn:aws:events:{us-east-1,us-east-2,us-west-2}:930413537709:rule/musclefit-*` (três ARNs, sem curinga de região; sem `Enable/DisableRule`) | `musclefit-gha-deploy-v2-compute` **v3** (statement novo `EventsMusclefit2`) |
| alarme root (idem) | `iam:PassRole` | `arn:aws:iam::930413537709:role/musclefit-root-alert-forward-exec`, só com `iam:PassedToService = events.amazonaws.com` | `musclefit-gha-deploy-v2-iam` **v2** (statement novo `IamMusclefitRootAlertForwardExecEventsAmazonawsCom`) |
| SEG-05/AWS-08/AWS-10 (`infra/seg05-waf-throttling-api`, `infra/api_gateway_borda.tf`) | `wafv2:AssociateWebACL`, `CreateWebACL`, `DeleteWebACL`, `GetWebACL`, `ListTagsForResource`, `TagResource`, `UntagResource`, `UpdateWebACL` | `arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/musclefit-*/*`; `CreateWebACL`/`UpdateWebACL` também em `regional/managedruleset/*/*` da conta (referência ao `AWSManagedRulesCommonRuleSet`) | `musclefit-gha-deploy-v2-rede-dados` **v2** (statements novos `Wafv2Musclefit` e `Wafv2Managedruleset`) |
| SEG-05, correção pós-apply (`fix/seg05-iam-getwebaclforresource`) | `wafv2:GetWebACLForResource` (sai do `Wafv2Musclefit`) | `arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/*/*` (ARN genérico: ver abaixo) | `musclefit-gha-deploy-v2-rede-dados` **v3** (statement novo `Wafv2GetWebACLForResource`) |
| SEG-05 (idem) | `apigateway:SetWebACL`; `apigateway:GET`, `apigateway:PATCH` | `SetWebACL` em `arn:aws:apigateway:sa-east-1::/restapis/*/stages/*`; `GET`/`PATCH` só em `arn:aws:apigateway:sa-east-1::/account` (role de CloudWatch da conta) | `musclefit-gha-deploy-v2-compute` **v4** (statements novos `ApigatewayAccount` e `ApigatewayStages`) |
| SEG-05 (idem) | `logs:CreateLogGroup`, `DeleteLogGroup`, `ListTagsForResource`, `ListTagsLogGroup`, `PutRetentionPolicy`, `TagResource` | `log-group:/aws/apigateway/musclefit-*` e `log-group:API-Gateway-Execution-Logs_vaercx9o3g/*` | `musclefit-gha-deploy-v2-compute` **v4** (statement novo `LogsMusclefitE1`) |
| SEG-05 (idem) | `iam:PassRole` | `arn:aws:iam::930413537709:role/musclefit-apigw-cloudwatch-exec`, só com `iam:PassedToService = apigateway.amazonaws.com` | `musclefit-gha-deploy-v2-iam` **v3** (statement novo `IamMusclefitApigwCloudwatchExecApigatewayAmazonawsCom`) |
| SEG-05, correção do access log (`fix/seg05-access-log-apigw`) | `iam:UpdateAssumeRolePolicy` | só `arn:aws:iam::930413537709:role/musclefit-apigw-cloudwatch-exec` (a trust perde `aws:SourceAccount`) | `musclefit-gha-deploy-v2-iam` **v4** (statement novo `IamMusclefitApigwCloudwatchExec`) |
| CloudWatch Logs, ARN de log group (`fix/cwlogs-arn-log-group`; lifecycle event da AWS de 2027-04-26) | `logs:DeleteLogGroup` | `arn:aws:logs:sa-east-1:930413537709:log-group:/aws/lambda/musclefit-migrations` (ARN de grupo, sem `:*`; o ARN `:*` fica) | `musclefit-gha-deploy-v2-compute` **v5** (statement `LogsMusclefitEnginesE1` ganha um recurso) |

Sem curinga novo: S3 só nos dois buckets nomeados, logs só nos grupos exportados pelo cluster `musclefit-db`. Nada de `Delete*` (o PR não remove recurso). Tamanhos depois da v2: `s3-borda` 5.430 de 6.144 caracteres (88%), `compute` 4.760 (77%). Simulação (`iam:SimulateCustomPolicy` com os documentos novos, 2026-10-10): as 4 escritas de S3 e as 3 ações de logs (no ARN com e sem `:*`) dão `allowed`, e `PutRetentionPolicy` em outro cluster continua `implicitDeny`. Cada managed policy guarda no máximo 5 versões; depois desta, as duas estão em 2 de 5 (retrato da época; o estado atual de cada uma está nas seções seguintes).

**SEG-05 (WAF, throttling e logs da API), pendente de aval: publicar ANTES da promoção do PR para a `main`.** Sem curinga de conta nem de região; Web ACL só `musclefit-*`; nada de `PutLoggingConfiguration`, `CreateRuleGroup`, `CreateIPSet` ou `PutPermissionPolicy`. A role já cria `musclefit-apigw-cloudwatch-exec` (padrão `musclefit-*-exec`), põe a policy inline nela e cria o alarme `musclefit-waf-api-blocked` (`alarm:musclefit-*`). Tamanhos depois: `compute` 5.856 de 6.144 caracteres (95%: a próxima ampliação de Lambda/ECR/logs talvez precise de uma 6ª policy de tema), `rede-dados` 4.211 (69%), `iam` 1.811 (29%). Versões depois da publicação: `compute` 4 de 5, `iam` 3 de 5, `rede-dados` 2 de 5. Passos (credencial de administrador, Git Bash na raiz, branch do PR):

```bash
ACC=930413537709; DIR=infra/iam-policies/consolidacao
for T in compute iam rede-dados; do
  aws iam create-policy-version --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-deploy-v2-$T \
    --policy-document file://$DIR/musclefit-gha-deploy-v2-$T.json --set-as-default
done
# role de plan (só leitura do WAF; ver ../gha-plan/README.md): v4
aws iam create-policy-version --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-plan-leitura \
  --policy-document file://infra/iam-policies/gha-plan/musclefit-gha-plan-leitura.json --set-as-default
```

Rollback das permissões: `aws iam set-default-policy-version --version-id <anterior>` (`compute` v3, `iam` v2, `rede-dados` v1, `gha-plan-leitura` v3).

**SEG-05, correção pós-apply: `rede-dados` v3 e `gha-plan-leitura` v5 (pendente de aval; publicar ANTES do merge deste PR).** O apply do SEG-05 (run 38149166830 do "Deploy produção", 2026-10-11) criou o Web ACL `musclefit-api` e o associou ao stage `staging` da `vaercx9o3g`, mas falhou ao **ler** a associação: `AccessDeniedException ... wafv2:GetWebACLForResource on resource: arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/*/*`. A chamada recebe o ARN do recurso protegido (o stage), não o do Web ACL; o IAM não conhece o nome do ACL antes da resposta e avalia contra o ARN genérico `regional/webacl/*/*`, que `musclefit-*/*` não casa. A AWS documenta exatamente esse recurso em "Permissions for `GetWebACLForResource`" (WAF Developer Guide, seção Amazon API Gateway REST API: `wafv2:GetWebACLForResource` e `wafv2:GetWebACL` em `arn:aws:wafv2:region:account-id:regional/webacl/*/*`). O mínimo que funciona:

- `GetWebACLForResource` vai para um statement **só dele** em `regional/webacl/*/*`, só `sa-east-1`, só a conta. É leitura e só devolve ACL da própria conta.
- `GetWebACL` **fica** em `musclefit-*/*`: o WAF confere `GetWebACL` no ACL **devolvido** (o `musclefit-api`), que o statement atual já cobre. Por isso não alarguei `GetWebACL` (leitura das regras de qualquer ACL da conta).

```json
{
  "Sid": "Wafv2GetWebACLForResource",
  "Effect": "Allow",
  "Action": "wafv2:GetWebACLForResource",
  "Resource": "arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/*/*"
}
```

Tamanhos depois: `rede-dados` 13 statements, 4.343 de 6.144 caracteres (71%); `gha-plan-leitura` 18 statements, 4.397 (72%). Versões depois: `rede-dados` 3 de 5; `gha-plan-leitura` **5 de 5** (a próxima publicação da role de plan exige apagar antes a v1, não padrão). Passos (credencial de administrador, Git Bash na raiz, branch do PR):

```bash
ACC=930413537709
aws iam create-policy-version --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-deploy-v2-rede-dados \
  --policy-document file://infra/iam-policies/consolidacao/musclefit-gha-deploy-v2-rede-dados.json --set-as-default
aws iam create-policy-version --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-plan-leitura \
  --policy-document file://infra/iam-policies/gha-plan/musclefit-gha-plan-leitura.json --set-as-default
# conferencia (deve sair "allowed" para as duas roles)
for R in musclefit-gha-deploy musclefit-gha-plan; do
  aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::$ACC:role/$R \
    --action-names wafv2:GetWebACLForResource \
    --resource-arns "arn:aws:wafv2:sa-east-1:$ACC:regional/webacl/*/*" \
    --query 'EvaluationResults[].EvalDecision' --output text
done
```

Rollback: `aws iam set-default-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-deploy-v2-rede-dados --version-id v2` e `aws iam set-default-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-plan-leitura --version-id v4`.

Ordem: a `gha-plan-leitura` v5 antes do plan do PR ficar verde (o `import` da associação chama `GetWebACLForResource` já no plan, com a role de plan); a `rede-dados` v3 antes da promoção para a `main` (o apply lê a associação no import). O state ficou com `aws_wafv2_web_acl_association.api` **tainted**; o PR troca o endereço por `api_stage` com `removed { destroy = false }` + `import`, para o próximo apply só ler e gravar a associação, sem recriá-la (detalhe em `infra/api_gateway_borda.tf`). Lacuna conhecida, fora deste PR: a role de deploy não tem `wafv2:DisassociateWebACL`; um destroy/replace futuro da associação falharia no Delete. Risco conhecido: se o `CreateWebACL` com grupo gerenciado da AWS exigir outro formato de ARN de `managedruleset`, o apply falha com `AccessDenied` explícito no `aws_wafv2_web_acl.api` antes de associar qualquer coisa à API (nada fica pela metade no tráfego); o erro mostra o ARN exato a acrescentar numa nova versão da `rede-dados`.

**SEG-05, correção do access log: `iam` v4 (pendente de aval; publicar ANTES da promoção do PR `fix/seg05-access-log-apigw` para a `main`).** Desde o apply do SEG-05 (2026-10-11, ~15:14 UTC), o grupo `/aws/apigateway/musclefit-api-access` tem 0 streams com tráfego real, e o grupo de execução também está vazio. O mapeamento de `api.musclefit.com.br` aponta para `vaercx9o3g`/`staging`, e `Count` > 0 no stage. No CloudTrail, a role `musclefit-apigw-cloudwatch-exec` só foi assumida pela sessão `BackplaneAssumeRoleSession` (plano de controle, até 14:58:37Z). Nunca houve AssumeRole nem `CreateLogStream` da entrega dos logs, e o `RoleLastUsed` ficou parado em 14:58:37Z. O PR tira o `aws:SourceAccount` da trust, igual aos exemplos oficiais da AWS, e força um deployment novo do stage. Mudar a trust de uma role que já existe exige `iam:UpdateAssumeRolePolicy`, que a role de deploy só tem em `musclefit-migrations-exec`. É a **única** ampliação, só neste ARN e sem condição (a ação não aceita uma condição útil aqui):

```json
{
  "Sid": "IamMusclefitApigwCloudwatchExec",
  "Effect": "Allow",
  "Action": "iam:UpdateAssumeRolePolicy",
  "Resource": "arn:aws:iam::930413537709:role/musclefit-apigw-cloudwatch-exec"
}
```

O resto do apply usa permissões que a role já exercitou em 11/10: `CreateDeployment`, `UpdateStage` (troca de `deploymentId`) e `DeleteDeployment` do deployment antigo. Também já tem `iam:GetRole` no refresh. A role de plan não muda, porque o plan só lê. Tamanho depois: `iam` 8 statements, 1.984 de 6.144 caracteres (32%). Versões depois: `iam` **4 de 5** (a próxima publicação exige apagar antes a v1, não padrão). Passos (credencial de administrador, Git Bash na raiz, branch do PR):

```bash
ACC=930413537709
aws iam create-policy-version --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-deploy-v2-iam   --policy-document file://infra/iam-policies/consolidacao/musclefit-gha-deploy-v2-iam.json --set-as-default
# conferencia (deve sair "allowed")
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::$ACC:role/musclefit-gha-deploy   --action-names iam:UpdateAssumeRolePolicy   --resource-arns arn:aws:iam::$ACC:role/musclefit-apigw-cloudwatch-exec   --query 'EvaluationResults[].EvalDecision' --output text
```

Rollback: `aws iam set-default-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-deploy-v2-iam --version-id v3`. A v3 é idêntica à v4 sem o statement acima.

**CloudWatch Logs, ARN de log group: `compute` v5 (PUBLICADA em 2026-10-29 pelo dono da conta, como versão padrão).** A AWS anunciou (Planned Lifecycle Event) que, a partir de **2027-04-26**, as ações de nível log group do CloudWatch Logs só serão autorizadas pelo ARN do grupo (`arn:aws:logs:<região>:<conta>:log-group:<nome>`). O ARN de stream (`...:log-group:<nome>:*` ou `...:log-stream:<x>`) continua valendo só para `PutLogEvents`, `GetLogEvents`, `CreateLogStream` e `DeleteLogStream`. A recomendação é **acrescentar** o ARN do grupo e **manter** o de stream.

Varredura (2026-10-29) de `infra/*.tf` e das 5 `musclefit-gha-deploy-v2-*.json`: o único statement Allow com recurso de logs em formato de stream e ação de grupo é `LogsMusclefitEnginesE1` (`logs:DeleteLogGroup` em `log-group:/aws/lambda/musclefit-migrations:*`, herdado da `fase6-migrations`). Ele ganha `log-group:/aws/lambda/musclefit-migrations`. Os demais já são ARN de grupo: `log-group:/aws/lambda/musclefit-*`, `/aws/lambda/musclefit-engines*`, `/aws/apigateway/musclefit-*`, `API-Gateway-Execution-Logs_vaercx9o3g/*` e `/aws/rds/cluster/musclefit-db/*` terminam em curinga sem `:*` e casam o nome do grupo (como o `*` do IAM também casa `:`, eles casam ainda a forma `:*`). `DescribeLogGroups` está em `*`. Em `infra/api_gateway_borda.tf`, a policy do `musclefit-apigw-cloudwatch-exec` já lista cada grupo com e sem `:*`. As managed antigas (`fase3-*`, `fase6-migrations` etc.) **não** foram editadas: estavam desanexadas e foram apagadas no passo 7 (2026-10-29). O teste `test_cwlogs_acoes_de_grupo_tem_arn_de_grupo` (`scripts/test_iam_consolidacao.py`) impede que um recurso só de stream volte a aparecer com ação de grupo nas `v2-*`.

Na prática, nada muda hoje: a role não tinha `DeleteLogGroup` no ARN de grupo da migrations. Depois de 2027-04-26, sem esta versão, só um destroy do grupo `/aws/lambda/musclefit-migrations` falharia. Tamanho depois: `compute` 20 statements, 5.937 de 6.144 caracteres (97%). Versões: antes da publicação a conta tinha v1 a v4, com v4 padrão. Hoje (2026-10-29) são **5 de 5** (v1 a v5, v5 padrão), e a próxima publicação da `compute` exige apagar antes a v1 (`aws iam delete-policy-version --version-id v1`). Resultado do simulador depois da publicação (`simulate-principal-policy` na role `musclefit-gha-deploy`): `logs:DeleteLogGroup` dá **`allowed`** em `arn:aws:logs:sa-east-1:930413537709:log-group:/aws/lambda/musclefit-migrations` e na forma `:*`. Passos executados (registro):

```bash
ACC=930413537709
P=arn:aws:iam::$ACC:policy/musclefit-gha-deploy-v2-compute
# 0. conferir: padrão v4 e no máximo 4 versões
aws iam list-policy-versions --policy-arn $P --query 'Versions[].[VersionId,IsDefaultVersion]' --output text
# 0b. só se já houver 5 versões: apagar a não padrão mais antiga (hoje seria v1)
# aws iam delete-policy-version --policy-arn $P --version-id v1
# 1. publicar a v5
aws iam create-policy-version --policy-arn $P \
  --policy-document file://infra/iam-policies/consolidacao/musclefit-gha-deploy-v2-compute.json --set-as-default
# 2. conferência: as duas linhas devem sair "allowed" (ARN de grupo e ARN de stream)
aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::$ACC:role/musclefit-gha-deploy \
  --action-names logs:DeleteLogGroup \
  --resource-arns arn:aws:logs:sa-east-1:$ACC:log-group:/aws/lambda/musclefit-migrations \
                  "arn:aws:logs:sa-east-1:$ACC:log-group:/aws/lambda/musclefit-migrations:*" \
  --query 'EvaluationResults[].[EvalResourceName,EvalDecision]' --output text
```

Rollback: `aws iam set-default-policy-version --policy-arn arn:aws:iam::930413537709:policy/musclefit-gha-deploy-v2-compute --version-id v4`. A v4 é idêntica à v5 sem o ARN de grupo acima e continua na conta para isso.

## Arquivos

| Arquivo | O que é |
|---|---|
| `estado-atual-2026-10-09.json` | Retrato **lido da conta** em 2026-10-09 (9 managed com a versão padrão + 6 inline). Conferido por hash canônico contra a conta: os 15 documentos são idênticos. Não contém segredo. Também resolve o "policies fora do repo" do AWS-13: `fase0`, `fase1`, `fase3`, `fase3-uploads` e `fase5-planreadonly` passam a ter cópia versionada. |
| `musclefit-gha-deploy-v2-compute.json` | Lambda, ECR, ECR Public + token do STS, API Gateway, EventBridge, CloudWatch Logs (15 statements, 4.543 caracteres) |
| `musclefit-gha-deploy-v2-iam.json` | Roles `musclefit-*-exec`, `PassRole` com condição, service-linked roles (5 statements, 1.294) |
| `musclefit-gha-deploy-v2-observabilidade.json` | Budgets, Cost Anomaly, CloudTrail, Access Analyzer, GuardDuty, SNS, alarmes (13 statements, 3.027) |
| `musclefit-gha-deploy-v2-rede-dados.json` | DynamoDB, Route 53, ACM, EC2/VPC, RDS, Secrets Manager (10 statements, 3.696) |
| `musclefit-gha-deploy-v2-s3-borda.json` | Buckets S3, CloudFront, SES (12 statements, 5.347) |
| `EQUIVALENCIA.md` | Tabela ação a ação: onde cada uma das 282 ações está hoje (policy/Sid: recursos) e onde fica na proposta; as novas trazem o PR de origem |
| `scripts/iam_consolidacao.py` | `gerar` (retrato + ações novas → 5 policies), `verificar` (prova de equivalência), `tabela` (gera `EQUIVALENCIA.md`) |
| `scripts/test_iam_consolidacao.py` | Testes do verificador e da proposta versionada |

Limite usado: 6.144 caracteres por managed policy (sem espaço em branco, `PolicySizeQuota` da conta). Todas cabem com folga; a maior (`s3-borda`) usa 87% e a `compute`, que recebeu as ações novas, 74%.

## Antes e depois

| | Antes (até 2026-10-09) | Depois (aplicado) |
|---|---|---|
| Managed anexadas | 9 | **5** |
| Inline | 6 | **0** |
| Documentos de permissão na role | 15 | 5 |
| Statements | 69 | 55 |
| Tuplas (ação, recurso, condição) | 567 | 489 (567 − 88 redundantes + 10 novas; redundante = coberta por outra tupla com recurso mais largo e a mesma condição) |
| Ações distintas | 276 | 282 (= 276 + 3 de concorrência + `ecr-public:GetAuthorizationToken`, `sts:GetServiceBearerToken` e `ecr:PutImageTagMutability`; `ecr:DeleteLifecyclePolicy` já existia em `musclefit-engines` e só ganha 2 repositórios) |

**Cota de managed policies por role: 20, não 10.** `GetAccountSummary` da conta devolve `AttachedPoliciesPerRoleQuota = 20`. Textos antigos (`infra/README.md` e as descrições dos PRs #425, #455 e #456) falavam em "9 de 10" e "última vaga"; isso estava errado. A role estava em 9/20 antes da troca; o aperto real era o número de exceções manuais, não a cota. Com a consolidação ficou em 5/20, com espaço para as exceções futuras virarem uma edição de policy existente (nova versão) em vez de policy nova. O limite de 10.240 bytes citado nos `iam_gha_*.tf` é outro: a soma das policies **inline** da role, que a proposta zera.

### Como a proposta foi montada

1. Expandir as 15 policies em tuplas `(ação, recurso, condição)`.
2. Acrescentar as tuplas novas da tabela acima (#438, #455, #456), cada uma com o escopo e a condição exatos do documento de origem.
3. Remover só tupla **coberta por outra tupla do próprio conjunto** (mesma ação, mesma condição ou sem condição, recurso mais largo que casa o mais estreito). Exemplo: `lambda:GetFunction` em `function:musclefit-engines` (inline `fase3-engines`) já está em `function:musclefit-*` (inline `fase3`). Nenhuma permissão efetiva muda.
4. Reagrupar por serviço e recurso: um statement por recurso com a lista de ações; recursos do mesmo serviço com a mesma lista viram um statement só.
5. Distribuir os statements em 5 policies por tema.

Nenhum curinga novo foi introduzido; todo ARN específico de hoje continua específico. As ações novas de ECR ficam nos repositórios nomeados, não em `musclefit-*`. As duas com `Resource: "*"` (`ecr-public:GetAuthorizationToken` e `sts:GetServiceBearerToken`) não aceitam recurso na API; a do STS fica presa ao serviço `ecr-public.amazonaws.com` pela condição.

## Prova de equivalência

Duas verificações independentes:

1. **Local, por cobertura de padrões** (`python scripts/iam_consolidacao.py verificar`): toda tupla de hoje é coberta por alguma tupla da proposta (nenhuma perdida) e toda tupla da proposta é coberta por alguma de hoje, exceto as 10 tuplas novas de `ACOES_NOVAS` (nada mais largo). Resultado: `OK: nenhuma permissao perdida, nenhuma alargada, acoes novas presentes.` Os testes (`python -m pytest -q scripts/test_iam_consolidacao.py`, 8 testes) conferem também que as ampliações são exatamente as dos PRs #438, #455 e #456 e que a do STS mantém a condição.
2. **Na conta, pelo simulador do IAM** (`iam:SimulateCustomPolicy`, só leitura, nada criado; refeito em 2026-10-09 com as ações de ECR): as 15 policies atuais foram lidas ao vivo da role (as mesmas 9 managed + 6 inline do retrato). Para cada par (recurso concreto, contexto de condição) que aparece em qualquer um dos dois conjuntos (67 grupos, 573 pares ação/recurso), a decisão das 15 atuais foi comparada com a das 5 propostas. Resultado: **0 permitidas hoje e negadas na proposta; 10 negadas hoje e permitidas na proposta, exatamente as esperadas**: `lambda:Put/Delete/GetFunctionConcurrency` em `function:musclefit-*`; `ecr-public:GetAuthorizationToken` em `*`; `sts:GetServiceBearerToken` em `*` com `sts:AWSServiceName = ecr-public.amazonaws.com`; `ecr:PutImageTagMutability` em `musclefit-api-core`, `musclefit-engines` e `musclefit-ai-generators`; `ecr:DeleteLifecyclePolicy` em `musclefit-api-core` e `musclefit-ai-generators`. Os pares que o simulador nega nos dois lados (por exemplo, ações de Route 53 e RDS com ARN sintético) são iguais nos dois conjuntos e não afetam a comparação. Nos 4 grupos de S3 que contêm `s3:PutBucketEncryption` (nome que o simulador recusa, ver achados), a comparação foi feita sem essa ação.

## Achados durante a leitura (não alterados, para manter "exatamente as mesmas ações")

- **`s3:PutBucketEncryption` não é uma ação IAM válida** (o simulador recusa o nome; a ação real é `s3:PutEncryptionConfiguration`). Está nas inline `fase0` e `fase3-uploads` e foi mantida para a equivalência ser literal. É inerte; pode sair numa revisão futura.
- **`route53:CreateHostedZone` em `Resource: "*"`** (exceção `m44-route53`, 2026-10-03) continua: a zona já existe e o AWS-13 recomenda remover. Mantida aqui porque o pedido é equivalência exata; retirar é uma linha em `v2-rede-dados.json` (statement `Route53Todos`), com autorização.
- `infra/iam-policies/musclefit-gha-deploy-fase3-workout-ai.json` está no repositório mas **não está anexada** à role (nem como inline). O que ela tem de exclusivo (`iam:UntagRole`/`UpdateAssumeRolePolicy` em `musclefit-workout-ai-exec`, `lambda:UntagResource`/`ListTags` em `musclefit-workout-ai`) não é concedido hoje e por isso não entra na proposta.
- `infra/iam_gha_eventbridge.tf`, `iam_gha_ses.tf`, `iam_gha_route53.tf` e `iam_gha_backend_deploy.tf` modelavam policies antigas atrás de flags `manage_gha_*` (todas `false`, `count = 0`, sem estado). Depois da troca viraram documentação morta e foram **removidas** em 2026-10-10 (PR `chore/docs-consolidacao-aplicada`, plan sem mudanças); ficam no histórico do git.
- **Relação com os PRs #438, #455 e #456: aplicando esta consolidação, nenhum dos três precisa de policy própria.**
  - #438: o passo 2 dele (v3 da `lote1-observabilidade`) deixa de ser necessário. Se já tiver sido publicado antes da troca, nada muda: as 3 ações estão na `v2-compute` e a `lote1` sai no passo 4.
  - #455: `musclefit-gha-deploy-aws12-ecr-public-pull.json` **não** precisa ser criada nem anexada; as duas ações estão na `v2-compute`. Sem elas o pipeline segue com pull anônimo (o login é por melhor esforço).
  - #456: `musclefit-gha-deploy-aws14-ecr-tags-imutaveis.json` **não** precisa ser criada nem anexada; as ações estão na `v2-compute`. O #456 continua exigindo a permissão na role **antes** da promoção dele para a main: faça a troca (passos 0 a 6) antes dessa promoção.
  - Se alguma dessas policies avulsas tiver sido anexada antes da troca, desanexe-a no passo 4 junto com as antigas (a v2 já cobre o conteúdo).

## Runbook (executado pelo dono da conta; passos 0 a 6 em 2026-10-09, passo 7 em 2026-10-29)

Pré-requisitos: credencial de administrador da conta 930413537709 (**não** a role do pipeline), Git Bash na raiz do repositório com este PR em `develop`, e nenhum `Deploy produção` rodando durante a troca. Região padrão `sa-east-1`. Nenhum passo remove permissão antes de a substituta estar anexada.

```bash
ACC=930413537709
ROLE=musclefit-gha-deploy
DIR=infra/iam-policies/consolidacao
aws sts get-caller-identity   # conferir que NAO e a role do pipeline
```

### 0. Backup do estado atual (fica fora do repositório)

```bash
mkdir -p ~/musclefit-iam-backup-$(date +%F) && cd ~/musclefit-iam-backup-$(date +%F)
aws iam list-attached-role-policies --role-name $ROLE > attached.json
aws iam list-role-policies --role-name $ROLE > inline.json
for P in fase0 fase1 fase3 fase3-engines fase3-uploads fase4-frontend; do
  aws iam get-role-policy --role-name $ROLE --policy-name musclefit-gha-deploy-$P \
    --query PolicyDocument --output json > inline-$P.json
done
cd -
```

(As 9 managed não precisaram de backup: continuaram na conta depois de desanexadas, até o passo 7. Depois dele, a cópia delas é `estado-atual-2026-10-09.json`.)

### 1. Criar as 5 policies novas

```bash
for T in compute iam observabilidade rede-dados s3-borda; do
  aws iam create-policy \
    --policy-name musclefit-gha-deploy-v2-$T \
    --policy-document file://$DIR/musclefit-gha-deploy-v2-$T.json \
    --description "musclefit-gha-deploy consolidada (AWS-13): $T" \
    --tags Key=project,Value=musclefit Key=managed_by,Value=manual-aws-13
done
```

### 2. Anexar as 5 novas (a role fica com 14 managed + 6 inline; cota de managed por role: 20)

```bash
for T in compute iam observabilidade rede-dados s3-borda; do
  aws iam attach-role-policy --role-name $ROLE \
    --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-deploy-v2-$T
done
aws iam list-attached-role-policies --role-name $ROLE --query 'length(AttachedPolicies)'   # 14
```

### 3. Conferir antes de remover qualquer coisa

```bash
# As ações novas passam a valer (antes: implicitDeny). Todas devem sair "allowed".
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::$ACC:role/$ROLE \
  --action-names lambda:PutFunctionConcurrency lambda:DeleteFunctionConcurrency lambda:GetFunctionConcurrency \
  --resource-arns arn:aws:lambda:sa-east-1:$ACC:function:musclefit-api-core \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
for R in musclefit-api-core musclefit-engines musclefit-ai-generators; do
  aws iam simulate-principal-policy \
    --policy-source-arn arn:aws:iam::$ACC:role/$ROLE \
    --action-names ecr:PutImageTagMutability ecr:DeleteLifecyclePolicy \
    --resource-arns arn:aws:ecr:sa-east-1:$ACC:repository/$R \
    --query 'EvaluationResults[].[EvalResourceName,EvalActionName,EvalDecision]' --output table
done
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::$ACC:role/$ROLE \
  --action-names ecr-public:GetAuthorizationToken \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::$ACC:role/$ROLE \
  --action-names sts:GetServiceBearerToken \
  --context-entries ContextKeyName=sts:AWSServiceName,ContextKeyValues=ecr-public.amazonaws.com,ContextKeyType=string \
  --query 'EvaluationResults[].[EvalActionName,EvalDecision]' --output table
```

### 4. Desanexar as 9 managed antigas, uma por vez

```bash
for P in fase8-ses m69-m70 cards-cloudfront fase7-eventbridge fase5-planreadonly \
         fase5-rag lote1-observabilidade fase6-migrations m44-route53; do
  aws iam detach-role-policy --role-name $ROLE \
    --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-deploy-$P
done
```

### 5. Apagar as 6 inline (o conteúdo está no backup do passo 0 e em `estado-atual-2026-10-09.json`)

```bash
for P in fase0 fase1 fase3 fase3-engines fase3-uploads fase4-frontend; do
  aws iam delete-role-policy --role-name $ROLE --policy-name musclefit-gha-deploy-$P
done
aws iam list-attached-role-policies --role-name $ROLE --query 'AttachedPolicies[].PolicyName'   # só as 5 v2
aws iam list-role-policies --role-name $ROLE --query 'PolicyNames'                               # []
```

### 6. Validar com o pipeline real (sem apply)

Rodar o `terraform plan` de um PR aberto que toque `infra/**` (re-run do job de plan do `terraform.yml`). O plan precisa sair igual ao de antes da troca: o plan lê todos os recursos e falha com `AccessDenied` se alguma leitura tiver se perdido. O próximo `Deploy produção` autorizado valida também as escritas.

### 7. APLICADO em 2026-10-29: apagar as 9 managed antigas

O deploy verde aconteceu em 2026-10-10 (runs 38041882892 e 38043337951). Em 2026-10-29 o dono da conta conferiu `AttachmentCount 0` nas 9 e rodou o laço abaixo (registro). Leitura posterior: `get-policy` responde `NoSuchEntity`, e só as 6 policies listadas em "Estado na conta (lido em 2026-10-29)" existem.

```bash
for P in fase8-ses m69-m70 cards-cloudfront fase7-eventbridge fase5-planreadonly \
         fase5-rag lote1-observabilidade fase6-migrations m44-route53; do
  ARN=arn:aws:iam::$ACC:policy/musclefit-gha-deploy-$P
  for V in $(aws iam list-policy-versions --policy-arn $ARN \
               --query 'Versions[?IsDefaultVersion==`false`].VersionId' --output text); do
    aws iam delete-policy-version --policy-arn $ARN --version-id $V
  done
  aws iam delete-policy --policy-arn $ARN
done
```

### Rollback (depois do passo 7: exige recriar as policies)

Desde 2026-10-29 as 9 managed antigas não existem mais na conta. Voltar ao arranjo anterior exige **recriá-las** (`aws iam create-policy`) a partir dos documentos de `estado-atual-2026-10-09.json` (versão padrão de cada uma na época) antes de anexá-las; as 6 inline vêm do mesmo JSON ou do backup do passo 0. Para regressão de permissão, preferir o rollback por versão das `v2-*` (`set-default-policy-version`), descrito em cada seção acima.

```bash
# 0. antes: recriar cada uma das 9 a partir de estado-atual-2026-10-09.json, por exemplo
# aws iam create-policy --policy-name musclefit-gha-deploy-$P --policy-document file://<documento-extraido>.json
for P in fase8-ses m69-m70 cards-cloudfront fase7-eventbridge fase5-planreadonly \
         fase5-rag lote1-observabilidade fase6-migrations m44-route53; do
  aws iam attach-role-policy --role-name $ROLE --policy-arn arn:aws:iam::$ACC:policy/musclefit-gha-deploy-$P
done
for P in fase0 fase1 fase3 fase3-engines fase3-uploads fase4-frontend; do
  aws iam put-role-policy --role-name $ROLE --policy-name musclefit-gha-deploy-$P \
    --policy-document file://$HOME/musclefit-iam-backup-AAAA-MM-DD/inline-$P.json
done
# e então desanexar as v2, se desejado
```

Como as permissões são a união de todas as policies anexadas, ter antigas e novas juntas (passos 2 a 4) nunca nega nada: a janela de troca não tem risco de `AccessDenied`.
