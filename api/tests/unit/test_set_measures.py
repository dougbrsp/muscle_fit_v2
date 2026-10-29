"""Dono das medidas de série (`domain/set_measures.py`, Lote T1, 2026-10-20):
normalização, `is_rep_based`, fragmentos SQL, validações por código (item do
Modelo/ficha e série gravada), dose de recuperação, tempo planejado, texto
pt-BR e o bloco exportado ao FE.

Rodar: cd api && python -m pytest tests/unit/test_set_measures.py
"""

from __future__ import annotations

import pytest

from app.domain import set_measures as sm
from app.domain.set_measures import SetMeasure

# ---------------------------------------------------------------------------
# Vocabulário e normalização
# ---------------------------------------------------------------------------


def test_vocabulario_e_default():
    assert [m.value for m in SetMeasure] == ["reps", "duration", "max_reps"]
    assert sm.DEFAULT_MEASURE is SetMeasure.REPS
    assert set(sm.MEASURE_LABELS) == set(SetMeasure) == set(sm.MEASURE_SHORT_LABELS)
    assert sm.MEASURE_LABELS[SetMeasure.REPS] == "Repetições"
    assert sm.MEASURE_LABELS[SetMeasure.DURATION] == "Tempo"
    assert sm.MEASURE_LABELS[SetMeasure.MAX_REPS] == "Máximo de repetições"
    assert SetMeasure.MAX_REPS.label == "Máximo de repetições"
    assert sm.MEASURE_SHORT_LABELS[SetMeasure.DURATION] == "s"


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        (None, SetMeasure.REPS),
        ("", SetMeasure.REPS),
        ("   ", SetMeasure.REPS),
        ("reps", SetMeasure.REPS),
        (" REPS ", SetMeasure.REPS),
        ("duration", SetMeasure.DURATION),
        ("Duration", SetMeasure.DURATION),
        ("max_reps", SetMeasure.MAX_REPS),
        (SetMeasure.MAX_REPS, SetMeasure.MAX_REPS),
        ("tempo", SetMeasure.REPS),  # desconhecido: leitura tolerante
        (42, SetMeasure.REPS),
    ],
)
def test_normalize_measure_tolerante(entrada, esperado):
    assert sm.normalize_measure(entrada) is esperado


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        (None, None),
        ("", None),
        ("tempo", None),
        ("reps", SetMeasure.REPS),
        ("MAX_REPS", SetMeasure.MAX_REPS),
        (SetMeasure.DURATION, SetMeasure.DURATION),
    ],
)
def test_parse_measure_estrito(entrada, esperado):
    assert sm.parse_measure(entrada) is esperado


def test_is_rep_based_so_reps():
    assert sm.is_rep_based(None) is True
    assert sm.is_rep_based("") is True
    assert sm.is_rep_based("reps") is True
    assert sm.is_rep_based(SetMeasure.REPS) is True
    assert sm.is_rep_based("duration") is False
    assert sm.is_rep_based(SetMeasure.MAX_REPS) is False


def test_measure_label():
    assert sm.measure_label("duration") == "Tempo"
    assert sm.measure_label(SetMeasure.REPS) == "Repetições"
    assert sm.measure_label(None) is None
    assert sm.measure_label("tempo") is None


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------


def test_sql_helpers():
    assert sm.sql_measure_expr() == "COALESCE(es.measure, 'reps')"
    assert sm.sql_measure_expr("ec.default_measure") == "COALESCE(ec.default_measure, 'reps')"
    assert sm.sql_is_rep_based() == "(es.measure IS NULL OR es.measure = 'reps')"
    assert sm.sql_is_rep_based("wpe.measure") == "(wpe.measure IS NULL OR wpe.measure = 'reps')"
    for sql in (sm.sql_measure_expr(), sm.sql_is_rep_based()):
        assert ":" not in sql  # Data API: literal com ':' viraria parâmetro


@pytest.mark.parametrize("coluna", ["", "es.measure; DROP TABLE x", "a.b.c", "es measure"])
def test_sql_recusa_coluna_invalida(coluna):
    with pytest.raises(ValueError):
        sm.sql_measure_expr(coluna)
    with pytest.raises(ValueError):
        sm.sql_is_rep_based(coluna)


