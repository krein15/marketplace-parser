"""The proxy the browser goes through, written the way people usually write one.

``user:pass@10.0.0.1:3128``, ``http://10.0.0.1:3128``, ``socks5://host:1080`` — all of them mean the same
thing to the browser, so the text from the settings is turned into what Chromium expects. Empty text is
a direct connection.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

SCHEMES = ("http", "https", "socks5")
IP_SERVICE = "https://api.ipify.org/?format=json"  # чем проверяем, что прокси жив
PLACEHOLDER = "user:password@10.0.0.1:3128"


class ProxyError(ValueError):
    """The address is written in a way the browser cannot use; the text explains what to fix."""


def parse_proxy(address: str) -> dict[str, str] | None:
    """Proxy settings for the browser, or None for a direct connection."""
    address = address.strip()
    if not address:
        return None
    if "://" not in address:
        address = "http://" + address  # the protocol is usually left out
    parsed = urlsplit(address)
    if parsed.scheme not in SCHEMES:
        raise ProxyError(f"Прокси: протокол «{parsed.scheme}» не подходит. Нужен http, https или socks5.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProxyError("Прокси: порт должен быть числом, например 3128.") from exc
    if not parsed.hostname or not port:
        raise ProxyError(f"Прокси: укажите адрес и порт, например {PLACEHOLDER}.")
    settings = {"server": f"{parsed.scheme}://{parsed.hostname}:{port}"}
    if parsed.username:
        settings["username"] = unquote(parsed.username)
        settings["password"] = unquote(parsed.password or "")
    return settings


def describe(address: str) -> str:
    """How the proxy is shown in the report and the log — without the password."""
    try:
        settings = parse_proxy(address)
    except ProxyError:
        return address.strip()
    if settings is None:
        return ""
    user = f"{settings['username']}@" if "username" in settings else ""
    return settings["server"].replace("://", f"://{user}")
