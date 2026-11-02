"""Read once per process; substitute only explicit $variables, never JSON braces."""
from functools import lru_cache
from pathlib import Path
import re
from string import Template


@lru_cache(maxsize=None)
def _load_template(filename: str) -> Template:
    path = Path(__file__).parent / filename
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError as exc:
        raise FileNotFoundError(f'标书提示词文件不存在：{path}') from exc
    if not text.strip():
        raise ValueError(f'标书提示词文件为空：{path}')
    return Template(text)


def get_prompt(name: str, /, **kwargs) -> str:
    """Load `name.md`; missing variables and invalid names fail explicitly."""
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*(?:\.md)?', name):
        raise ValueError(f'无效的标书提示词名称：{name!r}')
    filename = name if name.endswith('.md') else name + '.md'
    template = _load_template(filename)
    try:
        return template.substitute(kwargs)
    except KeyError as exc:
        raise ValueError(f'标书提示词 {filename} 缺少变量：{exc.args[0]}') from exc


def clear_prompt_cache() -> None:
    """Clear process-local templates, e.g. in tests or a maintenance command."""
    _load_template.cache_clear()
