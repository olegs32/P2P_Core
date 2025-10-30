import base64
import hashlib
import os
import random
from pathlib import Path
from typing import List

root = Path('test_root')
os.makedirs(root, exist_ok=True)

direction_file = root / 'dir_1761551712_server'
vector_file = root / "vector_1761551712_server"

direction_data = open(direction_file).read()
vector_data = open(vector_file).read()


def gen_set(data: list, index):
    res = ''
    for step, l in enumerate(data):
        step_index = index
        data_len = len(data[step])
        if step_index > data_len:
            step_index = step_index - data_len * (step_index // data_len)
            if step_index == data_len:
                step_index = 1
        # print(step, step_index, data_len)
        res += l[step_index]
    return res

def get_index(data: List[str,], char):
    index = None
    for step, l in enumerate(data):
        if char in l:
            index = l.index(char)
    return index



def create_data(count: int, block: int = 10, gap: int = 2):
    """
    Create plot with specified characteristics
    length: summary length
    block: size of payload block
    gap: control check data
    """
    wordset = "qwertyuiopasdfghjklzxcvbnmQWERTYUIOPASDFGHJKLZXCVBNM"
    symset = "1234567890!@#$%^&*()_+-=/?;:'[{]}"
    dataset = [wordset, symset]
    chunk = []
    vector = int(random.random() * 10 ** 3)
    print(vector)
    temp = vector
    iters = int(count * (block + gap) // 2)
    raw = ''  # create empty dataset
    for i in range(iters):
        raw += gen_set(dataset, temp + random.randrange(1, vector + i))
    # print(raw)

    for i in range(1, count):
        fragment = raw[i * (block + gap) - (block + gap):i * (block + gap)]
        chunk.append([fragment[:block], fragment[-gap:]])
        difference = len(raw) / i * (block + gap)
        # print(i, i * (block + gap), len(raw), difference, chunk[-1])
    # print(chunk)

    for l0 in chunk:
        print(l0)
        print(l0[0])
        for c1 in l0[0]:
            print(c1, get_index(dataset, c1))
    print(c1, get_index(dataset, 'M'))
    print(c1, get_index(dataset, '}'))




create_data(3)


def dependency():
    pass


def plot():
    pass


def calc_pass(direction, vector):
    direction = str(int(hashlib.md5(direction.name.encode('utf-8')).hexdigest(), 16))
    vector = hashlib.md5(vector.name.encode('utf-8')).hexdigest()

    plot = vector.encode("utf-8")
    for _ in range(6):
        plot = base64.b64encode(plot)
    plot = plot.decode('utf-8')
    password = ''
    for index in range(1, len(direction) + 1):
        tmp_direction = str(int(direction) * index)
        key = int(tmp_direction[index - 1:index])
        password += plot[key]

    print(f'{password}, len {len(password)}')
    return password