# ---------------------------------------------------------------------------
# Validação do item (Modelo / ficha)
# ---------------------------------------------------------------------------


def _item(measure, target=None, window=None, reps_target=None, advanced=False):
    return sm.validate_item_measure(measure, target, window, reps_target, advanced)


def test_item_reps_aceita_a_forma_de_hoje_e_proibe_tempo():
    assert _item(None, reps_target="8-12") == []
    assert _item("reps", reps_target="8-12", advanced=True) == []  # bloco avançado segue as regras de hoje
    assert _item("reps", target=60) == [sm.DURATION_TARGET_NOT_ALLOWED]
    assert _item("reps", window=30) == [sm.TIME_WINDOW_NOT_ALLOWED]
    assert _item("reps", target=60, window=30) == [sm.DURATION_TARGET_NOT_ALLOWED, sm.TIME_WINDOW_NOT_ALLOWED]


def test_item_duration():
    assert _item("duration", target=60) == []
    assert _item("duration", target=sm.DURATION_SECONDS_MIN) == []
    assert _item("duration", target=sm.DURATION_SECONDS_MAX) == []
    assert _item("duration", target="60") == []  # texto numérico vindo do formulário
    assert _item("duration") == [sm.DURATION_TARGET_REQUIRED]
    assert _item("duration", target="") == [sm.DURATION_TARGET_REQUIRED]
    assert _item("duration", target=0) == [sm.DURATION_TARGET_OUT_OF_RANGE]
    assert _item("duration", target=sm.DURATION_SECONDS_MAX + 1) == [sm.DURATION_TARGET_OUT_OF_RANGE]
    assert _item("duration", target="um minuto") == [sm.DURATION_TARGET_OUT_OF_RANGE]
    assert _item("duration", target=60.5) == [sm.DURATION_TARGET_OUT_OF_RANGE]
    assert _item("duration", target=60, window=30) == [sm.TIME_WINDOW_NOT_ALLOWED]
    assert _item("duration", target=60, reps_target="8-12") == [sm.REPS_TARGET_NOT_ALLOWED]
    assert _item("duration", target=60, advanced=True) == [sm.ADVANCED_BLOCK_NOT_ALLOWED]
    assert _item("duration", window=30, reps_target="10", advanced=True) == [
        sm.DURATION_TARGET_REQUIRED,
        sm.TIME_WINDOW_NOT_ALLOWED,
        sm.REPS_TARGET_NOT_ALLOWED,
        sm.ADVANCED_BLOCK_NOT_ALLOWED,
    ]


def test_item_max_reps():
    assert _item("max_reps") == []  # janela opcional
    assert _item("max_reps", window=30) == []
    assert _item("max_reps", window=sm.TIME_WINDOW_SECONDS_MIN) == []
    assert _item("max_reps", window=sm.TIME_WINDOW_SECONDS_MAX) == []
    assert _item("max_reps", window=sm.TIME_WINDOW_SECONDS_MIN - 1) == [sm.TIME_WINDOW_OUT_OF_RANGE]
    assert _item("max_reps", window=sm.TIME_WINDOW_SECONDS_MAX + 1) == [sm.TIME_WINDOW_OUT_OF_RANGE]
    assert _item("max_reps", window="x") == [sm.TIME_WINDOW_OUT_OF_RANGE]
    assert _item("max_reps", target=60) == [sm.DURATION_TARGET_NOT_ALLOWED]
    assert _item("max_reps", reps_target="15") == [sm.REPS_TARGET_NOT_ALLOWED]
    assert _item("max_reps", advanced=True) == [sm.ADVANCED_BLOCK_NOT_ALLOWED]
    assert _item("max_reps", target=60, window=1, reps_target="15", advanced=True) == [
        sm.DURATION_TARGET_NOT_ALLOWED,
        sm.TIME_WINDOW_OUT_OF_RANGE,
        sm.REPS_TARGET_NOT_ALLOWED,
        sm.ADVANCED_BLOCK_NOT_ALLOWED,
    ]


