# import timeit
#
#
# def index_to_combination(index: int, charset: str, length: int) -> str:
#     """
#     Пример: index=5, charset="abc", length=2
#
#     Шаг 1: index=5, pos=1 (справа)
#            5 % 3 = 2 → charset[2] = 'c'
#            5 // 3 = 1
#
#     Шаг 2: index=1, pos=0 (слева)
#            1 % 3 = 1 → charset[1] = 'b'
#            1 // 3 = 0
#
#     Результат: "bc"
#     """
#     base = len(charset)
#     result = []
#
#     for _ in range(length):
#         result.append(charset[index % base])
#         index //= base
#
#     return ''.join(reversed(result))
#
#
# # Тест
# print(index_to_combination(5, "abc", 2))  # "bc"
# print(index_to_combination(7, "abc", 2))  # "cb"
#
#
# charset = 'zxcvbnmasdfghjklqwertyuiop12344567890!@#$%^&*()'
# # Тест на реальном алфавите
# length = 8
#
# # Прыжок на позицию 1,000,000
# t = timeit.timeit(
#     lambda: index_to_combination(1_000_000, charset, length),
#     number=10_000
# )
# print(f"Время на 10k преобразований: {t:.4f}s")
# # Ожидаемо: ~0.01s (около 1 микросекунды на преобразование)
#

import base64
import hashlib
import multiprocessing
import os
import random
import re
import threading
from pathlib import Path

root = Path('test_root')
os.makedirs(root, exist_ok=True)
plot_file = root / 'plot'


def create_data(count: int, dataset: str):
    """
    Create plot with specified length
    count: summary length
    """

    raw = ''  # create empty dataset
    for i in range(count):
        raw += dataset[random.randrange(0, len(dataset))]
    return raw


def extract_loop(data, dataset: str):
    pos = {}
    for index, sym in enumerate(data):
        pos[index] = dataset.index(sym)
    # print(str(pos)[:20])
    # print(data)
    for i in range(0, len(data)):
        walk = ''
        step, hop = i, i
        while step < len(data):
            sym_pos = data[hop]
            walk += sym_pos
            hop = pos[hop]
            step += 1
        match = re.search(r'(.+?)\1+', walk)
        if match and len(match.group(1)) > 13:
            # print(f"{i}/{len(data)}, {match.group(1)})")
            return match.group(1)
        # else:
        #     print(f"{i}/{len(data)}, {walk}")
    return None

    # return True


dataset = "qwertyuiopasdfghjklzxcvbnmQWERTYUIOPASDFGHJKLZXCVBNM1234567890!@#$%^&*()_+-=/?;:'[{]}"


def gen_key_plot(queue, dataset, counter):
    attempt = 1
    while attempt <= 10:
        data = create_data(10 ** 2 + 3247, dataset)
        loop = extract_loop(data, dataset)
        if loop:
            print(f"Найдено: попытка {attempt}, длина паттерна {len(loop)}")
            queue.put((loop, data))  # без block=False
            with counter.get_lock():
                counter.value += 1
            return  # выходим после первого успеха
        # else:
        #     print(f"Попытка {attempt}: None")
        attempt += 1


