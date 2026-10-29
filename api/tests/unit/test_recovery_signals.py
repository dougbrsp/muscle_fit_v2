"""Regras do `recovery_signals_service` (F1.5) que nao dependem de banco.

O que cada bloco protege (plano §6 `recovery-signals-v1`, §7 "Estou
recuperando bem?", §16):

1. **Baseline pessoal**: mediana da janela de referencia, e `None` - nunca um
   numero inventado - com menos de `MIN_BASELINE_OBSERVATIONS` pontos.
2. **Nenhum sinal isolado decreta recuperacao ruim**: HRV baixo sozinho nao
   eleva o nivel; HRV + FC de repouso + queda de desempenho juntos elevam,
   com a incerteza declarada.
3. **Sinais divergentes aparecem**, lado a lado, em vez de serem escondidos.
4. **Dor articular bloqueia intensificacao** - a regra e a do modelo.
5. **Wearable ausente nao zera prontidao** nem vira zero em lugar nenhum.
6. **Ownership**: `require_user_id` antes de qualquer SQL.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.intelligence.dashboard import recovery_signals_service as service
from app.intelligence.dashboard.contracts import (
    DataQuality,
    ReadinessReading,
    RecoverySignalsResponse,
    Severity,
    SignalAgreement,
    SignalDirection,
    SignalReading,
    WindowInfo,
    WindowKind,
)

HOJE = date(2026, 9, 22)


def _window(days: int = 7) -> WindowInfo:
    return WindowInfo(
        kind=WindowKind.ROLLING_7D,
        date_from=HOJE - timedelta(days=days - 1),
        date_to=HOJE,
        timezone="America/Sao_Paulo",
        days=days,
    )


class _Conn:
    """Conexao de mentira: devolve as linhas preparadas para cada fonte,
    reconhecida pela tabela citada no SQL."""

    def __init__(self, *, sleep=None, wellness=None, pain=None, faltando=()):
        self._rows = {"sleep_metrics": sleep or [], "intervals_wellness": wellness or [], "activities": pain or []}
        self._faltando = set(faltando)
        self.executadas: list[str] = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self._atual = next(
            (table for table in self._rows if table in sql), None
        )
        self.executadas.append(self._atual or sql)
        if self._atual in self._faltando:
            raise RuntimeError(f"Table '{self._atual}' doesn't exist")

    def fetchall(self):
        return list(self._rows.get(self._atual) or [])


def _dias(inicio: date, quantidade: int):
    return [inicio - timedelta(days=offset) for offset in range(quantidade)]


def _wellness(hrv_janela: float, hrv_base: float, fc_janela: float, fc_base: float):
    """7 dias de janela + 28 dias de referencia, todos com HRV e FC."""
    linhas = []
    for dia in _dias(HOJE, 7):
        linhas.append({"local_date": dia, "hrv_ms": hrv_janela, "resting_hr_bpm": fc_janela, "form_tsb": -5.0})
    base_inicio = HOJE - timedelta(days=7)
    for dia in _dias(base_inicio, 28):
        linhas.append({"local_date": dia, "hrv_ms": hrv_base, "resting_hr_bpm": fc_base, "form_tsb": -5.0})
    return linhas


def _prontidao(status="READY", performance=None, blocking=(), grupo="Peitoral", group_id=1):
    return {
        "muscle_group": grupo,
        "muscle_group_id": group_id,
        "readiness_status": status,
        "guidance": "ideal" if status == "READY" else "recovering",
        "recovery_pct": 100.0 if status == "READY" else 40.0,
        "estimated_recovery_hours": 24.0,
        "remaining_hours": 0.0 if status == "READY" else 12.0,
        "hours_since_trained": 30.0,
        "session_dose": 4.2,
        "performance_ratio": performance,
        "recommendation": {"volume_multiplier": 1.0 if status == "READY" else 0.0},
        "max_rpe": 10,
        "allow_failure": status == "READY",
        "blocking_signals": [{"code": code} for code in blocking],
        "explanation": ["A última sessão gerou dose 4.20."],
    }


def _build(monkeypatch, conn, itens, *, contexto=None, contexto_disponivel=True, ajuste=None):
    """`contexto` e o retorno de `load_context_events` (F2.1).

    Ele e substituido aqui de proposito: a leitura do contexto tem teste
    proprio (`test_dashboard_context_service.py`, funcoes puras, e a
    integracao contra MySQL). Fazer a conexao de mentira responder tambem as
    tres consultas do contexto so acoplaria este arquivo a elas."""
    # M33: `_readiness_items` passou a devolver TAMBEM o ajuste global (TSB)
    # que o modelo aplicou - ele sai da mesma instancia que rodou o overview,
    # sem consulta nova. `now_local`: o "agora" lido uma vez ("Bom para").
    monkeypatch.setattr(
        service,
        "_readiness_items",
        lambda user_id, now_local=None: (list(itens), service._adjustment_out(ajuste)),
    )
    monkeypatch.setattr(
        service,
        "load_context_events",
        lambda conn, *, user_id, window: (
            list(contexto or []),
            contexto_disponivel,
            [] if contexto_disponivel else [service.TABLE_MISSING_LIMITATION],
        ),
    )
    payload = service.build_recovery_signals(conn, user_id=7, window=_window())
    # O contrato precisa validar: e ele que a rota devolve.
    RecoverySignalsResponse.model_validate(payload)
    return payload


def _sinal(payload, code):
    return next(signal for signal in payload["signals"] if signal["code"] == code)


# ---------------------------------------------------------------------------
# 1) Baseline pessoal
# ---------------------------------------------------------------------------


def test_baseline_e_a_mediana_da_janela_de_referencia():
    valores = [40.0, 42.0, 44.0] + [43.0] * 12
    baseline, pontos = service.personal_baseline(valores)
    assert pontos == 15
    assert baseline == 43.0


def test_baseline_com_poucos_pontos_nao_inventa_numero():
    baseline, pontos = service.personal_baseline([40.0] * (service.MIN_BASELINE_OBSERVATIONS - 1))
    assert baseline is None
    assert pontos == service.MIN_BASELINE_OBSERVATIONS - 1


def test_sinal_sem_baseline_suficiente_declara_e_nao_compara(monkeypatch):
    """Cinco dias de HRV nao viram baseline: o sinal existe, mostra o valor
    atual e diz que nao ha com o que comparar."""
    poucos = [
        {"local_date": dia, "hrv_ms": 40.0, "resting_hr_bpm": 55.0, "form_tsb": -2.0}
        for dia in _dias(HOJE - timedelta(days=7), 5)
    ] + [{"local_date": dia, "hrv_ms": 39.0, "resting_hr_bpm": 55.0, "form_tsb": -2.0} for dia in _dias(HOJE, 7)]

    payload = _build(monkeypatch, _Conn(wellness=poucos), [_prontidao()])
    hrv = _sinal(payload, "hrv")

    assert hrv["current"]["value"] == 39.0
    assert hrv["baseline"]["value"] is None
    assert hrv["baseline"]["data_quality"] is DataQuality.INSUFFICIENT
    assert hrv["direction"] is SignalDirection.UNKNOWN
    assert hrv["agreement"] is SignalAgreement.NO_BASELINE
    assert any("sem baseline pessoal" in limitacao for limitacao in hrv["limitations"])
    assert "hrv" in payload["assessment"]["unavailable_codes"]


# ---------------------------------------------------------------------------
# 2) Nenhum sinal isolado decreta recuperacao ruim
# ---------------------------------------------------------------------------


def test_hrv_baixo_isolado_nao_gera_conclusao_forte(monkeypatch):
    """HRV 20% abaixo da baseline, com a prontidao intacta e nenhum outro
    sinal negativo: nivel segue INFO e o texto diz que um sinal sozinho nao
    conclui."""
    wellness = _wellness(hrv_janela=32.0, hrv_base=40.0, fc_janela=55.0, fc_base=55.0)
    payload = _build(monkeypatch, _Conn(wellness=wellness), [_prontidao()])

    hrv = _sinal(payload, "hrv")
    assert hrv["direction"] is SignalDirection.BELOW
    assert hrv["reading"] is SignalReading.UNFAVORABLE

    assessment = payload["assessment"]
    assert assessment["level"] is Severity.INFO
    assert assessment["allow_intensification"] is True
    assert any("isolado" in linha for linha in assessment["explanation"])


def test_hrv_mais_fc_repouso_mais_queda_de_desempenho_elevam_atencao(monkeypatch):
    """Tres sinais negativos concordantes -> ATTENTION, com a incerteza
    declarada (nunca um veredito sem qualificacao)."""
    wellness = _wellness(hrv_janela=32.0, hrv_base=40.0, fc_janela=62.0, fc_base=55.0)
    payload = _build(
        monkeypatch, _Conn(wellness=wellness), [_prontidao(performance=0.90), _prontidao(grupo="Costas", group_id=2)]
    )

    assessment = payload["assessment"]
    assert assessment["level"] is Severity.ATTENTION
    assert {"hrv", "resting_hr", "performance"} <= set(assessment["agreeing_codes"] + assessment["diverging_codes"])
    assert assessment["uncertainty"] in tuple(DataQuality)
    assert _sinal(payload, "performance")["reading"] is SignalReading.UNFAVORABLE
    assert "2 " not in assessment["headline"] or "sinais" in assessment["headline"]


def test_action_so_quando_o_modelo_bloqueia_trabalho_direto(monkeypatch):
    """Nivel ACTION nao nasce de sinal objetivo nenhum: so do modelo."""
    wellness = _wellness(hrv_janela=32.0, hrv_base=40.0, fc_janela=62.0, fc_base=55.0)
    sem_bloqueio = _build(monkeypatch, _Conn(wellness=wellness), [_prontidao(performance=0.90)])
    assert sem_bloqueio["assessment"]["level"] is Severity.ATTENTION

    com_bloqueio = _build(
        monkeypatch,
        _Conn(wellness=wellness),
        [_prontidao(status="NO_DIRECT_WORK", blocking=("JOINT_PAIN",))],
    )
    assert com_bloqueio["assessment"]["level"] is Severity.ACTION


# ---------------------------------------------------------------------------
# 3) Sinais divergentes aparecem lado a lado
# ---------------------------------------------------------------------------


def test_sinal_divergente_nao_e_escondido(monkeypatch):
    """HRV ACIMA da baseline com a prontidao rebaixada: o sinal fica visivel,
    marcado como divergente, e o outro lado tambem."""
    wellness = _wellness(hrv_janela=52.0, hrv_base=40.0, fc_janela=55.0, fc_base=55.0)
    payload = _build(
        monkeypatch,
        _Conn(wellness=wellness),
        [_prontidao(status="NO_DIRECT_WORK", blocking=("LOW_READINESS",))],
    )

    hrv = _sinal(payload, "hrv")
    assert hrv["reading"] is SignalReading.FAVORABLE
    assert hrv["agreement"] is SignalAgreement.DIVERGES
    assert "hrv" in payload["assessment"]["diverging_codes"]
    assert any("Divergem" in linha for linha in payload["assessment"]["explanation"])


def test_concordancia_depende_da_leitura_de_prontidao():
    spec = service.SIGNAL_SPEC_BY_CODE["hrv"]
    assert service.classify_reading(SignalDirection.BELOW, spec) is SignalReading.UNFAVORABLE
    assert (
        service.classify_agreement(SignalReading.UNFAVORABLE, ReadinessReading.STRAINED)
        is SignalAgreement.AGREES
    )
    assert (
        service.classify_agreement(SignalReading.UNFAVORABLE, ReadinessReading.RECOVERED)
        is SignalAgreement.DIVERGES
    )
    # FC de repouso ABAIXO da baseline e boa - a direcao e a mesma, a leitura
    # e o oposto.
    fc = service.SIGNAL_SPEC_BY_CODE["resting_hr"]
    assert service.classify_reading(SignalDirection.BELOW, fc) is SignalReading.FAVORABLE


def test_leitura_de_prontidao_nao_vira_strained_por_um_grupo_em_dose_reduzida():
    itens = [_prontidao(status="REDUCED_DOSE")] + [
        _prontidao(grupo=f"G{n}", group_id=n) for n in range(2, 6)
    ]
    assert service.classify_readiness_reading(itens) is ReadinessReading.RECOVERED
    assert service.classify_readiness_reading([_prontidao(status="NO_DIRECT_WORK")]) is ReadinessReading.STRAINED
    assert service.classify_readiness_reading([]) is ReadinessReading.UNKNOWN


# ---------------------------------------------------------------------------
# 4) Dor
# ---------------------------------------------------------------------------


def test_dor_articular_bloqueia_intensificacao(monkeypatch):
    dor = [{"local_date": HOJE, "muscle_pain": 2, "joint_pain": 1, "sessions": 1}]
    payload = _build(monkeypatch, _Conn(pain=dor), [_prontidao()])

    articular = _sinal(payload, "joint_pain")
    assert articular["current"]["value"] == 1
    assert articular["reading"] is SignalReading.UNFAVORABLE
    assert payload["assessment"]["allow_intensification"] is False
    assert "articular" in (payload["assessment"]["intensification_blocked_reason"] or "")


def test_sem_dor_articular_a_intensificacao_segue_liberada(monkeypatch):
    dor = [{"local_date": HOJE, "muscle_pain": 2, "joint_pain": 0, "sessions": 1}]
    payload = _build(monkeypatch, _Conn(pain=dor), [_prontidao()])
    assert payload["assessment"]["allow_intensification"] is True
    assert _sinal(payload, "joint_pain")["reading"] is SignalReading.NEUTRAL


# ---------------------------------------------------------------------------
# 5) Ausencia de dado nunca vira zero
# ---------------------------------------------------------------------------


def test_wearable_ausente_nao_zera_prontidao(monkeypatch):
    """Sem nenhuma linha de `intervals_wellness`: os sinais do wearable ficam
    `null` (nao zero), a limitacao e declarada e a prontidao segue intacta."""
    payload = _build(monkeypatch, _Conn(), [_prontidao()])

    for code in ("hrv", "resting_hr", "form_tsb"):
        sinal = _sinal(payload, code)
        assert sinal["current"]["value"] is None
        assert sinal["baseline"]["value"] is None
        assert sinal["agreement"] is SignalAgreement.NO_BASELINE

    grupo = payload["groups"][0]
    assert grupo["recovery_pct"]["value"] == 100.0
    assert grupo["readiness_status"] == "READY"
    assert any("NAO e afetada" in limitacao for limitacao in payload["limitations"])


def test_fonte_indisponivel_nao_derruba_a_tela(monkeypatch):
    """Tabela de wellness inexistente (integracao nao provisionada) responde
    200 com os demais sinais - nunca 500."""
    conn = _Conn(pain=[{"local_date": HOJE, "muscle_pain": 3, "joint_pain": 0, "sessions": 1}], faltando=("intervals_wellness",))
    payload = _build(monkeypatch, conn, [_prontidao()])
    assert payload["groups"]
    assert _sinal(payload, "hrv")["current"]["value"] is None


def test_sem_evento_registrado_a_limitacao_diz_que_nada_foi_anotado(monkeypatch):
    """Ate a F2.1 a frase era "ainda nao sao registrados"; com a 048 isso
    deixou de ser verdade e a limitacao passa a descrever o estado real."""
    payload = _build(monkeypatch, _Conn(), [_prontidao()])
    assert payload["assessment"]["context_events"] == []
    assert service.CONTEXT_EVENTS_EMPTY_LIMITATION in payload["limitations"]
    assert not hasattr(service, "CONTEXT_EVENTS_LIMITATION")


def test_evento_de_contexto_aparece_na_avaliacao_sem_mudar_o_nivel(monkeypatch):
    """Contexto fica LADO A LADO: ele nao entra em `level`, nem em
    `allow_intensification`, nem nas contagens de concordancia."""
    evento = {
        "id": 1,
        "event_type": "alcohol",
        "event_type_label": "Álcool",
        "local_date": HOJE,
        "end_local_date": None,
        "intensity": 3,
        "notes": None,
        "source": "manual",
        "derived": False,
        "editable": True,
    }
    sem = _build(monkeypatch, _Conn(), [_prontidao()])
    com = _build(monkeypatch, _Conn(), [_prontidao()], contexto=[evento])

    assert [item["event_type"] for item in com["assessment"]["context_events"]] == ["alcohol"]
    assert com["assessment"]["level"] is sem["assessment"]["level"]
    assert com["assessment"]["allow_intensification"] == sem["assessment"]["allow_intensification"]
    assert com["assessment"]["agreeing_codes"] == sem["assessment"]["agreeing_codes"]
    assert service.CONTEXT_EVENTS_ASSOCIATION_LIMITATION in com["limitations"]


def test_antes_da_migracao_048_a_limitacao_e_a_da_tabela_ausente(monkeypatch):
    payload = _build(monkeypatch, _Conn(), [_prontidao()], contexto_disponivel=False)
    assert payload["assessment"]["context_events"] == []
    assert service.TABLE_MISSING_LIMITATION in payload["limitations"]


def test_sem_sessao_e_sem_sinal_a_resposta_existe_e_declara_insuficiencia(monkeypatch):
    payload = _build(monkeypatch, _Conn(), [])
    assert payload["groups"] == []
    assert payload["data_quality"] is DataQuality.INSUFFICIENT
    assert payload["assessment"]["level"] is Severity.INFO
    assert payload["assessment"]["readiness_reading"] is ReadinessReading.UNKNOWN


# ---------------------------------------------------------------------------
# 6) Ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("user_id", [None, 0, -1, "abc"])
def test_servico_recusa_user_id_invalido_antes_de_qualquer_sql(user_id):
    class _ConexaoProibida:
        def cursor(self):  # pragma: no cover - o teste falha se for chamado
            raise AssertionError("nenhuma consulta pode sair com user_id invalido")

    with pytest.raises(ValueError):
        service.build_recovery_signals(_ConexaoProibida(), user_id=user_id, window=_window())


# ---------------------------------------------------------------------------
# 7) F2.5 - M33: o detalhamento do MODELO passa a sair daqui
#
# Ate a F2.5 a recomendacao de series, os fatores do calculo e o ajuste global
# de fadiga so existiam em `/recuperacao-muscular`. O que estes testes fixam e
# que eles sao REPASSADOS do mesmo `get_recovery_overview` - nenhum numero e
# recalculado aqui, e ausencia nunca vira zero.
# ---------------------------------------------------------------------------


def _prontidao_detalhada(status="REDUCED_DOSE"):
    item = _prontidao(status=status)
    item["recommendation"] = {
        "volume_multiplier": 0.75,
        "direct_sets_min": 1,
        "direct_sets_max": 2,
        "maximum_rpe": 9,
        "allow_failure_on_last_set": False,
        "allow_progression": False,
        "allow_indirect_work": False,
    }
    item["avg_rpe"] = 8.4
    item["density_penalty"] = 6.0
    item["signal_penalty"] = 1.5
    item["performance_ratio"] = 0.91
    item["factors"] = {
        "session_dose": 4.2,
        "dose_last_72h": 7.75,
        "avg_rpe_used": 8.25,
        "rpe10_isolated_note": "RPE 10 isolado na ultima serie.",
        "recent_sets": [
            {
                "exercise_id": 11,
                "trained_at": "2026-09-21 19:30:00",
                "reps": 8,
                "rpe": 9.0,
                "set_type": "work",
                "role": "PRIMARY",
                "participation": 1.0,
                "effort_points": 1.25,
                "repetition_factor": 1.1,
                "set_type_factor": 1.0,
            }
        ]
        * 7,
    }
    return item


def test_recomendacao_de_series_e_repassada_do_modelo(monkeypatch):
    payload = _build(monkeypatch, _Conn(), [_prontidao_detalhada()])
    recomendacao = payload["groups"][0]["recommendation"]
    assert recomendacao["direct_sets_min"] == 1
    assert recomendacao["direct_sets_max"] == 2
    assert recomendacao["maximum_rpe"] == 9
    assert recomendacao["allow_failure_on_last_set"] is False
    assert recomendacao["allow_progression"] is False
    assert recomendacao["volume_multiplier"] == pytest.approx(0.75)


def test_fatores_do_calculo_saem_prontos_e_a_lista_de_series_tem_teto(monkeypatch):
    payload = _build(monkeypatch, _Conn(), [_prontidao_detalhada()])
    fatores = payload["groups"][0]["factors"]
    assert fatores["session_dose"] == pytest.approx(4.2)
    assert fatores["dose_last_72h"] == pytest.approx(7.75)
    assert fatores["avg_rpe_used"] == pytest.approx(8.25)
    assert fatores["avg_rpe_window"] == pytest.approx(8.4)
    assert fatores["density_penalty"] == pytest.approx(6.0)
    assert fatores["signal_penalty"] == pytest.approx(1.5)
    assert fatores["performance_ratio"] == pytest.approx(0.91)
    assert fatores["notes"] == ["RPE 10 isolado na ultima serie."]
    # 7 series no item, teto de 5 no payload: o detalhamento nao engorda a
    # resposta sem limite.
    assert len(fatores["recent_sets"]) == service.MAX_FACTOR_SETS
    assert fatores["recent_sets"][0]["reps"] == 8
    assert fatores["recent_sets"][0]["effort_points"] == pytest.approx(1.25)


def test_grupo_sem_fatores_declara_ausencia_em_vez_de_zero(monkeypatch):
    """Item sem `factors` (grupo sem sessao na janela) nao pode virar uma
    linha de zeros: "nao da para calcular" e diferente de "foi zero"."""
    payload = _build(monkeypatch, _Conn(), [_prontidao(status="READY")])
    fatores = payload["groups"][0]["factors"]
    assert fatores["avg_rpe_used"] is None
    assert fatores["performance_ratio"] is None
    assert fatores["recent_sets"] == []
    assert fatores["notes"] == []


def test_ajuste_global_ausente_e_none_e_nunca_fator_neutro(monkeypatch):
    payload = _build(monkeypatch, _Conn(), [_prontidao()])
    assert payload["global_readiness_adjustment"] is None


def test_ajuste_global_presente_repassa_fator_e_tsb(monkeypatch):
    payload = _build(
        monkeypatch,
        _Conn(),
        [_prontidao()],
        ajuste={"form_tsb": -12.5, "factor": 1.12, "wellness_date": "2026-09-21"},
    )
    ajuste = payload["global_readiness_adjustment"]
    assert ajuste["factor"] == pytest.approx(1.12)
    assert ajuste["form_tsb"] == pytest.approx(-12.5)
    assert ajuste["wellness_date"] == date(2026, 9, 21)


# ---------------------------------------------------------------------------
# 8) F2.5 - M36: dia COM treino x dia SEM treino
#
# O recorte veio de `/treinos` ("Cardio & Recuperacao"), onde nunca foi
# cardio. Ele nao entra em `level`, em `allow_intensification` nem em contagem
# de concordancia - e descricao, nao veredito.
# ---------------------------------------------------------------------------


def _dor(dias):
    return [{"local_date": dia, "muscle_pain": 2.0, "joint_pain": 0, "sessions": 1} for dia in dias]


def _wellness_por_dia(valores_por_dia):
    return [
        {"local_date": dia, "hrv_ms": hrv, "resting_hr_bpm": fc, "form_tsb": -5.0}
        for dia, (hrv, fc) in valores_por_dia.items()
    ]


def test_recorte_por_dia_de_treino_compara_com_o_dia_sem_treino(monkeypatch):
    dias_janela = _dias(HOJE, 7)
    dias_treino = dias_janela[:3]
    valores = {dia: ((50.0, 62.0) if dia in dias_treino else (70.0, 55.0)) for dia in dias_janela}
    conn = _Conn(wellness=_wellness_por_dia(valores), pain=_dor(dias_treino))

    payload = _build(monkeypatch, conn, [_prontidao()])
    bloco = payload["training_vs_rest"]

    assert bloco["training_days"] == 3
    assert bloco["rest_days"] == 4

    hrv = next(item for item in bloco["metrics"] if item["code"] == "hrv")
    assert hrv["training_days"]["value"] == pytest.approx(50.0)
    assert hrv["rest_days"]["value"] == pytest.approx(70.0)
    assert hrv["delta"]["value"] == pytest.approx(-20.0)
    assert hrv["direction"] is SignalDirection.BELOW
    # HRV alto e bom: HRV menor em dia de treino le desfavoravel.
    assert hrv["reading"] is SignalReading.UNFAVORABLE

    fc = next(item for item in bloco["metrics"] if item["code"] == "resting_hr")
    # FC de repouso alta e ruim: mais alta em dia de treino tambem le
    # desfavoravel, mesmo com a direcao oposta.
    assert fc["direction"] is SignalDirection.ABOVE
    assert fc["reading"] is SignalReading.UNFAVORABLE

    assert service.TRAINING_VS_REST_ASSOCIATION_LIMITATION in bloco["limitations"]
    assert service.TRAINING_VS_REST_SAME_DAY_LIMITATION in bloco["limitations"]


def test_recorte_nao_e_um_sinal_a_mais_na_avaliacao(monkeypatch):
    """O recorte e DESCRITIVO: ele nao entra em `signals`, entao a contagem
    de sinais da avaliacao continua sendo exatamente a das `SIGNAL_SPECS`."""
    dias_janela = _dias(HOJE, 7)
    dias_treino = dias_janela[:3]
    valores = {dia: ((50.0, 62.0) if dia in dias_treino else (70.0, 55.0)) for dia in dias_janela}
    conn = _Conn(wellness=_wellness_por_dia(valores), pain=_dor(dias_treino))

    payload = _build(monkeypatch, conn, [_prontidao()])

    assert len(payload["signals"]) == len(service.SIGNAL_SPECS)
    # Nenhum codigo do recorte vira um sinal novo: os codigos que ele usa sao
    # os dos sinais que JA existiam.
    codigos_do_recorte = {item["code"] for item in payload["training_vs_rest"]["metrics"]}
    assert codigos_do_recorte <= {sinal["code"] for sinal in payload["signals"]}


def test_sem_dia_de_treino_o_recorte_declara_que_nao_ha_o_que_comparar(monkeypatch):
    dias_janela = _dias(HOJE, 7)
    valores = {dia: (60.0, 58.0) for dia in dias_janela}
    conn = _Conn(wellness=_wellness_por_dia(valores))

    bloco = _build(monkeypatch, conn, [_prontidao()])["training_vs_rest"]
    assert bloco["training_days"] == 0
    assert service.TRAINING_VS_REST_NO_TRAINING_LIMITATION in bloco["limitations"]

    hrv = next(item for item in bloco["metrics"] if item["code"] == "hrv")
    assert hrv["training_days"]["value"] is None
    assert hrv["delta"]["value"] is None
    assert hrv["direction"] is SignalDirection.UNKNOWN
    assert any("dia de treino" in limitacao for limitacao in hrv["limitations"])


def test_todos_os_dias_com_treino_declaram_a_falta_do_lado_de_descanso(monkeypatch):
    dias_janela = _dias(HOJE, 7)
    valores = {dia: (60.0, 58.0) for dia in dias_janela}
    conn = _Conn(wellness=_wellness_por_dia(valores), pain=_dor(dias_janela))

    bloco = _build(monkeypatch, conn, [_prontidao()])["training_vs_rest"]
    assert bloco["training_days"] == 7
    assert bloco["rest_days"] == 0
    assert service.TRAINING_VS_REST_NO_REST_LIMITATION in bloco["limitations"]

    hrv = next(item for item in bloco["metrics"] if item["code"] == "hrv")
    assert hrv["rest_days"]["value"] is None
    assert hrv["reading"] is SignalReading.UNKNOWN


def test_sem_wearable_o_recorte_existe_e_nao_inventa_numero(monkeypatch):
    dias_treino = _dias(HOJE, 3)
    bloco = _build(monkeypatch, _Conn(pain=_dor(dias_treino)), [_prontidao()])["training_vs_rest"]

    hrv = next(item for item in bloco["metrics"] if item["code"] == "hrv")
    assert hrv["training_days"]["value"] is None
    assert hrv["rest_days"]["value"] is None
    assert hrv["delta"]["value"] is None
    assert any("nenhuma medida" in limitacao for limitacao in hrv["limitations"])


def test_poucos_dias_de_cada_lado_declaram_de_quantos_dias_a_diferenca_e_feita(monkeypatch):
    """Dois dias de cada lado nao viram "sem dado" - eles sao observacoes
    reais. O que a resposta faz e DIZER de quantos dias cada lado e feito."""
    dias_janela = _dias(HOJE, 4)
    dias_treino = dias_janela[:2]
    valores = {dia: ((50.0, 62.0) if dia in dias_treino else (70.0, 55.0)) for dia in dias_janela}
    conn = _Conn(wellness=_wellness_por_dia(valores), pain=_dor(dias_treino))

    bloco = _build(monkeypatch, conn, [_prontidao()])["training_vs_rest"]
    hrv = next(item for item in bloco["metrics"] if item["code"] == "hrv")
    assert hrv["training_observations"] == 2
    assert hrv["rest_observations"] == 2
    assert hrv["delta"]["value"] == pytest.approx(-20.0)
    assert any(
        f"menos que os {service.TRAINING_VS_REST_MIN_DAYS}" in limitacao
        for limitacao in hrv["limitations"]
    )
