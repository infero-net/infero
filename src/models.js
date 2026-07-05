window.MODELS = {
  "providers": {
    "infero":     { "name": "infero.net", "format": null, "endpoint": null, "cacheEndpoint": "/api/cache" },
    "google":     { "name": "Google AI", "format": "gemini", "endpoint": "https://generativelanguage.googleapis.com/v1beta/", "cacheEndpoint": "https://generativelanguage.googleapis.com/v1beta/cachedContents" },
    "openai":     { "name": "OpenAI", "format": "openai", "endpoint": "https://api.openai.com/v1/chat/completions" },
    "anthropic":  { "name": "Anthropic", "format": "anthropic", "endpoint": "https://api.anthropic.com/v1/messages" },
    "deepseek":   { "name": "DeepSeek", "format": "openai", "endpoint": "https://api.deepseek.com/v1/chat/completions" },
    "openrouter": { "name": "OpenRouter", "format": "openai", "endpoint": "https://openrouter.ai/api/v1/chat/completions" },
    "dashscope":  { "name": "Alibaba DashScope", "format": "openai", "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions" },
    "moonshot":   { "name": "Moonshot (Kimi)", "format": "openai", "endpoint": "https://api.moonshot.cn/v1/chat/completions" },
    "minimax":    { "name": "MiniMax", "format": "openai", "endpoint": "https://api.minimax.chat/v1/chat/completions" },
    "zhipu":      { "name": "Zhipu AI (GLM)", "format": "openai", "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions" }
  },
  "models": [
    { "name": "gemini-3.5-flash", "format": "gemini", "from": {
      "infero": "gemini-3.5-flash", "google": "gemini-3.5-flash", "openrouter": "google/gemini-3.5-flash" }},
    { "name": "gemini-3.1-pro", "format": "gemini", "from": {
      "infero": "gemini-3.1-pro-preview", "google": "gemini-3.1-pro-preview", "openrouter": "google/gemini-3.1-pro-preview" }},
    { "name": "gemini-3.1-flash-lite", "format": "gemini", "from": {
      "infero": "gemini-3.1-flash-lite-preview", "google": "gemini-3.1-flash-lite-preview", "openrouter": "google/gemini-3.1-flash-lite-preview" }},
    { "name": "gemini-3-flash", "format": "gemini", "from": {
      "infero": "gemini-3-flash-preview", "google": "gemini-3-flash-preview", "openrouter": "google/gemini-3-flash-preview" }},
    { "name": "gpt-5.4", "format": "openai", "stop": false, "from": {
      "openai": "gpt-5.4", "openrouter": "openai/gpt-5.4" }},
    { "name": "gpt-5.4-mini", "format": "openai", "stop": false, "from": {
      "openai": "gpt-5.4-mini", "openrouter": "openai/gpt-5.4-mini" }},
    { "name": "gpt-5.4-nano", "format": "openai", "stop": false, "from": {
      "openai": "gpt-5.4-nano", "openrouter": "openai/gpt-5.4-nano" }},
    { "name": "claude-fable-5", "format": "anthropic", "maxContext": 1000000, "compress": { "at": 128000, "head": 0.1, "tail": 0.6 }, "thinkingMode": "adaptive", "from": {
      "anthropic": "claude-fable-5", "openrouter": "anthropic/claude-fable-5" }},
    { "name": "claude-opus-4.8", "format": "anthropic", "maxContext": 1000000, "compress": { "at": 128000, "head": 0.1, "tail": 0.6 }, "thinkingMode": "adaptive", "from": {
      "anthropic": "claude-opus-4-8", "openrouter": "anthropic/claude-opus-4.8" }},
    { "name": "claude-opus-4.7", "format": "anthropic", "maxContext": 1000000, "compress": { "at": 128000, "head": 0.1, "tail": 0.6 }, "thinkingMode": "adaptive", "from": {
      "anthropic": "claude-opus-4-7", "openrouter": "anthropic/claude-opus-4.7" }},
    { "name": "claude-opus-4.6", "format": "anthropic", "maxContext": 128000, "compress": { "at": 128000, "head": 0.1, "tail": 0.6 }, "from": {
      "anthropic": "claude-opus-4-6", "openrouter": "anthropic/claude-opus-4.6" }},
    { "name": "claude-sonnet-4.6", "format": "anthropic", "from": {
      "anthropic": "claude-sonnet-4-6", "openrouter": "anthropic/claude-sonnet-4.6" }},
    { "name": "claude-haiku-4.5", "format": "anthropic", "from": {
      "anthropic": "claude-haiku-4-5-20251001", "openrouter": "anthropic/claude-haiku-4.5" }},
    { "name": "deepseek-v3.2", "format": "openai", "vision": false, "maxContext": 128000, "from": {
      "deepseek": "deepseek-chat", "openrouter": "deepseek/deepseek-v3.2" }},
    { "name": "deepseek-v4-pro", "format": "openai", "vision": false, "maxContext": 1000000, "from": {
      "deepseek": "deepseek-v4-pro", "openrouter": "deepseek/deepseek-v4-pro" }},
    { "name": "deepseek-v4-flash", "format": "openai", "vision": false, "maxContext": 1000000, "from": {
      "deepseek": "deepseek-v4-flash", "openrouter": "deepseek/deepseek-v4-flash" }},
    { "name": "qwen3.5-plus", "format": "openai", "maxContext": 128000, "vision": true, "from": {
      "dashscope": "qwen3.5-plus" }},
    { "name": "qwen3.6-plus", "format": "openai", "maxContext": 128000, "vision": true, "from": {
      "dashscope": "qwen3.6-plus" }},
    { "name": "glm-5", "format": "openai", "maxContext": 131072, "vision": true, "from": {
      "zhipu": "glm-5", "openrouter": "z-ai/glm-5" }},
    { "name": "glm-5.1", "format": "openai", "maxContext": 131072, "vision": true, "from": {
      "dashscope": "glm-5.1", "openrouter": "z-ai/glm-5.1" }},
    { "name": "glm-5.2", "format": "openai", "maxContext": 131072, "vision": false, "from": {
      "zhipu": "glm-5.2", "dashscope": "glm-5.2", "openrouter": "z-ai/glm-5.2" }},
    { "name": "kimi-k2.5", "format": "openai", "maxContext": 256000, "vision": true, "from": {
      "moonshot": "kimi-k2.5", "openrouter": "moonshotai/kimi-k2.5", "dashscope": "kimi-k2.5" }},
    { "name": "MiniMax-M2.5", "format": "openai", "maxContext": 256000, "vision": false, "from": {
      "minimax": "MiniMax-M2.5", "dashscope": "MiniMax-M2.5", "openrouter": "minimax/minimax-m2.5" }},
    { "name": "MiniMax-M2.7", "format": "openai", "maxContext": 256000, "vision": false, "from": {
      "minimax": "MiniMax-M2.7", "dashscope": "MiniMax/MiniMax-M2.7", "openrouter": "minimax/minimax-m2.7" }}
  ]
}
;
