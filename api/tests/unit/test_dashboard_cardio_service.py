"""Cardio no hub (`cardio_service`, F2.3, plano §11 `/dashboard/cardio`).

O que este arquivo protege, em ordem de risco:

1. **A associacao FC <-> sessao NAO e refeita na leitura.** Ela vem de
   `activity_intervals_link`, e o que o servico faz e LER - inclusive o
   `match_status`. Um `MATCHED_AMBIGUOUS` precisa chegar ao payload: FC que
   pode ser de outro treino apresentada como certeza e o defeito mais caro
   desta tela.
1b. **A FC MEDIDA na propria sessao vence a ASSOCIADA por horario.** Quando
   `activities.avg_hr_bpm` existe (import do relogio), ela e a resposta, e
   `hr_source` diz isso - as duas nao podem virar o mesmo numero anonimo.
2. **Limitacao escolhida pelo ESTADO.** Quatro estados diferentes (sem
   Intervals, sem sincronizacao, sem cardio na janela, cardio sem FC) tem
   quatro frases. Uma frase generica mentiria em tres deles - e a regra do
   projeto e que limitacao que mente e pior do que nenhuma.
3. **Ausencia nunca vira zero.** Atividade sem sensor de FC entra nos minutos
   e fica FORA da media; `avg_hr_bpm` vira `null`, nunca 0. `coverage_pct`
   sem sessao nenhuma e `null`, porque sem denominador nao ha percentual.
4. **Classificacao nao inventa cardio.** Forca vence, e o que nao casa com
   nenhuma das listas fica em "outras" - nao vira cardio por eliminacao.

Nenhuma data sai do relogio: as janelas sao fixas (regra do projeto -
`date.today()` em teste de dashboard ja produziu flake real no CI).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app import intervals_activity_kind as kind
from app.intelligence.dashboard import cardio_service as cs
from app.intelligence.dashboard.contracts import CardioResponse, DataQuality, WindowInfo, WindowKind

DATE_TO = date(2026, 9, 22)


def _window(days: int = 28) -> WindowInfo:
    return WindowInfo(
        kind=WindowKind.ROLLING_28D,
        date_from=DATE_TO - timedelta(days=days - 1),
        date_to=DATE_TO,
        timezone="America/Sao_Paulo",
        days=days,
    )


# ---------------------------------------------------------------------------
# 0) Classificacao (modulo puro compartilhado com a tela `/cardio`)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sport_type,nome,esperado",
    [
        ("Run", None, kind.CARDIO),
        ("VirtualRide", "Zwift", kind.CARDIO),
        ("Walk", "Caminhada no parque", kind.CARDIO),
        ("WeightTraining", None, kind.STRENGTH),
        ("Workout", "Musculação", kind.STRENGTH),
        # Forca VENCE: um treino de forca que menciona esteira no nome nao
        # pode entrar nos minutos de cardio.
        ("WeightTraining", "Força + esteira no fim", kind.STRENGTH),
        # E o resto NAO vira cardio por eliminacao.
        ("Yoga", None, kind.OTHER),
        ("Other", "Alongamento", kind.OTHER),
        (None, None, kind.OTHER),
    ],
)
def test_classificacao_de_atividade(sport_type, nome, esperado):
    assert kind.classify(sport_type, nome) == esperado


def test_copias_da_tela_antiga_sairam():
    """M98 C4: o dashboard ao vivo (`/integrations/intervals/cardio-dashboard`)
    e as copias da heuristica dele sairam; "o que e cardio" so existe no dono."""
    from app import intervals_integration as legado

    for nome in ("_CARDIO_PATTERNS", "_STRENGTH_PATTERNS", "_is_cardio_activity", "_is_strength_activity"):
        assert not hasattr(legado, nome), nome
    rotas = {getattr(rota, "path", "") for rota in legado.router.routes}
    assert not any(path.endswith("/cardio-dashboard") for path in rotas)


# ---------------------------------------------------------------------------
# 1) Normalizacao: ausencia nunca vira zero
# ---------------------------------------------------------------------------


def _atividade(**over):
    linha = {
        "id": 1,
        "external_id": "i-1",
        "activity_date": date(2026, 9, 20),
        "start_time": datetime(2026, 9, 20, 6, 30),
        "name": "Corrida matinal",
        "sport_type": "Run",
        "moving_time_seconds": 1800,
        "elapsed_time_seconds": 1900,
        "distance_meters": "5000.00",
        "calories_kcal": "320.00",
        "avg_hr_bpm": "148.00",
        "max_hr_bpm": "171.00",
        "training_load": "45.00",
        "intensity": "70.00",
    }
    linha.update(over)
    return linha


def test_atividade_sem_fc_entra_nos_minutos_e_fica_fora_da_media():
    com_fc = cs.normalize_intervals_row(_atividade())
    sem_fc = cs.normalize_intervals_row(
        _atividade(id=2, external_id="i-2", avg_hr_bpm=None, max_hr_bpm=None)
    )
    assert sem_fc["avg_hr_bpm"] is None  # nunca 0
    assert sem_fc["minutes"] == 30

    totais = cs.summarize_totals([com_fc, sem_fc])
    assert totais["sessions"] == 2
    assert totais["sessions_with_hr"] == 1
    assert totais["minutes"] == 60
    # A media e das que MEDIRAM - a sem sensor nao rebaixa o numero.
    assert totais["avg_hr_bpm"] == 148.0


def test_zero_do_intervals_nao_e_medida():
    """O Intervals.icu devolve 0 para varios campos nao medidos. Somar esses
    zeros na media rebaixaria o numero de quem de fato mediu."""
    item = cs.normalize_intervals_row(_atividade(avg_hr_bpm="0.00", distance_meters="0.00"))
    assert item["avg_hr_bpm"] is None
    assert item["distance_km"] is None


def test_totais_de_janela_vazia_nao_inventam_zero():
    totais = cs.summarize_totals([])
    assert totais["sessions"] == 0
    assert totais["minutes"] == 0  # contagem/soma de minutos: 0 e observado
    assert totais["avg_hr_bpm"] is None
    assert totais["distance_km"] is None


def test_agregado_por_modalidade_ordena_por_minutos():
    itens = [
        cs.normalize_intervals_row(_atividade(id=1, sport_type="Run", moving_time_seconds=1800)),
        cs.normalize_intervals_row(_atividade(id=2, sport_type="Ride", moving_time_seconds=5400)),
        cs.normalize_intervals_row(_atividade(id=3, sport_type="Run", moving_time_seconds=1800)),
    ]
    por_modalidade = cs.summarize_by_sport(itens)
    # Ride 90 min vem antes de Run 60 min, mesmo Run tendo mais sessoes: a
    # ordem e por TEMPO, que e o que "onde foi meu cardio" pergunta.
    assert [i["sport_type"] for i in por_modalidade] == ["Ride", "Run"]
    assert por_modalidade[1]["sessions"] == 2
    assert por_modalidade[1]["minutes"] == 60


def test_empate_de_minutos_desempata_pelo_nome_para_a_ordem_ser_total():
    """Sem desempate, duas leituras do mesmo dado poderiam sair em ordens
    diferentes - e a tela pareceria instavel sem nada ter mudado."""
    itens = [
        cs.normalize_intervals_row(_atividade(id=1, sport_type="Run", moving_time_seconds=1800)),
        cs.normalize_intervals_row(_atividade(id=2, sport_type="Ride", moving_time_seconds=1800)),
    ]
    assert [i["sport_type"] for i in cs.summarize_by_sport(itens)] == ["Ride", "Run"]


# ---------------------------------------------------------------------------
# 2) FC por sessao: lida do link, nunca recalculada
# ---------------------------------------------------------------------------


def _sessao(**over):
    linha = {
        "activity_id": 10,
        "local_date": date(2026, 9, 21),
        "name": "Treino A",
        "started_at": datetime(2026, 9, 21, 19, 0),
        "duration_seconds": 3600,
        "activity_avg_hr_bpm": None,
        "activity_max_hr_bpm": None,
        "avg_hr_bpm": "118.00",
        "max_hr_bpm": "155.00",
        "calories_kcal": "410.00",
        "training_load": "38.00",
        "moving_time_seconds": 3500,
        "match_status": "MATCHED",
        "match_delta_seconds": 120,
        "candidates_count": 1,
    }
    linha.update(over)
    return linha


def test_sessao_sem_link_nao_tem_fc_e_nao_e_erro():
    """LEFT JOIN sem linha e o estado NORMAL de quem nao usa relogio."""
    item = cs.normalize_session_hr_row(
        _sessao(
            avg_hr_bpm=None,
            max_hr_bpm=None,
            match_status=None,
            match_delta_seconds=None,
            candidates_count=None,
        )
    )
    assert item["has_heart_rate"] is False
    assert item["avg_hr_bpm"] is None
    assert item["match_status"] is None


def test_link_existente_com_fc_nula_nao_conta_como_fc():
    """O relogio registrou a atividade mas nao mediu a FC: dizer "tem FC"
    aqui seria falso, mesmo com a linha de link presente."""
    item = cs.normalize_session_hr_row(_sessao(avg_hr_bpm=None, match_status="MATCHED"))
    assert item["match_status"] == "MATCHED"
    assert item["has_heart_rate"] is False


def test_fc_medida_na_propria_sessao_vence_a_associada():
    """`activities.avg_hr_bpm` e a medida daquele treino; o link e uma
    atividade vizinha no tempo. Quando as duas existem, a medida manda - e a
    origem vai declarada, porque as duas nao valem o mesmo."""
    item = cs.normalize_session_hr_row(
        _sessao(activity_avg_hr_bpm="130.00", activity_max_hr_bpm="168.00", avg_hr_bpm="118.00")
    )
    assert item["hr_source"] == cs.HR_SOURCE_ACTIVITY
    assert item["avg_hr_bpm"] == 130.0
    assert item["max_hr_bpm"] == 168.0


def test_sem_fc_propria_a_origem_e_o_link():
    item = cs.normalize_session_hr_row(_sessao())
    assert item["hr_source"] == cs.HR_SOURCE_LINK
    assert item["avg_hr_bpm"] == 118.0


def test_sessao_sem_fc_nenhuma_nao_tem_origem():
    item = cs.normalize_session_hr_row(
        _sessao(avg_hr_bpm=None, max_hr_bpm=None, match_status=None)
    )
    assert item["hr_source"] is None
    assert item["has_heart_rate"] is False


def test_ambiguidade_de_sessao_com_fc_propria_nao_rebaixa_a_resposta():
    """A sessao que tem FC propria nao depende do casamento: o job ter achado
    dois candidatos para ela nao torna o numero exibido menos confiavel."""
    itens = [
        cs.normalize_session_hr_row(
            _sessao(
                activity_avg_hr_bpm="130.00",
                match_status="MATCHED_AMBIGUOUS",
                candidates_count=2,
            )
        )
    ]
    resumo = cs.summarize_session_hr(itens)
    assert resumo["ambiguous_total"] == 1  # a ambiguidade continua VISIVEL
    assert resumo["ambiguous_in_use_total"] == 0  # mas nao esta em uso
    assert resumo["from_activity_total"] == 1 and resumo["from_link_total"] == 0


def test_casamento_ambiguo_chega_ao_payload():
    """`MATCHED_AMBIGUOUS` e a informacao que impede a tela de apresentar a
    FC como certeza. Se ela nao subir, a duvida some sem ninguem notar."""
    itens = [
        cs.normalize_session_hr_row(_sessao()),
        cs.normalize_session_hr_row(
            _sessao(activity_id=11, match_status="MATCHED_AMBIGUOUS", candidates_count=2)
        ),
    ]
    resumo = cs.summarize_session_hr(itens)
    assert resumo["ambiguous_total"] == 1
    assert resumo["ambiguous_in_use_total"] == 1  # sem FC propria, o link E a resposta
    assert resumo["items"][1]["candidates_count"] == 2


def test_cobertura_e_media_so_das_sessoes_com_fc():
    itens = [
        cs.normalize_session_hr_row(_sessao(activity_id=10, avg_hr_bpm="120.00")),
        cs.normalize_session_hr_row(_sessao(activity_id=11, avg_hr_bpm="130.00")),
        cs.normalize_session_hr_row(_sessao(activity_id=12, avg_hr_bpm=None, max_hr_bpm=None)),
    ]
    resumo = cs.summarize_session_hr(itens)
    assert resumo["sessions_total"] == 3
    assert resumo["sessions_with_hr"] == 2
    assert resumo["coverage_pct"] == pytest.approx(66.7)
    assert resumo["avg_hr_bpm"] == 125.0


def test_cobertura_sem_sessao_nenhuma_e_nula_nao_zero():
    """Sem denominador nao ha percentual - 0% diria "voce tem sessoes e
    nenhuma com FC", que e outra coisa."""
    assert cs.summarize_session_hr([])["coverage_pct"] is None