def test_item_medida_desconhecida_e_o_unico_erro():
    assert _item("tempo", target=60, window=30, reps_target="x", advanced=True) == [sm.MEASURE_UNKNOWN]


def test_codigos_do_item_sao_os_declarados():
    vistos = set()
    for args in [
        ("x",),
        ("reps", 60, 30),
        ("duration",),
        ("duration", 0, 30, "8", True),
        ("max_reps", 60, 1, "15", True),
    ]:
        vistos.update(sm.validate_item_measure(*(list(args) + [None, None, None, False])[:5]))
    assert vistos == set(sm.ITEM_VALIDATION_CODES)


# ---------------------------------------------------------------------------
# Validação da série gravada
# ---------------------------------------------------------------------------


def _set(measure, reps=None, duration=None):
    return sm.validate_set_measure(measure, reps, duration)


def test_serie_reps():
    assert _set(None, reps=10) == []
    assert _set("reps") == []  # série sem reps continua aceita (legado)
    assert _set("reps", reps=10, duration=30) == [sm.DURATION_NOT_ALLOWED]


def test_serie_duration():
    assert _set("duration", duration=60) == []
    assert _set("duration", duration="60") == []
    assert _set("duration", duration=sm.DURATION_SECONDS_MIN) == []
    assert _set("duration", duration=sm.DURATION_SECONDS_MAX) == []
    assert _set("duration") == [sm.DURATION_REQUIRED]
    assert _set("duration", duration=0) == [sm.DURATION_OUT_OF_RANGE]
    assert _set("duration", duration=sm.DURATION_SECONDS_MAX + 1) == [sm.DURATION_OUT_OF_RANGE]
    assert _set("duration", duration=True) == [sm.DURATION_OUT_OF_RANGE]
    assert _set("duration", reps=10, duration=60) == [sm.REPS_NOT_ALLOWED]
    assert _set("duration", reps=10) == [sm.DURATION_REQUIRED, sm.REPS_NOT_ALLOWED]


def test_serie_max_reps():
    assert _set("max_reps", reps=15) == []
    assert _set("max_reps", reps=sm.MAX_REPS_MIN_REPS) == []
    assert _set("max_reps", reps=15, duration=30) == []
    assert _set("max_reps", reps=15, duration=sm.TIME_WINDOW_SECONDS_MIN) == []
    assert _set("max_reps", reps=15, duration=sm.TIME_WINDOW_SECONDS_MAX) == []
    assert _set("max_reps") == [sm.REPS_REQUIRED]
    assert _set("max_reps", reps=0) == [sm.REPS_BELOW_MIN]
    assert _set("max_reps", reps=-3) == [sm.REPS_BELOW_MIN]
    assert _set("max_reps", reps="muitas") == [sm.REPS_BELOW_MIN]
    assert _set("max_reps", reps=15, duration=sm.TIME_WINDOW_SECONDS_MIN - 1) == [sm.TIME_WINDOW_OUT_OF_RANGE]
    assert _set("max_reps", reps=15, duration=sm.TIME_WINDOW_SECONDS_MAX + 1) == [sm.TIME_WINDOW_OUT_OF_RANGE]
    assert _set("max_reps", reps=0, duration=1) == [sm.REPS_BELOW_MIN, sm.TIME_WINDOW_OUT_OF_RANGE]


def test_serie_medida_desconhecida_e_o_unico_erro():
    assert _set("tempo", reps=0, duration=0) == [sm.MEASURE_UNKNOWN]


def test_codigos_da_serie_sao_os_declarados():
    vistos = set()
    for args in [("x", None, None), ("reps", 10, 30), ("duration", 10, None), ("duration", None, 0), ("max_reps", 0, 1), ("max_reps", None, None)]:
        vistos.update(sm.validate_set_measure(*args))
    assert vistos == set(sm.SET_VALIDATION_CODES)


# ---------------------------------------------------------------------------
# O que os motores consomem
# ---------------------------------------------------------------------------


