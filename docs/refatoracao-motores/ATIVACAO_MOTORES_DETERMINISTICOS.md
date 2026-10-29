# Ativação dos motores determinísticos de geração (PR-12 e PR-13)

> Este documento nasceu no PR-12 como `ATIVACAO_MODO_B.md`. O PR-13 trouxe o
> Modo A para a **mesma flag** e para a **mesma ordem de ativação**, então ele
> foi estendido e renomeado em vez de duplicado: há um único procedimento, e
> ele vale para os dois modos.

Este documento existe por um motivo só: **a ordem de ativação não é livre**.
Ligar a geração antes de popular as trilhas da progressão faz o motor
prescrever sobre estado vazio — toda trilha em `CALIBRATION`, carga `null`,
todo grupo em F1 (6–12 reps sem carga sugerida) — e isso não dá erro nenhum.
É a única falha grave possível aqui, e é silenciosa.

## As duas flags

| flag | valores | default | o que faz |
|---|---|---|---|
| `PROGRESSION_ENGINE` | `v1` \| `shadow` \| `v2` | `v1` | `shadow`: o `finish` avalia a sessão e grava `progression_group_states` / `progression_tracks` / `progression_decisions`, sem afetar nada visível. `v2`: o mesmo **e** essas trilhas podem alimentar a prescrição. |
| `GENERATION_ENGINE` | `legacy` \| `deterministic` | `legacy` | `deterministic`: o Modo B (`volume_mode='equipment_series'`, §6.2) **e o Modo A** (`volume_mode='percent_budget'`, §6.1) passam a gerar e a congelar a prescrição pelos motores novos. |

As duas são temporárias e saem no PR-20 (§12).

**Uma flag, os dois modos.** O PR-13 **não** criou flag nova: `mode_a.py` usa a
mesma `generation.deterministic_enabled()` do `mode_b.py`. Ligar a flag troca os
dois modos de uma vez — não existe "ligar só o Modo A". O que decide qual motor
roda para uma ficha é o `volume_mode` do modelo, como sempre foi.

**`generation.deterministic_enabled()` exige as duas.** Com
`GENERATION_ENGINE=deterministic` e `PROGRESSION_ENGINE` em `v1`/`shadow`, o
motor **não** assume: o caminho legado continua valendo e um
`generation.flag_mismatch` vai para o CloudWatch. A configuração pela metade é
visível, nunca silenciosa. `shadow` não basta de propósito: ela *grava* as
trilhas, mas o contrato da §5.11 diz que só `v2` as usa para prescrever.

**Onde as duas ficam definidas (desde o PR-14).** Em `infra/`, num `locals`
único: `infra/lambda_motores_deterministicos.tf`. Elas entram por `merge()` em
**duas** Lambdas — `musclefit-api-core` e `musclefit-plan-generator` — e em mais
nenhuma. O mapa completo, com o motivo de cada Lambda receber ou não, está no
comentário do próprio arquivo e é verificado no CI por
`tests/unit/test_pr12_modo_b_flags_e_autoridade.py`:

| Lambda | flags | por quê |
|---|---|---|
| `musclefit-api-core` | as duas | hook do `finish` (`workout_sessions.py` → `progression_state.on_session_finish`), `engine_mode` do `GET /progression/tracks` e o congelamento (`services._freeze_plan_prescriptions` → `freeze_mode_a`/`freeze_mode_b`, que chama `deterministic_enabled()`) |
| `musclefit-plan-generator` | as duas | a geração roda aqui: `dispatch_plan_generation` → `generation/mode_a.py` / `mode_b.py`, os dois chamando `deterministic_enabled()`; o Modo A ainda lê as trilhas direto |
| `musclefit-engines` | nenhuma | serve só analytics / dashboard_modules / dashboard_intelligence / engines_extra; nenhuma dessas rotas chama congelamento, geração ou `progression_state` |
| `musclefit-workout-ai` | nenhuma | avaliação de série e resumos; não importa `generation` nem `progression_state` |
| `musclefit-cardio-agent` | nenhuma | cardio. Compartilha `local.ai_generators_env` com a plan-generator — é por isso que as flags entram na plan-generator por `merge()`, e não no local compartilhado |
| `musclefit-migrations` | nenhuma | roda o replay, que é **independente de flag** de propósito (`app/replay_progression.py`) |

Até o PR-13 a garantia era a oposta — as flags **não** podiam estar em `infra/`,
e havia teste para isso. O teste não foi apagado: foi invertido, e continua
sendo o que impede a configuração pela metade (uma Lambda que lê a flag e não a
recebe) de passar no CI.

## Ordem de ativação em produção

> **Status em produção (2026-09-18):** passo 1 **concluído**, passo 2 e 3
> entregues **juntos** pelo PR-14 (este). Ver "O que já rodou em produção"
> abaixo.

