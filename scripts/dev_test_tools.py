import time
from operator import index

global it, skip
it = 0

def calc_start(symbols, skip, len_key):
    start = [0 for _ in range(len_key)]
    for index, degree in enumerate(range(len_key, 0, -1)):
        index = index - 1
        max_iters = len(symbols) ** degree # максимальное количество итераций в данном разряде
        current_state = skip // max_iters # полные вхождения в этот разряд
        # print(index, degree, 'max_iters', max_iters, 'skip', skip, "current_state", current_state)

        if degree == 1:
            if current_state > 0:
                skip = skip - current_state * max_iters
                # print('final', index, degree, 'max_iters', max_iters, 'skip', skip, "current_state", current_state)
                start[index] = current_state
                start[index + 1] = skip - 1
            else:
                start[index + 1] = 0
        elif current_state > 0: #если вошло хоть раз
            # print('old skip', skip)
            skip = skip -  current_state * max_iters # остаток вхождений
            # print('new skip', skip)
            start[index] = current_state
    result = start.copy()
    # for index, i in enumerate(start):
    #     result[index] = symbols[i]
    # print(start)

    return result


def recursive_iter(symbols: str, line: list, step: int, max_steps: int, target: list):
    global it
    global skip
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
        it += 1
        if it >= skip:
            # print('skip on', line, skip, )
            return line
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

    max_iters = len(symbols) ** len_key
    # print('max_iters', max_iters)
    line = [symbols[0] for _ in range(len_key)]
    if skip > 0:
        line = calc_start(symbols, skip, len_key)
    target = list(target)
    result = recursive_iter(symbols, line, 0, len_key - 1, target)

    return result


data = '1234567890'
len_key = 5
ts_start = time.time()
# print(result)


for i in range(1, 1000, 1):
    global skip
    it = 0
    indexes = []
    skip = i
    print(i,'     ', calc_start(data, i, len_key))
    result = custom_iterator(data, len_key, target='0' * len_key)

    for s in result:
        # print(i, 'index', data.index(i))
        indexes.append(data.index(s))
    print('indexes', indexes)
# print(calc_start(data, 846, 4))
ts_end = time.time()

print('Done:', (ts_end - ts_start).__round__(7))
