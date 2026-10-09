import argparse
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
    # tkinter не используется в проекте — исключаем, иначе pyi_rth__tkinter падает
    # FileNotFoundError: Tcl data directory "..._tcl_data" not found
    '--exclude-module', 'tkinter',
    '--exclude-module', '_tkinter',
    '--exclude-module', 'FixTk',
    '--exclude-module', 'tcl',
    '--exclude-module', 'tk',
    '--exclude-module', 'Tkinter',
]


def detect_flavor(explicit: str | None, has_se_dir: bool) -> str:
    """SE/open для любой цели сборки: явный флаг > наличие src/se на билд-машине."""
    if explicit in ("se", "open"):
        return explicit
    return "se" if has_se_dir else "open"


def flavor_excludes(flavor: str) -> list[str]:
    """open = полное отсутствие SE в бинарнике (общий флаг для node/webui/agent)."""
    return ["--exclude-module", "src.se"] if flavor == "open" else []


AGENT_HIDDEN = [
    "websockets", "msgpack", "msgpack.fallback", "cryptography",
    "cryptography.hazmat.primitives.ciphers.aead", "pydantic",
    "src.networking.protocol", "src.networking.neighbor_table",
    "services.deployer.agent.package", "services.deployer.agent.machine",
]

AGENT_EXCLUDES = [
    "--exclude-module", "fastapi", "--exclude-module", "uvicorn",
    "--exclude-module", "streamlit", "--exclude-module", "pandas",
    "--exclude-module", "watchdog", "--exclude-module", "tkinter",
    "--exclude-module", "_tkinter", "--exclude-module", "FixTk",
]

# модули deployer, не нужные агенту (агент = CLI, не RPC-сервис)
_DEPLOYER_SKIP = ("service", "web_ui", "agent_api", "key_registry")


def service_excludes() -> list[str]:
    """Исключения соседних сервисов — поимённо, агенту нужны только services.deployer.agent.*.

    Пакетный --exclude-module services недопустим: PyInstaller гасит подмодули
    (hidden-import services.deployer.agent.* → "not found"), поэтому исключаем
    каждое имя отдельно, а родительские пакеты services/services.deployer оставляем.
    """
    args: list[str] = []
    services_dir = Path("services")
    if services_dir.is_dir():
        for p in sorted(services_dir.iterdir()):
            if p.is_dir() and p.name != "deployer" and p.name != "__pycache__":
                args += ["--exclude-module", f"services.{p.name}"]
        for mod in _DEPLOYER_SKIP:
            if (services_dir / "deployer" / f"{mod}.py").is_file():
                args += ["--exclude-module", f"services.deployer.{mod}"]
    return args


def agent_args(flavor: str) -> list[str]:
    """Полный список аргументов PyInstaller для DeployAgent."""
    args = [
        "services/deployer/agent/main.py",
        "--onefile", "--clean",
        "--paths", ".",
        "--name", "DeployAgent_se" if flavor == "se" else "DeployAgent",
    ] + flavor_excludes(flavor) + AGENT_EXCLUDES + service_excludes()
    for mod in AGENT_HIDDEN:
        args += ["--hidden-import", mod]
    return args


def build(name, ui=True, services: list[str] | None = None, extra_args: list[str] | str | None = None, packer: str = "pyinstaller", flavor: str = "se"):
    """Сборка с поддержкой deployer: выборочные сервисы, extra_args, packer выбор.

    services: None → как раньше (все), иначе — только указанные + транзитивные зависимости уже разрешены вызывающим.
    extra_args: строка или список доп. флагов для pack -e (whitelist проверяет deployer).
    packer: "pyinstaller" (дефолт CLI) или "pyarmor" (дефолт deployer) — пока оба идут через PyInstaller, pyarmor ветка — через `pyarmor pack` если доступен.
    flavor: "se"/"open" — open-сборка не включает src.se.
    """
    log.info(f"Building P2P_Core (UI={ui}, packer={packer}, services={services or 'all'}, flavor={flavor})...")

    # Создаем КОПИЮ базовых аргументов для текущей сборки
    current_args = BASE_ARGS.copy()
    current_args.extend(flavor_excludes(flavor))

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
                if svc in ('webpanel', 'deployer', ):
                    continue
                current_args.extend(['--collect-all', f'services.{svc}'])
                # if (Path("./services") / svc / "web_ui.py").exists():
                #     exclude_args.extend(['--exclude-module', f'services.{svc}.web_ui'])
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

    # packer — строго что выбрано, без fallback (требование deployer)
    if packer == "pyarmor":
        pyarmor = shutil.which("pyarmor")
        if not pyarmor:
            log.error("pyarmor not found but packer='pyarmor' requested — build failed (no fallback)")
            return False
        log.info(f"pyarmor pack detected: {pyarmor}")
        # pyarmor pack -e "pyinstaller ..." — пока используем PyInstaller напрямую,
        # но отсутствие pyarmor уже ошибка (выше). Лицензия/настройка — следующий этап.

    if packer not in ("pyinstaller", "pyarmor"):
        log.error(f"unknown packer {packer!r}")
        return False

    try:
        PyInstaller.__main__.run(current_args)
        log.info(f"Build {name} successfully! (packer={packer})")
        return True
    except Exception as e:
        log.info(f"Build {name} failed")
        log.error(traceback.format_exc())
        return False




