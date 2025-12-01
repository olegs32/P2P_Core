"""
Worker functions for multiprocessing hash computation

These functions must be in a separate importable module (not dynamically loaded)
because multiprocessing.Pool requires picklable functions.
"""

import hashlib
import hmac
from typing import List, Tuple, Optional


class HashAlgorithms:
    """Поддерживаемые hash алгоритмы"""

    ALGORITHMS = {
        # SHA-2 family
        "md5": lambda: hashlib.md5(),
        "sha1": lambda: hashlib.sha1(),
        "sha224": lambda: hashlib.sha224(),
        "sha256": lambda: hashlib.sha256(),
        "sha384": lambda: hashlib.sha384(),
        "sha512": lambda: hashlib.sha512(),
        "sha512_224": lambda: hashlib.new('sha512_224'),
        "sha512_256": lambda: hashlib.new('sha512_256'),

        # SHA-3 family
        "sha3_224": lambda: hashlib.sha3_224(),
        "sha3_256": lambda: hashlib.sha3_256(),
        "sha3_384": lambda: hashlib.sha3_384(),
        "sha3_512": lambda: hashlib.sha3_512(),
        "shake_128": lambda: hashlib.shake_128(),
        "shake_256": lambda: hashlib.shake_256(),

        # BLAKE2
        "blake2b": lambda: hashlib.blake2b(),
        "blake2s": lambda: hashlib.blake2s(),
    }

    @staticmethod
    def compute_hash(data: bytes, algo: str, output_length: int = None, **kwargs) -> bytes:
        """
        Вычисляет хеш с поддержкой различных алгоритмов

        Args:
            data: Данные для хеширования
            algo: Алгоритм хеширования
            output_length: Длина выходного хеша (для SHAKE)
            **kwargs: Дополнительные параметры (username, domain для NTLMv2)
        """
        if algo == "ntlm":
            # NTLM = MD4(UTF-16LE(password))
            try:
                password_str = data.decode('utf-8') if isinstance(data, bytes) else data
                password_utf16le = password_str.encode('utf-16le')
                return hashlib.new('md4', password_utf16le).digest()
            except ValueError:
                # MD4 не поддерживается в некоторых версиях Python
                # Используем passlib если доступна
                try:
                    from passlib.hash import nthash
                    password_str = data.decode('utf-8') if isinstance(data, bytes) else data
                    return bytes.fromhex(nthash.hash(password_str).split('$')[-1])
                except ImportError:
                    raise ValueError("MD4 not available. Install passlib: pip install passlib")

        elif algo == "ntlmv2":
            # NTLMv2 = HMAC-MD5(NTLM_hash, uppercase(username + domain))
            username = kwargs.get('username', '')
            domain = kwargs.get('domain', '')

            if not username:
                raise ValueError("NTLMv2 requires username parameter")

            # Сначала вычисляем NTLM hash
            ntlm_hash = HashAlgorithms.compute_hash(data, "ntlm")

            # NTLMv2 = HMAC-MD5(ntlm_hash, (username + domain).upper().utf-16le)
            identity = (username + domain).upper().encode('utf-16le')
            return hmac.new(ntlm_hash, identity, hashlib.md5).digest()

        elif algo.startswith("wpa"):
            # WPA/WPA2 обрабатывается отдельно
            raise ValueError("WPA requires SSID parameter, use compute_wpa_psk()")

        elif algo in HashAlgorithms.ALGORITHMS:
            hasher = HashAlgorithms.ALGORITHMS[algo]()
            hasher.update(data)

            # SHAKE требует output_length
            if algo.startswith("shake_"):
                if output_length is None:
                    output_length = 32  # default 256 bits
                return hasher.digest(output_length)
            else:
                return hasher.digest()
        else:
            raise ValueError(f"Unsupported algorithm: {algo}")

    @staticmethod
    def compute_wpa_psk(passphrase: str, ssid: str) -> bytes:
        """
        WPA/WPA2 PSK = PBKDF2-HMAC-SHA1(passphrase, SSID, 4096 iterations, 32 bytes)
        """
        return hashlib.pbkdf2_hmac(
            'sha1',
            passphrase.encode('utf-8'),
            ssid.encode('utf-8'),
            iterations=4096,
            dklen=32
        )