# ---------------------------------------------------------------------------
# 3) Limitacoes escolhidas pelo estado
# ---------------------------------------------------------------------------


def test_cobertura_tem_uma_frase_por_estado():
    assert cs.hr_coverage_limitation(sessions_total=0, sessions_with_hr=0) == (
        cs.NO_STRENGTH_SESSIONS_LIMITATION
    )
    nenhuma = cs.hr_coverage_limitation(sessions_total=4, sessions_with_hr=0)
    parcial = cs.hr_coverage_limitation(sessions_total=4, sessions_with_hr=3)
    assert nenhuma and parcial and nenhuma != parcial
    assert "3 de 4" not in nenhuma and "1 de 4" in parcial
    # Cobertura total nao tem ressalva a fazer: inventar uma seria ruido.
    assert cs.hr_coverage_limitation(sessions_total=4, sessions_with_hr=4) is None


@pytest.mark.parametrize(
    "estado,esperado",
    [
        (dict(configured=False, cardio_total=0, cardio_with_hr=0, strength_total=0, strength_with_hr=0, ambiguous_total=0), DataQuality.INSUFFICIENT),
        (dict(configured=True, cardio_total=0, cardio_with_hr=0, strength_total=3, strength_with_hr=0, ambiguous_total=0), DataQuality.INSUFFICIENT),
        (dict(configured=True, cardio_total=2, cardio_with_hr=2, strength_total=2, strength_with_hr=2, ambiguous_total=1), DataQuality.LOW),
        (dict(configured=True, cardio_total=2, cardio_with_hr=1, strength_total=2, strength_with_hr=2, ambiguous_total=0), DataQuality.MEDIUM),
        (dict(configured=True, cardio_total=2, cardio_with_hr=2, strength_total=2, strength_with_hr=1, ambiguous_total=0), DataQuality.MEDIUM),
        (dict(configured=True, cardio_total=2, cardio_with_hr=2, strength_total=2, strength_with_hr=2, ambiguous_total=0), DataQuality.HIGH),
    ],
)
def test_qualidade_da_resposta_por_estado(estado, esperado):
    assert cs.classify_quality(**estado) is esperado


