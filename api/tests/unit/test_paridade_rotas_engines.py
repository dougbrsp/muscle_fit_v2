"""Paridade de roteamento da Lambda `musclefit-engines` entre o codigo
(`app/main_engines.py` e seus routers), o API Gateway (`infra/api_gateway.tf`)
e o proxy de DEV (`nginx/local-dev.conf`).

Motivacao (o bug que originou este arquivo): o M7 criou
`GET /analytics/dashboard-progression-by-muscle-group` em `app/analytics.py`
com teste unitario verde, mas nao registrou o path em NENHUMA das duas listas
de roteamento. Como `/analytics/*` nao tem catch-all para `musclefit-engines`
- cada path vira um `aws_api_gateway_resource` explicito - a requisicao caia
no catch-all de `musclefit-api-core`, que nao conhece a rota: 404 em DEV e em
producao. O painel `/dashboard/progressao` ficou sem dados, sem erro nenhum na
suite de testes e sem erro visivel na Lambda certa.

Os tres lados sao verificados:

1. todo path dos routers de `musclefit-engines` esta em `infra/api_gateway.tf`
   (senao o endpoint nasce inalcancavel em producao);
2. o mesmo vale para `nginx/local-dev.conf` (senao nasce inalcancavel em DEV,
   e o defeito so aparece depois do deploy);
3. as duas listas nao tem sobra: um path removido/renomeado no codigo nao pode
   continuar criando resource no API Gateway apontando para lugar nenhum.
"""

from __future__ import annotations

import pytest

from tests.support.infra_engines_routes import (
    PREFIXO_PARA_LOCAL_TF,
    paths_do_nginx,
    paths_do_terraform,
    paths_dos_routers,
)


@pytest.fixture(scope="module")
def rotas():
    return paths_dos_routers()


@pytest.mark.parametrize("prefixo", sorted(PREFIXO_PARA_LOCAL_TF))
def test_todo_path_do_router_esta_no_api_gateway(rotas, prefixo):
    faltando = sorted(rotas[prefixo] - paths_do_terraform()[prefixo])
    assert not faltando, (
        f"paths de `{prefixo}/*` implementados nos routers de musclefit-engines "
        f"mas ausentes do local `{PREFIXO_PARA_LOCAL_TF[prefixo]}` em "
        f"infra/api_gateway.tf: {faltando}. Sem o resource explicito a "
        "requisicao cai no catch-all de musclefit-api-core e responde 404 em "
        "producao."
    )


@pytest.mark.parametrize("prefixo", sorted(PREFIXO_PARA_LOCAL_TF))
def test_todo_path_do_router_esta_no_nginx_de_dev(rotas, prefixo):
    faltando = sorted(rotas[prefixo] - paths_do_nginx()[prefixo])
    assert not faltando, (
        f"paths de `{prefixo}/*` ausentes de nginx/local-dev.conf: {faltando}. "
        "O proxy de DEV replica o roteamento do API Gateway e e transcrito a "
        "mao - ver o aviso no topo do arquivo."
    )


@pytest.mark.parametrize("prefixo", sorted(PREFIXO_PARA_LOCAL_TF))
def test_terraform_e_nginx_nao_divergem(prefixo):
    tf = paths_do_terraform()[prefixo]
    nginx = paths_do_nginx()[prefixo]
    assert tf == nginx, (
        f"divergencia em `{prefixo}/*` entre infra/api_gateway.tf e "
        f"nginx/local-dev.conf - so no Terraform: {sorted(tf - nginx)}; "
        f"so no nginx: {sorted(nginx - tf)}."
    )


@pytest.mark.parametrize("prefixo", sorted(PREFIXO_PARA_LOCAL_TF))
def test_listas_de_roteamento_sem_sobra(rotas, prefixo):
    """Path registrado na infra que nenhum router de engines implementa mais.

    `/analytics/health` e o health check do proprio router `analytics.py`
    (nao o `/health` da Lambda), entao aparece nos dois lados normalmente."""
    sobra = sorted(paths_do_terraform()[prefixo] - rotas[prefixo])
    assert not sobra, (
        f"paths em `{PREFIXO_PARA_LOCAL_TF[prefixo]}` (infra/api_gateway.tf) "
        f"sem endpoint correspondente nos routers de musclefit-engines: "
        f"{sobra}. Se foram removidos/renomeados no codigo, remova tambem das "
        "duas listas de roteamento."
    )
