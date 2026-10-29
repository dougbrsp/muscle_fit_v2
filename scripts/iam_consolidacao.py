"""Consolidacao das policies da role de deploy musclefit-gha-deploy (AWS-13).

Dois subcomandos, ambos 100% locais (nenhuma chamada AWS):

  python scripts/iam_consolidacao.py gerar
      Le o retrato da conta (infra/iam-policies/consolidacao/estado-atual-2026-10-09.json),
      acrescenta as acoes novas (ACOES_NOVAS) e escreve as policies consolidadas em
      infra/iam-policies/consolidacao/musclefit-gha-deploy-v2-*.json.

  python scripts/iam_consolidacao.py verificar
      Prova, por um caminho de codigo INDEPENDENTE da geracao, que:
        (a) nenhuma permissao de hoje se perde: toda tupla (acao, recurso, condicao)
            do retrato e coberta por alguma tupla da proposta;
        (b) nada fica mais largo: toda tupla da proposta e coberta por alguma tupla
            do retrato OU e uma das ACOES_NOVAS no escopo declarado;
        (c) as ACOES_NOVAS estao presentes;
        (d) cada policy cabe no limite de 6.144 caracteres (sem espaco em branco),
            e a proposta tem no maximo MAX_POLICIES policies.
      Sai com codigo 1 se qualquer item falhar.

Modelo de cobertura: tupla A e coberta por B quando a acao e a mesma, a condicao
de B e vazia ou identica a de A, e o recurso-padrao de B casa o recurso-padrao de
A tratando o "*" de A como caractere literal (o "*" de B absorve qualquer
expansao do de A; nenhuma policy aqui usa "?"). So "Allow"; nenhuma policy da
role tem Deny, NotAction ou NotResource (verificado no retrato).
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR = RAIZ / "infra" / "iam-policies" / "consolidacao"
RETRATO = DIR / "estado-atual-2026-10-09.json"
PREFIXO = "musclefit-gha-deploy-v2-"
LIMITE_MANAGED = 6144
MAX_POLICIES = 5

CONTA = "930413537709"
REGIAO = "sa-east-1"

_ECR = f"arn:aws:ecr:{REGIAO}:{CONTA}:repository/musclefit-"
_LAMBDA = f"arn:aws:lambda:{REGIAO}:{CONTA}:function:musclefit-*"
_RDS_LOGS = f"arn:aws:logs:{REGIAO}:{CONTA}:log-group:/aws/rds/cluster/musclefit-db/*"
_REGIOES_ALERTA_ROOT = ("us-east-1", "us-east-2", "us-west-2")
_WAF_ACL = f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/webacl/musclefit-*/*"
_WAF_MANAGED = f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/managedruleset/*/*"
_WAF_ACL_ACOES = (
    "wafv2:AssociateWebACL", "wafv2:CreateWebACL", "wafv2:DeleteWebACL", "wafv2:GetWebACL",
    "wafv2:ListTagsForResource", "wafv2:TagResource", "wafv2:UntagResource",
    "wafv2:UpdateWebACL",
)
# GetWebACLForResource e autorizado contra o ARN generico regional/webacl/*/* (a chamada
# recebe o ARN do recurso protegido, nao o do Web ACL, e o IAM nao sabe o nome do ACL antes
# de responder). Documentado em "Permissions for GetWebACLForResource" (WAF Developer
# Guide). O WAF ainda confere GetWebACL no ACL devolvido, que fica em musclefit-*.
_WAF_ACL_QUALQUER = f"arn:aws:wafv2:{REGIAO}:{CONTA}:regional/webacl/*/*"
_APIGW_LOGS = (
    f"arn:aws:logs:{REGIAO}:{CONTA}:log-group:/aws/apigateway/musclefit-*",
    f"arn:aws:logs:{REGIAO}:{CONTA}:log-group:API-Gateway-Execution-Logs_vaercx9o3g/*",
)
_MIGRATIONS_LOG_GROUP = f"arn:aws:logs:{REGIAO}:{CONTA}:log-group:/aws/lambda/musclefit-migrations"
_APIGW_LOGS_ACOES = (
    "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:ListTagsForResource",
    "logs:ListTagsLogGroup", "logs:PutRetentionPolicy", "logs:TagResource",
)
_EVENTS_REGRA = (
    "events:DeleteRule", "events:DescribeRule", "events:ListTagsForResource",
    "events:ListTargetsByRule", "events:PutRule", "events:PutTargets",
    "events:RemoveTargets", "events:TagResource", "events:UntagResource",
)

# Acoes que a role NAO tem hoje (retrato de 2026-10-09) e que entram na proposta,
# cada uma com o escopo exato do documento de origem. Tupla:
# (acao, recurso, condicao ou None, origem). Sao as UNICAS ampliacoes permitidas
# pelo `verificar`.
ACOES_NOVAS = [
    # PR #438 (concorrencia reservada, AWS-01): mesmo escopo do statement do #438.
    ("lambda:PutFunctionConcurrency", _LAMBDA, None, "PR #438"),
    ("lambda:DeleteFunctionConcurrency", _LAMBDA, None, "PR #438"),
    ("lambda:GetFunctionConcurrency", _LAMBDA, None, "PR #438"),
    # PR #455 (AWS-12, pull autenticado no ECR Public):
    # infra/iam-policies/musclefit-gha-deploy-aws12-ecr-public-pull.json.
    ("ecr-public:GetAuthorizationToken", "*", None, "PR #455"),
    ("sts:GetServiceBearerToken", "*",
     {"StringEquals": {"sts:AWSServiceName": "ecr-public.amazonaws.com"}}, "PR #455"),
    # PR #456 (AWS-14, tags imutaveis no ECR):
    # infra/iam-policies/musclefit-gha-deploy-aws14-ecr-tags-imutaveis.json.
    ("ecr:PutImageTagMutability", _ECR + "api-core", None, "PR #456"),
    ("ecr:PutImageTagMutability", _ECR + "engines", None, "PR #456"),
    ("ecr:PutImageTagMutability", _ECR + "ai-generators", None, "PR #456"),
    ("ecr:DeleteLifecyclePolicy", _ECR + "api-core", None, "PR #456"),
    ("ecr:DeleteLifecyclePolicy", _ECR + "ai-generators", None, "PR #456"),
    # PR #471 (FIN-04, TLS-only e lifecycle de versoes nao correntes em S3): a role
    # nao tinha PutBucketPolicy nem PutLifecycleConfiguration no bucket do site, nem
    # PutLifecycleConfiguration no musclefit-rag. Publicado como v2 da v2-s3-borda.
    ("s3:PutBucketPolicy", "arn:aws:s3:::musclefit-frontend-assets", None, "PR #471"),
    ("s3:PutLifecycleConfiguration", "arn:aws:s3:::musclefit-frontend-assets", None, "PR #471"),
    ("s3:PutLifecycleConfiguration", "arn:aws:s3:::musclefit-rag", None, "PR #471"),
    # PR #471 (AWS-18, retencao do log de erro do Aurora, grupo importado): leitura
    # de tags no import/refresh, tags do provider e a retencao. Publicado como v2 da
    # v2-compute.
    ("logs:ListTagsForResource", _RDS_LOGS, None, "PR #471"),
    ("logs:PutRetentionPolicy", _RDS_LOGS, None, "PR #471"),
    ("logs:TagResource", _RDS_LOGS, None, "PR #471"),
    # PR alarme root (feat/alarme-login-root, infra/alerta_root.tf): regras EventBridge
    # musclefit-root-* nas regioes onde o login/uso do root aparece. Hoje a role so
    # gerencia regras em sa-east-1. Mesmo prefixo de regra, sem curinga de regiao; sem
    # Enable/DisableRule (o provider liga a regra no PutRule). Publicado como v3 da
    # v2-compute.
    *[(acao, f"arn:aws:events:{regiao}:{CONTA}:rule/musclefit-*", None, "PR alarme root")
      for regiao in _REGIOES_ALERTA_ROOT for acao in _EVENTS_REGRA],
    # A regra de encaminhamento (us-east-2/us-west-2 -> us-east-1) usa a role
    # musclefit-root-alert-forward-exec: PassRole so dela e so para o EventBridge.
    # Publicado como v2 da v2-iam.
    ("iam:PassRole", f"arn:aws:iam::{CONTA}:role/musclefit-root-alert-forward-exec",
     {"StringEquals": {"iam:PassedToService": "events.amazonaws.com"}}, "PR alarme root"),
    # PR SEG-05/AWS-08/AWS-10 (infra/api_gateway_borda.tf): WAF regional na API, so em
    # Web ACLs musclefit-* da sa-east-1. Create/UpdateWebACL tambem no ARN de managed rule
    # set da conta (referencia ao AWSManagedRulesCommonRuleSet). Publicado como v2 da
    # v2-rede-dados.
    *[(acao, _WAF_ACL, None, "PR SEG-05") for acao in _WAF_ACL_ACOES],
    ("wafv2:CreateWebACL", _WAF_MANAGED, None, "PR SEG-05"),
    ("wafv2:UpdateWebACL", _WAF_MANAGED, None, "PR SEG-05"),
    # Leitura da associacao (refresh do aws_wafv2_web_acl_association). Na v2 estava em
    # musclefit-*/* e o apply do SEG-05 (run 38149166830) falhou com AccessDenied em
    # regional/webacl/*/*. So esta acao, so leitura, so sa-east-1 da conta. Publicado como
    # v3 da v2-rede-dados.
    ("wafv2:GetWebACLForResource", _WAF_ACL_QUALQUER, None, "PR SEG-05"),
    # Associar/desassociar o Web ACL ao stage (API Gateway REST exige SetWebACL no stage)
    # e o role de CloudWatch da conta (GET/PATCH em /account). Publicado como v4 da
    # v2-compute.
    ("apigateway:SetWebACL", f"arn:aws:apigateway:{REGIAO}::/restapis/*/stages/*", None, "PR SEG-05"),
    ("apigateway:GET", f"arn:aws:apigateway:{REGIAO}::/account", None, "PR SEG-05"),
    ("apigateway:PATCH", f"arn:aws:apigateway:{REGIAO}::/account", None, "PR SEG-05"),
    # Log de acesso e de execucao do API Gateway, 14 dias. Publicado como v4 da v2-compute.
    *[(acao, rec, None, "PR SEG-05") for rec in _APIGW_LOGS for acao in _APIGW_LOGS_ACOES],
    # aws_api_gateway_account entrega o role musclefit-apigw-cloudwatch-exec ao API
    # Gateway: PassRole so dele e so para apigateway. Publicado como v3 da v2-iam.
    ("iam:PassRole", f"arn:aws:iam::{CONTA}:role/musclefit-apigw-cloudwatch-exec",
     {"StringEquals": {"iam:PassedToService": "apigateway.amazonaws.com"}}, "PR SEG-05"),
    # SEG-05, correcao do access log (fix/seg05-access-log-apigw): a trust do
    # musclefit-apigw-cloudwatch-exec perde a condicao aws:SourceAccount (o access log nao
    # gravava nada; a entrega nunca assumiu a role). Mudar a trust de uma role existente
    # exige UpdateAssumeRolePolicy, que hoje so existe para musclefit-migrations-exec.
    # So esta role. Publicado como v4 da v2-iam.
    ("iam:UpdateAssumeRolePolicy", f"arn:aws:iam::{CONTA}:role/musclefit-apigw-cloudwatch-exec",
     None, "PR SEG-05 access log"),
    # CloudWatch Logs, Planned Lifecycle Event de 2027-04-26: acoes de nivel log group
    # passam a ser autorizadas so pelo ARN do grupo (sem ":*"). O DeleteLogGroup do
    # grupo da migrations so estava no ARN de stream (":*", herdado da fase6). Mesma
    # acao, mesmo grupo, no formato novo; o ARN ":*" fica. Publicado como v5 da v2-compute.
    ("logs:DeleteLogGroup", _MIGRATIONS_LOG_GROUP, None, "CWLogs ARN de grupo"),
]


def tuplas_novas() -> set[tuple[str, str, str]]:
    return {(a, r, _cond_key(c)) for a, r, c, _ in ACOES_NOVAS}


# Tema de cada policy consolidada, pelo prefixo de servico da acao.
TEMAS = {
    "rede-dados": ["dynamodb", "route53", "acm", "ec2", "rds", "secretsmanager", "wafv2"],
    "compute": ["lambda", "ecr", "ecr-public", "sts", "apigateway", "events", "logs"],
    "iam": ["iam"],
    "s3-borda": ["s3", "cloudfront", "ses"],
    "observabilidade": ["budgets", "ce", "cloudtrail", "access-analyzer", "guardduty", "sns", "cloudwatch"],
}


def _lista(valor):
    return valor if isinstance(valor, list) else [valor]


def _cond_key(cond) -> str:
    return json.dumps(cond or {}, sort_keys=True, separators=(",", ":"))


def tuplas(doc) -> set[tuple[str, str, str]]:
    out = set()
    for st in doc["Statement"]:
        if st.get("Effect") != "Allow" or "NotAction" in st or "NotResource" in st:
            raise SystemExit(f"Statement fora do modelo (so Allow/Action/Resource): {st}")
        for acao in _lista(st["Action"]):
            for rec in _lista(st["Resource"]):
                out.add((acao, rec, _cond_key(st.get("Condition"))))
    return out


def retrato_tuplas() -> set[tuple[str, str, str]]:
    r = json.loads(RETRATO.read_text(encoding="utf-8"))
    docs = [v["doc"] for v in r["managed"].values()] + list(r["inline"].values())
    out = set()
    for d in docs:
        out |= tuplas(d)
    return out


def _casa(padrao_largo: str, padrao_estreito: str) -> bool:
    # fnmatchcase trata "[" como classe; nenhum ARN aqui tem "[", mas escapa por garantia.
    largo = re.sub(r"([\[\]?])", r"[\1]", padrao_largo)
    return fnmatch.fnmatchcase(padrao_estreito, largo)


def coberta(t, conjunto) -> bool:
    acao, rec, cond = t
    vazio = _cond_key(None)
    return any(
        a == acao and (c == cond or c == vazio) and _casa(r, rec)
        for (a, r, c) in conjunto
    )


# ---------------------------------------------------------------- gerar


def _sem_redundancia(ts: set) -> set:
    """Remove tupla coberta por OUTRA tupla do mesmo conjunto (ex.: GetFunction em
    function:musclefit-engines quando ja existe em function:musclefit-*)."""
    return {t for t in ts if not coberta(t, ts - {t})}


def _curto(arn: str) -> str:
    if arn == "*":
        return "Todos"
    s = re.sub(r"(:\*|/\*|\*)+$", "", arn)
    s = s.split(":::")[-1] if ":::" in s else s.split(":", 5)[-1]
    s = s.rstrip("/").rsplit("/", 1)[-1] or s
    s = s.split(":")[-1] if s.count(":") else s
    sufixo = "Objetos" if arn.startswith("arn:aws:s3:::") and arn.endswith("/*") else ""
    return re.sub(r"[^A-Za-z0-9]", "", s.replace("-", " ").title().replace(" ", "")) + sufixo


def _sid(servico: str, acoes: list[str], recursos: list[str], cond: str) -> str:
    nomes = list(dict.fromkeys(_curto(r) for r in recursos))
    alvo = nomes[0] + (f"E{len(nomes) - 1}" if len(nomes) > 1 else "")
    base = servico.title().replace("-", "") + alvo
    if cond != _cond_key(None):
        valores = "".join(str(v) for c in json.loads(cond).values() for v in c.values())
        base += re.sub(r"[^A-Za-z0-9]", "", valores.title())
    return base[:100] or "Stmt"


def gerar() -> None:
    ts = retrato_tuplas()
    ts |= tuplas_novas()
    ts = _sem_redundancia(ts)

    # Agrupamento legivel: um statement por (servico, condicao, recurso) com a
    # lista de acoes; recursos do mesmo servico com EXATAMENTE a mesma lista de
    # acoes viram um statement so (ex.: bucket e bucket/* do tfstate).
    por_recurso = defaultdict(set)
    for acao, rec, cond in ts:
        por_recurso[(acao.split(":")[0], cond, rec)].add(acao)
    grupos = defaultdict(list)
    for (servico, cond, rec), acoes in por_recurso.items():
        grupos[(servico, cond, tuple(sorted(acoes)))].append(rec)

    tema_de = {svc: tema for tema, svcs in TEMAS.items() for svc in svcs}
    por_tema = defaultdict(list)
    for (servico, cond, acoes), recs in grupos.items():
        recs = sorted(recs)
        st = {"Effect": "Allow", "Action": list(acoes) if len(acoes) > 1 else acoes[0],
              "Resource": recs if len(recs) > 1 else recs[0]}
        if cond != _cond_key(None):
            st["Condition"] = json.loads(cond)
        st = {"Sid": _sid(servico, list(acoes), recs, cond), **st}
        por_tema[tema_de[servico]].append(st)

    for tema, sts in por_tema.items():
        sts.sort(key=lambda s: (_lista(s["Action"])[0], json.dumps(s["Resource"]),
                            json.dumps(s.get("Condition"), sort_keys=True)))
        vistos = defaultdict(int)
        for st in sts:
            vistos[st["Sid"]] += 1
            if vistos[st["Sid"]] > 1:
                st["Sid"] = f"{st['Sid']}{vistos[st['Sid']]}"
        doc = {"Version": "2012-10-17", "Statement": sts}
        (DIR / f"{PREFIXO}{tema}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(por_tema)} policies geradas em {DIR}")


# ---------------------------------------------------------------- verificar


def tamanho_iam(doc) -> int:
    """IAM nao conta espaco em branco no limite de tamanho de managed policy."""
    return len(re.sub(r"\s", "", json.dumps(doc, separators=(",", ":"))))


def verificar() -> int:
    atual = retrato_tuplas()
    arquivos = sorted(DIR.glob(f"{PREFIXO}*.json"))
    proposta = set()
    erros = []
    for f in arquivos:
        doc = json.loads(f.read_text(encoding="utf-8"))
        n = tamanho_iam(doc)
        print(f"{f.name}: {len(doc['Statement'])} statements, {n} caracteres (limite {LIMITE_MANAGED})")
        if n > LIMITE_MANAGED:
            erros.append(f"{f.name} passa do limite: {n}")
        proposta |= tuplas(doc)
    if len(arquivos) > MAX_POLICIES:
        erros.append(f"{len(arquivos)} policies > {MAX_POLICIES}")

    perdidas = sorted(t for t in atual if not coberta(t, proposta))
    novas_ok = tuplas_novas()
    alargadas = sorted(t for t in proposta if not coberta(t, atual) and t not in novas_ok)
    faltam_novas = sorted(t for t in novas_ok if not coberta(t, proposta))

    acoes_atual = {a for a, _, _ in atual}
    acoes_prop = {a for a, _, _ in proposta}
    print(f"retrato: {len(atual)} tuplas, {len(acoes_atual)} acoes distintas")
    print(f"proposta: {len(proposta)} tuplas, {len(acoes_prop)} acoes distintas")
    print(f"acoes so no retrato: {sorted(acoes_atual - acoes_prop)}")
    print(f"acoes so na proposta: {sorted(acoes_prop - acoes_atual)}")

    for nome, lst in (("PERDIDAS", perdidas), ("ALARGADAS", alargadas), ("NOVAS AUSENTES", faltam_novas)):
        for t in lst:
            erros.append(f"{nome}: {t}")
    if erros:
        print("\n".join(erros))
        return 1
    print("OK: nenhuma permissao perdida, nenhuma alargada, acoes novas presentes.")
    return 0


# ---------------------------------------------------------------- tabela


def _onde(docs: dict[str, dict]) -> dict[str, list[str]]:
    """acao -> ["policy/Sid (recurso, recurso...)"]"""
    out = defaultdict(list)
    for nome, doc in docs.items():
        for st in doc["Statement"]:
            recs = ", ".join(f"`{r}`" for r in _lista(st["Resource"]))
            cond = " + condicao" if st.get("Condition") else ""
            for acao in _lista(st["Action"]):
                out[acao].append(f"{nome}/{st.get('Sid', '-')}: {recs}{cond}")
    return out


def tabela() -> None:
    r = json.loads(RETRATO.read_text(encoding="utf-8"))
    hoje = {k: v["doc"] for k, v in r["managed"].items()}
    hoje.update({f"{k} (inline)": v for k, v in r["inline"].items()})
    hoje = {k.replace("musclefit-gha-deploy-", ""): v for k, v in hoje.items()}
    prop = {f.stem.replace("musclefit-gha-deploy-", ""): json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(DIR.glob(f"{PREFIXO}*.json"))}
    a_hoje, a_prop = _onde(hoje), _onde(prop)
    origem = {a: o for a, _, _, o in ACOES_NOVAS}
    linhas = [
        "# Tabela de equivalencia acao a acao",
        "",
        "Gerada por `python scripts/iam_consolidacao.py tabela` a partir de",
        "`estado-atual-2026-10-09.json` (retrato lido da conta) e das 5 policies",
        "`musclefit-gha-deploy-v2-*.json`. Cada linha: onde a acao esta HOJE",
        "(policy/Sid: recursos) e onde fica na PROPOSTA. Prefixo",
        "`musclefit-gha-deploy-` omitido. A prova formal de que nenhuma",
        "(acao, recurso, condicao) se perde nem fica mais larga e o subcomando",
        "`verificar` (e a simulacao IAM descrita no README).",
        "",
        f"Acoes distintas hoje: {len(a_hoje)}. Na proposta: {len(a_prop)}. "
        f"Ampliacoes em relacao a hoje (unicas permitidas pelo `verificar`): "
        + "; ".join(f"`{a}` em `{r}`" + (" + condicao" if c else "") + f" ({o})"
                    for a, r, c, o in ACOES_NOVAS) + ".",
        "",
        "| Acao | Hoje | Proposta |",
        "|---|---|---|",
    ]
    for acao in sorted(set(a_hoje) | set(a_prop)):
        h = "<br>".join(a_hoje.get(acao, [])) or f"**(nova, {origem.get(acao, '?')})**"
        p = "<br>".join(a_prop.get(acao, [])) or "**(ausente)**"
        linhas.append(f"| `{acao}` | {h} | {p} |")
    (DIR / "EQUIVALENCIA.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"EQUIVALENCIA.md: {len(linhas) - 14} acoes")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verificar"
    if cmd == "gerar":
        gerar()
        tabela()
        sys.exit(verificar())
    if cmd == "tabela":
        tabela()
        sys.exit(0)
    sys.exit(verificar())
