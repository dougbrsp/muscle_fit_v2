"""Regras do `muscle_summary_service` (F1.2) que nao dependem de banco.

O que cada bloco protege:

1. **Realizado x planejado x projetado nunca se somam** e o `status` sai SO
   do realizado - o defeito que este PR corrige (a tela v2 classificava
   `realizado + projetado`).
2. **Frequencia** (`freq-v1`): mediana do intervalo, `None` com menos de dois
   estimulos (nunca zero).
3. **`target_source` derruba a qualidade** quando cai no fallback generico.
4. **Direto / indireto / estabilizador** ficam em campos separados.
5. **Ownership**: `require_user_id` recusa `None` antes de qualquer SQL.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.intelligence.dashboard.contracts import DataNature, DataQuality, TargetSource, WindowInfo, WindowKind
from app.intelligence.dashboard.muscle_summary_service import (
    FALLBACK_TARGET_LIMITATION,
    NO_DIRECT_WORK_LIMITATION,
    _group_sets_into_sessions,
    _top_exercises,
    build_muscle_summary,
    envelope_quality,
    item_data_quality,
    median_interval_hours,
    metric,
    resolve_targets,
    weekly_rate,
)
from app.intelligence.volume_service import (
    DASHBOARD_FALLBACK_MAX_SETS,
    DASHBOARD_FALLBACK_MIN_SETS,
    DASHBOARD_FALLBACK_TARGET_SETS,
    classify_weekly_volume_status,
)


def _window(days: int = 28) -> WindowInfo:
    end = date(2026, 9, 22)
    return WindowInfo(
        kind=WindowKind.ROLLING_28D,
        date_from=end - timedelta(days=days - 1),
        date_to=end,
        timezone="America/Sao_Paulo",
        days=days,
    )


# ---------------------------------------------------------------------------
# 1) Realizado x planejado x projetado
# ---------------------------------------------------------------------------


def test_normalizacao_semanal_usa_a_janela_e_nao_um_numero_fixo():
    """28 dias de volume contra uma meta SEMANAL tem que virar taxa semanal -
    sem isso todo grupo estouraria `ABOVE_MAX` (o defeito que
    `_build_muscle_status_items` ja tinha corrigido na v2)."""
    assert weekly_rate(40.0, 28) == 10.0
    assert weekly_rate(10.0, 7) == 10.0
    assert weekly_rate(None, 28) is None
    # Janela de zero dia nao vira divisao por zero nem zero inventado.
    assert weekly_rate(10.0, 0) is None


def test_status_do_realizado_nao_muda_quando_ha_projetado():
    """O projetado vive em campo proprio: mudar a projecao NAO pode mover o
    status. Prova direta contra o comportamento de `dashboard-cycle`."""
    realizado_semanal = 4.0
    minimo, alvo, maximo = 10.0, 14.0, 20.0

    status_sozinho = classify_weekly_volume_status(realizado_semanal, minimo, alvo, maximo)
    status_somando_projecao = classify_weekly_volume_status(
        realizado_semanal + 10.0, minimo, alvo, maximo
    )

    assert status_sozinho == "BELOW_MIN"
    assert status_somando_projecao == "ON_TARGET"
    # O servico usa o PRIMEIRO. O teste de integracao
    # (`test_status_classifica_so_o_realizado`) prova isso ponta a ponta.


def test_metric_preserva_none_e_nunca_inventa_zero():
    vazio = metric(None, unit="séries", nature=DataNature.DERIVED, version_key="effective_sets_stimulus")
    zero = metric(0.0, unit="séries", nature=DataNature.DERIVED, version_key="effective_sets_stimulus")

    assert vazio["value"] is None
    assert zero["value"] == 0.0
    assert vazio["metric_version"] == zero["metric_version"] == "effective-sets-v1"


def test_planejado_e_projetado_tem_natureza_propria():
    planejado = metric(12.0, unit="séries", nature=DataNature.PLANNED, version_key="effective_sets_stimulus")
    projetado = metric(6.0, unit="séries no ciclo", nature=DataNature.PROJECTED, version_key="effective_sets_stimulus")

    assert planejado["nature"] is DataNature.PLANNED
    assert projetado["nature"] is DataNature.PROJECTED
    # Unidades diferentes de proposito: uma projecao de ciclo nao e uma taxa
    # semanal e nao pode ser lida lado a lado com a meta.
    assert planejado["unit"] != projetado["unit"]


# ---------------------------------------------------------------------------
# 2) Frequencia (`freq-v1`)
# ---------------------------------------------------------------------------


def test_mediana_de_intervalo_entre_estimulos():
    base = datetime(2026, 9, 1, 8, 0)
    estimulos = [base, base + timedelta(hours=48), base + timedelta(hours=120)]
    # intervalos: 48h e 72h -> mediana 60h
    assert median_interval_hours(estimulos) == 60.0


def test_mediana_ignora_ordem_de_entrada():
    base = datetime(2026, 9, 1, 8, 0)
    fora_de_ordem = [base + timedelta(hours=120), base, base + timedelta(hours=48)]
    assert median_interval_hours(fora_de_ordem) == 60.0


@pytest.mark.parametrize("estimulos", [[], [datetime(2026, 9, 1, 8, 0)]])
def test_mediana_e_none_com_menos_de_dois_estimulos(estimulos):
    """Um treino so nao tem intervalo. Zero diria que o grupo foi treinado
    duas vezes no mesmo instante."""
    assert median_interval_hours(estimulos) is None


def test_mediana_resiste_a_uma_pausa_longa_isolada():
    """Mediana, nao media: uma pausa de ferias nao pode desabar a leitura de
    frequencia de uma janela inteira."""
    base = datetime(2026, 7, 1, 8, 0)
    estimulos = [base + timedelta(hours=48 * i) for i in range(5)]
    estimulos.append(estimulos[-1] + timedelta(days=30))
    assert median_interval_hours(estimulos) == 48.0


# ---------------------------------------------------------------------------
# 3) `target_source` e qualidade
# ---------------------------------------------------------------------------


def test_meta_explicita_preserva_a_origem_por_grupo():
    targets = {5: {"min": 10.0, "target": 14.0, "max": 20.0}}
    minimo, alvo, maximo, origem = resolve_targets(5, targets, TargetSource.ASSIGNED_TEMPLATE)
    assert (minimo, alvo, maximo) == (10.0, 14.0, 20.0)
    assert origem is TargetSource.ASSIGNED_TEMPLATE


def test_grupo_sem_linha_de_meta_cai_em_fallback_mesmo_com_meta_de_ativacao():
    """Ter Meta de Ativacao atribuida NAO garante meta para TODO grupo. A v2
    marcava a resposta inteira como `training_type`; aqui a origem e por
    item."""
    targets = {5: {"min": 10.0, "target": 14.0, "max": 20.0}}
    minimo, alvo, maximo, origem = resolve_targets(99, targets, TargetSource.ASSIGNED_TEMPLATE)
    assert (minimo, alvo, maximo) == (
        DASHBOARD_FALLBACK_MIN_SETS,
        DASHBOARD_FALLBACK_TARGET_SETS,
        DASHBOARD_FALLBACK_MAX_SETS,
    )
    assert origem is TargetSource.FALLBACK_GENERIC


def test_fallback_generico_derruba_a_qualidade_do_item():
    quality, limitations = item_data_quality(
        target_source=TargetSource.FALLBACK_GENERIC, direct_sets=8.0, unattributed_sets_in_window=0
    )
    assert quality is DataQuality.LOW
    assert FALLBACK_TARGET_LIMITATION in limitations


def test_meta_cadastrada_com_trabalho_direto_mantem_qualidade_alta():
    quality, limitations = item_data_quality(
        target_source=TargetSource.ASSIGNED_TEMPLATE, direct_sets=8.0, unattributed_sets_in_window=0
    )
    assert quality is DataQuality.HIGH
    assert limitations == []


def test_sem_trabalho_direto_vira_leitura_parcial():
    quality, limitations = item_data_quality(
        target_source=TargetSource.EXPLICIT, direct_sets=0.0, unattributed_sets_in_window=0
    )
    assert quality is DataQuality.MEDIUM
    assert NO_DIRECT_WORK_LIMITATION in limitations


def test_series_sem_contribuicao_cadastrada_viram_limitacao_do_item():
    quality, limitations = item_data_quality(
        target_source=TargetSource.EXPLICIT, direct_sets=8.0, unattributed_sets_in_window=12
    )
    assert quality is DataQuality.MEDIUM
    assert any("12 serie(s)" in limitation for limitation in limitations)


def test_qualidade_do_envelope_e_a_pior_dos_itens():
    itens = [{"data_quality": DataQuality.HIGH}, {"data_quality": DataQuality.LOW}]
    assert envelope_quality(itens) is DataQuality.LOW


def test_envelope_sem_item_e_insuficiente_e_nao_alto():
    """Lista vazia nao e "tudo certo": e "nao ha o que concluir"."""
    assert envelope_quality([]) is DataQuality.INSUFFICIENT


# ---------------------------------------------------------------------------
# 4) Direto / indireto / estabilizador
# ---------------------------------------------------------------------------


def _set_row(role: str, *, set_id: int, stimulus: float, exercise_row_id: int = 1) -> dict:
    return {
        "activity_id": 10,
        "activity_name": "Treino A",
        "local_date": date(2026, 9, 20),
        "trained_at": datetime(2026, 9, 20, 7, 30),
        "exercise_row_id": exercise_row_id,
        "position_in_session": 1,
        "original_name": "Supino reto",
        "exercise_id": 5001,
        "role": role,
        "stimulus_weight": stimulus,
        "fatigue_weight": stimulus,
        "set_id": set_id,
        "set_number": set_id,
        "set_type": "working",
        "reps": 10,
        "weight_kg": 60.0,
        "rpe": 8.0,
    }


def test_estabilizador_nao_e_fundido_com_secundario():
    """`STABILIZER` e um papel proprio: fundi-lo com `SECONDARY` esconderia
    que o grupo so participou segurando a carga."""
    rows = [
        _set_row("PRIMARY", set_id=1, stimulus=1.0),
        _set_row("SECONDARY", set_id=1, stimulus=0.5, exercise_row_id=1),
        _set_row("STABILIZER", set_id=2, stimulus=0.25, exercise_row_id=2),
    ]
    sessoes = _group_sets_into_sessions(rows, {})
    papeis = {exposicao["role"] for sessao in sessoes for exposicao in sessao["exposures"]}
    assert papeis == {"PRIMARY", "SECONDARY", "STABILIZER"}


def test_series_fisicas_da_sessao_contam_series_distintas():
    """A MESMA serie pode chegar ao grupo por dois papeis; conta-la duas vezes
    seria contar o mesmo trabalho duas vezes."""
    rows = [
        _set_row("PRIMARY", set_id=1, stimulus=1.0),
        _set_row("STABILIZER", set_id=1, stimulus=0.25, exercise_row_id=2),
    ]
    (sessao,) = _group_sets_into_sessions(rows, {})
    assert sessao["physical_sets"] == 1
    # O estimulo, esse sim, soma os dois papeis (sao contribuicoes distintas).
    assert sessao["effective_sets_stimulus"] == 1.25


def test_sessao_sem_papel_primario_nao_conta_como_trabalho_direto():
    rows = [_set_row("SECONDARY", set_id=1, stimulus=0.5)]
    (sessao,) = _group_sets_into_sessions(rows, {})
    assert sessao["has_direct_work"] is False


def test_peso_de_contribuicao_por_exercicio():
    """`contribution_share` responde "quanto deste grupo veio deste
    exercicio" - sem ele, uma lista de nomes nao responde nada."""
    rows = [
        {"exercise_id": 1, "original_name": "Supino", "role": "PRIMARY", "sets_count": 6, "stimulus_sets": 6.0, "last_local_date": date(2026, 9, 20)},
        {"exercise_id": 2, "original_name": "Crucifixo", "role": "SECONDARY", "sets_count": 4, "stimulus_sets": 2.0, "last_local_date": date(2026, 9, 18)},
    ]
    top = _top_exercises(rows, group_stimulus=8.0, names_by_exercise={1: "Supino reto", 2: "Crucifixo"})
    assert [t["name"] for t in top] == ["Supino reto", "Crucifixo"]
    assert top[0]["contribution_share"] == 0.75
    assert top[1]["contribution_share"] == 0.25


