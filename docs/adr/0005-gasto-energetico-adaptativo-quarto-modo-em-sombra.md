# ADR 0005: Gasto energético adaptativo como 4º modo, em sombra primeiro

- **Status**: Aceita
- **Data**: 2026-11-05 (Lote A); atualizada em 2026-11-06 com o Lote C

## Contexto

Pedido do usuário (2026-11-05): refatorar a nutrição com base na spec `.omc/research/NUTRICAO-spec-2026-11-05.md`. A spec troca "TMB + calorias do relógio" por duas fases: bootstrap por fórmula e, depois, gasto adaptativo observado (ingestão média menos a energia da variação do peso), com as atividades do Intervals só como delta diário.

O que existe hoje (plano `.omc/plans/nutricao-adaptativo-2026-11-05.md`, §1):

- Três modos de gasto em `energy_expenditure.py` (`explicit_training`, `included_in_factor`, `auto_intervals`; mapa §89 e §90), gravados em `nutrition_plans.training_kcal_mode` (VARCHAR(24)).
- `adaptive_tdee.py`: gasto observado sem estado, nunca aplicado a meta (decisão D-J); alimenta o dashboard (`applied: false`), o check-in semanal e a sugestão por IA de planos publicados.
- Plano publicado é imutável; a meta do dia grava `inputs_json`, `inputs_hash` e `engine_version` e congela em D+2. Subir `ENGINE_VERSION` regravaria hoje e ontem de todos os usuários.
- O mapa proíbe a densidade 7.700 kcal/kg fixa; o déficit do plano já usa Hall/Forbes (`energy_density.py`).
- Regra permanente "Motores: fonte única da verdade" (`.claude/CLAUDE.md`, mapa `docs/motores/FONTES_DA_VERDADE.md`, guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`): cálculo novo mora no módulo dono; serviço, SQL, tela e prompt só consomem.
- Há risco de erro silencioso em produção: o estimador novo depende de dados que o produto ainda não valida (dia completo, atividade confirmada, kcal ativa × bruta) e de defaults de desenho sem fonte bibliográfica.

## Alternativas consideradas

1. **Substituir o cálculo dos três modos pelo adaptativo.** Rejeitada: mudaria a meta de planos publicados e de dias não congelados sem aval, e viola a trava de não tocar planos publicados.
2. **Evoluir `adaptive_tdee.py` em vez de criar módulos novos.** Rejeitada: o arquivo alimenta o check-in e a sugestão por IA; mudar sua semântica altera propostas em planos publicados. A regressão é importada, não copiada.
3. **Usar 7.700 kcal/kg como a spec.** Rejeitada (D1): contradiz o mapa e o déficit do plano; a diferença é de ~12% no termo de energia armazenada (~60 kcal/dia a −0,45 kg/semana).
4. **Ligar o modo adaptativo direto na meta já neste lote.** Rejeitada: sem validação em dados reais, e a decisão D2 (adaptativo governa a meta) e D5 (reprocessar o histórico ao publicar) pendem do usuário.
5. **Persistir o estado desde o início (tabela de estados).** Descartada no Lote C (D5: o estado é recalculado por `replay`, sem tabela); a forma de fold (`step`/`replay`) já garante que a sombra e o estado persistido deem o mesmo resultado.
6. **Núcleo puro + sombra somente leitura primeiro (escolhida).**

## Decisão

Introduzir o gasto adaptativo como um 4º modo (`adaptive_balance`) em etapas, começando por um núcleo PURO observado em sombra:

- Módulos donos novos em `api/app/domain/nutrition/`: `weight_trend.py` (EMA de meia-vida de 7 dias com aquecimento, outliers, suficiência), `adaptive_energy.py` (`ALGORITHM_VERSION = "adaptive-v1"`; status do dia, janela de 21 dias, observado, suavização 0,20 com passo de 75 kcal, confiança, bootstrap, ajuste diário; fold `step`/`replay`) e `activity_energy.py` (kcal ativa × bruta, deduplicação, dia confirmado). Mapa §93 a §96; chaves da guarda `peso_tendencia`, `gasto_adaptativo_sombra` e `atividades_normalizadas`, com allowlist vazia.
- Densidade energética do dono `energy_density` (D1); `unknown` do relógio tratada como bruta com marca de precisão reduzida (D4); fração mínima da TMB para o dia completo como heurística do motor (D3).
- **Lote C (2026-11-06): o 4º modo ponta a ponta.** `adaptive_balance` governa a meta SÓ nos planos que o escolhem (decisão D2, spec §1.1 e §14): `daily_target` troca o `gasto` que entra em `kcal_target` pela estimativa do dia (`adaptive_energy.estimate_for_day`: dia passado com o ajuste pela atividade que valeu, hoje e dia futuro só com a base); déficit, teto, piso, macros, ciclo e refeições seguem o mesmo código. Sem estado persistido (D5): o estado é recalculado por `replay` do histórico e o retrato do dia fica em `inputs_json.energy`; a tabela `nutrition_energy_states` NÃO é criada. Migração 106 aditiva em `nutrition_plans` (`energy_params_json`, `energy_model_version`, `published_preview_json`). O check-in semanal é informativo nesse modo. A D-J continua valendo nos três modos legados. Mapa §97.
- No Lote A o adaptativo só aparece como bloco informativo no dashboard (`adaptive_energy`, lido por `nutrition_energy_service.py`) e na prévia do plano; nenhuma meta usa o valor. D-J continua valendo.

## Trade-offs

- **Ganho:** nenhuma meta, hash ou `ENGINE_VERSION` muda; o estimador pode ser observado em dados reais antes de governar a meta; a forma de fold evita divergência futura entre sombra e estado persistido.
- **Custo:** o dashboard passa a ler peso, totais e atividades de ~90 dias por abertura (sem `raw_json`), até existir estado persistido.
- **Custo:** vários números são defaults de desenho a confirmar (fração 0,70 da TMB, plausibilidade, teto de 0,5 em suspensão, limiares de confiança, histórico de 90 dias, sequência máxima de 7 dias de descanso confirmado, dias exibidos no bloco do dashboard); ficam nomeados no dono e marcados no mapa.
- **Custo (Lote C):** hoje e ontem de um plano adaptativo são regravados enquanto o hash da estimativa muda; a leitura (`replay` de 90 dias) só ocorre em planos adaptativos.
- **Custo:** dois estimadores de gasto observado coexistem (`adaptive_tdee.py` sem estado e o novo) até o Lote C.

## Consequências

- Trava permanente: não alterar `adaptive_tdee.py`, o check-in, a sugestão por IA, `ENGINE_VERSION` nem planos publicados; chaves novas de `PlanSettings`/`DayContext` só entram no `to_dict` quando saem do padrão; consultas novas só quando o plano em jogo é adaptativo.
- `dashboard.py` não pode importar `adaptive_energy.py` (ciclo por `DayState`); o consumo é pelo serviço.
- Rollback do Lote A: remover o bloco do dashboard e a prévia; nada gravado.
- Lotes (plano §4):
  - **A (concluído):** "Meta de hoje" decomposta nos 3 modos com testes-ouro, núcleo puro e sombra no dashboard. Sem migração.
  - **C (concluído em 2026-11-06, mapa §97):** migração 106 (`energy_params_json`, `energy_model_version`, `published_preview_json`; a tabela `nutrition_energy_states` NÃO foi criada, D5) e 4º modo ponta a ponta (rascunho, prévia, publicação com snapshot, metas). D2 e D5 resolvidas (acima); D1 mantida. Rollback: o código antigo lê `adaptive_balance` como `explicit_training`; o código antigo o lê EM SILÊNCIO; voltar exige republicar um plano legado antes de reverter o backend. Promoção: `RUN_DB_MIGRATIONS=true` para a 105 e a 106 na mesma rodada, 106 antes do código.
  - **F (E1 + D1, 2026-11-06, mapa §98):** registro de execuções do sync (migração 107, tabela `intervals_sync_runs`, aditiva) e dia confirmado exato pelo motor (`activity_energy.day_confirmation_source`); ausência de execução deixa o dia pendente, nunca zero; execução ok sem atividade conta como descanso até 7 dias seguidos (indistinguível de descanso real), acima disso fica pendente, qualquer que seja a origem da confirmação. Diário explicável com a composição do gasto do dia a partir do gravado (`tdee_breakdown_from_stored`). Sem mudança nos modos legados nem no hash. Rollback: reverter o código (tabela fica sem uso).
  - **G (2026-11-06, mapa §99):** fechamento manual do dia ("Fechei o dia") que COMPLEMENTA a regra dos 70% da TMB (dia fechado conta como completo; reabrir volta à regra), correção manual de atividades (excluir duplicata/erro e reclassificar ativas × totais, aplicadas pelo motor antes de normalizar e deduplicar) e modo adaptativo no relatório Markdown (seção "7. Nutrição"). Migração 108 aditiva (`nutrition_day_closures`, `activity_energy_corrections`), tolerada se ausente. Sem mudança nos modos legados, no hash das metas nem em `ENGINE_VERSION`. Rollback: reverter o código (tabelas ficam sem uso).
  - **D:** explicabilidade (diário com atividades, `MyPlanView`, relatório, botão "fechei o dia").
  - **E:** robustez (excluir duplicata e reclassificar à mão, força sem vínculo, fuso do usuário, composição suavizada só no adaptativo).
- Rollback do Lote C: o código antigo lê `adaptive_balance` como `explicit_training`; REPUBLIQUE um plano legado ANTES de reverter o backend. As colunas da 106 ficam sem uso e as chaves novas do `inputs_json` são ignoradas pelo código antigo.
- Registrar nova ADR que substitua esta se a decisão D2 (adaptativo governando a meta) mudar o contrato do plano publicado.
