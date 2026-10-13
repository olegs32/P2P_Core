# Статический тест UI deployer: SMB-форма удалена, секция генерации agent-пакетов есть.
# Только чтение текста файлов — без импортов src/se и без рендера.
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "services" / "deployer" / "web_ui.py"
SERVICE = ROOT / "services" / "deployer" / "service.py"

# "psexec" убран из маркеров: UI теперь легально упоминает psexec code в
# статусе доставки (Task 14); старая SMB push-механика по-прежнему удалена.
SMB_MARKS = ("remote_path", "deploy_only")
SECTION_MARKS = ("Генерация agent-пакетов", "gen_agent_package", "agent_status", "fragment",
                 "Сборка агента", "build_agent_exe")
# RPC-имена из брифа Task 11 (Tasks 4-5) + deliver_agent (Task 14) + build_agent_exe (сборка агента из UI)
RPC_NAMES = ("gen_agent_package", "deploy", "agent_status",
             "rotate_agent_key", "list_devices", "get_status", "deliver_agent",
             "build_agent_exe")
# из этих RPC-имён web_ui обязан делать прямые вызовы deployer'а
CALLED_RPC = ("gen_agent_package", "agent_status", "rotate_agent_key",
              "list_devices", "get_status", "deliver_agent", "build_agent_exe")
# Task 15: фоновая доставка — живая секция deliveries в UI + отсутствие
# блокирующего `await _deliver_agent` внутри gen_agent_package
DELIVERY_MARKS = ("deliveries", "Доставка пакетов", "в очереди на доставку",
                  "Повторить доставку")


def test_deployer_webui_static():
    ui = UI.read_text(encoding="utf-8")
    svc = SERVICE.read_text(encoding="utf-8")

    for token in SMB_MARKS:
        assert token not in ui, f"SMB-марка осталась в web_ui: {token}"

    for marker in SECTION_MARKS:
        assert marker in ui, f"нет маркера секции генерации: {marker}"

    for marker in DELIVERY_MARKS:
        assert marker in ui, f"нет маркера фоновой доставки: {marker}"

    # gen_agent_package не должен ждать доставку (блокирующий await в RPC)
    m = re.search(r"async def gen_agent_package\(.*?(?=\n    @rpc|\n    async def )",
                  svc, re.S)
    assert m, "gen_agent_package не найдена в services/deployer/service.py"
    assert "_deliver_agent" not in m.group(0), \
        "gen_agent_package блокируется доставкой (нет фонового паттерна)"
    assert "async def _run_deliveries" in svc, "нет фонового воркера доставки"
    assert '"deliveries"' in svc, "agent_status не отдаёт deliveries"

    for name in RPC_NAMES:
        if name == "deploy":
            # вакуумная подстрока: "deploy" всегда матчится внутри "deployer",
            # поэтому в web_ui проверяем негативом — UI этот RPC не вызывает
            # (deploy = генерация пакетов, Task 5)
            # ВНИМАНИЕ: покрытие частичное — ловится только литеральная форма
            # rpc.call('deployer', 'deploy'); косвенные вызовы (через переменную
            # с именем метода, getattr, eval-подобные конструирования) не проверяются.
            assert not re.search(r"""["']deployer["'],\s*["']deploy["']""", ui), \
                "web_ui не должен вызывать rpc.call('deployer', 'deploy')"
        else:
            assert name in ui, f"RPC-имя отсутствует как строка в web_ui: {name}"
        assert re.search(rf"@rpc\s*\n\s*(?:async\s+)?def\s+{name}\b", svc), \
            f"нет @rpc-метода {name} в services/deployer/service.py"

    for name in CALLED_RPC:
        assert re.search(rf"""["']deployer["'],\s*["']{name}["']""", ui), \
            f"web_ui не вызывает rpc.call('deployer', '{name}')"