def test_peso_de_contribuicao_e_none_quando_o_grupo_nao_teve_estimulo():
    rows = [{"exercise_id": 1, "original_name": "Supino", "role": "PRIMARY", "sets_count": 0, "stimulus_sets": 0.0, "last_local_date": None}]
    (top,) = _top_exercises(rows, group_stimulus=0.0, names_by_exercise={})
    assert top["contribution_share"] is None


# ---------------------------------------------------------------------------
# 5) Ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("user_id", [None, 0, -1, "abc"])
def test_servico_recusa_user_id_invalido_antes_de_qualquer_sql(user_id):
    """`require_user_id` roda ANTES de montar consulta: um `None` nunca pode
    virar "sem filtro" e varrer o historico de todo mundo."""

    class _ConexaoProibida:
        def cursor(self):  # pragma: no cover - o teste falha se for chamado
            raise AssertionError("nenhuma consulta pode sair com user_id invalido")

    with pytest.raises(ValueError):
        build_muscle_summary(_ConexaoProibida(), user_id=user_id, window=_window())


# --- #188 etapa 3: T-0 contra T-1 de series efetivas pronto no backend ---


def _cmp_row(activity, local_date, stimulus, group=1):
    return {
        "muscle_group_id": group, "activity_id": activity, "activity_name": "Treino",
        "local_date": local_date, "trained_at": f"{local_date} 19:00:00",
        "equipment_name": "Supino", "physical_sets": 3, "stimulus_sets": stimulus,
        "tonnage_kg": 1000.0, "tonnage_n": 3, "rpe_sum": 24.0, "rpe_n": 3, "has_direct": 1,
    }


