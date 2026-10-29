"""Check-in diario (F2.2, `daily_checkins`/migracao 050): validacao do CRUD e
o que os quatro escores viram no `recovery_signals_service`.

O que estes testes protegem:

1. **escore ausente permanece ausente** - `null != 0` e a regra do hub inteiro;
   um 0 ou um 3 "neutro" gravado por conveniencia entraria na baseline pessoal
   e mentiria com um numero plausivel;
2. **os quatro escores contam UMA vez na concordancia** - eles vem do mesmo
   formulario; contar quatro faria um unico check-in ruim elevar a leitura de
   recuperacao para ATTENTION, contra a regra do plano (§7);
3. **a dor do check-in nao se confunde com a dor da sessao** - `soreness` (dia,
   global, inclusive sem treino) e `muscle_pain` (sessao, rollup 0-10) sao
   perguntas diferentes.

Rodar: cd api && python -m pytest tests/unit/test_daily_checkins.py
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.daily_checkins import DailyCheckinIn, DailyCheckinPatch
from app.daily_checkins_schema import (
    SCORE_FIELDS,
    SCORE_HIGHER_IS_BETTER,
    SCORE_MAX,
    SCORE_MIN,
    normalize_score,
    score_label,
)
from app.intelligence.dashboard.checkin_service import (
    CHECKIN_SIGNAL_GROUP,
    answered_days,
    latest_checkin,
    signal_code,
)
from app.intelligence.dashboard.contracts import DataQuality, Severity
from app.intelligence.dashboard.recovery_signals_service import (
    NEGATIVE_SIGNALS_FOR_ATTENTION,
    SIGNAL_SPEC_BY_CODE,
    SIGNAL_SPECS,
    SLEEP_SIGNAL_GROUP,
    ReadinessReading,
    SignalAgreement,
    SignalReading,
    classify_direction,
    classify_reading,
    combine_assessment,
)

HOJE = date(2026, 9, 25)


# ---------------------------------------------------------------------------
# Validacao (escala 1..5, nunca 0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valor", [1, 2, 3, 4, 5, None])
def test_escore_valido_passa(valor):
    assert normalize_score(valor, field="energy") == valor


@pytest.mark.parametrize("valor", [0, -1, 6, 10])
def test_escore_fora_da_escala_e_recusado(valor):
    with pytest.raises(ValueError) as exc:
        normalize_score(valor, field="soreness")
    assert "soreness" in str(exc.value)


def test_zero_nao_e_o_menor_grau_possivel():
    """"Nao respondi" e AUSENCIA. Aceitar 0 criaria um quinto valor fora da
    escala que a baseline trataria como medida."""
    with pytest.raises(ValueError):
        normalize_score(0, field="mood")


def test_checkin_aceita_resposta_parcial():
    item = DailyCheckinIn(local_date=HOJE, soreness=4)
    assert item.soreness == 4
    assert item.energy is None and item.stress is None and item.mood is None


def test_checkin_totalmente_vazio_e_recusado():
    """Uma linha sem nenhuma resposta ocuparia o UNIQUE do dia sem dizer nada -
    e o usuario acharia que registrou algo."""
    with pytest.raises(ValidationError):
        DailyCheckinIn(local_date=HOJE)


def test_nota_sozinha_e_um_checkin_valido():
    item = DailyCheckinIn(local_date=HOJE, notes="joelho incomodou ao subir escada")
    assert item.notes.startswith("joelho")


def test_campo_desconhecido_e_recusado():
    with pytest.raises(ValidationError):
        DailyCheckinIn(local_date=HOJE, energy=3, sleep_quality=4)


def test_put_aceita_null_explicito_para_apagar_um_escore():
    """Escore respondido por engano volta a "nao respondido", nunca a 0."""
    patch = DailyCheckinPatch(energy=None)
    assert "energy" in patch.model_dump(exclude_unset=True)
    assert patch.model_dump(exclude_unset=True)["energy"] is None


def test_put_nao_aceita_mudar_a_data():
    """Mover um check-in de dia seria afirmar que a pessoa se sentia assim em
    outro dia."""
    with pytest.raises(ValidationError):
        DailyCheckinPatch(local_date=HOJE)


def test_todo_escore_tem_rotulo():
    for field in SCORE_FIELDS:
        assert score_label(field) != field
    assert score_label("desconhecido") == "desconhecido"


def test_escala_e_de_um_a_cinco():
    assert (SCORE_MIN, SCORE_MAX) == (1, 5)


# ---------------------------------------------------------------------------
# Sinais
# ---------------------------------------------------------------------------


def test_cada_escore_gera_um_sinal_com_prefixo_proprio():
    for field in SCORE_FIELDS:
        code = signal_code(field)
        assert code in SIGNAL_SPEC_BY_CODE
        assert code.startswith("checkin_")


def test_a_dor_do_checkin_nao_e_a_dor_da_sessao():
    """`muscle_pain` e o rollup 0-10 por SESSAO que o Modo Treino mantem;
    `checkin_soreness` e a dor GLOBAL do dia, inclusive em dia sem treino. Sao
    dois sinais, com fontes declaradas diferentes."""
    sessao = SIGNAL_SPEC_BY_CODE["muscle_pain"]
    dia = SIGNAL_SPEC_BY_CODE["checkin_soreness"]
    assert sessao.code != dia.code
    assert sessao.source != dia.source
    assert sessao.unit == "0-10" and dia.unit == "1-5"


def test_direcao_dos_escores_respeita_o_que_e_bom():
    assert SIGNAL_SPEC_BY_CODE["checkin_energy"].higher_is_better is True
    assert SIGNAL_SPEC_BY_CODE["checkin_mood"].higher_is_better is True
    assert SIGNAL_SPEC_BY_CODE["checkin_soreness"].higher_is_better is False
    assert SIGNAL_SPEC_BY_CODE["checkin_stress"].higher_is_better is False
    for field in SCORE_FIELDS:
        assert SIGNAL_SPEC_BY_CODE[signal_code(field)].higher_is_better == SCORE_HIGHER_IS_BETTER[field]


def test_tolerancia_e_absoluta_em_meio_degrau():
    """A escala tem 5 degraus: 5% de 3 (0,15) nao significa nada. Meio degrau e
    a menor diferenca que uma pessoa distingue ao responder."""
    spec = SIGNAL_SPEC_BY_CODE["checkin_energy"]
    assert spec.tolerance_is_absolute is True
    assert spec.tolerance == 0.5
    # 3,4 contra baseline 3,0 ainda esta "em linha"; 3,6 nao.
    assert classify_direction(3.4, 3.0, spec).value == "IN_LINE"
    assert classify_direction(3.6, 3.0, spec).value == "ABOVE"


def test_energia_abaixo_da_baseline_e_desfavoravel():
    spec = SIGNAL_SPEC_BY_CODE["checkin_energy"]
    reading = classify_reading(classify_direction(2.0, 4.0, spec), spec)
    assert reading is SignalReading.UNFAVORABLE


def test_dor_acima_da_baseline_e_desfavoravel():
    spec = SIGNAL_SPEC_BY_CODE["checkin_soreness"]
    reading = classify_reading(classify_direction(4.0, 2.0, spec), spec)
    assert reading is SignalReading.UNFAVORABLE


def test_sem_baseline_nao_ha_leitura():
    spec = SIGNAL_SPEC_BY_CODE["checkin_mood"]
    assert classify_reading(classify_direction(3.0, None, spec), spec) is SignalReading.UNKNOWN


# ---------------------------------------------------------------------------
# Concordancia: quatro respostas do mesmo formulario valem UMA
# ---------------------------------------------------------------------------


def _negativo(code: str, group: str) -> dict:
    return {
        "code": code,
        "group": group,
        "reading": SignalReading.UNFAVORABLE,
        "agreement": SignalAgreement.AGREES,
    }


def test_os_quatro_escores_do_checkin_nao_elevam_sozinhos_a_atencao():
    """O caso que o agrupamento existe para evitar: num dia ruim a pessoa marca
    energia baixa, humor baixo, estresse alto e dor alta - quatro respostas
    correlacionadas de UM formulario. Contar quatro faria um unico check-in
    ruim decretar recuperacao incompleta."""
    signals = [_negativo(signal_code(field), CHECKIN_SIGNAL_GROUP) for field in SCORE_FIELDS]
    level, _quality, negatives = combine_assessment(signals, ReadinessReading.RECOVERED, None)
    assert level is Severity.INFO
    assert len(negatives) == 1, "um representante por grupo"


def test_checkin_mais_um_sinal_objetivo_eleva_a_atencao():
    """O check-in NAO e ignorado: com outro sinal negativo independente (HRV),
    sao dois grupos concordantes e a leitura sobe."""
    signals = [
        *(_negativo(signal_code(field), CHECKIN_SIGNAL_GROUP) for field in SCORE_FIELDS),
        _negativo("hrv", "hrv"),
    ]
    level, _quality, negatives = combine_assessment(signals, ReadinessReading.RECOVERED, None)
    assert level is Severity.ATTENTION
    assert len(negatives) == NEGATIVE_SIGNALS_FOR_ATTENTION


def test_sinais_objetivos_continuam_contando_um_por_um():
    """Nenhum numero dos sinais que ja existiam muda com a F2.2: cada um forma
    grupo com ele mesmo."""
    signals = [_negativo("hrv", "hrv"), _negativo("resting_hr", "resting_hr")]
    level, _quality, negatives = combine_assessment(signals, ReadinessReading.RECOVERED, None)
    assert level is Severity.ATTENTION
    assert len(negatives) == 2


def test_todo_sinal_declara_grupo():
    for spec in SIGNAL_SPECS:
        assert spec.group
    for field in SCORE_FIELDS:
        assert SIGNAL_SPEC_BY_CODE[signal_code(field)].group == CHECKIN_SIGNAL_GROUP
    # Sinal de fonte unica: grupo proprio.
    assert SIGNAL_SPEC_BY_CODE["hrv"].group == "hrv"
    assert SIGNAL_SPEC_BY_CODE["resting_hr"].group == "resting_hr"


def test_os_dois_sinais_de_sono_compartilham_grupo():
    """Decisao do usuario, 2026-09-25: `sleep_duration` e `sleep_regularity`
    saem da MESMA noite e da mesma tabela - quem dorme pouco num horario
    baguncado marca as duas de uma vez."""
    assert SIGNAL_SPEC_BY_CODE["sleep_duration"].group == SLEEP_SIGNAL_GROUP
    assert SIGNAL_SPEC_BY_CODE["sleep_regularity"].group == SLEEP_SIGNAL_GROUP


def test_o_sono_sozinho_nao_eleva_mais_a_atencao():
    """ANTES da decisao: dois sinais negativos -> ATTENTION. DEPOIS: um grupo
    negativo -> INFO. E o mesmo raciocinio dos quatro escores do check-in."""
    signals = [_negativo("sleep_duration", SLEEP_SIGNAL_GROUP), _negativo("sleep_regularity", SLEEP_SIGNAL_GROUP)]
    level, _quality, negatives = combine_assessment(signals, ReadinessReading.RECOVERED, None)
    assert level is Severity.INFO
    assert len(negatives) == 1

    # Prova do "antes", com os MESMOS sinais em grupos separados: era ATTENTION.
    separados = [_negativo("sleep_duration", "sleep_duration"), _negativo("sleep_regularity", "sleep_regularity")]
    antes, _q, antes_neg = combine_assessment(separados, ReadinessReading.RECOVERED, None)
    assert antes is Severity.ATTENTION and len(antes_neg) == 2


def test_sono_mais_um_sinal_independente_ainda_eleva():
    """O sono NAO e ignorado: com HRV negativo ao lado sao dois grupos
    concordantes e a leitura sobe - so nao sobe sozinho."""
    signals = [
        _negativo("sleep_duration", SLEEP_SIGNAL_GROUP),
        _negativo("sleep_regularity", SLEEP_SIGNAL_GROUP),
        _negativo("hrv", "hrv"),
    ]
    level, _quality, negatives = combine_assessment(signals, ReadinessReading.RECOVERED, None)
    assert level is Severity.ATTENTION
    assert len(negatives) == NEGATIVE_SIGNALS_FOR_ATTENTION


def test_apenas_os_escores_do_checkin_sao_subjetivos():
    subjetivos = {spec.code for spec in SIGNAL_SPECS if spec.subjective}
    assert subjetivos == {signal_code(field) for field in SCORE_FIELDS}


# ---------------------------------------------------------------------------
# Helpers de leitura
# ---------------------------------------------------------------------------


def test_dia_sem_escore_nao_conta_como_respondido():
    rows = [
        {"local_date": date(2026, 9, 23), "energy": 4.0, "soreness": None},
        {"local_date": date(2026, 9, 24), "energy": None, "soreness": 2.0},
    ]
    assert answered_days(rows, "energy") == 1
    assert answered_days(rows, "soreness") == 1
    assert answered_days(rows, "mood") == 0


def test_o_ultimo_checkin_e_o_mais_recente_e_nunca_uma_linha_de_zeros():
    assert latest_checkin([]) is None
    rows = [{"local_date": date(2026, 9, 23)}, {"local_date": date(2026, 9, 24)}]
    assert latest_checkin(rows)["local_date"] == date(2026, 9, 24)


def test_versao_propria_da_metrica_do_checkin():
    """Relato nao e medida: a fonte e outra, e a versao tambem."""
    spec = SIGNAL_SPEC_BY_CODE["checkin_energy"]
    assert spec.version_key == "daily_checkin"
    assert SIGNAL_SPEC_BY_CODE["hrv"].version_key == "recovery_signals"


def test_qualidade_do_envelope_nao_e_afetada_por_escore_ausente():
    """Sem check-in nenhum os quatro sinais ficam UNKNOWN - nao NEUTRAL, que
    afirmaria uma comparacao que nao aconteceu."""
    signals = [
        {
            "code": signal_code(field),
            "group": CHECKIN_SIGNAL_GROUP,
            "reading": SignalReading.UNKNOWN,
            "agreement": SignalAgreement.NO_BASELINE,
        }
        for field in SCORE_FIELDS
    ]
    level, quality, negatives = combine_assessment(signals, ReadinessReading.UNKNOWN, None)
    assert level is Severity.INFO
    assert quality is DataQuality.INSUFFICIENT
    assert negatives == []
