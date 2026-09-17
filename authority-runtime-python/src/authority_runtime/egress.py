"""Validated outbound HTTP: the single egress path for carryall sinks.

Exports: EgressPolicy, LOCAL_SERVICE, NOTIFY, PUBLIC_HTTPS, Destination,
EgressDenied, EgressTransportError, EgressResponse, evaluate, request, arequest.

Flow for every call: parse URL -> reject metadata names and obfuscated IP
literals -> resolve DNS exactly once -> classify every answer -> append an
audit record -> connect to the pinned sockaddr (Host header and TLS SNI keep
the hostname) -> refuse any 3xx. There is no redirect following, no proxy
support (http.client ignores *_PROXY), and no second DNS lookup.

Invariants that hold under every policy:
  - link-local, multicast, unspecified, reserved, site-local, RFC1918/ULA,
    cloud metadata (169.254.169.254, fd00:ec2::254, metadata hostnames) and
    IPv6 forms embedding IPv4 are always denied
  - a public destination is only reachable over https
  - if the audit record cannot be written, the request is not sent
"""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import os
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union
from urllib.parse import urlsplit

from .enforce import SystemAuditEntry
from .storage import EnvelopeStore

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

METADATA_HOSTS = frozenset({
    "metadata", "metadata.google.internal", "metadata.goog",
    "instance-data", "instance-data.ec2.internal",
})
_METADATA_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254"),
})
_ZERO_NET = ipaddress.IPv4Network("0.0.0.0/8")
_NAT64_NET = ipaddress.IPv6Network("64:ff9b::/96")
_V4_COMPAT_NET = ipaddress.IPv6Network("::/96")
_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")

# Test seam; the resolver is called exactly once per request.
_getaddrinfo = socket.getaddrinfo


@dataclass(frozen=True)
class EgressPolicy:
    """Which destination classes a call site may reach. Private LAN is never allowed."""

    name: str
    allow_loopback: bool
    allow_public: bool


LOCAL_SERVICE = EgressPolicy("local_service", allow_loopback=True, allow_public=False)
NOTIFY = EgressPolicy("notify", allow_loopback=True, allow_public=True)
PUBLIC_HTTPS = EgressPolicy("public_https", allow_loopback=False, allow_public=True)


class EgressDenied(Exception):
    """Destination refused. Deliberately not an OSError so network-error handlers don't swallow it."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"egress denied ({reason}): {detail}")
        self.reason = reason
        self.detail = detail


class EgressTransportError(OSError):
    """Protocol-level failure after an allowed connection (malformed response etc.)."""


@dataclass(frozen=True)
class Destination:
    """A validated destination pinned to the answer set of its single DNS lookup."""

    scheme: str
    host: str
    port: int
    target: str
    addresses: Tuple[Tuple[int, Tuple[Any, ...]], ...]

    @property
    def ip(self) -> str:
        """The first pinned IP address as a string."""
        return str(self.addresses[0][1][0])

    @property
    def origin(self) -> str:
        """scheme://host:port with no path or query (safe to audit)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"


@dataclass(frozen=True)
class EgressResponse:
    """A fully-read HTTP response."""

    status: int
    headers: Dict[str, str]
    body: bytes

    def json(self) -> Any:
        """Decode the body as UTF-8 JSON."""
        return json.loads(self.body.decode("utf-8"))


