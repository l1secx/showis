from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class LLMPreset:
    name: str
    base_url: str
    default_model: str
    description: str


PRESETS: dict[str, LLMPreset] = {
    "opencode-go": LLMPreset(
        name="opencode-go",
        base_url="https://opencode.ai/inference/go/openai/v1",
        default_model="minimax-m3.1-flash",
        description="OpenCode Go ($10/mo) OpenAI-compatible fast inference",
    ),
    "opencode-zen": LLMPreset(
        name="opencode-zen",
        base_url="https://opencode.ai/inference/openai/v1",
        default_model="gpt-4o-mini",
        description="OpenCode Zen standard inference",
    ),
    "openai": LLMPreset(
        name="openai",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        description="Official OpenAI API",
    ),
    "openrouter": LLMPreset(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4o-mini",
        description="OpenRouter model gateway (OpenAI-compatible)",
    ),
    "anthropic": LLMPreset(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-opus-5-5",
        description="Anthropic Messages API (native tool use, thinking, prompt caching)",
    ),
    "groq": LLMPreset(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        description="Groq fast inference (OpenAI-compatible)",
    ),
}


def get_preset(name: str) -> LLMPreset:
    return PRESETS.get(name, PRESETS["opencode-go"])


#: Model rolleri: planlama/muhakeme güçlü modelde, çıkarım/özet ucuz modelde.
ROLES: tuple[str, ...] = ("planner", "reasoner", "extractor", "summarizer")

#: Rol başına varsayılan zaman aşımı (sn). Reasoning modelleri uzun sürer.
ROLE_TIMEOUTS: dict[str, float] = {
    "planner": 120.0,
    "reasoner": 180.0,
    "extractor": 30.0,
    "summarizer": 60.0,
}

#: Anthropic preset'i için önerilen rol → model eşlemesi.
ANTHROPIC_DEFAULT_ROLES: dict[str, str] = {
    "planner": "claude-opus-5-5",
    "reasoner": "claude-opus-5-5",
    "extractor": "claude-haiku-5-5",
    "summarizer": "claude-sonnet-5-5",
}


def detect_api_style(base_url: str) -> str:
    """`anthropic` (native /messages) veya `openai` (chat/completions)."""
    host = urlparse(str(base_url or "")).hostname or ""
    return "anthropic" if host.lower() == "api.anthropic.com" else "openai"


def model_capabilities(model: str, api_style: str) -> dict[str, bool]:
    """Model kimliğinden tahmini yetenekler; bilinmeyen modelde tools açık sayılır
    ve çağıran, hata alırsa JSON-şema yoluna düşer."""
    name = (model or "").lower()
    if api_style == "anthropic":
        return {"tools": True, "stream": True, "thinking": "claude" in name}
    reasoning = any(tag in name for tag in ("o1", "o3", "o4", "gpt-5", "reason", "thinking", "r1"))
    no_tools = any(tag in name for tag in ("embed", "whisper"))
    return {"tools": not no_tools, "stream": True, "thinking": reasoning}