# ---------------------------------------------------------------------------
# 4) `build_cardio` de ponta a ponta, com um banco de mentira
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, respostas):
        self._respostas = respostas
        self._linhas: list[dict] = []
        self.executados: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.executados.append(sql)
        for chave, linhas in self._respostas.items():
            if chave in sql:
                self._linhas = linhas
                return
        self._linhas = []

    def fetchall(self):
        return list(self._linhas)

    def fetchone(self):
        return self._linhas[0] if self._linhas else None


class _FakeConn:
    """Banco de mentira: casa cada consulta pela tabela citada no SQL. O que
    interessa aqui e o COMPORTAMENTO do servico, nao o dialeto."""

    def __init__(self, respostas):
        self._respostas = respostas
        self.cursores: list[_FakeCursor] = []

    def cursor(self):
        cursor = _FakeCursor(self._respostas)
        self.cursores.append(cursor)
        return cursor

    @property
    def statements(self) -> list[str]:
        return [sql for cursor in self.cursores for sql in cursor.executados]


CONFIGURADO = [{"intervals_athlete_id": "i123", "intervals_api_key_encrypted": "xxx"}]
NAO_CONFIGURADO = [{"intervals_athlete_id": None, "intervals_api_key_encrypted": None}]


# M98 C4: consultas dos blocos novos que casariam por engano com chaves mais
# genericas dos testes ("FROM activities", "FROM users"). Vem PRIMEIRO no
# dicionario: o banco de mentira usa a primeira chave contida no SQL.
BLOCOS_C4_VAZIOS = {
    # 27/10/2026: fichas de descanso com cardio (cita "FROM activities" no NOT EXISTS).
    "FROM workout_plans wp": [],
    "JOIN workout_plans": [],
    "FROM intervals_wellness": [],
    # M99: o PAI de 7 dias estimado le o tempo em zona do cache do Intervals.
    "hr_zone_seconds IS NOT NULL": [],
    "FROM cardio_recommendations": [],
    "FROM intervals_athlete_hr_zones": [],
}


