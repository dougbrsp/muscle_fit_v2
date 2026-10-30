# Provedor LLM por feature (OpenAI x Anthropic) - api/app/llm_anthropic.py.
#
# Publicado SO nas 3 Lambdas que ja chamam a OpenAI (api-core, ai-generators,
# workout-ai), junto com o ARN do secret musclefit/claude-api-key. engines,
# intervals-sync e migrations NAO recebem nada daqui (nem o ARN, nem a
# permissao de leitura).
#
# Padrao: toda feature em `openai`, sem modelo/esforco/fallback publicados -
# o codigo usa os padroes dele (DEFAULT_ANTHROPIC_MODEL/EFFORT) e, com
# LLM_PROVIDER_<FEATURE>=openai, nem le o resto. Ligar uma feature no Claude
# e um PR que poe a feature em `llm_provider_by_feature` (ver
# docs/CODEBASE_MAP.md, secao de LLM).
#
# A lista de features e a de `llm_anthropic.LLM_FEATURES` (rotulo `feature`
# do `llm.usage`); paridade em api/tests/unit/test_llm_provider_infra_paridade.py.

variable "llm_provider_by_feature" {
  description = "Provedor LLM por feature (openai | anthropic). Feature ausente = openai."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for p in values(var.llm_provider_by_feature) : contains(["openai", "anthropic"], p)])
    error_message = "llm_provider_by_feature aceita so openai ou anthropic."
  }
}

variable "anthropic_model_by_feature" {
  description = "Modelo Claude por feature (ANTHROPIC_MODEL_<FEATURE>). Ausente = padrao do codigo."
  type        = map(string)
  default     = {}
}

variable "anthropic_effort_by_feature" {
  description = "Esforco do Claude por feature (ANTHROPIC_EFFORT_<FEATURE>). Ausente = padrao do codigo."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for e in values(var.anthropic_effort_by_feature) : contains(["low", "medium", "high", "xhigh", "max"], e)])
    error_message = "anthropic_effort_by_feature aceita so low, medium, high, xhigh ou max."
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
    var.llm_fallback_provider == "" ? {} : { LLM_FALLBACK_PROVIDER = var.llm_fallback_provider },
    { ANTHROPIC_API_KEY_SECRET_ARN = aws_secretsmanager_secret.anthropic_api_key.arn },
  )
}
