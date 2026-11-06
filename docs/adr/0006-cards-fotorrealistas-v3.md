# ADR 0006: Cards de exercício fotorrealistas (V3), com duas cenas e fotos de referência do admin

- **Status**: Aceita. Implementada na branch `feat/cards-fotorrealistas-v3`; ainda não promovida para produção.
- **Data**: 2026-11-06

## Contexto

Evidência de produção que motivou a mudança: na geração V2 (ilustração com recorte por alfa e cor), foram feitas 38 gerações e 13 foram rejeitadas. Todas as 13 rejeições foram por posicionamento no aparelho: rolo da cadeira flexora, polia do tríceps, leg press a 90°, pegada da rosca inversa. Nesses casos, o administrador colava na instrução URLs de fotos reais para corrigir a posição, e o modelo não recebia essas imagens.

Estado anterior no código: o worker gerava uma única imagem e a recortava no compositor V2 (`CARD_LAYOUT_V2`). Esse recorte e o layout foram removidos.

Drivers:

- **Posição do aparelho**: o principal motivo de rejeição exige referência visual. Texto sozinho não resolveu.
- **Fundo**: o recorte por alfa e cor era frágil. O card passa a usar fundo opaco, sem recorte.
- **Tempo e custo**: o job precisa caber no timeout da Lambda `musclefit-plan-generator`, e o custo precisa ser rastreável por chamada.
- **Regras do projeto**: fonte única dos motores, sem AWS aplicada à mão e nada só-dev indo para produção.

## Alternativas consideradas

1. **Gemini Nano Banana Pro.** Rejeitada: exige chave de um fornecedor novo e não oferece transparência.
2. **Bedrock com Nova Canvas.** Rejeitada: o modelo está em fim de vida.
3. **Upload por presigned PUT.** Rejeitada: são dois passos, exige CORS no bucket, e o objeto não é validado antes de ser gravado. O envio em JSON base64 pela api-core permite validar tipo, tamanho e dimensões, e re-encodar a imagem.
4. **Painéis empilhados verticalmente.** Rejeitada: com recorte `cover`, a foto mostraria só cerca de um quarto da altura do painel e cortaria o aparelho.
5. **Fundo transparente (`background=transparent`).** Rejeitada: o compositor não recorta mais, e o fundo opaco é mais estável.
6. **Lambda dedicada aos cards.** Rejeitada: exigiria nova função, nova role e novo workflow. O padrão de job assíncrono (`async_jobs`) já atende.
7. **Dividir o card em dois jobs.** Rejeitada: exigiria coordenar dois jobs para uma única aprovação.

## Decisão

Cada card é gerado com `gpt-image-2.5-sunburst`, qualidade `high`, tamanho 1024x1536 e fundo `opaque`, em duas chamadas sequenciais no mesmo job:

- **Cena 1 (posição inicial)**: `/v1/images/generations` quando não há fotos de referência, ou `/v1/images/edits` com elas.
- **Cena 2 (movimento)**: `/v1/images/edits`, com a cena 1 re-encodada em JPEG q92 e as fotos de referência.

O compositor `CARD_LAYOUT_V3_PHOTO` põe as duas cenas lado a lado, com corte centralizado e cantos arredondados, rótulos fora das fotos, logo e título em Oswald, e rodapé com o mapa corporal.

As fotos de referência são enviadas pelo admin, até três por exercício, e entram nas duas chamadas.

O teto diário de gerações é `EXERCISE_CARD_MAX_GENERATIONS_PER_DAY`, com padrão 40.

## Trade-offs

- **Ganho**: fotos realistas, com o aparelho posicionado a partir de referência real. O compositor fica mais simples, sem recorte.
- **Custo**: a cobrança passa a ser por token, então o custo varia por card. Há duas chamadas por card, o que aumenta a latência e a chance de falha.

## Consequências

### Custo

- O GPT Image 2.5 é cobrado por token: US$ 5 por 1M de tokens de texto de entrada, US$ 8 por 1M de imagem de entrada e US$ 30 por 1M de imagem de saída (`openai_client.IMAGE_TOKEN_PRICES_USD_PER_MILLION`).
- O `cost_usd` de cada geração é calculado do `usage` de cada chamada, pela função `openai_client.image_cost_usd`, e somado.
- A estimativa de agregadores, de cerca de US$ 0,33 a 0,35 por card, não foi confirmada. O preço real precisa ser medido na calibração.

### Tempo

