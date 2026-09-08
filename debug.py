import psutil

pid = list(set([con.pid for con in psutil.net_connections() if con.laddr.port == 8501]))

print(pid)