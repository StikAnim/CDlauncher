"""Сетевые сведения: внешний адрес через публичный STUN (Google) и локальные адреса."""
import os
import socket
import struct

STUN_SERVERS = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478)]
MAGIC = 0x2112A442


def stun_public_address(servers=None, timeout=2.5):
    """(ip, порт) как нас видит интернет, либо None. RFC 5389 Binding Request, только стандартная библиотека."""
    for host, port in (servers or STUN_SERVERS):
        tid = os.urandom(12)
        req = struct.pack("!HHI", 0x0001, 0, MAGIC) + tid
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(req, (host, port))
            data, _ = s.recvfrom(2048)
        except OSError:
            continue
        finally:
            s.close()
        if len(data) < 20 or data[8:20] != tid:
            continue
        pos = 20
        while pos + 4 <= len(data):
            atype, alen = struct.unpack("!HH", data[pos:pos + 4])
            val = data[pos + 4:pos + 4 + alen]
            if atype == 0x0020 and alen >= 8 and val[1] == 1:          # XOR-MAPPED-ADDRESS (IPv4)
                p = struct.unpack("!H", val[2:4])[0] ^ (MAGIC >> 16)
                ip = struct.unpack("!I", val[4:8])[0] ^ MAGIC
                return socket.inet_ntoa(struct.pack("!I", ip)), p
            if atype == 0x0001 and alen >= 8 and val[1] == 1:          # MAPPED-ADDRESS
                return socket.inet_ntoa(val[4:8]), struct.unpack("!H", val[2:4])[0]
            pos += 4 + alen + (-alen % 4)
    return None


def local_ips() -> list:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)
