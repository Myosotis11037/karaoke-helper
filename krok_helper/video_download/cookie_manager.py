from __future__ import annotations

import http.cookiejar
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from krok_helper.config import APP_NAME


@dataclass(slots=True)
class BilibiliAccountProfile:
    nickname: str
    avatar_url: str = ""
    avatar_bytes: bytes = b""


class CookieManager:
    def __init__(self, cookie_path: str = "") -> None:
        self._configured_path = cookie_path.strip()

    def set_cookie_path(self, cookie_path: str) -> None:
        self._configured_path = cookie_path.strip()

    def default_cookie_path(self) -> Path:
        appdata = os.getenv("APPDATA")
        if os.name == "nt" and appdata:
            return Path(appdata) / APP_NAME / "video_download" / "bilibili_cookies.txt"
        return Path.home() / ".config" / APP_NAME.lower().replace(" ", "-") / "bilibili_cookies.txt"

    def resolved_cookie_path(self) -> Path:
        if self._configured_path:
            return Path(self._configured_path).expanduser()
        return self.default_cookie_path()

    def has_cookie(self) -> bool:
        path = self.resolved_cookie_path()
        return path.is_file() and path.stat().st_size > 0

    def get_cookie_path(self) -> str | None:
        path = self.resolved_cookie_path()
        return str(path) if path.exists() else None

    def clear_cookie(self) -> None:
        path = self.resolved_cookie_path()
        if path.exists():
            path.unlink()

    def load_cookie_jar(self) -> http.cookiejar.MozillaCookieJar:
        path = self.resolved_cookie_path()
        jar = http.cookiejar.MozillaCookieJar(str(path))
        if path.is_file():
            jar.load(ignore_discard=True, ignore_expires=True)
        return jar

    def save_cookie_jar(self, jar: http.cookiejar.MozillaCookieJar) -> Path:
        path = self.resolved_cookie_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        for cookie in jar:
            cookie.discard = False
        jar.filename = str(path)
        jar.save(ignore_discard=True, ignore_expires=True)
        return path

    def set_cookie(
        self,
        jar: http.cookiejar.MozillaCookieJar,
        *,
        name: str,
        value: str,
        domain: str = ".bilibili.com",
        path: str = "/",
        expires: int | None = None,
        secure: bool = True,
        http_only: bool = False,
    ) -> None:
        cookie = http.cookiejar.Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path=path,
            path_specified=True,
            secure=secure,
            expires=expires,
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None} if http_only else {},
            rfc2109=False,
        )
        jar.set_cookie(cookie)

    def check_login_status(self) -> bool:
        if not self.has_cookie():
            return False

        try:
            payload = self._fetch_nav_payload()
            return bool(payload.get("data", {}).get("isLogin"))
        except Exception:
            return self._has_valid_sessdata_locally()

    def get_account_profile(self) -> BilibiliAccountProfile | None:
        try:
            payload = self._fetch_nav_payload()
        except Exception:
            return None

        data = payload.get("data") or {}
        if not data.get("isLogin"):
            return None

        avatar_url = str(data.get("face") or "")
        return BilibiliAccountProfile(
            nickname=str(data.get("uname") or "Bilibili 用户"),
            avatar_url=avatar_url,
            avatar_bytes=self._fetch_bytes(avatar_url),
        )

    def _has_valid_sessdata_locally(self) -> bool:
        path = self.resolved_cookie_path()
        if not path.is_file():
            return False

        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _flag, cookie_path, _secure, expires, name, value = parts[:7]
                domain = domain.lower()
                name = name.strip()
                value = value.strip()
                if "bilibili" not in domain:
                    continue
                if name != "SESSDATA" or not value:
                    continue
                if cookie_path not in ("/", ""):
                    continue
                if expires.isdigit() and int(expires) not in (0, 2147483647) and int(expires) < int(time.time()):
                    return False
                return True
        except Exception:
            return False
        return False

    def _fetch_nav_payload(self) -> dict:
        jar = self.load_cookie_jar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        request = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.bilibili.com/",
            },
        )
        with opener.open(request, timeout=15) as response:
            return json.load(response)

    def _fetch_bytes(self, url: str) -> bytes:
        if not url:
            return b""
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.bilibili.com/",
                },
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read()
        except Exception:
            return b""
