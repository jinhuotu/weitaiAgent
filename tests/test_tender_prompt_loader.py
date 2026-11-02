import ast
from pathlib import Path

import pytest

from api.services.tenders.prompts import get_prompt
from api.services.tenders.prompts import loader


@pytest.fixture
def folder(tmp_path, monkeypatch):
    loader.clear_prompt_cache()
    monkeypatch.setattr(loader, '__file__', str(tmp_path / 'loader.py'))
    yield tmp_path
    loader.clear_prompt_cache()


def test_template_keeps_json_unicode_and_exact_whitespace(folder):
    text = '\n你是质检员。JSON：{"chapters":[{"title":"$title","body":""}]}\n '
    (folder / 'example.md').write_text(text, encoding='utf-8', newline='')
    assert get_prompt('example', title='安全方案') == text.replace('$title', '安全方案')


def test_substitution_does_not_interpret_values_as_templates(folder):
    (folder / 'example.md').write_text('$name / $$', encoding='utf-8')
    assert get_prompt('example', name='${unknown} {JSON} $5') == '${unknown} {JSON} $5 / $'


def test_template_cache_reads_once_but_never_caches_rendered_values(folder, monkeypatch):
    path = folder / 'example.md'
    path.write_text('你好$title', encoding='utf-8')
    read = Path.read_text
    reads = []

    def tracked(self, *args, **kwargs):
        reads.append(self)
        assert kwargs['encoding'] == 'utf-8'
        return read(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', tracked)
    assert get_prompt('example', title='甲') == '你好甲'
    assert get_prompt('example.md', title='乙') == '你好乙'
    assert reads == [path]
    path.write_text('新版$title', encoding='utf-8')
    assert get_prompt('example', title='丙') == '你好丙'
    loader.clear_prompt_cache()
    assert get_prompt('example', title='丁') == '新版丁'
    assert reads == [path, path]


def test_template_location_is_independent_of_cwd(folder, tmp_path, monkeypatch):
    (folder / 'example.md').write_text('中文提示词', encoding='utf-8')
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.chdir(other)
    assert get_prompt('example') == '中文提示词'


def test_missing_and_empty_files_report_the_filename(folder):
    with pytest.raises(FileNotFoundError, match='missing.md'):
        get_prompt('missing')
    (folder / 'empty.md').write_text('', encoding='utf-8')
    with pytest.raises(ValueError, match='empty.md'):
        get_prompt('empty')


def test_missing_variable_is_not_silently_left_unresolved(folder):
    (folder / 'example.md').write_text('$required', encoding='utf-8')
    with pytest.raises(ValueError, match='required'):
        get_prompt('example')


@pytest.mark.parametrize('name', ['../secret', '/etc/passwd', 'C:\\secret', 'a/b', '.env', 'example.txt', '', None])
def test_prompt_names_cannot_escape_folder(folder, name):
    with pytest.raises(ValueError, match='提示词名称'):
        get_prompt(name)


def test_tender_system_messages_do_not_contain_inline_static_strings():
    root = Path(__file__).resolve().parents[1] / 'apps/api/services/tenders'
    for file in root.glob('*.py'):
        for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
            if not isinstance(node, ast.Dict):
                continue
            entries = {k.value:v for k,v in zip(node.keys,node.values) if isinstance(k,ast.Constant)}
            role = entries.get('role')
            if isinstance(role,ast.Constant) and role.value == 'system':
                assert not isinstance(entries.get('content'), (ast.Constant,ast.JoinedStr)), str(file)
