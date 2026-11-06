"""Energia das atividades do Intervals para o modo adaptativo (`app/domain/nutrition/activity_energy.py`): normalização
ativa × bruta, deduplicação (id externo, fingerprint, possível duplicata) e agregação por dia local com "dia confirmado".
Spec `.omc/research/NUTRICAO-spec-2026-11-05.md` §4, §5, §19 e §21. Módulo puro: sem banco."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from app.domain.nutrition import activity_energy as ae
from app.domain.nutrition.activity_energy import ActivityFlag, CalorieMode, RawActivity

TMB = 2020.0
DIA = date(2026, 11, 3)
INICIO = datetime(2026, 11, 3, 18, 0)
# Sincronização às 05:45 do dia seguinte: confirma o dia 03/11 (folga de 5 h depois da meia-noite).
SYNC_APOS_DIA = datetime(2026, 11, 4, 5, 45)
ACOMPANHA_DESDE = date(2026, 10, 1)


def _raw(id: str = "a1", *, inicio: datetime = INICIO, minutos: float | None = 60, kcal=428.0, modo="gross",
         tipo="strength", externo: str | None = None, nome: str | None = "Musculação") -> RawActivity:
    return RawActivity(id=id, start=inicio, duration_seconds=None if minutos is None else minutos * 60, kind=tipo,
                       name=nome, calories_kcal=kcal, calorie_mode=modo, external_id=externo)


def _norm(raw: RawActivity, tmb=TMB):
    return ae.normalize_activity(raw, tmb)


def _dia(atividades, sync=SYNC_APOS_DIA, tmb=TMB, dia=DIA):
    return ae.summarize_days(atividades, tmb, [dia], sync, ACOMPANHA_DESDE)[0]


# --- normalização -----------------------------------------------------------------------------------------


def test_kcal_bruta_desconta_o_repouso_da_janela():
    """Spec §5.2: TMB 2.020, 60 min, 428 kcal totais -> ~344 ativas (repouso 84,17)."""
    atividade = _norm(_raw(kcal=428.0, minutos=60, modo="gross"))
    assert atividade.active_kcal == pytest.approx(428.0 - 2020.0 / 1440.0 * 60.0)
    assert round(atividade.active_kcal) == 344
    assert atividade.flags == () and atividade.counts_in_total


def test_kcal_ativa_entra_sem_desconto():
    atividade = _norm(_raw(kcal=428.0, minutos=60, modo="active"))
    assert atividade.active_kcal == 428.0
    assert atividade.calorie_mode == CalorieMode.ACTIVE


def test_kcal_bruta_menor_que_o_repouso_fica_em_zero_e_nao_negativa():
    atividade = _norm(_raw(kcal=50.0, minutos=60, modo="gross"))
    assert atividade.active_kcal == 0.0 and atividade.counts_in_total


def test_modo_desconhecido_trata_como_bruta_com_marca_de_precisao_reduzida():
    """Decisão D4 do plano."""
    atividade = _norm(_raw(modo="unknown"))
    assert atividade.active_kcal == pytest.approx(428.0 - 2020.0 / 1440.0 * 60.0)
    assert ActivityFlag.REDUCED_PRECISION in atividade.flags
    assert _norm(_raw(modo=None)).calorie_mode == CalorieMode.UNKNOWN
    assert _norm(_raw(modo="lixo")).calorie_mode == CalorieMode.UNKNOWN


@pytest.mark.parametrize("kcal", [None, 0, -10, "abc", float("nan"), ae.MAX_PLAUSIBLE_ACTIVITY_KCAL + 1])
def test_kcal_invalida_fica_fora_do_total_com_marca(kcal):
    atividade = _norm(_raw(kcal=kcal))
    assert atividade.active_kcal is None and not atividade.counts_in_total
    assert ActivityFlag.INVALID_KCAL in atividade.flags


def test_bruta_sem_duracao_ou_sem_tmb_nao_entra():
    sem_duracao = _norm(_raw(minutos=None))
    assert sem_duracao.active_kcal is None and ActivityFlag.MISSING_DURATION in sem_duracao.flags
    sem_tmb = _norm(_raw(), tmb=None)
    assert sem_tmb.active_kcal is None and ActivityFlag.MISSING_BMR in sem_tmb.flags
    # ativa não precisa de duração nem de TMB
    assert _norm(_raw(minutos=None, modo="active"), tmb=None).active_kcal == 428.0


def test_atividade_que_atravessa_a_meia_noite_fica_no_dia_em_que_comecou():
    tarde = datetime(2026, 11, 3, 23, 30)  # 60 min -> termina 00:30 do dia 04
    resumo = ae.summarize_days([_raw(inicio=tarde, minutos=60, modo="active")], TMB,
                               [DIA, DIA + timedelta(days=1)], datetime(2026, 11, 5, 6, 0), ACOMPANHA_DESDE)
    assert resumo[0].active_kcal == 428.0
    assert resumo[1].active_kcal == 0.0 and resumo[1].confirmed


# --- deduplicação -----------------------------------------------------------------------------------------


def test_mesma_atividade_duas_vezes_com_o_mesmo_id_externo_conta_uma():
    primeira = _raw("a1", externo="i123", modo="active")
    repetida = _raw("a2", externo="i123", modo="active")
    dia = _dia([primeira, repetida])
    assert dia.active_kcal == 428.0
    duplicata = next(a for a in dia.activities if a.id == "a2")
    assert duplicata.duplicate_of == "a1" and ActivityFlag.DUPLICATE_EXTERNAL_ID in duplicata.flags
    assert not duplicata.counts_in_total
    assert len(dia.activities) == 2  # a lista de exibição mantém as duas


def test_duplicata_sem_id_externo_e_pega_pelo_fingerprint():
    a = _raw("a1", modo="active")
    b = _raw("a2", modo="active", inicio=INICIO + timedelta(seconds=20))  # mesmo minuto
    dia = _dia([a, b])
    assert dia.active_kcal == 428.0
    duplicata = next(x for x in dia.activities if x.id == "a2")
    assert ActivityFlag.DUPLICATE_FINGERPRINT in duplicata.flags and duplicata.duplicate_of == "a1"


def test_fingerprint_vale_quando_so_um_dos_lados_tem_id_externo():
    dia = _dia([_raw("a1", modo="active"), _raw("a2", modo="active", externo="i9")])
    assert dia.active_kcal == 428.0


def test_dois_ids_externos_diferentes_com_o_mesmo_fingerprint_nao_sao_duplicata_certa():
    dia = _dia([_raw("a1", externo="i1", modo="active"), _raw("a2", externo="i2", modo="active")])
    assert dia.active_kcal == 856.0  # contam as duas
    assert dia.has_possible_duplicates
    assert all(ActivityFlag.POSSIBLE_DUPLICATE in a.flags for a in dia.activities)


def test_possivel_duplicata_e_sinalizada_nas_duas_e_conta_no_total():
    a = _raw("a1", modo="active", kcal=400.0, minutos=60)
    b = _raw("a2", modo="active", kcal=380.0, minutos=55, inicio=INICIO + timedelta(minutes=6))
    dia = _dia([a, b])
    assert dia.active_kcal == 780.0
    por_id = {x.id: x for x in dia.activities}
    assert por_id["a2"].possible_duplicate_of == ("a1",) and por_id["a1"].possible_duplicate_of == ("a2",)
    assert all(x.duplicate_of is None for x in dia.activities)


def test_tipos_diferentes_ou_inicio_distante_nao_sao_possivel_duplicata():
    cardio = _raw("a2", modo="active", tipo="cardio", kcal=300.0, minutos=60, inicio=INICIO + timedelta(minutes=5))
    longe = _raw("a3", modo="active", kcal=300.0, minutos=60, inicio=INICIO + timedelta(hours=3))
    dia = _dia([_raw("a1", modo="active", kcal=410.0), cardio, longe])
    assert not dia.has_possible_duplicates
    assert dia.active_kcal == 410.0 + 300.0 + 300.0


def test_deduplicate_devolve_em_ordem_cronologica_e_a_primeira_fica():
    tarde = _raw("tarde", inicio=INICIO + timedelta(hours=2), externo="x", modo="active")
    cedo = _raw("cedo", inicio=INICIO, externo="x", modo="active")
    resultado = ae.deduplicate([_norm(tarde), _norm(cedo)])
    assert [a.id for a in resultado] == ["cedo", "tarde"]
    assert resultado[0].duplicate_of is None and resultado[1].duplicate_of == "cedo"


# --- agregação por dia ------------------------------------------------------------------------------------


def test_quatro_atividades_no_mesmo_dia_somam_as_ativas_normalizadas():
    base = datetime(2026, 11, 3, 6, 0)
    atividades = [
        _raw("ida", inicio=base, minutos=20, kcal=100.0, modo="active", tipo="other", nome="Caminhada academia"),
        _raw("forca", inicio=base + timedelta(hours=1), minutos=60, kcal=428.0, modo="gross"),
        _raw("cardio", inicio=base + timedelta(hours=3), minutos=30, kcal=250.0, modo="active", tipo="cardio"),
        _raw("volta", inicio=base + timedelta(hours=5), minutos=20, kcal=90.0, modo="active", tipo="other"),
    ]
    dia = _dia(atividades)
    assert len(dia.counted) == 4
    assert dia.active_kcal == pytest.approx(100.0 + (428.0 - 2020.0 / 1440.0 * 60.0) + 250.0 + 90.0)
    assert not dia.has_possible_duplicates and not dia.reduced_precision


def test_descanso_confirmado_e_zero():
    dia = _dia([])
    assert dia.confirmed and dia.active_kcal == 0.0 and dia.activities == ()


def test_atividade_ainda_nao_sincronizada_o_dia_nao_e_confirmado_e_vira_none_nunca_zero():
    antes_do_fim = datetime(2026, 11, 3, 22, 0)
    dia = _dia([], sync=antes_do_fim)
    assert not dia.confirmed and dia.active_kcal is None
    # com atividade já conhecida, a soma também não é afirmada: a lista de exibição segue disponível
    parcial = _dia([_raw(modo="active")], sync=antes_do_fim)
    assert parcial.active_kcal is None and len(parcial.activities) == 1
    assert ae.active_kcal_by_day([], TMB, [DIA], None, ACOMPANHA_DESDE) == {DIA: None}


def test_saida_simples_por_dia_mistura_confirmado_e_pendente():
    dias = [DIA, DIA + timedelta(days=1), DIA + timedelta(days=2)]
    saida = ae.active_kcal_by_day([_raw(modo="active")], TMB, dias, datetime(2026, 11, 5, 6, 0), ACOMPANHA_DESDE)
    # sync de 05/11 06:00 cobre 03/11 e 04/11; 05/11 ainda está em andamento
    assert saida == {DIA: 428.0, dias[1]: 0.0, dias[2]: None}


def test_dia_confirmado_exige_sync_depois_do_fim_do_dia_com_folga_e_acompanhamento_iniciado():
    assert ae.day_confirmed(DIA, datetime(2026, 11, 4, 5, 0), ACOMPANHA_DESDE)  # no limite
    assert not ae.day_confirmed(DIA, datetime(2026, 11, 4, 4, 59), ACOMPANHA_DESDE)
    assert not ae.day_confirmed(DIA, None, ACOMPANHA_DESDE)
    assert not ae.day_confirmed(DIA, SYNC_APOS_DIA, None)  # sem data de início do acompanhamento não se afirma
    assert not ae.day_confirmed(date(2026, 9, 30), SYNC_APOS_DIA, ACOMPANHA_DESDE)  # antes de conectar: sem dado, não é descanso
    assert not ae.day_confirmed(date(2026, 11, 20), SYNC_APOS_DIA, ACOMPANHA_DESDE)  # futuro


def test_atividade_fora_dos_dias_pedidos_e_ignorada():
    dia = _dia([_raw(inicio=INICIO + timedelta(days=5), modo="active")])
    assert dia.activities == () and dia.active_kcal == 0.0


def test_to_dict_e_serializavel_e_traz_o_contrato():
    dia = _dia([_raw("a1", externo="i1", modo="unknown"), _raw("a2", externo="i1", modo="unknown")])
    dados = dia.to_dict()
    json.dumps(dados)
    assert dados["day"] == "2026-11-03" and dados["confirmed"] is True
    assert dados["counted_activities"] == 1 and dados["reduced_precision"] is True
    assert dados["active_kcal"] == round(428.0 - 2020.0 / 1440.0 * 60.0, 1)
    assert set(dados["activities"][0]) == {
        "id", "external_id", "start", "local_date", "duration_minutes", "kind", "name", "reported_kcal",
        "calorie_mode", "active_kcal", "counts_in_total", "flags", "duplicate_of", "possible_duplicate_of", "source",
        "correction", "excluded",
    }
    assert dados["activities"][1]["flags"][-1] == "duplicate_external_id"


def test_repouso_da_janela():
    assert ae.resting_kcal(2020.0, 60) == pytest.approx(84.1667, rel=1e-4)
    assert ae.resting_kcal(None, 60) is None and ae.resting_kcal(2020.0, 0) is None


def test_duracao_da_atividade_e_o_decorrido_com_reserva_no_movimento():
    assert ae.activity_duration_seconds(3600, 3000) == 3600.0
    assert ae.activity_duration_seconds(None, 3000) == 3000.0
    assert ae.activity_duration_seconds(0, 3000) == 3000.0  # decorrido inválido: usa o movimento
    assert ae.activity_duration_seconds("abc", None) is None
    assert ae.activity_duration_seconds(None, None) is None
    assert ae.SHADOW_DISPLAY_DAYS == 7


# --- trava da lacuna de sincronização (spec §19: nunca zero por ausência) -----------------------------------------


def _forca_em(dia: date, id: str):
    return _raw(id, inicio=datetime.combine(dia, datetime.min.time()).replace(hour=18), modo="active")


def test_janela_do_sync_espelha_a_do_job_de_sincronizacao():
    from app import intervals_sync

    assert ae.INTERVALS_SYNC_WINDOW_DAYS == intervals_sync.DEFAULT_SYNC_DAYS


def test_lacuna_longa_fora_da_janela_do_sync_nao_vira_descanso_confirmado():
    sync = datetime(2026, 11, 20, 6, 0)  # janela do job: de 21/10 em diante
    primeira, segunda = date(2026, 9, 1), date(2026, 9, 20)  # lacuna de 18 dias (02/09 a 19/09), fora da janela
    dias = [primeira + timedelta(days=i) for i in range(21)]
    resumo = ae.summarize_days([_forca_em(primeira, "a"), _forca_em(segunda, "b")], TMB, dias, sync, primeira)
    por_dia = {item.day: item for item in resumo}
    assert por_dia[primeira].confirmed and por_dia[segunda].confirmed
    assert all(not por_dia[primeira + timedelta(days=i)].confirmed and por_dia[primeira + timedelta(days=i)].active_kcal is None
               for i in range(1, 19))


def test_descanso_curto_fora_da_janela_continua_confirmado_com_zero():
    sync = datetime(2026, 11, 20, 6, 0)
    primeira = date(2026, 9, 1)
    resumo = ae.summarize_days([_forca_em(primeira, "a"), _forca_em(primeira + timedelta(days=4), "b")], TMB,
                               [primeira + timedelta(days=i) for i in range(5)], sync, primeira)
    assert [item.active_kcal for item in resumo[1:4]] == [0.0, 0.0, 0.0] and all(item.confirmed for item in resumo)


def test_dentro_da_janela_do_sync_a_lacuna_longa_e_confiavel_pois_foi_buscada_de_novo():
    sync = datetime(2026, 11, 20, 6, 0)
    dias = [date(2026, 11, 1) + timedelta(days=i) for i in range(15)]  # 01/11 a 15/11: dentro da janela
    resumo = ae.summarize_days([], TMB, dias, sync, date(2026, 10, 1))
    assert all(item.confirmed and item.active_kcal == 0.0 for item in resumo)


def test_lacuna_aberta_ate_o_ultimo_sync_tambem_e_barrada_fora_da_janela():
    sync = datetime(2026, 11, 20, 6, 0)
    primeira = date(2026, 9, 1)  # depois dela, 80 dias sem atividade até o sync; 02/09 a 09/09 fora da janela
    resumo = ae.summarize_days([_forca_em(primeira, "a")], TMB, [primeira + timedelta(days=i) for i in range(9)], sync, primeira)
    assert resumo[0].confirmed and all(not item.confirmed for item in resumo[1:])
