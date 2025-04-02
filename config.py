# LLM配置
CURRENT_MODEL = "openai"  # 当前使用的模型

# 可用模型配置
MODEL_CONFIGS = {
    "deepseek": {
        "id": "deepseek-chat",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "api_key": "sk-XXX"
    },
    "openai": {
        "id": "gpt-4o",
        "url": "https://agent.aigc369.com/v1/chat/completions",
        "api_key": "sk-XXX"
    }
    # 可以添加更多模型配置
} 

# GitHub配置
GITHUB_TOKEN = "ghp_XXX"

# 并发和重试配置
MAX_CONCURRENT = 50
MAX_RETRIES = 3
RETRY_DELAY = 5  # 重试等待秒数 