"""Tests for authority_runtime.egress: destination validation, IP pinning, audit."""

import http.server
import socket
import threading
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from authority_runtime import egress
from authority_runtime.storage import EnvelopeStore

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def store(tmp_path) -> EnvelopeStore:
    return EnvelopeStore(str(tmp_path / "audit.db"))


def audit_rows(store: EnvelopeStore) -> List[Dict[str, Any]]:
    rows = store.get_audit_trail()
    return sorted((r for r in rows if r["action"].startswith("egress:")), key=lambda r: r["id"])


def addrinfo(ip: str, port: int) -> List[Tuple[Any, ...]]:
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(fam, socket.SOCK_STREAM, 6, "", (ip, port))]


@pytest.fixture
def no_connect(monkeypatch) -> None:
    def fail(*a, **k):
        raise AssertionError("a denied destination must never be connected to")

    monkeypatch.setattr(egress, "_open_socket", fail)


class _Handler(http.server.BaseHTTPRequestHandler):
    seen_hosts: List[str] = []
    redirect_to: str = ""

    def do_GET(self) -> None:
        type(self).seen_hosts.append(self.headers.get("Host", ""))
        if type(self).redirect_to:
            self.send_response(302)
            self.send_header("Location", type(self).redirect_to)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def local_server() -> Iterator[Tuple[int, type]]:
    handler = type("H", (_Handler,), {"seen_hosts": [], "redirect_to": ""})
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1], handler
    srv.shutdown()
    srv.server_close()


def test_egress_metadata_denied(store, no_connect):
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata/computeMetadata/v1/",
        "http://METADATA.google.internal./",
    ):
        with pytest.raises(egress.EgressDenied) as exc:
            egress.request(url, policy=egress.NOTIFY, purpose="test", store=store)
        assert exc.value.reason != "unresolvable", url
    assert [r["result"] for r in audit_rows(store)] == ["deny"] * 4


@pytest.mark.parametrize("url", [
    "http://[fd00:ec2::254]/latest/meta-data/",
    "http://[fe80::1]/",
    "https://2130706433/",
    "https://0x7f.1/",
    "https://127.1/",
    "http://0.0.0.0:11434/",
    "http://[::ffff:127.0.0.1]/",
    "http://[::]/",
    "https://10.0.0.5/",
    "https://192.168.1.1/",
    "https://224.0.0.1/",
    "https://[ff02::1]/",
    "https://255.255.255.255/",
    f"http://{PUBLIC_IP}/",
    "file:///etc/passwd",
    "http://user:pw@127.0.0.1/",
])
def test_egress_private_ranges_denied(url, store, no_connect):
    with pytest.raises(egress.EgressDenied) as exc:
        egress.request(url, policy=egress.NOTIFY, purpose="test", store=store)
    assert exc.value.reason != "unresolvable"
    rows = audit_rows(store)
    assert len(rows) == 1 and rows[0]["result"] == "deny"


def test_egress_loopback_only_policy_denies_public(store, no_connect):
    with pytest.raises(egress.EgressDenied):
        egress.request(f"https://{PUBLIC_IP}/", policy=egress.LOCAL_SERVICE,
                       purpose="test", store=store)


def test_egress_dns_rebinding_pinned(store, monkeypatch):
    lookups: List[str] = []
    answers = [PUBLIC_IP, "169.254.169.254"]

    def resolver(host, port, *a, **k):
        lookups.append(host)
        return addrinfo(answers[len(lookups) - 1], port)

    connected: List[Tuple[Any, ...]] = []

    def record(family, sockaddr, timeout):
        connected.append(sockaddr)
        raise ConnectionRefusedError("stop before TLS")

    monkeypatch.setattr(egress, "_getaddrinfo", resolver)
    monkeypatch.setattr(egress, "_open_socket", record)

    with pytest.raises(OSError):
        egress.request("https://rebind.example/x", policy=egress.PUBLIC_HTTPS,
                       purpose="test", store=store)

    assert lookups == ["rebind.example"]
    assert connected == [(PUBLIC_IP, 443)]


def test_egress_mixed_answer_denied(store, monkeypatch, no_connect):
    def resolver(host, port, *a, **k):
        return addrinfo(PUBLIC_IP, port) + addrinfo("169.254.169.254", port)

    monkeypatch.setattr(egress, "_getaddrinfo", resolver)
    with pytest.raises(egress.EgressDenied):
        egress.request("https://mixed.example/", policy=egress.PUBLIC_HTTPS,
                       purpose="test", store=store)


