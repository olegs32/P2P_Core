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
    # '--noupx',
    # '--noconsole',
    '-i=src/icon.ico',
    '--clean',
]

for mod in hidden_imports:
    main_args.append(f'--hidden-import={mod}')

main_args += [
    '--collect-all=services',
    '--collect-all=src',

    '--collect-all=click',
    '--collect-all=watchdog',
    '--collect-all=toml',
]


def build(name, ui=True):
    log.info("Building P2P_Core...")
    if ui:
        args = [

            '--collect-binaries=streamlit',
            '--collect-datas=streamlit',
            '--recursive-copy-metadata=streamlit',
            '--hidden-import=streamlit.runtime.scriptrunner.magic_funcs',

        ]
        for mod in args:
            main_args.append(mod)


    else:
        main_args.append('--exclude-module=services.webpanel')

    main_args.append(f'--name={name}', )
    try:
        os.popen(f"pyinstaller {' '.join(main_args)} ").read()
        log.info("Build successfully!")
    except Exception:
        log.info("Build failed")
        traceback.format_exc()


if __name__ == '__main__':
    log = logging.getLogger('Compiler')
    os.makedirs(SIGNED_DIR, exist_ok=True)

    # Основное приложение с UI P2P_Core
    build('P2P_Core.exe', True)
    log.info("Signing admin_P2P_Core...")
    sign_exe(Path('dist/P2P_Core.exe'), SIGNED_DIR)
    shutil.copy('dist/signed_P2P_Core.exe', 'dist/WebUI_P2P_Core.exe')

    # Node without UI P2P_Core
    build('compiled_P2P_Core.exe', False)
    log.info("Signing admin_P2P_Core...")
    sign_exe(Path('dist/P2P_Core.exe'), SIGNED_DIR)
    shutil.copy('dist/signed_P2P_Core.exe', 'dist/Node_P2P_Core.exe')