def generate_secure_password(data_file: Path, pattern: str, length: int = 100):
    """
    Генерация пароля с максимальной зависимостью от всего содержимого файла
    """
    data = data_file.read_text()
    data_len = len(data)

    # === СЛОЙ 1: Полный хеш всего файла ===
    full_hash = hashlib.sha512(data.encode()).digest()

    # === СЛОЙ 2: Позиции ВСЕХ вхождений символов паттерна ===
    all_positions = []
    for char in pattern:
        positions = [i for i, c in enumerate(data) if c == char]
        all_positions.extend(positions)
    pos_hash = hashlib.sha3_256(''.join(map(str, all_positions)).encode()).digest()

    # === СЛОЙ 3: Разбивка файла на блоки и хеш каждого ===
    block_size = max(1, data_len // 100)  # 100 блоков
    block_hashes = b''
    for i in range(0, data_len, block_size):
        block = data[i:i + block_size]
        block_hashes += hashlib.md5(block.encode()).digest()
    blocks_hash = hashlib.sha256(block_hashes).digest()

    # === СЛОЙ 4: Каскадное извлечение по индексам паттерна ===
    cascade = pattern.encode()
    for char in pattern:
        idx = ord(char) % data_len
        # Берём фрагмент вокруг индекса
        start = max(0, idx - 50)
        end = min(data_len, idx + 50)
        fragment = data[start:end]
        cascade = hashlib.blake2b(cascade + fragment.encode()).digest()

    # === СЛОЙ 5: XOR между различными срезами данных ===
    # Начало, середина, конец файла
    parts = [
        data[:data_len // 3],
        data[data_len // 3:2 * data_len // 3],
        data[2 * data_len // 3:]
    ]
    xor_result = 0
    for part in parts:
        part_hash = int.from_bytes(hashlib.sha256(part.encode()).digest(), 'big')
        xor_result ^= part_hash
    xor_bytes = xor_result.to_bytes(32, 'big')

    # === СЛОЙ 6: Статистика файла (частоты символов) ===
    freq = {}
    for char in set(data):
        freq[char] = data.count(char)
    freq_str = ''.join(f"{k}{v}" for k, v in sorted(freq.items())[:20])
    freq_hash = hashlib.sha256(freq_str.encode()).digest()

    # === СЛОЙ 7: Метаданные ===
    stat = data_file.stat()
    meta_hash = hashlib.sha256(
        f"{stat.st_size}{data_file.name}{data_len}".encode()
    ).digest()

    # === ОБЪЕДИНЯЕМ ВСЕ СЛОИ ===
    combined = (
            full_hash +
            pos_hash +
            blocks_hash +
            cascade +
            xor_bytes +
            freq_hash +
            meta_hash
    )

    # === ФИНАЛЬНАЯ ДЕРИВАЦИЯ ===
    # Количество итераций зависит от размера файла
    iterations = 500_000 + (data_len % 500_000)

    # Salt из паттерна и первых/последних символов файла
    salt = hashlib.sha256(
        (pattern + data[:100] + data[-100:]).encode()
    ).digest()

    final_key = hashlib.pbkdf2_hmac(
        'sha512',
        combined,
        salt,
        iterations,
        dklen=length
    )

    # === КОНВЕРТАЦИЯ В СИМВОЛЫ ===
    charset = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        "!@#$%^&*()_+-=[]{}|;:,.<>?/~`"
    )

    password = ''.join(charset[b % len(charset)] for b in final_key)

    return password


if __name__ == '__main__':
    if not os.path.exists(plot_file):
        counter = multiprocessing.Value('i', 0)
        manager = multiprocessing.Manager()
        q = manager.Queue()

        processes = [
            multiprocessing.Process(target=gen_key_plot, args=(q, dataset, counter))
            for _ in range(10)
        ]

        for p in processes:
            p.start()

        for p in processes:
            p.join()

        print(f'Найдено результатов: {counter.value}')

        keys = []
        while not q.empty():
            keys.append(q.get())

        if keys:
            best = max(keys, key=lambda x: len(x[0]))
            loop, data = best
            print(f"Лучший паттерн: {loop}, длина {len(loop)}")
            print(f"Данные: {data[:50]}...")
            with open(plot_file, 'w') as f:
                f.write(data)
        else:
            print("Ключ не создался, повторите попытку позже")

    else:
        data = open(plot_file).read()
        loop = extract_loop(data, dataset)
        print(loop, len(loop))
    if loop:
        password = generate_secure_password(plot_file, loop, 100)
        print(f"Пароль (100 символов): {password[:20]}...{password[-20:]}")
        print(f"Полный хеш файла: {hashlib.sha256(data.encode()).hexdigest()[:16]}...")
