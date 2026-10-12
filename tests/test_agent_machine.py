# tests/test_agent_machine.py
"""Конечный автомат агента: happy/откаты/busy/auth/админ недоступен/sha-retry."""
import asyncio
import hashlib
import sys
import time
import uuid
from pathlib import Path

import pytest

from services.deployer.agent.package import AgentConf

_REAL_NEW = Path(r"C:\Core\Node_P2P_Core.exe.new")
_REAL_BAK = Path(r"C:\Core\.deploy_bak")
_BAK_ROOT = r"C:\Core\.deploy_bak"          # подменяется фикстурой на tmp_path


def _fs_state(p: Path) -> tuple:
    return (p.exists(), p.stat().st_mtime_ns if p.exists() else None)


@pytest.fixture(autouse=True)
def _paths_in_tmp(tmp_path, monkeypatch):
    """Все пути фикстур — внутри tmp_path; пост-чек реального диска."""
    before = (_fs_state(_REAL_NEW), _fs_state(_REAL_BAK))
    monkeypatch.setitem(PC, "install", {
        "work_dir": str(tmp_path),
        "exe_name": "Node_P2P_Core.exe",
        "full_path": str(tmp_path / "Node_P2P_Core.exe"),
    })
    monkeypatch.setattr(sys.modules[__name__], "_BAK_ROOT",
                        str(tmp_path / ".deploy_bak"))
    yield
    assert (_fs_state(_REAL_NEW), _fs_state(_REAL_BAK)) == before, \
        "тест записал файлы вне tmp_path"


CONF_D = {
    "protocol": 1, "mode": "open", "admin_ws": "ws://10.0.0.1:9000",
    "admin_node": "node0", "node_id": "node7", "key": "ab" * 32,
    "version": "1.0.0", "deploy_timeout": 5,
}
PC = {
    "v": 1, "mode": "open", "node": "node7", "deploy_id": str(uuid.uuid4()),
    "exp": time.time() + 3600, "config": "node: node7\n",
    "install": {"work_dir": r"C:\Core", "exe_name": "Node_P2P_Core.exe",
                "full_path": r"C:\Core\Node_P2P_Core.exe"},
    # sha256 согласован с фейковым payload b"NODE" (в плане стоял "ab"*32
    # от ключа — happy-path падал бы на mismatch; отклонение от фикстуры)
    "exe": {"name": "Node_P2P_Core.exe", "sha256": hashlib.sha256(b"NODE").hexdigest(),
            "size": 4, "version": "1.0.0"},
}


class FakeClient:
    # op -> значение или Exception; pre_config/node_seen переопределяем гибко.
    def __init__(self, pc=None, fail=None, trace=None):
        self.pc = pc if pc is not None else dict(PC)
        self.fail = fail            # Exception, который кидает connect/ping
        self.reports = []
        self.chunk_calls = 0
        self.node_seen_value = True
        self.pre_config_calls = 0
        self.trace = trace if trace is not None else []

    async def connect(self):
        if self.fail and self.fail.__class__.__name__ == "AdminDown":
            raise _admin_down()

    async def ping(self):
        if self.fail and self.fail.__class__.__name__ == "AdminDown":
            raise _admin_down()
        return time.time()

    async def pre_config(self, version):
        self.trace.append("pre_config")
        self.pre_config_calls += 1
        if isinstance(self.fail, str) and self.fail.startswith("op:"):
            name = self.fail[3:]
            if name == "pre_config_busy":
                raise _business("deploy in progress")
            if name == "pre_config_auth":
                raise _auth()
        return dict(self.pc)

    async def fetch_chunk(self, offset, size):
        self.chunk_calls += 1
        return b"NODE" [offset:offset + size]

    async def node_seen(self):
        return self.node_seen_value

    async def report(self, data):
        self.reports.append(dict(data))


class FakeHost:
    def __init__(self, system="ok", backup=True, die_after_launch=False, trace=None):
        self.system = system
        self.backup_ok = backup
        self.die = die_after_launch
        self.calls = []
        self.alive = True
        self.written = False
        self.trace = trace if trace is not None else []

    def detect(self, pc):
        self.calls.append("detect")
        return {"strategy": "open"}

    async def ensure_system(self):
        self.calls.append("ensure_system")
        self.trace.append("ensure_system")
        return self.system

    async def stop_node(self, exe_name):
        self.calls.append(("stop", exe_name))

    async def backup(self, deploy_id, pc, plan):
        self.calls.append("backup")
        return str(Path(_BAK_ROOT) / deploy_id) if self.backup_ok else None

    async def write(self, pc, plan):
        self.calls.append("write")
        self.written = True

    async def launch(self, pc):
        self.calls.append("launch")
        self.alive = not self.die
        return 4242

    async def pid_alive(self, pid):
        return self.alive

    async def restore(self, backup_dir, pc):
        self.calls.append(("restore", backup_dir))

    async def self_terminate(self, code):
        self.calls.append(("terminate", code))


