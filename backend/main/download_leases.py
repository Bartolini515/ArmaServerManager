"""Ownership-aware Redis lease primitive for serialized Steam downloads.

This lease is an expiring coordination signal, not a durable task claim or
fence. Work that can outlive the TTL still needs heartbeat monitoring and a
downstream persistent fence or cancellation mechanism.
"""

from __future__ import annotations

import codecs
import ipaddress
import math
from queue import LifoQueue
import socket
import ssl
import threading
import uuid
from numbers import Real
from typing import Any

from redis.backoff import NoBackoff
from redis.connection import (
    BlockingConnectionPool,
    Connection,
    DefaultParser,
    SSLConnection,
)
from redis.event import EventDispatcher
from redis.retry import Retry


_RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

_HEARTBEAT_SHUTDOWN_TIMEOUT_SECONDS = 1.0
_MAX_CLIENT_IO_TIMEOUT_SECONDS = 1.0
_MAX_TTL_MILLISECONDS = (2**63) - 1
_SUPPORTED_CONNECTION_OPTIONS = frozenset(
    {
        "host",
        "port",
        "numeric_host",
        "socket_type",
        "socket_keepalive",
        "socket_keepalive_options",
        "db",
        "password",
        "username",
        "socket_timeout",
        "socket_connect_timeout",
        "retry",
        "retry_on_timeout",
        "retry_on_error",
        "encoding",
        "encoding_errors",
        "decode_responses",
        "parser_class",
        "socket_read_size",
        "health_check_interval",
        "client_name",
        "lib_name",
        "lib_version",
        "protocol",
        "command_packer",
    }
)
_SUPPORTED_TLS_OPTIONS = frozenset(
    {
        "ssl_cert_reqs",
        "ssl_check_hostname",
        "ssl_keyfile",
        "ssl_certfile",
        "ssl_ca_certs",
        "ssl_ca_path",
        "ssl_ca_data",
        "ssl_password",
        "ssl_min_version",
        "ssl_ciphers",
        "ssl_validate_ocsp",
        "ssl_validate_ocsp_stapled",
    }
)


