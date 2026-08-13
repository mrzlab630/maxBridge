"""Static and executable deployment contracts."""

import os
import pathlib
import shutil
import stat
import subprocess

CLI_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = CLI_ROOT.parent
INSTALLER = CLI_ROOT / "deploy/install.sh"


def _installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_installer_is_local_first_and_avoids_privileged_paths():
    installer = _installer_text()

    assert 'CLI_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"' in installer
    assert 'cd "$CLI_ROOT"' in installer
    for forbidden in ("useradd", "/etc/maxbridge", "/usr/local", "/var/lib", "systemctl", "sudo"):
        assert forbidden not in installer


def test_installer_uses_checkout_venv_for_regular_and_optional_dev_install():
    installer = _installer_text()

    assert "python3 -m venv .venv" in installer
    assert installer.count(".venv/bin/python -m pip install .") == 1
    assert 'if [ "${INSTALL_DEV:-0}" = "1" ]; then' in installer
    assert installer.count(".venv/bin/python -m pip install '.[dev]'") == 1
    assert "pip install -e" not in installer
    assert "pip install --editable" not in installer


def test_installer_creates_and_preserves_local_state_from_arbitrary_cwd(tmp_path):
    checkout = tmp_path / "checkout" / "cli"
    (checkout / "deploy").mkdir(parents=True)
    (checkout / "src/maxbridge/data").mkdir(parents=True)
    shutil.copy2(INSTALLER, checkout / "deploy/install.sh")
    (checkout / "src/maxbridge/data/default.yaml").write_text("marker: default\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"venv\" ]; then\n"
        "    mkdir -p .venv/bin\n"
        "    cp \"$0\" .venv/bin/python\n"
        "else\n"
        "    printf '%s\\n' \"$*\" >> pip-calls\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "INSTALL_DEV": "1"}

    first = subprocess.run(
        ["bash", str(checkout / "deploy/install.sh")],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    config = checkout / "config/local.yaml"
    assert config.read_text(encoding="utf-8") == "marker: default\n"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert (checkout / "data").is_dir()
    assert (checkout / "logs").is_dir()
    assert (checkout / "pip-calls").read_text(encoding="utf-8").splitlines() == [
        "-m pip install .",
        "-m pip install .[dev]",
    ]
    assert f"cd {checkout}" in first.stdout
    assert "Authenticate: .venv/bin/maxbridge --auth-only -c config/local.yaml" in first.stdout
    assert "Start:        .venv/bin/maxbridge -c config/local.yaml" in first.stdout

    config.write_text("marker: operator\n", encoding="utf-8")
    subprocess.run(
        ["bash", str(checkout / "deploy/install.sh")],
        cwd=tmp_path,
        env={**env, "INSTALL_DEV": "0"},
        check=True,
        capture_output=True,
        text=True,
    )
    assert config.read_text(encoding="utf-8") == "marker: operator\n"


def test_systemd_application_pid_contract_is_consistent():
    unit = (CLI_ROOT / "deploy/maxbridge.service").read_text(encoding="utf-8")
    required = [
        "User=maxbridge",
        "Group=maxbridge",
        "RuntimeDirectory=maxbridge",
        "RuntimeDirectoryMode=0750",
        "Environment=MAXBRIDGE_DAEMON_PID_FILE=/run/maxbridge/maxbridge.pid",
        "PIDFile=/run/maxbridge/maxbridge.pid",
        "StateDirectory=maxbridge",
        "LogsDirectory=maxbridge",
        "PrivateTmp=true",
        "ExecStart=/usr/local/bin/maxbridge -c /etc/maxbridge/config.yaml",
    ]

    for declaration in required:
        assert unit.count(declaration) == 1


def test_pm2_sets_application_pid_env_without_supervisor_pid_file():
    config = (CLI_ROOT / "ecosystem.config.cjs").read_text(encoding="utf-8")

    assert 'MAXBRIDGE_DAEMON_PID_FILE: path.resolve(__dirname, "data", "maxbridge.pid")' in config
    assert "pid_file" not in config


def test_docs_define_exclusive_supervisors_and_conflict_recovery():
    docs = "\n".join([
        (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
        (CLI_ROOT / "KNOWLEDGE.md").read_text(encoding="utf-8"),
    ]).lower()

    assert "mutually exclusive" in docs
    assert "maxbridge_daemon_pid_file" in docs
    assert "pm2_home" in docs
    assert "external_conflict" in docs
    assert "network namespace" in docs
    assert "operator-triggered restart" in docs
