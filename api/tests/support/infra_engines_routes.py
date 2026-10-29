"""Paths de roteamento da Lambda `musclefit-engines` como o Terraform e o
nginx de DEV os enxergam.

Contexto: `/analytics/*` e `/exercises/*` NAO tem catch-all para
`musclefit-engines`. Cada path precisa virar um `aws_api_gateway_resource`
explicito, e a lista disso esta em `infra/api_gateway.tf`, nos locals
`engines_analytics_path_parts` e `engines_exercise_path_parts`. O
`nginx/local-dev.conf` e a transcricao manual dessa mesma lista para DEV.
Quem nao esta nas duas listas cai no catch-all de `musclefit-api-core`, que
nao tem a rota - ou seja, 404 em DEV e em producao, mesmo com o endpoint
implementado e testado no pacote `app/`.

Mesma tecnica (e mesmas limitacoes) de `tests/support/infra_rag.py`: parser
textual, sem `terraform` disponivel no CI de testes de unidade. Este modulo
so le `infra/` e `nginx/` - nao altera nada.
"""

from __future__ import annotations

import re
from pathlib import Path

# Routers montados em `app/main_engines.py` (`app.include_router(...)`). Se um
# router novo entrar la, ele entra aqui tambem - senao os paths dele ficam
# fora da verificacao de paridade.
ENGINES_ROUTER_MODULES = (
    "app.analytics",
    "app.analytics_dashboard",
    "app.dashboard_modules",
    "app.dashboard_intelligence",
    "app.engines_extra",
)

# Prefixos que o API Gateway roteia path a path (sem catch-all) e o local de
# `infra/api_gateway.tf` que guarda a lista de cada um.
PREFIXO_PARA_LOCAL_TF = {
    "/analytics": "engines_analytics_path_parts",
    "/exercises": "engines_exercise_path_parts",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _tf_text() -> str:
    return (repo_root() / "infra" / "api_gateway.tf").read_text(encoding="utf-8")


def _nginx_text() -> str:
    return (repo_root() / "nginx" / "local-dev.conf").read_text(encoding="utf-8")


def paths_dos_routers() -> dict[str, set[str]]:
    """Primeiro segmento apos o prefixo, por prefixo, lido dos routers REAIS
    (nao do texto-fonte): importa os modulos de `ENGINES_ROUTER_MODULES` e
    percorre `router.routes`, entao um decorator escrito de forma diferente
    ou um path montado dinamicamente continua sendo visto."""
    from importlib import import_module

    por_prefixo: dict[str, set[str]] = {p: set() for p in PREFIXO_PARA_LOCAL_TF}
    for nome in ENGINES_ROUTER_MODULES:
        router = import_module(nome).router
        for rota in router.routes:
            caminho = getattr(rota, "path", "")
            for prefixo in por_prefixo:
                if caminho.startswith(prefixo + "/"):
                    resto = caminho[len(prefixo) + 1 :]
                    if resto:
                        por_prefixo[prefixo].add(resto.split("/")[0])
    return por_prefixo


def paths_do_terraform() -> dict[str, set[str]]:
    """Conteudo de cada local de `infra/api_gateway.tf`."""
    texto = _tf_text()
    resultado: dict[str, set[str]] = {}
    for prefixo, nome_local in PREFIXO_PARA_LOCAL_TF.items():
        bloco = re.search(rf"{nome_local}\s*=\s*\[(.*?)\n\s*\]", texto, re.S)
        assert bloco, f"local `{nome_local}` nao encontrado em infra/api_gateway.tf"
        resultado[prefixo] = set(re.findall(r'"([^"]+)"', bloco.group(1)))
    return resultado


def paths_do_nginx() -> dict[str, set[str]]:
    """`location` de `nginx/local-dev.conf`, nas DUAS formas que o roteamento
    de DEV usa:

    - `location = /<prefixo>/<parte>` - match EXATO, a forma da maioria das
      rotas (um path, um endpoint);
    - `location /<prefixo>/<parte>/` - match por PREFIXO, necessario desde que
      existe rota com segmento dinamico no meio
      (`/analytics/dashboard/exercises/{exercise_id}/progress`): um `location =`
      nunca casaria com o path completo, e enumerar cada id seria impossivel.
      No API Gateway o equivalente e um `{proxy+}` sob o resource da parte.
    """
    texto = _nginx_text()
    return {
        prefixo: set(re.findall(rf"location = {prefixo}/([\w-]+)", texto))
        | set(re.findall(rf"location {prefixo}/([\w-]+)/", texto))
        for prefixo in PREFIXO_PARA_LOCAL_TF
    }
