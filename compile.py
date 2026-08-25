import logging
import os
import re
import shutil
import time
import traceback
from pathlib import Path

import PyInstaller.__main__

from sign.signer import sign_exe

SIGNED_DIR = Path('dist')
ROOT = Path(__file__).parent
start_time = time.time()


# --------------------------------------------------------------------------- #
#  Версия: VERSION (семантика) + BUILD_NUMBER (счётчик) → version.txt в бандл
# --------------------------------------------------------------------------- #

def make_version_txt() -> str:
    ver_file = ROOT / 'VERSION'
    semver = ver_file.read_text(encoding='utf-8').strip() \
        if ver_file.exists() else '0.0.0'
    if not re.match(r'^\d+\.\d+\.\d+$', semver):
        raise SystemExit(f'VERSION файл задан неверно: {semver!r} '
                         f'(нужно MAJOR.MINOR.PATCH)')

    bn_file = ROOT / 'BUILD_NUMBER'
    try:
        build = int(bn_file.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        build = 0
    build += 1
    bn_file.write_text(str(build), encoding='utf-8')

    version = f'{semver}-build{build}'
    (ROOT / 'version.txt').write_text(version, encoding='utf-8')
    return version

# --------------------------------------------------------------------------- #
#  Общие hidden-imports, необходимые проекту
# --------------------------------------------------------------------------- #
BASE_HIDDEN_IMPORTS = [
    'fastapi',
    'uvicorn',
    'websockets',
    'pydantic',
    'pydantic_settings',
    'watchdog',
    'cryptography',
    'msgpack',
    'msgpack.fallback',
    'yaml',
    'pyparsing',
    'click',
    'anyio',
    'colorama',
    'tornado',
    'tornado.web',
    'tornado.ioloop',

    'src.networking.protocol',
    'src.internal_modules.context',
    'src.networking.router',
    'src.networking.node_connector',
    'src.networking.neighbor_table',
    'colorama.ansitowin32',
    'colorama.win32',
]

# --------------------------------------------------------------------------- #
#  Базовые аргументы (неизменяемый шаблон)
# --------------------------------------------------------------------------- #
BASE_ARGS = [
    'main.py',
    '--onefile',
    '-i=src/icon.ico',
    '--clean',
    '--collect-all=src',
    '--collect-all=click',
    '--collect-all=watchdog',
    '--collect-all=toml',
]


def build(name, ui=True):
    log.info(f"Building P2P_Core (UI={ui})...")

    # Создаем КОПИЮ базовых аргументов для текущей сборки
    current_args = BASE_ARGS.copy()

    # version.txt внутрь бандла (читает src.internal_modules.app_version)
    version_txt = ROOT / 'version.txt'
    if version_txt.exists():
        current_args.extend(['--add-data', f'{version_txt};.'])

    # Добавляем базовые скрытые импорты
    for mod in BASE_HIDDEN_IMPORTS:
        current_args.extend(['--hidden-import', mod])

    if ui:
        hidden_imports_ui = [
            'streamlit.web.cli',
            'streamlit.web.bootstrap',
            'streamlit.runtime.scriptrunner.magic_funcs'
        ]
        for mod in hidden_imports_ui:
            current_args.extend(['--hidden-import', mod])

        # Собираем нужные части services и streamlit отдельно
        ui_args = [
            '--collect-all', 'services',
            '--collect-binaries', 'streamlit',
            '--collect-datas', 'streamlit',
            '--recursive-copy-metadata', 'streamlit',
        ]
        current_args.extend(ui_args)

    else:
        # Полностью отсекаем streamlit и webpanel
        exclude_args = [
            '--exclude-module', 'services.webpanel',
            '--exclude-module', 'streamlit',
            '--exclude-module', 'streamlit.web',
            '--exclude-module', 'streamlit.runtime',
        ]

        # Собираем только те сервисы, которые НЕ webpanel
        for p in Path("./services").glob("*/"):
            if p.is_dir() and p.name != 'webpanel' and p.name != '__pycache__':
                current_args.extend(['--collect-all', f'services.{p.name}'])
            if os.path.exists(Path(f"./services/{p}/web_ui.py")):
                exclude_args.extend(['--exclude-module', f"./services/{p}/web_ui.py", ])

        current_args.extend(exclude_args)

    # Добавляем имя выходного файла
    current_args.extend(['--name', name])

    try:
        # Передаем изолированный список аргументов
        PyInstaller.__main__.run(current_args)
        log.info(f"Build {name} successfully!")
    except Exception as e:
        log.info(f"Build {name} failed")
        log.error(traceback.format_exc())


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('Compiler')
    os.makedirs(SIGNED_DIR, exist_ok=True)

    version = make_version_txt()
    log.info(f'Building version {version}')

    # 1. Сборка приложения с UI
    build('WebUI_P2P_Core', ui=True)
    log.info("Signing WebUI_P2P_Core...")
    sign_exe(Path('dist/WebUI_P2P_Core.exe'), SIGNED_DIR)
    # Предполагается, что sign_exe создает файл с префиксом signed_ внутри SIGNED_DIR
    if os.path.exists('dist/signed_WebUI_P2P_Core.exe'):
        shutil.move('dist/signed_WebUI_P2P_Core.exe', 'dist/WebUI_P2P_Core.exe')

    # 2. Сборка приложения БЕЗ UI (Node)
    build('Node_P2P_Core', ui=False)
    log.info("Signing Node_P2P_Core...")
    sign_exe(Path('dist/Node_P2P_Core.exe'), SIGNED_DIR)
    if os.path.exists('dist/signed_Node_P2P_Core.exe'):
        shutil.move('dist/signed_Node_P2P_Core.exe', 'dist/Node_P2P_Core.exe')

    end_time = time.time()
    execution_time = end_time - start_time
    print(f"Время выполнения: {execution_time:.6f} секунд")