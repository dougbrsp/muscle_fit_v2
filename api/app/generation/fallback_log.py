"""`generation.fallback` (PLANO_20260912.md, secao 14) num lugar so.

Pergunta que este modulo responde: quando a geracao de ficha recorre a um
caminho ALTERNATIVO ao pretendido, como isso vira uma linha de log identica
venha ela do dispatcher, do Modo A ou do Modo B.

A metrica desse evento e **esperada ZERO em producao** (secao 14, "Metricas").
E exatamente por isso que ele precisa existir: hoje, quando a degradacao
acontece, ela fica so na auditoria do plano (`generation_audit.fallbacks[]`) e
ninguem fica sabendo sem abrir o banco.

**Conteudo.** Apenas id numerico de usuario, ids de modelo/grupo/exercicio,
`engine_version` e `reason_code`. Nada de nome de exercicio, de grupo, de
academia, nota livre, dor ou instrucao do usuario - o motivo em texto continua
na auditoria do plano e nos `warnings` da resposta.

Este modulo NAO decide nada: quem sabe se houve fallback e o motor.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from ..observability import EVENT_GENERATION_FALLBACK, log_event


def log_generation_fallback(
    *,
    reason_code: str,
    user_id: Optional[int] = None,
    template_id: Optional[int] = None,
    engine_version: Optional[str] = None,
    volume_mode: Optional[str] = None,
    muscle_group_id: Optional[int] = None,
    exercise_id: Optional[int] = None,
    **extra: Any,
) -> None:
    """Uma linha de `generation.fallback`.

    So parametros nomeados: o `reason_code` e obrigatorio porque um fallback
    sem motivo nao responde a pergunta que a metrica faz. `extra` existe para
    campos escalares de configuracao (ex.: `progression_engine`) - nunca para
    texto vindo do usuario.
    """
    log_event(
        EVENT_GENERATION_FALLBACK,
        user_id=None if user_id is None else int(user_id),
        template_id=template_id,
        engine_version=engine_version,
        volume_mode=volume_mode,
        muscle_group_id=muscle_group_id,
        exercise_id=exercise_id,
        reason_code=reason_code,
        **extra,
    )


def log_generation_fallbacks(
    fallbacks: Iterable[Mapping[str, Any]],
    *,
    user_id: Optional[int],
    template_id: Optional[int],
    engine_version: str,
    volume_mode: str,
) -> None:
    """Espelha `generation_audit.fallbacks[]` no log, uma linha por entrada.

    De cada entrada saem SO os tres campos que a secao 14 admite - o
    `muscle_group_name`, o `deficit` e o `required_technique` continuam na
    auditoria do plano, que e onde alguem investiga o caso concreto.
    Entrada sem `reason` e ignorada (nao ha o que medir).
    """
    for fallback in fallbacks:
        reason_code = fallback.get("reason")
        if not reason_code:
            continue
        log_generation_fallback(
            reason_code=str(reason_code),
            user_id=user_id,
            template_id=template_id,
            engine_version=engine_version,
            volume_mode=volume_mode,
            muscle_group_id=fallback.get("muscle_group_id"),
            exercise_id=fallback.get("exercise_id"),
        )