def _js_session_delta(values, epsilon=0.01):
    """Formula de `sessionDelta` (lib/dashboard-muscles.ts), para paridade."""
    current = values[0] if values else None
    previous = values[1] if len(values) > 1 else None
    if current is None or previous is None:
        return None, None
    delta = current - previous
    if abs(delta) <= epsilon:
        return delta, "stable"
    return delta, "up" if delta > 0 else "down"


@pytest.mark.parametrize(
    "t1,t0,expected",
    [(3.0, 3.5, "up"), (3.0, 2.5, "down"), (3.0, 3.01, "stable"), (3.0, 3.0000001, "stable")],
)
def test_comparativo_por_sessao_traz_delta_e_direcao_de_series_efetivas(t1, t0, expected):
    from app.intelligence.dashboard.contracts import MuscleComparativesOut
    from app.intelligence.dashboard.muscle_summary_service import assemble_comparatives

    rows = [_cmp_row(100, "2026-09-20", t1), _cmp_row(101, "2026-09-22", t0)]
    payload = assemble_comparatives(rows, group_names={1: "Peito"})
    (item,) = payload["by_session"]
    values = [p["effective_sets_stimulus"] for p in item["points"]]
    js_delta, js_direction = _js_session_delta(values)
    assert item["effective_sets_direction"] == js_direction == expected
    assert item["effective_sets_delta"] == round(js_delta, 2)
    MuscleComparativesOut.model_validate(payload)


