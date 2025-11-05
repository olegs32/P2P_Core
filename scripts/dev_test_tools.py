import time



def calc_start(symbols, skip, len_key):
    return ['0' for _ in range(len_key)]


def recursive_iter(symbols: str, line: list, step: int, max_steps: int, target: list):
    for sym in symbols:
        if step < max_steps:
            rec_step = step + 1
            if recursive_iter(symbols, line, rec_step, max_steps, target):
                return True
        line[step] = sym
        if line == target:
            return True
    return False


def custom_iterator(symbols: str, len_key: int, skip: int = 0, target: str = ''):
    """
    Кастомный итератор со стартовой позицией перебора

    data: данные для перебора
    result: результат (хеш)
    len_key: длина ключа
    skip: отступ с начала итераций
    """

    max_iters = len(symbols) ** len(symbols)
    print('max_iters', max_iters)
    line = [symbols[0] for _ in range(len_key)]
    if skip > 0:
        line = calc_start(symbols, skip, len_key)
    target = list(target)
    result = recursive_iter(symbols, line, 0, len_key - 1, target)

    return result


data = '1234567890'
ts_start = time.time()
print(custom_iterator(data, 8, target = '00000000'))
ts_end = time.time()

print('Done:', (ts_end - ts_start).__round__(3) )