class MutationEngine:
    """Движок мутаций для dictionary attack"""

    @staticmethod
    def apply_mutations(word: str, rules: List[str]) -> List[str]:
        """
        Применяет правила мутации к слову

        Правила:
        - l: lowercase
        - u: uppercase
        - c: capitalize
        - $X: append character X
        - ^X: prepend character X
        - sa@: substitute 'a' with '@'
        - d: duplicate
        - r: reverse
        """
        mutations = [word]

        for rule in rules:
            new_mutations = []

            for w in mutations:
                if rule == "l":
                    new_mutations.append(w.lower())
                elif rule == "u":
                    new_mutations.append(w.upper())
                elif rule == "c":
                    new_mutations.append(w.capitalize())
                elif rule == "d":
                    new_mutations.append(w + w)
                elif rule == "r":
                    new_mutations.append(w[::-1])
                elif rule.startswith("$"):
                    # Append
                    new_mutations.append(w + rule[1:])
                elif rule.startswith("^"):
                    # Prepend
                    new_mutations.append(rule[1:] + w)
                elif rule.startswith("s"):
                    # Substitute: sab = replace 'a' with 'b'
                    if len(rule) == 3:
                        new_mutations.append(w.replace(rule[1], rule[2]))
                else:
                    new_mutations.append(w)

            mutations = new_mutations

        return mutations


def compute_brute_subchunk(args) -> Tuple[list, int]:
    """
    Worker function для brute force sub-chunk (picklable)

    Args:
        args: Tuple of (start_idx, end_idx, charset_list, length, hash_algo, ssid, target_hash_set_hex)

    Returns:
        Tuple of (solutions, hash_count)
    """
    (start_idx, end_idx, charset_list, length, hash_algo, ssid,
     target_hash_set_hex) = args

    solutions = []
    hash_count = 0
    base = len(charset_list)

    # Преобразуем hex hashes обратно в bytes
    if target_hash_set_hex:
        target_hash_set = {bytes.fromhex(h) for h in target_hash_set_hex}
    else:
        target_hash_set = None

    # index_to_combination inline
    def idx_to_comb(idx):
        result = [None] * length
        for pos in range(length - 1, -1, -1):
            result[pos] = charset_list[idx % base]
            idx //= base
        return ''.join(result)

    for idx in range(start_idx, end_idx):
        combination = idx_to_comb(idx)

        # Вычисляем хеш
        if hash_algo.startswith("wpa"):
            if not ssid:
                raise ValueError("SSID required for WPA/WPA2")
            hash_bytes = HashAlgorithms.compute_wpa_psk(combination, ssid)
        else:
            hash_bytes = HashAlgorithms.compute_hash(
                combination.encode(),
                hash_algo
            )

        # Проверка на совпадение
        if target_hash_set and hash_bytes in target_hash_set:
            hash_hex = hash_bytes.hex()
            solutions.append({
                "combination": combination,
                "hash": hash_hex,
                "index": idx,
                "mode": "brute"
            })

        hash_count += 1

    return solutions, hash_count


def compute_dict_subchunk(args) -> Tuple[list, int]:
    """
    Worker function для dictionary sub-chunk (picklable)

    Args:
        args: Tuple of (words, mutations, hash_algo, ssid, target_hash_set_hex, base_index)

    Returns:
        Tuple of (solutions, hash_count)
    """
    (words, mutations, hash_algo, ssid, target_hash_set_hex, base_index) = args

    solutions = []
    hash_count = 0

    # Преобразуем hex hashes обратно в bytes
    if target_hash_set_hex:
        target_hash_set = {bytes.fromhex(h) for h in target_hash_set_hex}
    else:
        target_hash_set = None

    mutation_engine = MutationEngine()

    for idx, word in enumerate(words):
        # Генерируем мутации
        if mutations:
            candidates = mutation_engine.apply_mutations(word, mutations)
        else:
            candidates = [word]

        # Проверяем каждый кандидат
        for candidate in candidates:
            # Вычисляем хеш
            if hash_algo.startswith("wpa"):
                if not ssid:
                    raise ValueError("SSID required for WPA/WPA2")
                hash_bytes = HashAlgorithms.compute_wpa_psk(candidate, ssid)
            else:
                hash_bytes = HashAlgorithms.compute_hash(
                    candidate.encode(),
                    hash_algo
                )

            # Проверка на совпадение
            if target_hash_set and hash_bytes in target_hash_set:
                hash_hex = hash_bytes.hex()
                solutions.append({
                    "combination": candidate,
                    "hash": hash_hex,
                    "index": base_index + idx,
                    "base_word": word,
                    "mode": "dictionary"
                })

            hash_count += 1

    return solutions, hash_count
