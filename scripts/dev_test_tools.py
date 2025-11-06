import math
import time
from multiprocessing import Pool, cpu_count, freeze_support
from typing import Dict, List


class Iterator:
    def __init__(self, data, len_key):
        self.result: list = []
        self.proc: int = cpu_count()
        self.data: str = data
        self.len_key: int = len_key
        self.data_len = len(data)
        self.start_pos: Dict[int, int] = {x: 0 for x in range(0, self.len_key)}
        self.end_pos: Dict[int, int] = {x: self.data_len for x in range(0, self.len_key)}

    def calc_start(self, skip):
        start = [0 for _ in range(self.len_key)]
        for index, degree in enumerate(range(self.len_key, 0, -1)):
            index = index - 1
            max_iters = self.data_len ** degree  # максимальное количество итераций в данном разряде
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

    def recursive_iter(self, line: list, step: int, max_steps: int, target: list, start_pos: dict, end_pos: dict, ):
        # print(self.data, line, step, max_steps, target)
        # print('test', start_pos, end_pos)
        max_step = end_pos[step] + 1 if end_pos[step] <= self.data_len else end_pos[step]
        result_range = [x for x in range(start_pos[step], max_step)]
        iters_max = max_step - start_pos[step]
        # print('iters started')
        if len(result_range) < iters_max or iters_max <= 0:
            old_resrange = result_range
            result_range = [x for x in range(start_pos[step], self.data_len)] + [x for x in range(0, max_step)]
            # print(iters_max, 'low, add', result_range)
            # print(old_resrange, 'low, add', result_range, start_pos, end_pos)
        # print(result_range)
        for pos in result_range:
            # print(pos, start_pos, line, result_range)
            sym = self.data[pos]
            line[step] = sym
            if step < max_steps:
                recursive_step = step + 1
                if self.recursive_iter(line, recursive_step, max_steps, target, start_pos, end_pos):
                    self.result.append(line)
                    print('found1', line)
                # print('loop closed')
                # start_pos[step] = 0
            if line == target:
                self.result.append(line)
                print('found2', line, start_pos, end_pos, step, result_range)

        return False

    def custom_iterator(self, skip: int = 0, end: int = 0, target: str = ''):
        """
        Кастомный итератор со стартовой позицией перебора

        data: данные для перебора
        result: результат например  (хеш)
        len_key: длина ключа
        skip: отступ с начала итераций
        """

        max_iters = len(self.data) ** self.len_key
        print('max_iters', max_iters)
        target = list(target)

        line = [self.data[0] for _ in range(self.len_key)]
        if skip > 0:
            if end == 0:
                end = max_iters
                print('end', end)
            summary = max_iters - skip
            print('end', end, 'summary', summary)
            if summary > 0:
                pool = Pool(processes=self.proc)
                limits: Dict[int, list] = {}
                part = int(summary / self.proc)
                for n in range(self.proc + 1):
                    if n == self.proc + 1:
                        limits[n] = [self.calc_start(int(skip + n * part)), self.calc_start(max_iters)]
                    # print('n', n, part, self.proc)
                    limits[n] = [self.calc_start(int(skip + n * part)), self.calc_start(int(skip + (n + 1) * part))]
                    print(f" begin {skip + n * part}, end {skip + (n + 1) * part}")
                for i in limits:
                    print('limits', limits[i])
                    pool.apply_async(self.recursive_iter,
                                     args=(line, 0, self.len_key - 1, target, limits[i][0], limits[i][1]))
                pool.close()
                pool.join()
        else:
            limits = [[0 for x in range(self.len_key)], [len(self.data) for x in range(self.len_key)]]

            self.recursive_iter(line, 0, length_key - 1, target, limits[0], limits[1])
            # self.start_pos = self.calc_start(skip)
            # if end != 0:
            #     self.end_pos = [x + 1 for x in self.calc_start(end)]  # fix to range()
            # else:
            #     self.end_pos = {x: self.data_len for x in range(0, self.len_key)}

        # result = self.recursive_iter(line, 0, self.len_key - 1, target)

        return self.result


if __name__ == '__main__':
    freeze_support()
    dataset = '1234567890'
    length_key = 5
    ts_start = time.time()
    # print(result)
    iterator = Iterator(dataset, length_key)

    # print(iterator.calc_start(9000))
    print(cpu_count())

    print(iterator.custom_iterator(target='2' * length_key, skip=1, ))

    # limits = [[1, 4, 3, 6], [0, 3, 2, 5]]
    # line = [dataset[0] for _ in range(length_key)]
    # target = '2' * length_key
    # result = iterator.recursive_iter(line, 0, length_key - 1, target, limits[0], limits[1])
    # print(result)

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
