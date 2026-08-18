import logging
import os
import shutil
import traceback
from pathlib import Path

from sign.signer import sign_exe

SIGNED_DIR = Path('dist')

# --------------------------------------------------------------------------- #
#  Общие hidden-imports, необходимые проекту
# --------------------------------------------------------------------------- #
hidden_imports = [
    # Зависимости
    'fastapi',
    'uvicorn',
    'websockets',
    'pydantic',
    'pydantic_settings',
    'streamlit',
    'watchdog',
    'cryptography',
    'msgpack',
    'yaml',
    'pyparsing',
    'click',
    'anyio',
    'colorama',
    'tornado',
    'tornado.web',
    'tornado.ioloop',
    'streamlit.web.cli',
    'streamlit.web.bootstrap',
    # Дополнительные модули для корректной работы
    'src.networking.protocol',
    'src.internal_modules.context',
    'src.networking.router',
    'src.networking.node_connector',
    'src.networking.neighbor_table',
    'colorama.ansitowin32',
    'colorama.win32',
]

# --------------------------------------------------------------------------- #
#  Сборка основного приложения P2P_Core (без webpanel)
# --------------------------------------------------------------------------- #
main_args = [
    'main.py',
    '--onefile',
    '--noupx',
    '--noconsole',
    '-i=src/icon.ico',
    '--name=P2P_Core',
    '--clean',
]

for mod in hidden_imports:
    main_args.append(f'--hidden-import={mod}')

main_args += [
    '--collect-all=services',
    '--collect-all=src',
    '--collect-all=streamlit',
    '--collect-all=click',
    '--collect-all=watchdog',
    '--collect-all=toml',
]


if __name__ == '__main__':
    log = logging.getLogger('Compiler')
    # Основное приложение P2P_Core
    log.info("Building P2P_Core...")
    try:
        os.popen(f"pyinstaller {' '.join(main_args)} ").read()
        log.info("Build successfully!")
    except Exception:
        log.info("Build failed")
        traceback.format_exc()

    # os.rename('dist/P2P_Core.exe', 'dist/compiled_P2P_Core.exe')
    shutil.copy('dist/P2P_Core.exe', 'dist/compiled_P2P_Core.exe')
    log.info("Signing P2P_Core...")

    os.makedirs(SIGNED_DIR, exist_ok=True)
    sign_exe(Path('dist/compiled_P2P_Core.exe'), SIGNED_DIR)
    shutil.copy('dist/signed_compiled_P2P_Core.exe','dist/P2P_Core.exe')
    # os.rename('dist/signed_compiled_P2P_Core.exe','dist/P2P_Core.exe')