def _parse(url: str) -> Tuple[str, str, int, str]:
    """Split a URL into (scheme, host, port, request-target), denying unsupported shapes."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise EgressDenied("malformed_url", str(e)) from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise EgressDenied("scheme", f"scheme {scheme!r} is not http or https")
    if parts.username is not None or parts.password is not None:
        raise EgressDenied("userinfo", "credentials embedded in URL")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise EgressDenied("malformed_url", "no host")
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return scheme, host, port or (443 if scheme == "https" else 80), target


def _check_host(host: str) -> Optional[IPAddress]:
    """Deny metadata names and non-canonical IP literals. Returns the IP if host is a literal."""
    if host in METADATA_HOSTS:
        raise EgressDenied("metadata_host", host)
    if "%" in host:
        raise EgressDenied("zone_id", host)
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        socket.inet_aton(host)
    except OSError:
        pass
    else:
        raise EgressDenied("obfuscated_ip", f"non-canonical IPv4 literal {host!r}")
    if not set(host) <= _HOST_CHARS:
        raise EgressDenied("hostname_charset", host)
    return None


def _classify(ip: IPAddress) -> str:
    """Return 'loopback' or 'public'; raise for every other address class."""
    if ip in _METADATA_IPS:
        raise EgressDenied("metadata_ip", str(ip))
    if isinstance(ip, ipaddress.IPv6Address) and (
        ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None
        or ip in _NAT64_NET or (ip in _V4_COMPAT_NET and int(ip) > 1)
    ):
        raise EgressDenied("embedded_ipv4", str(ip))
    if ip.is_link_local:
        raise EgressDenied("link_local", str(ip))
    if ip.is_multicast:
        raise EgressDenied("multicast", str(ip))
    if ip.is_unspecified or (isinstance(ip, ipaddress.IPv4Address) and ip in _ZERO_NET):
        raise EgressDenied("unspecified", str(ip))
    if ip.is_loopback:
        return "loopback"
    if ip.is_reserved or (isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local):
        raise EgressDenied("reserved", str(ip))
    if ip.is_global:
        return "public"
    raise EgressDenied("private", str(ip))


def _admit(ip: IPAddress, scheme: str, policy: EgressPolicy) -> None:
    """Apply the call site's policy to one classified address."""
    kind = _classify(ip)
    if kind == "loopback" and not policy.allow_loopback:
        raise EgressDenied("loopback_not_allowed", f"{ip} under policy {policy.name}")
    if kind == "public" and not policy.allow_public:
        raise EgressDenied("public_not_allowed", f"{ip} under policy {policy.name}")
    if kind == "public" and scheme != "https":
        raise EgressDenied("plaintext_public", f"{ip} requires https")


def evaluate(url: str, policy: EgressPolicy) -> Destination:
    """Validate a URL and resolve it once. Returns the pinned destination. Does not audit."""
    scheme, host, port, target = _parse(url)
    literal = _check_host(host)
    if literal is not None:
        _admit(literal, scheme, policy)
    try:
        answers = _getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        raise EgressDenied("unresolvable", f"{host}: {e}") from None
    if not answers:
        raise EgressDenied("unresolvable", f"{host}: no addresses")
    for answer in answers:
        _admit(ipaddress.ip_address(str(answer[4][0]).split("%")[0]), scheme, policy)
    pinned = tuple((int(a[0]), tuple(a[4])) for a in answers)
    return Destination(scheme, host, port, target, pinned)


def _open_socket(family: int, sockaddr: Tuple[Any, ...], timeout: float) -> socket.socket:
    """Connect to an already-resolved sockaddr. Never performs name resolution."""
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


