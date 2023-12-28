import datetime
import time
from threading import Thread

START_TIME = time.time()


def thread_controller(threads: list):
    ths = []
    for th in threads:
        args = ''
        if 'args' in th:
            args = th['args']
        ths.append(Thread(target=th['target'], args=args))
        ths[-1].start()
    while True:
        for index, th in enumerate(threads):
            if not ths[index].is_alive:
                args = ''
                if 'args' in th:
                    args = th['args']
                ths.append(Thread(target=th['target'], args=args))
                ths[-1].start()
            time.sleep(1)


def threader(threads: list):
    """

    :param threads: [{'target': target, 'args': list}]
    """
    th = Thread(target=thread_controller, args=(threads,), daemon=True)
    th.start()


def get_uptime():
    secs = mins = hours = 0
    secs = round(time.time() - START_TIME)
    # if secs > 60:
    #     mins = secs // 60
    # if mins > 60:
    #     hours = mins // 60
    # if hours > 24:
    #     days = hours // 24
    # return secs
    sec = datetime.timedelta(seconds=secs)
    d = datetime.datetime(1, 1, 1) + sec

    # print("DAYS:HOURS:MIN:SEC")
    # print("%d:%d:%d:%d" % (d.day - 1, d.hour, d.minute, d.second))
    return "%dD:%dH:%dM:%dS" % (d.day - 1, d.hour, d.minute, d.second)
    # return datetime.datetime.fromtimestamp(int(round(time.time() - START_TIME))).strftime('%Y-%m-%d %H:%M:%S')



