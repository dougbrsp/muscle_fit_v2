"""Motor de Periodizacao (PLANO_20260912.md secao 7).

O UNICO lugar onde a LLM permanece depois da refatoracao dos motores, e
aceitavel exatamente por causa das duas travas que vem depois dela:

1. `validation.py` - validacao DETERMINISTICA em cascata (schema -> referencial
   -> regras de modelo -> projecao de ativacao -> seguranca/RPE);
2. a edicao humana: o resultado e um RASCUNHO devolvido no job. **Nada e
   gravado em `workout_plan_templates`** aqui - so o
   `POST /workout-plan-templates` existente, disparado pelo usuario depois de
   editar, cria o modelo (PR-17).

Modulos:
- `output_schema.py` - JSON Schema estrito da saida da LLM + espelho pydantic;
- `context.py` - contexto e prompt (RAG, metas, grupos, catalogo da academia,
  tecnicas) e o tratamento das instrucoes do usuario como DADO;
- `validation.py` - o validador deterministico;
- `logs.py` - `template_generation_logs` e o limite de 10 geracoes/dia;
- `core.py` - o job em si (roda na Lambda `plan-generator`).
"""

from __future__ import annotations

from .core import PROMPT_VERSION, run_periodization_draft

__all__ = ["PROMPT_VERSION", "run_periodization_draft"]
