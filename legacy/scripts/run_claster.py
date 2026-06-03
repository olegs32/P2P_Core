#!/usr/bin/env python3
# debug_cluster.py

import subprocess
import time
import sys
import signal
import os

COORD_PORT = 8001
WORKER_START_PORT = 8100
WORKER_COUNT = 50

processes = []


def cleanup():
    print("\nStopping cluster...")
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=5)
        except:
            p.kill()


def signal_handler(sig, frame):
    cleanup()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def start_coordinator():
    cmd = ["dist/p2p.exe", "coordinator", "--port", str(COORD_PORT), "--address", "127.0.0.1", "--node-id",
           "coord-debug"]
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'

    with open("logs/coordinator.log", "w", encoding='utf-8') as f:
        p = subprocess.Popen(cmd, stdout=f, stderr=f)
    processes.append(p)
    print(f"Started coordinator on port {COORD_PORT}")
    time.sleep(3)  # Ждем запуск координатора


def start_workers():
    coord_addr = f"127.0.0.1:{COORD_PORT}"
    # Настройка кодировки
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'

    for i in range(WORKER_COUNT):
        port = WORKER_START_PORT + i
        node_id = f"worker-{i:02d}"

        cmd = ["dist/p2p.exe", "worker", "--port", str(port), "--address", "127.0.0.1", "--coord", coord_addr,
               "--node-id", node_id]

        with open(f"logs/worker-{i:02d}.log", "w", encoding='utf-8') as f:
            p = subprocess.Popen(cmd, stdout=f, stderr=f)
        processes.append(p)
        print(f"Started worker {node_id} on port {port}")
        time.sleep(0.1)  # Небольшая задержка между воркерами


if __name__ == "__main__":
    # Создаем папку для логов
    os.makedirs("logs", exist_ok=True)

    try:
        # start_coordinator()
        start_workers()

        print(f"\nCluster started: 1 coordinator + {WORKER_COUNT} workers")
        print(f"Coordinator: http://127.0.0.1:{COORD_PORT}")
        print(f"Workers: ports {WORKER_START_PORT}-{WORKER_START_PORT + WORKER_COUNT - 1}")
        print("\nPress Ctrl+C to stop cluster")

        # Ждем завершения
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        cleanup()
