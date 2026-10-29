"""M76 Lote 3b — série de trabalho e recorde com um dono só.

- `set_types`: o SQL dá a mesma resposta que as funções Python (apelidos),
  o `CASE` canônico é do dono e `is_linked_application` mora nele.
- `session_records.record_events`: a regra única de recorde (decisão 3).
- `progression_service.select_recent_records`/`record_highlight`: os recordes
  do relatório consomem a regra única.

Rodar: cd api && python -m pytest tests/unit/test_m76_lote3b_set_types_e_recorde.py
"""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

import pytest

from app import set_types as st
from app.domain import progression_engine
from app.intelligence import recovery_read_model as rrm
from app.intelligence.dashboard import session_records as sr
from app.intelligence.progression_service import record_highlight, select_recent_records

# ---------------------------------------------------------------------------
# set_types: SQL == Python
# ---------------------------------------------------------------------------


def _sql_lista(predicado: str) -> set[str]:
    """Valores entre aspas do `IN (...)` de um predicado."""
    return set(re.findall(r"'([^']*)'", predicado.split(" IN ", 1)[1]))


@pytest.mark.parametrize("cru", sorted(st.KNOWN_SET_TYPE_SQL_VALUES - {""}))
def test_sql_not_preparatory_responde_como_is_preparatory_set(cru):
    excluidos = _sql_lista(st.sql_not_preparatory())
    assert (cru in excluidos) == st.is_preparatory_set(cru)


def test_sql_not_preparatory_cobre_os_apelidos():
    excluidos = _sql_lista(st.sql_not_preparatory())
    assert {"warm-up", "warm_up", "feeler_set"} <= excluidos


def _avalia_case(case_sql: str, cru: str) -> str:
    """Interpreta o `CASE` gerado para um valor JÁ em lower/trim."""
    for valor, resultado in re.findall(r"WHEN '([^']*)' THEN '([^']*)'", case_sql):
        if valor == cru:
            return resultado
    fallback = re.search(r"ELSE (.+)\n", case_sql).group(1).strip()
    return fallback.strip("'") if fallback.startswith("'") else cru


@pytest.mark.parametrize("cru", sorted(st.KNOWN_SET_TYPE_SQL_VALUES) + ["superset"])
def test_case_canonico_e_o_normalize_set_type(cru):
    assert _avalia_case(st.sql_canonical_set_type_case(), cru) == st.normalize_set_type(cru)


def test_case_com_codigos_do_recovery_read_model_resolve_apelidos():
    """Antes: `drop-set`/`warm-up` caíam em "working" no empacotamento da
    recuperação, mas viravam drop/aquecimento no caminho Python."""
    for cru, esperado in (("drop-set", "drop_set"), ("warm-up", "warmup"), ("dropset", "drop_set"), ("", "working"),
                          ("superset", "working")):
        codigo = _avalia_case(rrm._SET_TYPE_CASE, cru)
        assert rrm.SET_TYPE_CODES[int(codigo)] == esperado
    assert rrm.SET_TYPE_CODES[0] == "working"
    assert set(rrm.SET_TYPE_CODES) == st.KNOWN_SET_TYPES


def test_case_com_codigos_incompletos_falha():
    with pytest.raises(ValueError):
        st.sql_canonical_set_type_case(codes={"working": "0"})


def test_is_linked_application_mora_no_dono():
    assert progression_engine.is_linked_application is st.is_linked_application
    linha = {"technique_code": "drop_set", "anchor_set_number": 2}
    assert st.is_linked_application(linha)
    assert st.is_linked_application(SimpleNamespace(**linha))
    assert not st.is_linked_application({"technique_code": "drop_set", "anchor_set_number": None})
    assert not st.is_linked_application({"technique_code": None, "anchor_set_number": 2})


# ---------------------------------------------------------------------------
# session_records: a regra única de recorde
# ---------------------------------------------------------------------------


