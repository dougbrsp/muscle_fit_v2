# ADR 0037: Foto do prato v2, com dicas da pessoa e evidência por item (issue #323, fase A)

- **Status**: Aceita, 2026-11-26, PR #329 (fase A da #323).
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
2. **Descrição fora de log.** A descrição vai ao job só no evento de invocação. Não entra no registro do job, em `ai_usage_log`, em log nem em mensagem de erro. No prompt ela é saneada e delimitada como dado do usuário. O saneamento (`prompt_user_data.sanitize_user_text`) neutraliza o delimitador `dados_do_usuario` em qualquer forma de tag de abertura ou fechamento, com atributos, sem `>` final ou autofechada (ajuste abaixo).
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
7. **Persistência.** Evidência, faixa e incerteza vivem no resultado do job (com o TTL do job assíncrono) e na revisão aberta no editor. Não são gravadas na refeição: sem migração. Na tela, a origem aparece como "Informado por você" quando a pessoa altera a quantidade ou a unidade de um item proposto, e também ao trocar o alimento (regra revista nos ajustes posteriores; `photoMetaView` em `frontend/src/lib/nutrition-meal-form.ts`); o bloco de metadados não entra no corpo de salvamento de `frontend/src/lib/nutrition-meal-form.ts` (a busca por `photoMeta` só acha a leitura e a visualização).
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

### Ajustes posteriores (#328/#330)

Estes ajustes vieram no PR das issues #328/#330 (branch `fix/p3-foto-prato-l59`), não no PR #329.

- **Alimento trocado conta como edição.** Trocar o alimento candidato, mantendo o peso, também marca o item como editado: passa a ter a evidência `user_informed` e perde a faixa da IA (`edited_item_evidence` e `edited_item_keeps_range` do dono). Voltar ao alimento original desfaz a marca (`photoMetaView` em `frontend/src/lib/nutrition-meal-form.ts`). Isto substitui a regra anterior, em que a troca de alimento não contava.
- **Soma divergente é da leitura.** O aviso `LIMIT_TOTAL_GRAMS_MISMATCH` mostra o que a leitura devolveu. O editor não o reavalia quando a pessoa muda pesos.
- **`uncertainty_none` sai do espelho.** A tela usa `Uncertainty.NONE.value` em vez de escrever o código `none` à mão.
- **A rota confere as dicas antes da imagem.** A ordem é permissão/alvo, dicas, imagem, slot e cota. Um erro de dica volta 400 só com o código, antes de ler a imagem e sem gastar cota.
- **Saneamento compartilhado com NFKC.** `prompt_user_data.sanitize_user_text` aplica NFKC e remove as categorias Unicode Cf, Zl e Zp. Efeito: símbolos como `m²` e `½` são normalizados e emojis compostos se desfazem no texto enviado à IA. Vale para todas as features que usam o helper.
- **Delimitador neutralizado em todas as formas.** O saneamento troca por `[delimitador removido]` a tag `dados_do_usuario` de abertura ou fechamento, inclusive com atributos, sem `>` final e autofechada. Vale para todas as features que usam o helper.

## Impacto em produção

Medido pelo Tech Lead em 2026-11-26, por SELECT no Aurora de produção em `ai_usage_log`, `feature='meal_photo'`, últimos 30 dias: 3 leituras, 1 usuário, todas `succeeded`, modelo Haiku (`claude-haiku-5-5`), média de 3.459 tokens de entrada e 2.106 de saída, cerca de 25,3 s por leitura, custo total US$ 0,0042. Primeira leitura em 2026-11-07, última em 2026-11-18, nenhuma falha (sem `reading_invalid`). Uso muito baixo (só o próprio usuário).

Sem migração e sem Terraform. O v2 muda a saída da IA (campos novos por item e `main_uncertainty`) e acrescenta tokens de saída por item, sem mudar macros (`meal_math`) nem o que é gravado. O aviso de soma divergente só existe quando a pessoa informa o peso total, então o impacto é zero para quem não usa os campos. Cliente antigo (sem os campos) segue o mesmo fluxo; o resultado do job ganha chaves novas (aditivo). A descrição não vai para log, `ai_usage_log` nem mensagem de erro. Acompanhar após a promoção, na mesma tabela, a taxa de falha por `reading_invalid` e os `tokens_out` por leitura.

## Verificação

Esta ADR não afirma resultado de execução. A integração só roda no CI. A tela no navegador e a chamada real à IA com foto não foram verificadas por esta ADR.

- **Evidência da decisão (PR #329):** os testes unitários de `api/tests/unit/test_nutrition_meal_photo*.py`, o teste de prosa do prompt em `api/privado/tests/unit/`, a guarda `api/tests/unit/test_guarda_fontes_da_verdade.py` e o teste `frontend/src/lib/nutrition-meal-photo-form.test.ts`.
- **Evidência dos ajustes (PR das issues #328/#330, branch `fix/p3-foto-prato-l59`):** os testes de `api/tests/unit/test_prompt_user_data.py` (NFKC, Cf/Zl/Zp e delimitador em todas as formas), a regra de alimento trocado em `frontend/src/lib/nutrition-meal-form.ts` (`photoMetaView`) e a ordem de validação da rota (dicas antes da imagem) em `api/tests/unit/test_nutrition_meal_photo_routes.py`.
