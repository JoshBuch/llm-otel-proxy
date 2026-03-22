from __future__ import annotations

# GenAI semantic conventions (required span attributes)
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

# HTTP context
SERVER_ADDRESS = "server.address"
HTTP_RESPONSE_STATUS_CODE = "http.response.status_code"

# Error
ERROR_TYPE = "error.type"

# Optional span events (when CAPTURE_PROMPTS=true)
GEN_AI_CONTENT_PROMPT = "gen_ai.content.prompt"
GEN_AI_CONTENT_COMPLETION = "gen_ai.content.completion"
GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"

# Model fallback sentinel
UNKNOWN_MODEL = "unknown"

# Operation name
OPERATION_CHAT = "chat"