def _build(respostas, *, today=DATE_TO):
    conn = _FakeConn({**BLOCOS_C4_VAZIOS, **respostas})
    return cs.build_cardio(conn, user_id=1, window=_window(), today=today), conn


def test_sem_intervals_configurado_responde_vazio_e_diz_o_que_fazer():
    payload, conn = _build({"FROM users": NAO_CONFIGURADO, "FROM activities": []})
    assert payload["intervals_configured"] is False
    assert payload["data_quality"] is DataQuality.INSUFFICIENT
    assert cs.NOT_CONFIGURED_LIMITATION in payload["limitations"]
    assert payload["totals"]["sessions"] == 0
    # Sem credencial nao existe atividade sincronizada: as duas consultas ao
    # cache do Intervals sao PULADAS - a resposta honesta e a mais barata. O
    # PAI estimado (M99) le o cache historico, como o wellness.
    assert not any(
        "intervals_activities" in sql and "hr_zone_seconds IS NOT NULL" not in sql for sql in conn.statements
    )
    # E o contrato fecha mesmo vazio (a tela nunca recebe 500 por isso).
    CardioResponse.model_validate(payload)


def test_configurado_sem_sincronizacao_nao_diz_que_faltou_cardio():
    """"Configurado e nunca sincronizado" nao e "voce nao fez cardio"."""
    payload, _ = _build(
        {"FROM users": CONFIGURADO, "FROM intervals_activities": [], "FROM activities": []}
    )
    assert cs.NEVER_SYNCED_LIMITATION in payload["limitations"]
    assert cs.NO_CARDIO_IN_WINDOW_LIMITATION not in payload["limitations"]


