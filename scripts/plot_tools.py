import timeit


def index_to_combination(index: int, charset: str, length: int) -> str:
    """
    Пример: index=5, charset="abc", length=2

    Шаг 1: index=5, pos=1 (справа)
           5 % 3 = 2 → charset[2] = 'c'
           5 // 3 = 1

    Шаг 2: index=1, pos=0 (слева)
           1 % 3 = 1 → charset[1] = 'b'
           1 // 3 = 0

    Результат: "bc"
    """
    base = len(charset)
    result = []

    for _ in range(length):
        result.append(charset[index % base])
        index //= base

    return ''.join(reversed(result))


# Тест
print(index_to_combination(5, "abc", 2))  # "bc"
print(index_to_combination(7, "abc", 2))  # "cb"


charset = 'zxcvbnmasdfghjklqwertyuiop12344567890!@#$%^&*()'
# Тест на реальном алфавите
length = 8

# Прыжок на позицию 1,000,000
t = timeit.timeit(
    lambda: index_to_combination(1_000_000, charset, length),
    number=10_000
)
print(f"Время на 10k преобразований: {t:.4f}s")
# Ожидаемо: ~0.01s (около 1 микросекунды на преобразование)



exit(0)








exit(0)
import base64
import hashlib
import os
import random
from pathlib import Path

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
        res += l[step_index ]
    return res


def create_data(count:int, block: int = 10, gap:int = 2):
    """
    Create plot with specified characteristics
    length: summary length
    block: size of payload block
    gap: control check data
    """
    wordset = "qwertyuiopasdfghjklzxcvbnmQWERTYUIOPASDFGHJKLZXCVBNM"
    symset = "1234567890!@#$%^&*()_+-=/?;:'[{]}"
    dataset = [wordset, symset]
    vector = int(random.random() * 10 ** 3 )
    print(vector)
    temp = vector
    iters = count * (block + gap)
    raw = '' # create empty dataset
    for i in range(iters):
        raw += gen_set(dataset, temp + random.randrange(2, vector + i))
    print(raw)
    # create control points
    for index, i in enumerate(range(iters)):
        service = 4
        skip = index * iters + service
        raw = raw[:index + block] + '__' + raw[index + block + gap:]
        print(raw)
    print(raw)


create_data(4)

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
