# Provedor LLM por feature (OpenAI x Anthropic) - api/app/llm_anthropic.py.
#
# Publicado SO nas Lambdas que ja chamam a OpenAI (api-core, workout-ai e as
# duas de local.ai_generators_env: plan-generator e cardio-agent), junto com o
# ARN do secret musclefit/claude-api-key. engines, intervals-sync e migrations
# NAO recebem nada daqui (nem o ARN, nem a permissao de leitura).
#
# Feature fora de `llm_provider_by_feature` fica em `openai`, sem modelo,
# esforco nem fallback publicados (com LLM_PROVIDER_<FEATURE>=openai o codigo
# nem le o resto).
#
# Virada de 2026-10-31 (decisao do usuario, eval em
# docs/ia/EVAL_ANTHROPIC_2026-10-30.md): 6 features no claude-haiku-5-5, com
# o esforco que venceu no eval e fallback POR FEATURE para a OpenAI.
# periodization, set_evaluation, workouts_analysis, exercise_summary e
# workout_plan seguem na OpenAI (sem env de modelo/esforco/fallback).
# periodization foi reavaliada sem raciocinio (rodada 2 de
# docs/ia/EVAL_PERIODIZACAO_2026-10-31.md): 50-54% de aprovacao, nao liga.
#
# REVERTER uma feature: apagar a linha dela em `llm_provider_by_feature` (ou
# trocar para "openai"). As linhas de modelo/esforco/fallback podem ficar: sem
# `anthropic`, o codigo nao as le. O apply so muda env das Lambdas.
#
# A lista de features e a de `llm_anthropic.LLM_FEATURES` (rotulo `feature`
# do `llm.usage`); paridade em api/tests/unit/test_llm_provider_infra_paridade.py.

variable "llm_provider_by_feature" {
  description = "Provedor LLM por feature (openai | anthropic). Feature ausente = openai."
  type        = map(string)
  default = {
    cardio_agent         = "anthropic"
    label_photo          = "anthropic"
    insights             = "anthropic"
    meal_parse           = "anthropic"
    exercise_description = "anthropic"
    workout_summary      = "anthropic"
  }

  validation {
    condition     = alltrue([for p in values(var.llm_provider_by_feature) : contains(["openai", "anthropic"], p)])
    error_message = "llm_provider_by_feature aceita so openai ou anthropic."
  }
}

variable "anthropic_model_by_feature" {
  description = "Modelo Claude por feature (ANTHROPIC_MODEL_<FEATURE>). Ausente = padrao do codigo."
  type        = map(string)
  default = {
    cardio_agent         = "claude-haiku-5-5"
    label_photo          = "claude-haiku-5-5"
    insights             = "claude-haiku-5-5"
    meal_parse           = "claude-haiku-5-5"
    exercise_description = "claude-haiku-5-5"
    workout_summary      = "claude-haiku-5-5"
  }
}

# Esforco vencedor no eval (braco C = Haiku medium; workout_summary no braco B
# = Haiku low, o padrao das features leves).
variable "anthropic_effort_by_feature" {
  description = "Esforco do Claude por feature (ANTHROPIC_EFFORT_<FEATURE>). Ausente = padrao do codigo."
  type        = map(string)
  default = {
    cardio_agent         = "medium"
    label_photo          = "medium"
    insights             = "medium"
    meal_parse           = "medium"
    exercise_description = "medium"
    workout_summary      = "low"
  }

  validation {
    condition     = alltrue([for e in values(var.anthropic_effort_by_feature) : contains(["low", "medium", "high", "xhigh", "max"], e)])
    error_message = "anthropic_effort_by_feature aceita so low, medium, high, xhigh ou max."
  }
}

# Raciocinio por feature (ANTHROPIC_THINKING_<FEATURE>): adaptive (padrao do
# codigo) ou disabled. disabled so com esforco low/medium/high (a API responde
# 400 com xhigh/max; o codigo falha antes da rede com ANTHROPIC_CONFIG_INVALID
# e a combinacao publicada e travada em teste). Feature ausente = adaptive, o
# corpo de sempre.
variable "anthropic_thinking_by_feature" {
  description = "Raciocinio do Claude por feature (ANTHROPIC_THINKING_<FEATURE>): adaptive ou disabled. Ausente = adaptive."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for t in values(var.anthropic_thinking_by_feature) : contains(["adaptive", "disabled"], t)])
    error_message = "anthropic_thinking_by_feature aceita so adaptive ou disabled."
  }
}

variable "llm_fallback_provider" {
  description = "Fallback global quando o Claude falha (credito, chave, indisponibilidade, recusa): \"\" (desligado) ou openai."
  type        = string
  default     = ""

  validation {
    condition     = contains(["", "openai"], var.llm_fallback_provider)
    error_message = "llm_fallback_provider aceita so \"\" ou openai."
  }
}

# Fallback POR FEATURE (LLM_FALLBACK_PROVIDER_<FEATURE>), preferido ao global:
# so as features ligadas no Claude recebem a env, e as que seguem na OpenAI
# ficam sem nada (o global continua "").
variable "llm_fallback_provider_by_feature" {
  description = "Fallback por feature quando o Claude falha: openai. Feature ausente = o global (llm_fallback_provider)."
  type        = map(string)
  default = {
    cardio_agent         = "openai"
    label_photo          = "openai"
    insights             = "openai"
    meal_parse           = "openai"
    exercise_description = "openai"
    workout_summary      = "openai"
  }

  validation {
    condition     = alltrue([for p in values(var.llm_fallback_provider_by_feature) : p == "openai"])
    error_message = "llm_fallback_provider_by_feature aceita so openai."
  }
}

locals {
  llm_features = [
    "set_evaluation",
    "exercise_summary",
    "workout_summary",
    "exercise_description",
    "insights",
    "workouts_analysis",
    "cardio_agent",
    "periodization",
    "meal_parse",
    "label_photo",
    "workout_plan",
  ]

  llm_provider_env = merge(
    { for f in local.llm_features : "LLM_PROVIDER_${upper(f)}" => lookup(var.llm_provider_by_feature, f, "openai") },
    { for f, m in var.anthropic_model_by_feature : "ANTHROPIC_MODEL_${upper(f)}" => m if contains(local.llm_features, f) },
    { for f, e in var.anthropic_effort_by_feature : "ANTHROPIC_EFFORT_${upper(f)}" => e if contains(local.llm_features, f) },
    { for f, t in var.anthropic_thinking_by_feature : "ANTHROPIC_THINKING_${upper(f)}" => t if contains(local.llm_features, f) },
    { for f, p in var.llm_fallback_provider_by_feature : "LLM_FALLBACK_PROVIDER_${upper(f)}" => p if contains(local.llm_features, f) },
    var.llm_fallback_provider == "" ? {} : { LLM_FALLBACK_PROVIDER = var.llm_fallback_provider },
    { ANTHROPIC_API_KEY_SECRET_ARN = aws_secretsmanager_secret.anthropic_api_key.arn },
  )
}
