import os
print(os.popen('pyinstaller --hidden-import=Client2 --onefile Client2.py').read())
print(os.popen('pyinstaller --onefile updater.py').read())


