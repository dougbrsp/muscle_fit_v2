"""Leitura do check-in diario (`daily_checkins`, migracao 050) para a engines
(Novo Dashboard F2.2 - plano §5, §6 `recovery_signals`, §11).

O check-in responde "como VOCE esta hoje" - disposicao, dor muscular global,
estresse e humor, na escala 1..5. Ele nao mede nada: e relato. Por isso entra
no hub como SINAL SUBJETIVO ao lado dos objetivos (sono, HRV, FC de repouso,
TSB, desempenho), com a mesma maquinaria de baseline pessoal, e nunca como
veredito proprio.

**O que este modulo NAO faz**, de proposito:

- nao grava nada. A escrita e do CRUD da api-core (`app/daily_checkins.py`):
  entrada de dado do usuario nao pertence a Lambda dos motores;
- nao transforma escore ausente em valor neutro. Dia sem check-in nao entra
  na janela nem na baseline - `null != 0` vale aqui como no resto do hub;
- nao soma os quatro escores num indice. Um "prontidao subjetiva = media de
  energia, dor, estresse e humor" seria formula nova (o plano proibe fora dos
  servicos donos) e, pior, esconderia o sinal: "dormi mal mas estou disposto"
  e informacao, nao ruido a ser mediado.

**Por que os quatro escores contam como UM sinal na concordancia**
(`recovery_signals_service.SignalSpec.group`): eles vem do MESMO formulario,
respondido de uma vez. Num dia ruim a pessoa marca energia baixa, humor baixo,
estresse alto e dor alta - quatro respostas correlacionadas, nao quatro
observacoes independentes. Contar cada uma como um "sinal negativo
concordante" faria a regra "dois sinais negativos elevam para ATTENTION"
disparar com UM unico check-in ruim, que e exatamente o que a regra do plano
(§7, "nenhum sinal isolado decreta recuperacao ruim") existe para evitar. Os
quatro aparecem na tela, cada um com sua baseline; na CONTAGEM eles valem um.

Janela de deploy: antes da 050 a tabela nao existe. A leitura devolve vazio
com a limitacao declarada (`TABLE_MISSING_LIMITATION`) e os sinais subjetivos
ficam `UNKNOWN` - nunca zero, nunca 500.

Custo: 1 consulta agregada por dia, por indice `(user_id, local_date)`, na
MESMA janela estendida (janela + baseline) que as outras tres leituras de
`recovery_signals_service` ja usam.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ...daily_checkins_schema import (
    NO_CHECKIN_LIMITATION,
    SCORE_FIELDS,
    TABLE,
    TABLE_MISSING_LIMITATION,
    has_daily_checkins_table,
)
from ...observability import log_event
from .coercion import to_date, to_float
from .identity import require_user_id

# Um check-in por dia (UNIQUE da 050), entao o teto por janela e o numero de
# dias. 400 cobre a janela maxima (180d) mais a baseline (28d) com folga e
# respeita o teto de 1 MB da Data API (§15).
MAX_CHECKIN_ROWS = 400

# Grupo de concordancia dos sinais subjetivos - ver o cabecalho.
CHECKIN_SIGNAL_GROUP = "daily_checkin"

CHECKIN_SIGNAL_PREFIX = "checkin_"

DAILY_CHECKINS_SQL = f"""
SELECT local_date, {", ".join(SCORE_FIELDS)}
FROM {TABLE}
WHERE user_id = %s AND local_date BETWEEN %s AND %s
ORDER BY local_date ASC
LIMIT %s
"""


def signal_code(field: str) -> str:
    """`energy` -> `checkin_energy`. O prefixo existe para o codigo do sinal
    nunca colidir com um sinal objetivo de nome parecido (`muscle_pain` da
    sessao x `checkin_soreness` do dia) - conflati-los seria somar dor medida
    com dor relatada."""
    return f"{CHECKIN_SIGNAL_PREFIX}{field}"


def load_checkins(
    conn: Any, *, user_id: Any, date_from: Any, date_to: Any
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    """`(linhas, tabela_disponivel, limitacoes)` no intervalo `[date_from,
    date_to]` (que ja inclui a janela de baseline de quem chama).

    Nunca levanta: tabela ausente (antes da 050) ou fonte indisponivel voltam
    vazias com a limitacao declarada - um sinal subjetivo ausente nao pode
    derrubar a tela de recuperacao."""
    owner = require_user_id(user_id)

    try:
        with conn.cursor() as cursor:
            if not has_daily_checkins_table(cursor):
                return [], False, [TABLE_MISSING_LIMITATION]
            cursor.execute(DAILY_CHECKINS_SQL, (owner, date_from, date_to, MAX_CHECKIN_ROWS))
            rows = list(cursor.fetchall() or [])
    except Exception as exc:  # noqa: BLE001 - ver docstring
        log_event(
            "dashboard.recovery_signal_source_unavailable",
            source=TABLE,
            user_id=owner,
            error=str(exc),
        )
        return [], False, [TABLE_MISSING_LIMITATION]

    normalized: list[dict[str, Any]] = []
    for row in rows:
        day = to_date(row.get("local_date"))
        if day is None:
            continue
        entry: dict[str, Any] = {"local_date": day}
        for field in SCORE_FIELDS:
            entry[field] = to_float(row.get(field))
        normalized.append(entry)

    limitations = [] if normalized else [NO_CHECKIN_LIMITATION]
    return normalized, True, limitations


def answered_days(rows: Sequence[Mapping[str, Any]], field: str) -> int:
    """Quantos dias tem ESTE escore respondido. Serve as limitacoes: "voce
    respondeu dor em 3 dos 28 dias" e mais util que "sem baseline"."""
    return sum(1 for row in rows if row.get(field) is not None)


def latest_checkin(rows: Sequence[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """O check-in mais recente das linhas (elas vem em ordem crescente de
    data). `None` quando nao ha nenhum - jamais uma linha de zeros."""
    return dict(rows[-1]) if rows else None


__all__ = [
    "CHECKIN_SIGNAL_GROUP",
    "CHECKIN_SIGNAL_PREFIX",
    "DAILY_CHECKINS_SQL",
    "MAX_CHECKIN_ROWS",
    "answered_days",
    "latest_checkin",
    "load_checkins",
    "signal_code",
]
