"""Local gost-proxy supervisor (operator-driven, no root).

The admin panel lets the operator paste a raw ISP proxy
(``login:pass@ip:port``), verify that it actually egresses, and then "raise" a
local ``gost`` child process that re-exposes it as ``http://127.0.0.1:PORT``.
That local URL is what onboarding stores as an account's ISP proxy, so a new
account can be added end-to-end from the panel.

Design (Model A): processes are owned by the bot user — no systemd/root needed.
The upstream→port map is persisted in a 0600 JSON file; on bot startup and on a
periodic tick the supervisor re-spawns any entry whose process is not alive.

SECRET HYGIENE: upstream URLs carry proxy credentials. They live only in the
0600 state file and in process argv. They are NEVER logged and NEVER returned by
the public API — only ``host:port`` labels leave this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import account_onboarding

log = logging.getLogger("flow.proxy_supervisor")

GOST_BIN = os.getenv("GOST_BIN", "/usr/local/bin/gost")
BIND_HOST = "127.0.0.1"
# Local listen-port window for operator-raised proxies. Kept above the
# hand-managed systemd gost units (8118 privoxy, 8119-8128 gost) so the two
# never collide.
PORT_LO = int(os.getenv("LOCAL_PROXY_PORT_LO", "8129"))
PORT_HI = int(os.getenv("LOCAL_PROXY_PORT_HI", "8199"))
# Display window for the "used ports" panel (covers the legacy units too).
DISPLAY_LO = int(os.getenv("LOCAL_PROXY_DISPLAY_LO", "8118"))
DISPLAY_HI = PORT_HI

# Egress-IP probes (first that answers wins). Plain-text, tiny, no auth.
IP_ECHO_URLS = (
    "https://api.ipify.org?format=json",
    "https://ifconfig.me/ip",
    "https://www.cloudflare.com/cdn-cgi/trace",
)


class ProxyError(Exception):
    """Operator-facing, safe-to-display error (never contains credentials)."""


# ── pure helpers (no I/O — unit tested) ────────────────────────────────────

def normalize_upstream(raw: str | None) -> str:
    """Normalize a pasted proxy to a full ``scheme://[creds@]host:port`` URL.

    Reuses the onboarding normalizer so the accepted formats match exactly.
    Raises :class:`ProxyError` (safe message) on anything unusable.
    """
    try:
        url = account_onboarding.normalize_proxy_url(raw)
    except account_onboarding.AccountOnboardingError as exc:
        raise ProxyError(str(exc)) from exc
    if not url or url == "direct":
        raise ProxyError("empty_proxy")
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        raise ProxyError("invalid_proxy_url")
    return url


def proxy_label(url: str | None) -> str:
    """``scheme://host:port`` with credentials stripped (safe to log/show)."""
    return account_onboarding.proxy_public_label(url)


def local_url(port: int) -> str:
    return f"http://{BIND_HOST}:{int(port)}"


def first_free_port(used: set[int], lo: int = PORT_LO, hi: int = PORT_HI) -> int:
    """Lowest port in ``[lo, hi]`` not present in ``used``."""
    for port in range(lo, hi + 1):
        if port not in used:
            return port
    raise ProxyError("no_free_port")


def _listening_ports() -> set[int]:
    """LISTEN ports parsed from ``/proc/net/tcp{,6}`` (Linux). Empty elsewhere."""
    ports: set[int] = set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path, "r", encoding="ascii", errors="ignore") as fh:
                next(fh, None)  # header
                for line in fh:
                    cols = line.split()
                    if len(cols) < 4 or cols[3] != "0A":  # 0A = TCP_LISTEN
                        continue
                    local = cols[1]
                    hexport = local.rsplit(":", 1)[-1]
                    try:
                        ports.add(int(hexport, 16))
                    except ValueError:
                        continue
        except OSError:
            continue
    return ports


