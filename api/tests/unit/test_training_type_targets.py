"""Vigencia das Metas de Ativacao (M12): a regra de leitura por data e a de
escrita que preserva o passado, sem banco.

O que este arquivo fixa:

- a resolucao por data (flag `DASHBOARD_PLAN_VERSION_BOUNDARY` aposentada em
  2026-10-08) decide entre "a meta de hoje" (sem `date_to`) e "a meta da
  epoca";
- uma DATA (o tipico `window.date_to`) resolve pelo FIM do dia, nao pelo
  comeco;
- o escritor so cria versao nova do que MUDOU - sem isso, salvar o cadastro
  para corrigir o nome do tipo de treino criaria uma fronteira de versao que
  nunca aconteceu.

O round-trip contra o banco de verdade (invariante do UNIQUE, intervalo sem
buraco) esta em `tests/integration/test_m12_vigencia_metas_ativacao.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import pytest

from app import training_type_targets as tt

TIPO = 7


@dataclass(frozen=True)
class _Alvo:
    """Mesma forma de `TrainingTypeMuscleGroupTargetIn` (so os campos que o
    escritor le) - o modulo nao depende do Pydantic, e o teste tambem nao."""

    muscle_group_id: int
    min_weekly_sets: float
    target_weekly_sets: float
    max_weekly_sets: float


def _row(muscle_group_id: int, minimo, alvo, maximo, *, id_=None) -> dict:
    return {
        "id": id_ if id_ is not None else muscle_group_id * 10,
        "muscle_group_id": muscle_group_id,
        "min_weekly_sets": minimo,
        "target_weekly_sets": alvo,
        "max_weekly_sets": maximo,
    }


class _Cursor:
    """Cursor de mentira: guarda o que foi executado e devolve as linhas
    vigentes que o teste preparou."""

    def __init__(self, vigentes):
        self.vigentes = list(vigentes)
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), tuple(params or ())))

    def fetchall(self):
        return list(self.vigentes)

    def _por_prefixo(self, prefixo):
        return [(sql, params) for sql, params in self.executed if sql.startswith(prefixo)]

    @property
    def supersedes(self):
        return self._por_prefixo("UPDATE")

    @property
    def inserts(self):
        return self._por_prefixo("INSERT")


# ---------------------------------------------------------------------------
# Leitura por data
# ---------------------------------------------------------------------------


@pytest.fixture
def consultas(monkeypatch):
    """Intercepta `_query`: o teste ve o SQL e os parametros que teriam ido ao
    banco, sem banco."""
    registradas: list[tuple[str, tuple]] = []

    def fake(sql, params):
        registradas.append((" ".join(sql.split()), params))
        return []

    monkeypatch.setattr(tt, "_query", fake)
    return registradas


def test_a_janela_le_a_meta_da_epoca(monkeypatch, consultas):
    tt.resolve_targets_for_window(TIPO, date(2026, 7, 15))
    sql, params = consultas[0]
    assert sql == " ".join(tt.TARGETS_AT_SQL.split())
    # Fim do dia 15/07 de Brasilia no relogio do banco (UTC) - M76 Lote 2b.
    assert params == (TIPO, datetime(2026, 7, 16, 2, 59, 59), datetime(2026, 7, 16, 2, 59, 59))


def test_sem_date_to_le_a_vigente(monkeypatch, consultas):
    """Sem fim de janela nao ha data para resolver contra. Cair em `date.min`
    devolveria a meta do ano 1 - isto e, nenhuma."""
    tt.resolve_targets_for_window(TIPO, None)
    assert consultas[0][0] == " ".join(tt.CURRENT_TARGETS_SQL.split())


def test_uma_data_resolve_pelo_fim_do_dia(consultas):
    """Se a meta foi editada as 14h do ultimo dia da janela, ela JA e a meta
    daquele dia. Resolver pelo comeco do dia devolveria a anterior. Mesma
    regra de `template_assignments._as_moment`."""
    tt.resolve_targets_at(TIPO, date(2026, 9, 3))
    assert consultas[0][1][1] == datetime(2026, 9, 4, 2, 59, 59)


def test_um_datetime_passa_intacto(consultas):
    momento = datetime(2026, 9, 3, 14, 30, 0)
    tt.resolve_targets_at(TIPO, momento)
    assert consultas[0][1][1:] == (momento, momento)


def test_effective_from_nulo_conta_como_desde_sempre():
    """As 38 linhas que a 052 encontra ficam com `effective_from` NULL, e o
    SQL tem de trata-lo como "-infinito": sem isso, o passado inteiro ficaria
    SEM meta - trocar "meta errada" por "nenhuma meta"."""
    assert "effective_from IS NULL OR effective_from <= %s" in tt.TARGETS_AT_SQL


def test_o_intervalo_e_semiaberto():
    """`superseded_at` e EXCLUSIVO: o instante em que uma versao termina e o
    instante em que a proxima comeca. Fechado dos dois lados, esse instante
    pertenceria a duas versoes e a leitura devolveria duas linhas para o
    mesmo grupo."""
    assert "superseded_at IS NULL OR superseded_at > %s" in tt.TARGETS_AT_SQL


def test_as_duas_leituras_sao_uma_consulta_so(consultas):
    """O caminho do dashboard nao ganha statement nenhum com o M12 - cada
    statement a mais e uma ida a mais a RDS Data API."""
    tt.resolve_targets_now(TIPO)
    tt.resolve_targets_at(TIPO, date(2026, 9, 3))
    assert len(consultas) == 2


def test_a_conversao_de_linhas_e_a_mesma_de_antes(monkeypatch):
    """Equivalencia de payload: mesmo formato, mesmos tipos (`float`) que
    `get_training_type_muscle_group_targets` sempre devolveu."""
    monkeypatch.setattr(
        tt, "_query", lambda sql, params: [_row(3, Decimal("10.00"), Decimal("16.00"), Decimal("22.00"))]
    )
    assert tt.resolve_targets_now(TIPO) == {3: {"min": 10.0, "target": 16.0, "max": 22.0}}


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------

AGORA = datetime(2026, 9, 26, 10, 0, 0)


def test_o_que_nao_mudou_nao_vira_versao_nova():
    """A parte que faz o historico valer alguma coisa. Salvar o cadastro so
    para corrigir o NOME do tipo de treino nao pode superar as 20 metas: o
    historico encheria de trocas ficticias e a resolucao por data passaria a
    distinguir periodos identicos."""
    cur = _Cursor([_row(1, Decimal("8.00"), Decimal("12.00"), Decimal("18.00"))])
    tt.replace_targets(cur, TIPO, [_Alvo(1, 8.0, 12.0, 18.0)], moment=AGORA)
    assert cur.supersedes == []
    assert cur.inserts == []


def test_um_valor_alterado_supera_a_versao_antiga_e_insere_a_nova():
    cur = _Cursor([_row(1, Decimal("8.00"), Decimal("12.00"), Decimal("18.00"), id_=41)])
    tt.replace_targets(cur, TIPO, [_Alvo(1, 8.0, 14.0, 18.0)], moment=AGORA)

    assert len(cur.supersedes) == 1
    assert cur.supersedes[0][1] == (AGORA, 41)
    assert len(cur.inserts) == 1
    assert cur.inserts[0][1] == (TIPO, 1, 8.0, 14.0, 18.0, AGORA)


def test_os_dois_lados_usam_o_MESMO_instante():
    """Fim de uma versao e inicio da proxima sao o mesmo instante - senao o
    intervalo semiaberto teria um buraco de alguns milissegundos em que o
    grupo nao tem meta nenhuma."""
    cur = _Cursor([_row(1, Decimal("8.00"), Decimal("12.00"), Decimal("18.00"), id_=41)])
    tt.replace_targets(cur, TIPO, [_Alvo(1, 9.0, 12.0, 18.0)], moment=AGORA)
    assert cur.supersedes[0][1][0] == cur.inserts[0][1][-1] == AGORA


def test_um_grupo_removido_e_superado_sem_insercao():
    """Tirar um grupo do cadastro deixa de APAGAR a meta dele: ela e encerrada
    e continua descrevendo o periodo em que valeu."""
    cur = _Cursor([_row(1, 8, 12, 18, id_=41), _row(2, 4, 6, 9, id_=42)])
    tt.replace_targets(cur, TIPO, [_Alvo(1, 8.0, 12.0, 18.0)], moment=AGORA)
    assert [params for _, params in cur.supersedes] == [(AGORA, 42)]
    assert cur.inserts == []


def test_um_grupo_novo_e_inserido_sem_superar_ninguem():
    cur = _Cursor([_row(1, 8, 12, 18, id_=41)])
    tt.replace_targets(cur, TIPO, [_Alvo(1, 8.0, 12.0, 18.0), _Alvo(5, 2.0, 4.0, 6.0)], moment=AGORA)
    assert cur.supersedes == []
    assert [params[1] for _, params in cur.inserts] == [5]


def test_o_escritor_nunca_apaga():
    """O DELETE fisico e exatamente o defeito que o M12 corrige."""
    cur = _Cursor([_row(1, 8, 12, 18, id_=41)])
    tt.replace_targets(cur, TIPO, [_Alvo(2, 1.0, 2.0, 3.0)], moment=AGORA)
    assert not any(sql.startswith("DELETE") for sql, _ in cur.executed)


def test_o_update_so_pega_a_linha_ainda_vigente():
    """`AND superseded_at IS NULL` no UPDATE: duas escritas concorrentes nao
    podem reescrever o fim de uma vigencia ja encerrada."""
    assert "superseded_at IS NULL" in tt.SUPERSEDE_SQL


def test_a_terceira_casa_decimal_nao_inventa_versao():
    """A coluna e `DECIMAL(6,2)`: 12.004 e 12.00 sao o MESMO valor depois de
    gravado. Comparar sem arredondar faria todo salvamento parecer edicao."""
    cur = _Cursor([_row(1, Decimal("12.00"), Decimal("16.00"), Decimal("20.00"))])
    tt.replace_targets(cur, TIPO, [_Alvo(1, 12.004, 16.0, 20.0)], moment=AGORA)
    assert cur.supersedes == [] and cur.inserts == []


def test_a_leitura_de_escrita_so_ve_as_vigentes():
    """Se o escritor enxergasse as superadas, ele compararia a meta de hoje
    com uma de julho e superaria de novo o que ja estava encerrado."""
    assert "superseded_at IS NULL" in tt.CURRENT_ROWS_FOR_WRITE_SQL