- Orçamento do job: `exercise_card_jobs.JOB_TIME_BUDGET_SECONDS` vale 400 s. Fica 20 s abaixo do timeout da Lambda, que é 420 s (`async_jobs.LAMBDA_TIMEOUT_SECONDS`).
- Cada chamada tem timeout de 150 s (`OPENAI_IMAGE_TIMEOUT_SECONDS`). O pior caso de uma chamada, com retry, é de 173 s. Duas chamadas mais a reserva de pós-processamento de 25 s somam 371 s, dentro do orçamento.
- Se a cena 2 não couber no orçamento, o job termina em `FAILED` com `TIME_BUDGET_EXCEEDED`, e o custo da cena 1 fica registrado.
- A latência típica, de 60 a 90 s por card, vem de testes de terceiros. Não foi medida neste projeto.
- O prazo `worker_lost` dos jobs da Lambda `musclefit-plan-generator` sobe de 240 s para 480 s (`async_jobs.worker_lost_deadline_seconds`). Isso vale também para os jobs de nutrição que rodam nessa Lambda.
- A periodização não herda o timeout novo. O teto da LLM dela continua fixo em 150 s (`periodization.core.PERIODIZATION_LLM_TIMEOUT_SECONDS`), sem mudança de semântica do motor.

### Armazenamento e migração

- Migração 109 (`api/db/migrations/109_exercise_card_references.py`): cria a tabela `exercise_card_references`. É aditiva e não tem chave estrangeira, porque `exercise_id` é referência lógica a `exercise_catalog`. O soft-delete usa `deleted_at`. O limite de três fotos ativas por exercício é do serviço, não do banco (`exercise_card_references.MAX_ACTIVE_REFERENCES`).
- Fotos de referência: chaves `exercise-card-references/{exercise_id}/{ref_id}.jpg` no bucket privado `musclefit-uploads`, fora da CDN (`exercise_card_storage.REFERENCE_PREFIX`).
- Rascunhos: `exercise-card-drafts/{gid}/scene1.png`, `scene2.png` e `preview.png`. O antigo `illustration.png` deixou de ser gravado.
- IAM: a Lambda `musclefit-plan-generator` ganha leitura só no prefixo `exercise-card-references/*`, statement `ExerciseCardReferencesRead` em `infra/lambda_ai_generators.tf`.

### Upload de referência

- Rotas admin `POST`, `GET` e `DELETE` em `/exercises/{id}/card-references`, definidas em `api/app/exercise_cards.py`.
- O corpo é JSON com `image_base64`, com limite de 2 MiB decodificados (`exercise_card_references.MAX_UPLOAD_BYTES`). O limite conta bytes decodificados por causa da codificação base64 do API Gateway, configurado com `binary_media_types=["*/*"]`.
- O tipo é conferido pela assinatura do arquivo (PNG, JPEG ou WebP), nunca pelo declarado. A imagem é re-encodada em JPEG q90, sem metadados, com Pillow.
- O `GET` devolve `limits`, para que o frontend não repita números.

### Rollback

- Trocar modelo ou qualidade por variáveis de ambiente: `OPENAI_IMAGE_MODEL=gpt-image-1.5` e `OPENAI_IMAGE_QUALITY=medium` mantêm o fluxo V3. Esse caminho ainda precisa ser confirmado em teste real antes de ser usado.
- Desligar a geração: `EXERCISE_CARD_GENERATION_ENABLED=false`. As rotas de geração devolvem 503 `exercise_card_generation_disabled`, e `GET /card` continua funcionando.
- Reverter o PR restaura o código anterior. A migração 109 é aditiva e pode ficar sem uso.

### Pendências

- Medir o preço real por card na calibração (`scripts/exercise_card_calibration.py`), com o `usage` das duas chamadas.
- Medir a latência real por card contra o orçamento de 400 s.
- Calibrar o layout e a proporção das cenas com fotos reais.
- Confirmar o fluxo V3 com o modelo de rollback antes de depender dele.

## Referências

- Worker e orçamento de tempo: `api/app/exercise_card_jobs.py`.
- Cliente de imagem, preços e `generate_image`: `api/app/openai_client.py`.
- Prompt por cena: `api/app/exercise_card_prompt.py` (`PROMPT_VERSION`, `SCENE_*`, `build_scene_prompts`) e os 33 templates `api/privado/prompts/exercise_card/exercise-card-v3.*`.
- Compositor: `api/app/exercise_card_composer.py` (`LAYOUT_VERSION`, `compose_card_v3`).
- Fotos de referência: `api/app/exercise_card_references.py`, `api/app/exercise_card_storage.py`, `api/app/exercise_cards.py`.
- Timeouts compartilhados: `api/app/async_jobs.py`, `api/app/periodization/core.py`.
- Serviço e teto diário: `api/app/exercise_card_service.py`.
- Infraestrutura: `infra/lambda_ai_generators.tf`.
- Calibração: `scripts/exercise_card_calibration.py`.
- Mapa de fontes da verdade: `docs/motores/FONTES_DA_VERDADE.md`, seção 73.4.
- Documentação oficial da OpenAI consultada: referências de `images/edit`, `images/generate` e preços, citadas em `api/app/openai_client.py`.
