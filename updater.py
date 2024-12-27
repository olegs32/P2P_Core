import requests
import os
import sys
import subprocess
import hashlib

SERVER_URL = "http://127.0.0.1:8080/api/check_version"
CLIENT_TOKEN = "client-token-1"
CURRENT_VERSION = "1.0.0"


def check_for_updates():
    headers = {"x-client-token": CLIENT_TOKEN}
    response = requests.get(SERVER_URL, headers=headers)
    if response.status_code == 200:
        data = response.json()
        if data["version"] > CURRENT_VERSION:
            return data["download_url"]
    return None


def download_new_version(download_url, save_path):
    response = requests.get(download_url, stream=True)
    if response.status_code == 200:
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                f.write(chunk)
        return save_path
    return None


def update_client(new_exe_path):
    updater_script = os.path.join(os.path.dirname(new_exe_path), "updater.bat")
    with open(updater_script, 'w') as f:
        f.write(f"""@echo off
timeout 2 >nul
move /y "{new_exe_path}" "{sys.argv[0]}"
start "" "{sys.argv[0]}"
del "%~f0"
""")
    subprocess.Popen(updater_script, shell=True)
    sys.exit(0)


if __name__ == "__main__":
    print("Проверка обновлений...")
    new_version_url = check_for_updates()
    if new_version_url:
        print("Доступна новая версия! Скачивание...")
        temp_path = os.path.join(os.getcwd(), "new_client.exe")
        new_file = download_new_version(new_version_url, temp_path)
        if new_file:
            print("Обновление завершено. Перезапуск...")
            update_client(new_file)
    else:
        print("Вы используете последнюю версию.")
