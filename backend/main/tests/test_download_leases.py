import codecs
from collections import deque
from queue import LifoQueue
import socket
import ssl
import threading
import uuid
from unittest import TestCase
from unittest.mock import Mock, patch

from redis import Redis
from redis.backoff import NoBackoff
from redis.connection import BlockingConnectionPool, Connection, ConnectionPool, SSLConnection
from redis.event import EventDispatcher
from redis.retry import Retry

from main import download_leases
from main.download_leases import (
    AtomicDownloadLease,
    LeaseConfigurationError,
    LeaseLostError,
    LeaseUnavailableError,
)


_REAL_THREAD = threading.Thread


def bounded_pool(
    *,
    pool_type=BlockingConnectionPool,
    pool_timeout=0.1,
    socket_timeout=0.1,
    socket_connect_timeout=0.1,
    retries=0,
    connection_class=None,
    queue_class=LifoQueue,
    numeric_host="127.0.0.1",
    host="cache.example.invalid",
    port=6379,
    extra_options=None,
):
    if connection_class is None:
        connection_class = getattr(download_leases, "BoundedConnection", Connection)
    options = dict(
        connection_class=connection_class,
        numeric_host=numeric_host,
        host=host,
        port=port,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        retry=Retry(NoBackoff(), retries),
    )
    if extra_options:
        options.update(extra_options)
    if pool_type is BlockingConnectionPool:
        options["timeout"] = pool_timeout
        options["queue_class"] = queue_class
    return pool_type(**options)


class FakeRedis:
    """A deterministic Redis lease model with atomic Lua-script operations."""

    def __init__(self, *, connection_pool=None):
        self.now = 0.0
        self.entries = {}
        self.set_calls = []
        self.eval_calls = []
        self.set_error = None
        self.eval_errors = deque()
        self.connection_pool = connection_pool or bounded_pool()
        self._single_connection_client = False
        self._event_dispatcher = EventDispatcher()
        self.block_next_eval = None

    def advance(self, seconds):
        self.now += seconds

    def set(self, key, value, *, nx=False, px=None):
        self.set_calls.append((key, value, nx, px))
        if self.set_error is not None:
            raise self.set_error
        self._expire(key)
        if nx and key in self.entries:
            return None
        self.entries[key] = (value, self.now + px / 1000)
        return True

    def eval(self, script, number_of_keys, key, owner, ttl_ms=None):
        self.eval_calls.append((script, number_of_keys, key, owner, ttl_ms))
        if self.block_next_eval is not None:
            started, allow = self.block_next_eval
            self.block_next_eval = None
            started.set()
            socket_timeout = self.connection_pool.connection_kwargs["socket_timeout"]
            if not allow.wait(timeout=socket_timeout):
                raise TimeoutError("fake bounded Redis operation timed out")
        if self.eval_errors:
            error = self.eval_errors.popleft()
            if error is not None:
                raise error

        if number_of_keys != 1:
            raise AssertionError("lease scripts must use exactly one key")
        self._expire(key)
        current = self.entries.get(key)
        if current is None or current[0] != owner:
            return 0

        normalized_script = script.lower()
        if "pexpire" in normalized_script:
            self.entries[key] = (owner, self.now + ttl_ms / 1000)
            return 1
        if 'redis.call("del"' in normalized_script or "redis.call('del'" in normalized_script:
            del self.entries[key]
            return 1
        raise AssertionError("unknown lease Lua script")

    def remaining_ttl(self, key):
        self._expire(key)
        current = self.entries.get(key)
        return None if current is None else current[1] - self.now

    def owner_for(self, key):
        self._expire(key)
        current = self.entries.get(key)
        return None if current is None else current[0]

    def _expire(self, key):
        current = self.entries.get(key)
        if current is not None and current[1] <= self.now:
            del self.entries[key]


class ControlledEvent:
    """An event whose waits can model heartbeat timeouts without sleeping."""

    def __init__(self, wait_results=()):
        self.wait_results = deque(wait_results)
        self.was_set = False
        self.wait_timeouts = []

    def set(self):
        self.was_set = True

    def is_set(self):
        return self.was_set

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.was_set:
            return True
        if not self.wait_results:
            raise AssertionError("unexpected heartbeat wait")
        result = self.wait_results.popleft()
        if result:
            self.was_set = True
        return result


class ControlledThread:
    def __init__(self, *, target, name, daemon, join_started=None, allow_join=None):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        self.join_timeouts = []
        self.start_error = None
        self.join_started = join_started
        self.allow_join = allow_join
        self.alive_after_join = False

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def run_to_completion(self):
        self.target()

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)
        if self.join_started is not None:
            self.join_started.set()
        if self.allow_join is not None:
            self.allow_join.wait(timeout=timeout)

    def is_alive(self):
        return self.alive_after_join


class CustomConnection(Connection):
    pass


class HookingQueue(LifoQueue):
    get_calls = 0

    def get(self, *args, **kwargs):
        type(self).get_calls += 1
        return super().get(*args, **kwargs)


