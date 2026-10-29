"""Contrato, teto de tamanho e ordem de truncamento do `intelligence_package`
(F3.2, plano §16, §17).

O que este arquivo trava:

1. o pacote montado e VALIDO pelo contrato fechado (`extra="forbid"`): um
   campo novo num servico nao entra no pacote sem passar por aqui;
2. nenhum numero e recalculado - cada metrica do pacote e identica, valor a
   valor, ao envelope que `build_overview` produziu;
3. o teto de 40 KB e respeitado E o corte e DECLARADO em `limitations`
   (truncamento silencioso e a falha que este teste existe para impedir);
4. os grupos do pacote sao o filtro do RAG - se a lista esvaziar, o prompt
   volta a levar o dump inteiro.
"""

from __future__ import annotations

import json

import pytest

from app.intelligence.dashboard.contracts import IntelligencePackageResponse
from app.intelligence.dashboard.intelligence_package import (
    ALLOWED_ACTIONS,
    MAX_CONTEXT_EVENTS,
    PACKAGE_MAX_BYTES,
    PACKAGE_VERSION,
    TRUNCATION_ORDER,
    measure_bytes,
    package_from_overview,
    rag_target_groups,
)
from tests.support.pacote_inteligencia import (
    JANELA,
    evento,
    evidencia,
    grupo,
    insight,
    overview_sintetico,
)
from datetime import date


@pytest.fixture
def pacote():
    return package_from_overview(overview_sintetico(), question="Estou evoluindo no peito?")


def test_pacote_valida_contra_o_contrato_fechado(pacote):
    modelo = IntelligencePackageResponse.model_validate(pacote)
    assert modelo.package_version == PACKAGE_VERSION
    assert modelo.question == "Estou evoluindo no peito?"
    assert modelo.timezone == "America/Sao_Paulo"
    assert modelo.window.days == 28
    assert modelo.truncated is False


def test_nenhuma_metrica_e_recalculada(pacote):
    """Cada metrica do pacote e a COPIA de um envelope do overview - mesmo
    valor, mesma unidade, mesma versao de formula. Se alguem "arredondar" ou
    "converter" no montador, este teste reprova."""
    overview = overview_sintetico()
    por_chave = {m["key"]: m for m in pacote["metrics"]}

    consistencia = overview["blocks"]["consistency"]
    assert por_chave["consistency.rate"]["value"] == consistencia["rate"]["value"]
    assert por_chave["consistency.rate"]["unit"] == consistencia["rate"]["unit"]
    assert por_chave["consistency.rate"]["metric_version"] == consistencia["rate"]["metric_version"]

    corpo = overview["blocks"]["body"]
    assert por_chave["body.weight_ma7_change_pct"]["value"] == corpo["weight_ma7_change_pct"]["value"]

    # `value: None` continua None: "nao calculavel" nunca vira zero.
    assert por_chave["body.waist_cm"]["value"] is None
    assert por_chave["body.waist_cm"]["data_quality"].value == "INSUFFICIENT"


def test_contagens_de_progressao_entram_com_versao(pacote):
    por_chave = {m["key"]: m for m in pacote["metrics"]}
    assert por_chave["progression.count.STAGNANT"]["value"] == 9
    assert por_chave["progression.count.STAGNANT"]["metric_version"] == "exercise-progression-v1"
    assert por_chave["progression.exercises_total"]["value"] == 24


def test_evidencia_carrega_data_e_regra_de_origem(pacote):
    assert pacote["evidence"], "pacote sem evidência não sustenta conclusão nenhuma"
    for item in pacote["evidence"]:
        assert item["insight_code"], "evidência sem regra de origem é incitável"
        assert item["observed_at"] is not None, "o prompt exige citar a data"
        assert item["observed_scope"] in ("point", "window")
    codigos = {item["insight_code"] for item in pacote["evidence"]}
    assert {"MUSCLE_BELOW_MIN", "EXERCISE_STAGNANT"} <= codigos


def test_agregado_de_janela_e_datado_pela_janela_e_marcado_como_tal(pacote):
    """O defeito que o CI do #241 encontrou: 12 das 26 chamadas de `_evidence`
    em `insight_rules` nao informam `observed_at` (agregados de janela e
    constantes de plano), e o pacote repassava o `None` - entregando ao modelo
    uma evidencia que o proprio prompt o proibe de citar.

    Agora a data da janela entra, MARCADA. As tres alternativas erradas ficam
    travadas por este teste: repassar `None` (primeiro assert), descartar o
    item (segundo) e apresentar um agregado como medicao de um dia (terceiro)."""
    por_metrica = {item["metric"]: item for item in pacote["evidence"]}

    agregado = por_metrica["weekly_effective_sets[Peito]"]
    assert agregado["observed_at"] is not None
    assert agregado["observed_scope"] == "window"
    assert agregado["observed_from"] == JANELA.date_from
    assert agregado["observed_at"] == JANELA.date_to

    pontual = por_metrica["weekly_effective_sets"]
    assert pontual["observed_scope"] == "point"
    assert pontual["observed_from"] is None, "evidência pontual não tem período"
    assert pontual["observed_at"] == date(2026, 9, 20), "a data da regra foi preservada"


def test_grupos_do_pacote_sao_o_filtro_do_rag(pacote):
    grupos = rag_target_groups(pacote)
    assert grupos == ["Perna", "Costas", "Peito"], "ordem por volume, do maior para o menor"
    assert all(isinstance(nome, str) and nome for nome in grupos)


def test_acoes_permitidas_sao_vocabulario_fechado(pacote):
    codigos = [a["code"] for a in pacote["allowed_actions"]]
    assert codigos == [a["code"] for a in ALLOWED_ACTIONS]
    assert "NO_ACTION" in codigos, "sem ela o modelo inventa ajuste quando está tudo bem"
    assert len(set(codigos)) == len(codigos)


