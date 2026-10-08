"""CLI-аргументы compile.py: --type/--flavor и аргументы agent-сборки."""
import importlib.util

import pytest

if importlib.util.find_spec("PyInstaller") is None:
    pytest.skip("PyInstaller не установлен", allow_module_level=True)

import compile as C   # noqa: E402  (module name shadow - compile builtin, импорт по importlib)


def test_detect_flavor():
    assert C.detect_flavor("open", True) == "open"
    assert C.detect_flavor("se", False) == "se"
    assert C.detect_flavor(None, True) == "se"
    assert C.detect_flavor("auto", False) == "open"


def test_agent_args_open_has_no_se():
    args = C.agent_args("open")
    joined = " ".join(args)
    assert "services/deployer/agent/main.py" in args
    assert "--exclude-module" in args and "src.se" in args
    assert "--name" in args and "DeployAgent" in args
    assert "DeployAgent_se" not in joined          # open-имя без суффикса
    assert args[0] == "services/deployer/agent/main.py"   # entry — агентский main
    assert "--collect-all=services" not in joined   # дерево сервисов не тянем
    for ui in ("streamlit", "fastapi", "uvicorn"):
        assert f"--exclude-module {ui}" in joined   # агент без UI/веб-сервера
    assert "--hidden-import" in args and "websockets" in args
    assert "src.networking.protocol" in args
    assert "src.networking.neighbor_table" in args


def test_agent_args_se_name():
    args = C.agent_args("se")
    i = args.index("--name")
    assert args[i + 1] == "DeployAgent_se"
    assert "--exclude-module src.se" not in " ".join(args)


def test_main_parses_type_and_flavor(monkeypatch):
    # __main__ блок вынесен в функцию main(argv) — вызываем напрямую
    assert callable(getattr(C, "main", None))
    assert C.arg_parser().parse_args([]).type == "all"   # дефолт как сейчас: node+webui
    assert C.arg_parser().parse_args(["--type", "agent"]).type == "agent"
