"""Testes de scripts/iam_consolidacao.py (AWS-13). Locais, sem AWS:
`python -m pytest -q scripts/test_iam_consolidacao.py`."""

import json

import iam_consolidacao as ic


def _proposta():
    out = set()
    for f in sorted(ic.DIR.glob(f"{ic.PREFIXO}*.json")):
        out |= ic.tuplas(json.loads(f.read_text(encoding="utf-8")))
    return out


def test_proposta_versionada_passa_na_verificacao():
    assert ic.verificar() == 0


def test_no_maximo_cinco_policies_e_cada_uma_no_limite():
    arquivos = sorted(ic.DIR.glob(f"{ic.PREFIXO}*.json"))
    assert 1 <= len(arquivos) <= ic.MAX_POLICIES
    for f in arquivos:
        assert ic.tamanho_iam(json.loads(f.read_text(encoding="utf-8"))) <= ic.LIMITE_MANAGED, f.name


def test_toda_tupla_de_hoje_continua_coberta():
    prop = _proposta()
    assert [t for t in ic.retrato_tuplas() if not ic.coberta(t, prop)] == []


def test_nada_fica_mais_largo_alem_das_acoes_novas():
    atual = ic.retrato_tuplas()
    novas = ic.tuplas_novas()
    assert [t for t in _proposta() if not ic.coberta(t, atual) and t not in novas] == []


def test_acoes_novas_presentes_com_o_escopo_exato():
    prop = _proposta()
    assert [t for t in ic.tuplas_novas() if not ic.coberta(t, prop)] == []


def test_ampliacoes_sao_exatamente_as_dos_prs_438_455_456_471_e_alarme_root():
    acoes = {(a, o) for a, _, _, o in ic.ACOES_NOVAS}
    assert acoes == {
        ("s3:PutBucketPolicy", "PR #471"),
        ("s3:PutLifecycleConfiguration", "PR #471"),
        ("logs:ListTagsForResource", "PR #471"),
        ("logs:PutRetentionPolicy", "PR #471"),
        ("logs:TagResource", "PR #471"),
        ("lambda:PutFunctionConcurrency", "PR #438"),
        ("lambda:DeleteFunctionConcurrency", "PR #438"),
        ("lambda:GetFunctionConcurrency", "PR #438"),
        ("ecr-public:GetAuthorizationToken", "PR #455"),
        ("sts:GetServiceBearerToken", "PR #455"),
        ("ecr:PutImageTagMutability", "PR #456"),
        ("ecr:DeleteLifecyclePolicy", "PR #456"),
        *{(a, "PR alarme root") for a in ic._EVENTS_REGRA},
        ("iam:PassRole", "PR alarme root"),
        *{(a, "PR SEG-05") for a in ic._WAF_ACL_ACOES},
        ("wafv2:GetWebACLForResource", "PR SEG-05"),
        *{(a, "PR SEG-05") for a in ic._APIGW_LOGS_ACOES},
        ("apigateway:SetWebACL", "PR SEG-05"),
        ("apigateway:GET", "PR SEG-05"),
        ("apigateway:PATCH", "PR SEG-05"),
        ("iam:PassRole", "PR SEG-05"),
        ("iam:UpdateAssumeRolePolicy", "PR SEG-05 access log"),
        ("logs:DeleteLogGroup", "CWLogs ARN de grupo"),
    }


def test_novas_batem_com_os_documentos_de_origem():
    """As tuplas novas sao exatamente as do aws12 (versionado) e as do aws14 (PR #456),
    descontado o que a role ja tem hoje (DeleteLifecyclePolicy em musclefit-engines)."""
    aws12 = ic.tuplas(json.loads(
        (ic.RAIZ / "infra/iam-policies/musclefit-gha-deploy-aws12-ecr-public-pull.json").read_text(encoding="utf-8")))
    novas = ic.tuplas_novas()
    assert aws12 <= novas
    # sts:GetServiceBearerToken so com a condicao de servico ecr-public.
    sts = [t for t in novas if t[0] == "sts:GetServiceBearerToken"]
    assert sts == [("sts:GetServiceBearerToken", "*",
                    ic._cond_key({"StringEquals": {"sts:AWSServiceName": "ecr-public.amazonaws.com"}}))]
    # Nenhuma acao de ECR nova em curinga.
    assert all("*" not in r for a, r, _ in novas if a.startswith("ecr:"))


