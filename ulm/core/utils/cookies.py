"""Netscape (cookies.txt) loader, stdlib http.cookiejar."""
from __future__ import annotations

from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Dict, List


def load_netscape(path: Path) -> List[Dict[str, Any]]:
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    out: List[Dict[str, Any]] = []
    for cookie in jar:
        out.append(
            {
                "name": cookie.name,
                "value": cookie.value or "",
                "domain": cookie.domain,
                "path": cookie.path,
                "expires": cookie.expires,
            }
        )
    return out


def save_netscape(path: Path, cookies: List[Dict[str, Any]]) -> None:
    jar = MozillaCookieJar(str(path))
    for cookie in cookies:
        jar.set_cookie(_mk_cookie(cookie))
    jar.save(ignore_discard=True, ignore_expires=True)


def _mk_cookie(cookie: Dict[str, Any]):
    import time
    from http.cookiejar import Cookie

    return Cookie(
        version=0,
        name=cookie["name"],
        value=cookie.get("value") or "",
        port=None, port_specified=False,
        domain=cookie.get("domain") or "", domain_specified=bool(cookie.get("domain")), domain_initial_dot=str(cookie.get("domain", "")).startswith("."),
        path=cookie.get("path") or "/",
        path_specified=True,
        secure=False,
        expires=cookie.get("expires") or int(time.time()) + 60 * 60 * 24 * 30,
        discard=False,
        comment=None, comment_url=None, rest={}, rfc2109=False,
    )
