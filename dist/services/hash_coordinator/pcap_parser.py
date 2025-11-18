"""
PCAP Parser для WiFi handshakes

Извлекает WPA/WPA2 handshakes из PCAP файлов для последующего crack'а
"""

import struct
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger("PCAP_Parser")


class PCAPParser:
    """Парсер PCAP файлов для извлечения WiFi handshakes"""

    # PCAP magic numbers
    PCAP_MAGIC = 0xa1b2c3d4
    PCAP_MAGIC_NANO = 0xa1b23c4d
    PCAP_NG_MAGIC = 0x0a0d0d0a

    # Radiotap header field types
    RADIOTAP_FLAGS = 1
    RADIOTAP_RATE = 2
    RADIOTAP_CHANNEL = 3

    def __init__(self, pcap_file: str):
        self.pcap_file = pcap_file
        self.handshakes = []

    def parse(self) -> List[Dict]:
        """
        Парсит PCAP и извлекает handshakes

        Returns:
            List of handshake dicts with ESSID, BSSID, and EAPOL frames
        """
        try:
            with open(self.pcap_file, 'rb') as f:
                data = f.read()

            # Определяем формат
            magic = struct.unpack('I', data[0:4])[0]

            if magic == self.PCAP_MAGIC or magic == self.PCAP_MAGIC_NANO:
                return self._parse_pcap(data)
            elif magic == self.PCAP_NG_MAGIC:
                return self._parse_pcapng(data)
            else:
                raise ValueError(f"Unknown PCAP format: {hex(magic)}")

        except Exception as e:
            logger.error(f"Failed to parse PCAP: {e}")
            return []

    def _parse_pcap(self, data: bytes) -> List[Dict]:
        """Парсит классический PCAP формат"""
        handshakes = {}
        offset = 24  # Skip global header

        while offset < len(data):
            # Packet header (16 bytes)
            if offset + 16 > len(data):
                break

            ts_sec, ts_usec, incl_len, orig_len = struct.unpack('IIII', data[offset:offset+16])
            offset += 16

            # Packet data
            if offset + incl_len > len(data):
                break

            packet_data = data[offset:offset+incl_len]
            offset += incl_len

            # Извлекаем EAPOL из пакета
            eapol_info = self._extract_eapol(packet_data)

            if eapol_info:
                bssid = eapol_info['bssid']
                if bssid not in handshakes:
                    handshakes[bssid] = {
                        'bssid': bssid,
                        'essid': eapol_info.get('essid', ''),
                        'eapol_frames': []
                    }

                handshakes[bssid]['eapol_frames'].append({
                    'timestamp': ts_sec + ts_usec / 1000000.0,
                    'frame': eapol_info['eapol_data'],
                    'message_type': eapol_info['message_type']
                })

        # Фильтруем только полные handshakes (4-way handshake)
        complete_handshakes = []
        for bssid, hs in handshakes.items():
            if self._is_complete_handshake(hs['eapol_frames']):
                complete_handshakes.append(hs)

        return complete_handshakes

    def _parse_pcapng(self, data: bytes) -> List[Dict]:
        """Парсит PCAP-NG формат"""
        # Упрощенная реализация - основная логика аналогична _parse_pcap
        # В реальном случае потребуется полный парсер PCAP-NG блоков
        logger.warning("PCAP-NG parsing is simplified, may not extract all handshakes")
        return []

    def _extract_eapol(self, packet_data: bytes) -> Optional[Dict]:
        """
        Извлекает EAPOL frame из пакета

        Returns:
            Dict with bssid, essid, eapol_data, message_type
        """
        try:
            # Пропускаем Radiotap header (если есть)
            offset = 0

            # Проверяем Radiotap
            if len(packet_data) > 3:
                it_version, it_pad, it_len = struct.unpack('BBH', packet_data[0:4])
                if it_version == 0:  # Radiotap version 0
                    offset = it_len

            # 802.11 header
            if offset + 24 > len(packet_data):
                return None

            # Frame Control
            frame_control = struct.unpack('H', packet_data[offset:offset+2])[0]
            frame_type = (frame_control >> 2) & 0x3
            frame_subtype = (frame_control >> 4) & 0xF

            # Data frame (type 2)
            if frame_type != 2:
                return None

            # Extract addresses
            addr1 = packet_data[offset+4:offset+10]   # Destination
            addr2 = packet_data[offset+10:offset+16]  # Source
            addr3 = packet_data[offset+16:offset+22]  # BSSID

            bssid = ':'.join(f'{b:02x}' for b in addr3)

            # Skip to LLC/SNAP header (after 802.11 header + QoS if present)
            offset += 24
            if frame_subtype == 8:  # QoS Data
                offset += 2

            # LLC/SNAP header (8 bytes)
            if offset + 8 > len(packet_data):
                return None

            # Check for EAPOL (EtherType 0x888e)
            llc_snap = packet_data[offset:offset+8]
            if llc_snap[6:8] != b'\x88\x8e':
                return None

            offset += 8

            # EAPOL frame
            if offset + 4 > len(packet_data):
                return None

            eapol_version = packet_data[offset]
            eapol_type = packet_data[offset+1]
            eapol_length = struct.unpack('!H', packet_data[offset+2:offset+4])[0]

            # EAPOL-Key (type 3)
            if eapol_type != 3:
                return None

            eapol_data = packet_data[offset:offset+4+eapol_length]

            # Определяем message type (M1, M2, M3, M4)
            if offset + 4 + 1 > len(packet_data):
                return None

            key_info = struct.unpack('!H', packet_data[offset+5:offset+7])[0]

            # Key ACK and Key MIC flags
            key_ack = (key_info >> 7) & 1
            key_mic = (key_info >> 8) & 1

            if key_ack and not key_mic:
                message_type = 1  # M1
            elif not key_ack and key_mic:
                message_type = 2  # M2
            elif key_ack and key_mic:
                message_type = 3  # M3
            else:
                message_type = 4  # M4

            return {
                'bssid': bssid,
                'essid': '',  # ESSID извлекается из Beacon/ProbeResponse отдельно
                'eapol_data': eapol_data,
                'message_type': message_type
            }

        except Exception as e:
            logger.debug(f"Failed to extract EAPOL: {e}")
            return None

    def _is_complete_handshake(self, eapol_frames: List[Dict]) -> bool:
        """
        Проверяет, является ли handshake полным (4-way handshake)

        Минимум необходимо: M1 + M2 или M2 + M3
        """
        message_types = {frame['message_type'] for frame in eapol_frames}

        # M1 + M2 достаточно для crack'а
        if 1 in message_types and 2 in message_types:
            return True

        # M2 + M3 тоже работает
        if 2 in message_types and 3 in message_types:
            return True

        return False

    def extract_pmkid(self, packet_data: bytes) -> Optional[Dict]:
        """
        Извлекает PMKID из EAPOL frame (PMKID attack)

        PMKID = HMAC-SHA1-128(PMK, "PMK Name" | MAC_AP | MAC_STA)
        """
        try:
            eapol_info = self._extract_eapol(packet_data)
            if not eapol_info:
                return None

            eapol_data = eapol_info['eapol_data']

            # PMKID находится в Key Data (RSN IE)
            # Упрощенная проверка - ищем тег 0xdd (Vendor Specific)
            offset = 4  # Skip EAPOL header

            # TODO: Полная реализация PMKID extraction
            # Требуется парсинг Key Data и поиск PMKID IE

            return None

        except Exception as e:
            logger.debug(f"Failed to extract PMKID: {e}")
            return None


