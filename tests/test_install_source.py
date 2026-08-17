"""issue #79: installation-source detection must correctly tell a
Homebrew-managed install apart from a direct one so the update flow never
overwrites a bundle Homebrew owns."""
import subprocess

from utils import install_source


def test_detect_direct_when_brew_missing(monkeypatch):
    monkeypatch.setattr(install_source, "brew_path", lambda: None)
    assert install_source.detect() == install_source.DIRECT


def test_detect_homebrew_when_cask_listed(monkeypatch):
    monkeypatch.setattr(install_source, "brew_path", lambda: "/opt/homebrew/bin/brew")

    def fake_run(cmd, **kwargs):
        assert cmd == ["/opt/homebrew/bin/brew", "list", "--cask", "--versions", "qforge"]
        return subprocess.CompletedProcess(cmd, 0, stdout="qforge 1.1.3\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert install_source.detect() == install_source.HOMEBREW


def test_detect_direct_when_cask_not_installed(monkeypatch):
    monkeypatch.setattr(install_source, "brew_path", lambda: "/opt/homebrew/bin/brew")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No such cask\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert install_source.detect() == install_source.DIRECT


def test_detect_unknown_when_brew_errors(monkeypatch):
    monkeypatch.setattr(install_source, "brew_path", lambda: "/opt/homebrew/bin/brew")

    def fake_run(cmd, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert install_source.detect() == install_source.UNKNOWN
