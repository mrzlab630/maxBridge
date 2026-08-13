"""Static deployment ownership contracts."""

import pathlib

CLI_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = CLI_ROOT.parent


def test_installer_uses_packaged_default_config():
    installer = (CLI_ROOT / "deploy/install.sh").read_text(encoding="utf-8")
    default_config = CLI_ROOT / "src/maxbridge/data/default.yaml"

    assert default_config.is_file()
    assert "src/maxbridge/data/default.yaml" in installer
    assert "config/default.yaml" not in installer


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