1. **Replay.** Popular as trilhas a partir do histórico real, antes de
   qualquer flag. Em produção isso roda **pelo pipeline**, nunca à mão:
   workflow **DB Migrations (manual)** (`db-migrations.yml`, branch `main`),
   primeiro com `action=replay-dry-run` e, depois de conferir o resumo, com
   `action=replay`.

   ```bash
   # equivalente local do que o workflow dispara (requer credenciais OIDC):
   scripts/invoke-migrations.sh replay-dry-run
   scripts/invoke-migrations.sh replay
   ```

   As duas invocam a Lambda `musclefit-migrations` com
   `{"action": "replay-progression", "dry_run": true|false}`
   (`api/app/replay_progression.py`, chamado por `api/app/migrate.py`). Como
   qualquer ação dessa Lambda, ela roda a imagem publicada pelo **último deploy
   de backend** — o replay só existe em produção depois de um deploy que carregue
   este código.

   O que conferir no resumo do workflow: usuários, sessões lidas, trilhas,
   grupos e decisões; as contagens `antes (t/g/d)`, que em produção começam em
   `0/0/0`; e o `fingerprint` por usuário.

   - `replay-dry-run` calcula tudo sobre o banco real e faz `rollback`: **não
     grava nada**.
   - O replay é **idempotente** (apaga e reconstrói o estado do usuário) e não
     depende de flag nenhuma. Rodar duas vezes repete o mesmo `fingerprint` e
     as mesmas contagens, sem duplicar linha.
   - Tudo em **uma transação**: falha no meio (inclusive timeout da Lambda) não
     deixa escrita parcial.

   Em DEV, a mesma lógica pela linha de comando:

   ```bash
   python -m scripts.rebuild_progression_tracks --all            # dentro de api/
   python -m scripts.rebuild_progression_tracks --all --dry-run
   ```

2. **`PROGRESSION_ENGINE=v2`.** A partir daqui o `finish` mantém as trilhas
   vivas. Deixar rodando o tempo necessário para confirmar que as decisões
   estão sendo gravadas (`progression_decisions`) sem erro
   (`progression.shadow_failed` deve ficar em zero).

3. **`GENERATION_ENGINE=deterministic`.** Só então os Modos A e B passam a
   prescrever pela progressão v2.

Inverter 1 e 3 é o erro a evitar. Inverter 2 e 3 não quebra nada — a segunda
flag sozinha não liga o motor (é justamente o que `deterministic_enabled()`
impede).

## O que já rodou em produção

### Passo 1 — replay: **concluído em 2026-09-18**

Executado pelo pipeline (workflow **DB Migrations (manual)**, `action=replay`),
nunca à mão. Estado conferido depois, direto na Aurora:

| medida | resultado |
|---|---|
| `progression_tracks` | **106**, todas `load_status='ACTIVE'`, todas com `working_load_kg` |
| `progression_group_states` | **19** — 6 em F1, 12 em F2, 1 em F3 |
| `progression_decisions` | **179** — 161 `HOLD`, 14 `SET_REP_RANGE_10_12`, 3 `DECREASE_LOAD`, 1 `INCREASE_LOAD` |
| pendências ativas | 2 |

Nenhuma trilha em `CALIBRATION` e nenhuma carga `null`: é exatamente o
pré-requisito que o passo 3 exige. **Não rodar o replay de novo** — ele é
idempotente, mas não há motivo, e o estado já está conferido.

### Passos 2 e 3 — as flags: PR-14

Entregues no mesmo PR, e por dois motivos. Primeiro, o escalonamento entre 2 e 3
existia para observar as decisões sendo gravadas antes de prescrever com elas —
e o replay já produziu esse corpo de decisões (179), verificável sem esperar
treino novo. Segundo, ligar só a 2 e deixar a 3 para depois manteria a geração
no motor legado com LLM enquanto as trilhas já estariam vivas: um estado
intermediário sem ganho, que só duplica deploys. `deterministic_enabled()`
continua sendo a rede: se por qualquer razão só uma das duas chegar ao ambiente,
o motor **não** assume e um `generation.flag_mismatch` aparece no CloudWatch.

### Como verificar que ficou no ar

Depois do deploy (o `apply` é do pipeline, nunca manual):

```bash
# 1. as variáveis chegaram às duas Lambdas - e a nenhuma outra
for f in musclefit-api-core musclefit-plan-generator musclefit-engines \
         musclefit-workout-ai musclefit-cardio-agent \n         musclefit-migrations; do
  echo "== $f"
  aws lambda get-function-configuration --function-name $f \
    --profile musclefit --region sa-east-1 \
    --query 'Environment.Variables.{P:PROGRESSION_ENGINE,G:GENERATION_ENGINE}'
done
# esperado: v2/deterministic nas duas primeiras, null/null nas cinco restantes
```

```bash
# 2. nenhuma configuração pela metade nos logs
aws logs filter-log-events --log-group-name /aws/lambda/musclefit-plan-generator \
  --filter-pattern '"generation.flag_mismatch"' --profile musclefit --region sa-east-1
# esperado: vazio
```

3. No primeiro treino finalizado depois do deploy: `progression.shadow_evaluated`
   no log da `musclefit-api-core` (e `progression.shadow_failed` em zero), com
   linha nova em `progression_decisions`.
