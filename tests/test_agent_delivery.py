# tests/test_agent_delivery.py
# Авто-доставка agent-пакетов (SMB copy + psexec), name-based admin_ws (Task 14)
# и ФОНОВАЯ доставка с live-статусом/логами (Task 15).
import asyncio
from pathlib import Path

try:
    from tests.test_agent_api_integration import (
        make_deployer, run, VER, run_gen)
except ModuleNotFoundError:
    # на этой машине site-packages содержит пакет 'tests' (shadowing) — sibling-import
    from test_agent_api_integration import (
        make_deployer, run, VER, run_gen)


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
    assert cmd == [r"C:\Tools\psexec.exe", "-s", "-d", r"\\10.0.0.5",
                   "-w", r"C:\Core\agent\node7",
                   r"C:\Core\agent\node7\agent.exe"]
    assert cap is True and tmo == 30, "-d (detached) -> локальный таймаут только на запуск"
    assert res["psexec_code"] == 0


def test_gen_agent_package_queues_background_delivery(tmp_path, monkeypatch):
    """(Task 15) gen отвечает ДО копирования: delivery.state == queued, фоновая
    задача создана; само копирование идёт в _run_deliveries после ответа RPC."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")
    copied = []
    monkeypatch.setattr(svc, "_delivery_copy",
                        lambda pkg_dir, dest: copied.append(dest))
    monkeypatch.setattr("services.deployer.service.shutil.which", lambda name: None)

    captured = []

    class _StubTask:
        """Захваченная, но НЕ запланированная задача — воркер не стартует."""
        def done(self):
            return False

        def add_done_callback(self, cb):
            pass

    def _capture(coro, *args, **kwargs):
        captured.append(coro)
        return _StubTask()

    real_create_task = asyncio.create_task
    monkeypatch.setattr(asyncio, "create_task", _capture)
    res = run(svc.gen_agent_package({"targets": ["Node7"]}))
    assert res["ok"] is True                       # ok гена не зависит от delivery
    assert res["packages"][0]["delivery"] == {"state": "queued"}
    assert captured, "фоновая задача доставки не создана"
    assert copied == [], "gen скопировал пакет до ответа RPC"
    st = svc._delivery_status["node7"]
    assert st["state"] == "queued" and st["version"] == VER
    assert st["logs"] and st["logs"][0].startswith("queued:")
    assert svc._deliver_tasks.get("node7") is not None, "guard задачи не записан"
    for c in captured:
        c.close()

    # дренаж (шпиона create_task снимаем): доставка доходит до конца,
    # результат и логи оказываются в _delivery_status
    monkeypatch.setattr(asyncio, "create_task", real_create_task)
    svc._deliver_tasks.clear()
    svc._deliver_task = None
    run_gen(svc, {"targets": ["Node7"]})
    st = svc._delivery_status["node7"]
    assert st["state"] == "error" and st["result"]["ok"] is False
    assert "psexec" in st["result"]["error"]
    assert copied and copied[0] == Path(r"\\node7\C$\Core\agent\node7")


def test_run_deliveries_isolates_nodes_and_writes_logs(tmp_path, monkeypatch):
    """(Task 15) _run_deliveries: все ноды обработаны, done/error + result +
    непустые логи; исключение одной ноды не роняет остальные."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")

    async def _fake(node, pkg_dir, on_log=None):
        if node == "boom":
            raise RuntimeError("boom")
        if node == "node7":
            if on_log:
                on_log(r"copy → \\node7\C$\Core\agent\node7")
            return {"ok": False, "error": "psexec missing"}
        if on_log:
            on_log("copy → local")
        return {"ok": True, "local": True, "unc": r"C:\Core\agent\admin0"}

    monkeypatch.setattr(svc, "_deliver_agent", _fake)
    res = run_gen(svc, {"targets": ["Node7", "Boom", "Admin0"]})
    assert res["ok"] is True
    m = svc._delivery_status
    assert m["node7"]["state"] == "error" and m["node7"]["result"]["ok"] is False
    assert m["boom"]["state"] == "error"
    assert "boom" in m["boom"]["result"]["error"]
    assert any("exception" in ln for ln in m["boom"]["logs"])
    assert m["admin0"]["state"] == "done" and m["admin0"]["result"]["ok"] is True
    assert m["admin0"]["result"]["unc"] == r"C:\Core\agent\admin0"
    for node in ("node7", "boom", "admin0"):
        assert m[node]["logs"], f"нет логов доставки для {node}"
        assert m[node]["finished_at"] is not None, f"нет finished_at для {node}"
    assert svc._deliver_tasks == {}, "guard не очищен после завершения воркера"