def _admin_down():
    from services.deployer.agent.machine import AdminUnreachable
    return AdminUnreachable("admin down")


def _business(msg):
    from services.deployer.agent.machine import BusinessError
    return BusinessError(msg)


def _auth():
    from services.deployer.agent.machine import AuthRejected
    return AuthRejected("auth failed")


def _run(conf, client, host):
    from services.deployer.agent.machine import DeployMachine
    return asyncio.run(DeployMachine(conf, client, host).run())


def _conf(**over):
    d = dict(CONF_D)
    d.update(over)
    return AgentConf.from_dict(d)


def test_happy_path_reports_done_and_terminates():
    trace: list = []
    client, host = FakeClient(trace=trace), FakeHost(trace=trace)
    code = _run(_conf(), client, host)
    assert code == 0
    stages = [r["stage"] for r in client.reports]
    assert stages[0] == "pre_config" and stages[-1] == "done"      # Delta 6: до pre_config отчётов нет
    assert ("terminate", 0) in host.calls and "write" in host.calls
    # Delta 1: ENSURE_SYSTEM строго ДО PRE_CONFIG (иначе deadlock busy-lock)
    assert trace.index("ensure_system") < trace.index("pre_config")
    assert client.pre_config_calls == 1


def test_handoff_no_cleanup():
    host = FakeHost(system="relaunched")
    code = _run(_conf(), FakeClient(), host)
    assert code == 0
    assert not any(c for c in host.calls if isinstance(c, tuple) and c[0] == "terminate")


def test_need_system_exit14_keeps_package():
    host = FakeHost(system="denied")
    code = _run(_conf(), FakeClient(), host)
    assert code == 14
    assert not any(c for c in host.calls if isinstance(c, tuple) and c[0] == "terminate")
    assert host.written is False


def test_process_dies_triggers_rollback():
    client, host = FakeClient(), FakeHost(die_after_launch=True, backup=True)
    code = _run(_conf(), client, host)
    assert code == 13
    assert any(isinstance(c, tuple) and c[0] == "restore" for c in host.calls)
    assert client.reports[-1]["stage"] in ("rolled_back", "failed")
    assert ("terminate", 13) in host.calls            # запись была -> cleanup


def test_no_backup_no_rollback_keeps_old_state():
    host = FakeHost(die_after_launch=True, backup=False)
    code = _run(_conf(), FakeClient(), host)
    assert code == 13
    assert not any(isinstance(c, tuple) and c[0] == "restore" for c in host.calls)
    assert any(isinstance(c, tuple) and c[0] == "terminate" for c in host.calls)


def test_busy_is_business_12_and_keeps_package():
    host = FakeHost()
    code = _run(_conf(), FakeClient(fail="op:pre_config_busy"), host)
    assert code == 12
    assert not any(c for c in host.calls if isinstance(c, tuple) and c[0] == "terminate")


def test_admin_down_is_11_and_keeps_package():
    host = FakeHost()
    code = _run(_conf(), FakeClient(fail=type("AdminDown", (), {})()), host)
    assert code == 11
    assert not any(c for c in host.calls if isinstance(c, tuple) and c[0] == "terminate")


def test_auth_rejected_is_10():
    code = _run(_conf(), FakeClient(fail="op:pre_config_auth"), FakeHost())
    assert code == 10


def test_exp_node_mismatch_is_10():
    bad = dict(PC, node="node8")
    code = _run(_conf(), FakeClient(pc=bad), FakeHost())
    assert code == 10


def test_mode_mismatch_is_12():
    bad = dict(PC, mode="se")
    code = _run(_conf(), FakeClient(pc=bad), FakeHost())
    assert code == 12


def test_sha_mismatch_refetches_then_fails():
    class ShaClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.payload = b"NOPE"          # не совпадает с sha256(b"NODE")
        async def fetch_chunk(self, offset, size):
            self.chunk_calls += 1
            return self.payload[offset:offset + size]
    client = ShaClient()
    code = _run(_conf(), client, FakeHost())
    assert code == 13
    assert client.chunk_calls == 2               # 2 попытки по 1 чанк -> перекачка была