def test_ampliacoes_do_pr_471_ficam_nos_recursos_exatos():
    """FIN-04/AWS-18: S3 so nos dois buckets nomeados (sem curinga) e logs so nos
    grupos exportados pelo cluster musclefit-db."""
    novas = [(a, r) for a, r, c, o in ic.ACOES_NOVAS if o == "PR #471"]
    assert sorted(r for a, r in novas if a.startswith("s3:")) == [
        "arn:aws:s3:::musclefit-frontend-assets",
        "arn:aws:s3:::musclefit-frontend-assets",
        "arn:aws:s3:::musclefit-rag",
    ]
    assert {r for a, r in novas if a.startswith("logs:")} == {
        "arn:aws:logs:sa-east-1:930413537709:log-group:/aws/rds/cluster/musclefit-db/*",
    }
    assert all(c is None for a, r, c, o in ic.ACOES_NOVAS if o == "PR #471")


def test_ampliacoes_do_alarme_root_ficam_no_prefixo_e_nas_tres_regioes():
    """Alarme de uso do root (infra/alerta_root.tf): regras musclefit-* so em
    us-east-1/us-east-2/us-west-2 (sem curinga de regiao, sem Enable/DisableRule) e
    PassRole so da role de encaminhamento, so para o EventBridge."""
    novas = [(a, r, c) for a, r, c, o in ic.ACOES_NOVAS if o == "PR alarme root"]
    events = [(a, r) for a, r, c in novas if a.startswith("events:")]
    assert {r for _, r in events} == {
        f"arn:aws:events:{reg}:930413537709:rule/musclefit-*"
        for reg in ("us-east-1", "us-east-2", "us-west-2")
    }
    assert len(events) == 9 * 3
    assert not {a for a, _ in events} & {"events:EnableRule", "events:DisableRule", "events:PutEvents"}
    assert all(c is None for a, r, c in novas if a.startswith("events:"))
    passrole = [(r, c) for a, r, c in novas if a == "iam:PassRole"]
    assert passrole == [(
        "arn:aws:iam::930413537709:role/musclefit-root-alert-forward-exec",
        {"StringEquals": {"iam:PassedToService": "events.amazonaws.com"}},
    )]


def test_cobertura_detecta_perda_e_alargamento():
    vazio = ic._cond_key(None)
    estreito = ("lambda:GetFunction", "arn:aws:lambda:sa-east-1:1:function:musclefit-engines", vazio)
    largo = ("lambda:GetFunction", "arn:aws:lambda:sa-east-1:1:function:musclefit-*", vazio)
    assert ic.coberta(estreito, {largo})
    assert not ic.coberta(largo, {estreito})
    # Padrao com "*" no meio do mais estreito continua coberto pelo mais largo.
    assert ic.coberta(
        ("logs:DeleteLogGroup", "arn:aws:logs:sa-east-1:1:log-group:/aws/lambda/musclefit-engines*", vazio),
        {("logs:DeleteLogGroup", "arn:aws:logs:sa-east-1:1:log-group:/aws/lambda/musclefit-*", vazio)},
    )
    # Statement sem condicao cobre o com condicao; o contrario nao.
    cond = ic._cond_key({"StringEquals": {"aws:RequestedRegion": "sa-east-1"}})
    assert ic.coberta(("ec2:CreateVpc", "*", cond), {("ec2:CreateVpc", "*", vazio)})
    assert not ic.coberta(("ec2:CreateVpc", "*", vazio), {("ec2:CreateVpc", "*", cond)})
    # Acao diferente nunca cobre.
    assert not ic.coberta(("lambda:DeleteFunction",) + largo[1:], {largo})


