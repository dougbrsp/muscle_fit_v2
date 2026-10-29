"""Recorde e e1RM estimado de um exercicio NUM equipamento (Modo Treino).

Exibidos no card de registro de serie, acima das series do treino anterior
("Recorde nesse equipamento: 13 x 30kg" / "e1RM estimado nesse equipamento:
43,3kg"). Mesmo escopo do `GET /exercises/last-session-sets`: usuario logado,
exercicio canonico e - quando informado - o equipamento GLOBAL
(`gym_equipment.equipment_id`), excluindo a sessao atual. O historico e o
INTEIRO (e "recorde"), nao a janela do dashboard.

Definicoes:

- RECORDE (M76 Lote 3b, decisao 3 do usuario): a regra e a do dashboard,
  `intelligence/dashboard/session_records.record_events` - nada e decidido
  aqui. Carga direcional ESTRITAMENTE maior que todas as exposicoes anteriores
  da MESMA chave (exercicio canonico + equipamento + `load_convention`); a
  primeira exposicao nao e recorde; empate na carga com mais reps nao e
  recorde; sem equipamento resolvido nao ha recorde (fora da excecao do §8.1);
  `bodyweight` nao tem recorde de carga. O Modo Treino mostra o recorde
  VIGENTE da chave (`current_record`) e as reps da serie recorde so como
  informacao. M76 Lote 3d: sem recorde vigente, mostra a "Melhor carga" da
  chave (`current_mark`/`session_records.best_load`, mesmas restricoes), com
  `kind` no payload. Antes: melhor (carga, reps) com desempate por reps, primeira
  exposicao valendo recorde e, sem `equipment_id`, equipamentos misturados.
- e1RM = o MAIOR e1RM CONFIAVEL: `domain/e1rm.reliable_e1rm`
  (Epley com RIR derivado do RPE) so de series com ate
  `E1RM_MAX_RELIABLE_REPS` reps - acima disso o Epley nao
  e confiavel e o dashboard tambem nao deixa o e1RM decidir. Sem RPE nao ha
  RIR e sem RIR nao ha e1RM; `assisted`/`bodyweight` nao tem e1RM
  (`supports_e1rm`). Nenhuma serie elegivel -> `e1rm_kg = None`.

Funcoes puras sobre linhas ja lidas, testaveis sem banco.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from ..load_conventions import (
    LoadConvention,
    normalize_load_convention,
    supports_e1rm,
)
from .e1rm import reliable_e1rm


def summarize_equipment_history(
    rows: Iterable[dict[str, Any]],
    load_convention: Any = None,
) -> dict[str, Any]:
    """`{"e1rm_kg": float | None, "load_convention": str}`.

    `rows`: series de trabalho do historico NA convencao `load_convention`
    (pode vir agregado por `weight_kg, reps, rpe`). Linhas com reps nulas/<= 0
    sao ignoradas. `load_convention` `None` = `total` (D12). O recorde sai de
    `current_record`/`record_payload` (regra do dashboard)."""
    convention = normalize_load_convention(load_convention)
    best_e1rm = 0.0

    # e1RM: SO o dono (`domain/e1rm.reliable_e1rm` - Epley + RIR do RPE, teto
    # `E1RM_MAX_RELIABLE_REPS`, sem RPE = sem e1RM na politica `SKIP`).
    if supports_e1rm(convention):
        for row in rows:
            e1rm = reliable_e1rm(row.get("weight_kg"), row.get("reps"), row.get("rpe"))
            if e1rm is not None and e1rm > best_e1rm:
                best_e1rm = e1rm

    return {
        "e1rm_kg": round(best_e1rm, 2) if best_e1rm > 0 else None,
        "load_convention": convention.value,
    }


def _history_and_key(history_rows, exercise_id, equipment_id, load_convention, exclude_activity_id):
    from ..intelligence.dashboard import session_records as sr

    rows = [
        row
        for row in history_rows or []
        if exclude_activity_id is None or str(row.get("activity_id")) != str(exclude_activity_id)
    ]
    history = sr.parse_history(rows)
    return history, sr.key_for(history, exercise_id, equipment_id, load_convention)


def current_record(
    history_rows: Iterable[Mapping[str, Any]],
    *,
    exercise_id: int,
    equipment_id: Optional[int],
    load_convention: Any = None,
    exclude_activity_id: Optional[int] = None,
):
    """Recorde VIGENTE do exercicio no escopo pedido (`RecordEvent` ou
    `None`), pela regra unica de `session_records`.

    `history_rows`: linhas de `session_records.HISTORY_SQL_TEMPLATE` do
    exercicio (historico inteiro, ordem cronologica). A sessao em andamento
    (`exclude_activity_id`) fica de fora. `equipment_id` = equipamento GLOBAL
    (`gym_equipment.equipment_id`); `None` so produz recorde na excecao do
    §8.1 (nenhuma exposicao com equipamento)."""
    from ..intelligence.dashboard import session_records as sr

    history, key = _history_and_key(history_rows, exercise_id, equipment_id, load_convention, exclude_activity_id)
    return sr.current_record(history, key)


def current_mark(
    history_rows: Iterable[Mapping[str, Any]],
    *,
    exercise_id: int,
    equipment_id: Optional[int],
    load_convention: Any = None,
    exclude_activity_id: Optional[int] = None,
):
    """A marca a bater no Modo Treino (M76 Lote 3d): `("record",
    RecordEvent)` quando a chave tem recorde vigente, `("best_load",
    BestLoad)` quando nao tem (a 1a exposicao ainda detem a marca) ou `None`
    (sem equipamento resolvido, `bodyweight`, sem carga). Mesmo escopo e
    mesmas restricoes de `current_record` - `session_records.current_mark`."""
    from ..intelligence.dashboard import session_records as sr

    history, key = _history_and_key(history_rows, exercise_id, equipment_id, load_convention, exclude_activity_id)
    return sr.current_mark(history, key)


def record_payload(
    event: Any,
    set_rows: Iterable[Mapping[str, Any]],
    kind: str = "record",
) -> Optional[dict[str, Any]]:
    """`{"reps", "weight_kg", "kind"}` do Modo Treino a partir do
    `RecordEvent` (ou `BestLoad`): a carga da marca (numero registrado;
    `assisted` = a menor assistencia) e as reps da serie (so informacao).
    `kind` = `"record"` (recorde vigente) ou `"best_load"` (melhor carga, sem
    recorde ainda) - a tela so escolhe o rotulo. `set_rows` = linhas de
    `session_records.SETS_SQL` da exposicao da marca."""
    if event is None:
        return None
    from ..intelligence.dashboard.session_records import record_set_reps

    load = event.load_kg
    return {
        "reps": record_set_reps(event, set_rows),
        "weight_kg": None if load is None else round(float(load), 2),
        "kind": kind,
    }


__all__ = ["LoadConvention", "current_mark", "current_record", "record_payload", "summarize_equipment_history"]