def test_watch_timeout_live_process_rolls_back(monkeypatch):
    import services.deployer.agent.machine as M
    monkeypatch.setattr(M, "ROLLBACK_SEEN_TIMEOUT", 1.0)   # иначе фейк ждал бы 60с

    class NeverSeen(FakeClient):
        async def node_seen(self):
            return False
    host = FakeHost(backup=True)
    code = _run(_conf(deploy_timeout=2), NeverSeen(), host)
    assert code == 13
    assert any(isinstance(c, tuple) and c[0] == "restore" for c in host.calls)


def test_report_always_failing_still_returns_code():
    class DeadReport(FakeClient):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def report(self, data):
            self.attempts += 1
            raise RuntimeError("report channel down")

    client, host = DeadReport(), FakeHost()
    code = _run(_conf(), client, host)
    assert isinstance(code, int) and code == 0
    assert client.attempts == 6               # 3 ретрая × отчёты (pre_config, done)
    assert ("terminate", 0) in host.calls     # FSM всё равно дошёл до SELF_TERMINATE


def test_launch_exception_triggers_rollback():
    class LaunchFailHost(FakeHost):
        async def launch(self, pc):
            self.calls.append("launch")
            raise RuntimeError("spawn failed")

    client = FakeClient()
    host = LaunchFailHost(backup=True)
    code = _run(_conf(), client, host)
    assert code == 13
    assert any(isinstance(c, tuple) and c[0] == "restore" for c in host.calls)
    assert client.reports[-1]["stage"] in ("rolled_back", "failed")
    assert ("terminate", 13) in host.calls     # запись была -> cleanup


def test_connect_error_carries_uri_and_exc_type(monkeypatch):
    """connect failed обязан нести URI и тип исключения: голое
    «did not receive a valid HTTP response» не говорит, куда агент
    стучался (имя ноды вместо IP, чужой порт, прокси)."""
    from services.deployer.agent import machine as M

    class FakeHandshakeErr(Exception):
        pass

    async def _boom(*a, **k):
        raise FakeHandshakeErr("did not receive a valid HTTP response")

    monkeypatch.setattr(M.websockets, "connect", _boom)
    client = M.AgentApiClient(_conf(), retries=1, retry_delay=0)
    with pytest.raises(M.AdminUnreachable) as ei:
        asyncio.run(client.connect())
    msg = str(ei.value)
    assert "ws://10.0.0.1:9000" in msg          # куда стучался
    assert "FakeHandshakeErr" in msg            # чем отвалилось
    assert "did not receive a valid HTTP response" in msg  # исходный текст цел


def test_connect_wss_uses_unverified_ssl_context(monkeypatch):
    """wss к mesh-узлу с self-signed PKI: агент обязан передать websockets
    клиентский SSL-контекст без верификации (личность админа проверяет
    HMAC-конверт, не TLS-цепочка; прецедент — webpanel rpc_client fallback).
    Иначе exit=11 SSLCertVerificationError на любом wss-админе."""
    import ssl as _ssl
    from services.deployer.agent import machine as M

    seen: dict = {}

    async def _boom(*a, **k):
        seen.update(k)
        raise RuntimeError("nope")

    monkeypatch.setattr(M.websockets, "connect", _boom)
    client = M.AgentApiClient(_conf(admin_ws="wss://sysadmin-pc:9000"),
                              retries=1, retry_delay=0)
    with pytest.raises(M.AdminUnreachable):
        asyncio.run(client.connect())
    ctx = seen.get("ssl")
    assert isinstance(ctx, _ssl.SSLContext)
    assert ctx.verify_mode == _ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_connect_ws_passes_no_ssl_context(monkeypatch):
    """ws (без TLS) — никакого ssl-аргумента, поведение не меняется."""
    from services.deployer.agent import machine as M

    seen: dict = {}

    async def _boom(*a, **k):
        seen.update(k)
        raise RuntimeError("nope")

    monkeypatch.setattr(M.websockets, "connect", _boom)
    client = M.AgentApiClient(_conf(admin_ws="ws://10.0.0.1:9000"),
                              retries=1, retry_delay=0)
    with pytest.raises(M.AdminUnreachable):
        asyncio.run(client.connect())
    assert "ssl" not in seen


