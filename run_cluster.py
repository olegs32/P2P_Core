import os
import time

workstations = 80

for i in range(workstations):
    os.popen(f"start python run.py --host 127.0.0.1 --port {8000 + i} --dht-port {10001 + i} --bootstrap 127.0.0.1:10000")
    time.sleep(0.2)
