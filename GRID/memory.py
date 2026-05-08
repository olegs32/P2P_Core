import queue
import random
import threading
import time

from streamlit.connections.util import running_in_sis


def gen(pipe):
    i = 0
    while i < 100:
        print(f'Generator lazy {i}')
        pipe.input(i)
        i += 1
    pipe.close()
    print('gen finish')
    # yield i


class Pipe:
    def __init__(self, pipe_id, buff_len: int = 10, reverse_buff_len: int = 1):
        self.pipe_active = True
        self.buff_len = buff_len
        self.reverse_buff_len = reverse_buff_len
        self.pipe_id = pipe_id
        self.incoming_buffer = queue.Queue(buff_len * 3)
        self.outgoing_buffer = queue.Queue(buff_len * 3)
        # self.buffer = queue.Queue(buff_len * 3)

    def input(self, item):
        self.pipe_active = True
        print('queue size', self.incoming_buffer.qsize())
        while self.incoming_buffer.qsize() >= self.buff_len:
            time.sleep(0.1)
        else:
            self.incoming_buffer.put(item)

    def close(self):
        self.pipe_active = False
        return None

    def __iter__(self):
        return self

    def __next__(self):
        print('out', self.outgoing_buffer.qsize(), 'in', self.incoming_buffer.qsize())
        while self.outgoing_buffer.qsize() < 0:
            time.sleep(0.1)
        else:
            if not self.pipe_active:
                if self.outgoing_buffer.empty():
                    raise StopIteration
            return self.outgoing_buffer.get()

    def __call__(self, *args, **kwargs):
        self.running = True
        while self.running:
            while self.outgoing_buffer.qsize() <= self.buff_len:
                if not self.incoming_buffer.empty():
                    self.outgoing_buffer.put(self.incoming_buffer.get())
                else:
                    if not self.pipe_active:
                        if self.incoming_buffer.empty() and self.outgoing_buffer.empty():
                            time.sleep(1)
                            self.running = False
                            print(self.running)
                        if not self.running:
                            return True
                        time.sleep(0.1)

            # if not self.pipe_active:
            #     # print(self.pipe_active, self.outgoing_buffer.qsize())
            #     # while not self.outgoing_buffer.empty():
            #     print(self.incoming_buffer.empty(),self.outgoing_buffer.empty())
            #     while not self.incoming_buffer.empty() and self.outgoing_buffer.empty():
            #         time.sleep(1)
            #         self.running = False
            #         print(self.running)
            time.sleep(0.01)
            # print(self.pipe_active)
        print('call loop finished')
        return True


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
            time.sleep(0.1)
            print('Run at remote ', i)
        started = False
        print(started)
    print('run finished')


mem = Memory('test')
pipe = mem.create_pipe()
print(pipe)

th = threading.Thread(target=gen, args=(mem.get_pipe(pipe),))
th.start()

th2 = threading.Thread(target=run, args=(mem.get_pipe(pipe),))
th2.start()

time.sleep(1)
mem.get_pipe(pipe)()
print('final')

# gen(mem.get_pipe(pipe))

# print(mem.get_pipe(pipe).test(123))
