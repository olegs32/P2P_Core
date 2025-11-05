import time


def calc_start(symbols, skip, len_key):
    return ['0' for _ in range(len_key)]


global it
it = 0


def recursive_iter(symbols: str, line: list, step: int, max_steps: int, target: list):
    # global it
    for sym in symbols:
        if step < max_steps:
            rec_step = step + 1
            old_line = line
            line[step] = sym

            if recursive_iter(symbols, old_line, rec_step, max_steps, target):
                return line
            # print('loop closed')
            continue
        line[step] = sym
        # it += 1
        # print(step, line, sym)
        if line == target:
            # print('Solve', line, target)
            return line
    return False


def custom_iterator(symbols: str, len_key: int, skip: int = 0, target: str = ''):
    """
    Кастомный итератор со стартовой позицией перебора

    data: данные для перебора
    result: результат (хеш)
    len_key: длина ключа
    skip: отступ с начала итераций
    """
    global it

    max_iters = len(symbols) ** len_key
    print('max_iters', max_iters)
    line = [symbols[0] for _ in range(len_key)]
    if skip > 0:
        line = calc_start(symbols, skip, len_key)
    target = list(target)
    result = recursive_iter(symbols, line, 0, len_key - 1, target)
    print('real iter', it)


    return result


data = '1234567890'
ts_start = time.time()
print(custom_iterator(data, 8, target='00000000'))
ts_end = time.time()

print('Done:', (ts_end - ts_start).__round__(3))
