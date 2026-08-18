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

DEFAULT_PANEL_PORT = 8501
SERVICES_DIR = Path(__file__).parent.parent


class WebPanel(ModuleGeneric):
    def __init__(self, name, context):
        super().__init__(name, context)
        # self._process: asyncio.subprocess.Process | None = None
        self._panel_port = DEFAULT_PANEL_PORT

    async def start(self):
        # app_path = Path(__file__).parent / 'streamlit_app.py'
        # if not app_path.exists():
        #     log.error(f'streamlit_app.py not found: {app_path}')
        #     return
        log.info(
            f'Streamlit booting... '
        )

        env = {
            **os.environ,
            'P2P_NODE_ID': self.ctx.NODE,
            'P2P_WS_PORT': str(self.ctx.config.network.port),
            'P2P_WS_HOST': self.ctx.config.network.host,
            'P2P_PANEL_PORT': str(self._panel_port),
            'P2P_PROJECT_ROOT': str(Path(__file__).parent.parent.parent),
            'PYTHONWARNINGS': 'ignore::DeprecationWarning',
        }


        # self._process = await asyncio.create_subprocess_exec(
        #     sys.executable, '-m', 'streamlit', 'run', str(app_path),
        #     '--server.port', str(self._panel_port),
        #     '--server.headless', 'true',
        #     '--browser.gatherUsageStats', 'false',
        #     env=env,
        #     stdout=asyncio.subprocess.PIPE,
        #     stderr=asyncio.subprocess.PIPE,
        # )

        try:
            # PyInstaller creates a temp folder and stores path in _MEIPASS
            base_path = Path(sys._MEIPASS) / 'services' / 'webpanel'
        except Exception as e:
            base_path = os.path.abspath("./services/webpanel")
            print(e)

        path = os.path.join(base_path, 'streamlit_app.py')

        log.info(
            f'Streamlit started on port {self._panel_port} '
        )

        args = [
            "streamlit",
            "run",
            path,
            "--global.developmentMode=false",
            '--server.port', str(self._panel_port),

            '--browser.gatherUsageStats', 'false',
            # env,
        ]
        subprocess.Popen(args)

        # sys.exit(stcli.main())

    # async def stop(self):
    #     if self._process:
    #         self._process.terminate()
    #         try:
    #             await asyncio.wait_for(self._process.wait(), timeout=5)
    #         except asyncio.TimeoutError:
    #             self._process.kill()
    #         log.info('Streamlit stopped')

    async def stop(self):
        for p in psutil.process_iter():
            if p.name() == 'streamlit.exe':
                p.kill()

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
