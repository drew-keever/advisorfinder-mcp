"""Abuse protection for tool calls: the real client IP, per-IP rate limits
by source class, and one usage log line per tool call.

The server is public with no login, runs as ONE instance, and is stateless
HTTP, so all state here is in-memory and per process. Only tools/call is
limited and logged (ToolCallGuard.on_call_tool); initialize, tools/list and
resource reads are not.

Source classes:
- "anthropic": the client IP is in Anthropic's documented OUTBOUND range
  (https://platform.claude.com/docs/en/api/ip-addresses, checked
  2026-10-23: IPv4 160.79.104.0/21 only; 2607:6bc0::/48 is listed as
  inbound only). Every claude.ai user arrives through these shared IPs, so
  the per-IP limit is large and there is no daily cap.
- "other": every other IP (Claude Code direct, scripts, ...).

Environment overrides (read once, when the server module is imported; an
unreadable or negative value keeps the default; for the four rate limits, 0
means no limit; MCP_RATE_LIMIT_MAX_TRACKED_IPS=0 keeps the default, so the
IP table is always bounded):
  MCP_RATE_LIMIT_ANTHROPIC_PER_MINUTE   default 600
  MCP_RATE_LIMIT_ANTHROPIC_PER_DAY      default 0 (no daily cap)
  MCP_RATE_LIMIT_OTHER_PER_MINUTE       default 60
  MCP_RATE_LIMIT_OTHER_PER_DAY          default 1000 (rolling 24 hours)
  MCP_RATE_LIMIT_MAX_TRACKED_IPS        default 10000
  MCP_CLIENT_IP_HEADERS                 default
                                        "cf-connecting-ip,true-client-ip,x-forwarded-for"

Privacy: the usage log never holds tool arguments (search terms are not
stored, per the privacy policy) or the raw IP -- only a short keyed hash of
the IP, with a random key made per process.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import math
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware

log = logging.getLogger("advisorfinder_mcp.guard")
usage_log = logging.getLogger("advisorfinder_mcp.usage")

# ── client IP ────────────────────────────────────────────────────────────────

# Headers that can carry the real client IP, in the order they are tried.
# CF-Connecting-IP is set by Cloudflare (Render's edge) and overwrites any
# value a client sends; True-Client-IP is set only on some Cloudflare plans.
# X-Forwarded-For is a LAST RESORT: its first entry can be written by the
# client, so its use is logged. The live server must confirm which header
# carries the real IP; MCP_CLIENT_IP_HEADERS overrides this order.
CLIENT_IP_HEADERS: tuple[str, ...] = ("cf-connecting-ip", "true-client-ip", "x-forwarded-for")
CLIENT_IP_HEADERS_ENV = "MCP_CLIENT_IP_HEADERS"
_FORWARDED_FOR = "x-forwarded-for"


def header_order_from_env() -> tuple[str, ...]:
    """CLIENT_IP_HEADERS, or the comma-separated MCP_CLIENT_IP_HEADERS list
    (lowercased, blanks dropped) when that is set and not empty."""
    raw = os.environ.get(CLIENT_IP_HEADERS_ENV, "")
    names = tuple(n.strip().lower() for n in raw.split(",") if n.strip())
    return names or CLIENT_IP_HEADERS


def _parse_ip(value: str) -> str | None:
    """The normalized text form of one IP address, or None if `value` is not
    one. An IPv4-mapped IPv6 address becomes the plain IPv4 address."""
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def client_ip(
    headers: Mapping[str, str],
    peer_host: str | None,
    order: tuple[str, ...] | None = None,
) -> tuple[str | None, str]:
    """(client IP, where it came from). Tries each header in `order`
    (default: header_order_from_env()) and uses the first one with a valid
    IP; for X-Forwarded-For that is its first valid entry. An unparseable
    value is skipped. Falls back to the TCP peer ("peer"), then (None,
    "none"). `headers` must look names up case-insensitively (Starlette
    headers do) or use lowercase keys."""
    for name in order if order is not None else header_order_from_env():
        value = headers.get(name)
        if not value:
            continue
        candidates = value.split(",") if name == _FORWARDED_FOR else [value]
        for candidate in candidates:
            ip = _parse_ip(candidate)
            if ip is not None:
                return ip, name
    if peer_host:
        ip = _parse_ip(peer_host)
        if ip is not None:
            return ip, "peer"
    return None, "none"


# Anthropic's documented outbound IPs (see the module docstring).
ANTHROPIC_NETWORKS = (ipaddress.ip_network("160.79.104.0/21"),)


def source_class(ip: str) -> str:
    """"anthropic" for an IP in ANTHROPIC_NETWORKS, else "other"."""
    addr = ipaddress.ip_address(ip)
    if any(addr in net for net in ANTHROPIC_NETWORKS):
        return "anthropic"
    return "other"


def _is_public(ip: str) -> bool:
    return ipaddress.ip_address(ip).is_global


# Per-process key: client ids cannot be reversed to an IP, and they change
# on every restart.
_CLIENT_ID_KEY = secrets.token_bytes(32)


def client_id(ip: str) -> str:
    """A short, non-reversible id for an IP (first 10 hex characters of
    HMAC-SHA256 with a per-process random key). Stable inside one process."""
    return hmac.new(_CLIENT_ID_KEY, ip.encode(), hashlib.sha256).hexdigest()[:10]


# ── limits ───────────────────────────────────────────────────────────────────

ANTHROPIC_PER_MINUTE = 600
ANTHROPIC_PER_DAY = 0  # 0 = no daily cap
OTHER_PER_MINUTE = 60
OTHER_PER_DAY = 1000
MAX_TRACKED_IPS = 10_000


@dataclass(frozen=True)
class Limits:
    """Per-IP limits for one source class. 0 means no limit."""
    per_minute: int
    per_day: int


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        value = -1
    if value < 0:
        log.warning("ignoring %s=%r (not a whole number >= 0); using %d", name, raw, default)
        return default
    return value


def limits_from_env() -> tuple[dict[str, Limits], int]:
    """({class: Limits}, max tracked IPs) from the defaults above and the
    MCP_RATE_LIMIT_* overrides."""
    limits = {
        "anthropic": Limits(
            per_minute=_env_int("MCP_RATE_LIMIT_ANTHROPIC_PER_MINUTE", ANTHROPIC_PER_MINUTE),
            per_day=_env_int("MCP_RATE_LIMIT_ANTHROPIC_PER_DAY", ANTHROPIC_PER_DAY),
        ),
        "other": Limits(
            per_minute=_env_int("MCP_RATE_LIMIT_OTHER_PER_MINUTE", OTHER_PER_MINUTE),
            per_day=_env_int("MCP_RATE_LIMIT_OTHER_PER_DAY", OTHER_PER_DAY),
        ),
    }
    max_tracked = _env_int("MCP_RATE_LIMIT_MAX_TRACKED_IPS", MAX_TRACKED_IPS) or MAX_TRACKED_IPS
    return limits, max_tracked


_HOUR = 3600
_DAY_HOURS = 24


class _Entry:
    """One IP's state: a token bucket for the per-minute limit and a count
    per clock hour for the rolling 24-hour limit (at most 24 hours kept)."""
    __slots__ = ("cls", "tokens", "last", "hours")

    def __init__(self, cls: str, tokens: float, now: float):
        self.cls = cls
        self.tokens = tokens
        self.last = now
        self.hours: dict[int, int] = {}


class RateLimiter:
    """Per-IP limits, in memory, safe to call from any thread or the event
    loop (one lock; no awaits inside it).

    Per minute: a token bucket per IP (capacity = the per-minute limit,
    refilled at limit/60 per second), so a burst up to the limit is allowed.
    Per day: a rolling 24-hour window counted in clock-hour slots (the
    current hour and the 23 before it), so the window moves by the hour.
    A refused call uses none of either limit.

    Memory: at most `max_tracked` IPs (least recently seen evicted first);
    idle entries are removed every CLEANUP_EVERY checks."""

    CLEANUP_EVERY = 1000

    def __init__(
        self,
        limits: Mapping[str, Limits],
        max_tracked: int = MAX_TRACKED_IPS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limits = dict(limits)
        self.max_tracked = max(1, max_tracked)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._checks = 0

    def tracked_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def check(self, ip: str, cls: str) -> float | None:
        """None if the call is allowed (and counted), else the number of
        seconds until a call from this IP can be allowed again."""
        limits = self.limits[cls]
        if not limits.per_minute and not limits.per_day:
            return None
        with self._lock:
            now = self._clock()
            self._checks += 1
            if self._checks % self.CLEANUP_EVERY == 0:
                self._cleanup_locked(now)

            entry = self._entries.get(ip)
            if entry is None or entry.cls != cls:
                while len(self._entries) >= self.max_tracked:
                    self._entries.popitem(last=False)
                entry = _Entry(cls, float(limits.per_minute), now)
                self._entries[ip] = entry
            else:
                self._entries.move_to_end(ip)

            retry = 0.0
            if limits.per_minute:
                rate = limits.per_minute / 60.0
                entry.tokens = min(
                    float(limits.per_minute), entry.tokens + (now - entry.last) * rate
                )
                entry.last = now
                if entry.tokens < 1.0:
                    retry = (1.0 - entry.tokens) / rate
            if limits.per_day:
                hour = int(now // _HOUR)
                oldest = hour - (_DAY_HOURS - 1)
                for h in [h for h in entry.hours if h < oldest]:
                    del entry.hours[h]
                used = sum(entry.hours.values())
                if used >= limits.per_day:
                    # Wait until enough old hour slots leave the window.
                    remaining = used
                    for h in sorted(entry.hours):
                        remaining -= entry.hours[h]
                        if remaining < limits.per_day:
                            retry = max(retry, (h + _DAY_HOURS) * _HOUR - now)
                            break
            if retry > 0:
                return retry

            if limits.per_minute:
                entry.tokens -= 1.0
            if limits.per_day:
                hour = int(now // _HOUR)
                entry.hours[hour] = entry.hours.get(hour, 0) + 1
            return None

    def cleanup(self) -> None:
        """Removes entries that hold no state any more: a full token bucket
        and no calls inside the 24-hour window."""
        with self._lock:
            self._cleanup_locked(self._clock())

    def _cleanup_locked(self, now: float) -> None:
        oldest = int(now // _HOUR) - (_DAY_HOURS - 1)
        idle = []
        for ip, entry in self._entries.items():
            limits = self.limits[entry.cls]
            minute_idle = not limits.per_minute or (
                entry.tokens + (now - entry.last) * limits.per_minute / 60.0
                >= limits.per_minute
            )
            day_idle = not any(h >= oldest for h in entry.hours)
            if minute_idle and day_idle:
                idle.append(ip)
        for ip in idle:
            del self._entries[ip]


def _wait_text(seconds: float) -> str:
    s = max(1, math.ceil(seconds))
    if s < 120:
        return f"{s} seconds"
    if s < 2 * _HOUR:
        return f"about {math.ceil(s / 60)} minutes"
    return f"about {math.ceil(s / _HOUR)} hours"


def rate_limit_message(retry_after: float) -> str:
    """The tool error text for a refused call. No internal details."""
    return (
        "The rate limit for this AdvisorFinder connector was reached. "
        f"Please retry in {_wait_text(retry_after)}. This connector is for "
        "interactive research, not bulk data extraction."
    )


# ── middleware ───────────────────────────────────────────────────────────────

# A tool name is logged only when it looks like one (no spaces, no line
# breaks), so a client cannot write arbitrary text into the log.
_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")


def _current_request():
    """The HTTP request behind the current MCP request, or None (in-process
    clients have none)."""
    try:
        return get_http_request()
    except RuntimeError:
        return None


class ToolCallGuard(Middleware):
    """Rate-limits and logs tools/call requests (on_call_tool only).

    A call is limited only when its client IP is a public address. No HTTP
    request (in-process client), no IP, or a private/loopback IP (the proxy
    itself, e.g. Render's 10.x when no client-IP header is present) is not
    limited: limiting a proxy address would put every user in one bucket.
    Any failure inside the guard is logged and the call goes through (fail
    open); only the deliberate rate-limit ToolError is raised here."""

    def __init__(self, limiter: RateLimiter):
        self.limiter = limiter
        self._xff_warned = False

    async def on_call_tool(self, context, call_next):
        t0 = time.perf_counter()
        raw_name = getattr(context.message, "name", None)
        tool = raw_name if isinstance(raw_name, str) and _TOOL_NAME_RE.fullmatch(raw_name) else "invalid"
        cls, cid, header = "-", "-", "none"
        retry = None
        try:
            request = _current_request()
            if request is not None:
                peer = request.client.host if request.client else None
                ip, header = client_ip(request.headers, peer)
                if header == _FORWARDED_FOR and not self._xff_warned:
                    self._xff_warned = True
                    log.warning(
                        "client IP taken from X-Forwarded-For (no CF-Connecting-IP or "
                        "True-Client-IP header); its first entry can be set by the client"
                    )
                if ip is not None:
                    cls = source_class(ip)
                    cid = client_id(ip)
                    if _is_public(ip):
                        retry = self.limiter.check(ip, cls)
        except Exception:
            log.warning("tool call guard failed; call allowed", exc_info=True)
            retry = None

        if retry is not None:
            self._log(tool, cls, cid, header, "rate_limited", t0, None)
            raise ToolError(rate_limit_message(retry))

        try:
            result = await call_next(context)
        except BaseException:
            self._log(tool, cls, cid, header, "error", t0, None)
            raise
        count = None
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            value = structured.get("result_count")
            if isinstance(value, int) and not isinstance(value, bool):
                count = value
        self._log(tool, cls, cid, header, "ok", t0, count)
        return result

    @staticmethod
    def _log(tool, cls, cid, header, outcome, t0, count) -> None:
        try:
            duration_ms = round((time.perf_counter() - t0) * 1000)
            if count is None:
                usage_log.info(
                    "tool_call tool=%s class=%s client=%s ip_header=%s outcome=%s duration_ms=%d",
                    tool, cls, cid, header, outcome, duration_ms,
                )
            else:
                usage_log.info(
                    "tool_call tool=%s class=%s client=%s ip_header=%s outcome=%s "
                    "duration_ms=%d result_count=%d",
                    tool, cls, cid, header, outcome, duration_ms, count,
                )
        except Exception:
            pass


def from_env() -> ToolCallGuard:
    """The guard with the limits from the environment (see the module
    docstring)."""
    limits, max_tracked = limits_from_env()
    return ToolCallGuard(RateLimiter(limits, max_tracked=max_tracked))
