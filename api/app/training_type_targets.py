"""Vigencia das Metas de Ativacao: UMA versao vigente por (tipo de treino,
grupo muscular), com historico (M12; migracao 052).

Este modulo e o unico lugar que responde "qual e a meta semanal deste grupo
muscular" - e, desde a 052, tambem "qual ERA a meta em tal data". Antes dele,
a pergunta era respondida por uma unica funcao
(`training_types.get_training_type_muscle_group_targets`) que so sabia ler a
meta de HOJE, porque so a meta de hoje existia: o escritor fazia DELETE +
INSERT e o passado sumia. O efeito visivel era o historico do dashboard ser
medido contra um objetivo que, na epoca, ainda nao existia.

E o mesmo desenho de `template_assignments` (M23, migracao 049), de
proposito: a atribuicao de Modelo ja tinha vigencia com intervalo semiaberto,
invariante no banco e DELETE que virou UPDATE. Ter duas gramaticas de
vigencia no mesmo dominio seria pior do que nao ter nenhuma.

Tres perguntas, nao uma:

- `resolve_targets_now(training_type_id)` - a meta VIGENTE agora
  (`superseded_at IS NULL`). E o que a geracao de ficha, a periodizacao e os
  alertas precisam: eles planejam o futuro, e o futuro se mede pelo alvo de
  hoje.
- `resolve_targets_at(training_type_id, momento)` - a meta vigente NAQUELE
  instante, isto e, a linha cujo intervalo `[effective_from, superseded_at)`
  contem o momento. E o que um numero HISTORICO do dashboard precisa.
- `resolve_targets_for_window(training_type_id, date_to)` - a meta que governa
  uma janela que TERMINA em `date_to`, (sem `date_to`, a vigente de hoje).

A decisao de produto e uma so (M23): o historico e lido contra o plano da
epoca. A flag `DASHBOARD_PLAN_VERSION_BOUNDARY` foi aposentada em 2026-10-08
(COD-01); Modelo e meta resolvem ambos por data.

GRANULARIDADE: `DATETIME`, sem fracao de segundo (mesma escolha da 049).
Duas edicoes no MESMO segundo produzem uma versao com `effective_from ==
superseded_at`, isto e, de duracao ZERO. Isso e honesto, nao um defeito:
aquela versao nao valeu tempo nenhum, e `[x, x)` e o conjunto vazio - ela
nao governa instante algum, enquanto a vigente continua sendo UMA so. Quem
edita metas e o admin/PT pela tela de cadastro, entao o caso e raro; o
integrado o afirma em vez de evita-lo.

`effective_from IS NULL` = VIGENTE DESDE SEMPRE. E o estado em que a 052
deixa todas as linhas que ja existiam: o banco nao sabe desde quando aquelas
metas valem (o DELETE fisico apagou), e fingir uma data de inicio deixaria o
passado anterior a ela sem meta nenhuma. Por isso toda leitura por data trata
`NULL` como "-infinito", e nao como "desconhecido".
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional

from .db import transaction

TABLE = "training_type_muscle_group_targets"

# ---------------------------------------------------------------------------
# SQL (um lugar so)
# ---------------------------------------------------------------------------

_SELECT = (
    "SELECT muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets "
    f"FROM {TABLE}"
)

CURRENT_TARGETS_SQL = f"{_SELECT} WHERE training_type_id = %s AND superseded_at IS NULL"

# `effective_from IS NULL` = desde sempre; `superseded_at` e EXCLUSIVO (o
# instante em que uma versao termina e o instante em que a proxima comeca).
TARGETS_AT_SQL = (
    f"{_SELECT} WHERE training_type_id = %s "
    "AND (effective_from IS NULL OR effective_from <= %s) "
    "AND (superseded_at IS NULL OR superseded_at > %s)"
)

# Versao vigente de cada grupo, para o escritor comparar com o que chegou.
CURRENT_ROWS_FOR_WRITE_SQL = (
    "SELECT id, muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets "
    f"FROM {TABLE} WHERE training_type_id = %s AND superseded_at IS NULL"
)

SUPERSEDE_SQL = f"UPDATE {TABLE} SET superseded_at = %s WHERE id = %s AND superseded_at IS NULL"

INSERT_SQL = f"""
INSERT INTO {TABLE} (
    training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets,
    max_weekly_sets, effective_from
) VALUES (%s, %s, %s, %s, %s, %s)
"""


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _rows_to_targets(rows: Iterable[dict[str, Any]]) -> dict[int, dict[str, float]]:
    return {
        int(row["muscle_group_id"]): {
            "min": float(row["min_weekly_sets"]),
            "target": float(row["target_weekly_sets"]),
            "max": float(row["max_weekly_sets"]),
        }
        for row in rows
    }


def _query(sql: str, params: tuple) -> list[dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])


def resolve_targets_now(training_type_id: int) -> dict[int, dict[str, float]]:
    """`muscle_group_id -> {"min", "target", "max"}` das metas VIGENTES.

    Uma unica consulta, como a leitura que existia antes da 052 - o
    `superseded_at IS NULL` entra no WHERE, nao numa segunda ida ao banco.
    Importa para o caminho do dashboard, onde cada statement extra e uma ida
    a mais a RDS Data API."""
    return _rows_to_targets(_query(CURRENT_TARGETS_SQL, (training_type_id,)))


def _as_moment(moment: date | datetime) -> datetime:
    """Uma DATA vira o FIM daquele dia do usuario, no relogio do banco.

    E a MESMA regra de `template_assignments._as_moment` - delega para ela
    (M76 Lote 2b: era uma copia com 23:59:59 fixo, que comparava o dia do
    usuario com `effective_from` em UTC). O chamador tipico passa
    `window.date_to`; se a meta foi editada as 14h desse dia, ela JA e a meta
    daquele dia."""
    from .template_assignments import _as_moment as _owner

    return _owner(moment)


def resolve_targets_at(training_type_id: int, moment: date | datetime) -> dict[int, dict[str, float]]:
    """Metas vigentes NAQUELE instante. Independe da flag: quem chama esta
    funcao esta pedindo a resolucao por data explicitamente.

    O UNIQUE de `current_training_type_id` (052) garante no maximo uma linha
    vigente por (tipo, grupo), e o escritor fecha uma versao no MESMO instante
    em que abre a seguinte - entao um par nunca devolve duas linhas para o
    mesmo momento. Um grupo que nao existia naquela epoca simplesmente nao
    aparece no dicionario, e o chamador cai no fallback generico dele."""
    at = _as_moment(moment)
    return _rows_to_targets(_query(TARGETS_AT_SQL, (training_type_id, at, at)))


def resolve_targets_for_window(
    training_type_id: int, date_to: Optional[date | datetime]
) -> dict[int, dict[str, float]]:
    """Metas que governam uma janela que TERMINA em `date_to`.

    Sem `date_to` devolve as vigentes hoje; com ele, as vigentes no fim da
    janela.

    Por que o FIM da janela e nao cada dia dela: mesma razao de
    `template_assignments.resolve_template_id_for_window`. Uma janela pode
    atravessar duas versoes da meta, e somar meta parcial de duas seria uma
    metrica NOVA, nao uma correcao. O fim da janela responde "contra qual
    versao este periodo deve ser lido" com uma regra so e sem inventar
    aritmetica."""
    if date_to is None:
        return resolve_targets_now(training_type_id)
    return resolve_targets_at(training_type_id, date_to)


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def _as_float(value: Any) -> float:
    """`DECIMAL(6,2)` -> `float` ARREDONDADO na escala da coluna.

    Os dois lados da comparacao precisam morar na mesma escala: o que veio do
    banco ja passou pelo `DECIMAL(6,2)` (12.005 virou 12.01), o que chegou no
    payload nao. Sem o `round`, um cliente que mande a terceira casa veria
    "mudou" em TODO salvamento e o historico encheria de versoes identicas."""
    return round(float(value), 2)


def _same(row: dict[str, Any], target: Any) -> bool:
    """A versao vigente ja diz a mesma coisa que chegou?

    Comparacao em `float` porque a coluna volta como `Decimal` no pymysql e
    como `float`/`str` na Data API - comparar os objetos crus diria "mudou" so
    por causa do tipo, e cada PUT criaria uma versao nova identica a
    anterior."""
    return (
        _as_float(row["min_weekly_sets"]) == _as_float(target.min_weekly_sets)
        and _as_float(row["target_weekly_sets"]) == _as_float(target.target_weekly_sets)
        and _as_float(row["max_weekly_sets"]) == _as_float(target.max_weekly_sets)
    )


def replace_targets(cur: Any, training_type_id: int, targets: list[Any], *, moment: datetime) -> None:
    """Substitui o conjunto de metas de um tipo de treino PRESERVANDO o
    passado: o que saiu ou mudou e SUPERADO (`superseded_at = moment`), o que
    entrou ou mudou e INSERIDO com `effective_from = moment`.

    Recebe o CURSOR (e nao abre transacao propria) porque encerrar a versao
    antiga e abrir a nova precisam ser atomicos: um instante em que um grupo
    nao tem meta nenhuma e um estado que nenhum leitor deve conseguir
    observar. E os dois lados usam o MESMO `moment`, para o intervalo
    semiaberto nao ter buraco nem sobreposicao.

    O que NAO mudou nao e tocado. Essa e a parte que faz o historico valer
    alguma coisa: sem ela, salvar o cadastro so para corrigir o NOME do tipo
    de treino superaria as 20 metas e criaria uma fronteira de versao que nao
    aconteceu - o historico encheria de trocas ficticias e a resolucao por
    data passaria a distinguir periodos identicos."""
    cur.execute(CURRENT_ROWS_FOR_WRITE_SQL, (training_type_id,))
    vigentes = {int(row["muscle_group_id"]): row for row in (cur.fetchall() or [])}

    chegaram = {int(t.muscle_group_id): t for t in targets}

    for muscle_group_id, row in vigentes.items():
        alvo = chegaram.get(muscle_group_id)
        if alvo is not None and _same(row, alvo):
            # Inalterada: continua vigente, com a vigencia que ja tinha.
            continue
        cur.execute(SUPERSEDE_SQL, (moment, int(row["id"])))

    for muscle_group_id, alvo in chegaram.items():
        row = vigentes.get(muscle_group_id)
        if row is not None and _same(row, alvo):
            continue
        cur.execute(
            INSERT_SQL,
            (
                training_type_id,
                muscle_group_id,
                alvo.min_weekly_sets,
                alvo.target_weekly_sets,
                alvo.max_weekly_sets,
                moment,
            ),
        )
