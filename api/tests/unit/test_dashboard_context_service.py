"""Eventos de contexto (`context_service`, F2.1, plano §11 `/dashboard/contexto`).

O que este arquivo protege, em ordem de risco:

1. **Derivado nao e materializado** - ele e uma FUNCAO das tabelas de origem.
   O teste que vale por todos e o de idempotencia: derivar duas vezes devolve
   exatamente a mesma lista. Era esse o defeito que um job de materializacao
   teria (duplicar o mesmo evento a cada execucao).
2. **Troca de academia precisa do dia ANTERIOR** - sem ele, o primeiro dia da
   janela pareceria sempre uma troca.
3. **Evento com duracao entra na janela se o INTERVALO a toca** - uma viagem
   que comecou antes do inicio da janela continua sendo contexto dos dias de
   dentro dela.
4. **Ausencia de evento nao e ausencia de contexto** - a limitacao declarada
   diz "nada foi anotado", e `null` nunca vira 0.

Nenhuma data e tirada do relogio: as janelas sao fixas (regra do projeto -
`date.today()` em teste de dashboard ja produziu flake real no CI).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.intelligence.dashboard import context_service as cs
from app.intelligence.dashboard.contracts import ContextResponse, DataQuality, WindowInfo, WindowKind

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
# 1) Derivado: troca de ficha
# ---------------------------------------------------------------------------


def test_atribuicao_de_modelo_vira_evento_derivado():
    janela = _window()
    eventos = cs.template_change_events(
        [{"assigned_at": datetime(2026, 9, 10, 8, 30), "template_id": 3, "template_name": "PPL"}],
        window=janela,
    )
    assert len(eventos) == 1
    assert eventos[0]["event_type"] == "plan_change"
    assert eventos[0]["local_date"] == date(2026, 9, 10)
    assert eventos[0]["source"] == "derived"
    # Derivado nao e editavel: ele nao e opiniao, e registro.
    assert eventos[0]["editable"] is False
    assert eventos[0]["id"] is None
    assert "PPL" in eventos[0]["notes"]


def test_duas_atribuicoes_no_mesmo_dia_viram_um_evento_so():
    """O contexto registra "a ficha mudou neste dia", nao quantas vezes o
    botao foi clicado."""
    janela = _window()
    eventos = cs.template_change_events(
        [
            {"assigned_at": datetime(2026, 9, 10, 8, 0), "template_id": 3, "template_name": "PPL"},
            {"assigned_at": datetime(2026, 9, 10, 8, 5), "template_id": 4, "template_name": "Upper/Lower"},
        ],
        window=janela,
    )
    assert len(eventos) == 1
    assert "Upper/Lower" in eventos[0]["notes"]


def test_atribuicao_fora_da_janela_nao_entra():
    assert cs.template_change_events(
        [{"assigned_at": datetime(2026, 1, 5, 8, 0), "template_id": 3, "template_name": "PPL"}],
        window=_window(),
    ) == []


# ---------------------------------------------------------------------------
# 2) Derivado: troca de academia
# ---------------------------------------------------------------------------


def _dia_de_treino(dia: date, gym_id: int, nome: str):
    return {"local_date": dia, "gym_id": gym_id, "gym_name": nome}


def test_troca_de_academia_entre_dias_consecutivos_vira_evento():
    janela = _window()
    eventos = cs.gym_change_events(
        [
            _dia_de_treino(DATE_TO - timedelta(days=5), 1, "Gaviões"),
            _dia_de_treino(DATE_TO - timedelta(days=2), 2, "SmartFit"),
        ],
        window=janela,
    )
    assert [e["event_type"] for e in eventos] == ["gym_change"]
    assert eventos[0]["local_date"] == DATE_TO - timedelta(days=2)
    assert "Gaviões" in eventos[0]["notes"] and "SmartFit" in eventos[0]["notes"]


def test_mesma_academia_todos_os_dias_nao_gera_evento():
    janela = _window()
    linhas = [_dia_de_treino(DATE_TO - timedelta(days=offset), 1, "Gaviões") for offset in range(5)]
    assert cs.gym_change_events(linhas, window=janela) == []


def test_primeiro_dia_da_janela_so_e_troca_se_o_dia_anterior_for_outra_academia():
    """A consulta le `GYM_LOOKBACK_DAYS` antes da janela justamente para isso:
    sem o dia anterior, todo primeiro dia pareceria uma troca."""
    janela = _window(7)
    antes = janela.date_from - timedelta(days=3)

    mesma = cs.gym_change_events(
        [_dia_de_treino(antes, 1, "Gaviões"), _dia_de_treino(janela.date_from, 1, "Gaviões")],
        window=janela,
    )
    outra = cs.gym_change_events(
        [_dia_de_treino(antes, 1, "Gaviões"), _dia_de_treino(janela.date_from, 2, "SmartFit")],
        window=janela,
    )
    assert mesma == []
    assert len(outra) == 1 and outra[0]["local_date"] == janela.date_from


def test_voltar_para_a_academia_anterior_tambem_e_troca():
    """O que muda o contexto e o equipamento disponivel ter mudado, nao a
    academia ser inedita."""
    janela = _window()
    eventos = cs.gym_change_events(
        [
            _dia_de_treino(DATE_TO - timedelta(days=6), 1, "Gaviões"),
            _dia_de_treino(DATE_TO - timedelta(days=4), 2, "SmartFit"),
            _dia_de_treino(DATE_TO - timedelta(days=1), 1, "Gaviões"),
        ],
        window=janela,
    )
    assert len(eventos) == 2


def test_derivar_duas_vezes_devolve_exatamente_a_mesma_lista():
    """Idempotencia por construcao - a razao de nao materializar derivado.

    Um job de materializacao teria de deduplicar por uma chave artificial
    para nao duplicar o evento a cada execucao; derivando na leitura, a lista
    E uma funcao dos dados de origem."""
    janela = _window()
    linhas = [
        _dia_de_treino(DATE_TO - timedelta(days=5), 1, "Gaviões"),
        _dia_de_treino(DATE_TO - timedelta(days=2), 2, "SmartFit"),
    ]
    assert cs.gym_change_events(linhas, window=janela) == cs.gym_change_events(linhas, window=janela)


# ---------------------------------------------------------------------------
# 3) Manuais e janela
# ---------------------------------------------------------------------------


def _linha_manual(**overrides):
    base = {
        "id": 10,
        "event_type": "alcohol",
        "local_date": DATE_TO - timedelta(days=2),
        "end_local_date": None,
        "intensity": 3,
        "notes": "duas taças",
        "source": "manual",
    }
    base.update(overrides)
    return base


def test_evento_manual_carrega_rotulo_e_e_editavel():
    (evento,) = cs.manual_events([_linha_manual()])
    assert evento["event_type_label"] == "Álcool"
    assert evento["editable"] is True
    assert evento["derived"] is False
    assert evento["intensity"] == 3


def test_intensidade_ausente_continua_nula_e_nunca_zero():
    (evento,) = cs.manual_events([_linha_manual(intensity=None)])
    assert evento["intensity"] is None


def test_evento_com_duracao_mantem_o_intervalo():
    (evento,) = cs.manual_events(
        [_linha_manual(event_type="travel", local_date=date(2026, 9, 1), end_local_date=date(2026, 9, 10))]
    )
    assert evento["local_date"] == date(2026, 9, 1)
    assert evento["end_local_date"] == date(2026, 9, 10)


# ---------------------------------------------------------------------------
# 4) Contagens e payload
# ---------------------------------------------------------------------------


def test_contagem_por_tipo_ordena_por_quantidade():
    eventos = [
        {"event_type": "alcohol"},
        {"event_type": "travel"},
        {"event_type": "alcohol"},
    ]
    assert cs.context_counts(eventos) == [
        {"event_type": "alcohol", "event_type_label": "Álcool", "count": 2},
        {"event_type": "travel", "event_type_label": "Viagem", "count": 1},
    ]


class _Conn:
    """Conexao de mentira: cada consulta e reconhecida pela tabela citada."""

    def __init__(self, *, manuais=(), atribuicoes=(), academias=(), ultima=None, sessoes_antes=0, tabela=True):
        self._manuais = list(manuais)
        self._atribuicoes = list(atribuicoes)
        self._academias = list(academias)
        self._ultima = ultima
        self._sessoes_antes = sessoes_antes
        self._tabela = tabela
        self._atual = None

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        texto = " ".join(sql.split())
        if "DATABASE() AS db" in texto or "SELECT DATABASE()" in texto:
            self._atual = "schema"
        elif "information_schema" in texto:
            self._atual = "existe"
        elif "FROM context_events" in texto:
            self._atual = "manuais"
        elif "workout_plan_template_assignments" in texto:
            self._atual = "atribuicoes"
        elif "MAX(local_date)" in texto:
            self._atual = "ultima"
        elif "COUNT(DISTINCT local_date)" in texto:
            self._atual = "sessoes_antes"
        else:
            self._atual = "academias"

    def fetchone(self):
        if self._atual == "schema":
            return {"db": "teste"}
        if self._atual == "existe":
            return {"n": 1 if self._tabela else 0}
        if self._atual == "ultima":
            return {"last_local_date": self._ultima}
        if self._atual == "sessoes_antes":
            return {"n": self._sessoes_antes}
        return None

    def fetchall(self):
        # `_rows()` le por `fetchall` ate nas consultas de uma linha so.
        return {
            "manuais": self._manuais,
            "atribuicoes": self._atribuicoes,
            "academias": self._academias,
            "ultima": [{"last_local_date": self._ultima}],
            "sessoes_antes": [{"n": self._sessoes_antes}],
        }.get(self._atual, [])


@pytest.fixture(autouse=True)
def _sem_cache_de_schema():
    """O cache de "a tabela existe" e por schema e por processo: um teste que
    finge a ausencia dela envenenaria os seguintes."""
    from app.context_events_schema import reset_context_events_table_cache

    reset_context_events_table_cache()
    yield
    reset_context_events_table_cache()


def test_payload_sem_evento_nenhum_existe_e_declara_o_que_falta():
    payload = cs.build_context(_Conn(), user_id=7, window=_window())
    ContextResponse.model_validate(payload)
    assert payload["items"] == []
    assert payload["data_quality"] is DataQuality.INSUFFICIENT
    assert cs.NO_EVENTS_LIMITATION in payload["limitations"]
    assert payload["manual_total"] == 0 and payload["derived_total"] == 0


def test_payload_une_manuais_e_derivados_em_uma_timeline_ordenada():
    conn = _Conn(
        manuais=[_linha_manual()],
        atribuicoes=[{"assigned_at": datetime(2026, 9, 10, 8, 0), "template_id": 3, "template_name": "PPL"}],
        academias=[
            _dia_de_treino(DATE_TO - timedelta(days=9), 1, "Gaviões"),
            _dia_de_treino(DATE_TO - timedelta(days=4), 2, "SmartFit"),
        ],
        ultima=DATE_TO - timedelta(days=1),
        sessoes_antes=12,
    )
    payload = cs.build_context(conn, user_id=7, window=_window())
    ContextResponse.model_validate(payload)

    datas = [item["local_date"] for item in payload["items"]]
    assert datas == sorted(datas)
    assert payload["manual_total"] == 1
    assert payload["derived_total"] == 2
    assert cs.DERIVED_ONLY_LIMITATION in payload["limitations"]
    assert payload["days_since_last_session"] == 1


def test_antes_da_048_os_manuais_faltam_mas_os_derivados_continuam():
    """Os derivados nao dependem da tabela nova - por isso a limitacao e
    sobre "eventos registrados", nao sobre a tela inteira."""
    from app.context_events_schema import TABLE_MISSING_LIMITATION

    conn = _Conn(
        tabela=False,
        atribuicoes=[{"assigned_at": datetime(2026, 9, 10, 8, 0), "template_id": 3, "template_name": "PPL"}],
    )
    payload = cs.build_context(conn, user_id=7, window=_window())
    ContextResponse.model_validate(payload)
    assert payload["table_available"] is False
    assert TABLE_MISSING_LIMITATION in payload["limitations"]
    assert [item["event_type"] for item in payload["items"]] == ["plan_change"]


def test_sem_sessao_nenhuma_o_intervalo_e_nulo_e_nao_zero():
    payload = cs.build_context(_Conn(), user_id=7, window=_window())
    assert payload["days_since_last_session"] is None
    assert payload["last_session_local_date"] is None


def test_ownership_e_exigido_antes_de_qualquer_sql():
    with pytest.raises(Exception):
        cs.build_context(_Conn(), user_id=None, window=_window())
