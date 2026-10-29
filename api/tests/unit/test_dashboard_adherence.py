"""Regras do `adherence_service` (F1.1) que nao dependem de banco.

O que cada bloco protege:

1. **As cinco derivacoes do §5** (concluido / interrompido / pulado /
   substituido / extra), cada uma com caso positivo E negativo - sao elas que
   substituem a coluna `execution_status` descartada no §18.
2. **Prescricao congelada vs. ficha legada**: qual fonte valeu e DECLARADA, e
   as duas nunca se misturam no mesmo item.
3. **Aderencia sem Modelo atribuido**: a resposta existe, mas sem denominador
   inventado (`rate: null`, `data_quality=LOW`).
4. **Ciclo arbitrario**: `window=cycle` com 8 dias e com um Modelo
   `Upper 1 / Lower 1 / Upper 2 / Lower 2` - nenhuma regra olha o NOME do
   slot.
5. **Sessao pulada nao desloca comparacao**: o buraco no ciclo nao empurra a
   sessao seguinte para o lugar da que faltou.
6. **Recordes e progressoes** por sessao.
7. **Ownership**: `require_user_id` recusa `None` antes de qualquer SQL.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence.dashboard.contracts import (
    DataQuality,
    PrescriptionSource,
    WindowInfo,
    WindowKind,
)
from app.intelligence.dashboard.adherence_service import (
    NO_PRESCRIPTION_LIMITATION,
    adherence_rate,
    build_session_items,
    build_workouts,
    compute_records_and_progressions,
    record_exercise_ids,
    count_item_statuses,
    derive_item_status,
    expected_sessions_in_window,
    prescribed_from_plan_item,
)
from app.intelligence.dashboard.session_records import exposures_to_compare, parse_history
from app.intelligence.dashboard.metrics_registry import (
    PlanItemStatus,
    SessionExecutionStatus,
    normalize_session_status,
)
from app.intelligence.dashboard.time_windows import resolve_window
from tests.support.fake_db import CursorFactoryConn


def _window(days: int = 28) -> WindowInfo:
    end = date(2026, 9, 22)
    return WindowInfo(
        kind=WindowKind.ROLLING_28D,
        date_from=end - timedelta(days=days - 1),
        date_to=end,
        timezone="America/Sao_Paulo",
        days=days,
    )


def _plan_item(**overrides):
    row = {
        "plan_id": 1,
        "plan_exercise_id": 10,
        "plan_sequence": 1,
        "exercise_name": "Supino reto",
        "mapping_id": 500,
        "sets_count": 3,
        "reps_target": "8-10",
        "load_target": "60",
        "rpe_target": "8",
        "rest_seconds": 90,
        "warmup": 0,
        "notes": None,
        "frozen_sets_count": None,
        "frozen_reps_target": None,
        "frozen_load_target": None,
        "frozen_rpe_target": None,
        "frozen_engine_version": None,
    }
    row.update(overrides)
    return row


def _performed(**overrides):
    row = {
        "activity_id": 99,
        "exercise_row_id": 700,
        "position_in_session": 1,
        "original_name": "Supino reto",
        "workout_plan_exercise_id": 10,
        "exercise_id": 500,
        "notes": None,
        "total_sets": 3,
        "working_sets": 3,
        "total_reps": 27,
        "best_weight_kg": 60.0,
        "rpe_sum": 24.0,
        "rpe_n": 3,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# 1) As cinco derivacoes do §5
# ---------------------------------------------------------------------------


def test_completed_quando_series_de_trabalho_alcancam_o_prescrito():
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=500,
            has_plan_link=True,
            working_sets=3,
            prescribed_sets=3,
        )
        is PlanItemStatus.COMPLETED
    )
    # Fazer MAIS que o prescrito continua concluido - nao existe "excedido".
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=500,
            has_plan_link=True,
            working_sets=5,
            prescribed_sets=3,
        )
        is PlanItemStatus.COMPLETED
    )


def test_interrupted_so_com_pelo_menos_uma_serie_e_abaixo_do_prescrito():
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=500,
            has_plan_link=True,
            working_sets=2,
            prescribed_sets=4,
        )
        is PlanItemStatus.INTERRUPTED
    )
    # Negativo: mesma quantidade, prescrito igual -> nao e interrompido.
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=500,
            has_plan_link=True,
            working_sets=4,
            prescribed_sets=4,
        )
        is PlanItemStatus.COMPLETED
    )


def test_sem_prescrito_nunca_vira_interrompido():
    """Ficha sem `sets_count` nao permite dizer que "faltou" - inventar um
    prescrito seria a mesma armadilha de transformar ausencia em zero."""
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=500,
            has_plan_link=True,
            working_sets=1,
            prescribed_sets=None,
        )
        is PlanItemStatus.COMPLETED
    )


def test_skipped_quando_o_item_nao_teve_nenhuma_serie_de_trabalho():
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=500,
            has_plan_link=True,
            working_sets=0,
            prescribed_sets=3,
        )
        is PlanItemStatus.SKIPPED
    )
    # Negativo: uma unica serie de trabalho ja tira o item de "pulado".
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=500,
            has_plan_link=True,
            working_sets=1,
            prescribed_sets=3,
        )
        is PlanItemStatus.INTERRUPTED
    )


def test_substituted_quando_o_exercicio_executado_e_outro():
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=777,
            has_plan_link=True,
            working_sets=3,
            prescribed_sets=3,
        )
        is PlanItemStatus.SUBSTITUTED
    )
    # Negativo: mesmo exercicio -> nunca substituido, mesmo com o vinculo
    # presente e a quantidade diferente.
    assert (
        derive_item_status(
            planned_exercise_id=500,
            performed_exercise_id=500,
            has_plan_link=True,
            working_sets=1,
            prescribed_sets=3,
        )
        is PlanItemStatus.INTERRUPTED
    )


def test_extra_quando_nao_ha_vinculo_com_item_da_ficha():
    assert (
        derive_item_status(
            planned_exercise_id=None,
            performed_exercise_id=800,
            has_plan_link=False,
            working_sets=4,
            prescribed_sets=None,
        )
        is PlanItemStatus.EXTRA
    )


def test_serie_de_aquecimento_nao_salva_um_item_pulado():
    """`working_sets` ja chega do SQL sem aquecimento/ajuste
    (`sql_not_preparatory`): 3 aquecimentos e nenhuma serie de trabalho e um
    item PULADO, nao concluido."""
    itens = build_session_items(
        [_plan_item()],
        [_performed(working_sets=0, total_sets=3, total_reps=0, rpe_n=0, rpe_sum=0.0)],
    )
    assert itens[0]["status"] == PlanItemStatus.SKIPPED.value
    assert itens[0]["performed"]["total_sets"] == 3


def test_itens_da_sessao_trazem_substituicao_e_quanto_faltou():
    itens = build_session_items(
        [
            _plan_item(plan_exercise_id=10, plan_sequence=1, sets_count=4),
            _plan_item(
                plan_exercise_id=11,
                plan_sequence=2,
                exercise_name="Remada curvada",
                mapping_id=600,
            ),
            _plan_item(plan_exercise_id=12, plan_sequence=3, exercise_name="Rosca direta", mapping_id=700),
        ],
        [
            _performed(exercise_row_id=70, workout_plan_exercise_id=10, working_sets=2, position_in_session=1),
            _performed(
                exercise_row_id=71,
                workout_plan_exercise_id=11,
                exercise_id=601,
                original_name="Remada unilateral",
                position_in_session=2,
            ),
            _performed(
                exercise_row_id=72,
                workout_plan_exercise_id=None,
                exercise_id=900,
                original_name="Abdominal",
                position_in_session=3,
            ),
        ],
    )

    por_status = {item["status"]: item for item in itens}
    assert por_status["INTERRUPTED"]["missing_sets"] == 2
    assert por_status["SUBSTITUTED"]["substitution"] == {
        "planned_exercise_id": 600,
        "planned_name": "Remada curvada",
        "performed_exercise_id": 601,
        "performed_name": "Remada unilateral",
    }
    assert por_status["SKIPPED"]["planned_name"] == "Rosca direta"
    assert por_status["SKIPPED"]["performed"] is None
    assert por_status["EXTRA"]["performed"]["name"] == "Abdominal"

    assert count_item_statuses(itens) == {
        "completed": 0,
        "interrupted": 1,
        "skipped": 1,
        "substituted": 1,
        "extra": 1,
    }


def test_sessao_em_andamento_nao_classifica_item_ainda_nao_executado():
    """Chamar de "pulado" um item de um treino que ainda esta rolando seria
    inventar um fato - o item simplesmente ainda nao existe na leitura."""
    itens = build_session_items([_plan_item()], [], classify_missing=False)
    assert itens == []
    assert normalize_session_status("in_progress") is SessionExecutionStatus.IN_PROGRESS


def test_sessao_sem_ficha_transforma_tudo_em_extra():
    itens = build_session_items([], [_performed(workout_plan_exercise_id=None)])
    assert [item["status"] for item in itens] == [PlanItemStatus.EXTRA.value]


# ---------------------------------------------------------------------------
# 2) Prescricao congelada vs. ficha legada
# ---------------------------------------------------------------------------


def test_prescricao_congelada_tem_prioridade_e_declara_a_fonte():
    prescrito = prescribed_from_plan_item(
        _plan_item(
            sets_count=3,
            reps_target="8-10",
            frozen_sets_count="4",
            frozen_reps_target="6-8",
            frozen_load_target="70",
            frozen_rpe_target="9",
            frozen_engine_version="progression-v2.0.0",
        )
    )
    assert prescrito["source"] is PrescriptionSource.FROZEN_SNAPSHOT
    # O snapshot vale para o item INTEIRO: nenhum campo cai de volta para a
    # coluna, senao a prescricao exibida nunca teria existido.
    assert prescrito["sets_count"] == 4
    assert prescrito["reps_target"] == "6-8"
    assert prescrito["engine_version"] == "progression-v2.0.0"


def test_ficha_legada_usa_as_colunas_e_declara_a_fonte():
    prescrito = prescribed_from_plan_item(_plan_item())
    assert prescrito["source"] is PrescriptionSource.PLAN_COLUMNS
    assert prescrito["sets_count"] == 3
    assert prescrito["engine_version"] is None


def test_item_sem_prescricao_declara_a_limitacao():
    prescrito = prescribed_from_plan_item(_plan_item(sets_count=None, reps_target=None))
    assert prescrito["source"] is PrescriptionSource.UNAVAILABLE

    itens = build_session_items(
        [_plan_item(sets_count=None, reps_target=None)], [_performed(working_sets=1)]
    )
    assert itens[0]["status"] == PlanItemStatus.COMPLETED.value
    assert NO_PRESCRIPTION_LIMITATION in itens[0]["limitations"]
    assert itens[0]["missing_sets"] is None


# ---------------------------------------------------------------------------
# 3) Aderencia sem Modelo: sem denominador inventado
# ---------------------------------------------------------------------------


def test_sem_modelo_nao_existe_sessao_esperada():
    assert expected_sessions_in_window(window_days=28, training_slots=0, cycle_length_days=None) is None
    assert adherence_rate(4.0, None) is None
    # E zero esperado tambem nao vira divisao por zero nem 100%.
    assert adherence_rate(4.0, 0.0) is None


def test_taxa_de_aderencia_e_realizadas_sobre_esperadas():
    assert adherence_rate(3.0, 4.0) == 0.75
    assert adherence_rate(0.0, 4.0) == 0.0


def test_slot_de_descanso_nao_conta_como_sessao_esperada():
    """Um ciclo com um dia de descanso nao pode reprovar quem seguiu o Modelo
    a risca: o esperado sai dos slots de TREINO."""
    esperadas = expected_sessions_in_window(window_days=28, training_slots=3, cycle_length_days=28)
    assert esperadas == 3.0


# ---------------------------------------------------------------------------
# 4) Ciclo arbitrario, sem branch por nome de slot
# ---------------------------------------------------------------------------


def test_window_cycle_com_ciclo_de_oito_dias():
    janela = resolve_window(
        WindowKind.CYCLE,
        today=date(2026, 9, 22),
        cycle_length_days=8,
        timezone_name="America/Sao_Paulo",
    )
    assert janela.days == 8
    assert janela.date_from == date(2026, 9, 15)
    assert janela.date_to == date(2026, 9, 22)


def test_window_cycle_exige_o_tamanho_do_ciclo():
    with pytest.raises(ValueError, match="cycle_length_days"):
        resolve_window(WindowKind.CYCLE, today=date(2026, 9, 22))


@pytest.mark.parametrize(
    "nomes",
    [
        ("Upper 1", "Lower 1", "Upper 2", "Lower 2"),
        ("A", "B", "C", "D"),
        ("Push", "Pull", "Legs", "Full body"),
    ],
)
def test_esperadas_por_ciclo_nao_dependem_do_nome_do_slot(nomes):
    """O plano exige que funcione sem nenhum branch por nome/letra: o que
    manda e a QUANTIDADE de slots de treino."""
    ciclo = len(nomes)  # um slot = um dia (`template_cycle_length_days`)
    assert expected_sessions_in_window(
        window_days=ciclo, training_slots=len(nomes), cycle_length_days=ciclo
    ) == float(len(nomes))


def test_janela_de_duas_voltas_espera_o_dobro():
    assert expected_sessions_in_window(window_days=56, training_slots=4, cycle_length_days=28) == 8.0


# ---------------------------------------------------------------------------
# 5) Sessao pulada nao desloca comparacao
# ---------------------------------------------------------------------------


def _hist(activity_id, *, exercise_id=500, equipment_id=7, convention=None, max_kg=None, min_kg=None, reps_target=None):
    """Linha de `session_records.HISTORY_SQL_TEMPLATE` (uma por exposicao).
    `exercise_row_id` = activity_id * 10 + digito do exercicio."""
    return {
        "activity_id": activity_id,
        "exercise_row_id": activity_id * 10 + (exercise_id % 10),
        "exercise_id": exercise_id,
        "local_date": None,
        "started_at": None,
        "position_in_session": 1,
        "equipment_id": equipment_id,
        "load_convention": convention,
        "prescribed_reps_target": reps_target,
        "max_weight_kg": max_kg,
        "min_weight_kg": min_kg if min_kg is not None else max_kg,
    }


def _sets(activity_id, series, *, exercise_id=500):
    """Series de trabalho da exposicao: `[(reps, kg, rpe), ...]`."""
    return [
        {
            "exercise_row_id": activity_id * 10 + (exercise_id % 10),
            "set_number": index + 1,
            "reps": reps,
            "weight_kg": kg,
            "rpe": rpe,
        }
        for index, (reps, kg, rpe) in enumerate(series)
    ]


def test_sessao_pulada_nao_desloca_a_comparacao_entre_exposicoes():
    """Faltar a um treino reduz a aderencia, mas a sessao seguinte continua
    sendo comparada com a ANTERIOR do mesmo exercicio - o buraco nao empurra
    ninguem para o lugar da que faltou."""
    historico = [_hist(1, max_kg=60.0), _hist(3, max_kg=60.0)]
    series = _sets(1, [(8, 60.0, None)] * 3) + _sets(3, [(10, 60.0, None)] * 3)
    resultado = compute_records_and_progressions(historico, series, [1, 3])
    # mesma carga, mais reps por serie -> progressao; carga igual -> sem recorde
    assert resultado[3] == {"records": 0, "progressions": 1}
    # A primeira sessao do exercicio nao e recorde: nao havia marca a bater.
    assert resultado[1] == {"records": 0, "progressions": 0}


# ---------------------------------------------------------------------------
# 6) Recordes e progressoes - a regra de `comparability`
# ---------------------------------------------------------------------------


def test_recorde_exige_superar_todo_o_historico_da_chave():
    historico = [
        _hist(1, max_kg=60.0),
        _hist(2, max_kg=70.0),  # recorde
        _hist(3, max_kg=65.0),  # queda
        _hist(4, max_kg=68.0),  # subiu contra a anterior, mas nao e recorde
    ]
    assert record_exercise_ids(historico) == {2: {500}}


def test_troca_de_maquina_nao_e_recorde_nem_progressao():
    historico = [
        _hist(1, equipment_id=7, max_kg=40.0),
        _hist(2, equipment_id=9, max_kg=80.0),  # outra maquina: chave nova
    ]
    series = _sets(1, [(10, 40.0, 8)] * 3) + _sets(2, [(12, 80.0, 8)] * 3)
    assert record_exercise_ids(historico) == {}
    assert compute_records_and_progressions(historico, series, [2]) == {2: {"records": 0, "progressions": 0}}


def test_progressao_compara_com_a_anterior_da_mesma_chave_saltando_outra_maquina():
    historico = [
        _hist(1, equipment_id=7, max_kg=40.0),
        _hist(2, equipment_id=9, max_kg=80.0),
        _hist(3, equipment_id=7, max_kg=40.0),
    ]
    series = _sets(1, [(8, 40.0, None)] * 3) + _sets(3, [(10, 40.0, None)] * 3)
    assert compute_records_and_progressions(historico, series, [3])[3]["progressions"] == 1


def test_mesma_maquina_com_convencao_diferente_nao_e_comparavel():
    historico = [
        _hist(1, convention="per_dumbbell", max_kg=20.0),
        _hist(2, convention="total", max_kg=40.0),
    ]
    assert record_exercise_ids(historico) == {}


def test_assistido_menos_assistencia_e_recorde_e_mais_assistencia_nao():
    historico = [
        _hist(1, convention="assisted", max_kg=30.0),
        _hist(2, convention="assisted", max_kg=20.0),  # menos assistencia: recorde
        _hist(3, convention="assisted", max_kg=40.0),  # MAIS assistencia: nao e recorde
    ]
    series = (
        _sets(1, [(8, 30.0, None)] * 3)
        + _sets(2, [(8, 20.0, None)] * 3)
        + _sets(3, [(8, 40.0, None)] * 3)
    )
    assert record_exercise_ids(historico) == {2: {500}}
    resultado = compute_records_and_progressions(historico, series, [3])
    assert resultado[3] == {"records": 0, "progressions": 0}


def test_assistido_mesma_assistencia_mais_reps_e_progressao():
    historico = [
        _hist(1, convention="assisted", max_kg=20.0),
        _hist(2, convention="assisted", max_kg=20.0),
    ]
    series = _sets(1, [(6, 20.0, None)] * 3) + _sets(2, [(8, 20.0, None)] * 3)
    assert compute_records_and_progressions(historico, series, [2])[2]["progressions"] == 1


def test_assistido_menos_assistencia_com_reps_na_faixa_e_progressao():
    historico = [
        _hist(1, convention="assisted", max_kg=30.0, reps_target="8-10"),
        _hist(2, convention="assisted", max_kg=20.0, reps_target="8-10"),
    ]
    series = _sets(1, [(10, 30.0, None)] * 3) + _sets(2, [(8, 20.0, None)] * 3)
    assert compute_records_and_progressions(historico, series, [2])[2] == {"records": 1, "progressions": 1}


def test_mais_carga_com_menos_reps_nao_e_progressao_sem_e1rm():
    historico = [_hist(1, max_kg=60.0), _hist(2, max_kg=70.0)]
    series = _sets(1, [(10, 60.0, None)] * 3) + _sets(2, [(6, 70.0, None)] * 3)
    resultado = compute_records_and_progressions(historico, series, [2])
    # recorde de CARGA sim (70 > 60 na mesma chave); progressao nao.
    assert resultado[2] == {"records": 1, "progressions": 0}


def test_mais_carga_com_menos_reps_progride_quando_o_e1rm_decide():
    historico = [_hist(1, max_kg=60.0), _hist(2, max_kg=80.0)]
    # e1RM: 60 x (1 + 10/30) = 80 ; 80 x (1 + 8/30) = 101,3 (+26%, <= 12 reps)
    series = _sets(1, [(10, 60.0, 10)]) + _sets(2, [(8, 80.0, 10)])
    assert compute_records_and_progressions(historico, series, [2])[2]["progressions"] == 1


def test_menos_series_nao_e_progressao_nem_regressao_por_si():
    historico = [_hist(1, max_kg=60.0), _hist(2, max_kg=60.0)]
    series = _sets(1, [(10, 60.0, None)] * 4) + _sets(2, [(10, 60.0, None)] * 2)
    assert compute_records_and_progressions(historico, series, [2])[2]["progressions"] == 0


def test_sessao_sem_carga_registrada_nao_vira_recorde():
    historico = [_hist(1, max_kg=None), _hist(2, max_kg=40.0)]
    # A sessao sem carga nao e marca a bater; a seguinte nao tem o que superar.
    assert record_exercise_ids(historico) == {}


def test_peso_corporal_nao_tem_recorde_de_carga_mas_progride_por_reps():
    historico = [
        _hist(1, convention="bodyweight", max_kg=None),
        _hist(2, convention="bodyweight", max_kg=None),
    ]
    series = _sets(1, [(10, None, None)] * 3) + _sets(2, [(12, None, None)] * 3)
    assert compute_records_and_progressions(historico, series, [2])[2] == {"records": 0, "progressions": 1}


def test_recorde_identifica_o_exercicio_e_nao_so_a_contagem():
    """O detalhe marca a LINHA que bateu recorde; a lista mostra o total. As
    duas leituras saem da mesma regra."""
    historico = [
        _hist(1, max_kg=60.0),
        _hist(2, max_kg=70.0),
        _hist(2, exercise_id=601, max_kg=40.0),
    ]
    assert record_exercise_ids(historico) == {2: {500}}


def test_historico_sem_equipamento_compara_pela_excecao_do_8_1():
    """Nenhuma exposicao com equipamento resolvido: a chave ignora o
    equipamento (mesma excecao de `assemble_exposures`)."""
    historico = [_hist(1, equipment_id=None, max_kg=60.0), _hist(2, equipment_id=None, max_kg=65.0)]
    assert record_exercise_ids(historico) == {2: {500}}


def test_equipamento_nao_resolvido_misturado_nao_e_comparavel():
    historico = [_hist(1, equipment_id=7, max_kg=60.0), _hist(2, equipment_id=None, max_kg=65.0)]
    assert record_exercise_ids(historico) == {}


def test_exposicoes_comparadas_sao_so_as_da_pagina_e_suas_anteriores():
    historico = [_hist(1, max_kg=60.0), _hist(2, max_kg=60.0), _hist(3, max_kg=60.0)]
    assert exposures_to_compare(parse_history(historico), [3]) == [20, 30]
    assert exposures_to_compare(parse_history(historico), [1]) == []


# ---------------------------------------------------------------------------
# 7) Ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("user_id", [None, 0, -1, "abc"])
def test_servico_recusa_user_id_invalido_antes_de_qualquer_sql(user_id):
    class _ConexaoProibida:
        def cursor(self):  # pragma: no cover - o teste falha se for chamado
            raise AssertionError("nenhuma consulta pode sair com user_id invalido")

    with pytest.raises(ValueError):
        build_workouts(_ConexaoProibida(), user_id=user_id, window=_window())


def test_lista_vazia_responde_insuficiente_e_nao_zeros():
    """Janela sem nenhuma sessao: `items: []` e `INSUFFICIENT` - "nao ha o
    que concluir" nao e "esta tudo bem"."""

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return [{"total": 0}] if "COUNT(*)" in self.sql else []

    payload = build_workouts(CursorFactoryConn(_Cursor), user_id=1, window=_window())
    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["has_more"] is False
    assert payload["data_quality"] is DataQuality.INSUFFICIENT