def test_dose_reps_for_measure():
    assert sm.dose_reps_for_measure("reps", 10) == 10
    assert sm.dose_reps_for_measure(None, "12") == 12
    assert sm.dose_reps_for_measure("reps", None) is None  # cai no DOSE_REPS_WHEN_MISSING do consumidor
    assert sm.dose_reps_for_measure("max_reps", 15) == 15
    assert sm.dose_reps_for_measure("duration", 60) is None  # série por tempo não tem reps


def test_planned_set_execution_seconds():
    assert sm.planned_set_execution_seconds("duration", 60, None) == 60.0
    assert sm.planned_set_execution_seconds("duration", "45", 30) == 45.0
    assert sm.planned_set_execution_seconds("duration", None, None) is None
    assert sm.planned_set_execution_seconds("max_reps", None, 30) == 30.0
    assert sm.planned_set_execution_seconds("max_reps", 60, None) is None  # sem janela: fórmula de reps
    assert sm.planned_set_execution_seconds("reps", 60, 30) is None
    assert sm.planned_set_execution_seconds(None, None, None) is None


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "measure, reps, duration, esperado",
    [
        ("reps", 10, None, "10 reps"),
        (None, 8, None, "8 reps"),
        ("reps", "12", None, "12 reps"),
        ("reps", 10, 30, "10 reps"),  # duração não pertence a `reps`: ignorada no texto
        ("reps", None, None, "—"),
        ("duration", None, 60, "60 s"),
        ("duration", 10, 60, "60 s"),
        ("duration", None, None, "—"),
        ("max_reps", 15, 30, "15 reps em 30 s"),
        ("max_reps", 15, None, "15 reps"),
        ("max_reps", None, 30, "—"),
    ],
)
def test_format_set_measure(measure, reps, duration, esperado):
    assert sm.format_set_measure(measure, reps, duration) == esperado


# ---------------------------------------------------------------------------
# Espelho do FE
# ---------------------------------------------------------------------------


def test_export_for_frontend():
    bloco = sm.export_for_frontend()
    assert set(bloco) == {"measures", "default_measure", "values", "limits", "empty_text", "reason_not_rep_based", "reason_labels",
                          "advanced_block_presence_fields"}
    assert [m["value"] for m in bloco["measures"]] == ["reps", "duration", "max_reps"]
    assert bloco["values"] == {"reps": "reps", "duration": "duration", "max_reps": "max_reps"}
    assert [m["rep_based"] for m in bloco["measures"]] == [True, False, False]
    assert [m["label"] for m in bloco["measures"]] == ["Repetições", "Tempo", "Máximo de repetições"]
    assert [m["short_label"] for m in bloco["measures"]] == ["reps", "s", "reps máx."]
    assert bloco["default_measure"] == "reps"
    assert bloco["limits"] == {
        "duration_seconds_min": sm.DURATION_SECONDS_MIN,
        "duration_seconds_max": sm.DURATION_SECONDS_MAX,
        "time_window_seconds_min": sm.TIME_WINDOW_SECONDS_MIN,
        "time_window_seconds_max": sm.TIME_WINDOW_SECONDS_MAX,
        "max_reps_min_reps": sm.MAX_REPS_MIN_REPS,
    }
    assert bloco["empty_text"] == sm.EMPTY_MEASURE_TEXT
    assert bloco["advanced_block_presence_fields"] == list(sm.ADVANCED_BLOCK_PRESENCE_FIELDS)
    assert bloco["reason_not_rep_based"] == sm.REASON_MEASURE_NOT_REP_BASED == "measure_not_rep_based"
    assert bloco["reason_labels"] == {sm.REASON_MEASURE_NOT_REP_BASED: sm.MEASURE_REASON_LABELS[sm.REASON_MEASURE_NOT_REP_BASED]}
    assert "tempo" in bloco["reason_labels"][sm.REASON_MEASURE_NOT_REP_BASED].lower()


def test_modulo_puro_sem_imports_do_app():
    import inspect

    fonte = inspect.getsource(sm)
    assert "from app" not in fonte and "import app" not in fonte and "from ." not in fonte
