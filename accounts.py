"""Хранилище аккаунтов: accounts.json зашифрован (Fernet, ключ привязан к машине и пользователю)."""
import base64
import json
import os
import uuid

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from . import auth
from .config import ACCOUNTS_FILE, ROOT


def _key() -> bytes:
    ROOT.mkdir(parents=True, exist_ok=True)
    salt_file = ROOT / ".salt"
    if not salt_file.exists():
        salt_file.write_bytes(os.urandom(16))
    salt = salt_file.read_bytes()
    seed = f"{uuid.getnode()}|{os.environ.get('USERNAME') or os.environ.get('USER', '')}|CDLauncher"
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
    return base64.urlsafe_b64encode(kdf.derive(seed.encode("utf-8")))


class AccountStore:
    def __init__(self):
        self.accounts = []
        self.active = ""
        self.load()

    @staticmethod
    def account_id(acc: dict) -> str:
        return f"{acc['type']}:{acc['uuid']}"

    def load(self):
        try:
            wrapper = json.loads(ACCOUNTS_FILE.read_text("utf-8"))
            raw = Fernet(_key()).decrypt(wrapper["data"].encode("ascii"))
            data = json.loads(raw.decode("utf-8"))
            self.accounts = data.get("accounts", [])
            self.active = data.get("active", "")
        except (FileNotFoundError, InvalidToken, KeyError, ValueError):
            self.accounts, self.active = [], ""

    def save(self):
        payload = json.dumps({"accounts": self.accounts, "active": self.active}).encode("utf-8")
        token = Fernet(_key()).encrypt(payload).decode("ascii")
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNTS_FILE.write_text(json.dumps({"v": 1, "data": token}), "utf-8")

    def add(self, acc: dict, make_active=True):
        aid = self.account_id(acc)
        self.accounts = [a for a in self.accounts if self.account_id(a) != aid]
        self.accounts.append(acc)
        if make_active:
            self.active = aid
        self.save()

    def remove(self, aid: str):
        self.accounts = [a for a in self.accounts if self.account_id(a) != aid]
        if self.active == aid:
            self.active = self.account_id(self.accounts[0]) if self.accounts else ""
        self.save()

    def set_active(self, aid: str):
        if any(self.account_id(a) == aid for a in self.accounts):
            self.active = aid
            self.save()

    def current(self):
        for a in self.accounts:
            if self.account_id(a) == self.active:
                return a
        return self.accounts[0] if self.accounts else None

    def ensure_fresh(self, acc: dict, settings) -> dict:
        """Обновляет токен при необходимости и сохраняет."""
        if acc["type"] == "ely":
            auth.ely_refresh(acc, settings)
        elif acc["type"] == "microsoft":
            auth.microsoft_refresh(acc, settings.get("ms_client_id"))
        self.save()
        return acc