def test_ampliacoes_do_seg05_ficam_no_escopo_do_projeto():
    """SEG-05/AWS-08/AWS-10 (infra/api_gateway_borda.tf): WAF so em Web ACL musclefit-* da
    sa-east-1 (managed rule set so para Create/Update), logs so nos grupos do API Gateway,
    /account so GET/PATCH e PassRole so do role de CloudWatch, so para o API Gateway."""
    novas = [(a, r, c) for a, r, c, o in ic.ACOES_NOVAS if o == "PR SEG-05"]
    waf = {(a, r) for a, r, c in novas if a.startswith("wafv2:")}
    assert {r for _, r in waf} == {
        "arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/musclefit-*/*",
        "arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/*/*",
        "arn:aws:wafv2:sa-east-1:930413537709:regional/managedruleset/*/*",
    }
    assert {a for a, r in waf if "managedruleset" in r} == {"wafv2:CreateWebACL", "wafv2:UpdateWebACL"}
    # Fora de musclefit-*, SO a leitura da associacao (o WAF autoriza GetWebACLForResource
    # contra o ARN generico regional/webacl/*/*; apply do SEG-05, run 38149166830).
    assert {a for a, r in waf if r.endswith("regional/webacl/*/*")} == {"wafv2:GetWebACLForResource"}
    assert ("wafv2:GetWebACLForResource",
            "arn:aws:wafv2:sa-east-1:930413537709:regional/webacl/musclefit-*/*") not in waf
    assert not {a for a, _ in waf} & {"wafv2:PutLoggingConfiguration", "wafv2:CreateRuleGroup",
                                       "wafv2:CreateIPSet", "wafv2:PutPermissionPolicy"}
    logs = {r for a, r, c in novas if a.startswith("logs:")}
    assert logs == {
        "arn:aws:logs:sa-east-1:930413537709:log-group:/aws/apigateway/musclefit-*",
        "arn:aws:logs:sa-east-1:930413537709:log-group:API-Gateway-Execution-Logs_vaercx9o3g/*",
    }
    apigw = {(a, r) for a, r, c in novas if a.startswith("apigateway:")}
    assert apigw == {
        ("apigateway:SetWebACL", "arn:aws:apigateway:sa-east-1::/restapis/*/stages/*"),
        ("apigateway:GET", "arn:aws:apigateway:sa-east-1::/account"),
        ("apigateway:PATCH", "arn:aws:apigateway:sa-east-1::/account"),
    }
    assert [(r, c) for a, r, c in novas if a == "iam:PassRole"] == [(
        "arn:aws:iam::930413537709:role/musclefit-apigw-cloudwatch-exec",
        {"StringEquals": {"iam:PassedToService": "apigateway.amazonaws.com"}},
    )]
    assert all(c is None for a, r, c in novas if a != "iam:PassRole")


def test_ampliacao_do_access_log_e_so_a_trust_do_role_de_cloudwatch():
    """SEG-05, correcao do access log: a role de deploy so ganha UpdateAssumeRolePolicy, so no
    musclefit-apigw-cloudwatch-exec, sem curinga (a trust perde aws:SourceAccount)."""
    novas = [(a, r, c) for a, r, c, o in ic.ACOES_NOVAS if o == "PR SEG-05 access log"]
    assert novas == [(
        "iam:UpdateAssumeRolePolicy",
        "arn:aws:iam::930413537709:role/musclefit-apigw-cloudwatch-exec",
        None,
    )]
    doc = json.loads((ic.DIR / "musclefit-gha-deploy-v2-iam.json").read_text(encoding="utf-8"))
    alvo = [st for st in doc["Statement"] if "iam:UpdateAssumeRolePolicy" in ic._lista(st["Action"])]
    assert sorted(ic._lista(st["Resource"])[0] for st in alvo) == [
        "arn:aws:iam::930413537709:role/musclefit-apigw-cloudwatch-exec",
        "arn:aws:iam::930413537709:role/musclefit-migrations-exec",
    ]
    assert all(len(ic._lista(st["Resource"])) == 1 and "Condition" not in st for st in alvo)


def test_cwlogs_acoes_de_grupo_tem_arn_de_grupo():
    """CloudWatch Logs (lifecycle event de 2027-04-26): acao de nivel log group so vale no
    ARN do grupo. Todo recurso de logs em formato de stream (":*" ou ":log-stream:") com
    acao fora das 4 de stream precisa do ARN de grupo equivalente no mesmo statement."""
    so_stream = {"logs:PutLogEvents", "logs:GetLogEvents", "logs:CreateLogStream",
                 "logs:DeleteLogStream"}
    faltando = []
    for f in sorted(ic.DIR.glob(f"{ic.PREFIXO}*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for st in doc["Statement"]:
            if st.get("Effect") != "Allow":
                continue
            acoes = set(ic._lista(st["Action"]))
            if not any(a.startswith("logs:") and a not in so_stream for a in acoes):
                continue
            recursos = ic._lista(st["Resource"])
            for r in recursos:
                if not r.startswith("arn:aws:logs:"):
                    continue
                if r.endswith(":*"):
                    grupo = r[:-2]
                elif ":log-stream:" in r:
                    grupo = r.split(":log-stream:")[0]
                else:
                    continue
                if grupo not in recursos:
                    faltando.append((f.name, st.get("Sid"), r))
    assert faltando == []
