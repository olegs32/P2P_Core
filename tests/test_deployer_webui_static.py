# Статический тест UI deployer: SMB-форма удалена, секция генерации agent-пакетов есть.
# Только чтение текста файлов — без импортов src/se и без рендера.
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "services" / "deployer" / "web_ui.py"
SERVICE = ROOT / "services" / "deployer" / "service.py"

SMB_MARKS = ("remote_path", "deploy_only", "psexec")
SECTION_MARKS = ("Генерация agent-пакетов", "gen_agent_package", "agent_status", "fragment")
# RPC-имена из брифа Task 11 (Tasks 4-5)
RPC_NAMES = ("gen_agent_package", "deploy", "agent_status",
             "rotate_agent_key", "list_devices", "get_status")
# из этих RPC-имён web_ui обязан делать прямые вызовы deployer'а
CALLED_RPC = ("gen_agent_package", "agent_status", "rotate_agent_key",
              "list_devices", "get_status")


def test_deployer_webui_static():
    ui = UI.read_text(encoding="utf-8")
    svc = SERVICE.read_text(encoding="utf-8")

    for token in SMB_MARKS:
        assert token not in ui, f"SMB-марка осталась в web_ui: {token}"

    for marker in SECTION_MARKS:
        assert marker in ui, f"нет маркера секции генерации: {marker}"

    for name in RPC_NAMES:
        assert name in ui, f"RPC-имя отсутствует как строка в web_ui: {name}"
        assert re.search(rf"@rpc\s*\n\s*(?:async\s+)?def\s+{name}\b", svc), \
            f"нет @rpc-метода {name} в services/deployer/service.py"

    for name in CALLED_RPC:
        assert re.search(rf"""["']deployer["'],\s*["']{name}["']""", ui), \
            f"web_ui не вызывает rpc.call('deployer', '{name}')"
