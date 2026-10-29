"""`GET /analytics/dashboard/intelligence-package` (F3.2) contra MySQL real,
nos DOIS backends.

O que este arquivo prova, e que o teste de unidade (que usa um overview
sintetico) nao pode provar:

- o `build_overview` DE VERDADE produz um pacote valido pelo contrato fechado;
- o pacote cabe no teto de 40 KB com dados reais;
- **isolamento**: o pacote do usuario B nao contem nenhum numero, nome de
  exercicio ou evento do usuario A. Esta e a afirmacao mais importante do
  arquivo - o pacote e o objeto que vai INTEIRO para dentro de um prompt, e um
  vazamento aqui e um vazamento para fora do sistema;
- nao existe parametro para pedir o pacote de outra pessoa (o escopo e
  estrutural, nao uma checagem que se pode esquecer de aplicar).

O seed e o mesmo de `test_dashboard_overview.py` - importado, nao copiado: o
pacote e um recorte daquela leitura, e dois seeds divergiriam em silencio.
"""

from __future__ import annotations

import json

import pytest

from app import analytics_dashboard
from app.intelligence.dashboard.contracts import IntelligencePackageResponse
from app.intelligence.dashboard.intelligence_package import (
    PACKAGE_MAX_BYTES,
    PACKAGE_VERSION,
)
from tests.support.dataapi_backend import BACKENDS, use_backend

from .test_dashboard_overview import _client, seed  # noqa: F401 - fixture reutilizada


@pytest.fixture(autouse=True)
def sem_rate_limit():
    """O contador de acessos vive na memoria do processo e os ids de usuario
    se repetem entre testes (TRUNCATE reinicia o AUTO_INCREMENT). Sem esta
    limpeza, o 12o teste do arquivo levaria 429 por causa do 1o."""
    analytics_dashboard._package_hits.clear()
    yield
    analytics_dashboard._package_hits.clear()


def _pacote(quem: int, **params):
    """`quem` e a IDENTIDADE da requisicao; `params` vai como query string.

    O nome nao e `user_id` de proposito: um dos testes manda `user_id=` como
    query param (para provar que a rota o ignora), e com o nome antigo a
    chamada virava `TypeError: got multiple values for argument 'user_id'` -
    defeito do helper, nao do produto."""
    return _client(quem).get("/analytics/dashboard/intelligence-package", params=params)


def test_payload_identico_nos_dois_backends(seed, monkeypatch, fake_rds, mysql_schema):  # noqa: F811 - fixture do pytest importada (falso positivo)
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _pacote(seed["dono"])
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]

    corpo = resultados["pymysql"]
    assert corpo["package_version"] == PACKAGE_VERSION
    assert corpo["window"]["kind"] == "28d"
    assert corpo["timezone"] == "America/Sao_Paulo"
    assert corpo["metrics"], "pacote sem métrica nenhuma não responde pergunta nenhuma"
    assert corpo["allowed_actions"], "sem vocabulário fechado o modelo sugere qualquer coisa"
    # O contrato e fechado: se um servico ganhar campo novo e alguem o enfiar
    # no pacote sem passar pelo contrato, a validacao abaixo reprova.
    IntelligencePackageResponse.model_validate(corpo)


def test_pacote_real_cabe_no_teto(seed, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    corpo = _pacote(seed["dono"]).json()
    tamanho = len(json.dumps(corpo, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert tamanho <= PACKAGE_MAX_BYTES, tamanho
    assert corpo["size_bytes"] <= PACKAGE_MAX_BYTES


def test_toda_metrica_carrega_natureza_versao_e_confianca(seed, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    corpo = _pacote(seed["dono"]).json()
    for metrica in corpo["metrics"]:
        assert metrica["nature"], metrica["key"]
        assert metrica["metric_version"] and metrica["metric_version"] != "unknown", metrica["key"]
        assert metrica["data_quality"], metrica["key"]


def test_evidencia_real_tem_data_citavel(seed, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Toda evidencia precisa de uma data que o modelo POSSA citar - o prompt
    exige isso. Com dados reais aparecem os dois alcances: `insight_rules`
    data a evidencia principal e deixa os agregados de janela sem data (12 das
    26 chamadas de `_evidence`), e o pacote os completa com a janela, marcados.

    Este teste reprovou na primeira rodada do #241 com `assert None is not
    None` - e o certo era corrigir o pacote, nao afrouxar a assercao."""
    corpo = _pacote(seed["dono"]).json()
    assert corpo["evidence"], "cenário sem evidência não provaria nada"
    for item in corpo["evidence"]:
        assert item["insight_code"]
        assert item["observed_at"] is not None, item["metric"]
        assert item["observed_scope"] in ("point", "window"), item["metric"]
        if item["observed_scope"] == "window":
            assert item["observed_from"] == corpo["window"]["date_from"], item["metric"]
            assert item["observed_at"] == corpo["window"]["date_to"], item["metric"]
        else:
            assert item["observed_from"] is None, item["metric"]


def test_isolamento_o_pacote_do_outro_nao_contem_nada_do_dono(seed, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """A afirmacao central: o pacote vai inteiro para dentro de um prompt.
    O usuario B (sem historico) recebe um pacote VALIDO e vazio de conteudo -
    nao um pedaco do usuario A."""
    dono = _pacote(seed["dono"]).json()
    outro = _pacote(seed["outro"]).json()

    IntelligencePackageResponse.model_validate(outro)
    assert outro["evidence"] == []
    assert outro["muscle_groups"] == []

    por_chave = {m["key"]: m for m in outro["metrics"]}
    assert por_chave["progression.exercises_total"]["value"] == 0
    assert por_chave["consistency.completed_sessions"]["value"] == 0

    # Nenhum dos numeros nao triviais do dono aparece no pacote do outro.
    texto_outro = json.dumps(outro, ensure_ascii=False)
    do_dono = {
        str(m["value"])
        for m in dono["metrics"]
        if isinstance(m["value"], (int, float)) and abs(m["value"]) > 3
    }
    vazados = sorted(v for v in do_dono if f":{v}" in texto_outro)
    assert not vazados, f"valores do usuário A no pacote do usuário B: {vazados}"


def test_nao_existe_parametro_para_pedir_o_pacote_de_outro_usuario(seed, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    """Escopo ESTRUTURAL: `user_id` nao e um filtro que alguem possa esquecer
    de aplicar - ele simplesmente nao e aceito. Um query param desconhecido e
    ignorado pelo FastAPI, entao o pacote continua sendo o de quem pediu."""
    corpo = _pacote(seed["outro"], user_id=seed["dono"]).json()
    por_chave = {m["key"]: m for m in corpo["metrics"]}
    assert por_chave["progression.exercises_total"]["value"] == 0

    rota = [r for r in analytics_dashboard.router.routes if r.path.endswith("/intelligence-package")][0]
    assert "user_id" not in {p.name for p in rota.dependant.query_params}


def test_pergunta_viaja_no_pacote(seed, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    corpo = _pacote(seed["dono"], question="Estou evoluindo?").json()
    assert corpo["question"] == "Estou evoluindo?"


def test_janela_invalida_responde_400(seed, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    assert _pacote(seed["dono"], window="1y").status_code == 400


def test_rate_limit_responde_429(seed, db_backend):  # noqa: F811 - fixture do pytest importada (falso positivo)
    limite = analytics_dashboard.PACKAGE_RATE_LIMIT
    analytics_dashboard._package_hits[seed["dono"]] = [
        __import__("time").monotonic() for _ in range(limite)
    ]
    assert _pacote(seed["dono"]).status_code == 429
