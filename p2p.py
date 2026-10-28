"""P2P-клиент: друзья, статусы и туннели к открытым LAN-мирам через сервер-координатор.

Хост: когда в логе игры появляется порт «Открыть для сети», лаунчер сообщает серверу статус
«мир открыт». Гость нажимает «P2P-подключение»: на его ПК поднимается 127.0.0.1:<порт>,
а каждое соединение Minecraft пересылается к хосту (никаких Radmin/Hamachi/Ngrok).
"""
import json
import re
import secrets
import socket
import threading
import time

LAN_PORT_RE = re.compile(r"(?:Started serving on|Local game hosted on port)\s+(\d{2,5})")


def parse_lan_port(line: str):
    m = LAN_PORT_RE.search(line)
    return int(m.group(1)) if m else None


def parse_address(addr: str):
    addr = (addr or "").strip()
    if not addr:
        return None
    host, _, port = addr.rpartition(":")
    if not host:
        return addr, 25585
    return host, int(port)


def new_identity():
    return "CD-" + secrets.token_hex(4).upper(), secrets.token_hex(16)


def _pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def bridge(a: socket.socket, b: socket.socket):
    t = threading.Thread(target=_pipe, args=(a, b), daemon=True)
    t.start()
    _pipe(b, a)
    t.join(timeout=2)
    a.close()
    b.close()


def _read_line(sock: socket.socket, limit=4096) -> bytes:
    """Читает строку побайтно, чтобы не «съесть» данные туннеля, идущие следом."""
    buf = b""
    while not buf.endswith(b"\n") and len(buf) < limit:
        ch = sock.recv(1)
        if not ch:
            break
        buf += ch
    return buf


class P2PClient:
    def __init__(self, server: str, cd_id: str, secret: str, name: str, on_event):
        self.addr = parse_address(server)
        self.cd_id, self.secret, self.name = cd_id, secret, name
        self.on_event = on_event
        self.sock = None
        self.running = False
        self.world_port = 0
        self._presence = {"status": "online", "profile": "", "port": 0}
        self._lock = threading.Lock()
        self._listeners = {}

    # ------------------------------------------------------------ соединение
    def start(self):
        if not self.addr:
            return
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False
        for l in list(self._listeners.values()):
            try:
                l.close()
            except OSError:
                pass
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    def _send(self, obj) -> bool:
        with self._lock:
            try:
                self.sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                return True
            except (OSError, AttributeError):
                return False

    def _loop(self):
        while self.running:
            try:
                self.sock = socket.create_connection(self.addr, timeout=10)
                self.sock.settimeout(None)
                self._send({"op": "hello", "id": self.cd_id, "secret": self.secret, "name": self.name})
                self.on_event({"op": "status", "connected": True})
                self._send({"op": "presence", **self._presence})
                buf = b""
                while self.running:
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.strip():
                            self._handle(json.loads(line.decode("utf-8")))
            except (OSError, ValueError):
                pass
            self.on_event({"op": "status", "connected": False})
            for _ in range(50):
                if not self.running:
                    return
                time.sleep(0.1)

    def _handle(self, msg: dict):
        if msg.get("op") == "incoming":
            threading.Thread(target=self._host_tunnel, args=(msg["tid"],), daemon=True).start()
        else:
            self.on_event(msg)

    # ------------------------------------------------------------- действия
    def add_friend(self, query: str):
        self._send({"op": "add_friend", "query": query})

    def remove_friend(self, fid: str):
        self._send({"op": "remove_friend", "id": fid})

    def set_presence(self, status: str, profile: str = "", port: int = 0):
        self._presence = {"status": status, "profile": profile, "port": port}
        self.world_port = port if status == "open_world" else 0
        self._send({"op": "presence", **self._presence})

    # ---------------------------------------------------------- туннель: хост
    def _host_tunnel(self, tid: str):
        if not self.world_port:
            return
        try:
            srv = socket.create_connection(self.addr, timeout=10)
            srv.settimeout(None)
            srv.sendall((json.dumps({"op": "tunnel_host", "id": self.cd_id, "secret": self.secret,
                                     "tid": tid}) + "\n").encode("utf-8"))
            game = socket.create_connection(("127.0.0.1", self.world_port), timeout=10)
            game.settimeout(None)
            bridge(srv, game)
        except OSError:
            pass

    # ---------------------------------------------------------- туннель: гость
    def open_local_listener(self, friend_id: str, port: int = 0) -> int:
        """Поднимает 127.0.0.1:<port> и пробрасывает подключения к миру друга. Возвращает порт."""
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind(("127.0.0.1", port))
        ls.listen(8)
        real = ls.getsockname()[1]
        old = self._listeners.pop(friend_id, None)
        if old:
            old.close()
        self._listeners[friend_id] = ls

        def accept_loop():
            while True:
                try:
                    client, _ = ls.accept()
                except OSError:
                    return
                threading.Thread(target=self._join_tunnel, args=(client, friend_id), daemon=True).start()

        threading.Thread(target=accept_loop, daemon=True).start()
        return real

    def _join_tunnel(self, client: socket.socket, friend_id: str):
        try:
            srv = socket.create_connection(self.addr, timeout=10)
            srv.settimeout(20)
            srv.sendall((json.dumps({"op": "tunnel_join", "id": self.cd_id, "secret": self.secret,
                                     "friend": friend_id}) + "\n").encode("utf-8"))
            reply = json.loads(_read_line(srv).decode("utf-8") or "{}")
            if not reply.get("ok"):
                client.close()
                srv.close()
                self.on_event({"op": "error", "code": reply.get("error", "join_failed")})
                return
            srv.settimeout(None)
            bridge(client, srv)
        except (OSError, ValueError):
            client.close()
