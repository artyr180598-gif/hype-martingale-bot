"""Контракт переключателя версий при деплое: entrypoint, Docker, Procfile, main.py v2."""

from __future__ import annotations

import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_entrypoint_is_executable_and_switches_on_run_v2():
    path = ROOT / "entrypoint.sh"
    assert path.exists()
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, "entrypoint.sh должен быть исполняемым (git mode 100755)"
    text = path.read_text(encoding="utf-8")
    assert "RUN_V2" in text
    assert 'python -m v2 "${V2_COMMAND:-serve}" "$@"' in text
    assert 'python main.py "$@"' in text


def test_dockerfile_copies_v2_entrypoint_and_healthcheck():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY v2/ ./v2/" in text
    assert "COPY entrypoint.sh" in text
    assert "chmod" in text and "entrypoint.sh" in text
    assert 'CMD ["./entrypoint.sh"]' in text
    assert "${PORT:-8000}" in text
    assert "HEALTHCHECK" in text


def test_procfile_and_compose_use_entrypoint_and_port():
    procfile = (ROOT / "Procfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./entrypoint.sh" in procfile
    assert "./entrypoint.sh" in compose
    assert "${PORT:-8000}" in compose
    assert "RUN_V2" in compose


def test_env_example_and_readme_document_switch():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for blob in (env, readme):
        assert "RUN_V2" in blob
        assert "V2_COMMAND" in blob
        assert "PORT" in blob


def test_main_delegates_v2_before_argparse(monkeypatch):
    import main as main_mod

    seen: dict[str, list[str] | None] = {}

    def fake_v2(argv=None):
        seen["argv"] = list(argv) if argv is not None else None
        return 0

    monkeypatch.setattr("v2.cli.main", fake_v2)
    import sys

    monkeypatch.setattr(sys, "argv", ["main.py", "v2", "analyze", "AURORA", "--data-mode", "demo"])
    assert main_mod.main() == 0
    assert seen["argv"] == ["analyze", "AURORA", "--data-mode", "demo"]