def sign_in_place(name: str) -> bool:
    """Подпись exe в dist: signed_<name>.exe → <name>.exe (как в старых build_one/build_two)."""
    exe = Path(f'dist/{name}.exe')
    if not exe.is_file():
        log.error(f'{exe} не найден — подпись пропущена')
        return False
    sign_exe(exe, SIGNED_DIR)
    signed = Path(f'dist/signed_{name}.exe')
    if signed.exists():
        exe.unlink()
        shutil.move(str(signed), str(exe))
        return True
    log.warning(f'signed_{name}.exe не создан — {exe} остался неподписанным')
    return False


def build_agent(flavor: str) -> bool:
    """Сборка DeployAgent: agent_args() + подпись, как у node/webui."""
    name = "DeployAgent_se" if flavor == "se" else "DeployAgent"
    log.info(f"Building {name} (flavor={flavor})...")
    if not Path("services/deployer/agent/main.py").is_file():
        # бизнес-отказ: entry агента отсутствует
        log.error("services/deployer/agent/main.py не найден — агент не собран")
        return False
    try:
        PyInstaller.__main__.run(agent_args(flavor))
    except (Exception, SystemExit):
        # SystemExit у PyInstaller = фатальная ошибка конвейера
        log.error(traceback.format_exc())
        return False
    exe = Path(f"dist/{name}.exe")
    if not exe.is_file():
        log.error(f"{exe} не создан")
        return False
    sign_in_place(name)
    return True


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


def arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Сборка P2P_Core")
    p.add_argument("--type", choices=["node", "webui", "agent", "all"], default="all")
    p.add_argument("--flavor", choices=["auto", "se", "open"], default="auto")
    return p


def _build_node(flavor: str) -> None:
    # цель Process: на Windows spawn нужен вызов в Process (lambda не пиклится)
    if build("Node_P2P_Core", ui=False, flavor=flavor):
        sign_in_place("Node_P2P_Core")


def _build_webui(flavor: str) -> None:
    if build("WebUI_P2P_Core", ui=True, flavor=flavor):
        sign_in_place("WebUI_P2P_Core")


def main(argv: list[str] | None = None) -> int:
    args = arg_parser().parse_args(argv)
    flavor = detect_flavor(args.flavor, (ROOT / "src" / "se").is_dir())
    logging.basicConfig(level=logging.INFO)
    os.makedirs(SIGNED_DIR, exist_ok=True)
    wanted = {"node", "webui"} if args.type == "all" else {args.type}
    if "node" in wanted:
        # бамп BUILD_NUMBER + version.txt только для node-сборки:
        # --type webui/agent не должен двигать версию
        version = make_version_txt()
        log.info(f"Building version {version} (flavor={flavor})")
    else:
        version = ""
        log.info(f"Building type={args.type} (flavor={flavor}, version без бампа)")
    ths = []
    if "node" in wanted:
        ths.append(Process(target=_build_node, args=(flavor,)))
    if "webui" in wanted:
        ths.append(Process(target=_build_webui, args=(flavor,)))
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    if "node" in wanted:
        make_manifest(version)
    if "agent" in wanted:
        if not build_agent(flavor):
            return 1
    log.info(f"Время выполнения: {time.time() - start_time:.6f} сек")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
