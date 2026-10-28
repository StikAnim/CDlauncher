"""Авторизация: Ely.by (Yggdrasil и OAuth2), Microsoft (device code), Offline."""
import base64
import hashlib
import http.server
import os
import secrets
import threading
import time
import urllib.parse
import uuid
import webbrowser

from .net import Reporter, get_json, post_json

ELY_AUTH = "https://authserver.ely.by"
ELY_OAUTH_AUTHORIZE = "https://account.ely.by/oauth2/v1"
ELY_OAUTH_TOKEN = "https://account.ely.by/api/oauth2/v1/token"
ELY_ACCOUNT_INFO = "https://account.ely.by/api/account/v1/info"
MS_DEVICE = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
MS_TOKEN = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MS_SCOPE = "XboxLive.signin offline_access"


class AuthError(Exception):
    pass


# --------------------------------------------------------------- offline ---
def offline_uuid(name: str) -> str:
    """UUID v3 как у ванильного Minecraft для офлайн-игроков."""
    raw = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    raw[6] = (raw[6] & 0x0F) | 0x30
    raw[8] = (raw[8] & 0x3F) | 0x80
    return raw.hex()


def offline_account(name: str) -> dict:
    name = name.strip()
    if not (3 <= len(name) <= 16) or not all(c.isalnum() or c == "_" for c in name):
        raise AuthError("Ник: 3-16 символов, латиница, цифры и _")
    return {"type": "offline", "name": name, "uuid": offline_uuid(name), "access_token": "0"}


# ----------------------------------------------------------------- Ely.by ---
def ely_login(username: str, password: str, totp: str = "") -> dict:
    """Вход по логину/паролю Ely.by. При включённой 2FA укажите код - он добавится через ':'."""
    pwd = f"{password}:{totp}" if totp else password
    client_token = uuid.uuid4().hex
    r = post_json(f"{ELY_AUTH}/auth/authenticate",
                  {"username": username, "password": pwd, "clientToken": client_token,
                   "requestUser": True}, check=False)
    data = r.json() if r.content else {}
    if r.status_code != 200:
        msg = data.get("errorMessage") or data.get("error") or f"HTTP {r.status_code}"
        if "two factor" in msg.lower() or "двухфактор" in msg.lower():
            msg += " (введите код 2FA)"
        raise AuthError(f"Ely.by: {msg}")
    prof = data["selectedProfile"]
    return {"type": "ely", "auth": "yggdrasil", "name": prof["name"], "uuid": prof["id"],
            "access_token": data["accessToken"], "client_token": data["clientToken"]}


class LoopbackServer:
    """Временный локальный сервер http://localhost:<случайный порт>/callback для OAuth-перенаправления."""

    OK_HTML = ("<html><head><meta charset='utf-8'><title>CD Launcher</title></head><body style='font-family:Segoe UI,Arial;"
               "background:#0f172a;color:#e6f1ff;text-align:center;padding-top:15vh'><h1>CD Launcher</h1>"
               "<p>Вход выполнен. Эту вкладку можно закрыть и вернуться в лаунчер.</p></body></html>")

    def __init__(self, port: int = 0, path: str = "/callback"):
        owner = self
        self.result = None
        self.path = path

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != owner.path:          # favicon и прочее игнорируем
                    self.send_response(404)
                    self.end_headers()
                    return
                owner.result = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(LoopbackServer.OK_HTML.encode("utf-8"))

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        self.server.timeout = 0.5
        self.port = self.server.server_address[1]

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.port}{self.path}"

    def wait(self, timeout: float = 180) -> dict:
        deadline = time.time() + timeout
        try:
            while self.result is None and time.time() < deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()           # соединение закрываем сразу после получения кода
        if self.result is None:
            raise AuthError("Время ожидания входа через браузер истекло")
        return self.result