def test_deliver_retry_busy_and_gen_does_not_overwrite(tmp_path, monkeypatch):
    """(Task 15) retry при живой доставке -> D9 «уже выполняется»;
    gen при running не перетирает запись и её логи."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    (dist / VER / "agent" / "node7").mkdir(parents=True, exist_ok=True)
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")

    async def _go():
        gate = asyncio.Event()
        live = asyncio.create_task(gate.wait())     # «живая» задача ноды
        svc._deliver_tasks["node7"] = live
        svc._delivery_status["node7"] = {
            "state": "running", "version": VER, "logs": ["start"],
            "started_at": 0.0, "finished_at": None, "result": None}
        res = await svc.deliver_agent({"node": "node7", "version": VER})
        assert res["ok"] is False and "уже выполняется" in res["error"], res
        res2 = await svc.gen_agent_package({"targets": ["Node7"], "version": VER})
        assert res2["ok"] is True
        assert res2["packages"][0]["delivery"]["state"] == "running"
        assert svc._delivery_status["node7"]["state"] == "running"
        assert svc._delivery_status["node7"]["logs"] == ["start"]
        live.cancel()
        try:
            await live
        except asyncio.CancelledError:
            pass

    run(_go())


def test_agent_status_reports_deliveries(tmp_path, monkeypatch):
    """(Task 15) agent_status отдаёт ключ deliveries с полными записями,
    существующие ключи не ломаются."""
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    (dist / "DeployAgent.exe").write_bytes(b"MZ-agent")
    monkeypatch.setattr(svc, "_delivery_copy", lambda pkg_dir, dest: None)
    monkeypatch.setattr("services.deployer.service.shutil.which", lambda name: None)
    run_gen(svc, {"targets": ["Node7"]})
    st = run(svc.agent_status({}))
    for k in ("statuses", "keys", "mode", "admin_ws", "admin_port", "deliveries"):
        assert k in st, f"нет ключа {k} в agent_status"
    d = st["deliveries"]["node7"]
    assert d["state"] == "error" and d["result"]["ok"] is False
    assert d["version"] == VER and d["logs"] and d["finished_at"] is not None


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
    # валидный пакет -> мгновенный started, доставка уходит в фон
    pkg = dist / VER / "agent" / "node7"
    pkg.mkdir(parents=True)
    (pkg / "agent.exe").write_bytes(b"MZ-agent")
    monkeypatch.setattr(svc, "_delivery_copy", lambda pkg_dir, dest: None)
    monkeypatch.setattr("services.deployer.service.shutil.which", lambda name: None)
    res = run_gen(svc, {"node": "Node7", "version": VER}, method="deliver_agent")
    assert res["ok"] is True and res["started"] is True and res["state"] == "queued"
    st = svc._delivery_status["node7"]
    assert st["state"] == "error" and "psexec" in st["result"]["error"]
    assert st["result"]["unc"] == r"\\node7\C$\Core\agent\node7"


def test_deliver_agent_rejects_dot_segments(tmp_path, monkeypatch):
    r"""(review 1) `.`, `..`, пустой node и `\x00` — явный reject ДО resolve.

    canon_node_id отсекает только `/\:`, поэтому `..` проходит guard:
    `(DIST/ver/"agent"/"..").resolve()` = dist/<ver> (внутри dist), и копия ушла бы
    в `\\host\C$\Core\agent\..` = `C:\Core` цели любым mesh-узлом через RPC.
    """
    svc, dist, ctx = make_deployer(tmp_path, monkeypatch)
    # dist/<VER> создаёт make_deployer; dist/<VER>/agent — чтобы '.' тоже
    # проходил is_dir() и без guard'а доставка реально вызывалась
    (dist / VER / "agent").mkdir(parents=True, exist_ok=True)
    delivered = []

    def _fake_deliver(node, pkg_dir, on_log=None):
        delivered.append(node)
        return asyncio.sleep(0, result={"ok": True})

    monkeypatch.setattr(svc, "_deliver_agent", _fake_deliver)
    for bad in ("..", ".", " .. ", "..\x00", "\x00", "", None):
        res = run(svc.deliver_agent({"node": bad, "version": VER}))
        assert res["ok"] is False and res.get("error"), (bad, res)
    assert delivered == [], f"path-guard пропустил dot-компонент: {delivered}"
    # тот же компонент закрыт и в gen_agent_package (не создаёт пакет в dist/<ver>)
    res = run(svc.gen_agent_package({"targets": [".."], "version": VER}))
    assert res["ok"] is False and res.get("error")
    res = run(svc.gen_agent_package({"targets": ["."], "version": VER}))
    assert res["ok"] is False and res.get("error")
    assert delivered == []


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
                        lambda n, p, on_log=None: asyncio.sleep(0, result={"ok": True}))
    res = run_gen(svc, {"targets": ["Node7"]})
    assert res["admin_ws"] == f"ws://{ctx.NODE}:{ctx.config.network.port}"
