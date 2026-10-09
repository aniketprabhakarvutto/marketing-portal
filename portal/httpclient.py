"""Tiny HTTP helper on urllib, so the portal needs no third-party packages.

Every platform API this portal talks to is plain JSON over HTTPS, so a thin
wrapper is all that is missing from the standard library. HTTP errors retain
the response body for provider-specific handling, but generic error messages
exclude response text and URL credentials so logs do not expose secrets.
"""
from __future__ import annotations

import gzip
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 180
_ctx = ssl.create_default_context()


def validate_public_https_url(url: str, allowed_hosts: tuple[str, ...] | list[str] | None = None) -> None:
    """Reject connector URLs that could reach services on this machine or LAN."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Connector URLs must use HTTPS and cannot contain embedded credentials.")
    hostname = parts.hostname.lower().rstrip(".")
    if allowed_hosts is not None:
        allowed = tuple(host.lower().rstrip(".") for host in allowed_hosts if host.strip())
        if not allowed:
            raise ValueError("Generic URL connectors are disabled until PORTAL_CONNECTOR_HOSTS is configured.")
        if not any(hostname == host or hostname.endswith(f".{host}") for host in allowed):
            raise ValueError(f"Connector host '{hostname}' is not in PORTAL_CONNECTOR_HOSTS.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parts.hostname, parts.port or 443)}
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve connector host '{parts.hostname}'.") from exc
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise ValueError("Connector URLs cannot point to local, private, link-local, or reserved addresses.")


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts=None):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_https_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(req, timeout: int, public_only: bool,
          allowed_hosts: tuple[str, ...] | list[str] | None = None):
    if public_only:
        validate_public_https_url(req.full_url, allowed_hosts)
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ctx), _PublicRedirectHandler(allowed_hosts))
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx)


def _error_host(url: str) -> str:
    """Return only the host for diagnostics, never userinfo, path or query."""
    try:
        return urllib.parse.urlsplit(url).hostname or "remote server"
    except ValueError:
        return "remote server"


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"HTTP {status} from {_error_host(url)}")


def _read(resp) -> str:
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    public_only: bool = False,
    allowed_hosts: tuple[str, ...] | list[str] | None = None,
):
    """Perform a request and return the decoded JSON body."""
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"

    body: bytes | None = None
    hdrs = {"Accept": "application/json", "Accept-Encoding": "gzip", **(headers or {})}
    if json_body is not None:
        body = json.dumps(json_body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = urllib.request.Request(url, data=body, headers=hdrs, method=method.upper())
    try:
        with _open(req, timeout, public_only, allowed_hosts) as resp:
            text = _read(resp)
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, _read(exc), url) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {_error_host(url)}") from None

    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"Non-JSON response from {_error_host(url)}") from None


def get(url: str, **kw):
    return request("GET", url, **kw)


def get_text(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: int = DEFAULT_TIMEOUT, public_only: bool = False,
             allowed_hosts: tuple[str, ...] | list[str] | None = None) -> str:
    """Fetch a URL as plain text — for CSV endpoints and published sheets."""
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"Accept": "text/csv, text/plain, */*",
                      "Accept-Encoding": "gzip", **(headers or {})},
        method="GET",
    )
    try:
        with _open(req, timeout, public_only, allowed_hosts) as resp:
            return _read(resp)
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, _read(exc), url) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {_error_host(url)}") from None


def post(url: str, **kw):
    return request("POST", url, **kw)
