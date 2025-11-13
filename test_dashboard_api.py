#!/usr/bin/env python3
"""
Скрипт для проверки API metrics_dashboard
Используйте для диагностики проблем с веб-интерфейсом
"""

import requests
import json
import sys

def test_dashboard_api(host="127.0.0.1", port=8001):
    """Проверяет API дашборда"""
    base_url = f"http://{host}:{port}"

    print(f"🔍 Проверка API дашборда на {base_url}")
    print("=" * 80)

    # 1. Check metrics endpoint
    print("\n1️⃣  Проверка /api/dashboard/metrics...")
    try:
        response = requests.get(f"{base_url}/api/dashboard/metrics", timeout=5)
        if response.status_code == 200:
            data = response.json()

            # Check coordinator services
            coordinator = data.get("coordinator", {})
            services = coordinator.get("services", {})

            print(f"   ✅ Status: {response.status_code}")
            print(f"   📊 Coordinator services: {len(services)} найдено")

            if services:
                print(f"   📋 Список сервисов координатора:")
                for service_name, service_data in services.items():
                    status = service_data.get("status", "unknown")
                    print(f"      - {service_name}: {status}")
            else:
                print(f"   ⚠️  ПРОБЛЕМА: Нет сервисов координатора!")
                print(f"      Данные coordinator: {json.dumps(coordinator, indent=2)}")

            # Check workers
            workers = data.get("workers", {})
            print(f"\n   👷 Workers: {len(workers)} подключено")

        else:
            print(f"   ❌ Ошибка: HTTP {response.status_code}")
            print(f"      {response.text}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    # 2. Check dashboard page
    print("\n2️⃣  Проверка /dashboard...")
    try:
        response = requests.get(f"{base_url}/dashboard", timeout=5)
        if response.status_code == 200:
            html = response.text

            # Check for control buttons
            has_control_buttons = 'onclick="controlService(' in html
            has_coordinator_controls = "controlService('coordinator'" in html

            print(f"   ✅ Status: {response.status_code}")
            print(f"   📄 HTML размер: {len(html)} байт")
            print(f"   🔘 Кнопки управления найдены: {has_control_buttons}")
            print(f"   🎛️  Управление координатором: {has_coordinator_controls}")

            if not has_coordinator_controls:
                print(f"   ⚠️  ПРОБЛЕМА: Кнопки управления координатором отсутствуют!")
                print(f"      Проверьте updateCoordinatorServices() в HTML")
        else:
            print(f"   ❌ Ошибка: HTTP {response.status_code}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

    print("\n" + "=" * 80)
    print("✨ Диагностика завершена")
    print("\n💡 Если проблемы найдены:")
    print("   1. Перезапустите p2p.py")
    print("   2. Сделайте Ctrl+F5 в браузере для сброса кеша")
    print("   3. Проверьте консоль браузера (F12) на ошибки JavaScript")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        host = sys.argv[1]
    else:
        host = "127.0.0.1"

    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    else:
        port = 8001

    test_dashboard_api(host, port)
