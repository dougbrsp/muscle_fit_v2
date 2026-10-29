"""Vigencia da atribuicao de Modelo (`app/template_assignments.py`, M23).

Tudo aqui e PURO: `segment_window` recebe o historico pronto, `_as_moment` e
aritmetica de data. Nenhum teste toca
banco, nenhum usa `date.today()`/`datetime.now()` - as datas sao fixas e
escolhidas, que e o que torna um teste de janela reproduzivel.

O primeiro bloco e um cenario sintetico baseado em caso real (o que motivou o
M23): o usuario seguiu o "Treino ABC" de 27/08 a 14/09, teve o "Teste modo B"
por seis horas no dia 14 e esta no "Teste por grupo" desde entao.
"""

from __future__ import annotations

from datetime import date, datetime


from app.template_assignments import (
    Assignment,
    _as_moment,
    segment_window,
)

# ---------------------------------------------------------------------------
# Historico sintetico baseado em caso real
# ---------------------------------------------------------------------------

ABC = Assignment(
    id=1,
    template_id=101,
    user_id=1,
    assigned_at=datetime(2026, 8, 27, 18, 0, 0),
    unassigned_at=datetime(2026, 9, 14, 15, 0, 0),
    template_name="Treino ABC",
    training_type_id=2,
)
MODO_B = Assignment(
    id=2,
    template_id=102,
    user_id=1,
    assigned_at=datetime(2026, 9, 14, 15, 0, 0),
    unassigned_at=datetime(2026, 9, 14, 21, 0, 0),
    template_name="Teste modo B",
    training_type_id=None,
)
POR_GRUPO = Assignment(
    id=3,
    template_id=103,
    user_id=1,
    assigned_at=datetime(2026, 9, 14, 21, 0, 0),
    unassigned_at=None,
    template_name="Teste por grupo",
    training_type_id=2,
)
HISTORICO = [POR_GRUPO, MODO_B, ABC]  # como o SQL devolve: mais recente primeiro


def _ids(segments):
    return [(s.date_from, s.date_to, None if s.assignment is None else s.assignment.template_id) for s in segments]


# ---------------------------------------------------------------------------
# `is_current`
# ---------------------------------------------------------------------------


def test_vigente_e_a_que_nao_tem_fim():
    assert POR_GRUPO.is_current
    assert not ABC.is_current
    assert not MODO_B.is_current


# ---------------------------------------------------------------------------
# `_as_moment`: uma DATA vira o FIM do dia
# ---------------------------------------------------------------------------


def test_uma_data_vira_o_fim_do_dia_e_nao_o_comeco():
    """O chamador passa `window.date_to`. Um Modelo atribuido as 22h desse dia
    JA e o modelo daquele dia; resolver pelo comeco do dia devolveria o
    anterior e a janela fecharia medida contra um Modelo ja trocado.

    M76 Lote 2b: o fim do dia e o do USUARIO (Sao Paulo, UTC-3) no relogio do
    banco (UTC), onde `assigned_at`/`effective_from` nascem: 23:59:59 de
    Brasilia = 02:59:59 UTC do dia seguinte."""
    assert _as_moment(date(2026, 9, 14)) == datetime(2026, 9, 15, 2, 59, 59)


def test_o_fim_do_dia_segue_o_fuso_pedido():
    assert _as_moment(date(2026, 9, 14), "UTC") == datetime(2026, 9, 14, 23, 59, 59)
    assert _as_moment(date(2026, 9, 14), "Asia/Tokyo") == datetime(2026, 9, 14, 14, 59, 59)


def test_edicao_as_22h_de_brasilia_pertence_ao_proprio_dia():
    """O defeito que o Lote 2b corrige: `NOW()` do banco as 22h de Brasilia
    (01h UTC do dia seguinte) caia FORA do dia do usuario."""
    edicao_no_relogio_do_banco = datetime(2026, 9, 15, 1, 0, 0)  # 22h BRT de 14/09
    assert edicao_no_relogio_do_banco <= _as_moment(date(2026, 9, 14))
    assert edicao_no_relogio_do_banco > _as_moment(date(2026, 9, 13))


def test_um_datetime_passa_intacto():
    momento = datetime(2026, 9, 14, 16, 0, 0)
    assert _as_moment(momento) is momento


# ---------------------------------------------------------------------------
# `segment_window`: a fronteira de versao do plano
# ---------------------------------------------------------------------------


def test_janela_que_nao_atravessa_troca_devolve_um_segmento_so():
    """O caso comum. Uma janela inteira dentro de uma vigencia nao vira
    nenhuma complexidade nova."""
    segs = segment_window(1, date(2026, 9, 1), date(2026, 9, 10), history=HISTORICO)
    assert _ids(segs) == [(date(2026, 9, 1), date(2026, 9, 10), 101)]


def test_janela_que_atravessa_a_troca_quebra_no_dia_da_troca():
    """O ultimo dia do Modelo antigo e o dia ANTERIOR ao da troca:
    `unassigned_at` e exclusivo e, numa troca no meio do dia, o dia inteiro
    fica com o Modelo que ENTROU (mesma regra de `_as_moment`)."""
    segs = segment_window(1, date(2026, 9, 10), date(2026, 9, 20), history=HISTORICO)
    assert _ids(segs) == [
        (date(2026, 9, 10), date(2026, 9, 13), 101),
        (date(2026, 9, 14), date(2026, 9, 20), 103),
    ]


