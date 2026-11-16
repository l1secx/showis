from dataclasses import dataclass


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
}


def get_preset(name: str) -> LLMPreset:
    return PRESETS.get(name, PRESETS["opencode-go"])