class HookingDispatcher(EventDispatcher):
    dispatch_calls = 0

    def dispatch(self, event):
        type(self).dispatch_calls += 1
        return super().dispatch(event)


class HookingBackoff(NoBackoff):
    reset_calls = 0

    def reset(self):
        type(self).reset_calls += 1
        return super().reset()


class HookingParser:
    calls = 0

    def __init__(self, **_kwargs):
        type(self).calls += 1


class HookingPacker:
    calls = 0

    def pack(self, *_args):
        type(self).calls += 1
        return []


class HookingEncoder:
    calls = 0

    def __init__(self, *_args):
        type(self).calls += 1


class FakeSocket:
    def __init__(self, *, connect_error=None):
        self.connect_error = connect_error
        self.socket_options = []
        self.timeouts = []
        self.connected_to = None
        self.closed = False
        self.shutdown_modes = []

    def setsockopt(self, level, option, value):
        self.socket_options.append((level, option, value))

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def connect(self, address):
        self.connected_to = address
        if self.connect_error is not None:
            raise self.connect_error

    def close(self):
        self.closed = True

    def shutdown(self, how):
        self.shutdown_modes.append(how)


class FakeSSLContext:
    def __init__(self, *, wrap_error=None):
        self.check_hostname = None
        self.verify_mode = None
        self.wrap_error = wrap_error
        self.wrap_calls = []
        self.wrapped_socket = object()

    def wrap_socket(self, sock, *, server_hostname):
        self.wrap_calls.append((sock, server_hostname))
        if self.wrap_error is not None:
            raise self.wrap_error
        return self.wrapped_socket


