from pathlib import Path

from jinja2 import Environment, FileSystemLoader

PROMPTS_DIR = Path(__file__).resolve().parent

env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_prompt(prompt_name: str, **kwargs) -> str:
    template_file = f"{prompt_name}.jinja"
    template = env.get_template(template_file)
    return template.render(**kwargs)


__all__ = ["render_prompt", "PROMPTS_DIR"]
