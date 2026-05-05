import queue
import random
import threading
import time


def gen(pipe):
    i = 1
    while i < 100:
        print(f'Generator lazy {i}')
        pipe.input(i)
        i += 1
        # yield i


class Pipe:
    def __init__(self, pipe_id, buff_len: int = 10, reverse_buff_len: int = 1):
        self.buff_len = buff_len
        self.reverse_buff_len = reverse_buff_len
        self.pipe_id = pipe_id
        self.income = queue.Queue(100)
        self.outgoing = queue.Queue(100)
        self.buffer = queue.Queue(buff_len * 3)

    def input(self, item):
        print('queue size', self.buffer.qsize())
        while self.buffer.qsize() >= self.buff_len:
            time.sleep(0.1)
        else:
            self.buffer.put(item)

    def __iter__(self):
        return self

    def __next__(self):
        print(self.buffer.qsize())
        return self.buffer.get()


class Memory:
    def __init__(self, node):
        self.node = node
        self.table = {}

    def get_table(self):
        return self.table

    def get_pipe(self, pipe_id):
        return self.table[pipe_id]

    def create_pipe(self, buff: int = 10, reverse_buff: int = 1):
        pipe_id = f'{self.node}_{len(self.table) + 1}'
        self.table[pipe_id] = Pipe(pipe_id, buff, reverse_buff)
        return pipe_id

    def create_pipes(self, count: int, buff: int = 10, reverse_buff: int = 1):  # initiator side
        pipes = []
        for i in range(count):
            pipes.append(self.create_pipe(buff, reverse_buff))
        return pipes

    # def __getattr__(self, item: str):
    #     print(item)
    #     print(self.table)
    #     return self.table[item]
    #
    # def __getattribute__(self, item):
    #     table = self.table
    #     return table.get(item)


class Dispatcher:
    def __init__(self, pipes: list):
        self.pipes = pipes
        self.generator = None
        self.started = False

    # def process(self, item):
    #     self.queue.append(item)
    #

    def process(self, generator):
        self.generator = generator

    def run(self):
        self.started = True
        while self.started:
            for i in self.generator():
                print(i)
            self.started = False


def run(pipe):
    started = True
    while started:
        for i in pipe:
            time.sleep(random.randrange(1, 9) / 10)
            print('Run at remote ', i)
        started = False


mem = Memory('test')
pipe = mem.create_pipe()
print(pipe)

th = threading.Thread(target=gen, args=(mem.get_pipe(pipe),), daemon=True)
th.start()
run(mem.get_pipe(pipe))

# gen(mem.get_pipe(pipe))

# print(mem.get_pipe(pipe).test(123))
