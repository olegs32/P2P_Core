import time
import datetime
from uptime import uptime

START_TIME = time.time()


def get_uptime():
    return round(time.time() - START_TIME)
    # return datetime.datetime.fromtimestamp(int(round(time.time() - START_TIME))).strftime('%Y-%m-%d %H:%M:%S')


# from datetime import date, timedelta
#
# m, n = map(int, input().split())
# t = date(2023, m, n)
# print(f"{str(t - timedelta(days=1))[5:]} {str(t - timedelta(days=-1))[5:]}")

