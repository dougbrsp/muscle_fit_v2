"""A flag `DASHBOARD_PLAN_VERSION_BOUNDARY` foi aposentada em 2026-10-08 (COD-01):
o Modelo por versao/data e o unico comportamento. Este teste impede que a
variavel volte a ser definida (infra ou compose) e vire configuracao sem
consumidor - a "configuracao pela metade" do PR-14.
"""

from __future__ import annotations

from tests.support.compose_services import service_block
from tests.support.infra_flags import lambda_blocks, tf_files

_NOME = "DASHBOARD_PLAN_VERSION_BOUNDARY"


def _sem_comentarios(texto: str) -> str:
    return chr(10).join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))


def test_nenhum_arquivo_terraform_define_a_flag():
    achados = [c.name for c in tf_files() if _NOME in _sem_comentarios(c.read_text(encoding="utf-8"))]
    assert achados == [], achados


def test_nenhuma_lambda_recebe_a_flag():
    com_flag = [n for n, bloco in lambda_blocks().items() if _NOME in _sem_comentarios(bloco)]
    assert com_flag == [], com_flag


def test_nenhum_servico_do_compose_recebe_a_flag():
    for servico in ("api", "api-engines"):
        assert _NOME not in _sem_comentarios(service_block(servico)), servico