def test_comparativo_por_sessao_sem_t1_fica_none():
    from app.intelligence.dashboard.muscle_summary_service import assemble_comparatives

    payload = assemble_comparatives([_cmp_row(100, "2026-09-20", 3.0)], group_names={1: "Peito"})
    (item,) = payload["by_session"]
    assert item["effective_sets_delta"] is None and item["effective_sets_direction"] is None


# ---------------------------------------------------------------------------
# #188 (fecho): contribuicao por exercicio pronta do servidor. Paridade com os
# casos de `frontend/src/lib/dashboard-muscles.test.ts` (`contributionsByExercise`).
# ---------------------------------------------------------------------------

from app.intelligence.dashboard.contracts import MuscleExerciseContributionOut  # noqa: E402
from app.intelligence.dashboard.muscle_summary_service import _exercise_contributions  # noqa: E402

_SET = {"set_id": 1, "effective_sets_stimulus": 1}
_SUPINO = {"exercise_row_id": 1, "exercise_id": 10, "name": "Supino", "role": "PRIMARY",
           "effective_sets_stimulus": 3, "sets": [_SET, _SET, _SET]}
_CRUCIFIXO = {**_SUPINO, "exercise_row_id": 2, "exercise_id": 11, "name": "Crucifixo",
              "effective_sets_stimulus": 1, "sets": [_SET]}


