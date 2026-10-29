"""Guarda da role de plan so leitura `musclefit-gha-plan` (AWS-03). Local, sem AWS:
`python -m pytest -q scripts/test_iam_gha_plan.py`.

Confere os documentos versionados em infra/iam-policies/gha-plan/:
  - a policy de permissao so tem leitura (nenhum Put/Create/Delete/Update/Attach/Pass...);
  - os dados sensiveis ficam de fora (objetos S3, itens DynamoDB, logs, imagens ECR,
    valor de QUALQUER segredo), com as excecoes justificadas no README (state e RAG);
  - toda chamada que o `terraform plan` real fez na conta (CloudTrail, 2026-10-10)
    continua coberta;
  - a trust da role de plan so aceita `pull_request`, e a trust-alvo da role de deploy
    deixa de aceitar;
  - (etapa 5) o Terraform nao gerencia valor de segredo: nenhum
    `aws_secretsmanager_secret_version` nem `random_*` em infra/, porque o refresh deles
    exigiria `secretsmanager:GetSecretValue` na role de plan.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR = RAIZ / "infra" / "iam-policies" / "gha-plan"
CONTA = "930413537709"
REGIAO = "sa-east-1"
# M103: o projeto migrou para o repositorio publico `muscle_fit_v2`. Na virada
# (2026-10-29) os subs do antigo (`muscle_fit`, privado e arquivado) sairam das trusts
# na AWS; as trusts aceitam so o v2 (ver README, "Repositorio v2").
REPO_IDS = ("repo:dougbrsp@33515327/muscle_fit_v2@1436380326",)
REPO_NOME = ("repo:dougbrsp/muscle_fit_v2",)
LIMITE_MANAGED = 6144

# Prefixos de verbo permitidos numa role de plan. `apigateway:GET` e o unico verbo HTTP
# (API Gateway v1 expoe leitura como GET em /restapis/...).
VERBOS_LEITURA = ("Get", "List", "Describe", "View")

# Chamadas que o job `plan` de 3 PRs fez com a role de deploy em 2026-10-10 (CloudTrail
# LookupEvents por AccessKeyId da sessao OIDC `pull_request`, regioes sa-east-1, us-east-1,
# us-east-2 e us-west-2), ja traduzidas do eventName para a acao IAM. Mais as de dados que o
# CloudTrail de gerenciamento nao registra (estado no S3, digest do lock no DynamoDB, objetos
# do RAG). Recurso: um ARN concreto representativo.
_L = f"arn:aws:lambda:{REGIAO}:{CONTA}:function:musclefit-api-core"
_ECR = f"arn:aws:ecr:{REGIAO}:{CONTA}:repository/musclefit-engines"
_DDB = f"arn:aws:dynamodb:{REGIAO}:{CONTA}:table/musclefit-async-jobs"
_B = "arn:aws:s3:::musclefit-uploads"
_ROLE = f"arn:aws:iam::{CONTA}:role/musclefit-api-core-exec"
_SEC = f"arn:aws:secretsmanager:{REGIAO}:{CONTA}:secret:musclefit/"
OBSERVADAS = [
    # dados (nao aparecem no CloudTrail de gerenciamento)
    ("s3:ListBucket", "arn:aws:s3:::musclefit-tfstate-930413537709"),
    ("s3:GetObject", "arn:aws:s3:::musclefit-tfstate-930413537709/fase1/terraform.tfstate"),
    ("dynamodb:GetItem", f"arn:aws:dynamodb:{REGIAO}:{CONTA}:table/musclefit-tfstate-lock"),
    ("s3:GetObject", "arn:aws:s3:::musclefit-rag/protocolos/exemplo.md"),
    ("s3:GetObjectTagging", "arn:aws:s3:::musclefit-rag/protocolos/exemplo.md"),
    # HeadBucket do refresh de cada aws_s3_bucket (exige s3:ListBucket). Evento de dados:
    # nao aparece no CloudTrail de gerenciamento. Sem ele o provider 5.x le o 403 como
    # "has been deleted" e o plan propoe recriar os 4 buckets (PR #490, 1o plan com a role).
    ("s3:ListBucket", "arn:aws:s3:::musclefit-cloudtrail-930413537709"),
    ("s3:ListBucket", "arn:aws:s3:::musclefit-frontend-assets"),
    ("s3:ListBucket", "arn:aws:s3:::musclefit-rag"),
    ("s3:ListBucket", "arn:aws:s3:::musclefit-uploads"),
    # sa-east-1
    ("access-analyzer:GetAnalyzer", "*"),
    ("acm:DescribeCertificate", "*"), ("acm:GetCertificate", "*"),
    ("acm:ListCertificates", "*"), ("acm:ListTagsForCertificate", "*"),
    ("apigateway:GET", f"arn:aws:apigateway:{REGIAO}::/restapis/x/resources/y/methods/GET"),
    ("cloudtrail:DescribeTrails", "*"), ("cloudtrail:GetTrailStatus", "*"), ("cloudtrail:ListTags", "*"),
    ("dynamodb:DescribeContinuousBackups", _DDB), ("dynamodb:DescribeTable", _DDB),
    ("dynamodb:DescribeTimeToLive", _DDB), ("dynamodb:ListTagsOfResource", _DDB),
    ("ec2:DescribeAvailabilityZones", "*"), ("ec2:DescribeNetworkAcls", "*"),
    ("ec2:DescribeRouteTables", "*"), ("ec2:DescribeSecurityGroupRules", "*"),
    ("ec2:DescribeSecurityGroups", "*"), ("ec2:DescribeSubnets", "*"),
    ("ec2:DescribeVpcAttribute", "*"), ("ec2:DescribeVpcs", "*"),
    ("ecr:DescribeRepositories", _ECR), ("ecr:GetLifecyclePolicy", _ECR), ("ecr:ListTagsForResource", _ECR),
    ("events:DescribeRule", f"arn:aws:events:{REGIAO}:{CONTA}:rule/musclefit-intervals-sync-daily"),
    ("events:ListTagsForResource", f"arn:aws:events:{REGIAO}:{CONTA}:rule/musclefit-intervals-sync-daily"),
    ("events:ListTargetsByRule", f"arn:aws:events:{REGIAO}:{CONTA}:rule/musclefit-intervals-sync-daily"),
    ("guardduty:GetDetector", "*"),
    ("lambda:GetFunction", _L), ("lambda:GetFunctionEventInvokeConfig", _L),
    ("lambda:GetPolicy", _L), ("lambda:ListVersionsByFunction", _L),
    ("logs:DescribeLogGroups", "*"), ("logs:ListTagsForResource", "*"),
    ("cloudwatch:DescribeAlarms", "*"), ("cloudwatch:ListTagsForResource", "*"),
    ("rds:DescribeDBClusters", "*"), ("rds:DescribeDBInstances", "*"),
    ("rds:DescribeDBSubnetGroups", "*"), ("rds:DescribeGlobalClusters", "*"),
    ("rds:ListTagsForResource", "*"),
    ("s3:GetAccelerateConfiguration", _B), ("s3:GetBucketAcl", _B), ("s3:GetBucketCORS", _B),
    ("s3:GetEncryptionConfiguration", _B), ("s3:GetLifecycleConfiguration", _B),
    ("s3:GetBucketLogging", _B), ("s3:GetBucketObjectLockConfiguration", _B),
    ("s3:GetBucketPolicy", _B), ("s3:GetBucketPublicAccessBlock", _B),
    ("s3:GetReplicationConfiguration", _B), ("s3:GetBucketRequestPayment", _B),
    ("s3:GetBucketTagging", _B), ("s3:GetBucketVersioning", _B), ("s3:GetBucketWebsite", _B),
    ("secretsmanager:DescribeSecret", _SEC + "turnstile-secret-key-PGU7U1"),
    ("secretsmanager:GetResourcePolicy", _SEC + "turnstile-secret-key-PGU7U1"),
    # Import do secret do USDA (nutricao, 2026-10-17): mesmo statement SecretsMetadados.
    ("secretsmanager:DescribeSecret", _SEC + "usda-api-key-cMHVbT"),
    ("secretsmanager:GetResourcePolicy", _SEC + "usda-api-key-cMHVbT"),
    # GetSecretValue nos 3 segredos com `_version` no Terraform: SAIU na v3 (AWS-03, etapa 5),
    # junto com os 3 `aws_secretsmanager_secret_version` (blocos `removed` em secrets.tf).
    ("ses:GetEmailIdentity", f"arn:aws:ses:{REGIAO}:{CONTA}:identity/musclefit.com.br"),
    ("ses:ListTagsForResource", f"arn:aws:ses:{REGIAO}:{CONTA}:identity/musclefit.com.br"),
    ("sns:GetSubscriptionAttributes", f"arn:aws:sns:{REGIAO}:{CONTA}:musclefit-alerts:0b1c2d3e-aaaa"),
    ("sns:GetTopicAttributes", f"arn:aws:sns:{REGIAO}:{CONTA}:musclefit-alerts"),
    ("sns:ListTagsForResource", f"arn:aws:sns:{REGIAO}:{CONTA}:musclefit-alerts"),
    # us-east-1 (CloudFront, ACM da borda, IAM, Route 53, Budgets, SNS/alarme da borda)
    ("budgets:ViewBudget", f"arn:aws:budgets::{CONTA}:budget/musclefit-mensal"),
    ("budgets:ListTagsForResource", f"arn:aws:budgets::{CONTA}:budget/musclefit-mensal"),
    ("cloudfront:DescribeFunction", "*"), ("cloudfront:GetDistribution", "*"),
    ("cloudfront:GetFunction", "*"), ("cloudfront:GetOriginAccessControl", "*"),
    ("cloudfront:ListTagsForResource", "*"),
    ("iam:GetRole", _ROLE), ("iam:GetRolePolicy", _ROLE),
    ("iam:ListAttachedRolePolicies", _ROLE), ("iam:ListRolePolicies", _ROLE),
    ("iam:GetRole", f"arn:aws:iam::{CONTA}:role/musclefit-root-alert-forward-exec"),
    ("route53:GetHostedZone", "*"), ("route53:ListResourceRecordSets", "*"),
    ("route53:ListTagsForResource", "*"),
    ("sns:GetTopicAttributes", f"arn:aws:sns:us-east-1:{CONTA}:musclefit-alerts"),
    # us-east-2 / us-west-2: regras do alerta de root (infra/alerta_root.tf, PR #486),
    # ainda nao estavam no state quando o CloudTrail foi lido.
    ("events:DescribeRule", f"arn:aws:events:us-east-2:{CONTA}:rule/musclefit-root-api"),
    ("events:ListTargetsByRule", f"arn:aws:events:us-west-2:{CONTA}:rule/musclefit-root-signin"),
    ("events:ListTagsForResource", f"arn:aws:events:us-east-1:{CONTA}:rule/musclefit-root-forward"),
    # SEG-05/AWS-10 (infra/api_gateway_borda.tf): refresh do aws_wafv2_web_acl (GetWebACL,
    # ListTagsForResource) e do aws_wafv2_web_acl_association (GetWebACLForResource, que o
    # WAF autoriza junto com GetWebACL). Previstas pela documentacao do provider/WAF, nao
    # pelo CloudTrail: o Web ACL so passa a existir no state depois do apply do PR.
    # GetWebACLForResource e avaliado contra o ARN GENERICO `regional/webacl/*/*` (o IAM
    # nao sabe o nome do ACL antes da resposta): AccessDenied no apply do SEG-05, run
    # 38149166830, com a v2 escopada em musclefit-*. Por isso o recurso aqui e o literal.
    ("wafv2:GetWebACL", f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/webacl/musclefit-api/abc-123"),
    ("wafv2:GetWebACLForResource", f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/webacl/*/*"),
    ("wafv2:ListTagsForResource", f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/webacl/musclefit-api/abc-123"),
]

# Coisas que a role de plan NUNCA pode fazer, mesmo sendo "leitura".
PROIBIDAS = [
    ("s3:PutObject", "arn:aws:s3:::musclefit-tfstate-930413537709/fase1/terraform.tfstate"),
    ("s3:DeleteObject", "arn:aws:s3:::musclefit-tfstate-930413537709/fase1/terraform.tfstate"),
    ("s3:GetObject", "arn:aws:s3:::musclefit-uploads/users/1.jpg"),
    ("s3:GetObject", "arn:aws:s3:::musclefit-frontend-assets/index.html"),
    ("s3:GetObject", "arn:aws:s3:::musclefit-tfstate-930413537709/outra/chave.tfstate"),
    ("s3:ListBucket", "arn:aws:s3:::musclefit-outro-bucket"),
    ("dynamodb:PutItem", f"arn:aws:dynamodb:{REGIAO}:{CONTA}:table/musclefit-tfstate-lock"),
    ("dynamodb:DeleteItem", f"arn:aws:dynamodb:{REGIAO}:{CONTA}:table/musclefit-tfstate-lock"),
    ("dynamodb:GetItem", _DDB), ("dynamodb:Query", _DDB), ("dynamodb:Scan", _DDB),
    ("secretsmanager:GetSecretValue", _SEC + "turnstile-secret-key-PGU7U1"),
    ("secretsmanager:GetSecretValue",
     f"arn:aws:secretsmanager:{REGIAO}:{CONTA}:secret:rds!cluster-b3317b3e-2be1-41f8-9c18-132e4847112f-AbC123"),
    ("secretsmanager:GetSecretValue", _SEC + "openai-api-key-extra-AbC123"),
    # v3 (etapa 5): nem os 3 que o refresh dos `_version` lia na v1/v2.
    ("secretsmanager:GetSecretValue", _SEC + "openai-api-key-AbC123"),
    ("secretsmanager:GetSecretValue", _SEC + "api-token-AbC123"),
    ("secretsmanager:GetSecretValue", _SEC + "secret-encryption-key-AbC123"),
    ("secretsmanager:GetSecretValue", _SEC + "turnstile-secret-key-PGU7U1"),
    ("secretsmanager:GetSecretValue", _SEC + "usda-api-key-cMHVbT"),
    ("logs:GetLogEvents", "*"), ("logs:FilterLogEvents", "*"), ("logs:StartQuery", "*"),
    ("ecr:BatchGetImage", _ECR), ("ecr:GetDownloadUrlForLayer", _ECR), ("ecr:GetAuthorizationToken", "*"),
    ("rds:DownloadDBLogFilePortion", "*"), ("rds-data:ExecuteStatement", "*"),
    ("kms:Decrypt", "*"), ("ssm:GetParameter", "*"),
    ("lambda:UpdateFunctionCode", _L), ("lambda:InvokeFunction", _L),
    ("iam:PassRole", _ROLE), ("iam:PutRolePolicy", _ROLE),
    ("iam:UpdateAssumeRolePolicy", f"arn:aws:iam::{CONTA}:role/musclefit-gha-deploy"),
    ("sts:AssumeRole", f"arn:aws:iam::{CONTA}:role/musclefit-gha-deploy"),
    ("cloudfront:CreateInvalidation", "*"), ("route53:ChangeResourceRecordSets", "*"),
    ("wafv2:UpdateWebACL", f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/webacl/musclefit-api/abc-123"),
    ("wafv2:DisassociateWebACL", "*"), ("apigateway:SetWebACL", "*"),
    # O statement generico de GetWebACLForResource nao abre leitura de ACL de terceiros.
    ("wafv2:GetWebACL", f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/webacl/outro-acl/abc-123"),
    ("wafv2:ListTagsForResource", f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/webacl/outro-acl/abc-123"),
    ("wafv2:GetWebACLForResource", f"arn:aws:wafv2:us-east-1:{CONTA}:regional/webacl/*/*"),
]


def _lista(v):
    return v if isinstance(v, list) else [v]


def _carrega(nome):
    return json.loads((DIR / nome).read_text(encoding="utf-8"))


def _permite(doc, acao, recurso) -> bool:
    """Avaliacao IAM simplificada (so Allow, sem Condition; a policy nao usa os dois)."""
    for st in doc["Statement"]:
        assert st["Effect"] == "Allow" and "Condition" not in st
        if not any(fnmatch.fnmatchcase(acao.lower(), a.lower()) for a in _lista(st["Action"])):
            continue
        if any(r == "*" or fnmatch.fnmatchcase(recurso, r) for r in _lista(st["Resource"])):
            return True
    return False


def test_so_acoes_de_leitura():
    doc = _carrega("musclefit-gha-plan-leitura.json")
    ruins = []
    for st in doc["Statement"]:
        assert "NotAction" not in st and "NotResource" not in st, st["Sid"]
        for a in _lista(st["Action"]):
            servico, _, op = a.partition(":")
            ok = a == "apigateway:GET" or (op.startswith(VERBOS_LEITURA) and op != "*" and servico)
            if not ok:
                ruins.append(a)
    assert ruins == []


def test_nenhum_verbo_de_escrita_nem_curinga_total():
    texto = (DIR / "musclefit-gha-plan-leitura.json").read_text(encoding="utf-8")
    acoes = re.findall(r'"([a-z0-9-]+:[A-Za-z*]+)"', texto)
    escrita = re.compile(r":(Put|Create|Delete|Update|Attach|Detach|Pass|Tag|Untag|Modify|Set|"
                         r"Add|Remove|Invoke|Start|Stop|Change|Publish|Send|Enable|Disable|"
                         r"Associate|Restore|Copy|Upload|Replace|Reset|Run|Execute|Assume|"
                         r"Import|Revoke|Authorize|Subscribe|Unsubscribe|Rotate|\*)")
    assert [a for a in acoes if escrita.search(a)] == []


def test_cobre_tudo_que_o_plan_real_chamou():
    doc = _carrega("musclefit-gha-plan-leitura.json")
    assert [(a, r) for a, r in OBSERVADAS if not _permite(doc, a, r)] == []


def test_dados_sensiveis_ficam_de_fora():
    doc = _carrega("musclefit-gha-plan-leitura.json")
    assert [(a, r) for a, r in PROIBIDAS if _permite(doc, a, r)] == []


def test_cabe_numa_managed_policy():
    doc = _carrega("musclefit-gha-plan-leitura.json")
    assert len(json.dumps(doc, separators=(",", ":"))) <= LIMITE_MANAGED


def _subs(doc):
    (st,) = doc["Statement"]
    assert st["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert st["Principal"] == {
        "Federated": f"arn:aws:iam::{CONTA}:oidc-provider/token.actions.githubusercontent.com"}
    cond = st["Condition"]
    assert cond["StringEquals"]["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    subs = []
    for op in cond.values():
        subs += _lista(op.get("token.actions.githubusercontent.com:sub", []))
    return set(subs)


def test_trust_da_role_de_plan_so_aceita_pull_request():
    doc = _carrega("musclefit-gha-plan-trust.json")
    assert _subs(doc) == {f"{r}:pull_request" for r in REPO_NOME + REPO_IDS}
    # sem curinga: StringEquals e nenhum StringLike
    assert set(doc["Statement"][0]["Condition"]) == {"StringEquals"}


def test_trust_alvo_da_role_de_deploy_nao_aceita_pull_request():
    doc = _carrega("musclefit-gha-deploy-trust-sem-pull-request.json")
    subs = _subs(doc)
    assert not any("pull_request" in s or "*" in s for s in subs)
    assert subs == {
        f"{r}:{ctx}"
        for r in REPO_NOME + REPO_IDS
        for ctx in ("ref:refs/heads/main", "environment:production")
    }


def test_nenhum_get_secret_value_na_policy():
    """v3: a role de plan nao le valor de segredo nenhum, em nenhum recurso."""
    texto = (DIR / "musclefit-gha-plan-leitura.json").read_text(encoding="utf-8")
    assert "GetSecretValue" not in texto
    assert "secretsmanager:Get*" not in texto and "secretsmanager:*" not in texto


def test_terraform_nao_gerencia_valor_de_segredo():
    """Etapa 5: so o container do segredo fica no Terraform. Um `_version` (ou um
    `random_*` que gere o valor) voltaria a gravar o segredo no state e a exigir
    GetSecretValue na role de plan."""
    proibidos = re.compile(
        r'^\s*(resource|data)\s+"(aws_secretsmanager_secret_version|random_[a-z_]+)"', re.M)
    achados = []
    for tf in sorted((RAIZ / "infra").rglob("*.tf")):
        if ".terraform" in tf.parts:
            continue
        for m in proibidos.finditer(tf.read_text(encoding="utf-8")):
            achados.append(f"{tf.relative_to(RAIZ)}: {m.group(0).strip()}")
    assert achados == []


def test_os_tres_versions_saem_do_state_sem_destroy():
    """Os 3 `removed` precisam de `destroy = false`: sem ele o plan mostraria destroy e o
    apply tentaria destruir a versao na AWS (o provider tira os rotulos de estagio dela)
    em vez de so esquecer o recurso no state."""
    texto = (RAIZ / "infra" / "secrets.tf").read_text(encoding="utf-8")
    blocos = re.findall(r"removed\s*\{(.*?)\n\}", texto, re.S)
    alvos = {}
    for b in blocos:
        alvo = re.search(r"from\s*=\s*(\S+)", b).group(1)
        alvos[alvo] = re.search(r"destroy\s*=\s*false", b) is not None
    assert alvos == {
        "aws_secretsmanager_secret_version.openai_api_key": True,
        "aws_secretsmanager_secret_version.api_token": True,
        "aws_secretsmanager_secret_version.secret_encryption_key": True,
    }
