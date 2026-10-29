"""Validacao do pedido de geracao de ficha (PR-18, PLANO_20260912.md 6 e 9).

Desde o PR-18 **nenhum caminho gera ficha sem um Modelo de Treino**. A
ausencia de `template_id` no payload ja e barrada pelo proprio
`WorkoutPlanGenerateIn` (campo obrigatorio -> 422 do FastAPI). O que sobra e
o que so o banco sabe: o modelo existe? esta ativo e atribuido a este
usuario? tem um slot utilizavel?

Este modulo existe separado de `plan_generation_core.py` por um motivo
pratico: aquele modulo importa os motores (e, no caminho legado, a OpenAI)
no topo, e a api-core precisa responder 422/404 **cedo, no request
sincrono**, sem pagar esse import. Um job assincrono que falha 30 segundos
depois por um modelo inexistente e experiencia ruim, e o erro nem chegaria
como status HTTP - viraria texto de job `failed`.

Quem chama:

- `workout_plans.generate_plan` (api-core): valida ANTES de criar o job, e o
  usuario recebe 404/422 na hora;
- `plan_generation_core.dispatch_plan_generation` (Lambda plan-generator):
  valida de novo, porque a Lambda e invocada com o payload cru e nao pode
  confiar em validacao feita em outro processo (e o modelo pode ter sido
  desatribuido no meio do caminho).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

# Mensagens em um lugar so: o teste de API compara com estas constantes, e o
# frontend mostra `detail` cru ao usuario.
TEMPLATE_REQUIRED_MESSAGE = "É necessário um Modelo de Treino atribuído para gerar a ficha."
TEMPLATE_NOT_FOUND_MESSAGE = "Modelo não encontrado ou não atribuído a este usuário."
TEMPLATE_SLOT_NOT_FOUND_MESSAGE = "Slot informado não existe neste modelo."
TEMPLATE_WITHOUT_SLOTS_MESSAGE = "Este modelo não tem nenhum slot preenchido."


def resolve_template_and_slot(
    template_id: Optional[int],
    user_id: int,
    template_slot_sequence: Optional[int] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Modelo + slot do pedido, ou `HTTPException`.

    Codigos, na ordem em que sao testados:

    - `template_id` ausente -> **422** (`TEMPLATE_REQUIRED_MESSAGE`). Nao e
      404: nao ha recurso nenhum sendo procurado, o pedido e que esta
      incompleto. Na pratica o FastAPI ja devolve 422 pelo schema; esta
      guarda cobre o caminho da Lambda, que monta o payload no codigo.
    - modelo inexistente, inativo ou de outro usuario -> **404**, com a MESMA
      mensagem para os tres casos (nao vaza a existencia do modelo alheio).
      Mantem o codigo que a rota ja usava antes do PR-18 e o mesmo padrao das
      demais rotas de modelo (`workout_plan_templates.py`).
    - slot pedido que nao existe / modelo sem slots -> **422**: o modelo
      existe, o que esta errado e o conteudo do pedido.
    """
    if template_id is None:
        raise HTTPException(status_code=422, detail=TEMPLATE_REQUIRED_MESSAGE)

    # Import tardio: mantem este modulo barato o bastante para a rota
    # sincrona importar (ver docstring do modulo).
    from ..workout_plan_templates_engine import get_template_if_assigned, resolve_next_slot

    template = get_template_if_assigned(template_id, user_id)
    if template is None:
        raise HTTPException(status_code=404, detail=TEMPLATE_NOT_FOUND_MESSAGE)

    slot = resolve_next_slot(template, user_id, explicit_slot_sequence=template_slot_sequence)
    if slot is None:
        raise HTTPException(
            status_code=422,
            detail=(
                TEMPLATE_SLOT_NOT_FOUND_MESSAGE
                if template_slot_sequence is not None
                else TEMPLATE_WITHOUT_SLOTS_MESSAGE
            ),
        )
    return template, slot