def test_baselines_trazem_o_antes_sem_calcular_de_novo(pacote):
    por_chave = {b["key"]: b for b in pacote["baselines"]}
    # 81.0 (média móvel atual) - (-0.6) (variação) = 81.6 no início da janela.
    assert por_chave["body.weight_ma7_first"]["value"] == 81.6
    assert por_chave["body.weight_ma7_first"]["basis"] == "personal_baseline_window"
    assert por_chave["muscles.Peito.target_min"]["value"] == 10.0


def test_limitacao_de_escopo_sempre_declarada(pacote):
    assert any("não contém série a série" in linha for linha in pacote["limitations"])


def test_contexto_indisponivel_e_declarado():
    pacote = package_from_overview(overview_sintetico(context_available=False))
    assert any("não estão disponíveis" in linha for linha in pacote["limitations"])


def test_pacote_normal_cabe_com_folga(pacote):
    assert pacote["size_bytes"] < PACKAGE_MAX_BYTES
    # O payload FINAL (com `size_bytes` escrito) tambem cabe - a diferenca
    # entre a medicao e o payload final e de dezenas de bytes, e e por isso
    # que o teto e verificado nos dois.
    assert measure_bytes(pacote) <= PACKAGE_MAX_BYTES


def test_pior_caso_plausivel_cabe_no_teto_sem_truncar():
    """A defesa PRIMARIA do teto nao e o corte por bytes - sao os tetos por
    lista (`MAX_CONTEXT_EVENTS`, `MAX_EVIDENCE`, `MAX_MUSCLE_GROUPS`,
    `MAX_NOTES_CHARS`). Este teste monta o pior caso que esses tetos ainda
    permitem (todas as listas cheias, toda anotacao no comprimento maximo) e
    exige que ele caiba nos 40 KB **sem** truncar.

    Se um dia isso falhar, a leitura e: os tetos por lista ficaram largos
    demais para o teto global, e o pacote passou a depender do corte por
    bytes - que e a rede, nao o plano."""
    eventos = [evento("alcohol", date(2026, 9, 1), notes="x" * 600) for _ in range(300)]
    insights = [
        insight(f"REGRA_{i}", evidence=[evidencia("metrica_com_nome_longo", 10.0, 9.0, unit="sets")])
        for i in range(60)
    ]
    grupos = [grupo(i, f"Grupo Muscular Numero {i}", series=float(30 - i)) for i in range(1, 40)]
    pacote = package_from_overview(
        overview_sintetico(context_events=eventos, insights=insights, muscle_items=grupos),
        question="p" * 500,
    )

    assert pacote["truncated"] is False, "o pior caso plausível não deveria precisar do corte por bytes"
    assert measure_bytes(pacote) <= PACKAGE_MAX_BYTES, measure_bytes(pacote)


def test_truncamento_declara_o_corte_e_a_ordem():
    """O corte por bytes exercitado num teto pequeno - a unica forma honesta
    de exercita-lo, ja que o pior caso plausivel cabe nos 40 KB (acima)."""
    eventos = [evento("alcohol", date(2026, 9, 1), notes="x" * 600) for _ in range(30)]
    pacote = package_from_overview(
        overview_sintetico(context_events=eventos), max_bytes=14 * 1024
    )

    assert pacote["truncated"] is True
    assert measure_bytes(pacote) <= 14 * 1024
    declaracao = [linha for linha in pacote["limitations"] if "truncado" in linha.lower()]
    assert declaracao, "corte silencioso: o pacote encolheu e nada foi declarado"
    assert " > ".join(TRUNCATION_ORDER) in declaracao[0]
    assert "context_events" in declaracao[0]


def test_truncamento_nunca_come_metrica_nem_baseline():
    eventos = [evento("alcohol", date(2026, 9, 1), notes="x" * 600) for _ in range(30)]
    cheio = package_from_overview(overview_sintetico(context_events=eventos), max_bytes=14 * 1024)
    vazio = package_from_overview(overview_sintetico(context_events=[]))
    assert cheio["truncated"] is True, "sem truncamento este teste não prova nada"
    assert [m["key"] for m in cheio["metrics"]] == [m["key"] for m in vazio["metrics"]]
    assert [b["key"] for b in cheio["baselines"]] == [b["key"] for b in vazio["baselines"]]


def test_anotacao_livre_do_usuario_nao_estoura_o_pacote_sozinha():
    """Texto de usuario e a unica parte do pacote com tamanho imprevisivel.
    Ele e cortado por evento, ANTES da medicao - senao uma unica anotacao
    gigante expulsaria metricas do pacote pela porta dos fundos."""
    pacote = package_from_overview(
        overview_sintetico(context_events=[evento("travel", date(2026, 9, 5), notes="y" * 5000)])
    )
    assert pacote["truncated"] is False
    assert len(pacote["context_events"][0]["notes"]) < 400


def test_teto_por_lista_limita_eventos():
    eventos = [evento("alcohol", date(2026, 9, 1), notes="curto") for _ in range(200)]
    pacote = package_from_overview(overview_sintetico(context_events=eventos))
    assert len(pacote["context_events"]) <= MAX_CONTEXT_EVENTS


def test_pacote_serializa_em_json_puro(pacote):
    """O pacote viaja como JSON (prompt e HTTP). Data, enum e `WindowInfo`
    precisam sair serializaveis - um `TypeError` aqui so apareceria em
    producao, dentro do job."""
    modelo = IntelligencePackageResponse.model_validate(pacote)
    texto = json.dumps(modelo.model_dump(mode="json"), ensure_ascii=False)
    assert "2026-09-26" in texto