class DownloadLeaseTests(TestCase):
    KEY = "asm:steam-download:v1"

    def make_lease(self, redis_client, *, ttl_seconds=4, heartbeat_seconds=1):
        return AtomicDownloadLease(
            redis_client,
            key=self.KEY,
            ttl_seconds=ttl_seconds,
            heartbeat_seconds=heartbeat_seconds,
        )

    def make_controlled_lease(self, redis_client, wait_results=()):
        stop_event = ControlledEvent(wait_results)
        lost_event = ControlledEvent()
        with patch(
            "main.download_leases.threading.Event",
            side_effect=(stop_event, lost_event),
        ):
            lease = self.make_lease(redis_client)
        return lease, stop_event, lost_event

    def make_actual_redis_client(self, *, pool=None, client_options=None):
        client = Redis(
            connection_pool=pool or bounded_pool(),
            single_connection_client=False,
            **(client_options or {}),
        )
        client.set = Mock()
        client.eval = Mock()
        return client

    def test_acquire_is_atomic_contended_and_uses_a_fresh_owner_token(self):
        redis_client = FakeRedis()
        first = self.make_lease(redis_client)
        second = self.make_lease(redis_client)

        self.assertNotEqual(first._owner_token, second._owner_token)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())

        self.assertEqual(len(redis_client.set_calls), 2)
        for key, _owner, nx, px in redis_client.set_calls:
            self.assertEqual(key, self.KEY)
            self.assertTrue(nx)
            self.assertEqual(px, 4000)
        self.assertEqual(redis_client.owner_for(self.KEY), first._owner_token)
        self.assertEqual(redis_client.eval_calls, [])

    def test_acquire_cannot_be_retried_on_the_same_object(self):
        redis_client = FakeRedis()
        lease = self.make_lease(redis_client)
        self.assertTrue(lease.acquire())

        with self.assertRaises(LeaseLostError):
            lease.acquire()

        self.assertEqual(len(redis_client.set_calls), 1)

    def test_actual_redis_client_is_checked_using_private_single_connection_flag(self):
        pool = bounded_pool()
        with patch.object(
            BlockingConnectionPool,
            "get_connection",
            side_effect=AssertionError("connection attempted during validation"),
        ) as get_connection:
            client = Redis(connection_pool=pool, single_connection_client=False)
            with patch.object(client, "set") as set_command, patch.object(
                client,
                "eval",
            ) as eval_command:
                lease = self.make_lease(client)
                set_command.assert_not_called()
                eval_command.assert_not_called()

        self.assertFalse(client._single_connection_client)
        self.assertFalse(lease.is_lost)
        get_connection.assert_not_called()

        single_pool = bounded_pool()
        connection = Mock()
        with patch.object(
            BlockingConnectionPool,
            "get_connection",
            return_value=connection,
        ) as get_connection:
            single_client = Redis(
                connection_pool=single_pool,
                single_connection_client=True,
            )

        self.assertTrue(single_client._single_connection_client)
        get_connection.assert_called_once_with()
        connection.send_command.assert_not_called()
        with (
            patch.object(single_client, "set") as set_command,
            patch.object(single_client, "eval") as eval_command,
        ):
            with self.assertRaises(LeaseConfigurationError):
                self.make_lease(single_client)
            set_command.assert_not_called()
            eval_command.assert_not_called()
        connection.send_command.assert_not_called()

    def test_actual_redis_defaults_allow_supported_auth_and_tls_primitives(self):
        pool = bounded_pool(
            connection_class=download_leases.BoundedSSLConnection,
            extra_options={
                "db": 2,
                "username": "lease-user",
                "password": "lease-secret",
                "ssl_cert_reqs": "required",
                "ssl_check_hostname": True,
                "ssl_ca_certs": "ca.pem",
                "ssl_certfile": "client.pem",
                "ssl_keyfile": "client.key",
            },
        )
        client = self.make_actual_redis_client(pool=pool)

        lease = self.make_lease(client)

        self.assertFalse(lease.is_lost)
        client.set.assert_not_called()
        client.eval.assert_not_called()

    def test_actual_redis_public_extension_hooks_are_rejected_before_commands(self):
        HookingQueue.get_calls = 0
        HookingDispatcher.dispatch_calls = 0
        HookingBackoff.reset_calls = 0
        HookingParser.calls = 0
        HookingPacker.calls = 0
        HookingEncoder.calls = 0

        custom_queue_class = self.make_actual_redis_client(
            pool=bounded_pool(queue_class=HookingQueue)
        )
        substituted_queue = self.make_actual_redis_client()
        substituted_queue.connection_pool.pool = HookingQueue(
            substituted_queue.connection_pool.max_connections
        )
        custom_parser = self.make_actual_redis_client(
            pool=bounded_pool(extra_options={"parser_class": HookingParser})
        )
        custom_packer = self.make_actual_redis_client(
            pool=bounded_pool(extra_options={"command_packer": HookingPacker()})
        )
        custom_pool_dispatcher = self.make_actual_redis_client()
        pool_dispatcher = HookingDispatcher()
        custom_pool_dispatcher.connection_pool._event_dispatcher = pool_dispatcher
        custom_pool_dispatcher.connection_pool.connection_kwargs[
            "event_dispatcher"
        ] = pool_dispatcher
        custom_client_dispatcher = self.make_actual_redis_client()
        custom_client_dispatcher._event_dispatcher = HookingDispatcher()
        custom_encoder = self.make_actual_redis_client(
            pool=bounded_pool(extra_options={"encoder_class": HookingEncoder})
        )
        custom_backoff = self.make_actual_redis_client(
            pool=bounded_pool(extra_options={"retry": Retry(HookingBackoff(), 0)})
        )

        unsupported_clients = (
            custom_queue_class,
            substituted_queue,
            custom_parser,
            custom_packer,
            custom_pool_dispatcher,
            custom_client_dispatcher,
            custom_encoder,
            custom_backoff,
        )
        for client in unsupported_clients:
            with self.subTest(pool=type(client.connection_pool).__name__):
                with self.assertRaises(LeaseConfigurationError):
                    self.make_lease(client)
                client.set.assert_not_called()
                client.eval.assert_not_called()

        self.assertEqual(HookingQueue.get_calls, 0)
        self.assertEqual(HookingDispatcher.dispatch_calls, 0)
        self.assertEqual(HookingBackoff.reset_calls, 0)
        self.assertEqual(HookingParser.calls, 0)
        self.assertEqual(HookingPacker.calls, 0)
        self.assertEqual(HookingEncoder.calls, 0)

    def test_actual_redis_custom_codec_names_are_rejected_before_commands(self):
        codec_name = f"asm_task7c1_codec_{uuid.uuid4().hex}"
        error_handler_name = f"asm_task7c1_errors_{uuid.uuid4().hex}"
        callbacks = {"codec": 0, "error_handler": 0}

        def encode(_value, _errors="strict"):
            callbacks["codec"] += 1
            raise AssertionError("custom codec encoder must not run")

        def decode(_value, _errors="strict"):
            callbacks["codec"] += 1
            raise AssertionError("custom codec decoder must not run")

        codec_info = codecs.CodecInfo(
            name=codec_name,
            encode=encode,
            decode=decode,
            incrementalencoder=None,
            incrementaldecoder=None,
            streamreader=None,
            streamwriter=None,
        )

        def codec_search(name):
            callbacks["codec"] += 1
            if name.replace("-", "_").lower() == codec_name:
                return codec_info
            return None

        def custom_error_handler(_error):
            callbacks["error_handler"] += 1
            raise AssertionError("custom codec error handler must not run")

        codecs.register(codec_search)
        try:
            codecs.register_error(error_handler_name, custom_error_handler)
            try:
                unsupported_clients = (
                    (
                        "encoding",
                        self.make_actual_redis_client(
                            pool=bounded_pool(extra_options={"encoding": codec_name})
                        ),
                    ),
                    (
                        "encoding_errors",
                        self.make_actual_redis_client(
                            pool=bounded_pool(
                                extra_options={"encoding_errors": error_handler_name}
                            )
                        ),
                    ),
                )
                for option, client in unsupported_clients:
                    with self.subTest(option=option):
                        with self.assertRaises(LeaseConfigurationError):
                            self.make_lease(client)
                        client.set.assert_not_called()
                        client.eval.assert_not_called()

                supported_client = self.make_actual_redis_client(
                    pool=bounded_pool(
                        extra_options={
                            "encoding": "utf-8",
                            "encoding_errors": "strict",
                        }
                    )
                )
                self.make_lease(supported_client)
                supported_client.set.assert_not_called()
                supported_client.eval.assert_not_called()
                self.assertEqual(callbacks, {"codec": 0, "error_handler": 0})
            finally:
                # codecs has no public unregister_error; neutralize this unique name.
                codecs.register_error(error_handler_name, codecs.strict_errors)
        finally:
            codecs.unregister(codec_search)

    def test_actual_redis_fails_closed_if_strict_error_handler_is_replaced(self):
        callback_calls = 0

        def replacement_error_handler(_error):
            nonlocal callback_calls
            callback_calls += 1
            raise AssertionError("replacement strict error handler must not run")

        standard_strict_handler = codecs.strict_errors
        self.assertIs(codecs.lookup_error("strict"), standard_strict_handler)
        codecs.register_error("strict", replacement_error_handler)
        try:
            client = self.make_actual_redis_client(
                pool=bounded_pool(
                    extra_options={
                        "encoding": "utf-8",
                        "encoding_errors": "strict",
                    }
                )
            )
            with self.assertRaises(LeaseConfigurationError):
                self.make_lease(client)
            client.set.assert_not_called()
            client.eval.assert_not_called()
            self.assertEqual(callback_calls, 0)
        finally:
            codecs.register_error("strict", standard_strict_handler)

        self.assertIs(codecs.lookup_error("strict"), standard_strict_handler)

    def test_client_contract_is_rechecked_before_each_redis_command(self):
        redis_client = FakeRedis()
        lease = self.make_lease(redis_client)
        redis_client._single_connection_client = True

        with self.assertRaises(LeaseConfigurationError):
            lease.acquire()

        self.assertEqual(redis_client.set_calls, [])
        self.assertEqual(redis_client.eval_calls, [])

        redis_client = FakeRedis()
        lease = self.make_lease(redis_client)
        self.assertTrue(lease.acquire())
        redis_client.connection_pool.connection_kwargs["numeric_host"] = "cache.example.invalid"

        with self.assertRaises(LeaseLostError):
            lease.assert_owned()

        self.assertTrue(lease.is_lost)
        self.assertEqual(redis_client.eval_calls, [])

    def test_lease_transport_rejects_standard_dns_resolving_connection(self):
        redis_client = FakeRedis(
            connection_pool=bounded_pool(connection_class=Connection)
        )

        with self.assertRaises(LeaseConfigurationError):
            self.make_lease(redis_client)

        self.assertEqual(redis_client.set_calls, [])
        self.assertEqual(redis_client.eval_calls, [])

    def test_bounded_tcp_connect_and_reconnect_use_numeric_ipv4_and_ipv6(self):
        connection_class = getattr(download_leases, "BoundedConnection", None)
        self.assertIsNotNone(connection_class)

        cases = (
            ("192.0.2.17", socket.AF_INET, ("192.0.2.17", 6379)),
            ("2001:db8::17", socket.AF_INET6, ("2001:db8::17", 6379, 0, 0)),
        )
        for numeric_host, family, target in cases:
            with self.subTest(numeric_host=numeric_host):
                first_socket = FakeSocket()
                second_socket = FakeSocket()
                connection = connection_class(
                    host="cache.example.invalid",
                    port=6379,
                    numeric_host=numeric_host,
                    socket_timeout=0.25,
                    socket_connect_timeout=0.5,
                    socket_keepalive=True,
                    retry=Retry(NoBackoff(), 0),
                )
                with (
                    patch(
                        "main.download_leases.socket.socket",
                        side_effect=(first_socket, second_socket),
                    ) as socket_factory,
                    patch(
                        "main.download_leases.socket.getaddrinfo",
                        side_effect=AssertionError("leased connection attempted DNS"),
                    ) as resolver,
                    patch.object(connection, "on_connect_check_health"),
                ):
                    connection.connect_check_health(check_health=False)
                    self.assertIs(connection._sock, first_socket)
                    connection.disconnect()
                    connection.connect_check_health(check_health=False)
                    self.assertIs(connection._sock, second_socket)

                self.assertEqual(socket_factory.call_count, 2)
                self.assertEqual(
                    socket_factory.call_args.args,
                    (family, socket.SOCK_STREAM, socket.IPPROTO_TCP),
                )
                self.assertEqual(first_socket.connected_to, target)
                self.assertEqual(second_socket.connected_to, target)
                self.assertTrue(first_socket.closed)
                self.assertEqual(first_socket.timeouts, [0.5, 0.25])
                self.assertIn(
                    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
                    first_socket.socket_options,
                )
                self.assertIn(
                    (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
                    first_socket.socket_options,
                )
                resolver.assert_not_called()

    def test_invalid_or_scoped_numeric_addresses_are_rejected_before_socket_creation(self):
        connection_class = getattr(download_leases, "BoundedConnection", None)
        self.assertIsNotNone(connection_class)

        with patch("main.download_leases.socket.socket") as socket_factory:
            for numeric_host in (
                "cache.example.invalid",
                "fe80::1%eth0",
                "[2001:db8::17]",
            ):
                with self.subTest(numeric_host=numeric_host):
                    with self.assertRaises(ValueError):
                        connection_class(
                            host="cache.example.invalid",
                            port=6379,
                            numeric_host=numeric_host,
                            socket_timeout=0.25,
                            socket_connect_timeout=0.5,
                        )
            for port in (0, 65536, True, "redis-port"):
                with self.subTest(port=port):
                    with self.assertRaises(ValueError):
                        connection_class(
                            host="cache.example.invalid",
                            port=port,
                            numeric_host="192.0.2.17",
                            socket_timeout=0.25,
                            socket_connect_timeout=0.5,
                        )
            for socket_timeout, socket_connect_timeout in (
                (None, 0.5),
                (0.25, 1.01),
            ):
                with self.subTest(
                    socket_timeout=socket_timeout,
                    socket_connect_timeout=socket_connect_timeout,
                ):
                    with self.assertRaises(ValueError):
                        connection_class(
                            host="cache.example.invalid",
                            port=6379,
                            numeric_host="192.0.2.17",
                            socket_timeout=socket_timeout,
                            socket_connect_timeout=socket_connect_timeout,
                        )
            socket_factory.assert_not_called()

        redis_client = FakeRedis(
            connection_pool=bounded_pool(numeric_host="cache.example.invalid")
        )
        with self.assertRaises(LeaseConfigurationError):
            self.make_lease(redis_client)
        self.assertEqual(redis_client.set_calls, [])
        self.assertEqual(redis_client.eval_calls, [])

    def test_numeric_socket_is_closed_when_connect_fails(self):
        connection_class = getattr(download_leases, "BoundedConnection", None)
        self.assertIsNotNone(connection_class)
        socket_instance = FakeSocket(connect_error=OSError("private socket error"))
        connection = connection_class(
            host="cache.example.invalid",
            port=6379,
            numeric_host="192.0.2.18",
            socket_timeout=0.25,
            socket_connect_timeout=0.5,
        )

        with (
            patch("main.download_leases.socket.socket", return_value=socket_instance),
            patch(
                "main.download_leases.socket.getaddrinfo",
                side_effect=AssertionError("leased connection attempted DNS"),
            ) as resolver,
        ):
            with self.assertRaises(OSError):
                connection._connect()

        self.assertTrue(socket_instance.closed)
        resolver.assert_not_called()

    def test_bounded_tls_wraps_numeric_socket_with_original_sni_and_verification(self):
        connection_class = getattr(download_leases, "BoundedSSLConnection", None)
        self.assertIsNotNone(connection_class)
        raw_socket = FakeSocket()
        context = FakeSSLContext()
        connection = connection_class(
            host="cache.example.invalid",
            port=6379,
            numeric_host="192.0.2.19",
            socket_timeout=0.25,
            socket_connect_timeout=0.5,
            ssl_cert_reqs="required",
            ssl_check_hostname=True,
        )

        with (
            patch("main.download_leases.socket.socket", return_value=raw_socket),
            patch(
                "main.download_leases.socket.getaddrinfo",
                side_effect=AssertionError("leased TLS connection attempted DNS"),
            ) as resolver,
            patch(
                "redis.connection.ssl.create_default_context",
                return_value=context,
            ),
        ):
            result = connection._connect()

        self.assertIs(result, context.wrapped_socket)
        self.assertEqual(raw_socket.connected_to, ("192.0.2.19", 6379))
        self.assertEqual(context.wrap_calls, [(raw_socket, "cache.example.invalid")])
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(raw_socket.closed)
        resolver.assert_not_called()

    def test_tls_socket_is_closed_when_ssl_wrapping_fails(self):
        connection_class = getattr(download_leases, "BoundedSSLConnection", None)
        self.assertIsNotNone(connection_class)
        raw_socket = FakeSocket()
        context = FakeSSLContext(wrap_error=ssl.SSLError("private TLS error"))
        connection = connection_class(
            host="cache.example.invalid",
            port=6379,
            numeric_host="192.0.2.20",
            socket_timeout=0.25,
            socket_connect_timeout=0.5,
            ssl_cert_reqs="required",
            ssl_check_hostname=True,
        )

        with (
            patch("main.download_leases.socket.socket", return_value=raw_socket),
            patch("redis.connection.ssl.create_default_context", return_value=context),
        ):
            with self.assertRaises(ssl.SSLError):
                connection._connect()

        self.assertTrue(raw_socket.closed)

    def test_tls_configuration_rejects_downgrade_and_unbounded_ocsp(self):
        connection_class = getattr(download_leases, "BoundedSSLConnection", None)
        self.assertIsNotNone(connection_class)

        unsafe_tls_options = (
            {"ssl_cert_reqs": "none", "ssl_check_hostname": True},
            {"ssl_cert_reqs": "required", "ssl_check_hostname": False},
            {
                "ssl_cert_reqs": "required",
                "ssl_check_hostname": True,
                "ssl_validate_ocsp": True,
            },
            {
                "ssl_cert_reqs": "required",
                "ssl_check_hostname": True,
                "ssl_validate_ocsp_stapled": True,
            },
        )
        for tls_options in unsafe_tls_options:
            with self.subTest(tls_options=tls_options):
                redis_client = FakeRedis(
                    connection_pool=bounded_pool(
                        connection_class=connection_class,
                        extra_options=tls_options,
                    )
                )
                with self.assertRaises(LeaseConfigurationError):
                    self.make_lease(redis_client)
                self.assertEqual(redis_client.set_calls, [])
                self.assertEqual(redis_client.eval_calls, [])
                with self.assertRaises(ValueError):
                    connection_class(
                        host="cache.example.invalid",
                        port=6379,
                        numeric_host="192.0.2.21",
                        socket_timeout=0.1,
                        socket_connect_timeout=0.1,
                        **tls_options,
                    )

    def test_expired_owner_cannot_renew_or_delete_a_successor_lease(self):
        redis_client = FakeRedis()
        former_owner = self.make_lease(redis_client)
        successor = self.make_lease(redis_client)
        self.assertTrue(former_owner.acquire())

        redis_client.advance(4)
        self.assertTrue(successor.acquire())
        successor_ttl = redis_client.remaining_ttl(self.KEY)

        with self.assertRaises(LeaseLostError):
            former_owner.assert_owned()
        self.assertFalse(former_owner.release())

        self.assertEqual(redis_client.owner_for(self.KEY), successor._owner_token)
        self.assertEqual(redis_client.remaining_ttl(self.KEY), successor_ttl)

    def test_assert_owned_synchronously_renews_before_returning(self):
        redis_client = FakeRedis()
        lease = self.make_lease(redis_client)
        self.assertTrue(lease.acquire())
        redis_client.advance(3)

        lease.assert_owned()

        self.assertEqual(redis_client.remaining_ttl(self.KEY), 4)
        self.assertEqual(len(redis_client.eval_calls), 1)

    def test_heartbeat_renews_on_controlled_timeout_and_is_shutdown_on_release(self):
        redis_client = FakeRedis()
        lease, stop_event, lost_event = self.make_controlled_lease(
            redis_client,
            wait_results=(False, True),
        )
        self.assertTrue(lease.acquire())
        redis_client.advance(3)

        thread = ControlledThread(target=lambda: None, name="unused", daemon=True)
        with (
            patch("main.download_leases.threading.Thread", return_value=thread) as thread_factory,
        ):
            lease.start_heartbeat()
            thread.target = thread_factory.call_args.kwargs["target"]
            thread.run_to_completion()

        self.assertTrue(thread.started)
        self.assertTrue(thread.daemon)
        self.assertEqual(stop_event.wait_timeouts, [1, 1])
        self.assertEqual(redis_client.remaining_ttl(self.KEY), 4)
        self.assertFalse(lease.is_lost)

        self.assertTrue(lease.release())
        self.assertTrue(stop_event.is_set())
        self.assertEqual(len(thread.join_timeouts), 1)
        self.assertGreater(thread.join_timeouts[0], 0)
        self.assertIsNone(redis_client.owner_for(self.KEY))

    def test_heartbeat_failure_marks_loss_and_cannot_resurrect_attempt(self):
        redis_client = FakeRedis()
        lease, stop_event, lost_event = self.make_controlled_lease(
            redis_client,
            wait_results=(False,),
        )
        self.assertTrue(lease.acquire())
        redis_client.eval_errors.append(RuntimeError("private redis endpoint"))

        thread = ControlledThread(target=lambda: None, name="unused", daemon=True)
        with (
            patch("main.download_leases.threading.Thread", return_value=thread) as thread_factory,
        ):
            lease.start_heartbeat()
            thread.target = thread_factory.call_args.kwargs["target"]
            thread.run_to_completion()

        self.assertTrue(lease.is_lost)
        self.assertTrue(lease.wait_lost(timeout=0))
        eval_count_after_loss = len(redis_client.eval_calls)
        with self.assertRaises(LeaseLostError) as raised:
            lease.assert_owned()
        self.assertNotIn("private redis endpoint", str(raised.exception))
        self.assertEqual(len(redis_client.eval_calls), eval_count_after_loss)
        with self.assertRaises(LeaseLostError):
            lease.start_heartbeat()
        self.assertFalse(lease.release())
        self.assertIsNone(redis_client.owner_for(self.KEY))

    def test_bounded_client_timeout_stops_a_blocked_heartbeat_during_release(self):
        redis_client = FakeRedis()
        lease = self.make_lease(redis_client, ttl_seconds=1, heartbeat_seconds=0.01)
        self.assertTrue(lease.acquire())

        operation_started = threading.Event()
        allow_operation = threading.Event()
        redis_client.block_next_eval = (operation_started, allow_operation)
        lease.start_heartbeat()
        self.assertTrue(operation_started.wait(timeout=1))

        self.assertFalse(lease.release())

        self.assertTrue(lease.is_lost)
        self.assertTrue(lease.wait_lost(timeout=0))
        self.assertFalse(lease._heartbeat_thread.is_alive())
        self.assertIsNone(redis_client.owner_for(self.KEY))

    def test_heartbeat_detects_expiry_and_successor_without_touching_successor(self):
        redis_client = FakeRedis()
        former_owner, stop_event, lost_event = self.make_controlled_lease(
            redis_client,
            wait_results=(False,),
        )
        successor = self.make_lease(redis_client)
        self.assertTrue(former_owner.acquire())

        thread = ControlledThread(target=lambda: None, name="unused", daemon=True)
        with (
            patch("main.download_leases.threading.Thread", return_value=thread) as thread_factory,
        ):
            former_owner.start_heartbeat()
            thread.target = thread_factory.call_args.kwargs["target"]
            redis_client.advance(4)
            self.assertTrue(successor.acquire())
            thread.run_to_completion()

        self.assertTrue(former_owner.is_lost)
        self.assertEqual(redis_client.owner_for(self.KEY), successor._owner_token)
        with self.assertRaises(LeaseLostError):
            former_owner.assert_owned()

    def test_thread_start_failure_is_reported_and_marks_lease_lost(self):
        redis_client = FakeRedis()
        lease, stop_event, lost_event = self.make_controlled_lease(redis_client)
        self.assertTrue(lease.acquire())

        thread = ControlledThread(target=lambda: None, name="unused", daemon=True)
        thread.start_error = RuntimeError("thread internals")
        with (
            patch("main.download_leases.threading.Thread", return_value=thread),
        ):
            with self.assertRaises(LeaseLostError) as raised:
                lease.start_heartbeat()

        self.assertTrue(lease.is_lost)
        self.assertTrue(lease.wait_lost(timeout=0))
        self.assertNotIn("thread internals", str(raised.exception))

    def test_thread_construction_failure_is_reported_and_marks_lease_lost(self):
        redis_client = FakeRedis()
        lease, _stop_event, _lost_event = self.make_controlled_lease(redis_client)
        self.assertTrue(lease.acquire())

        with patch(
            "main.download_leases.threading.Thread",
            side_effect=RuntimeError("private thread internals"),
        ):
            with self.assertRaises(LeaseLostError) as raised:
                lease.start_heartbeat()

        self.assertTrue(lease.is_lost)
        self.assertTrue(lease.wait_lost(timeout=0))
        self.assertNotIn("private thread internals", str(raised.exception))

    def test_uncertain_heartbeat_shutdown_signals_loss_after_compare_delete(self):
        redis_client = FakeRedis()
        lease, _stop_event, _lost_event = self.make_controlled_lease(redis_client)
        self.assertTrue(lease.acquire())

        thread = ControlledThread(target=lambda: None, name="unused", daemon=True)
        thread.alive_after_join = True
        with patch("main.download_leases.threading.Thread", return_value=thread):
            lease.start_heartbeat()

        self.assertFalse(lease.release())
        self.assertTrue(lease.is_lost)
        self.assertTrue(lease.wait_lost(timeout=0))
        self.assertIsNone(redis_client.owner_for(self.KEY))

    def test_release_is_idempotent_and_assert_owned_rejects_released_lease(self):
        redis_client = FakeRedis()
        lease = self.make_lease(redis_client)
        self.assertTrue(lease.acquire())

        self.assertTrue(lease.release())
        first_release_eval_count = len(redis_client.eval_calls)
        self.assertTrue(lease.release())
        self.assertEqual(len(redis_client.eval_calls), first_release_eval_count)
        self.assertIsNone(redis_client.owner_for(self.KEY))
        with self.assertRaises(LeaseLostError):
            lease.assert_owned()

    def test_release_returns_false_after_ownership_loss(self):
        redis_client = FakeRedis()
        former_owner = self.make_lease(redis_client)
        successor = self.make_lease(redis_client)
        self.assertTrue(former_owner.acquire())
        redis_client.advance(4)
        self.assertTrue(successor.acquire())

        self.assertFalse(former_owner.release())
        self.assertFalse(former_owner.release())
        self.assertEqual(redis_client.owner_for(self.KEY), successor._owner_token)

    def test_successful_cleanup_still_reports_prior_lease_loss(self):
        redis_client = FakeRedis()
        lease = self.make_lease(redis_client)
        self.assertTrue(lease.acquire())
        redis_client.eval_errors.append(RuntimeError("private redis endpoint"))

        with self.assertRaises(LeaseLostError):
            lease.assert_owned()
        self.assertTrue(lease.is_lost)
        self.assertEqual(redis_client.owner_for(self.KEY), lease._owner_token)

        self.assertFalse(lease.release())

        self.assertIsNone(redis_client.owner_for(self.KEY))
        self.assertFalse(lease.release())

    def test_concurrent_release_calls_share_the_completed_result(self):
        redis_client = FakeRedis()
        lease, _stop_event, _lost_event = self.make_controlled_lease(redis_client)
        self.assertTrue(lease.acquire())

        join_started = threading.Event()
        allow_join = threading.Event()
        lease_thread = ControlledThread(
            target=lambda: None,
            name="unused",
            daemon=True,
            join_started=join_started,
            allow_join=allow_join,
        )
        with patch("main.download_leases.threading.Thread", return_value=lease_thread):
            lease.start_heartbeat()

        results = {}
        first_done = threading.Event()
        second_done = threading.Event()

        def first_release():
            results["first"] = lease.release()
            first_done.set()

        def second_release():
            results["second"] = lease.release()
            second_done.set()

        first = _REAL_THREAD(target=first_release)
        first.start()
        self.assertTrue(join_started.wait(timeout=1))

        second = _REAL_THREAD(target=second_release)
        second.start()
        with lease._release_condition:
            self.assertTrue(
                lease._release_condition.wait_for(
                    lambda: lease._release_waiters == 1,
                    timeout=1,
                )
            )
        self.assertFalse(second_done.is_set())

        allow_join.set()
        first.join(timeout=1)
        second.join(timeout=1)

        self.assertTrue(first_done.is_set())
        self.assertTrue(second_done.is_set())
        self.assertEqual(results, {"first": True, "second": True})
        self.assertIsNone(redis_client.owner_for(self.KEY))

    def test_rejects_unbounded_or_retrying_clients_before_redis_work(self):
        unsupported_clients = (
            FakeRedis(connection_pool=bounded_pool(pool_type=ConnectionPool)),
            FakeRedis(connection_pool=bounded_pool(connection_class=Connection)),
            FakeRedis(connection_pool=bounded_pool(connection_class=SSLConnection)),
            FakeRedis(connection_pool=bounded_pool(pool_timeout=None)),
            FakeRedis(connection_pool=bounded_pool(pool_timeout=1.01)),
            FakeRedis(connection_pool=bounded_pool(port=0)),
            FakeRedis(connection_pool=bounded_pool(socket_timeout=None)),
            FakeRedis(connection_pool=bounded_pool(socket_timeout=1.01)),
            FakeRedis(connection_pool=bounded_pool(socket_connect_timeout=1.01)),
            FakeRedis(connection_pool=bounded_pool(socket_connect_timeout=float("inf"))),
            FakeRedis(connection_pool=bounded_pool(retries=-1)),
            FakeRedis(connection_pool=bounded_pool(retries=1)),
            FakeRedis(connection_pool=bounded_pool(connection_class=CustomConnection)),
            FakeRedis(
                connection_pool=bounded_pool(
                    extra_options={"credential_provider": object()}
                )
            ),
            FakeRedis(
                connection_pool=bounded_pool(
                    extra_options={"redis_connect_func": lambda _connection: None}
                )
            ),
            FakeRedis(connection_pool=bounded_pool(extra_options={"ssl": True})),
        )
        single_connection_client = FakeRedis()
        single_connection_client._single_connection_client = True
        unsupported_clients += (single_connection_client,)
        for redis_client in unsupported_clients:
            with self.subTest(pool=type(redis_client.connection_pool).__name__):
                with self.assertRaises(LeaseConfigurationError) as raised:
                    self.make_lease(redis_client)
                self.assertEqual(raised.exception.code, "lease_client_unsupported")
                self.assertEqual(
                    raised.exception.to_dict(),
                    {
                        "error": "lease_client_unsupported",
                        "message": "Download lease client configuration is unsupported.",
                    },
                )
                self.assertEqual(redis_client.set_calls, [])
                self.assertEqual(redis_client.eval_calls, [])

    def test_redis_failures_are_safe_structured_errors_or_fail_closed_results(self):
        redis_client = FakeRedis()
        redis_client.set_error = RuntimeError("private redis endpoint")
        lease = self.make_lease(redis_client)

        with self.assertRaises(LeaseUnavailableError) as raised:
            lease.acquire()

        self.assertEqual(raised.exception.code, "lease_unavailable")
        self.assertEqual(
            raised.exception.to_dict(),
            {"error": "lease_unavailable", "message": "Download lease service unavailable."},
        )
        self.assertNotIn("private redis endpoint", str(raised.exception))

        redis_client = FakeRedis()
        lease = self.make_lease(redis_client)
        self.assertTrue(lease.acquire())
        redis_client.eval_errors.append(RuntimeError("private redis endpoint"))
        with self.assertRaises(LeaseLostError) as raised:
            lease.assert_owned()
        self.assertNotIn("private redis endpoint", str(raised.exception))
        redis_client.eval_errors.append(RuntimeError("private redis endpoint"))
        self.assertFalse(lease.release())

    def test_constructor_validates_ttl_and_heartbeat_before_redis_work(self):
        redis_client = FakeRedis()
        for ttl_seconds, heartbeat_seconds in (
            (0, 0.1),
            (1, 1),
            (2, 3),
            (float("inf"), 1),
            (1e308, 1),
            (True, 0.1),
            (threading.TIMEOUT_MAX + 2, threading.TIMEOUT_MAX + 1),
        ):
            with self.subTest(ttl_seconds=ttl_seconds, heartbeat_seconds=heartbeat_seconds):
                with self.assertRaises(ValueError):
                    AtomicDownloadLease(
                        redis_client,
                        ttl_seconds=ttl_seconds,
                        heartbeat_seconds=heartbeat_seconds,
                    )

        self.assertEqual(redis_client.set_calls, [])
        self.assertEqual(redis_client.eval_calls, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
