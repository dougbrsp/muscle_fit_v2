"""M4 - briefing do exercicio: desempenho (regra do dashboard) x ajuste do
motor (`app/domain/exercise_briefing.py`) e o endpoint agregado
(`app/exercise_briefing.py`).

Sem banco e sem HTTP: a funcao da rota e chamada direto, com
`services.get_workout_plan`, `transaction` e o repositorio de historico
substituidos por dubles.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app import exercise_briefing as route
from app import exercise_description_autogen as autogen
from app.domain import exercise_briefing as briefing
from app.domain.training_history import ExerciseExecution, SessionRecord, SetRecord
from app.security import Identity
from tests.support.fake_db import fake_transaction

# ---------------------------------------------------------------------------
# Desempenho (comparability) x ajuste de hoje (motor)
# ---------------------------------------------------------------------------


def _exposicoes(*exposicoes):
    """Exposicoes montadas pelo MESMO caminho do dashboard
    (`build_exposures` + `assemble_exposures`). Cada argumento:
    `(activity_id, equipment_id, [(reps, kg, rpe), ...])`, em ordem
    cronologica; convencao e faixa prescrita opcionais (4o e 5o itens)."""
    from app.intelligence.dashboard.exposure_service import assemble_exposures, build_exposures

    rows = []
    for indice, item in enumerate(exposicoes):
        activity_id, equipment_id, series = item[0], item[1], item[2]
        convention = item[3] if len(item) > 3 else None
        reps_target = item[4] if len(item) > 4 else None
        for numero, (reps, kg, rpe) in enumerate(series, start=1):
            rows.append(
                {
                    "exercise_row_id": 1000 + indice,
                    "activity_id": activity_id,
                    "local_date": date(2026, 9, 1 + indice),
                    "equipment_id": equipment_id,
                    "equipment_name": f"Aparelho {equipment_id}",
                    "load_convention": convention,
                    "prescribed_reps_target": reps_target,
                    "set_number": numero,
                    "reps": reps,
                    "weight_kg": kg,
                    "rpe": rpe,
                }
            )
    return assemble_exposures(build_exposures(rows), 10)


def test_desempenho_mesma_carga_mais_reps_e_progredindo():
    resultado = briefing.summarize_performance(
        _exposicoes((1, 9, [(8, 40.0, None)] * 3), (2, 9, [(10, 40.0, None)] * 3)), equipment_id=9
    )
    assert resultado["status"] == "PROGRESSING"
    assert resultado["comparable"] is True
    assert resultado["scope"] == briefing.PERFORMANCE_SCOPE_EQUIPMENT


def test_desempenho_mais_carga_com_menos_reps_nao_e_progresso_sem_e1rm():
    resultado = briefing.summarize_performance(
        _exposicoes((1, 9, [(10, 40.0, None)] * 3), (2, 9, [(6, 50.0, None)] * 3)), equipment_id=9
    )
    assert resultado["status"] == "STABLE"
    assert "more_load_fewer_reps_inconclusive" in resultado["reasons"]


def test_desempenho_prefere_o_equipamento_do_item_de_hoje():
    """Ultima exposicao foi em OUTRA maquina; o item de hoje e no aparelho 9,
    que tem historico: compara so as exposicoes do aparelho 9."""
    exposicoes = _exposicoes(
        (1, 9, [(8, 40.0, None)] * 3),
        (2, 9, [(10, 40.0, None)] * 3),
        (3, 7, [(10, 80.0, None)] * 3),
    )
    no_aparelho = briefing.summarize_performance(exposicoes, equipment_id=9)
    assert no_aparelho["status"] == "PROGRESSING"
    assert no_aparelho["equipment_name"] == "Aparelho 9"

    # Sem historico no aparelho de hoje: vale o historico do exercicio, e a
    # troca de maquina na ultima exposicao e NOT_COMPARABLE (nunca "piora").
    outro = briefing.summarize_performance(exposicoes, equipment_id=42)
    assert outro["scope"] == briefing.PERFORMANCE_SCOPE_EXERCISE
    assert outro["status"] == "NOT_COMPARABLE"
    assert outro["comparable"] is False


def test_desempenho_assistido_menos_assistencia_com_reps_na_faixa_e_progresso():
    exposicoes = _exposicoes(
        (1, 9, [(8, 30.0, None)] * 3, "assisted", "8-10"),
        (2, 9, [(8, 20.0, None)] * 3, "assisted", "8-10"),
    )
    assert briefing.summarize_performance(exposicoes, equipment_id=9)["status"] == "PROGRESSING"

    # MAIS assistencia com as mesmas reps nao e progresso.
    piora = _exposicoes(
        (1, 9, [(8, 20.0, None)] * 3, "assisted", "8-10"),
        (2, 9, [(8, 30.0, None)] * 3, "assisted", "8-10"),
    )
    assert briefing.summarize_performance(piora, equipment_id=9)["status"] != "PROGRESSING"


def test_desempenho_sem_historico_nao_tem_comparacao():
    resultado = briefing.summarize_performance([], equipment_id=9)
    assert resultado["status"] == "INSUFFICIENT_DATA"
    assert resultado["comparable"] is False
    assert resultado["scope"] is None


@pytest.mark.parametrize(
    "decision, rotulo",
    [
        ("INCREASE_LOAD", "Aumentar carga"),
        ("ADD_SET", "Acrescentar série"),
        ("APPLY_TECHNIQUE", "Aplicar técnica avançada"),
        ("SET_REP_RANGE_10_12", "Definir faixa de 10 a 12 repetições"),
        ("HOLD", "Manter"),
        ("DECREASE_LOAD", "Reduzir carga"),
        ("SKIP", "Sem progressão nesta sessão"),
    ],
)
def test_ajuste_de_hoje_e_a_acao_do_motor_nunca_rotulo_de_desempenho(decision, rotulo):
    ajuste = briefing.describe_adjustment({"decision": decision, "reason": "motivo do motor"})
    assert ajuste == {
        "decision": decision,
        "label": rotulo,
        "reason": "motivo do motor",
        "source": briefing.SOURCE_DECISION,
        # RPE 10 Lote 6/P9: sinais de recuperação e teto congelados (vazios aqui).
        "recovery_signals": [],
        "effective_rpe_ceiling": None,
    }
    assert ajuste["label"] not in {"Progredindo", "Mantendo", "Regredindo", "Estável"}


@pytest.mark.parametrize("indicador", ["increased", "decreased", "same"])
def test_indicador_do_motor_legado_nao_vira_ajuste_de_hoje(indicador):
    """M76 (C9): o "Ajuste de hoje" so sai da decisao do motor v2; a seta
    legada (`progression`) nao gera badge."""
    assert briefing.describe_adjustment({"progression": indicador}) is None
    assert not hasattr(briefing, "SOURCE_LEGACY")


def test_sem_decisao_nao_ha_ajuste_e_nao_levanta():
    assert briefing.describe_adjustment(None) is None
    assert briefing.describe_adjustment({"suggested_load_kg": 40}) is None


def test_trend_legado_e_derivado_do_desempenho_e_nao_da_decisao():
    """O motor manda REDUZIR carga hoje, mas a ultima execucao foi melhor que a
    anterior: o rotulo e "Progredindo" (desempenho) e a reducao aparece so
    como ajuste."""
    desempenho = briefing.summarize_performance(
        _exposicoes((1, 9, [(8, 40.0, None)] * 3), (2, 9, [(10, 40.0, None)] * 3)), equipment_id=9
    )
    ajuste = briefing.describe_adjustment({"decision": "DECREASE_LOAD"})
    trend = briefing.trend_from_performance(desempenho, ajuste)
    assert trend["status"] == briefing.PROGRESSING
    assert trend["label"] == "Progredindo"
    assert "Ajuste de hoje: reduzir carga." in trend["reason"]
    assert trend["source"] == briefing.SOURCE_COMPARABILITY


def test_trend_legado_sem_comparacao():
    trend = briefing.trend_from_performance(briefing.performance_unavailable(), None)
    assert trend["status"] == briefing.UNKNOWN
    assert trend["label"] == "Sem comparação"


# ---------------------------------------------------------------------------
# Ultima execucao
# ---------------------------------------------------------------------------


def _set(numero, tipo, reps, peso, rpe=None):
    return SetRecord(
        set_number=numero,
        set_type=tipo,
        reps=reps,
        weight_kg=Decimal(str(peso)),
        rpe=None if rpe is None else Decimal(str(rpe)),
    )


def _execucao(exercise_id=10, wpe_id=None, sets=()):
    return ExerciseExecution(
        exercise_row_id=1,
        sequence=1,
        exercise_id=exercise_id,
        equipment_id=5,
        workout_plan_exercise_id=wpe_id,
        sets=tuple(sets),
    )


def _sessao(activity_id, dia, execucoes):
    return SessionRecord(activity_id=activity_id, local_date=dia, exercises=tuple(execucoes))


def test_resumo_conta_so_series_de_trabalho_mas_lista_todas():
    execucao = _execucao(
        sets=[_set(1, "warmup", 15, 20), _set(2, "working", 10, 40, 8), _set(3, "working", 8, 42.5, 9)]
    )
    resumo = briefing.summarize_last_execution(execucao, local_date=date(2026, 9, 10), activity_id=77)
    assert len(resumo["sets"]) == 3
    assert resumo["working_sets"] == 2
    assert resumo["top_weight_kg"] == 42.5
    assert (resumo["reps_min"], resumo["reps_max"]) == (8, 10)
    assert resumo["top_rpe"] == 9.0
    assert resumo["local_date"] == "2026-09-10"


def test_resumo_de_execucao_inexistente_e_none():
    assert briefing.summarize_last_execution(None) is None


def test_ultima_execucao_e_a_mais_recente_do_mesmo_exercicio():
    sessions = [
        _sessao(1, date(2026, 9, 1), [_execucao(sets=[_set(1, "working", 10, 30)])]),
        _sessao(2, date(2026, 9, 5), [_execucao(exercise_id=99, sets=[_set(1, "working", 10, 99)])]),
        _sessao(3, date(2026, 9, 8), [_execucao(sets=[_set(1, "working", 10, 40)])]),
    ]
    session, execucao = briefing.find_last_execution(sessions, exercise_id=10)
    assert session.activity_id == 3
    assert execucao.sets[0].weight_kg == Decimal("40")


def test_a_propria_linha_da_ficha_em_execucao_nao_conta_como_ultima_execucao():
    """"Ultima execucao" e a ANTERIOR a esta - nunca a sessao de hoje, que o
    usuario acabou de comecar a partir da mesma linha da ficha."""
    sessions = [
        _sessao(1, date(2026, 9, 1), [_execucao(wpe_id=500, sets=[_set(1, "working", 10, 30)])]),
        _sessao(2, date(2026, 9, 8), [_execucao(wpe_id=777, sets=[_set(1, "working", 10, 40)])]),
    ]
    session, _ = briefing.find_last_execution(sessions, exercise_id=10, workout_plan_exercise_id=777)
    assert session.activity_id == 1


def test_exercicio_sem_historico_nenhum():
    assert briefing.find_last_execution([], exercise_id=10) is None
    assert briefing.find_last_execution([_sessao(1, date(2026, 9, 1), [])], exercise_id=None) is None


# ---------------------------------------------------------------------------
# Endpoint agregado
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, respostas):
        self.respostas = respostas
        self.executados: list[str] = []
        self._rows: list[dict] = []

    def execute(self, sql, params=None):
        normalizado = " ".join(sql.split())
        self.executados.append(normalizado)
        self._rows = []
        for trecho, rows in self.respostas.items():
            if trecho in normalizado:
                self._rows = list(rows)
                break

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


PLANO = {
    "id": 3,
    "exercises": [
        {
            "workout_plan_exercise_id": 777,
            "sequence": 2,
            "exercise_name": "Supino reto",
            "mapping_id": 10,
            "preferred_equipment_id": 55,
            "equipment_name": "Banco supino",
            "sets_count": 3,
            "reps_target": "10-12",
            "rest_seconds": 90,
            "advanced_technique": None,
            "prescription_status": "FROZEN",
            "prescription": {
                "sets_count": 3,
                "reps_target": "10-12",
                "load_target": "40",
                "rpe_target": "9",
                "effective_rpe_target": 8,
                "effective_rpe_ceiling": 9,
                "suggested_load_kg": 42.5,
                "advanced_technique": "drop_set",
                "decision": "INCREASE_LOAD",
                "reason": "Bateu o topo da faixa com folga.",
                "warnings": [],
            },
        }
    ],
}

RESPOSTAS_COMPLETAS = {
    "FROM exercise_legacy_id_map": [{"canonical_exercise_id": 10}],
    "FROM exercise_catalog WHERE id": [
        {
            "id": 10,
            "name": "Supino reto com barra",
            "description": "descrição curta",
            "execution_description": "Execução:\n- Empurre a barra.",
            "execution_description_reviewed_at": "2026-09-19T10:00:00",
            "is_unilateral": 0,
            "is_compound": 1,
        }
    ],
    "FROM exercise_muscle_contributions": [
        {"muscle_group_id": 1, "muscle_group_name": "Peitoral", "role": "PRIMARY", "stimulus_weight": Decimal("1.0")}
    ],
    "FROM gym_equipment ge": [
        {
            "gym_equipment_id": 55,
            "equipment_id": 9,
            "name": "Banco supino reto",
            "photo_url": "https://app.musclefit.com.br/equipment/9.jpg",
        }
    ],
    "FROM progression_tracks": [
        {
            "load_status": "ACTIVE",
            "working_load_kg": Decimal("40"),
            "sets_count": 3,
            "technique_code": None,
            "last_executed_at": None,
            "phase": "F2",
            "phase_status": "ACTIVE",
            "reps_min": 10,
            "reps_max": 12,
        }
    ],
}

IDENTIDADE = Identity(user_id=1, role="regular", source="session")


def _montar(monkeypatch, respostas, *, plano=PLANO, sessions=(), disparos=None, autogen_real=False):
    cursor = FakeCursor(respostas)

    class _Repo:
        def __init__(self, conn):
            pass

        def fetch_last_completed_sessions(self, user_id, n):
            return list(sessions)

    monkeypatch.setattr(route.services, "get_workout_plan", lambda plan_id, user_id=None: plano)
    monkeypatch.setattr(route, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(route, "TrainingHistoryRepository", _Repo)
    # A rede de seguranca da descricao de execucao e dublada por padrao: o
    # teste do briefing prova a TELA. `autogen_real=True` e para os dois casos
    # que precisam do modulo de verdade - e mesmo eles nunca chegam na OpenAI,
    # porque `invoke_async`/`transaction` continuam dublados.
    if not autogen_real:
        monkeypatch.setattr(
            route.exercise_description_autogen,
            "dispatch_if_missing",
            lambda exercise_id, **kwargs: (disparos if disparos is not None else []).append(exercise_id) or False,
        )
    return cursor


def test_briefing_entrega_tudo_o_que_a_tela_precisa_numa_chamada(monkeypatch):
    sessions = [_sessao(9, date(2026, 9, 8), [_execucao(wpe_id=500, sets=[_set(1, "working", 10, 40, 9)])])]
    _montar(monkeypatch, RESPOSTAS_COMPLETAS, sessions=sessions)

    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)

    assert resposta["exercise"]["name"] == "Supino reto"
    assert resposta["exercise"]["execution_description"].startswith("Execução:")
    assert resposta["exercise"]["execution_description_reviewed"] is True
    assert resposta["equipment"]["photo_url"].endswith("/equipment/9.jpg")
    assert [m["name"] for m in resposta["muscles"]] == ["Peitoral"]
    assert resposta["prescription"]["sets_count"] == 3
    assert resposta["prescription"]["advanced_technique"] == "drop_set"
    assert resposta["prescription"]["effective_rpe_ceiling"] == 9
    assert resposta["last_execution"]["top_weight_kg"] == 40.0
    # Sem exposicao comparavel (o cursor falso nao devolve historico): sem
    # veredito de desempenho. A decisao do motor sai como AJUSTE.
    assert resposta["performance"]["comparable"] is False
    assert resposta["trend"]["label"] == "Sem comparação"
    assert resposta["adjustment"]["decision"] == "INCREASE_LOAD"
    assert resposta["adjustment"]["label"] == "Aumentar carga"
    assert resposta["track"]["phase"] == "F2"


SEM_DESCRICAO = {
    **RESPOSTAS_COMPLETAS,
    "FROM exercise_catalog WHERE id": [
        {
            "id": 10,
            "name": "Supino reto com barra",
            "description": None,
            "execution_description": None,
            "execution_description_reviewed_at": None,
            "is_unilateral": 0,
            "is_compound": 1,
        }
    ],
}


def test_briefing_entrega_o_card_publicado_do_exercicio_canonico(monkeypatch):
    """PR5 dos cards: `exercise.exercise_card` sai do ponteiro do catalogo do
    id CANONICO (o cursor devolve 10 para o mapa legado) e as URLs, do helper
    de `public_assets` - nunca de literal de dominio."""
    monkeypatch.setenv("PUBLIC_ASSETS_BASE_URL", "https://cdn.teste")
    respostas = {
        **RESPOSTAS_COMPLETAS,
        "FROM exercise_catalog WHERE id": [
            {**RESPOSTAS_COMPLETAS["FROM exercise_catalog WHERE id"][0], "card_version": 2}
        ],
    }
    cursor = _montar(monkeypatch, respostas)

    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)

    assert resposta["exercise"]["exercise_card"] == {
        "url": "https://cdn.teste/exercise-cards/10/v2/card.webp",
        "thumbnail_url": "https://cdn.teste/exercise-cards/10/v2/thumb.webp",
        "version": 2,
    }
    # Zero consulta nova: o card vem da MESMA linha do catalogo ja lida.
    assert sum("FROM exercise_catalog" in sql for sql in cursor.executados) == 1
    assert any("card_version" in sql for sql in cursor.executados if "FROM exercise_catalog" in sql)


def test_briefing_sem_card_publicado_devolve_null(monkeypatch):
    _montar(monkeypatch, RESPOSTAS_COMPLETAS)
    assert route.get_exercise_briefing(3, 777, identity=IDENTIDADE)["exercise"]["exercise_card"] is None


def test_exercicio_sem_descricao_gerada_nao_quebra_a_tela(monkeypatch):
    _montar(monkeypatch, SEM_DESCRICAO)
    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert resposta["exercise"]["execution_description"] is None
    assert resposta["exercise"]["execution_description_reviewed"] is False
    assert resposta["exercise"]["name"] == "Supino reto"


# ---------------------------------------------------------------------------
# Rede de seguranca: geracao da descricao ENFILEIRADA, nunca esperada
# ---------------------------------------------------------------------------


def test_exercicio_sem_descricao_enfileira_a_geracao_e_responde_null_na_hora(monkeypatch):
    """O contrato da tela nao muda: `null` hoje, texto na proxima abertura."""
    disparos: list = []
    _montar(monkeypatch, SEM_DESCRICAO, disparos=disparos)

    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)

    assert disparos == [10]
    assert resposta["exercise"]["execution_description"] is None


def test_exercicio_que_ja_tem_descricao_nao_enfileira_nada(monkeypatch):
    disparos: list = []
    _montar(monkeypatch, RESPOSTAS_COMPLETAS, disparos=disparos)
    route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert disparos == []


BRANCOS = "   " + chr(10) + "  "


def test_descricao_so_com_espacos_conta_como_ausente(monkeypatch):
    respostas = {
        **RESPOSTAS_COMPLETAS,
        "FROM exercise_catalog WHERE id": [
            {**SEM_DESCRICAO["FROM exercise_catalog WHERE id"][0], "execution_description": BRANCOS}
        ],
    }
    disparos: list = []
    _montar(monkeypatch, respostas, disparos=disparos)
    assert route.get_exercise_briefing(3, 777, identity=IDENTIDADE)["exercise"]["execution_description"] == BRANCOS
    assert disparos == [10]


def test_banco_fora_do_ar_no_disparo_nao_derruba_o_briefing(monkeypatch):
    """O requisito duro: o briefing NUNCA responde 500 por causa disto.

    Aqui o modulo REAL de disparo e usado (nao o duble), com o banco dele
    explodindo: e a combinacao endpoint + rede de seguranca que precisa
    aguentar, nao cada metade isolada."""
    _montar(monkeypatch, SEM_DESCRICAO, autogen_real=True)
    monkeypatch.setenv(autogen.GENERATOR_FUNCTION_ENV_VAR, "musclefit-plan-generator")

    def _transaction_quebrada():
        raise RuntimeError("database_unavailable")

    monkeypatch.setattr(autogen, "transaction", _transaction_quebrada)

    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert resposta["exercise"]["execution_description"] is None
    assert resposta["exercise"]["name"] == "Supino reto"


def test_openai_fora_do_ar_nao_e_sequer_tocada_pelo_briefing(monkeypatch):
    """A tela nao espera geracao nenhuma: nenhuma chamada a OpenAI sai deste
    processo. O duble de `invoke_async` prova que o que sai e um enfileiramento
    - e o teste quebraria se alguem trocasse isso por uma chamada sincrona."""
    _montar(monkeypatch, SEM_DESCRICAO, autogen_real=True)
    monkeypatch.setenv(autogen.GENERATOR_FUNCTION_ENV_VAR, "musclefit-plan-generator")

    class _Cursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=None):
            assert "OPENAI" not in sql.upper()

    @contextmanager
    def _transaction():
        yield type("C", (), {"cursor": lambda self: _Cursor()})()

    enfileirados: list = []
    monkeypatch.setattr(autogen, "transaction", _transaction)
    monkeypatch.setattr(autogen.async_jobs, "create_job", lambda job_type, request=None, *, owner_user_id: "job-x")
    monkeypatch.setattr(autogen.async_jobs, "invoke_async", lambda env, payload: enfileirados.append(payload))

    route.get_exercise_briefing(3, 777, identity=IDENTIDADE)

    assert enfileirados == [{"job_id": "job-x", "job_kind": "exercise-description", "exercise_id": 10}]


def test_exercicio_fora_do_catalogo_nao_enfileira_nada(monkeypatch):
    """Sem linha no catalogo nao ha o que gerar - nem id para reservar."""
    respostas = {**RESPOSTAS_COMPLETAS, "FROM exercise_catalog WHERE id": []}
    disparos: list = []
    _montar(monkeypatch, respostas, disparos=disparos)
    route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert disparos == []


def test_equipamento_sem_foto_devolve_photo_url_nula_e_o_resto_inteiro(monkeypatch):
    respostas = dict(RESPOSTAS_COMPLETAS)
    respostas["FROM gym_equipment ge"] = [
        {"gym_equipment_id": 55, "equipment_id": 9, "name": "Banco supino reto", "photo_url": None}
    ]
    _montar(monkeypatch, respostas)
    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert resposta["equipment"]["photo_url"] is None
    assert resposta["equipment"]["name"] == "Banco supino reto"


def test_exercicio_sem_equipamento_resolvido_cai_no_nome_da_ficha(monkeypatch):
    plano = {"exercises": [dict(PLANO["exercises"][0], preferred_equipment_id=None)]}
    _montar(monkeypatch, RESPOSTAS_COMPLETAS, plano=plano)
    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert resposta["equipment"] == {
        "gym_equipment_id": None,
        "equipment_id": None,
        "name": "Banco supino",
        "photo_url": None,
    }


def test_exercicio_sem_historico_anterior_responde_sem_comparacao(monkeypatch):
    plano = {"exercises": [dict(PLANO["exercises"][0], prescription={"sets_count": 3})]}
    _montar(monkeypatch, RESPOSTAS_COMPLETAS, plano=plano, sessions=[])
    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert resposta["last_execution"] is None
    assert resposta["performance"]["status"] == "INSUFFICIENT_DATA"
    assert resposta["adjustment"] is None
    assert resposta["trend"]["status"] == briefing.UNKNOWN
    assert resposta["trend"]["label"] == "Sem comparação"


def test_badge_reflete_o_desempenho_e_a_decisao_do_motor_sai_separada(monkeypatch):
    """O motor manda AUMENTAR carga (PLANO), mas a ultima execucao no aparelho
    de hoje (equipment_id 9) foi pior que a anterior: o badge e "Regredindo"
    e o aumento aparece so como ajuste de hoje."""
    from app.intelligence.dashboard import exposure_service

    exposicoes = _exposicoes((1, 9, [(10, 40.0, None)] * 3), (2, 9, [(8, 40.0, None)] * 3))
    chamadas: list = []

    def _get(conn, exercise_id, *, user_id, date_from, date_to, limit, **_):
        chamadas.append((exercise_id, user_id, date_to - date_from))
        return exposicoes

    monkeypatch.setattr(exposure_service, "get_exposures_between", _get)
    _montar(monkeypatch, RESPOSTAS_COMPLETAS)

    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)

    assert chamadas and chamadas[0][0] == 10 and chamadas[0][1] == 1
    assert chamadas[0][2].days == 90  # exposure_lookback_days do dashboard
    assert resposta["performance"]["status"] == "REGRESSING"
    assert resposta["performance"]["scope"] == briefing.PERFORMANCE_SCOPE_EQUIPMENT
    assert resposta["trend"]["label"] == "Regredindo"
    assert resposta["adjustment"]["decision"] == "INCREASE_LOAD"
    assert resposta["adjustment"]["reason"] == "Bateu o topo da faixa com folga."


def test_falha_ao_classificar_o_desempenho_nao_derruba_o_briefing(monkeypatch):
    from app.intelligence.dashboard import exposure_service

    def _quebra(*args, **kwargs):
        raise RuntimeError("timeout do banco")

    monkeypatch.setattr(exposure_service, "get_exposures_between", _quebra)
    _montar(monkeypatch, RESPOSTAS_COMPLETAS)
    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert resposta["performance"]["comparable"] is False
    assert resposta["adjustment"]["decision"] == "INCREASE_LOAD"


def test_ambiente_sem_as_tabelas_de_progressao_devolve_track_nulo_em_vez_de_500(monkeypatch):
    class CursorQueExplodeNaTrilha(FakeCursor):
        def execute(self, sql, params=None):
            if "progression_tracks" in sql:
                raise RuntimeError("1146 Table 'progression_tracks' doesn't exist")
            super().execute(sql, params)

    cursor = CursorQueExplodeNaTrilha(RESPOSTAS_COMPLETAS)

    monkeypatch.setattr(route.services, "get_workout_plan", lambda plan_id, user_id=None: PLANO)
    monkeypatch.setattr(route, "transaction", fake_transaction(cursor))
    monkeypatch.setattr(route, "TrainingHistoryRepository", lambda conn: type("R", (), {"fetch_last_completed_sessions": lambda self, u, n: []})())

    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert resposta["track"] is None
    assert resposta["exercise"]["name"] == "Supino reto"


def test_falha_ao_ler_o_historico_nao_impede_o_briefing(monkeypatch):
    class RepoQuebrado:
        def __init__(self, conn):
            pass

        def fetch_last_completed_sessions(self, user_id, n):
            raise RuntimeError("timeout do banco")

    _montar(monkeypatch, RESPOSTAS_COMPLETAS)
    monkeypatch.setattr(route, "TrainingHistoryRepository", RepoQuebrado)
    resposta = route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert resposta["last_execution"] is None
    # A decisao do motor continua valendo como ajuste de hoje.
    assert resposta["adjustment"]["decision"] == "INCREASE_LOAD"


def test_ficha_de_outro_usuario_ou_inexistente_responde_404(monkeypatch):
    monkeypatch.setattr(route.services, "get_workout_plan", lambda plan_id, user_id=None: None)
    with pytest.raises(HTTPException) as exc:
        route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert exc.value.status_code == 404


def test_exercicio_que_nao_e_desta_ficha_responde_404(monkeypatch):
    _montar(monkeypatch, RESPOSTAS_COMPLETAS)
    with pytest.raises(HTTPException) as exc:
        route.get_exercise_briefing(3, 999, identity=IDENTIDADE)
    assert exc.value.status_code == 404


def test_briefing_nao_escreve_nada_no_banco(monkeypatch):
    cursor = _montar(monkeypatch, RESPOSTAS_COMPLETAS)
    route.get_exercise_briefing(3, 777, identity=IDENTIDADE)
    assert all(sql.upper().startswith("SELECT") for sql in cursor.executados)