def parse_hccapx(hccapx_file: str) -> List[Dict]:
    """
    Парсит hccapx формат (Hashcat)

    Формат: https://hashcat.net/wiki/doku.php?id=hccapx
    """
    handshakes = []

    try:
        with open(hccapx_file, 'rb') as f:
            data = f.read()

        # hccapx signature: "HCPX"
        if data[0:4] != b'HCPX':
            raise ValueError("Invalid hccapx format")

        offset = 4
        record_size = 393  # Fixed size

        while offset + record_size <= len(data):
            record = data[offset:offset+record_size]

            # Parse record
            # See hccapx format specification
            essid_len = record[0]
            essid = record[1:1+essid_len].decode('utf-8', errors='ignore')

            # Extract other fields...
            # (полная реализация требует парсинга всей структуры)

            handshakes.append({
                'essid': essid,
                'format': 'hccapx'
            })

            offset += record_size

    except Exception as e:
        logger.error(f"Failed to parse hccapx: {e}")

    return handshakes


def parse_22000(hash_22000: str) -> Dict:
    """
    Парсит формат 22000 (hashcat WPA*01/02)

    Format: WPA*01*PMKID*MAC_AP*MAC_STA*ESSID
            WPA*02*M1*M2*MAC_AP*MAC_STA*ESSID*NONCE_AP*EAPOL
    """
    parts = hash_22000.split('*')

    if len(parts) < 4:
        raise ValueError("Invalid 22000 format")

    attack_type = parts[1]

    if attack_type == '01':
        # PMKID attack
        return {
            'type': 'pmkid',
            'pmkid': parts[2],
            'mac_ap': parts[3],
            'mac_sta': parts[4],
            'essid': parts[5] if len(parts) > 5 else ''
        }
    elif attack_type == '02':
        # EAPOL handshake
        return {
            'type': 'eapol',
            'm1': parts[2],
            'm2': parts[3],
            'mac_ap': parts[4],
            'mac_sta': parts[5],
            'essid': parts[6] if len(parts) > 6 else '',
            'nonce_ap': parts[7] if len(parts) > 7 else '',
            'eapol': parts[8] if len(parts) > 8 else ''
        }
    else:
        raise ValueError(f"Unknown attack type: {attack_type}")