def test_sincronizado_sem_cardio_na_janela_declara_a_ausencia_como_ausencia_de_registro():
    payload, _ = _build(
        {
            "FROM users": CONFIGURADO,
            "FROM intervals_activities\nWHERE user_id = %s AND activity_date": [
                _atividade(sport_type="WeightTraining", name="Treino A")
            ],
            "MAX(synced_at)": [{"last_synced_at": datetime(2026, 9, 22, 5, 0)}],
            "FROM activities": [],
        }
    )
    assert cs.NO_CARDIO_IN_WINDOW_LIMITATION in payload["limitations"]
    # A ausencia fica EXPLICAVEL: ha atividade sincronizada, so nao de cardio.
    assert payload["strength_activities_total"] == 1
    assert payload["totals"]["sessions"] == 0
    assert any("22/09/2026" in texto for texto in payload["limitations"])


def test_payload_completo_fecha_no_contrato_e_carrega_a_ambiguidade():
    payload, conn = _build(
        {
            "FROM users": CONFIGURADO,
            "FROM intervals_activities\nWHERE user_id = %s AND activity_date": [
                _atividade(),
                _atividade(id=2, external_id="i-2", sport_type="Yoga", name="Yoga"),
            ],
            "MAX(synced_at)": [{"last_synced_at": datetime(2026, 9, 22, 5, 0)}],
            "FROM activities": [
                _sessao(),
                _sessao(activity_id=11, match_status="MATCHED_AMBIGUOUS", candidates_count=2),
            ],
        }
    )
    resposta = CardioResponse.model_validate(payload)
    assert resposta.totals.sessions == 1
    assert resposta.other_activities_total == 1  # Yoga nao virou cardio
    assert resposta.strength_heart_rate.sessions_with_hr == 2
    assert resposta.strength_heart_rate.ambiguous_total == 1
    assert resposta.data_quality is DataQuality.LOW  # a duvida vence a cobertura
    assert any("MAIS DE UMA atividade" in texto for texto in resposta.limitations)
    assert cs.HR_MATCH_LIMITATION in resposta.limitations
    assert resposta.strength_heart_rate.from_link_total == 2
    # Custo: consultas fixas, nenhuma por item (o N+1 e o que derruba estes
    # endpoints em producao, e aqui ele e impossivel por construcao): as 4 da
    # F2.3 + as da C4 (aderencia sem sessao planejada 1, wellness 1, PAI 1,
    # recomendacao 1, zonas 2) + a das fichas de descanso com cardio
    # (27/10/2026). O fuso vem da rota (`today`).
    assert len(conn.statements) == 11
    # Nenhum bloco caiu no caminho de degradacao.
    assert not any("Não foi possível ler" in texto for texto in resposta.limitations)