def test_atribuicao_que_nasceu_e_morreu_no_mesmo_dia_nao_vira_segmento():
    """O "Teste modo B" durou seis horas do dia 14. Ele nao cobre dia civil
    nenhum, entao nao vira segmento - mas continua no historico, que e quem
    guarda o fato de ele ter existido."""
    segs = segment_window(1, date(2026, 9, 14), date(2026, 9, 14), history=HISTORICO)
    assert _ids(segs) == [(date(2026, 9, 14), date(2026, 9, 14), 103)]


def test_o_trecho_anterior_a_primeira_atribuicao_fica_sem_modelo():
    """Boa parte das sessoes do cenario e anterior a 27/08, quando o
    usuario nao tinha Modelo nenhum. Dizer "Modelo X" para esse trecho seria medir
    o passado contra um plano que ainda nao existia - o defeito que o M23
    corrige."""
    segs = segment_window(1, date(2026, 8, 20), date(2026, 8, 31), history=HISTORICO)
    assert _ids(segs) == [
        (date(2026, 8, 20), date(2026, 8, 26), None),
        (date(2026, 8, 27), date(2026, 8, 31), 101),
    ]


def test_janela_inteiramente_anterior_a_qualquer_atribuicao():
    segs = segment_window(1, date(2026, 7, 1), date(2026, 7, 31), history=HISTORICO)
    assert _ids(segs) == [(date(2026, 7, 1), date(2026, 7, 31), None)]


def test_usuario_sem_historico_nenhum_devolve_um_segmento_vazio():
    segs = segment_window(7, date(2026, 9, 1), date(2026, 9, 30), history=[])
    assert _ids(segs) == [(date(2026, 9, 1), date(2026, 9, 30), None)]


def test_a_janela_inteira_do_usuario_cobre_os_tres_regimes():
    segs = segment_window(1, date(2026, 7, 7), date(2026, 9, 24), history=HISTORICO)
    assert _ids(segs) == [
        (date(2026, 7, 7), date(2026, 8, 26), None),
        (date(2026, 8, 27), date(2026, 9, 13), 101),
        (date(2026, 9, 14), date(2026, 9, 24), 103),
    ]


def test_os_segmentos_nao_se_sobrepoem_e_cobrem_a_janela_inteira():
    """Invariante do particionamento: sem buraco e sem dia em dois segmentos.
    Sem ela, somar por segmento contaria ou perderia sessao."""
    inicio, fim = date(2026, 7, 7), date(2026, 9, 24)
    segs = segment_window(1, inicio, fim, history=HISTORICO)
    assert segs[0].date_from == inicio
    assert segs[-1].date_to == fim
    for anterior, seguinte in zip(segs, segs[1:]):
        assert anterior.date_to < seguinte.date_from
        assert (seguinte.date_from - anterior.date_to).days == 1


def test_janela_invertida_nao_devolve_segmento():
    assert segment_window(1, date(2026, 9, 20), date(2026, 9, 10), history=HISTORICO) == []


def test_janela_de_um_dia_so():
    segs = segment_window(1, date(2026, 9, 3), date(2026, 9, 3), history=HISTORICO)
    assert _ids(segs) == [(date(2026, 9, 3), date(2026, 9, 3), 101)]


def test_a_ordem_de_entrada_do_historico_nao_importa():
    """O SQL devolve do mais recente para o mais antigo; um teste (ou um
    chamador futuro) pode passar na outra ordem. O resultado tem de ser o
    mesmo."""
    ordem_sql = segment_window(1, date(2026, 8, 1), date(2026, 9, 24), history=HISTORICO)
    ordem_inversa = segment_window(1, date(2026, 8, 1), date(2026, 9, 24), history=list(reversed(HISTORICO)))
    assert _ids(ordem_sql) == _ids(ordem_inversa)


# ---------------------------------------------------------------------------
# Robustez dos tipos: os dois backends devolvem a mesma coluna diferente
# ---------------------------------------------------------------------------


def test_data_em_string_iso_e_entendida():
    """O shim da RDS Data API ja converte `DATETIME` de volta para `datetime`,
    mas um `Assignment` montado a partir de linha crua/JSON traria string. Sem
    parse, ela viraria `date.min` e a vigencia governaria o passado inteiro."""
    from app.template_assignments import _as_date

    assert _as_date("2026-09-14 15:00:00") == date(2026, 9, 14)
    assert _as_date("2026-09-14") == date(2026, 9, 14)


def test_atribuicao_sem_inicio_legivel_nao_governa_dia_nenhum():
    """`date.min` como fallback seria um erro CARO e MUDO: a atribuicao
    passaria a responder por todo o historico do usuario."""
    quebrada = Assignment(
        id=99, template_id=99, user_id=1, assigned_at=None, unassigned_at=None, template_name="?"
    )
    segs = segment_window(1, date(2026, 9, 1), date(2026, 9, 10), history=[quebrada])
    assert _ids(segs) == [(date(2026, 9, 1), date(2026, 9, 10), None)]