4. Na primeira ficha gerada depois do deploy: `generation.completed` com
   `"llm_used": false` no log da `musclefit-plan-generator`, `engine_version`
   `mode-a-deterministic-v1` / `mode-b-deterministic-v1` e
   `raw_payload.generation_audit` preenchido na ficha.

## Rollback

Trivial e sem migração. Em `infra/lambda_motores_deterministicos.tf`, um lugar
só:

```hcl
locals {
  deterministic_engines_env = {
    PROGRESSION_ENGINE = "v1"           # era "v2"
    GENERATION_ENGINE  = "legacy"       # era "deterministic"
  }
}
```

— ou reverter o PR-14, que remove o arquivo e os dois `merge()`. Nos dois casos o
`apply` é do pipeline de deploy, nunca manual. **Sem migração e sem perda de
dado:** `progression_tracks`, `progression_group_states` e
`progression_decisions` continuam populadas (106/19/179), e voltar a ligar é só
desfazer o rollback — não precisa de replay novo.

`GENERATION_ENGINE=legacy` (ou remover a variável) devolve os motores anteriores
na hora, sem tocar em dado nenhum:

| modo | motor legado que volta | `engine_version` das fichas novas |
|---|---|---|
| B (`equipment_series`) | `generation/mode_b_legacy.py` | `legacy-generation-v1` |
| A (`percent_budget`) | `generation/mode_a_legacy.py` (o antigo `workout_plan_templates_engine_v2.py`, com LLM + RAG + reparo) | `v2` no rascunho, `legacy-generation-v1` no congelamento |

As fichas já congeladas pelos motores novos continuam legíveis — o contrato do
snapshot é o mesmo (§6.3), só a `engine_version` muda
(`mode-a-deterministic-v1` / `mode-b-deterministic-v1`).

Os dois módulos legados estão em quarentena: nenhuma linha deles mudou, e é por
isso que "com a flag desligada nada muda" é verificável. Eles saem no PR-19/20,
levando com eles `template_ai_selector_v2`, o RAG da geração e o
reparo/split/piso — que existem só para consertar saída de LLM.

## Antes de ligar: modelos a revisar

O relatório da §12 ("templates `equipment_series` com técnica/RPE
incompatível") está exposto por modelo em `safety_warnings`, no `GET
/workout-plan-templates/{id}` e nas respostas de POST/PUT. O builder mostra o
aviso no próprio item.

Ligar o motor **não** quebra esses modelos: a geração faz o clamp do RPE e
remove a técnica inválida, com aviso na ficha (`MODEL_TECHNIQUE_INVALID`). A
D14 só impede **salvar** um item novo ou alterado incompatível.

## Antes de ligar: o que muda no Modo A (PR-13)

O Modo A muda mais de comportamento que o Modo B, porque o motor antigo dele
pedia a ficha para uma LLM. O que esperar depois de ligar a flag:

- **A ficha fica estável.** Mesma entrada, mesma ficha, byte a byte. Não há mais
  "gerou diferente porque a IA respondeu diferente", nem fallback por falha de
  IA.
- **Séries passam a ser da progressão**, não do orçamento. O orçamento de
  ativação decide **quantos exercícios** o grupo recebe (máximo 3); as séries
  vêm da trilha (até 4). A única exceção é reduzir séries para não estourar o
  teto de um grupo secundário — que é limite, não prescrição, e fica registrado
  em `fallbacks[]`.
- **Subativação é reportada, não compensada.** Se o catálogo da academia, o
  limite de 3 exercícios ou o teto de outro grupo não permitirem alcançar o
  alvo, o que falta aparece como `deficit` na auditoria e como aviso na ficha.
  Técnica avançada e pico de contração **não** entram como compensação (D7).
- **A escolha do equipamento passa a respeitar estrelas e recência do usuário**
  (o ranking único do PR-08) — e a segurança filtra antes disso. Um par 5
  estrelas incompatível com a técnica exigida pela progressão é excluído, com o
  motivo na auditoria (`generation_audit.selection[].reason`).
- **Grupo sem equipamento na academia fica sem exercício**, com
  `fallbacks[].reason=NO_EQUIPMENT_FOR_GROUP`. O motor não inventa exercício
  para preencher.
- **Modelos com mais de 7 slots passam a ser projetados inteiros** (bug G7,
  corrigido neste PR): a janela de ativação tem `max(7, ciclo)` dias e a meta
  semanal é comparada escalada (`meta × ciclo/7`). Antes, o preview do builder
  perdia os slots do 8º em diante.

O que **não** muda: o contrato do rascunho (as mesmas chaves que o pipeline de
`POST /workout-plans/generate` já consumia), o congelamento do PR-11 e a leitura
da ficha.

## DEV

O `docker-compose.yml` já liga as duas (`PROGRESSION_ENGINE: v2`,
`GENERATION_ENGINE: deterministic`) — desde o PR-14 isso é **paridade** com
produção, não divergência. Em DEV o replay também deve ser rodado ao menos uma
vez, pelo mesmo motivo:

```bash
docker compose exec api python /app/scripts/rebuild_progression_tracks.py --all
```
