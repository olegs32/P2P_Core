import datetime
import time


def uptime(start_time: float) -> str:
    return str(datetime.timedelta(seconds=int(time.time() - start_time)))