def test_egress_pins_ip_and_preserves_host_header(store, monkeypatch, local_server):
    port, handler = local_server
    lookups: List[str] = []
    answers = ["127.0.0.1", "169.254.169.254"]

    def resolver(host, p, *a, **k):
        lookups.append(host)
        return addrinfo(answers[len(lookups) - 1], p)

    monkeypatch.setattr(egress, "_getaddrinfo", resolver)
    resp = egress.request(f"http://svc.test:{port}/p", policy=egress.LOCAL_SERVICE,
                          purpose="test", store=store)
    assert resp.status == 200 and resp.json() == {"ok": True}
    assert handler.seen_hosts == [f"svc.test:{port}"]
    assert lookups == ["svc.test"]


def test_egress_redirect_to_metadata_denied(store, local_server):
    port, handler = local_server
    handler.redirect_to = "http://169.254.169.254/latest/meta-data/"
    with pytest.raises(egress.EgressDenied) as exc:
        egress.request(f"http://127.0.0.1:{port}/", policy=egress.LOCAL_SERVICE,
                       purpose="test", store=store)
    assert exc.value.reason == "redirect"
    assert [r["result"] for r in audit_rows(store)] == ["allow", "deny"]


def test_egress_allows_public_host(store, monkeypatch):
    monkeypatch.setattr(egress, "_getaddrinfo", lambda h, p, *a, **k: addrinfo(PUBLIC_IP, p))
    connected: List[Tuple[Any, ...]] = []

    def record(family, sockaddr, timeout):
        connected.append(sockaddr)
        raise ConnectionRefusedError("stop before TLS")

    monkeypatch.setattr(egress, "_open_socket", record)
    dest = egress.evaluate("https://api.example.com/v1", egress.PUBLIC_HTTPS)
    assert dest.ip == PUBLIC_IP and dest.host == "api.example.com" and dest.port == 443

    with pytest.raises(OSError):
        egress.request("https://api.example.com/v1", policy=egress.PUBLIC_HTTPS,
                       purpose="test", store=store)
    assert connected == [(PUBLIC_IP, 443)]
    assert [r["result"] for r in audit_rows(store)] == ["allow"]


def test_egress_tries_each_pinned_address_without_reresolving(store, monkeypatch, local_server):
    port, handler = local_server
    lookups: List[str] = []

    def resolver(host, p, *a, **k):
        lookups.append(host)
        return addrinfo("::1", p) + addrinfo("127.0.0.1", p)

    monkeypatch.setattr(egress, "_getaddrinfo", resolver)
    resp = egress.request(f"http://localhost:{port}/", policy=egress.LOCAL_SERVICE,
                          purpose="test", store=store)
    assert resp.status == 200 and lookups == ["localhost"]
    assert audit_rows(store)[0]["metadata"]["pinned_ips"] == ["::1", "127.0.0.1"]


def test_egress_decision_is_audited(store, local_server):
    port, _ = local_server
    egress.request(f"http://127.0.0.1:{port}/api/tags?secret=x", policy=egress.LOCAL_SERVICE,
                   purpose="ollama", store=store)
    with pytest.raises(egress.EgressDenied):
        egress.request("http://169.254.169.254/", policy=egress.LOCAL_SERVICE,
                       purpose="ollama", store=store)

    allow, deny = audit_rows(store)
    assert allow["action"] == "egress:ollama" and allow["result"] == "allow"
    assert allow["resource"] == f"http://127.0.0.1:{port}"
    assert "secret" not in str(allow)
    assert deny["result"] == "deny" and deny["error"]
    assert store.verify_audit_chain()["valid"]


def test_egress_audit_failure_blocks_request(store, monkeypatch, local_server):
    port, handler = local_server

    def broken(entry):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "save_audit_entry", broken)
    with pytest.raises(egress.EgressDenied) as exc:
        egress.request(f"http://127.0.0.1:{port}/", policy=egress.LOCAL_SERVICE,
                       purpose="test", store=store)
    assert exc.value.reason == "audit_unavailable"
    assert handler.seen_hosts == []


def test_egress_caller_cannot_override_host_header(store, local_server, no_connect):
    port, _ = local_server
    with pytest.raises(egress.EgressDenied):
        egress.request(f"http://127.0.0.1:{port}/", policy=egress.LOCAL_SERVICE,
                       purpose="test", store=store, headers={"Host": "169.254.169.254"})