def _port_bindable(port: int) -> bool:
    """True if we can bind ``127.0.0.1:port`` right now (i.e. it is free)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((BIND_HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ── supervisor ─────────────────────────────────────────────────────────────

class LocalProxySupervisor:
    """Owns the operator-raised gost child processes and their state file."""

    def __init__(self, *, state_path: str | os.PathLike[str] | None = None,
                 gost_bin: str = GOST_BIN) -> None:
        self._path = Path(state_path or os.getenv("LOCAL_PROXIES_FILE", "local_proxies.json"))
        self._gost_bin = gost_bin
        # port -> {"port", "upstream", "label", "created_at"}
        self._entries: dict[int, dict] = {}
        self._procs: dict[int, subprocess.Popen] = {}
        self._lock = asyncio.Lock()
        self._load()

    # ── persistence ────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for row in (parsed or {}).get("entries", []):
            try:
                port = int(row["port"])
                upstream = str(row["upstream"])
            except (KeyError, TypeError, ValueError):
                continue
            self._entries[port] = {
                "port": port,
                "upstream": upstream,
                "label": str(row.get("label") or proxy_label(upstream)),
                "created_at": float(row.get("created_at") or time.time()),
            }

    def _save(self) -> None:
        payload = {"entries": [
            {k: e[k] for k in ("port", "upstream", "label", "created_at")}
            for e in self._entries.values()
        ]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
            try:
                os.chmod(self._path, 0o600)  # creds inside → owner-only
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── port bookkeeping ───────────────────────────────────────────────
    def used_ports(self) -> set[int]:
        return set(self._entries) | _listening_ports()

    def pick_free_port(self) -> int:
        used = self.used_ports()
        for port in range(PORT_LO, PORT_HI + 1):
            if port in used:
                continue
            if not _port_bindable(port):  # race / non-/proc fallback
                continue
            return port
        raise ProxyError("no_free_port")

    def ports_view(self) -> dict:
        """Safe snapshot for the admin panel — labels only, never creds."""
        listening = _listening_ports()
        managed_ports = set(self._entries)
        used = sorted(p for p in (listening | managed_ports)
                      if DISPLAY_LO <= p <= DISPLAY_HI)
        return {
            "used_ports": used,
            "managed": self.list_status(),
            "suggested_free": self._suggested_free(listening | managed_ports),
            "range": {"lo": PORT_LO, "hi": PORT_HI},
        }

    def _suggested_free(self, used: set[int]) -> int | None:
        for port in range(PORT_LO, PORT_HI + 1):
            if port not in used:
                return port
        return None

    # ── process lifecycle ──────────────────────────────────────────────
    def _alive(self, port: int) -> bool:
        proc = self._procs.get(port)
        return bool(proc and proc.poll() is None)

    def _spawn(self, port: int, upstream: str) -> None:
        if self._alive(port):
            return
        args = [self._gost_bin, "-L", f"http://{BIND_HOST}:{port}", "-F", upstream]
        # argv carries creds; never log `args`. Log only the safe label.
        proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        self._procs[port] = proc
        log.info("gost up on %s -> %s", local_url(port), proxy_label(upstream))

    def list_status(self) -> list[dict]:
        return [
            {
                "port": e["port"],
                "local_url": local_url(e["port"]),
                "label": e["label"],
                "alive": self._alive(e["port"]),
                "created_at": e["created_at"],
            }
            for e in sorted(self._entries.values(), key=lambda x: x["port"])
        ]

    async def ensure_all_running(self) -> None:
        """(Re)spawn any persisted entry whose process is not alive."""
        async with self._lock:
            for port, entry in list(self._entries.items()):
                if not self._alive(port):
                    try:
                        self._spawn(port, entry["upstream"])
                    except Exception:
                        log.warning("failed to spawn gost on port %s", port, exc_info=True)

    async def supervise_loop(self, interval: float = 30.0) -> None:
        while True:
            try:
                await self.ensure_all_running()
            except Exception:
                log.warning("supervise tick failed", exc_info=True)
            await asyncio.sleep(interval)

    def teardown(self, port: int) -> bool:
        port = int(port)
        entry = self._entries.pop(port, None)
        proc = self._procs.pop(port, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                log.warning("teardown kill failed for port %s", port, exc_info=True)
        if entry is not None:
            self._save()
            log.info("gost down on %s", local_url(port))
            return True
        return False

    def shutdown(self) -> None:
        for port in list(self._procs):
            proc = self._procs.get(port)
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

    # ── network checks ─────────────────────────────────────────────────
    async def _egress_through(self, proxy_url: str) -> dict:
        """Fetch an IP-echo through ``proxy_url``; return {ok, egress_ip, latency_ms}."""
        import aiohttp  # lazy: keep module importable without aiohttp in tests

        started = time.monotonic()
        last_err = "no_response"
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            for url in IP_ECHO_URLS:
                try:
                    async with http.get(url, proxy=proxy_url) as resp:
                        if resp.status != 200:
                            last_err = f"http_{resp.status}"
                            continue
                        text = (await resp.text())[:500].strip()
                except Exception as exc:  # noqa: BLE001 - surface class only
                    last_err = exc.__class__.__name__
                    continue
                ip = _parse_egress_ip(text)
                if ip:
                    return {
                        "ok": True,
                        "egress_ip": ip,
                        "latency_ms": int((time.monotonic() - started) * 1000),
                    }
                last_err = "unparsed_ip"
        return {"ok": False, "error": last_err,
                "latency_ms": int((time.monotonic() - started) * 1000)}

    async def check_upstream(self, raw: str) -> dict:
        """Verify a pasted upstream proxy works. Never raises; returns a dict."""
        try:
            upstream = normalize_upstream(raw)
        except ProxyError as exc:
            return {"ok": False, "error": str(exc)}
        res = await self._egress_through(upstream)
        res["label"] = proxy_label(upstream)
        return res

    async def raise_proxy(self, raw: str) -> dict:
        """Verify upstream, spawn a local gost, verify the local port, persist.

        Idempotent on the upstream URL: re-raising one already mapped returns the
        existing port. Returns a creds-free dict for the panel.
        """
        upstream = normalize_upstream(raw)
        async with self._lock:
            for entry in self._entries.values():  # idempotent reuse
                if entry["upstream"] == upstream:
                    port = entry["port"]
                    if not self._alive(port):
                        self._spawn(port, upstream)
                    return {"port": port, "local_url": local_url(port),
                            "label": entry["label"], "reused": True}
            # Confirm the upstream egresses before we bother binding a port.
            up_check = await self._egress_through(upstream)
            if not up_check.get("ok"):
                raise ProxyError(up_check.get("error") or "upstream_unreachable")

            port = self.pick_free_port()
            self._spawn(port, upstream)
            local = await self._verify_local(port)
            if not local.get("ok"):
                self.teardown(port)
                raise ProxyError("local_proxy_failed")

            self._entries[port] = {
                "port": port,
                "upstream": upstream,
                "label": proxy_label(upstream),
                "created_at": time.time(),
            }
            self._save()
            return {
                "port": port,
                "local_url": local_url(port),
                "label": self._entries[port]["label"],
                "egress_ip": local.get("egress_ip") or up_check.get("egress_ip"),
                "reused": False,
            }

    async def _verify_local(self, port: int, attempts: int = 6) -> dict:
        """Poll the freshly-spawned local port until it proxies (gost warmup)."""
        for i in range(attempts):
            await asyncio.sleep(0.5 * (i + 1))
            if not self._alive(port):
                return {"ok": False, "error": "process_exited"}
            res = await self._egress_through(local_url(port))
            if res.get("ok"):
                return res
        return {"ok": False, "error": "local_timeout"}


def _parse_egress_ip(text: str) -> str | None:
    """Extract an IP from ipify JSON / ifconfig plain / cloudflare trace."""
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("{"):
        try:
            return str(json.loads(t).get("ip") or "") or None
        except ValueError:
            return None
    if "ip=" in t:  # cloudflare trace: lines of key=value
        for line in t.splitlines():
            if line.startswith("ip="):
                return line[3:].strip() or None
        return None
    first = t.splitlines()[0].strip()
    return first or None