def ely_oauth_login(client_id: str, client_secret: str, rep: Reporter = None, port: int = 0) -> dict:
    """Ely.by OAuth2 через системный браузер (приложение регистрируется на ely.by/dev/apps)."""
    if not client_id or not client_secret:
        raise AuthError("Укажите Ely.by Client ID и Secret в настройках")
    rep = rep or Reporter()
    srv = LoopbackServer(port)
    state = secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": srv.redirect_uri, "response_type": "code", "state": state,
        "scope": "account_info minecraft_server_session offline_access"})
    rep.log("Открываю браузер для входа через Ely.by...")
    webbrowser.open(f"{ELY_OAUTH_AUTHORIZE}?{params}")
    got = srv.wait()
    if got.get("error"):
        raise AuthError(f"Ely.by: {got.get('error_description') or got['error']}")
    if got.get("state") != state:
        raise AuthError("Ely.by OAuth: неверный state (возможна подмена запроса)")
    if not got.get("code"):
        raise AuthError("Ely.by OAuth: код не получен")
    return _ely_oauth_token({"grant_type": "authorization_code", "code": got["code"],
                             "redirect_uri": srv.redirect_uri}, client_id, client_secret)


MS_AUTHORIZE = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def microsoft_browser_login(client_id: str, rep: Reporter = None) -> dict:
    """Microsoft через браузер: Authorization Code + PKCE, redirect на http://localhost:<порт>/callback.
    В Azure у приложения должен быть redirect URI http://localhost (тип 'Mobile and desktop')."""
    if not client_id:
        raise AuthError("Укажите Microsoft (Azure) Client ID в настройках - см. README")
    rep = rep or Reporter()
    verifier = _b64url(os.urandom(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(16)
    srv = LoopbackServer()
    params = urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": srv.redirect_uri,
        "response_mode": "query", "scope": MS_SCOPE, "state": state, "prompt": "select_account",
        "code_challenge": challenge, "code_challenge_method": "S256"})
    rep.log("Открываю браузер для входа через Microsoft...")
    webbrowser.open(f"{MS_AUTHORIZE}?{params}")
    got = srv.wait()
    if got.get("error"):
        raise AuthError(f"Microsoft: {got.get('error_description') or got['error']}")
    if got.get("state") != state:
        raise AuthError("Microsoft: неверный state (возможна подмена запроса)")
    t = post_json(MS_TOKEN, data={"grant_type": "authorization_code", "client_id": client_id, "code": got.get("code", ""),
                                  "redirect_uri": srv.redirect_uri, "code_verifier": verifier, "scope": MS_SCOPE},
                  check=False)
    j = t.json()
    if t.status_code != 200:
        raise AuthError(f"Microsoft: {j.get('error_description', j.get('error'))}")
    return _ms_chain(j["access_token"], j.get("refresh_token", ""))


def _ely_oauth_token(extra: dict, client_id: str, client_secret: str) -> dict:
    payload = {"client_id": client_id, "client_secret": client_secret}
    payload.update(extra)
    r = post_json(ELY_OAUTH_TOKEN, data=payload, check=False)
    if r.status_code != 200:
        raise AuthError(f"Ely.by OAuth: {r.text[:200]}")
    tok = r.json()
    info = get_json(ELY_ACCOUNT_INFO, headers={"Authorization": f"Bearer {tok['access_token']}"})
    return {"type": "ely", "auth": "oauth", "name": info["username"], "uuid": info["uuid"].replace("-", ""),
            "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
            "expires": time.time() + int(tok.get("expires_in", 3600))}