def test_agent_conf_has_no_tls_fields():
    """Вариант A: triple удалён — у AgentConf нет tls_*-полей, старые
    пакеты с tls_*-ключами грузятся (ключи игнорируются)."""
    c = _conf()
    assert not hasattr(c, "tls_ca_cert")
    c2 = _conf(tls_ca_cert="ca_cert.pem", tls_node_cert="node_cert.pem",
               tls_node_key="node_key.pem")
    assert not hasattr(c2, "tls_ca_cert")


def test_connect_wss_without_client_cert(tmp_path, monkeypatch):
    """Вариант A: агент идёт на bootstrap-порт БЕЗ клиентского серта —
    контекст CERT_NONE, личность админа проверяет HMAC-конверт."""
    import ssl as _ssl
    from services.deployer.agent import machine as M

    seen: dict = {}

    async def _boom(*a, **k):
        seen.update(k)
        raise RuntimeError("nope")

    monkeypatch.setattr(M.websockets, "connect", _boom)
    conf = _conf(admin_ws="wss://sysadmin-pc:9100")
    client = M.AgentApiClient(conf, retries=1, retry_delay=0)
    with pytest.raises(M.AdminUnreachable):
        asyncio.run(client.connect())
    ctx = seen.get("ssl")
    assert isinstance(ctx, _ssl.SSLContext)
    assert ctx.verify_mode == _ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_preflight_reports_banner_and_ips():
    """Локальный TCP-сервер с HTTP-ответом: ips, tcp=open, banner виден."""
    import socket as _sock
    import threading
    from services.deployer.agent import machine as M

    srv = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    srv.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _serve():
        try:
            c, _ = srv.accept()
            c.settimeout(5)
            try:
                c.recv(256)
            except Exception:
                pass
            c.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            c.close()
        except Exception:
            pass
        finally:
            try:
                srv.close()
            except Exception:
                pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    rep = M.preflight_report("127.0.0.1", port)
    t.join(timeout=10)
    assert rep["ips"] == ["127.0.0.1"]
    assert rep["tcp"] == "open"
    assert "404" in (rep["banner"] or "")


def test_preflight_reports_closed_port():
    """Закрытый порт: tcp несёт причину, баннера нет, исключений наружу нет."""
    import socket as _sock
    from services.deployer.agent import machine as M

    s = _sock.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    rep = M.preflight_report("127.0.0.1", port)
    assert rep["banner"] is None
    assert "Refused" in (rep["tcp"] or "")


def test_preflight_reports_proxy_env(monkeypatch):
    """Прокси-окружение процесса попадает в отчёт (подозреваемый №1
    при «did not receive a valid HTTP response» с тишиной на ноде)."""
    from services.deployer.agent import machine as M

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:3128")
    rep = M.preflight_report("127.0.0.1", 9)
    assert rep["proxy"].get("HTTPS_PROXY") == "http://proxy.local:3128"


def test_tls_cert_probe_names_squatter(tmp_path):
    """Порт молчит по HTTP, но делает TLS: зонд отдаёт subject/issuer
    самоподписанного серта — имя сквоттера порта."""
    import datetime
    import socket as _sock
    import ssl as _ssl
    import threading
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from services.deployer.agent import machine as M

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "squatter-test")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc)
                              - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc)
                             + datetime.timedelta(days=30))
            .sign(key, hashes.SHA256()))
    cert_f, key_f = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_f.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_f.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.TraditionalOpenSSL,
                                         serialization.NoEncryption()))
    srv = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    srv.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    sctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(certfile=str(cert_f), keyfile=str(key_f))

    def _serve():
        try:
            c, _ = srv.accept()
            try:
                t = sctx.wrap_socket(c, server_side=True)
                t.do_handshake()
                t.close()
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            try:
                srv.close()
            except Exception:
                pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    rep = M.tls_cert_probe("127.0.0.1", port)
    t.join(timeout=10)
    assert "squatter-test" in (rep.get("subject") or "")
    assert "squatter-test" in (rep.get("issuer") or "")


def test_tls_cert_probe_reports_non_tls():
    """Порт без TLS: зонд возвращает error, исключений наружу нет."""
    import socket as _sock
    import threading
    from services.deployer.agent import machine as M

    srv = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    srv.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _serve():
        try:
            c, _ = srv.accept()
            c.settimeout(5)
            try:
                c.recv(256)
            except Exception:
                pass
            c.close()                       # молча вешаем трубку, как сквоттер
        except Exception:
            pass
        finally:
            try:
                srv.close()
            except Exception:
                pass

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    rep = M.tls_cert_probe("127.0.0.1", port)
    t.join(timeout=10)
    assert rep.get("subject") is None and rep.get("error")
