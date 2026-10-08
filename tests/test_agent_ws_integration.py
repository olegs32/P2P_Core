# tests/test_agent_ws_integration.py
"""WS-интеграция: реальный NetworkModule + deployer + клиент агента."""
import asyncio as aio                              # noqa: E402

from src.internal_modules.context import AppContext        # noqa: E402
from src.internal_modules.config import Config             # noqa: E402
from src.networking.network import NetworkModule           # noqa: E402

CONF_D = {
    "protocol": 1, "mode": "open", "admin_ws": "ws://127.0.0.1:9000",
    "admin_node": "admin0", "node_id": "node7", "key": "ab" * 32,
    "version": "1.0.0", "deploy_timeout": 5,
}


def run(coro):
    return aio.run(coro)


async def make_admin(tmp_path, monkeypatch):
    """Админ-нода in-process на порту 0 (паттерн tests/test_integration_msgpack.py:109-124)."""
    import services.deployer.service as DS
    monkeypatch.setattr(DS, "DIST", tmp_path / "dist")   # артефакты во временном dist
    dist = tmp_path / "dist"
    (dist / "1.0.0").mkdir(parents=True)
    (dist / "1.0.0" / "Node_P2P_Core.exe").write_bytes(b"MZ-node")
    (dist / "1.0.0" / "manifest.json").write_text(
        '{"version": "1.0.0", "exe_name": "Node_P2P_Core.exe",'
        ' "exe_sha256": "x", "size": 8}', encoding="utf-8")
    cfg = Config(node="Admin0", local={"work_dir": str(tmp_path / "work")})
    ctx = AppContext(config=cfg)
    net = NetworkModule("Network", ctx, host="127.0.0.1", port=0)
    ctx.network = net
    svc = DS.Deployer("deployer", ctx)
    ctx.services.register_service(svc)
    for attr_name in dir(type(svc)):
        attr = getattr(type(svc), attr_name)
        # bound-метод (паттерн install_service): unbound функция сломает вызов без self
        if callable(attr) and getattr(attr, "_is_rpc", False):
            ctx.services.register_method(svc, attr_name, getattr(svc, attr_name))
    await net.start()
    deadline = aio.get_event_loop().time() + 10
    while not net._server.started:
        if aio.get_event_loop().time() > deadline:
            raise RuntimeError("uvicorn did not start")
        await aio.sleep(0.05)
    port = net._server.servers[0].sockets[0].getsockname()[1]
    key, _ = svc._agent_registry.get_or_create("node7")   # ключ как у реального агента
    return ctx, net, port, key


def test_ping_and_pre_config_over_ws(tmp_path, monkeypatch):
    from services.deployer.agent.machine import AgentApiClient
    from services.deployer.agent.package import AgentConf

    async def flow():
        ctx, net, port, key = await make_admin(tmp_path, monkeypatch)
        conf = AgentConf.from_dict({**CONF_D, "key": key.hex(),
                                    "admin_ws": f"ws://127.0.0.1:{port}",
                                    "admin_node": "admin0"})
        client = AgentApiClient(conf)
        try:
            await client.connect()
            server_ts = await client.ping()
            assert isinstance(server_ts, (int, float)) and server_ts > 0
            pc = await client.pre_config("1.0.0")
            assert pc["node"] == "node7" and pc["install"]["full_path"]
            assert "ca_key" not in pc                    # ca_key никогда не уходит
        finally:
            await client.close()
            await net.stop()

    run(flow())
