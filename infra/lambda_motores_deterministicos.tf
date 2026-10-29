# Ativacao dos motores deterministicos em producao (passo 2 e 3 da ordem de
# ativacao em docs/refatoracao-motores/ATIVACAO_MOTORES_DETERMINISTICOS.md).
#
# Este arquivo existe para que as DUAS flags temporarias tenham UM lugar so.
# Elas andam juntas por contrato de codigo, nao por convencao:
# `api/app/generation/__init__.py::deterministic_enabled()` exige
# `GENERATION_ENGINE=deterministic` E `PROGRESSION_ENGINE=v2`; qualquer
# combinacao pela metade cai no caminho legado e loga
# `generation.flag_mismatch`. Definir as duas no mesmo local torna impossivel
# ligar uma e esquecer a outra num `apply`.
#
# PRE-REQUISITO JA CUMPRIDO: o replay (passo 1) rodou em producao em
# 2026-09-18 pelo workflow "DB Migrations (manual)" (`action=replay`), e
# populou 106 trilhas (todas ACTIVE, todas com `working_load_kg`), 19 estados
# de grupo e 179 decisoes. Sem esse estado, ligar `GENERATION_ENGINE` faria o
# motor prescrever sobre trilhas vazias - o unico erro grave possivel aqui, e
# silencioso. NAO reordenar: as flags vem DEPOIS do replay.
#
# ONDE CADA FLAG E LIDA (mapa completo, por Lambda, em ordem de execucao):
#
#   musclefit-api-core      PRECISA DAS DUAS
#     - PROGRESSION_ENGINE: o hook do `finish`
#       (`app/workout_sessions.py:552` -> `progression_state.on_session_finish`,
#       gated por `shadow_enabled()`) e o `engine_mode` devolvido por
#       `GET /progression/tracks` (`app/progression.py:132`);
#     - as DUAS: o congelamento da prescricao
#       (`services._freeze_plan_prescriptions` -> `freeze_mode_a`/
#       `freeze_mode_b`, chamado por POST/PUT /workout-plans e
#       POST /workout-plans/{id}/refresh-prescription), que chama
#       `deterministic_enabled()`.
#
#   musclefit-plan-generator  PRECISA DAS DUAS
#     - a geracao da ficha roda AQUI, nao na api-core:
#       `app/lambda_plan_generator.handler` ->
#       `plan_generation_core.dispatch_plan_generation` ->
#       `generation/mode_a.py:465` / `generation/mode_b.py:282`, os dois
#       chamando `deterministic_enabled()`. O Modo A ainda le as trilhas
#       direto (`generation/mode_a.py:480`, `progression_state`), o que exige
#       `PROGRESSION_ENGINE=v2` no MESMO ambiente.
#
#   As outras cinco NAO recebem nenhuma das duas, porque nenhum caminho que
#   executam le as flags:
#
#   musclefit-engines       serve so analytics / dashboard_modules /
#                           dashboard_intelligence / engines_extra
#                           (`app/main_engines.py`). Importa `services`, mas
#                           nenhuma dessas rotas chama
#                           `_freeze_plan_prescriptions`, `mode_a`/`mode_b`
#                           nem `progression_state`.
#   musclefit-workout-ai    `app/lambda_workout_ai.handler` ->
#                           `workout_ai_generation_core` (avaliacao de serie,
#                           resumos). Nao importa `generation` nem
#                           `progression_state`.
#   musclefit-cardio-agent  cardio; compartilha `local.ai_generators_env` com
#                           a plan-generator, e por isso as flags entram na
#                           plan-generator por `merge()` e NAO no local
#                           compartilhado - dar a flag a quem nao a le seria
#                           ruido de configuracao.
#   (musclefit-reports, relatorio PDF, foi removida: o relatorio virou
#                           `GET /reports/markdown` na api-core.)
#   musclefit-migrations    `app/migrate.handler`, inclusive
#                           `action=replay-progression`. O replay e
#                           deliberadamente independente de flag
#                           (`app/replay_progression.py`): e bootstrap de
#                           dados e roda ANTES das flags. Dar a flag aqui
#                           sugeriria uma dependencia que nao existe.
#
# ROLLBACK (sem migracao, sem perda de dado): trocar os dois valores abaixo
# para `v1` / `legacy` - ou remover este arquivo e o `merge()` das duas
# Lambdas - e reaplicar pelo pipeline de deploy. Os motores legados
# (`generation/mode_a_legacy.py`, `generation/mode_b_legacy.py`) voltam na
# hora; `progression_tracks` / `progression_group_states` /
# `progression_decisions` continuam populadas e as fichas ja congeladas
# continuam legiveis (o contrato do snapshot e o mesmo, so a
# `engine_version` muda).
locals {
  # PROGRESSION_ENGINE=v2: o `finish` mantem as trilhas vivas E elas podem
  # alimentar a prescricao (`shadow` gravaria sem prescrever, e por isso nao
  # serve aqui).
  # GENERATION_ENGINE=deterministic: Modo A (`percent_budget`) e Modo B
  # (`equipment_series`) passam a gerar e congelar pelos motores novos.
  deterministic_engines_env = {
    PROGRESSION_ENGINE = "v2"
    GENERATION_ENGINE  = "deterministic"
  }
}
