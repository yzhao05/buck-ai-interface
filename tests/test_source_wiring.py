"""Check references that can escape normal numerical/API tests."""
import ast
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def test_html_assets_and_literal_dom_references_exist():
    html=(ROOT/'frontend/index.html').read_text(encoding='utf-8')
    ids=set(re.findall(r'\bid="([^"]+)"',html))
    for asset in re.findall(r'(?:src|href)="\./([^"?]+)',html):
        assert (ROOT/'frontend'/asset).is_file(),asset
    scripts=set(re.findall(r'<script[^>]+src="\./([^"?]+)',html))
    for path in (ROOT/'frontend').glob('*.js'):
        assert path.name in scripts,f'Frontend module is not loaded: {path.name}'
        wanted=set(re.findall(r'''\$\(['"]([^'"\n]+)['"]\)''',path.read_text(encoding='utf-8')))
        assert not wanted-ids,(path.name,wanted-ids)


def test_relative_backend_imports_exist():
    for path in (ROOT/'backend').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if not isinstance(node,ast.ImportFrom) or not node.level or not node.module:continue
            base=path.parent
            for _ in range(node.level-1):base=base.parent
            target=base.joinpath(*node.module.split('.'))
            assert target.with_suffix('.py').is_file() or (target/'__init__.py').is_file(),(path,node.module)
