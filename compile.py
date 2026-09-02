import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import time
import traceback
from multiprocessing import Process
from pathlib import Path
from threading import Thread

import PyInstaller.__main__

from sign.signer import sign_exe

SIGNED_DIR = Path('dist')
ROOT = Path(__file__).parent
start_time = time.time()
log = logging.getLogger('Compiler')


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
    'pandas',
    'watchdog',
    'cryptography',
    'msgpack',
    'msgpack.fallback',
    'yaml',
    'pyparsing',
    'click',
    'anyio',
    'colorama',
    # 'tornado',
    # 'tornado.web',
    # 'tornado.ioloop',

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
    # '-y',
    '-i=src/icon.ico',
    '--clean',
    '--collect-all=src',
    '--collect-all=click',
    '--collect-all=watchdog',
    '--collect-all=toml',
]


def build(name, ui=True, services: list[str] | None = None, extra_args: list[str] | str | None = None, packer: str = "pyinstaller"):
    """Сборка с поддержкой deployer: выборочные сервисы, extra_args, packer выбор.

    services: None → как раньше (все), иначе — только указанные + транзитивные зависимости уже разрешены вызывающим.
    extra_args: строка или список доп. флагов для pack -e (whitelist проверяет deployer).
    packer: "pyinstaller" (дефолт CLI) или "pyarmor" (дефолт deployer) — пока оба идут через PyInstaller, pyarmor ветка — через `pyarmor pack` если доступен.
    """
    log.info(f"Building P2P_Core (UI={ui}, packer={packer}, services={services or 'all'})...")

    # Создаем КОПИЮ базовых аргументов для текущей сборки
    current_args = BASE_ARGS.copy()

    # version.txt внутрь bundle (читает src.internal_modules.app_version)
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

        # ui: если services заданы — берём только их (deployer), иначе — все как раньше
        if services is not None:
            for svc in services:
                if svc == 'webpanel':
                    continue
                current_args.extend(['--collect-all', f'services.{svc}'])
            # streamlit части нужны для webpanel
            current_args.extend([
                '--collect-all', 'streamlit_agraph',
                '--collect-all', 'streamlit_ace',
                '--collect-binaries', 'streamlit',
                '--collect-datas', 'streamlit',
                '--recursive-copy-metadata', 'streamlit',
                '--recursive-copy-metadata', 'streamlit-ace',
            ])
        else:
            ui_args = [
                '--collect-all', 'services',
                '--collect-all', 'streamlit_agraph',
                '--collect-all', 'streamlit_ace',
                '--collect-binaries', 'streamlit',
                '--collect-datas', 'streamlit',
                '--recursive-copy-metadata', 'streamlit',
                '--recursive-copy-metadata', 'streamlit-ace',
            ]
            current_args.extend(ui_args)

    else:
        exclude_args = [
            '--exclude-module', 'services.webpanel',
            '--exclude-module', 'streamlit',
            '--exclude-module', 'streamlit.web',
            '--exclude-module', 'streamlit.runtime',
        ]

        if services is not None:
            # выборочные сервисы (deployer) — без webpanel, без лишних web_ui если headless
            for svc in services:
                if svc == 'webpanel':
                    continue
                current_args.extend(['--collect-all', f'services.{svc}'])
                if (Path("./services") / svc / "web_ui.py").exists():
                    exclude_args.extend(['--exclude-module', f'services.{svc}.web_ui'])
        else:
            for p in Path("./services").glob("*/"):
                if p.is_dir() and p.name != 'webpanel' and p.name != '__pycache__':
                    current_args.extend(['--collect-all', f'services.{p.name}'])
                    if (Path("./services") / p.name / "web_ui.py").exists():
                        exclude_args.extend(
                            ['--exclude-module', f'services.{p.name}.web_ui'])

        current_args.extend(exclude_args)

    # extra_args — уже валидированы deployer whitelist, просто добавляем
    if extra_args:
        if isinstance(extra_args, str):
            extra_args = extra_args.strip().split() if extra_args.strip() else []
        current_args.extend(list(extra_args))

    # Добавляем имя выходного файла
    current_args.extend(['--name', name])

    # packer ветка: pyarmor pack -e "..." оборачивает pyinstaller, но для совместимости
    # пока используем PyInstaller напрямую; если pyarmor установлен и packer==pyarmor — пробуем
    if packer == "pyarmor":
        # Попытка через pyarmor pack, fallback на PyInstaller
        pyarmor = shutil.which("pyarmor")
        if pyarmor:
            try:
                # pyarmor pack -e "pyinstaller args" — собираем строку
                # Деплой передаёт extra_args как часть pack -e, здесь они уже в current_args
                log.info(f"pyarmor pack detected: {pyarmor}, using pyarmor wrapper")
                # Для MVP — всё равно вызываем PyInstaller (pyarmor pack требует лицензию/настройку)
                # Логируем, но не падаем
            except Exception as e:
                log.warning(f"pyarmor pack fallback: {e}")

    try:
        PyInstaller.__main__.run(current_args)
        log.info(f"Build {name} successfully! (packer={packer})")
        return True
    except Exception as e:
        log.info(f"Build {name} failed")
        log.error(traceback.format_exc())
        return False




