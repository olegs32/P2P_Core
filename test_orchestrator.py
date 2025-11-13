#!/usr/bin/env python3
"""
Тест orchestrator API для диагностики проблем с получением списка сервисов
"""

import requests
import json
import sys
import urllib3

# Отключаем предупреждения о небезопасном SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def test_orchestrator(host="127.0.0.1", port=8001):
    """Проверяет orchestrator API"""
    base_url = f"https://{host}:{port}"

    print(f"🔍 Проверка Orchestrator API на {base_url}")
    print("=" * 80)

    # 1. Test orchestrator.list_services via RPC
    print("\n1️⃣  Проверка /rpc вызова orchestrator.list_services...")
    try:
        rpc_payload = {
            "jsonrpc": "2.0",
            "method": "orchestrator/list_services",
            "params": {},
            "id": "1"
        }

        response = requests.post(
            f"{base_url}/rpc",
            json=rpc_payload,
            timeout=10,
            verify=False
        )

        print(f"   📤 Request: {json.dumps(rpc_payload, indent=2)}")
        print(f"   📥 Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"   📊 Response:")
            print(f"      {json.dumps(data, indent=6)}")

            if "result" in data and data["result"]:
                result = data["result"]
                services = result.get("services", [])
                print(f"\n   ✅ Найдено сервисов: {len(services)}")

                if services:
                    print(f"   📋 Список сервисов:")
                    for svc in services:
                        name = svc.get("name", "unknown")
                        running = svc.get("running", False)
                        installed = svc.get("installed", False)
                        print(f"      - {name}: running={running}, installed={installed}")
                else:
                    print(f"   ⚠️  services = [] (пустой список)")
            elif "error" in data:
                print(f"   ❌ RPC Error: {data['error']}")
            else:
                print(f"   ⚠️  Нет 'result' в ответе")
        else:
            print(f"   ❌ HTTP Error: {response.status_code}")
            print(f"      {response.text}")

    except Exception as e:
        print(f"   ❌ Exception: {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")

    # 2. Test system service (для проверки что RPC вообще работает)
    print("\n2️⃣  Проверка /rpc вызова system.get_system_info (контроль)...")
    try:
        rpc_payload = {
            "jsonrpc": "2.0",
            "method": "system/get_system_info",
            "params": {},
            "id": "2"
        }

        response = requests.post(
            f"{base_url}/rpc",
            json=rpc_payload,
            timeout=10,
            verify=False
        )

        print(f"   📥 Status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            if "result" in data:
                print(f"   ✅ RPC работает! System info получен")
                # Показываем краткую информацию
                result = data["result"]
                if isinstance(result, dict):
                    print(f"      Platform: {result.get('platform', 'unknown')}")
                    print(f"      Python: {result.get('python_version', 'unknown')}")
            else:
                print(f"   ⚠️  Нет 'result' в ответе")
        else:
            print(f"   ❌ HTTP Error: {response.status_code}")

    except Exception as e:
        print(f"   ❌ Exception: {e}")

    print("\n" + "=" * 80)
    print("✨ Диагностика завершена")
    print("\n💡 Возможные причины проблемы:")
    print("   1. orchestrator сервис не запущен")
    print("   2. orchestrator.list_services() возвращает пустой список")
    print("   3. Ошибка при вызове метода через RPC")
    print("   4. Неправильный формат ответа от orchestrator")
    print("\n📋 Действия:")
    print("   - Проверьте логи координатора (ищите WARNING/ERROR от metrics_dashboard)")
    print("   - Убедитесь что orchestrator сервис запущен и работает")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        host = sys.argv[1]
    else:
        host = "127.0.0.1"

    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    else:
        port = 8001

    test_orchestrator(host, port)