def ely_refresh(acc: dict, settings) -> dict:
    if acc.get("auth") == "oauth":
        if acc.get("expires", 0) > time.time() + 60:
            return acc
        if not acc.get("refresh_token"):
            raise AuthError("Ely.by: нужен повторный вход")
        new = _ely_oauth_token({"grant_type": "refresh_token", "refresh_token": acc["refresh_token"]},
                               settings.get("ely_client_id"), settings.get("ely_client_secret"))
        acc.update(new)
        return acc
    r = post_json(f"{ELY_AUTH}/auth/validate", {"accessToken": acc["access_token"]}, check=False)
    if r.status_code in (200, 204):
        return acc
    r = post_json(f"{ELY_AUTH}/auth/refresh",
                  {"accessToken": acc["access_token"], "clientToken": acc.get("client_token", "")}, check=False)
    if r.status_code != 200:
        raise AuthError("Ely.by: сессия устарела, войдите заново")
    acc["access_token"] = r.json()["accessToken"]
    return acc


# -------------------------------------------------------------- Microsoft ---
def microsoft_login(client_id: str, rep: Reporter = None) -> dict:
    """Device Code Flow: пользователь вводит код на microsoft.com/link."""
    if not client_id:
        raise AuthError("Укажите Microsoft (Azure) Client ID в настройках - см. README")
    rep = rep or Reporter()
    r = post_json(MS_DEVICE, data={"client_id": client_id, "scope": MS_SCOPE}, check=False)
    if r.status_code != 200:
        raise AuthError(f"Microsoft: {r.text[:200]}")
    d = r.json()
    rep.log(f"Откройте {d['verification_uri']} и введите код: {d['user_code']}")
    try:
        webbrowser.open(d["verification_uri"])
    except Exception:
        pass
    deadline = time.time() + int(d.get("expires_in", 900))
    interval = int(d.get("interval", 5))
    while time.time() < deadline:
        time.sleep(interval)
        t = post_json(MS_TOKEN, data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                      "client_id": client_id, "device_code": d["device_code"]}, check=False)
        j = t.json()
        if t.status_code == 200:
            return _ms_chain(j["access_token"], j.get("refresh_token", ""))
        if j.get("error") not in ("authorization_pending", "slow_down"):
            raise AuthError(f"Microsoft: {j.get('error_description', j.get('error'))}")
    raise AuthError("Microsoft: время ожидания входа истекло")


def microsoft_refresh(acc: dict, client_id: str) -> dict:
    if acc.get("expires", 0) > time.time() + 60:
        return acc
    t = post_json(MS_TOKEN, data={"grant_type": "refresh_token", "client_id": client_id,
                                  "refresh_token": acc.get("refresh_token", ""), "scope": MS_SCOPE}, check=False)
    if t.status_code != 200:
        raise AuthError("Microsoft: сессия устарела, войдите заново")
    j = t.json()
    new = _ms_chain(j["access_token"], j.get("refresh_token", acc.get("refresh_token", "")))
    acc.update(new)
    return acc


def _ms_chain(ms_access: str, refresh: str) -> dict:
    xbl = post_json("https://user.auth.xboxlive.com/user/authenticate", {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": "d=" + ms_access},
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"}).json()
    xsts = post_json("https://xsts.auth.xboxlive.com/xsts/authorize", {
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl["Token"]]},
        "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}, check=False)
    if xsts.status_code != 200:
        raise AuthError("Xbox: аккаунт не может войти (нет Xbox-профиля или ограничения по возрасту/региону)")
    xj = xsts.json()
    uhs = xj["DisplayClaims"]["xui"][0]["uhs"]
    mc = post_json("https://api.minecraftservices.com/authentication/login_with_xbox",
                   {"identityToken": f"XBL3.0 x={uhs};{xj['Token']}"}).json()
    prof = post_json("https://api.minecraftservices.com/minecraft/profile", None,
                     headers={"Authorization": f"Bearer {mc['access_token']}"}, check=False)
    if prof.status_code != 200:
        raise AuthError("На этом Microsoft-аккаунте нет лицензии Minecraft Java Edition")
    pj = prof.json()
    return {"type": "microsoft", "name": pj["name"], "uuid": pj["id"], "access_token": mc["access_token"],
            "refresh_token": refresh, "expires": time.time() + int(mc.get("expires_in", 86400))}
