"""`body_service` (Novo Dashboard F1.4): funcoes puras do corpo.

- `weight-ma-v1`: dias sem pesagem nao viram zero, prioridade de fonte por
  dia, media so sobre dias com pesagem (as funcoes sao as MESMAS que a visao
  geral usa - `test_overview_usa_o_mesmo_servico_de_corpo`);
- `waist-v1`: fora de protocolo -> LOW e fora do sinal; "ultima cintura" e a
  ultima no protocolo;
- bioimpedancia: uma leitura isolada nao confirma tendencia (ESTIMATED);
- `recomp-v1` (§16.5): `compatible` / `not_compatible` / `insufficient_data`,
  sempre com `evidence[]` e `limitations[]`, texto "compativel com", nunca
  "confirmada";
- regra de insight com e sem cintura; contratos fechados validam o payload.

Rodar: cd api && python -m pytest tests/unit/test_body_service.py
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence.dashboard import body_service as bs
from app.intelligence.dashboard import insight_rules as ir
from app.intelligence.dashboard import overview_service as ov
from app.intelligence.dashboard.contracts import (
    BodyBlockOut,
    BodyResponse,
    DataNature,
    DataQuality,
    Insight,
    RecompositionSignalOut,
    WindowInfo,
    WindowKind,
)
from app.intelligence.dashboard.metrics_registry import THRESHOLDS, ExerciseProgressionStatus
from tests.support.fake_db import CursorFactoryConn

DATE_TO = date(2026, 9, 22)


def _window(days: int = 28, kind: WindowKind = WindowKind.ROLLING_28D) -> WindowInfo:
    return WindowInfo(
        kind=kind,
        date_from=DATE_TO - timedelta(days=days - 1),
        date_to=DATE_TO,
        timezone="America/Sao_Paulo",
        days=days,
    )


def _weight_row(day: date, kg: float, prio: int = 1, fonte: str = "body_composition"):
    return {"local_date": day, "weight_kg": str(kg), "prio": prio, "fonte": fonte}


def _stable_weight_rows(days: int = 28, base: float = 80.0):
    """Pesagem diaria oscilando ±0,2 kg em torno de `base`: media estavel."""
    return [
        _weight_row(DATE_TO - timedelta(days=d), base + (0.2 if d % 2 else -0.2)) for d in range(days - 1, -1, -1)
    ]


def _measurement(day: date, site: str, cm: float, protocol_ok: bool = True, id_: int | None = None, notes=None):
    return {"id": id_, "local_date": day, "site": site, "value_cm": str(cm), "protocol_ok": 1 if protocol_ok else 0, "notes": notes}


def _progression_item(exercise_id: int, status: ExerciseProgressionStatus, e1rms=()):
    exposures = [
        {"local_date": (DATE_TO - timedelta(days=21 - 7 * i)).isoformat(), "e1rm": value, "_e1rm_reps": 8}
        for i, value in enumerate(e1rms)
    ]
    return {
        "exercise_id": exercise_id,
        "name": f"Ex {exercise_id}",
        "status": status.value,
        "status_reasons": [],
        "comparable_exposures": 3,
        "exposures_in_window": 3,
        "last_local_date": DATE_TO.isoformat(),
        "_exposures": exposures,
        # run comparavel montado por `classify_batch`
        "_comparable_run": exposures[-3:],
    }


def _progression(*statuses: ExerciseProgressionStatus):
    return [_progression_item(i + 1, status, e1rms=(100.0, 102.0, 104.0)) for i, status in enumerate(statuses)]


# ---------------------------------------------------------------------------
# 1) Peso (`weight-ma-v1`)
# ---------------------------------------------------------------------------


def test_prioridade_por_dia_e_dias_sem_pesagem_nao_viram_zero():
    rows = [
        _weight_row(DATE_TO, 81.0, 3, "intervals_wellness"),
        _weight_row(DATE_TO, 80.0, 1, "body_composition"),
        _weight_row(DATE_TO, 80.5, 2, "daily_biometrics"),
        _weight_row(DATE_TO - timedelta(days=3), 82.0, 2, "daily_biometrics"),
        _weight_row(DATE_TO - timedelta(days=6), 84.0, 3, "intervals_wellness"),
    ]
    resumo = bs.weight_summary(rows, window=_window())
    pontos = resumo["points"]
    assert [(p["local_date"], p["weight_kg"], p["source"]) for p in pontos] == [
        (DATE_TO - timedelta(days=6), 84.0, "intervals_wellness"),
        (DATE_TO - timedelta(days=3), 82.0, "daily_biometrics"),
        (DATE_TO, 80.0, "body_composition"),
    ]
    # Media dos TRES dias pesados (nunca 7 dias com zeros).
    assert pontos[-1]["ma7_kg"] == 82.0
    assert pontos[-1]["ma7_points"] == 3
    assert resumo["weighings_count"] == 3
    assert resumo["latest"]["value"] == 80.0
    assert resumo["latest"]["nature"] is DataNature.OBSERVED
    assert resumo["ma7_latest"]["nature"] is DataNature.DERIVED


def test_media_movel_e_das_mesmas_funcoes_da_visao_geral():
    """A visao geral REEXPORTA `body_service`: nao existe segunda implementacao."""
    assert ov.pick_daily_weights is bs.pick_daily_weights
    assert ov.moving_average_points is bs.moving_average_points
    assert ov.body_block is bs.body_block
    assert ov.WEIGHT_SOURCES_SQL is bs.WEIGHT_SOURCES_SQL
    assert ov.BIOIMPEDANCE_SQL is bs.BIOIMPEDANCE_SQL
    assert not hasattr(ov, "_load_weight_rows")


def test_peso_sem_pesagem_e_insuficiente_e_nunca_zero():
    resumo = bs.weight_summary([], window=_window())
    assert resumo["data_quality"] is DataQuality.INSUFFICIENT
    assert resumo["latest"]["value"] is None
    assert resumo["ma7_latest"]["value"] is None
    assert resumo["change_pct"]["value"] is None
    assert resumo["points"] == []


# ---------------------------------------------------------------------------
# 2) Cintura (`waist-v1`)
# ---------------------------------------------------------------------------


def test_cintura_fora_de_protocolo_entra_na_serie_com_low_e_fica_fora_do_sinal():
    rows = [
        _measurement(DATE_TO - timedelta(days=21), "waist", 90.0, id_=1),
        _measurement(DATE_TO - timedelta(days=10), "waist", 86.0, protocol_ok=False, id_=2, notes="pós-almoço"),
        _measurement(DATE_TO, "waist", 88.5, id_=3),
    ]
    cintura = bs.waist_summary(rows, window=_window())
    assert cintura["available"] is True
    assert [p["data_quality"] for p in cintura["points"]] == [DataQuality.HIGH, DataQuality.LOW, DataQuality.HIGH]
    assert cintura["points"][1]["notes"] == "pós-almoço"
    # Ultima cintura = ultima NO PROTOCOLO; variacao entre as duas no protocolo.
    assert cintura["latest"]["value"] == 88.5
    assert cintura["latest"]["nature"] is DataNature.OBSERVED
    assert cintura["change"]["value"] == -1.5
    assert cintura["change_span_days"] == 21
    assert cintura["protocol_points"] == 2
    assert cintura["out_of_protocol_points"] == 1
    assert cintura["data_quality"] is DataQuality.HIGH
    assert bs.WAIST_OUT_OF_PROTOCOL_LIMITATION in cintura["limitations"]


def test_cintura_so_fora_de_protocolo_nao_gera_variacao():
    rows = [
        _measurement(DATE_TO - timedelta(days=14), "waist", 90.0, protocol_ok=False),
        _measurement(DATE_TO, "waist", 85.0, protocol_ok=False),
    ]
    cintura = bs.waist_summary(rows, window=_window())
    assert cintura["available"] is True
    assert cintura["latest"]["value"] is None
    assert cintura["change"]["value"] is None
    assert cintura["data_quality"] is DataQuality.LOW
    assert bs.WAIST_ONLY_OUT_OF_PROTOCOL_LIMITATION in cintura["limitations"]


def test_cintura_uma_medida_no_protocolo_e_medium_sem_variacao():
    cintura = bs.waist_summary([_measurement(DATE_TO, "waist", 88.0)], window=_window())
    assert cintura["latest"]["value"] == 88.0
    assert cintura["change"]["value"] is None
    assert cintura["data_quality"] is DataQuality.MEDIUM
    assert bs.WAIST_SINGLE_PROTOCOL_LIMITATION in cintura["limitations"]


def test_cintura_sem_medida_e_sem_tabela_declaram_limitacoes_distintas():
    sem_medida = bs.waist_summary([], window=_window())
    assert sem_medida["available"] is False
    assert sem_medida["table_available"] is True
    assert sem_medida["limitations"] == [bs.WAIST_NOT_RECORDED_LIMITATION]
    sem_tabela = bs.waist_summary([], window=_window(), table_available=False)
    assert sem_tabela["limitations"] == [ir.WAIST_UNAVAILABLE_LIMITATION]
    assert sem_tabela["data_quality"] is DataQuality.INSUFFICIENT


def test_demais_medidas_viram_series_simples_por_local():
    rows = [
        _measurement(DATE_TO - timedelta(days=20), "arm_l", 36.0),
        _measurement(DATE_TO, "arm_l", 36.5),
        _measurement(DATE_TO, "hip", 98.0, protocol_ok=False),
        _measurement(DATE_TO, "waist", 88.0),
    ]
    series = bs.measurement_series(rows)
    assert [s["site"] for s in series] == ["hip", "arm_l"]  # ordem canonica; cintura fora
    braco = next(s for s in series if s["site"] == "arm_l")
    assert braco["label"] == "Braço esquerdo"
    assert braco["latest"]["value"] == 36.5
    assert braco["change"]["value"] == 0.5
    assert braco["data_quality"] is DataQuality.HIGH
    quadril = next(s for s in series if s["site"] == "hip")
    assert quadril["data_quality"] is DataQuality.LOW
    assert quadril["change"]["value"] is None


# ---------------------------------------------------------------------------
# 3) Bioimpedancia (ESTIMATED)
# ---------------------------------------------------------------------------


def test_bioimpedancia_isolada_nao_confirma_tendencia():
    rows = [{"local_date": DATE_TO, "weight_kg": "80.0", "body_fat_percent": "0.21", "muscle_mass_kg": "38.0", "body_water_percent": "55.0"}]
    bio = bs.bioimpedance_summary(rows, window=_window())
    assert bio["readings_count"] == 1
    assert bio["body_fat_percent"]["value"] == 21.0  # fracao normalizada
    assert bio["body_fat_percent"]["nature"] is DataNature.ESTIMATED
    assert bio["body_fat_change"]["value"] is None
    assert bio["data_quality"] is DataQuality.LOW
    assert bs.BIOIMPEDANCE_SINGLE_LIMITATION in bio["limitations"]


def test_bioimpedancia_com_duas_leituras_da_tendencia_mas_continua_estimada():
    rows = [
        {"local_date": DATE_TO, "weight_kg": "80.0", "body_fat_percent": "20.0", "muscle_mass_kg": "38.5", "body_water_percent": None},
        {"local_date": DATE_TO - timedelta(days=14), "weight_kg": "80.4", "body_fat_percent": "21.0", "muscle_mass_kg": "38.0", "body_water_percent": None},
        {"local_date": DATE_TO - timedelta(days=60), "weight_kg": "81.0", "body_fat_percent": "23.0", "muscle_mass_kg": "37.0", "body_water_percent": None},
    ]
    bio = bs.bioimpedance_summary(rows, window=_window())
    assert bio["readings_count"] == 2  # a de 60 dias fica fora da janela
    assert bio["body_fat_change"]["value"] == -1.0
    assert bio["muscle_mass_change"]["value"] == 0.5
    assert bio["data_quality"] is DataQuality.MEDIUM  # nunca HIGH: e um modelo da balanca
    assert bio["body_water_percent"]["value"] is None
    assert bio["body_water_percent"]["data_quality"] is DataQuality.INSUFFICIENT


def test_bioimpedancia_anterior_a_janela_continua_como_ultima_leitura():
    rows = [{"local_date": DATE_TO - timedelta(days=60), "weight_kg": "81.0", "body_fat_percent": "23.0", "muscle_mass_kg": "37.0", "body_water_percent": None}]
    bio = bs.bioimpedance_summary(rows, window=_window())
    assert bio["readings_count"] == 0
    assert bio["latest_local_date"] == DATE_TO - timedelta(days=60)
    assert bio["body_fat_percent"]["value"] == 23.0
    assert any("anterior ao período" in l for l in bio["limitations"])


# ---------------------------------------------------------------------------
# 4) Desempenho (e1RM medio dos comparaveis)
# ---------------------------------------------------------------------------


def test_serie_de_desempenho_media_por_dia_so_dos_comparaveis():
    itens = [
        _progression_item(1, ExerciseProgressionStatus.PROGRESSING, e1rms=(100.0, 102.0, 104.0)),
        _progression_item(2, ExerciseProgressionStatus.STABLE, e1rms=(50.0, 50.0, 50.0)),
        _progression_item(3, ExerciseProgressionStatus.INSUFFICIENT_DATA, e1rms=(999.0,)),
        _progression_item(4, ExerciseProgressionStatus.REGRESSING, e1rms=(None, None, None)),
    ]
    serie = bs.performance_series(itens)
    assert serie["comparable_exercises"] == 3
    assert serie["progressing_or_stable"] == 2
    assert [(p["e1rm_avg_kg"], p["exercises"]) for p in serie["points"]] == [(75.0, 2), (76.0, 2), (77.0, 2)]
    assert serie["data_quality"] is DataQuality.HIGH
    assert bs.PERFORMANCE_MIX_LIMITATION in serie["limitations"]
    vazio = bs.performance_series([])
    assert vazio["points"] == []
    assert vazio["data_quality"] is DataQuality.INSUFFICIENT


# ---------------------------------------------------------------------------
# 5) Recomposicao (`recomp-v1`, §16.5)
# ---------------------------------------------------------------------------


def _recomp(weight_rows, waist_rows, progression):
    janela = _window()
    peso = bs.weight_summary(weight_rows, window=janela)
    cintura = bs.waist_summary(waist_rows, window=janela)
    out = bs.recomposition_signal(weight=peso, waist=cintura, progression_items=progression, window=janela)
    RecompositionSignalOut.model_validate(out)
    return out


_CINTURA_CAINDO = [
    _measurement(DATE_TO - timedelta(days=27), "waist", 90.0),
    _measurement(DATE_TO, "waist", 88.5),
]
_PROGREDINDO = _progression(
    ExerciseProgressionStatus.PROGRESSING,
    ExerciseProgressionStatus.STABLE,
    ExerciseProgressionStatus.REGRESSING,
)


def test_recomposicao_compativel_exige_as_tres_verificacoes():
    out = _recomp(_stable_weight_rows(), _CINTURA_CAINDO, _PROGREDINDO)
    assert out["status"] == bs.RECOMP_COMPATIBLE
    assert out["nature"] is DataNature.INFERRED
    assert out["metric_version"] == "recomp-v1"
    assert [c["result"] for c in out["checks"]] == [True, True, True]
    assert {e["metric"] for e in out["evidence"]} == {"body_weight_ma7", "waist_cm", "progress_share"}
    assert out["data_quality"] is DataQuality.MEDIUM  # INFERRED nunca e HIGH
    assert "compatível com recomposição" in out["answer"].lower()
    assert "confirmad" not in out["answer"].lower()
    assert bs.RECOMPOSITION_NEVER_PROOF_LIMITATION in out["limitations"]
    assert out["alternative_explanations"]


def test_recomposicao_nao_compativel_quando_a_cintura_nao_cai():
    """Peso estavel + cintura PARADA + desempenho ok = manutencao. A tela de
    corpo mostra o veredito (a home silencia; ver o teste da regra abaixo):
    `not_compatible`, dizendo o que se confirmou e o que nao, com os TRES
    numeros em `evidence[]` (decisao do usuario, 2026-09-24)."""
    cintura_parada = [
        _measurement(DATE_TO - timedelta(days=27), "waist", 90.0),
        _measurement(DATE_TO, "waist", 89.5),  # -0,5 cm < 1,0
    ]
    out = _recomp(_stable_weight_rows(), cintura_parada, _PROGREDINDO)
    assert out["status"] == bs.RECOMP_NOT_COMPATIBLE
    assert [c["result"] for c in out["checks"]] == [True, False, True]
    assert out["answer"] == (
        "Não compatível com recomposição: o peso ficou estável e o desempenho se manteve, "
        "mas a cintura não caiu nos últimos 28 dias."
    )
    assert "confirmad" not in out["answer"].lower()
    assert "descartad" not in out["answer"].lower()
    # Os tres numeros aparecem, mesmo com o veredito negativo.
    assert {e["metric"] for e in out["evidence"]} == {"body_weight_ma7", "waist_cm", "progress_share"}
    cintura = next(e for e in out["evidence"] if e["metric"] == "waist_cm")
    assert (cintura["from_value"], cintura["to_value"]) == (90.0, 89.5)
    # `not_compatible` e um veredito, nao falta de dado: nada de "falta".
    assert not any(l.startswith("Cintura caindo:") for l in out["limitations"])
    assert out["data_quality"] is not DataQuality.INSUFFICIENT


def test_recomposicao_nao_compativel_nomeia_todas_as_falhas():
    """Nenhuma verificacao passou: a frase lista as tres, sem o "mas"."""
    caindo = [_weight_row(DATE_TO - timedelta(days=d), 80.0 - (27 - d) * 0.1) for d in range(27, -1, -1)]
    cintura_subindo = [
        _measurement(DATE_TO - timedelta(days=27), "waist", 88.0),
        _measurement(DATE_TO, "waist", 90.0),
    ]
    regredindo = _progression(
        ExerciseProgressionStatus.REGRESSING,
        ExerciseProgressionStatus.REGRESSING,
        ExerciseProgressionStatus.REGRESSING,
    )
    out = _recomp(caindo, cintura_subindo, regredindo)
    assert out["status"] == bs.RECOMP_NOT_COMPATIBLE
    assert [c["result"] for c in out["checks"]] == [False, False, False]
    assert out["answer"] == (
        "Não compatível com recomposição: o peso não ficou estável, a cintura não caiu e "
        "o desempenho não se manteve nos últimos 28 dias."
    )
    assert "mas" not in out["answer"]


def test_recomposicao_nao_compativel_quando_o_peso_cai_demais():
    """Peso caindo 3% com cintura caindo e desempenho mantido: emagrecimento,
    nao recomposicao."""
    caindo = [_weight_row(DATE_TO - timedelta(days=d), 80.0 - (27 - d) * 0.1) for d in range(27, -1, -1)]
    out = _recomp(caindo, _CINTURA_CAINDO, _PROGREDINDO)
    assert out["status"] == bs.RECOMP_NOT_COMPATIBLE
    assert out["checks"][0]["result"] is False


def test_recomposicao_nao_compativel_quando_o_desempenho_cai():
    regredindo = _progression(
        ExerciseProgressionStatus.REGRESSING,
        ExerciseProgressionStatus.REGRESSING,
        ExerciseProgressionStatus.STABLE,
    )
    out = _recomp(_stable_weight_rows(), _CINTURA_CAINDO, regredindo)
    assert out["status"] == bs.RECOMP_NOT_COMPATIBLE
    assert out["checks"][2]["result"] is False


def test_recomposicao_insuficiente_sem_cintura_no_protocolo():
    fora = [
        _measurement(DATE_TO - timedelta(days=27), "waist", 90.0, protocol_ok=False),
        _measurement(DATE_TO, "waist", 85.0, protocol_ok=False),  # cairia 5 cm, mas fora do protocolo
    ]
    out = _recomp(_stable_weight_rows(), fora, _PROGREDINDO)
    assert out["status"] == bs.RECOMP_INSUFFICIENT
    assert out["checks"][1]["result"] is None
    assert out["data_quality"] is DataQuality.INSUFFICIENT
    assert bs.WAIST_ONLY_OUT_OF_PROTOCOL_LIMITATION in out["limitations"]
    assert not any(e["metric"] == "waist_cm" for e in out["evidence"])


def test_recomposicao_insuficiente_diz_o_que_falta_em_cada_verificacao():
    """`insufficient_data` nunca e um vazio: cada verificacao nao avaliavel
    vira uma limitacao dizendo o que registrar para sair do limbo."""
    out = _recomp([], [], _PROGREDINDO[:1])
    assert out["status"] == bs.RECOMP_INSUFFICIENT
    assert [c["result"] for c in out["checks"]] == [None, None, None]
    assert out["answer"] == (
        "Dados insuficientes para ler o sinal: falta pesagens suficientes para uma média móvel "
        "comparável, duas medidas de cintura no protocolo e exercícios com histórico comparável."
    )
    assert any(l.startswith("Peso estável:") for l in out["limitations"])
    assert any(l.startswith("Cintura caindo:") for l in out["limitations"])
    assert any(l.startswith("Desempenho mantido:") for l in out["limitations"])
    assert bs.RECOMPOSITION_NEVER_PROOF_LIMITATION in out["limitations"]
    # Sem repeticao: a limitacao da cintura chega por duas vias e sai uma vez.
    assert len(out["limitations"]) == len(set(out["limitations"]))


def test_recomposicao_insuficiente_sem_pesagens_ou_sem_exercicios_comparaveis():
    sem_peso = _recomp([], _CINTURA_CAINDO, _PROGREDINDO)
    assert sem_peso["status"] == bs.RECOMP_INSUFFICIENT
    assert sem_peso["checks"][0]["result"] is None
    poucos = _recomp(_stable_weight_rows(), _CINTURA_CAINDO, _PROGREDINDO[:2])
    assert poucos["status"] == bs.RECOMP_INSUFFICIENT
    assert poucos["checks"][2]["result"] is None
    # Cobertura curta da media movel (menos de 14 dias) tambem nao conclui.
    curto = _recomp(_stable_weight_rows(days=7), _CINTURA_CAINDO, _PROGREDINDO)
    assert curto["status"] == bs.RECOMP_INSUFFICIENT
    assert curto["checks"][0]["result"] is None


# ---------------------------------------------------------------------------
# 6) Bloco Corpo da visao geral: com e sem cintura
# ---------------------------------------------------------------------------


def test_bloco_corpo_com_cintura_preenche_waist_cm_e_tira_a_limitacao():
    janela = _window()
    cintura = bs.waist_summary(_CINTURA_CAINDO, window=janela)
    bloco = bs.body_block(weight_rows=_stable_weight_rows(), bioimpedance=None, window=janela, waist=cintura)
    BodyBlockOut.model_validate({k: v for k, v in bloco.items() if not k.startswith("_")})
    assert bloco["waist_cm"]["value"] == 88.5
    assert bloco["waist_cm"]["data_quality"] is DataQuality.HIGH
    assert ir.WAIST_UNAVAILABLE_LIMITATION not in bloco["limitations"]
    assert bs.WAIST_NOT_RECORDED_LIMITATION not in bloco["limitations"]
    assert bloco["_waist"]["change"]["value"] == -1.5
    assert bloco["drilldown_href"] == "/dashboard/corpo"


def test_bloco_corpo_sem_cintura_declara_a_limitacao_certa():
    janela = _window()
    sem_medida = bs.body_block(
        weight_rows=[], bioimpedance=None, window=janela, waist=bs.waist_summary([], window=janela)
    )
    assert sem_medida["waist_cm"]["value"] is None
    assert bs.WAIST_NOT_RECORDED_LIMITATION in sem_medida["limitations"]
    sem_tabela = bs.body_block(weight_rows=[], bioimpedance=None, window=janela)
    assert ir.WAIST_UNAVAILABLE_LIMITATION in sem_tabela["limitations"]
    assert sem_tabela["_waist"]["available"] is False


# ---------------------------------------------------------------------------
# 7) Regra de insight com e sem cintura
# ---------------------------------------------------------------------------


def _ctx(body, progression):
    return ir.InsightContext(window=_window(), cycle_days=7, muscles={"items": []}, body=body, progression=progression)


def _body(change_pct=0.3, waist=None):
    return {
        "weight_ma7_change_pct": {"value": change_pct},
        "weight_ma7_latest": {"value": 80.1},
        "weight_ma7_first": {"value": 80.0},
        "span_days": 21,
        "limitations": [],
        **({"waist": waist} if waist is not None else {}),
    }


def _waist(change, first=90.0, last=88.5, limitations=()):
    return {"available": True, "change": {"value": change}, "first": first, "last": last, "limitations": list(limitations)}


def test_regra_de_insight_sobe_a_confianca_com_cintura_caindo():
    out = ir.rule_recomposition_compatible(_ctx(_body(waist=_waist(-1.5)), _PROGREDINDO))
    assert len(out) == 1
    insight = Insight.model_validate(out[0])
    assert insight.data_quality is DataQuality.MEDIUM
    assert ir.WAIST_UNAVAILABLE_LIMITATION not in insight.limitations
    assert any(e.metric == "waist_cm" and e.from_value == 90.0 and e.to_value == 88.5 for e in insight.evidence)
    assert "cintura caindo" in insight.answer
    assert insight.source_screen == "/dashboard/corpo"


def test_regra_de_insight_silencia_com_cintura_conhecida_e_parada():
    """MESMO cenario que a tela de corpo mostra como `not_compatible`: a home
    tem espaco para duas acoes e um "nao compativel" ocuparia lugar sem
    sugerir nada. O veredito vive em /dashboard/corpo (decisao do usuario,
    2026-09-24) - ver `test_recomposicao_nao_compativel_quando_a_cintura_nao_cai`."""
    assert ir.rule_recomposition_compatible(_ctx(_body(waist=_waist(-0.5)), _PROGREDINDO)) == []
    assert ir.rule_recomposition_compatible(_ctx(_body(waist=_waist(0.5)), _PROGREDINDO)) == []


def test_regra_de_insight_sem_cintura_mantem_o_comportamento_da_f16():
    sem_chave = ir.rule_recomposition_compatible(_ctx(_body(), _PROGREDINDO))
    assert len(sem_chave) == 1
    assert sem_chave[0]["data_quality"] is DataQuality.LOW
    assert ir.WAIST_UNAVAILABLE_LIMITATION in sem_chave[0]["limitations"]
    # Tabela presente, nenhuma medida: a limitacao e a do body_service.
    sem_medida = ir.rule_recomposition_compatible(
        _ctx(_body(waist={"available": False, "change": {"value": None}, "first": None, "last": None,
                          "limitations": [bs.WAIST_NOT_RECORDED_LIMITATION]}), _PROGREDINDO)
    )
    assert sem_medida[0]["data_quality"] is DataQuality.LOW
    assert bs.WAIST_NOT_RECORDED_LIMITATION in sem_medida[0]["limitations"]
    assert ir.WAIST_UNAVAILABLE_LIMITATION not in sem_medida[0]["limitations"]


# ---------------------------------------------------------------------------
# 8) `build_body` com conexao falsa: payload fecha no contrato
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, respostas):
        self._respostas = respostas
        self._atual = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._atual = []
        for trecho, linhas in self._respostas:
            if trecho in sql:
                self._atual = list(linhas)
                return

    def fetchall(self):
        return list(self._atual)

    def fetchone(self):
        return self._atual[0] if self._atual else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_build_body_fecha_no_contrato_com_tabela_ausente(monkeypatch):
    monkeypatch.setattr(bs, "has_body_measurements_table", lambda cursor: False)
    monkeypatch.setattr(bs, "get_exercises_progression_batch", lambda conn, *, user_id, window: (_PROGREDINDO, False, 5))
    conn = CursorFactoryConn(_Cursor, 
        [
            ("intervals_wellness", _stable_weight_rows(90)),
            ("body_composition\nWHERE user_id = %s AND local_date <= %s", []),
            ("workout_plan_template_assignments", []),
        ]
    )
    payload = bs.build_body(conn, user_id=7, window=_window(90, WindowKind.ROLLING_90D))
    resposta = BodyResponse.model_validate({k: v for k, v in payload.items() if not k.startswith("_")})
    assert resposta.waist.table_available is False
    assert resposta.waist.latest.value is None
    assert ir.WAIST_UNAVAILABLE_LIMITATION in resposta.waist.limitations
    assert any("047" in l for l in resposta.limitations)
    assert resposta.recomposition.status.value == "insufficient_data"
    assert resposta.recomposition.window.days == 28  # janela propria do sinal
    assert resposta.weight.weighings_count == 90
    assert resposta.performance.comparable_exercises == 3
    assert resposta.metric_versions["recomposition_signal"] == "recomp-v1"
    assert payload["_timings"]["recomp_weight"] >= 0  # 90d != 28d: segunda leitura


def test_build_body_rejeita_user_id_nulo():
    with pytest.raises(ValueError):
        bs.build_body(CursorFactoryConn(_Cursor, []), user_id=None, window=_window())


def test_limiares_do_sinal_vem_do_registry():
    assert THRESHOLDS["weight_stable_pct"] == 0.01
    assert THRESHOLDS["waist_drop_cm"] == 1.0
    assert THRESHOLDS["insight_recomp_progress_share"] == 0.5


# ---------------------------------------------------------------------------
# 2026-10-06: desempenho so com o run comparavel e e1RM confiavel (<= 12 reps)
# ---------------------------------------------------------------------------


def test_desempenho_usa_so_o_run_comparavel_e_e1rm_confiavel():
    item = _progression_item(1, ExerciseProgressionStatus.PROGRESSING, e1rms=(500.0, 100.0, 104.0))
    # So as 2 ultimas exposicoes sao da chave do veredito: a primeira (outra
    # maquina) fica fora da media.
    item["comparable_exposures"] = 2
    item["_comparable_run"] = item["_exposures"][-2:]
    serie = bs.performance_series([item])
    assert [p["e1rm_avg_kg"] for p in serie["points"]] == [100.0, 104.0]

    # e1RM de serie com mais de 12 reps nao e confiavel: fora da media.
    item["_exposures"][-1]["_e1rm_reps"] = 15
    serie = bs.performance_series([item])
    assert [p["e1rm_avg_kg"] for p in serie["points"]] == [100.0]