def _sessao(activity_id, exposures):
    return {"activity_id": activity_id, "exposures": exposures}


def test_contribuicao_agrega_por_exercicio_e_calcula_a_fracao():
    contribs = _exercise_contributions([_sessao(1, [_SUPINO, _CRUCIFIXO])])
    assert [c["name"] for c in contribs] == ["Supino", "Crucifixo"]
    assert [c["share"] for c in contribs] == [0.75, 0.25]
    assert contribs[0]["sets"] == 3 and contribs[0]["stimulus"] == 3.0 and contribs[0]["exercise_id"] == 10
    MuscleExerciseContributionOut(**contribs[0])  # contrato aceita o formato


def test_contribuicao_conta_sessoes_distintas_nao_exposicoes():
    contribs = _exercise_contributions(
        [_sessao(1, [_SUPINO, {**_SUPINO, "exercise_row_id": 9}]), _sessao(2, [_SUPINO])]
    )
    assert contribs[0]["sessions"] == 2 and contribs[0]["sets"] == 9


def test_contribuicao_mantem_papeis_separados_e_ordenados():
    contribs = _exercise_contributions([_sessao(1, [_SUPINO, {**_SUPINO, "exercise_row_id": 3, "role": "STABILIZER"}])])
    assert contribs[0]["roles"] == ["PRIMARY", "STABILIZER"]


def test_contribuicao_sem_id_agrupa_pelo_nome():
    sem_id = {**_SUPINO, "exercise_id": None, "name": "Exercício sem cadastro"}
    contribs = _exercise_contributions([_sessao(1, [sem_id, sem_id])])
    assert len(contribs) == 1 and contribs[0]["exercise_id"] is None and contribs[0]["sets"] == 6


def test_contribuicao_sem_estimulo_tem_fracao_nula():
    zerado = {**_SUPINO, "effective_sets_stimulus": 0, "sets": []}
    contribs = _exercise_contributions([_sessao(1, [zerado])])
    assert contribs[0]["share"] is None and contribs[0]["stimulus"] == 0.0


def test_contribuicao_empate_mantem_ordem_de_aparicao_e_sem_sessoes_vazio():
    a = {**_CRUCIFIXO, "exercise_id": 20, "name": "A"}
    b = {**_CRUCIFIXO, "exercise_id": 21, "name": "B"}
    assert [c["name"] for c in _exercise_contributions([_sessao(1, [a, b])])] == ["A", "B"]
    assert _exercise_contributions([]) == []