def _parse_numeric_host(value: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if not isinstance(value, str) or "%" in value:
        raise ValueError("numeric_host must be an unscoped IPv4 or IPv6 literal")
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("numeric_host must be an unscoped IPv4 or IPv6 literal") from None


def _parse_tcp_port(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("port must be between 1 and 65535")
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        port = int(value)
    else:
        raise ValueError("port must be between 1 and 65535")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return port


def _is_bounded_timeout(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        seconds = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    return math.isfinite(seconds) and 0 < seconds <= _MAX_CLIENT_IO_TIMEOUT_SECONDS


def _validate_connection_timeouts(options: dict[str, Any]) -> None:
    if not _is_bounded_timeout(options.get("socket_timeout")):
        raise ValueError("socket_timeout must be positive and at most one second")
    if not _is_bounded_timeout(options.get("socket_connect_timeout")):
        raise ValueError("socket_connect_timeout must be positive and at most one second")
    socket_type = options.get("socket_type", 0)
    if (
        isinstance(socket_type, bool)
        or not isinstance(socket_type, int)
        or socket_type not in (0, socket.SOCK_STREAM)
    ):
        raise ValueError("socket_type must select TCP stream connections")


def _connection_host_and_port(
    args: tuple[object, ...],
    kwargs: dict[str, Any],
) -> tuple[str, int]:
    host = args[0] if args else kwargs.get("host", "localhost")
    port = args[1] if len(args) > 1 else kwargs.get("port", 6379)
    if not isinstance(host, str) or not host:
        raise ValueError("host must be a non-empty string")
    return host, _parse_tcp_port(port)


def _close_socket(sock: Any) -> None:
    try:
        sock.close()
    except Exception:
        pass


def _open_numeric_socket(connection: Any) -> socket.socket:
    address = connection._numeric_address
    if address.version == 4:
        family = socket.AF_INET
        socket_address = (address.compressed, connection.port)
    else:
        family = socket.AF_INET6
        socket_address = (address.compressed, connection.port, 0, 0)

    sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if connection.socket_keepalive:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for option, value in connection.socket_keepalive_options.items():
                sock.setsockopt(socket.IPPROTO_TCP, option, value)

        sock.settimeout(connection.socket_connect_timeout)
        sock.connect(socket_address)
        sock.settimeout(connection.socket_timeout)
        return sock
    except BaseException:
        _close_socket(sock)
        raise


def _secure_tls_options(options: dict[str, Any]) -> bool:
    try:
        cert_reqs = options.get("ssl_cert_reqs", "required")
        if cert_reqs not in ("required", ssl.CERT_REQUIRED):
            return False
        if options.get("ssl_check_hostname", True) is not True:
            return False
        if options.get("ssl_validate_ocsp", False):
            return False
        if options.get("ssl_validate_ocsp_stapled", False):
            return False
    except Exception:
        return False
    return True


def _is_standard_retry(retry: object) -> bool:
    if type(retry) is not Retry:
        return False
    try:
        retry_state = vars(retry)
        if set(retry_state) != {"_backoff", "_retries", "_supported_errors"}:
            return False
        backoff = retry_state["_backoff"]
        if (
            type(backoff) is not NoBackoff
            or set(vars(backoff)) != {"_backoff"}
            or type(vars(backoff)["_backoff"]) is not int
            or vars(backoff)["_backoff"] != 0
        ):
            return False
        if (
            type(retry_state["_retries"]) is not int
            or retry_state["_retries"] != 0
            or type(retry_state["_supported_errors"]) is not tuple
        ):
            return False
        standard_errors = Retry(NoBackoff(), 0)._supported_errors
        actual_errors = retry_state["_supported_errors"]
        return len(actual_errors) == len(standard_errors) and all(
            actual is expected for actual, expected in zip(actual_errors, standard_errors)
        )
    except Exception:
        return False


def _has_standard_event_dispatcher(dispatcher: object) -> bool:
    if type(dispatcher) is not EventDispatcher:
        return False
    try:
        dispatcher_state = vars(dispatcher)
        if set(dispatcher_state) != {"_event_listeners_mapping"}:
            return False
        listeners = dispatcher_state["_event_listeners_mapping"]
        if type(listeners) is not dict:
            return False
        standard_listeners = EventDispatcher()._event_listeners_mapping
        if listeners.keys() != standard_listeners.keys():
            return False
        for event_type, expected in standard_listeners.items():
            actual = listeners[event_type]
            if type(actual) is not list or len(actual) != len(expected):
                return False
            for listener, standard_listener in zip(actual, expected):
                if type(listener) is not type(standard_listener):
                    return False
                actual_state = vars(listener)
                standard_state = vars(standard_listener)
                if actual_state.keys() != standard_state.keys():
                    return False
                if any(value is not None for value in actual_state.values()):
                    return False
        return True
    except Exception:
        return False


def _is_supported_connection_options(
    connection_class: type[Connection],
    options: object,
) -> bool:
    if type(options) is not dict:
        return False
    if any(type(option) is not str for option in options):
        return False
    allowed = _SUPPORTED_CONNECTION_OPTIONS
    if connection_class is BoundedSSLConnection:
        allowed = allowed | _SUPPORTED_TLS_OPTIONS
    if not options.keys() <= allowed:
        return False

    if type(options.get("host", "localhost")) is not str:
        return False
    if type(options.get("numeric_host")) is not str:
        return False
    if type(options.get("port", 6379)) not in (int, str):
        return False
    if "db" in options and (
        type(options["db"]) is not int or options["db"] < 0
    ):
        return False
    for name in ("username", "password"):
        if (
            name in options
            and options[name] is not None
            and type(options[name]) not in (str, bytes)
        ):
            return False

    if "socket_type" in options and (
        type(options["socket_type"]) is not int
        or options["socket_type"] not in (0, socket.SOCK_STREAM)
    ):
        return False
    if "socket_keepalive" in options and type(options["socket_keepalive"]) is not bool:
        return False
    keepalive_options = options.get("socket_keepalive_options")
    if keepalive_options is not None:
        if type(keepalive_options) is not dict:
            return False
        if any(
            type(option) is not int or type(value) not in (int, bytes)
            for option, value in keepalive_options.items()
        ):
            return False

    for name, supported_value in (
        ("encoding", "utf-8"),
        ("encoding_errors", "strict"),
    ):
        if name in options and (
            type(options[name]) is not str or options[name] != supported_value
        ):
            return False
    try:
        if codecs.lookup_error("strict") is not codecs.strict_errors:
            return False
    except Exception:
        return False
    if "decode_responses" in options and type(options["decode_responses"]) is not bool:
        return False
    if "parser_class" in options and options["parser_class"] is not DefaultParser:
        return False
    if "command_packer" in options and options["command_packer"] is not None:
        return False
    if "socket_read_size" in options and (
        type(options["socket_read_size"]) is not int
        or not 1 <= options["socket_read_size"] <= 1_048_576
    ):
        return False
    if "health_check_interval" in options and options["health_check_interval"] != 0:
        return False
    if type(options.get("health_check_interval", 0)) is not int:
        return False
    if (
        "client_name" in options
        and options["client_name"] is not None
        and type(options["client_name"]) is not str
    ):
        return False
    for name in ("lib_name", "lib_version"):
        if name in options and options[name] is not None and type(options[name]) is not str:
            return False
    if "protocol" in options and (
        type(options["protocol"]) is not int or options["protocol"] not in (2, 3)
    ):
        return False

    if "retry_on_timeout" in options and options["retry_on_timeout"] is not False:
        return False
    if "retry_on_error" in options and (
        type(options["retry_on_error"]) is not list or options["retry_on_error"]
    ):
        return False
    if "retry" in options and options["retry"] is not None and not _is_standard_retry(
        options["retry"]
    ):
        return False

    if connection_class is BoundedSSLConnection:
        if not _secure_tls_options(options):
            return False
        for name in (
            "ssl_keyfile",
            "ssl_certfile",
            "ssl_ca_certs",
            "ssl_ca_path",
            "ssl_ca_data",
            "ssl_password",
            "ssl_ciphers",
        ):
            if name in options and options[name] is not None and type(options[name]) is not str:
                return False
        if "ssl_cert_reqs" in options and type(options["ssl_cert_reqs"]) not in (
            str,
            ssl.VerifyMode,
        ):
            return False
        if "ssl_check_hostname" in options and type(options["ssl_check_hostname"]) is not bool:
            return False
        if "ssl_min_version" in options and options["ssl_min_version"] is not None and type(
            options["ssl_min_version"]
        ) is not ssl.TLSVersion:
            return False
        for name in ("ssl_validate_ocsp", "ssl_validate_ocsp_stapled"):
            if name in options and options[name] is not False:
                return False
    return True


class BoundedConnection(Connection):
    """Redis TCP connection that connects only to a validated numeric address."""

    def __init__(self, *args: object, numeric_host: str, **kwargs: Any) -> None:
        if len(args) > 2:
            raise ValueError("connection options must be passed by keyword")
        if "ssl" in kwargs or any(option.startswith("ssl_") for option in kwargs):
            raise ValueError("TLS options require BoundedSSLConnection")
        _validate_connection_timeouts(kwargs)
        host, port = _connection_host_and_port(args, kwargs)
        self._numeric_address = _parse_numeric_host(numeric_host)
        self.numeric_host = self._numeric_address.compressed
        if len(args) > 1:
            normalized_args = list(args)
            normalized_args[1] = port
            args = tuple(normalized_args)
        else:
            kwargs["port"] = port
        if not host:
            raise ValueError("host must be a non-empty string")
        super().__init__(*args, **kwargs)

    def _connect(self) -> socket.socket:
        return _open_numeric_socket(self)


class BoundedSSLConnection(SSLConnection):
    """Redis TLS connection with numeric TCP routing and hostname verification."""

    def __init__(self, *args: object, numeric_host: str, **kwargs: Any) -> None:
        if args:
            raise ValueError("BoundedSSLConnection requires keyword connection parameters")
        if "ssl" in kwargs:
            raise ValueError("TLS transport is selected with BoundedSSLConnection")
        if not _secure_tls_options(kwargs):
            raise ValueError("TLS verification settings are unsupported")
        _validate_connection_timeouts(kwargs)
        host, port = _connection_host_and_port((), kwargs)
        self._numeric_address = _parse_numeric_host(numeric_host)
        self.numeric_host = self._numeric_address.compressed
        kwargs["host"] = host
        kwargs["port"] = port
        super().__init__(**kwargs)

    def _connect(self) -> socket.socket:
        sock = _open_numeric_socket(self)
        try:
            return self._wrap_socket_with_ssl(sock)
        except BaseException:
            _close_socket(sock)
            raise


class DownloadLeaseError(RuntimeError):
    """A safe, serializable lease error without infrastructure diagnostics."""

    code = "download_lease_error"
    message = "Download lease operation failed."

    def __init__(self) -> None:
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


class LeaseUnavailableError(DownloadLeaseError):
    """Redis could not safely complete the lease operation."""

    code = "lease_unavailable"
    message = "Download lease service unavailable."


class LeaseConfigurationError(DownloadLeaseError):
    """The injected Redis client cannot guarantee bounded lease operations."""

    code = "lease_client_unsupported"
    message = "Download lease client configuration is unsupported."


class LeaseLostError(DownloadLeaseError):
    """This attempt no longer has a usable lease."""

    code = "lease_lost"
    message = "Download lease is not owned by this attempt."


class AtomicDownloadLease:
    """Acquire and renew one owner-token Redis lease.

    The injected Redis client must support ``SET key value NX PX`` and atomic
    Lua ``EVAL`` through ``BoundedConnection`` or ``BoundedSSLConnection``.
    Construction accepts only a redis-py ``BlockingConnectionPool`` with
    positive pool, socket, and connect timeouts no greater than one second,
    zero automatic retries, and a pre-resolved numeric host. The helper never
    falls back to separate GET/SET or DELETE calls. DNS preparation belongs
    before this lease and before host work. Call :meth:`assert_owned`
    synchronously before critical actions and observe :meth:`wait_lost` while
    supervising long-running work. The client contract is rechecked before
    every Redis command.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        key: str = "asm:steam-download:v1",
        ttl_seconds: float = 120,
        heartbeat_seconds: float = 30,
    ) -> None:
        self._ttl_seconds = self._positive_finite_seconds("ttl_seconds", ttl_seconds)
        self._heartbeat_seconds = self._positive_finite_seconds(
            "heartbeat_seconds",
            heartbeat_seconds,
        )
        if self._heartbeat_seconds >= self._ttl_seconds:
            raise ValueError("heartbeat_seconds must be less than ttl_seconds")
        if self._heartbeat_seconds > threading.TIMEOUT_MAX:
            raise ValueError("heartbeat_seconds exceeds the supported thread wait timeout")

        ttl_milliseconds_float = self._ttl_seconds * 1000
        if (
            not math.isfinite(ttl_milliseconds_float)
            or ttl_milliseconds_float > _MAX_TTL_MILLISECONDS
        ):
            raise ValueError("ttl_seconds exceeds the supported Redis expiry range")
        ttl_milliseconds = math.ceil(ttl_milliseconds_float)
        self._ttl_milliseconds = ttl_milliseconds

        if redis_client is None:
            raise ValueError("redis_client is required")
        if not isinstance(key, (str, bytes)) or not key:
            raise ValueError("key must be a non-empty string or bytes value")
        if not self._has_bounded_redis_io(redis_client):
            raise LeaseConfigurationError() from None

        self._redis = redis_client
        self._key = key
        self._owner_token = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._lost_event = threading.Event()
        self._acquire_attempted = False
        self._acquired = False
        self._lost = False
        self._released = False
        self._release_attempted = False
        self._release_result = False
        self._release_complete = False
        self._release_waiters = 0
        self._release_condition = threading.Condition(self._lock)
        self._heartbeat_started = False
        self._thread_started = False
        self._heartbeat_thread: threading.Thread | None = None

    @staticmethod
    def _positive_finite_seconds(name: str, value: Real) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{name} must be a finite positive number")
        try:
            seconds = float(value)
        except (OverflowError, TypeError, ValueError):
            raise ValueError(f"{name} must be a finite positive number") from None
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f"{name} must be a finite positive number")
        return seconds

    @staticmethod
    def _bounded_timeout(value: object) -> bool:
        return _is_bounded_timeout(value)

    @classmethod
    def _has_bounded_redis_io(cls, redis_client: Any) -> bool:
        """Require only redis-py's stock bounded execution path and options."""
        try:
            pool = getattr(redis_client, "connection_pool", None)
            if type(pool) is not BlockingConnectionPool:
                return False
            if getattr(redis_client, "_single_connection_client", None) is not False:
                return False
            if not _has_standard_event_dispatcher(
                getattr(redis_client, "_event_dispatcher", None)
            ):
                return False
            connection_class = getattr(pool, "connection_class", None)
            if (
                connection_class is not BoundedConnection
                and connection_class is not BoundedSSLConnection
            ):
                return False

            max_connections = getattr(pool, "max_connections", None)
            if (
                type(max_connections) is not int
                or max_connections <= 0
                or pool.queue_class is not LifoQueue
            ):
                return False
            connection_queue = getattr(pool, "pool", None)
            if (
                type(connection_queue) is not LifoQueue
                or type(connection_queue.maxsize) is not int
                or connection_queue.maxsize != max_connections
            ):
                return False
            if not _has_standard_event_dispatcher(
                getattr(pool, "_event_dispatcher", None)
            ):
                return False

            connection_options = pool.connection_kwargs
            if not _is_supported_connection_options(connection_class, connection_options):
                return False
            _parse_numeric_host(connection_options.get("numeric_host"))
            _connection_host_and_port((), connection_options)
            if connection_class is BoundedConnection:
                if any(option.startswith("ssl_") for option in connection_options):
                    return False

            if not cls._bounded_timeout(getattr(pool, "timeout", None)):
                return False
            if not cls._bounded_timeout(connection_options.get("socket_timeout")):
                return False
            if not cls._bounded_timeout(connection_options.get("socket_connect_timeout")):
                return False

            return True
        except Exception:
            return False

    @property
    def is_lost(self) -> bool:
        """Whether this attempt has permanently lost its lease."""
        with self._lock:
            return self._lost

    def wait_lost(self, timeout: float | None = None) -> bool:
        """Wait for heartbeat or ownership failure without polling output."""
        return self._lost_event.wait(timeout)

    def acquire(self) -> bool:
        """Atomically acquire once; return ``False`` when another owner holds it."""
        with self._lock:
            if self._acquire_attempted or self._released:
                raise LeaseLostError() from None
            self._acquire_attempted = True
            if not self._has_bounded_redis_io(self._redis):
                self._mark_lost_locked()
                raise LeaseConfigurationError() from None
            try:
                result = self._redis.set(
                    self._key,
                    self._owner_token,
                    nx=True,
                    px=self._ttl_milliseconds,
                )
            except Exception:
                self._mark_lost_locked()
                raise LeaseUnavailableError() from None
            if not result:
                return False
            self._acquired = True
            return True

    def start_heartbeat(self) -> None:
        """Start one daemon heartbeat thread, or fail closed on startup error."""
        with self._lock:
            self._require_usable_locked()
            if self._heartbeat_started:
                return
            self._heartbeat_started = True
            try:
                thread = threading.Thread(
                    target=self._heartbeat_loop,
                    name="asm-download-lease-heartbeat",
                    daemon=True,
                )
                self._heartbeat_thread = thread
                thread.start()
            except Exception:
                self._mark_lost_locked()
                raise LeaseLostError() from None
            self._thread_started = True

    def assert_owned(self) -> None:
        """Synchronously verify and renew ownership before a critical action."""
        with self._lock:
            self._require_usable_locked()
        try:
            owned = self._eval_owner_script(_RENEW_SCRIPT, self._ttl_milliseconds)
        except Exception:
            self._mark_lost()
            raise LeaseLostError() from None
        if not owned:
            self._mark_lost()
            raise LeaseLostError() from None
        with self._lock:
            self._require_usable_locked()

    def release(self) -> bool:
        """Stop heartbeats and compare-delete this token once, without blind delete."""
        with self._release_condition:
            if self._release_attempted:
                if not self._release_complete:
                    self._release_waiters += 1
                    self._release_condition.notify_all()
                    try:
                        while not self._release_complete:
                            self._release_condition.wait()
                    finally:
                        self._release_waiters -= 1
                        self._release_condition.notify_all()
                return self._release_result
            self._release_attempted = True
            self._released = True
            self._stop_event.set()
            thread = self._heartbeat_thread if self._thread_started else None
            acquired = self._acquired
            had_prior_loss = self._lost

        release_succeeded = False
        try:
            if thread is not None:
                try:
                    thread.join(timeout=_HEARTBEAT_SHUTDOWN_TIMEOUT_SECONDS)
                    is_alive = getattr(thread, "is_alive", None)
                    if callable(is_alive) and is_alive():
                        self._mark_lost()
                except Exception:
                    self._mark_lost()

            if acquired:
                try:
                    deleted = self._eval_owner_script(_RELEASE_SCRIPT)
                except Exception:
                    self._mark_lost()
                else:
                    if not deleted:
                        self._mark_lost()
                    release_succeeded = deleted
        except Exception:
            self._mark_lost()
        except BaseException:
            self._mark_lost()
            raise
        finally:
            with self._release_condition:
                self._release_result = bool(
                    release_succeeded and not had_prior_loss and not self._lost
                )
                self._release_complete = True
                self._release_condition.notify_all()
        return self._release_result

    def _heartbeat_loop(self) -> None:
        while True:
            try:
                should_stop = self._stop_event.wait(self._heartbeat_seconds)
            except Exception:
                self._mark_lost()
                return
            if should_stop:
                return
            if self._stop_event.is_set():
                return
            with self._lock:
                if self._released or self._lost or not self._acquired:
                    return
            try:
                owned = self._eval_owner_script(_RENEW_SCRIPT, self._ttl_milliseconds)
            except Exception:
                self._mark_lost()
                return
            if not owned:
                with self._lock:
                    if self._released or self._lost:
                        return
                self._mark_lost()
                return

    def _eval_owner_script(self, script: str, *arguments: object) -> bool:
        if not self._has_bounded_redis_io(self._redis):
            raise LeaseConfigurationError() from None
        result = self._redis.eval(
            script,
            1,
            self._key,
            self._owner_token,
            *arguments,
        )
        return int(result) > 0

    def _require_usable_locked(self) -> None:
        if not self._acquired or self._lost or self._released:
            raise LeaseLostError() from None

    def _mark_lost(self) -> None:
        with self._lock:
            self._mark_lost_locked()

    def _mark_lost_locked(self) -> None:
        if self._lost:
            return
        self._lost = True
        self._lost_event.set()
        self._stop_event.set()
