import os


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
    # Основное приложение P2P_Core
    print("Building P2P_Core...")
    os.popen(f"pyinstaller {' '.join(main_args)} ").read()


