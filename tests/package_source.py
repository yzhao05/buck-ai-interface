"""Build a source archive from an explicit allowlist. Never include .env."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_DIRS = {"backend", "frontend", "examples", "tests", ".github"}
ALLOWED_FILES = {".env.example", ".gitignore", "LICENSE", "README.md", "THIRD_PARTY_NOTICES.md",
                 "render.yaml", "requirements.txt", "requirements-lock.txt", "requirements-dev.txt",
                 "requirements-pann.txt", "start-local.ps1", "启动界面.cmd"}


def build():
    destination = ROOT.parent / "pe-ai-interface-github.zip"
    files = []
    for folder in ALLOWED_DIRS:
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    files.extend(ROOT / name for name in ALLOWED_FILES)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for file in sorted(files):
            relative = file.relative_to(ROOT).as_posix()
            assert relative != ".env" and not relative.startswith((".venv/", "reference/", "runtime/", ".git/"))
            archive.write(file, relative)
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert ".github/workflows/pages.yml" in archive.namelist()
        assert "frontend/.nojekyll" in archive.namelist()
    record = {"archive": str(destination), "files": len(files), "bytes": destination.stat().st_size,
              "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
              "excluded": [".env", ".venv", ".git", ".tmp", "reference", "runtime"]}
    (ROOT / "runtime").mkdir(exist_ok=True)
    (ROOT / "runtime/package-manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    build()
