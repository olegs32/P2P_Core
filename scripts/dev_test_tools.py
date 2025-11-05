import time
from typing import Dict


class Iterator:
    def __init__(self, data):
        self.data: str = data
        self.start_pos: Dict[int, int] = {}

    def calc_start(self, skip, len_key):
        start = [0 for _ in range(len_key)]
        for index, degree in enumerate(range(len_key, 0, -1)):
            index = index - 1
            max_iters = len(self.data) ** degree  # максимальное количество итераций в данном разряде
            current_state = skip // max_iters  # полные вхождения в этот разряд
            if degree == 1:
                if current_state > 0:
                    skip = skip - current_state * max_iters
                    start[index] = current_state
                    start[index + 1] = skip - 1
                else:
                    start[index + 1] = 0
            elif current_state > 0:  # если вошло хоть раз
                skip = skip - current_state * max_iters  # остаток вхождений
                start[index] = current_state
        return start

    def recursive_iter(self, line: list, step: int, max_steps: int, target: list):
        for sym in self.data:
            if step < max_steps:
                rec_step = step + 1
                if self.recursive_iter(line, rec_step, max_steps, target):
                    return line
            line[step] = sym
            if line == target:
                return line
        return False

    def custom_iterator(self, len_key: int, skip: int = 0, target: str = ''):
        """
        Кастомный итератор со стартовой позицией перебора

        data: данные для перебора
        result: результат (хеш)
        len_key: длина ключа
        skip: отступ с начала итераций
        """

        max_iters = len(self.data) ** len_key
        print('max_iters', max_iters)
        line = [self.data[0] for _ in range(len_key)]
        if skip > 0:
            line = self.calc_start(skip, len_key)
        target = list(target)
        result = self.recursive_iter(line, 0, len_key - 1, target)

        return result


dataset = '1234567890'
length_key = 8
ts_start = time.time()
# print(result)
iterator = Iterator(dataset)

print(iterator.calc_start(654, length_key))
print(iterator.custom_iterator(length_key, target='0' * length_key, skip=654))

# for i in range(1, 1000, 1):
#     global skip
#     it = 0
#     indexes = []
#     skip = i
#     print(i,'     ', calc_start(data, i, len_key))
#     result = custom_iterator(data, len_key, target='0' * len_key)
#
#     for s in result:
#         # print(i, 'index', data.index(i))
#         indexes.append(data.index(s))
#     print('indexes', indexes)
# print(calc_start(data, 846, 4))
ts_end = time.time()

print('Done:', (ts_end - ts_start).__round__(7))