def _connect_pinned(dest: Destination, timeout: float) -> socket.socket:
    """Try each pinned address in order (e.g. ::1 then 127.0.0.1), as urlopen would."""
    last: Optional[OSError] = None
    for family, sockaddr in dest.addresses:
        try:
            return _open_socket(family, sockaddr, timeout)
        except OSError as e:
            last = e
    assert last is not None
    raise last


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects to the pinned sockaddr; Host header stays the hostname."""

    def __init__(self, dest: Destination, timeout: float) -> None:
        super().__init__(dest.host, dest.port, timeout=timeout)
        self._dest = dest
        self._egress_timeout = timeout

    def connect(self) -> None:
        self.sock = _connect_pinned(self._dest, self._egress_timeout)


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    """TLS over the pinned socket; certificate is verified against the hostname via SNI."""

    def connect(self) -> None:
        raw = _connect_pinned(self._dest, self._egress_timeout)
        try:
            ctx = ssl.create_default_context()
            self.sock = ctx.wrap_socket(raw, server_hostname=self._dest.host)
        except BaseException:
            raw.close()
            raise


def _send(dest: Destination, method: str, body: Optional[bytes],
          headers: Mapping[str, str], timeout: float) -> EgressResponse:
    """Perform one HTTP exchange against the pinned destination."""
    cls = _PinnedHTTPSConnection if dest.scheme == "https" else _PinnedHTTPConnection
    conn = cls(dest, timeout)
    try:
        conn.request(method, dest.target, body=body, headers=dict(headers))
        raw = conn.getresponse()
        return EgressResponse(raw.status, {k.lower(): v for k, v in raw.getheaders()}, raw.read())
    except http.client.HTTPException as e:
        raise EgressTransportError(f"{type(e).__name__}: {e}") from e
    finally:
        conn.close()


def _default_store() -> EnvelopeStore:
    """Open the audit store at $CARRYALL_DB (default ~/.carryall/authority.db)."""
    path = Path(os.path.expanduser(os.environ.get("CARRYALL_DB", "~/.carryall/authority.db")))
    path.parent.mkdir(parents=True, exist_ok=True)
    return EnvelopeStore(str(path))


def _safe_origin(url: str) -> str:
    """Best-effort scheme://host:port for audit records; never includes userinfo, path or query."""
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.hostname or ''}:{parts.port or ''}"[:200]
    except ValueError:
        return "<unparseable>"


def _audit(store: EnvelopeStore, purpose: str, policy: EgressPolicy, origin: str,
           denied: Optional[EgressDenied], dest: Optional[Destination] = None) -> None:
    """Append one hash-chained record. Raises EgressDenied if the write fails."""
    metadata: Dict[str, Any] = {"policy": policy.name, "purpose": purpose}
    if dest is not None:
        metadata["pinned_ips"] = [str(sa[0]) for _, sa in dest.addresses]
    if denied is not None:
        metadata["reason"] = denied.reason
    entry = SystemAuditEntry(
        action=f"egress:{purpose}", subsystem="egress",
        result="deny" if denied else "allow",
        error=denied.detail[:500] if denied else None,
        resource=origin, metadata=metadata,
    )
    try:
        store.save_audit_entry(entry)
    except Exception as e:
        raise EgressDenied("audit_unavailable", f"{type(e).__name__}: {e}") from e


def request(url: str, *, policy: EgressPolicy, purpose: str, method: str = "GET",
            body: Optional[bytes] = None, headers: Optional[Mapping[str, str]] = None,
            timeout: float = 30.0, store: Optional[EnvelopeStore] = None) -> EgressResponse:
    """Validate, audit, and perform one HTTP request. Raises EgressDenied on any refusal."""
    try:
        audit_store = store if store is not None else _default_store()
    except Exception as e:
        raise EgressDenied("audit_unavailable", f"{type(e).__name__}: {e}") from e
    hdrs = dict(headers or {})
    try:
        if any(k.lower() == "host" for k in hdrs):
            raise EgressDenied("host_header", "caller may not set Host")
        dest = evaluate(url, policy)
    except EgressDenied as denied:
        _audit(audit_store, purpose, policy, _safe_origin(url), denied)
        raise
    _audit(audit_store, purpose, policy, dest.origin, None, dest)
    resp = _send(dest, method, body, hdrs, timeout)
    if 300 <= resp.status < 400:
        location = resp.headers.get("location", "")[:200]
        refused = EgressDenied("redirect", f"HTTP {resp.status} to {location!r}")
        _audit(audit_store, purpose, policy, dest.origin, refused, dest)
        raise refused
    return resp


async def arequest(url: str, *, policy: EgressPolicy, purpose: str, method: str = "GET",
                   body: Optional[bytes] = None, headers: Optional[Mapping[str, str]] = None,
                   timeout: float = 30.0, store: Optional[EnvelopeStore] = None) -> EgressResponse:
    """Async wrapper: runs request() in a worker thread so there is one implementation."""
    return await asyncio.to_thread(
        request, url, policy=policy, purpose=purpose, method=method, body=body,
        headers=headers, timeout=timeout, store=store,
    )
