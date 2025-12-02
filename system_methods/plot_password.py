"""
Plot-based password generation system

This module provides secure password generation based on a plot file.
The plot file contains encrypted data that is used to derive a
deterministic password through multiple layers of hashing and derivation.
"""

import hashlib
import multiprocessing
import os
import random
import re
from pathlib import Path
from typing import Optional, Tuple


# Character set for plot generation
DATASET = "qwertyuiopasdfghjklzxcvbnmQWERTYUIOPASDFGHJKLZXCVBNM1234567890!@#$%^&*()_+-=/?;:'[{]}"


def get_plot_path() -> Path:
    """
    Get the standard plot file path relative to p2p.py

    Returns:
        Path to the plot file in dist/services/plot
    """
    # Get the P2P_Core root directory (where p2p.py is located)
    root_dir = Path(__file__).parent.parent
    plot_path = root_dir / "dist" / "services" / "plot"

    return plot_path


def create_data(count: int, dataset: str) -> str:
    """
    Create plot data with specified length

    Args:
        count: Total length of the data to generate
        dataset: Character set to use for generation

    Returns:
        Generated random string
    """
    raw = ''
    for i in range(count):
        raw += dataset[random.randrange(0, len(dataset))]
    return raw


def extract_loop(data: str, dataset: str) -> Optional[str]:
    """
    Extract repeating pattern from plot data

    This function searches for a repeating pattern in the data by
    following index chains and detecting cycles.

    Args:
        data: Plot data to analyze
        dataset: Character set used in the plot

    Returns:
        The repeating pattern if found (length > 13), None otherwise
    """
    pos = {}
    for index, sym in enumerate(data):
        pos[index] = dataset.index(sym)

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
            return match.group(1)

    return None


def gen_key_plot(queue, dataset: str, counter):
    """
    Generate a valid plot with extractable pattern

    This is a worker function for multiprocessing that attempts
    to generate a plot with a valid repeating pattern.

    Args:
        queue: Multiprocessing queue to put results
        dataset: Character set to use
        counter: Shared counter for successful generations
    """
    attempt = 1
    while attempt <= 10:
        data = create_data(10 ** 2 + 3247, dataset)
        loop = extract_loop(data, dataset)
        if loop:
            print(f"Plot pattern found: attempt {attempt}, pattern length {len(loop)}")
            queue.put((loop, data))
            with counter.get_lock():
                counter.value += 1
            return
        attempt += 1


def generate_plot_file(plot_file: Path, num_processes: int = 10) -> bool:
    """
    Generate a new plot file with encrypted pattern

    Args:
        plot_file: Path where to save the plot file
        num_processes: Number of parallel processes to use

    Returns:
        True if plot was successfully generated, False otherwise
    """
    print(f"Generating plot file at: {plot_file}")

    # Ensure parent directory exists
    plot_file.parent.mkdir(parents=True, exist_ok=True)

    counter = multiprocessing.Value('i', 0)
    manager = multiprocessing.Manager()
    q = manager.Queue()

    processes = [
        multiprocessing.Process(target=gen_key_plot, args=(q, DATASET, counter))
        for _ in range(num_processes)
    ]

    for p in processes:
        p.start()

    for p in processes:
        p.join()

    print(f'Plot generation results found: {counter.value}')

    keys = []
    while not q.empty():
        keys.append(q.get())

    if keys:
        # Select the plot with the longest pattern
        best = max(keys, key=lambda x: len(x[0]))
        loop, data = best
        print(f"Best pattern selected: length {len(loop)}")

        # Save plot to file
        with open(plot_file, 'w') as f:
            f.write(data)

        print(f"Plot file created successfully: {plot_file}")
        return True
    else:
        print("ERROR: Plot generation failed. Please try again.")
        return False


