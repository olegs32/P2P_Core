# services/deployer/agent/main.py
"""Entry одноразового deploy-агента: CLI + exit-codes.

Прямой запуск скрипта (dev): python services/deployer/agent/main.py --conf ...
Frozen: DeployAgent[_se].exe --conf ... [--system-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

if __package__ in (None, ""):                     # абсолютные импорты (см. Task 6, Step 3.2)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from services.deployer.agent.machine import (    # noqa: E402
    EXIT_BUSINESS, AgentApiClient, DeployMachine, HostOpsWindows,
)
from services.deployer.agent.package import AgentConf  # noqa: E402

log = logging.getLogger("deploy.agent")


def _default_conf_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "agent.conf"
    return Path(sys.argv[0]).resolve().parent / "agent.conf"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="P2P deploy agent")
    p.add_argument("--conf", default=None, help="путь к agent.conf (рядом с exe)")
    p.add_argument("--system-run", action="store_true",
                   help="инстанс, запущенный задачей schtasks под SYSTEM")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    conf_path = Path(args.conf) if args.conf else _default_conf_path()
    try:
        raw = json.loads(conf_path.read_text(encoding="utf-8"))
        conf = AgentConf.from_dict(raw)
    except FileNotFoundError:
        log.error("agent.conf не найден: %s", conf_path)
        return EXIT_BUSINESS
    except (ValueError, json.JSONDecodeError) as e:
        log.error("битый agent.conf: %s", e)
        return EXIT_BUSINESS
    client = AgentApiClient(conf)
    host = HostOpsWindows(conf, conf_path, system_run=args.system_run)

    async def _run() -> int:
        try:
            return await DeployMachine(conf, client, host).run()
        finally:
            # закрытие клиента в ТОМ ЖЕ loop: второй asyncio.run мог получить
            # уже закрытый loop/sock (ws.close() падал и глушился except'ом)
            try:
                await client.close()
            except Exception:
                pass

    return int(asyncio.run(_run()))


if __name__ == "__main__":
    os._exit(main())
