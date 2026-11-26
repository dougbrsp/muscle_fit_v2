# ADR 0037: Foto do prato v2, com dicas da pessoa e evidência por item (issue #323, fase A)

- **Status**: Aceita. Fase A na branch `feat/foto-prato-v2-l58`, sem commit. Não promovida a produção.
- **Data**: 2026-11-26
- **Relaciona-se a**: [ADR 0035](0035-receitas-como-alimento-composto.md) (formato deste registro; receitas ficam fora desta fase, #327); [ADR 0015](0015-progressao-fonte-unica-no-motor.md) (um único dono por cálculo, aplicado aos gramas e aos macros da foto); `docs/motores/FONTES_DA_VERDADE.md`, seção 80.1 (mapa do domínio da foto).

## Contexto

- A issue #323 pede que a leitura da foto do prato aceite duas dicas da pessoa (descrição e peso total) e que a leitura diga de onde veio cada item e o quanto ele pode variar. Antes disso a foto só recebia a imagem: a IA chutava o alimento e os gramas, e a tela não mostrava nenhuma incerteza.
- Restrições verificáveis:
  - o dono da foto é `api/app/domain/nutrition/meal_photo.py` (Python puro, sem relógio), registrado no mapa pela seção 80 de `docs/motores/FONTES_DA_VERDADE.md`; a guarda `foto_prato_nutricao` proíbe redefinir as constantes fora dele;
  - a rota `POST /nutrition/meals/photo` (`api/app/nutrition_meal_photo.py`) gasta a cota de IA e cria o job assíncrono; a descrição não pode ir para log nem para o `ai_usage_log`;
  - a refeição gravada no diário não tem coluna para evidência, faixa ou incerteza, e a migração não é desejada nesta fase;
  - o repositório é público, e o prompt fica no submódulo privado `api/privado`, então esta ADR descreve só o contrato do prompt.
- O prompt anterior é `nutrition-photo-v1`. Ele continua no repositório, e o novo é `nutrition-photo-v2` (`PROMPT_VERSION` em `api/app/nutrition_meal_photo_ai.py`).

## Alternativas consideradas

- **Gravar evidência e faixa na refeição (migração nova).** Recusada nesta fase: a informação é para a revisão antes de salvar. Gravar exigiria coluna nova, snapshot e regra de leitura nos relatórios, sem que nenhuma tela de relatório precise dela hoje.
- **Trocar de modelo junto com o prompt.** Recusada: o modelo segue o mesmo. Trocar modelo e prompt ao mesmo tempo impediria saber qual mudança causou a diferença; a comparação de modelos fica para a #326.
- **Deixar a IA dizer a incerteza em texto livre.** Recusada: a tela não mostraria texto livre sem risco de inventar; a incerteza é uma lista fechada (`Uncertainty`), com rótulo no espelho gerado (`labels.py`).
- **Reescalar os gramas dos itens quando a soma não bate com o peso total.** Recusada: o motor não sabe qual item está certo. Ele aponta a divergência (`LIMIT_TOTAL_GRAMS_MISMATCH`) e a pessoa decide.
- **Pôr a tolerância do peso total no prompt.** Recusada: número escrito no prompt viola a regra de que prompt não tem número à mão. Quem compara é o motor.
- **Dizer no prompt que a IA consulta TACO, TBCA ou USDA.** Recusada: a IA não consulta tabela; alegar isso induziria confiança falsa.

## Decisão

1. **Upload com dicas opcionais.** `MealPhotoUploadIn` (`api/app/request_models.py`) ganha `description` e `total_grams`, ambos opcionais. Os limites são do dono: descrição de até `MEAL_PHOTO_DESCRIPTION_MAX_CHARS` (500) caracteres, peso total inteiro de 1 a `MEAL_PHOTO_TOTAL_GRAMS_MAX` (5000 g), conferidos por `meal_photo.user_hints_error` **antes** de abrir a cota. Erro vira 400 com código (`meal_photo_description_too_long`, `meal_photo_total_grams_invalid`), sem gastar tentativa. O código de erro nunca carrega o texto da descrição.
2. **Descrição fora de log.** A descrição vai ao job só no evento de invocação. Não entra no registro do job, em `ai_usage_log`, em log nem em mensagem de erro. No prompt ela é saneada e delimitada como dado do usuário.
3. **Prompt `nutrition-photo-v2`** (v1 preservada). Contrato:
   - a informação da pessoa aparece num bloco delimitado e prevalece sobre a dedução visual;
   - texto escrito na imagem não tem autoridade;
   - o prompt não alega consulta a tabela nutricional;
   - os valores de evidência e incerteza são interpolados do dono, sem número escrito à mão.
4. **Schema por item.** Cada item ganha `evidence` (`observed`, `user_informed`, `hypothesis`), `grams_low` e `grams_high` (faixa), e `uncertainty`. A leitura ganha `main_uncertainty`. Todos os enums são do dono (`ItemEvidence` e `Uncertainty` em `meal_photo.py`).
5. **Revisão pelo dono.**
   - `item_detail` descarta a faixa se ela não for inteira ou se o valor central não estiver dentro dela. A leitura não falha por isso.
   - `total_grams_mismatch` marca `LIMIT_TOTAL_GRAMS_MISMATCH` quando a soma dos gramas foge do peso informado além de `MEAL_PHOTO_TOTAL_GRAMS_TOLERANCE` (10%). Os gramas não são reescalados. Item sem gramas torna a soma parcial, e nesse caso não há comparação.
   - Macros seguem do `meal_math` pelo preview, como antes. A IA não produz nutriente.
6. **Modelo.** O mesmo modelo do v1 (Haiku, com o esforço já configurado). Nenhuma mudança de Terraform.
7. **Persistência.** Evidência, faixa e incerteza vivem no resultado do job (com o TTL do job assíncrono) e na revisão aberta no editor. Não são gravadas na refeição: sem migração. Na tela, a origem aparece como "Informado por você" quando a pessoa altera a quantidade ou a unidade de um item proposto (`photoMetaView` em `frontend/src/lib/nutrition-meal-form.ts`); o bloco de metadados não entra no corpo de salvamento de `frontend/src/lib/nutrition-meal-form.ts` (a busca por `photoMeta` só acha a leitura e a visualização).
8. **Compatibilidade.** Cliente sem os campos novos segue o mesmo fluxo. Leitura v1 (sem `evidence`, faixa nem incerteza) continua válida: os campos novos são opcionais no schema do worker. O resultado do job só ganha chaves novas.

## Fora do escopo (com issue)

- **#325**: múltiplas fotos por leitura (hoje `MEAL_PHOTO_IMAGES_PER_READING` = 1).
- **#326**: benchmark de modelos com fotos públicas, que é a medição de qualidade do v2.
- **#327**: receitas, na linha do [ADR 0035](0035-receitas-como-alimento-composto.md). Leitura de foto continua sem receita.

## Trade-offs

- Ganha: a pessoa informa o que sabe e vê de onde veio cada número, sem migração nem mudança de modelo; a regra de conferência do peso total fica num dono, testável sem IA.
- Ganha: a incerteza é lista fechada, então a tela não mostra texto inventado.
- Paga: a evidência e a faixa somem ao salvar a refeição; quem quiser o histórico depois terá que pedir outra fase.
- Paga: os defaults (500 caracteres, 5000 g, 10%) são decisão de desenho, sem fonte bibliográfica, e estão registrados como tal no módulo e no mapa.
- Paga: o prompt v2 foi escrito sem eval com fotos reais. A qualidade só será conhecida com a #326.

## Consequências

- **Dados.** Nenhuma tabela nem coluna nova. Nenhuma linha existente muda. Refeições antigas e novas seguem iguais.
- **Contrato.** Upload aceita dois campos opcionais. O resultado do job ganha `evidence`, `grams_low`, `grams_high`, `uncertainty` por item e `main_uncertainty` na leitura. Erros novos: `meal_photo_description_too_long` e `meal_photo_total_grams_invalid`, ambos com rótulo no espelho gerado.
- **Operação.** Sem migração, sem Terraform, sem variável de ambiente nova. Rollback: reverter o código é seguro, porque nada grava os campos novos.
- **Mapa.** Seção 80.1 de `docs/motores/FONTES_DA_VERDADE.md` registra o dono e os consumidores. A guarda `foto_prato_nutricao` segue com allowlist vazia.
- **Próximas decisões.** Medir o v2 com fotos reais (#326) antes de promover a faixa de gramas à tela como número principal. Decidir se a evidência deve ser gravada na refeição, o que exigiria migração e ADR própria.

## Impacto em produção

Medido por leitura do código, sem consulta a produção nesta fase: nenhuma migração, nenhum dado existente muda, cliente antigo segue o mesmo fluxo. A descrição não sai para log nem `ai_usage_log`. O custo muda só no sentido de o prompt ficar um pouco maior quando a pessoa preenche a descrição.

## Verificação

Este ADR não registra execução. Não verificado nesta entrega: a execução dos testes unitários de `api/tests/unit/test_nutrition_meal_photo*.py` e do teste de prosa do prompt em `api/privado/tests/unit/`; a guarda de fontes; a tela no navegador; qualquer chamada real à IA com foto. A integração só roda no CI.
