import subprocess
import time

# Запускаем координатора на порту 8080
subprocess.Popen(['python', 'node.py', 'coordinator', '8080'])
time.sleep(2)  # даём координатору подняться

# Кол-во узлов
instances = 100
base_port = 9000

for i in range(instances):
    port = base_port + i
    print(f'Запускаю ноду на порту {port}')
    subprocess.Popen(['python', 'node.py', str(port)])
    time.sleep(0.1)
time.sleep(240)