def _hist(activity_id, max_kg, *, equipment_id=7, convention=None, exercise_id=500, local_date=None):
    return {
        "activity_id": activity_id,
        "exercise_row_id": activity_id * 10 + exercise_id % 10,
        "exercise_id": exercise_id,
        "local_date": local_date or f"2026-09-{activity_id:02d}",
        "started_at": None,
        "position_in_session": 1,
        "equipment_id": equipment_id,
        "load_convention": convention,
        "prescribed_reps_target": None,
        "max_weight_kg": max_kg,
        "min_weight_kg": max_kg,
    }


def _eventos(rows):
    return [(e.exposure.activity_id, e.load_kg, e.previous_load_kg) for e in sr.record_events(sr.parse_history(rows))]


def test_record_events_primeira_exposicao_e_empate_nao_sao_recorde():
    assert _eventos([_hist(1, 30), _hist(2, 30), _hist(3, 32.5), _hist(4, 32.5)]) == [(3, 32.5, 30.0)]


def test_record_events_sem_equipamento_ou_convencao_diferente():
    rows = [_hist(1, 30), _hist(2, 40, equipment_id=None), _hist(3, 50, convention="per_dumbbell"), _hist(4, 31)]
    assert _eventos(rows) == [(4, 31.0, 30.0)]


def test_record_exercise_ids_e_record_events_por_sessao():
    rows = [_hist(1, 30), _hist(2, 35), _hist(2, 10, exercise_id=601), _hist(3, 12, exercise_id=601)]
    assert sr.record_exercise_ids(sr.parse_history(rows)) == {2: {500}, 3: {601}}


def test_key_for_aplica_a_excecao_do_8_1_pelo_historico():
    sem = sr.parse_history([_hist(1, 30, equipment_id=None)])
    com = sr.parse_history([_hist(1, 30, equipment_id=None), _hist(2, 30)])
    assert sr.key_for(sem, 500, None, None).equipment_agnostic is True
    assert sr.key_for(com, 500, None, None).equipment_agnostic is False
    assert sr.key_for(com, 500, 7, "total") == sr.parse_history([_hist(2, 30)])[0].key


# ---------------------------------------------------------------------------
# Recordes recentes do relatório
# ---------------------------------------------------------------------------


def test_select_recent_records_um_por_chave_na_janela_mais_recente_primeiro():
    rows = [
        _hist(1, 30, local_date="2026-07-01"),
        _hist(2, 32.5, local_date="2026-07-10"),  # recorde fora da janela
        _hist(3, 35, local_date="2026-09-01"),
        _hist(4, 37.5, local_date="2026-09-20"),  # recorde mais recente da chave
        _hist(5, 20, exercise_id=601, local_date="2026-09-02"),
        _hist(6, 22, exercise_id=601, local_date="2026-09-05"),
    ]
    eventos = sr.record_events(sr.parse_history(rows))
    escolhidos = select_recent_records(eventos, start=date(2026, 8, 8), limit=8)
    assert [(e.exposure.activity_id, e.load_kg) for e in escolhidos] == [(4, 37.5), (6, 22.0)]
    assert len(select_recent_records(eventos, start=date(2026, 8, 8), limit=1)) == 1


def test_record_highlight_formato_do_payload():
    rows = [_hist(1, 30), _hist(2, 32.5)]
    (evento,) = sr.record_events(sr.parse_history(rows))
    sets = [{"exercise_row_id": 20, "reps": 6, "weight_kg": 32.5}, {"exercise_row_id": 20, "reps": 9, "weight_kg": 30}]
    assert record_highlight(evento, sets, {500: "Supino reto"}, {7: "Banco reto"}) == {
        "exercise_id": 500,
        "exercise_name": "Supino reto",
        "equipment_id": 7,
        "equipment_name": "Banco reto",
        "load_convention": "total",
        "local_date": "2026-09-02",
        "record_weight_kg": 32.5,
        "previous_best_weight_kg": 30.0,
        "reps": 6,
    }
