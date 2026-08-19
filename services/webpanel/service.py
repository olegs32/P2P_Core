# services/webpanel/service.py — WebPanel сервис
# Запускает Streamlit subprocess, предоставляет @rpc методы для UI

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

import psutil

from src.internal_modules.base import ModuleGeneric
from services.rpc import rpc
import streamlit.web.cli as stcli

log = logging.getLogger('WebPanel')
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.state.session_state_proxy").setLevel(logging.ERROR)


DEFAULT_PANEL_PORT = 8501
SERVICES_DIR = Path(__file__).parent.parent


class WebPanel(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)
        self._streamlit_process: asyncio.subprocess.Process | None = None
        self._panel_port = DEFAULT_PANEL_PORT

    async def start(self):
        log.info(
            f'Streamlit booting... '
        )


        try:
            # PyInstaller создает временную папку и сохраняет путь в _MEIPASS
            base_path = Path(sys._MEIPASS) / 'services' / 'webpanel'
            is_frozen = True
        except Exception as e:
            base_path = os.path.abspath("./services/webpanel")
            is_frozen = False
            print(e)

        path = os.path.join(base_path, 'streamlit_app.py')

        args = [
            sys.executable,  # В dev режиме это python.exe, в билде — это собранный .exe
            "-m",
            "streamlit",
            "run",
            path,
            "--global.developmentMode=false",
            '--server.port', str(self._panel_port),
            "--server.fileWatcherType=none",
            "--server.headless=true",
            '--browser.gatherUsageStats', 'false',
        ]

        # Важно: передаем текущее окружение (env), чтобы подпроцесс унаследовал пути,
        # особенно важно для корректной работы PyInstaller (переменные вроде PYTHONPATH)
        env = {
            **os.environ,
            'P2P_NODE_ID': self.ctx.NODE,
            'P2P_WS_PORT': str(self.ctx.config.network.port),
            'P2P_WS_HOST': self.ctx.config.network.host,
            'P2P_PANEL_PORT': str(self._panel_port),
            'P2P_PROJECT_ROOT': str(Path(__file__).parent.parent.parent),
            'PYTHONWARNINGS': 'ignore::DeprecationWarning',
        }


        self._streamlit_process = subprocess.Popen(
            args,
            env=env,
            stdout=sys.stdout,  # <--- Временно перенаправляем в вашу основную консоль
            stderr=sys.stderr  # <--- Чтобы увидеть traceback ошибки

        )
        log.info(
            f'Streamlit started on port {self._panel_port} '
        )
        # sys.exit(stcli.main())


    async def stop(self):
        """Корректно завершает процесс Streamlit"""
        if hasattr(self, '_streamlit_process') and self._streamlit_process:
            print("[WebPanel] Stopping Streamlit process...")

            # 1. Посылаем сигнал мягкого завершения (SIGTERM)
            self._streamlit_process.terminate()

            # 2. Ждем асинхронно пару секунд, пока процесс закроется сам
            for _ in range(10):
                if self._streamlit_process.poll() is not None:
                    break
                await asyncio.sleep(0.2)

            # 3. Если процесс всё еще жив, убиваем его жестко (SIGKILL)
            if self._streamlit_process.poll() is None:
                print("[WebPanel] Streamlit didn't respond. Forcing kill...")
                self._streamlit_process.kill()

            print("[WebPanel] Streamlit process terminated.")
            self._streamlit_process = None

    # ------------------------------------------------------------------ #
    #  RPC методы для Streamlit UI
    # ------------------------------------------------------------------ #

    @rpc
    def node_status(self, data: dict):
        """Полное состояние узла — для главной страницы."""
        nt = self.ctx.network.neighbor_table
        nm = self.ctx.network.nodes_manager
        return {
            'node_id': self.ctx.NODE,
            'host': self.ctx.config.network.host,
            'port': self.ctx.config.network.port,
            'connected': [n.model_dump() for n in nt.connected()],
            'known': [n.model_dump() for n in nt.known()],
            'all_services': list(self.ctx.services.services.keys()),
            'connected_count': len(nt.connected()),
            'known_count': len(nt.known()),
        }

    @rpc
    def discover_ui_services(self, data: dict):
        """Найти сервисы с web_ui.py — для sidebar навигации."""
        result = []
        for svc_dir in SERVICES_DIR.iterdir():
            if not svc_dir.is_dir() or svc_dir.name.startswith('_'):
                continue
            if svc_dir.name == 'webpanel':
                continue
            if (svc_dir / 'web_ui.py').exists():
                result.append(svc_dir.name)
        return sorted(result)