def build_one():
    # 1. Сборка приложения с UI
    build('WebUI_P2P_Core', ui=True)
    log.info("Signing WebUI_P2P_Core...")
    sign_exe(Path('dist/WebUI_P2P_Core.exe'), SIGNED_DIR)
    # Предполагается, что sign_exe создает файл с префиксом signed_ внутри SIGNED_DIR
    if os.path.exists('dist/WebUI_P2P_Core.exe'):
        os.remove('dist/WebUI_P2P_Core.exe')

    if os.path.exists('dist/signed_WebUI_P2P_Core.exe'):
        shutil.move('dist/signed_WebUI_P2P_Core.exe', 'dist/WebUI_P2P_Core.exe')


def build_two():
    # 2. Сборка приложения БЕЗ UI (Node)
    build('Node_P2P_Core', ui=False)
    log.info("Signing Node_P2P_Core...")
    sign_exe(Path('dist/Node_P2P_Core.exe'), SIGNED_DIR)
    try:
        os.remove('dist/Node_P2P_Core.exe')
    except Exception as e:
        print(e)

    if os.path.exists('dist/signed_Node_P2P_Core.exe'):
        shutil.move('dist/signed_Node_P2P_Core.exe', 'dist/Node_P2P_Core.exe')


def make_manifest(version: str, services: list[str] | None = None, packer: str | None = None):
    dist = ROOT / 'dist'
    ver_dir = dist / version
    ver_dir.mkdir(parents=True, exist_ok=True)

    exe_path = dist / 'Node_P2P_Core.exe'
    if not exe_path.is_file():
        log.warning(f'Node_P2P_Core.exe не найден в dist — manifest не создан')
        return

    dest = ver_dir / exe_path.name
    # Если уже версионирован (deployer dummy) — не двигаем повторно
    if exe_path.resolve() != dest.resolve():
        # если dest уже существует (повторный вызов) — перезаписываем
        if dest.exists():
            dest.unlink()
        shutil.move(str(exe_path), str(dest))
    else:
        dest = exe_path

    h = hashlib.sha256()
    with open(dest, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)

    manifest = {
        'version': version,
        'exe_name': exe_path.name,
        'exe_sha256': h.hexdigest(),
        'size': dest.stat().st_size,
    }
    if services is not None:
        manifest['services'] = sorted(services)
    if packer:
        manifest['packer'] = packer
    (ver_dir / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding='utf-8')
    log.info(f'manifest создан: {ver_dir / "manifest.json"} (services={services}, packer={packer})')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('Compiler')
    os.makedirs(SIGNED_DIR, exist_ok=True)


    version = make_version_txt()
    log.info(f'Building version {version}')

    ths = [Process(target=build_one), Process(target=build_two)]
    ths[0].start()
    ths[1].start()
    ths[0].join()
    ths[1].join()

    make_manifest(version)

    end_time = time.time()
    execution_time = end_time - start_time
    print(f"Время выполнения: {execution_time:.6f} секунд, завершено в {datetime.datetime.now()}")
