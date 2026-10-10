# tests/test_agent_delivery.py
# Авто-доставка agent-пакетов (SMB copy + psexec) и name-based admin_ws (Task 14).
import asyncio
from pathlib import Path

try:
    from tests.test_agent_api_integration import make_deployer, run, VER
except ModuleNotFoundError:
    # на этой машине site-packages содержит пакет 'tests' (shadowing) — sibling-import
    from test_agent_api_integration import make_deployer, run, VER


def _mk_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg" / "node7"
    pkg.mkdir(parents=True)
    (pkg / "agent.exe").write_bytes(b"MZ-agent")
    (pkg / "agent.conf").write_text("{}", encoding="utf-8")
    return pkg


def test_deliver_local_copy_no_psexec(tmp_path, monkeypatch):
    """Локальная цель (свой node): копия в work_dir, psexec не вызывается."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    pkg = _mk_pkg(tmp_path)
    which_calls = []
    monkeypatch.setattr("services.deployer.service.shutil.which",
                        lambda name: which_calls.append(name))
    res = run(svc._deliver_agent("admin0", pkg))     # NODE == admin0 -> локально
    assert res["ok"] is True and res["local"] is True
    dest = Path(ctx.config.local.work_dir) / "agent" / "admin0"
    assert (dest / "agent.exe").read_bytes() == b"MZ-agent"
    assert (dest / "agent.conf").exists()
    assert which_calls == [], "psexec не должен вызываться для локальной цели"


def test_deliver_remote_copy_then_psexec_missing(tmp_path, monkeypatch):
    """Порядок copy-then-psexec: копирование проходит ДО проверки PsTools,
    при отсутствии psexec пакет остаётся на месте, ответ — D9-словарь ok=False."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    pkg = _mk_pkg(tmp_path)
    copied = []
    monkeypatch.setattr(svc, "_delivery_copy",
                        lambda pkg_dir, dest: copied.append(dest))
    monkeypatch.setattr("services.deployer.service.shutil.which", lambda name: None)
    res = run(svc._deliver_agent("node7", pkg))
    assert res["ok"] is False and res["psexec_code"] is None
    assert "psexec" in res["error"] and "PsTools" in res["error"]
    # пакет НЕ теряется: копирование уже выполнено
    assert copied == [Path(r"\\node7\C$\Core\agent\node7")]


def test_deliver_remote_unc_and_psexec_args(tmp_path, monkeypatch):
    """UNC-форма для remote-хоста (резолв из neighbor_table) + аргументы psexec
    с обязательным рабочим каталогом -w."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    ctx.network.neighbor_table.entries["node7"] = \
        type("I", (), {"host": "10.0.0.5"})()
    pkg = _mk_pkg(tmp_path)
    copied = []
    monkeypatch.setattr(svc, "_delivery_copy",
                        lambda pkg_dir, dest: copied.append(dest))
    monkeypatch.setattr("services.deployer.service.shutil.which",
                        lambda name: r"C:\Tools\psexec.exe")
    cmds = []

    class _Proc:
        returncode = 0
        stdout = b""
        stderr = b""

    def _run(cmd, capture_output=False, timeout=None):
        cmds.append((cmd, capture_output, timeout))
        return _Proc()

    monkeypatch.setattr("services.deployer.service.subprocess.run", _run)
    res = run(svc._deliver_agent("node7", pkg))
    assert res["ok"] is True and res["local"] is False
    assert res["host"] == "10.0.0.5"
    assert res["unc"] == r"\\10.0.0.5\C$\Core\agent\node7"
    assert copied == [Path(r"\\10.0.0.5\C$\Core\agent\node7")]
    cmd, cap, tmo = cmds[0]
    assert cmd == [r"C:\Tools\psexec.exe", "-s", r"\\10.0.0.5",
                   "-w", r"C:\Core\agent\node7",
                   r"C:\Core\agent\node7\agent.exe"]
    assert cap is True and tmo == 60
    assert res["psexec_code"] == 0


def test_gen_agent_package_includes_delivery(tmp_path, monkeypatch):
    """gen_agent_package кладёт поле delivery; отказ доставки не валит генерацию."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")
    copied = []
    monkeypatch.setattr(svc, "_delivery_copy",
                        lambda pkg_dir, dest: copied.append(dest))
    monkeypatch.setattr("services.deployer.service.shutil.which", lambda name: None)
    res = run(svc.gen_agent_package({"targets": ["Node7"]}))
    assert res["ok"] is True                       # ok гена не зависит от delivery
    d = res["packages"][0]["delivery"]
    assert d["ok"] is False and "psexec" in d["error"]
    assert copied and copied[0] == Path(r"\\node7\C$\Core\agent\node7")
    # даже исключение внутри _deliver_agent не валит генерацию
    def _boom(node, pkg_dir):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "_deliver_agent", _boom)
    res2 = run(svc.gen_agent_package({"targets": ["Node7"]}))
    assert res2["ok"] is True
    assert res2["packages"][0]["delivery"]["ok"] is False
    assert "boom" in res2["packages"][0]["delivery"]["error"]


def test_deliver_agent_rpc_path_guard(tmp_path, monkeypatch):
    """deliver_agent: каталог пакета обязан лежать внутри dist/ (path traversal)."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    # version с выходом из dist -> отказ-словарь
    res = run(svc.deliver_agent({"node": "node7", "version": "../evil"}))
    assert res["ok"] is False and "dist" in res["error"]
    # без node
    res = run(svc.deliver_agent({}))
    assert res["ok"] is False and res.get("error")
    # пакета нет
    res = run(svc.deliver_agent({"node": "node7", "version": VER}))
    assert res["ok"] is False
    # валидный пакет -> уходит в обычную доставку
    pkg = dist / VER / "agent" / "node7"
    pkg.mkdir(parents=True)
    (pkg / "agent.exe").write_bytes(b"MZ-agent")
    monkeypatch.setattr(svc, "_delivery_copy", lambda pkg_dir, dest: None)
    monkeypatch.setattr("services.deployer.service.shutil.which", lambda name: None)
    res = run(svc.deliver_agent({"node": "Node7", "version": VER}))
    assert res["ok"] is False and "psexec" in res["error"]
    assert res["unc"] == r"\\node7\C$\Core\agent\node7"


def test_agent_status_admin_ws_name_based(tmp_path, monkeypatch):
    """admin_ws несёт имя ноды вместо IP (+ admin_port для UI); фоллбэк gen — тот же."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    st = run(svc.agent_status({}))
    assert st["admin_ws"] == f"ws://{ctx.NODE}:{ctx.config.network.port}"
    assert st["admin_port"] == ctx.config.network.port
    assert st["admin_node"] == ctx.NODE
    # фоллбэк gen (без admin_ws в data) — тоже имя ноды, без IP
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")
    monkeypatch.setattr(svc, "_deliver_agent",
                        lambda n, p: asyncio.sleep(0, result={"ok": True}))
    res = run(svc.gen_agent_package({"targets": ["Node7"]}))
    assert res["admin_ws"] == f"ws://{ctx.NODE}:{ctx.config.network.port}"
