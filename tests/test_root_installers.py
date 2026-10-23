"""Windows CMD forwarding in disposable trees; installation calls are inert spies."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result


@pytest.mark.skipif(os.name != "nt", reason="Real Windows CMD forwarding")
@pytest.mark.parametrize("wrapper,script", [("Install.cmd", "install_desktop.py"), ("InstallPlugin.cmd", "install_plugin.py"), ("InstallMCP.cmd", "install_mcp.py")])
@pytest.mark.parametrize("exit_code", [0, 17])
def test_cmd_forwards_spaces_unicode_and_exit_code(tmp_path, wrapper, script, exit_code):
    root = tmp_path / "Checkout with spaces"; (root / "scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / wrapper, root / wrapper)
    (root / "scripts" / script).write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\nraise SystemExit(' + str(exit_code) + ')\n', encoding="utf-8")
    arguments = ["--runtime-dir", str(tmp_path / "Runtime with spaces"), "--label", "工具 café"]
    command = '""' + str(root / wrapper) + '" ' + subprocess.list2cmdline(arguments) + '"'
    result = subprocess.run('"' + os.environ["COMSPEC"] + '" /d /s /c ' + command,
        env={**os.environ, "FANUC_INSTALL_PYTHON": sys.executable, "PYTHONUTF8": "1"}, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == exit_code, (result.stdout, result.stderr)
    assert json.loads(result.stdout) == arguments


@pytest.mark.skipif(os.name != "nt", reason="Real Windows CMD help")
@pytest.mark.parametrize("wrapper", ["Install.cmd", "InstallPlugin.cmd", "InstallMCP.cmd"])
def test_actual_root_wrapper_help_has_no_install_side_effect(wrapper):
    command = '"' + os.environ["COMSPEC"] + '" /d /s /c ""' + str(ROOT / wrapper) + '" --help"'
    result = subprocess.run(command, env={**os.environ, "FANUC_INSTALL_PYTHON": sys.executable},
                            capture_output=True, text=True)
    assert result.returncode == 0 and "usage:" in result.stdout, (result.stdout, result.stderr)


@pytest.mark.parametrize("name", ["install_desktop", "install_plugin"])
def test_help_exits_before_any_install_or_launch(monkeypatch, name, capsys):
    script = module(name)
    monkeypatch.setattr(script.subprocess, "run", lambda *a, **k: pytest.fail("Help cannot install or launch"))
    with pytest.raises(SystemExit) as result: script.main(["--help"])
    assert result.value.code == 0 and "usage:" in capsys.readouterr().out


def test_plugin_forwards_exact_bundle_setup_arguments_and_failure(tmp_path, monkeypatch):
    script = module("install_plugin"); bundle = tmp_path / "Bundle with spaces"; bundle.mkdir(); (bundle / "setup.py").touch()
    extra = ["--runtime-dir", str(tmp_path / "Runtime 日本語"), "--register", "none"]
    calls = []
    def run(command, **kwargs): calls.append((command, kwargs)); return subprocess.CompletedProcess(command, 23)
    monkeypatch.setattr(script.subprocess, "run", run)
    assert script.main(["--bundle", str(bundle), *extra]) == 23
    assert calls == [([sys.executable, "-B", str(bundle / "setup.py"), *extra], {"cwd": bundle})]


def test_desktop_stops_at_failed_pip_without_shortcut_or_gui(tmp_path, monkeypatch):
    script = module("install_desktop"); venv = tmp_path / "Desktop venv"; (venv / "Scripts").mkdir(parents=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"); python.parent.mkdir(exist_ok=True); python.touch(); (venv / "pyvenv.cfg").touch()
    calls = []
    def fail(command, **kwargs): calls.append(command); raise subprocess.CalledProcessError(19, command)
    monkeypatch.setattr(script.subprocess, "run", fail)
    assert script.main(["--venv-dir", str(venv), "--launch"]) == 19
    assert len(calls) == 1 and "pip" in calls[0] and "fanuc_workbench.app" not in calls[0]


@pytest.mark.parametrize("launch,no_shortcut", [(False, False), (True, False), (False, True)])
def test_successful_desktop_work_only_launches_when_selected(tmp_path, monkeypatch, launch, no_shortcut):
    script = module("install_desktop"); venv = tmp_path / "Disposable venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True); python.touch(); (venv / "pyvenv.cfg").touch()
    calls = []
    def run(command, **kwargs): calls.append(command); return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(script.subprocess, "run", run)
    extra = (["--launch"] if launch else []) + (["--no-shortcut"] if no_shortcut else [])
    assert script.main(["--venv-dir", str(venv), *extra]) == 0
    assert any("fanuc_workbench.app" in row for row in calls) is launch
    assert any("fanuc_workbench.desktop_install" in row for row in calls) is not no_shortcut
    assert all(row[0] == str(python) for row in calls)