def test_fc_de_sessao_sobrevive_a_chave_revogada():
    """O link e HISTORICO. Revogar a chave para de trazer cardio novo; nao
    apaga a FC dos treinos passados."""
    payload, _ = _build({"FROM users": NAO_CONFIGURADO, "FROM activities": [_sessao()]})
    assert payload["intervals_configured"] is False
    assert payload["strength_heart_rate"]["sessions_with_hr"] == 1
    assert payload["strength_heart_rate"]["avg_hr_bpm"] == 118.0


def test_limitacoes_nao_se_repetem():
    payload, _ = _build(
        {
            "FROM users": CONFIGURADO,
            "FROM intervals_activities\nWHERE user_id = %s AND activity_date": [_atividade()],
            "MAX(synced_at)": [{"last_synced_at": datetime(2026, 9, 22, 5, 0)}],
            "FROM activities": [_sessao(), _sessao(activity_id=11)],
        }
    )
    assert len(payload["limitations"]) == len(set(payload["limitations"]))


def test_usuario_e_obrigatorio():
    with pytest.raises(ValueError):
        cs.build_cardio(_FakeConn({}), user_id=None, window=_window(), today=DATE_TO)


# ---------------------------------------------------------------------------
# 5) M37 - o lado do INTERVALS do casamento
# ---------------------------------------------------------------------------

_FORCA = {"sport_type": "WeightTraining", "name": "Treino A"}


def test_atividade_de_forca_sem_id_conta_como_nao_casada():
    """Sem id ela nunca pode aparecer no link: assumir casamento esconderia o
    caso, e o numero passaria a mentir para menos."""
    atividades = [
        cs.normalize_intervals_row(_atividade(id=7, **_FORCA)),
        cs.normalize_intervals_row(_atividade(id=None, external_id="i-2", **_FORCA)),
    ]
    assert cs.count_unmatched_strength(atividades, {7}) == 1


def test_contador_de_sobra_e_zero_quando_todas_casaram():
    atividades = [cs.normalize_intervals_row(_atividade(id=7, **_FORCA))]
    assert cs.count_unmatched_strength(atividades, {7, 99}) == 0


def _build_com_forca(linked_rows):
    return _build(
        {
            "FROM users": CONFIGURADO,
            "FROM intervals_activities\nWHERE user_id = %s AND activity_date": [
                _atividade(id=7, **_FORCA),
                _atividade(id=8, external_id="i-2", **_FORCA),
            ],
            "FROM activity_intervals_link": linked_rows,
            "MAX(synced_at)": [{"last_synced_at": datetime(2026, 9, 22, 5, 0)}],
            "FROM activities": [],
        }
    )


def test_sobra_do_lado_do_intervals_vira_contador_e_ressalva():
    payload, _ = _build_com_forca([{"intervals_activity_id": 7}])
    assert payload["strength_activities_total"] == 2
    assert payload["unmatched_strength_activities_total"] == 1
    assert any("não casaram" in texto for texto in payload["limitations"])
    CardioResponse.model_validate(payload)


def test_sem_sobra_a_ressalva_nao_aparece():
    """Ressalva que nao tem o que ressalvar ensina a ignorar ressalva."""
    payload, _ = _build_com_forca([{"intervals_activity_id": 7}, {"intervals_activity_id": 8}])
    assert payload["unmatched_strength_activities_total"] == 0
    assert not any("não casaram" in texto for texto in payload["limitations"])


def test_sem_atividade_de_forca_na_janela_a_consulta_do_m37_nao_e_emitida():
    """A 5a consulta e CONDICIONAL: sem forca a diferenca e vazia por
    construcao, e pagar um statement para confirmar isso seria desperdicio."""
    payload, conn = _build(
        {
            "FROM users": CONFIGURADO,
            "FROM intervals_activities\nWHERE user_id = %s AND activity_date": [_atividade()],
            "MAX(synced_at)": [{"last_synced_at": datetime(2026, 9, 22, 5, 0)}],
            "FROM activities": [],
        }
    )
    assert payload["unmatched_strength_activities_total"] == 0
    assert not any("FROM activity_intervals_link l" in sql for sql in conn.statements)
