# Hash Algorithms & Enhanced Features Roadmap

## 📋 Содержание
1. [Криптографические хеши](#криптографические-хеши)
2. [WiFi/Wireless специфичные](#wifiwireless-специфичные)
3. [Системные и приложения](#системные-и-приложения)
4. [Расширенные возможности](#расширенные-возможности)
5. [Приоритеты реализации](#приоритеты-реализации)

---

## Криптографические хеши

### Базовые (уже поддерживаются)
- ✅ **MD5** - 128 bit, устаревший, быстрый
- ✅ **SHA-1** - 160 bit, устаревший, средняя скорость
- ✅ **SHA-256** - 256 bit, современный стандарт
- ✅ **SHA-512** - 512 bit, более медленный

### Требуют добавления

#### SHA-2 семейство
- **SHA-224** - 224 bit, усеченный SHA-256
- **SHA-384** - 384 bit, усеченный SHA-512
- **SHA-512/224** - 224 bit вариант SHA-512
- **SHA-512/256** - 256 bit вариант SHA-512

**Применение:** Digital signatures, certificates, blockchain (Bitcoin uses SHA-256)

**Сложность реализации:** ⭐ (trivial, уже в hashlib)

```python
hashlib.sha224(data).hexdigest()
hashlib.sha384(data).hexdigest()
```

#### SHA-3 (Keccak) семейство
- **SHA3-224** - 224 bit
- **SHA3-256** - 256 bit
- **SHA3-384** - 384 bit
- **SHA3-512** - 512 bit
- **SHAKE128** - variable output (128 bit security)
- **SHAKE256** - variable output (256 bit security)

**Применение:** Next-gen криптография, Ethereum (Keccak-256)

**Сложность:** ⭐ (в hashlib с Python 3.6+)

```python
hashlib.sha3_256(data).hexdigest()
```

#### BLAKE семейство
- **BLAKE2b** - до 512 bit, оптимизирован для 64-bit
- **BLAKE2s** - до 256 bit, оптимизирован для 32-bit

**Применение:** Password hashing, checksums, Zcash blockchain

**Сложность:** ⭐ (в hashlib с Python 3.6+)

```python
hashlib.blake2b(data, digest_size=32).hexdigest()
```

#### Другие
- **RIPEMD-160** - 160 bit, используется в Bitcoin
- **GOST R 34.11-94** - Russian standard
- **Whirlpool** - 512 bit

**Сложность:** ⭐⭐ (нужна библиотека `pycryptodome`)

---

## WiFi/Wireless специфичные

### WPA/WPA2 PSK (PBKDF2-HMAC-SHA1)

**Описание:** Самый популярный WiFi протокол

**Алгоритм:**
```
PMK = PBKDF2-HMAC-SHA1(passphrase, SSID, 4096 iterations, 256 bits)
```

**Что нужно для атаки:**
1. SSID (network name)
2. 4-way handshake (из PCAP файла)
3. Passphrase dictionary/bruteforce

**Особенности:**
- Очень медленный (4096 итераций PBKDF2!)
- Скорость: ~1000-5000 паролей/сек на CPU
- GPU ускорение дает ~100k-1M паролей/сек

**Сложность:** ⭐⭐⭐⭐

**Пример реализации:**
```python
import hashlib
from binascii import hexlify

def wpa_psk_to_pmk(passphrase: str, ssid: str) -> bytes:
    """
    Генерирует PMK (Pairwise Master Key) из пароля и SSID
    """
    return hashlib.pbkdf2_hmac(
        'sha1',
        passphrase.encode('utf-8'),
        ssid.encode('utf-8'),
        iterations=4096,
        dklen=32  # 256 bits
    )

def verify_handshake(pmk: bytes, handshake_data: dict) -> bool:
    """
    Проверяет PMK против 4-way handshake
    """
    # Simplified - реально нужно:
    # 1. Извлечь nonces, MAC addresses из handshake
    # 2. Вычислить PTK (Pairwise Transient Key)
    # 3. Вычислить MIC (Message Integrity Code)
    # 4. Сравнить с MIC из handshake
    pass
```

**Зависимости:**
- `scapy` для парсинга PCAP
- `pyaircrack-ng` для работы с handshakes

### PMKID Attack (WPA/WPA2 без handshake)

**Описание:** Атака на PMKID из первого EAPOL frame

**Алгоритм:**
```
PMKID = HMAC-SHA1-128(PMK, "PMK Name" | MAC_AP | MAC_STA)
```

**Что нужно:**
1. SSID
2. PMKID (из первого EAPOL frame, без полного handshake!)
3. MAC адреса AP и клиента

**Преимущества:**
- Не нужен полный handshake
- Проще захватить (только первый пакет)

**Сложность:** ⭐⭐⭐⭐

### WPA3 (SAE - Simultaneous Authentication of Equals)

**Описание:** Новый стандарт, заменяет WPA2

**Алгоритм:** Dragonfly key exchange

**Особенности:**
- Защита от offline атак
- Perfect Forward Secrecy
- Сложнее брутфорсить

**Сложность:** ⭐⭐⭐⭐⭐ (очень сложная реализация)

### WEP (устаревший)

**Описание:** Старый, сломанный протокол

**Алгоритм:** RC4 stream cipher

**Особенности:**
- Легко ломается (нужны только пакеты, не пароль)
- Для брутфорса не подходит (статистическая атака)

**Приоритет:** 🔴 Низкий (устаревший)

---

## Системные и приложения

### Windows

#### NTLM / NTLMv2
**Описание:** Windows password hashing

**NTLM алгоритм:**
```
NTLM = MD4(UTF-16LE(password))
```

**NTLMv2:**
```
NTLMv2 = HMAC-MD5(NTLM, username + domain)
```

**Применение:**
- Windows login
- SMB authentication
- Pass-the-hash attacks

**Сложность:** ⭐⭐

**Пример:**
```python
import hashlib

def ntlm_hash(password: str) -> str:
    """Generate NTLM hash"""
    return hashlib.new('md4', password.encode('utf-16le')).hexdigest()
```

#### LM Hash (устаревший)
**Описание:** Старый Windows hash

**Особенности:**
- Чрезвычайно слабый
- Делит пароль на 2 части по 7 символов
- Приводит к uppercase

**Сложность:** ⭐

**Приоритет:** 🔴 Низкий (устаревший, отключен в современных Windows)

### Unix/Linux

#### Unix crypt variants

**DES crypt:**
```
Format: $1$salt$hash
Algorithm: DES with 25 rounds
```

**MD5 crypt:**
```
Format: $1$salt$hash
Algorithm: MD5 with 1000 rounds
```

**SHA-256 crypt:**
```
Format: $5$rounds=5000$salt$hash
Algorithm: SHA-256 with variable rounds
```

**SHA-512 crypt:**
```
Format: $6$rounds=5000$salt$hash
Algorithm: SHA-512 with variable rounds
```

**Сложность:** ⭐⭐⭐

**Библиотека:**
```python
from passlib.hash import sha512_crypt

# Verify
sha512_crypt.verify(password, hash_string)

# Generate
sha512_crypt.hash(password, rounds=5000)
```

### Базы данных

#### MySQL
**MySQL 3.x/4.x:**
```python
import hashlib
def mysql_old(password: str) -> str:
    hash1 = hashlib.sha1(password.encode()).digest()
    return hashlib.sha1(hash1).hexdigest()
```

**MySQL 5.x+:**
```
Format: *HASH (SHA1 of SHA1)
```

**Сложность:** ⭐

#### PostgreSQL
**Формат:**
```
md5{hash}
где hash = MD5(password + username)
```

**Сложность:** ⭐

#### MongoDB
**Формат:**
```
MD5(username + ":mongo:" + password)
```

**Сложность:** ⭐

### Приложения

#### bcrypt
**Описание:** Адаптивная password hashing функция

**Особенности:**
- Очень медленная (by design)
- Настраиваемый cost factor (rounds)
- Используется во многих современных приложениях

**Формат:**
```
$2a$12$salt22characters...hash31characters
```

**Сложность:** ⭐⭐⭐

**Скорость:** ~10-100 паролей/сек (очень медленно!)

```python
import bcrypt

# Hash
hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))

# Verify
bcrypt.checkpw(password.encode(), hashed)
```

#### scrypt
**Описание:** Memory-hard функция (защита от GPU/ASIC)

**Параметры:**
- N: CPU/memory cost
- r: block size
- p: parallelization

**Применение:** Litecoin, Tarsnap

**Сложность:** ⭐⭐⭐⭐

```python
import hashlib

hashlib.scrypt(
    password.encode(),
    salt=salt,
    n=16384,  # CPU cost
    r=8,      # block size
    p=1,      # parallelization
    dklen=32
)
```

#### Argon2
**Описание:** Победитель Password Hashing Competition 2015

**Варианты:**
- Argon2d: data-dependent (защита от GPU)
- Argon2i: data-independent (защита от side-channel)
- Argon2id: гибрид (рекомендуется)

**Применение:** Современный стандарт для password hashing

**Сложность:** ⭐⭐⭐⭐

```python
from argon2 import PasswordHasher

ph = PasswordHasher(
    time_cost=2,        # iterations
    memory_cost=102400, # KB
    parallelism=8       # threads
)

# Hash
hash_val = ph.hash(password)

# Verify
ph.verify(hash_val, password)
```

#### PBKDF2
**Описание:** Password-Based Key Derivation Function

**Варианты:**
- PBKDF2-HMAC-SHA1
- PBKDF2-HMAC-SHA256
- PBKDF2-HMAC-SHA512

**Применение:**
- WPA/WPA2 (4096 iterations)
- iOS (10,000+ iterations)
- macOS keychain

**Сложность:** ⭐⭐

```python
hashlib.pbkdf2_hmac(
    'sha256',
    password.encode(),
    salt,
    iterations=100000,
    dklen=32
)
```

---

## Расширенные возможности

### 1. Словари и Wordlists

**Описание:** Использование готовых словарей вместо bruteforce

**Файловые форматы:**
- Plain text (one password per line)
- Compressed (gz, bz2, xz)
- Remote URLs
- Large files (streaming)

**Популярные словари:**
- rockyou.txt (14M паролей)
- crackstation.txt (1.4B паролей)
- SecLists
- weakpass.com

**Архитектура:**

```python
class DictionaryAttack:
    """
    Словарная атака с поддержкой потоковой обработки
    """

    def __init__(self, wordlist_path: str, chunk_size: int = 10000):
        self.wordlist_path = wordlist_path
        self.chunk_size = chunk_size

    async def distribute_wordlist(self, workers: List[str]):
        """
        Распределяет словарь между воркерами

        Стратегии:
        1. По строкам: Worker-1 получает строки 0-1M, Worker-2: 1M-2M
        2. Round-robin: Worker-1 берет строки 0,3,6..., Worker-2: 1,4,7...
        3. Hash-based: hash(password) % num_workers
        """
        total_lines = count_lines(self.wordlist_path)
        lines_per_worker = total_lines // len(workers)

        assignments = {}
        for i, worker_id in enumerate(workers):
            start = i * lines_per_worker
            end = start + lines_per_worker if i < len(workers)-1 else total_lines

            assignments[worker_id] = {
                "wordlist": self.wordlist_path,
                "start_line": start,
                "end_line": end
            }

        return assignments

    def read_chunk(self, start_line: int, chunk_size: int):
        """
        Читает чанк из словаря (с поддержкой сжатых файлов)
        """
        if self.wordlist_path.endswith('.gz'):
            import gzip
            f = gzip.open(self.wordlist_path, 'rt')
        else:
            f = open(self.wordlist_path, 'r')

        # Skip to start_line
        for _ in range(start_line):
            next(f)

        # Read chunk
        chunk = []
        for _ in range(chunk_size):
            try:
                chunk.append(next(f).strip())
            except StopIteration:
                break

        f.close()
        return chunk
```

**Gossip структура:**
```python
{
    "attack_mode": "dictionary",
    "wordlist": {
        "path": "/path/to/rockyou.txt",
        "total_lines": 14344392,
        "hash_algo": "sha256"
    },
    "chunks": {
        1: {"worker": "worker-001", "start_line": 0, "end_line": 1000000},
        2: {"worker": "worker-002", "start_line": 1000000, "end_line": 2000000}
    }
}
```

### 2. Правила мутаций (Mutation Rules)

**Описание:** Генерация вариаций паролей на основе правил (как в Hashcat)

**Примеры правил:**

```
l         - lowercase all
u         - uppercase all
c         - capitalize first letter
$1        - append "1"
^@        - prepend "@"
sa@       - substitute 'a' with '@'
d         - duplicate entire word
r         - reverse
```

**Примеры применения:**
```
password → Password (c)
password → password1 ($1)
password → p@ssword (sa@)
password → passwordpassword (d)
password → drowssap (r)
```

**Реализация:**

```python
class MutationEngine:
    """
    Применяет правила мутаций к словам
    """

    def __init__(self, rules_file: str = None):
        self.rules = self.load_rules(rules_file) if rules_file else []

    def apply_rule(self, word: str, rule: str) -> str:
        """Применяет одно правило"""
        result = word

        for op in rule:
            if op == 'l':
                result = result.lower()
            elif op == 'u':
                result = result.upper()
            elif op == 'c':
                result = result.capitalize()
            elif op == 'd':
                result = result + result
            elif op == 'r':
                result = result[::-1]
            elif op.startswith('$'):
                # Append character
                result = result + op[1]
            elif op.startswith('^'):
                # Prepend character
                result = op[1] + result
            elif op.startswith('s'):
                # Substitute
                old_char = op[1]
                new_char = op[2]
                result = result.replace(old_char, new_char)

        return result

    def mutate(self, word: str) -> Generator[str, None, None]:
        """Генерирует все вариации слова"""
        # Original
        yield word

        # Apply each rule
        for rule in self.rules:
            yield self.apply_rule(word, rule)

        # Common mutations (если нет rules файла)
        if not self.rules:
            yield word.lower()
            yield word.upper()
            yield word.capitalize()
            yield word + '1'
            yield word + '123'
            yield word + '!'
            yield word.replace('a', '@')
            yield word.replace('e', '3')
            yield word.replace('i', '1')
            yield word.replace('o', '0')
            yield word.replace('s', '$')

# Использование
engine = MutationEngine()

for variant in engine.mutate("password"):
    hash_val = hashlib.sha256(variant.encode()).hexdigest()
    if hash_val == target:
        print(f"Found: {variant}")
```

**Gossip интеграция:**
```python
{
    "attack_mode": "dictionary+rules",
    "wordlist": "rockyou.txt",
    "rules": ["c", "$1", "$!", "sa@"],
    "estimated_combinations": 14344392 * 4  # words × rules
}
```

### 3. Гибридные атаки

**Описание:** Комбинация словаря и маски/bruteforce

**Примеры:**

**Wordlist + Suffix mask:**
```
password + ?d?d?d
→ password000, password001, ..., password999
```

**Wordlist + Prefix mask:**
```
?u?l + password
→ Apassword, Bpassword, ..., Zapassword, Zbpassword, ...
```

**Wordlist + Combinator:**
```
word1 + word2
→ password123, admin2023, user@domain
```

**Реализация:**

```python
class HybridAttack:
    """
    Гибридная атака: словарь + маска
    """

    def __init__(self, wordlist: str, mask: str, position: str = "suffix"):
        self.wordlist = wordlist
        self.mask = mask  # e.g., "?d?d?d"
        self.position = position  # "prefix" or "suffix"

    def parse_mask(self, mask: str) -> List[str]:
        """
        Парсит маску в список charsets
        ?l = lowercase
        ?u = uppercase
        ?d = digits
        ?s = special
        """
        charsets = {
            '?l': string.ascii_lowercase,
            '?u': string.ascii_uppercase,
            '?d': string.digits,
            '?s': '!@#$%^&*()'
        }

        result = []
        i = 0
        while i < len(mask):
            if mask[i:i+2] in charsets:
                result.append(charsets[mask[i:i+2]])
                i += 2
            else:
                result.append(mask[i])
                i += 1

        return result

    def generate_candidates(self, word: str) -> Generator[str, None, None]:
        """Генерирует кандидатов для одного слова"""
        charsets = self.parse_mask(self.mask)

        # Generate all mask combinations
        for mask_combo in itertools.product(*charsets):
            mask_str = ''.join(mask_combo)

            if self.position == "suffix":
                yield word + mask_str
            elif self.position == "prefix":
                yield mask_str + word
            else:  # both
                yield mask_str + word + mask_str

# Использование
hybrid = HybridAttack("rockyou.txt", "?d?d?d", position="suffix")

with open("rockyou.txt") as f:
    for word in f:
        word = word.strip()
        for candidate in hybrid.generate_candidates(word):
            # Test candidate
            pass
```

### 4. Rainbow Tables

**Описание:** Предвычисленные таблицы hash → password

**Концепция:**
```
Вместо вычисления хеша на лету:
1. Предвычислить миллиарды hash-password пар
2. Сохранить в таблицу (с reduction функцией для сжатия)
3. Поиск за O(1) вместо O(n)
```

**Trade-off:**
- ✅ Очень быстрый поиск
- ❌ Огромные требования к диску (TB)
- ❌ Не работает с salt

**Приоритет:** 🟡 Средний (сложная реализация, большой размер)

### 5. Импорт/Экспорт результатов

**Форматы:**

**JSON:**
```json
{
    "job_id": "wifi-crack-1",
    "started_at": "2025-11-18T20:00:00Z",
    "completed_at": "2025-11-18T21:30:00Z",
    "total_hashes": 1500000000,
    "solutions": [
        {
            "hash": "5e884898...",
            "password": "MyP@ssw0rd",
            "found_at": "2025-11-18T20:45:12Z",
            "worker_id": "worker-003"
        }
    ],
    "statistics": {
        "total_time_seconds": 5400,
        "average_hash_rate": 277777,
        "peak_hash_rate": 350000
    }
}
```

**CSV:**
```csv
hash,password,found_at,worker_id
5e884898...,MyP@ssw0rd,2025-11-18T20:45:12Z,worker-003
```

**Hashcat potfile format:**
```
5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8:MyP@ssw0rd
```

### 6. Hash Identification

**Описание:** Автоматическое определение типа хеша

**Примеры:**

```python
def identify_hash(hash_string: str) -> List[str]:
    """
    Определяет возможные типы хеша по формату
    """
    hash_len = len(hash_string)
    possible_types = []

    if hash_len == 32:
        possible_types.extend(['MD5', 'NTLM', 'MD4'])

    elif hash_len == 40:
        possible_types.extend(['SHA-1', 'MySQL5'])

    elif hash_len == 64:
        possible_types.extend(['SHA-256', 'SHA3-256', 'BLAKE2s'])

    elif hash_len == 128:
        possible_types.extend(['SHA-512', 'SHA3-512', 'BLAKE2b', 'Whirlpool'])

    # Format-based detection
    if hash_string.startswith('$1$'):
        possible_types.append('MD5 crypt')

    elif hash_string.startswith('$5$'):
        possible_types.append('SHA-256 crypt')

    elif hash_string.startswith('$6$'):
        possible_types.append('SHA-512 crypt')

    elif hash_string.startswith('$2a$') or hash_string.startswith('$2b$'):
        possible_types.append('bcrypt')

    elif hash_string.startswith('$argon2'):
        possible_types.append('Argon2')

    return possible_types
```

### 7. Benchmarking Mode

**Описание:** Тестирование производительности кластера

**Функции:**
- Замер скорости для каждого алгоритма
- Сравнение производительности воркеров
- Оптимальный chunk_size для каждого алгоритма

**Пример:**

```python
async def benchmark_algorithm(algo: str, duration: int = 60):
    """
    Тестирует производительность алгоритма

    Returns:
        {
            "algorithm": "sha256",
            "hashes_per_second": 125000,
            "duration": 60,
            "total_hashes": 7500000
        }
    """
    start_time = time.time()
    count = 0

    while time.time() - start_time < duration:
        # Test in batches
        for i in range(10000):
            hashlib.sha256(f"test{count}".encode()).digest()
            count += 1

    elapsed = time.time() - start_time
    hash_rate = count / elapsed

    return {
        "algorithm": algo,
        "hashes_per_second": hash_rate,
        "duration": elapsed,
        "total_hashes": count
    }
```

### 8. Pause/Resume функциональность

**Сохранение состояния:**

```python
{
    "job_id": "wifi-crack-1",
    "status": "paused",
    "paused_at": "2025-11-18T21:00:00Z",
    "state": {
        "current_version": 15,
        "completed_chunks": [1, 2, 3, 4, 5],
        "in_progress_chunks": {
            6: {"worker": "worker-001", "progress": 750000}
        },
        "total_processed": 5750000,
        "total_combinations": 10000000
    }
}
```

**Resume:**
- Восстановить состояние из файла
- Переназначить in_progress chunks
- Продолжить с текущей позиции

### 9. PCAP Parsing для WiFi

**Описание:** Извлечение handshakes из PCAP файлов

**Библиотеки:**
- `scapy` для парсинга пакетов
- `pyaircrack-ng` для работы с WiFi

**Пример:**

```python
from scapy.all import rdpcap, EAPOL

def extract_handshake(pcap_file: str) -> dict:
    """
    Извлекает WPA handshake из PCAP
    """
    packets = rdpcap(pcap_file)

    eapol_packets = [p for p in packets if p.haslayer(EAPOL)]

    if len(eapol_packets) >= 4:
        # Found 4-way handshake
        handshake = {
            "ssid": extract_ssid(packets),
            "ap_mac": eapol_packets[0].addr2,
            "client_mac": eapol_packets[0].addr1,
            "nonce_ap": extract_nonce(eapol_packets[0]),
            "nonce_client": extract_nonce(eapol_packets[1]),
            "mic": extract_mic(eapol_packets[3])
        }
        return handshake

    return None
```

### 10. Multi-target Mode

**Описание:** Одновременный перебор нескольких хешей

**Преимущество:** Один проход - несколько целей

```python
class MultiTargetAttack:
    """
    Одновременная атака на несколько хешей
    """

    def __init__(self, target_hashes: List[str], hash_algo: str):
        self.targets = set(target_hashes)  # Set for O(1) lookup
        self.hash_algo = hash_algo
        self.found = {}

    def check_candidate(self, password: str) -> Optional[str]:
        """
        Проверяет пароль против всех целей
        """
        hash_val = hashlib.new(self.hash_algo, password.encode()).hexdigest()

        if hash_val in self.targets:
            self.found[hash_val] = password
            self.targets.remove(hash_val)  # Удаляем найденный
            return hash_val

        return None

# Использование
targets = ["5e884898...", "e99a18c4...", "7c4a8d0..."]
attack = MultiTargetAttack(targets, "sha256")

for password in generate_candidates():
    if found_hash := attack.check_candidate(password):
        print(f"Found: {attack.found[found_hash]}")

    if not attack.targets:
        print("All targets found!")
        break
```

---

## Приоритеты реализации

### 🔴 Высокий приоритет (Phase 1)

1. **SHA-2/SHA-3 семейство** - тривиальная реализация, важно
2. **NTLM/NTLMv2** - популярный для pentesting
3. **Dictionary attack** - критично для практического использования
4. **Mutation rules** - резко увеличивает эффективность словарей
5. **Multi-target mode** - простая оптимизация с большим эффектом
6. **Pause/Resume** - важно для длинных задач
7. **Результаты в JSON/CSV** - базовая функциональность

### 🟡 Средний приоритет (Phase 2)

8. **WPA/WPA2 PSK** - очень популярно, но сложно
9. **PMKID attack** - более простая альтернатива handshake
10. **Hybrid attacks** - комбинация словаря и маски
11. **PBKDF2 variants** - iOS, macOS, различные приложения
12. **bcrypt** - современные веб-приложения
13. **Unix crypt variants** - Linux/Unix системы
14. **Hash identification** - удобство использования
15. **Benchmarking** - оптимизация и диагностика
16. **PCAP parsing** - автоматизация WiFi attacks

### 🟢 Низкий приоритет (Phase 3)

17. **scrypt/Argon2** - редко встречаются, сложные
18. **Rainbow tables** - огромные требования к диску
19. **WPA3** - новый стандарт, очень сложный
20. **Database-specific hashes** - MySQL/PostgreSQL/MongoDB
21. **BLAKE2/RIPEMD** - нишевые алгоритмы
22. **LM Hash/WEP** - устаревшие

---

## Архитектурные изменения

### Абстракция алгоритмов

```python
class HashAlgorithm(ABC):
    """Базовый класс для всех алгоритмов"""

    @abstractmethod
    def hash(self, data: str) -> str:
        """Вычисляет хеш"""
        pass

    @abstractmethod
    def verify(self, data: str, hash_value: str) -> bool:
        """Проверяет соответствие"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Название алгоритма"""
        pass

    @property
    @abstractmethod
    def speed_rating(self) -> int:
        """Относительная скорость (1=очень медленный, 10=очень быстрый)"""
        pass

class SHA256Algorithm(HashAlgorithm):
    name = "SHA-256"
    speed_rating = 7

    def hash(self, data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()

    def verify(self, data: str, hash_value: str) -> bool:
        return self.hash(data) == hash_value

class WPA2Algorithm(HashAlgorithm):
    name = "WPA2-PSK"
    speed_rating = 1  # Очень медленный

    def __init__(self, ssid: str, handshake_data: dict):
        self.ssid = ssid
        self.handshake = handshake_data

    def hash(self, passphrase: str) -> str:
        pmk = hashlib.pbkdf2_hmac(
            'sha1',
            passphrase.encode(),
            self.ssid.encode(),
            4096,
            32
        )
        return pmk.hex()

    def verify(self, passphrase: str, hash_value: str) -> bool:
        pmk = bytes.fromhex(hash_value)
        return self._verify_handshake(pmk, self.handshake)
```

### Registry алгоритмов

```python
class AlgorithmRegistry:
    """Реестр всех поддерживаемых алгоритмов"""

    _algorithms = {}

    @classmethod
    def register(cls, algo_class: Type[HashAlgorithm]):
        cls._algorithms[algo_class.name] = algo_class

    @classmethod
    def get(cls, name: str) -> HashAlgorithm:
        return cls._algorithms.get(name)

    @classmethod
    def list_all(cls) -> List[str]:
        return list(cls._algorithms.keys())

# Регистрация
AlgorithmRegistry.register(SHA256Algorithm)
AlgorithmRegistry.register(WPA2Algorithm)
```

---

## Итого: Enhanced Feature List

**Алгоритмы (30+):**
- 15 криптографических хешей
- 5 WiFi/Wireless
- 10+ системных и приложений

**Режимы атак (6):**
- Bruteforce (уже есть)
- Dictionary
- Dictionary + Rules
- Hybrid (wordlist + mask)
- Multi-target
- Rainbow tables

**Дополнительно (10):**
- PCAP parsing
- Hash identification
- Benchmarking
- Pause/Resume
- Import/Export results
- Progress statistics
- Estimated time remaining
- Worker performance tuning
- Algorithm auto-selection
- Web UI для всех функций

**Какие фичи реализовать в первую очередь?**