def generate_secure_password(data_file: Path, pattern: str, length: int = 100) -> str:
    """
    Generate a secure password with maximum dependency on file contents

    This function uses multiple layers of hashing and key derivation
    to create a deterministic password from the plot file and pattern.

    The password generation process includes:
    - Full file hash (SHA-512)
    - Position-based hashing of pattern characters
    - Block-wise file hashing
    - Cascading extraction by pattern indices
    - XOR of file segments
    - Character frequency analysis
    - File metadata hashing
    - Final PBKDF2 key derivation with 500,000+ iterations

    Args:
        data_file: Path to the plot file
        pattern: The extracted repeating pattern
        length: Length of the password to generate (default: 100)

    Returns:
        Generated password string
    """
    data = data_file.read_text()
    data_len = len(data)

    # === LAYER 1: Full hash of entire file ===
    full_hash = hashlib.sha512(data.encode()).digest()

    # === LAYER 2: Positions of ALL pattern character occurrences ===
    all_positions = []
    for char in pattern:
        positions = [i for i, c in enumerate(data) if c == char]
        all_positions.extend(positions)
    pos_hash = hashlib.sha3_256(''.join(map(str, all_positions)).encode()).digest()

    # === LAYER 3: Split file into blocks and hash each ===
    block_size = max(1, data_len // 100)  # 100 blocks
    block_hashes = b''
    for i in range(0, data_len, block_size):
        block = data[i:i + block_size]
        block_hashes += hashlib.md5(block.encode()).digest()
    blocks_hash = hashlib.sha256(block_hashes).digest()

    # === LAYER 4: Cascading extraction by pattern indices ===
    cascade = pattern.encode()
    for char in pattern:
        idx = ord(char) % data_len
        # Take fragment around the index
        start = max(0, idx - 50)
        end = min(data_len, idx + 50)
        fragment = data[start:end]
        cascade = hashlib.blake2b(cascade + fragment.encode()).digest()

    # === LAYER 5: XOR between different file slices ===
    # Beginning, middle, end of file
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

    # === LAYER 6: File statistics (character frequencies) ===
    freq = {}
    for char in set(data):
        freq[char] = data.count(char)
    freq_str = ''.join(f"{k}{v}" for k, v in sorted(freq.items())[:20])
    freq_hash = hashlib.sha256(freq_str.encode()).digest()

    # === LAYER 7: File metadata ===
    stat = data_file.stat()
    meta_hash = hashlib.sha256(
        f"{stat.st_size}{data_file.name}{data_len}".encode()
    ).digest()

    # === COMBINE ALL LAYERS ===
    combined = (
        full_hash +
        pos_hash +
        blocks_hash +
        cascade +
        xor_bytes +
        freq_hash +
        meta_hash
    )

    # === FINAL DERIVATION ===
    # Iteration count depends on file size
    iterations = 500_000 + (data_len % 500_000)

    # Salt from pattern and first/last file characters
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

    # === CONVERT TO CHARACTERS ===
    charset = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        "!@#$%^&*()_+-=[]{}|;:,.<>?/~`"
    )

    password = ''.join(charset[b % len(charset)] for b in final_key)

    return password


def generate_password_from_plot(plot_file: Optional[Path] = None,
                                 password_length: int = 100) -> Tuple[bool, str]:
    """
    Generate password from plot file

    This is the main function that should be called to get a password.
    It will create the plot if it doesn't exist, or use existing plot.

    Args:
        plot_file: Path to plot file (uses default if None)
        password_length: Length of password to generate (default: 100)

    Returns:
        Tuple of (success: bool, password: str or error_message: str)
    """
    if plot_file is None:
        plot_file = get_plot_path()

    try:
        # Check if plot exists
        if not plot_file.exists():
            print(f"Plot file not found at: {plot_file}")
            print("Generating new plot file...")

            if not generate_plot_file(plot_file):
                return False, "Failed to generate plot file"

        # Read plot and extract pattern
        data = plot_file.read_text()
        pattern = extract_loop(data, DATASET)

        if not pattern:
            return False, f"Failed to extract pattern from plot file: {plot_file}"

        print(f"Extracted pattern length: {len(pattern)}")

        # Generate password from plot
        password = generate_secure_password(plot_file, pattern, password_length)

        # Verify password was generated
        if not password or len(password) < password_length:
            return False, "Generated password is invalid"

        return True, password

    except Exception as e:
        return False, f"Error generating password from plot: {e}"


if __name__ == "__main__":
    """
    Test the plot password generation
    """
    print("Testing plot password generation...")

    success, result = generate_password_from_plot()

    if success:
        password = result
        print(f"✓ Password generated successfully")
        print(f"  Length: {len(password)}")
        print(f"  First 20 chars: {password[:20]}...")
        print(f"  Last 20 chars: ...{password[-20:]}")
    else:
        print(f"✗ Error: {result}")
