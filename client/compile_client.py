import os
print(os.popen('pyinstaller --onefile client_deployer.py').read())
print(os.popen('pyinstaller --onefile client_payloads.py').read())
