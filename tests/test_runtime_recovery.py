import io
import json
import threading
import tempfile
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import urlopen

from syncbridge.app import Runtime, handler
from syncbridge.store import Store


class RuntimeRecoveryTests(unittest.TestCase):
    def runtime(self):
        runtime = Runtime.__new__(Runtime)
        runtime.store = Mock()
        runtime.storage_backend = "sqlite"
        return runtime

    def test_health_recovers_without_exposing_provider_error(self):
        runtime = self.runtime()
        runtime.store.stats.side_effect = [OSError("private-dsn-password"), {}]
        failed = runtime.health()
        self.assertEqual(failed["status"], "degraded")
        self.assertFalse(failed["database_ready"])
        self.assertNotIn("private", json.dumps(failed))
        self.assertTrue(runtime.health()["database_ready"])

    def test_claim_failure_waits_then_delivers_after_recovery(self):
        runtime = self.runtime()
        runtime.stop = Mock()
        runtime.stop.is_set.side_effect = [False, False, True]
        runtime.store.claim.side_effect = [OSError("private-dsn-password"),
                                          {"id": 7, "payload": '{"id":7}', "attempts": 0}]
        runtime.mapper = SimpleNamespace(apply=lambda value: value)
        runtime.deliver = Mock()
        output = io.StringIO()
        with redirect_stdout(output):
            runtime.worker()
        runtime.stop.wait.assert_called_once_with(5)
        runtime.deliver.assert_called_once_with({"id": 7})
        runtime.store.finish.assert_called_once_with(7)
        self.assertNotIn("private", output.getvalue())

    def test_shutdown_during_claim_backoff_does_not_reacquire(self):
        runtime = self.runtime()
        runtime.stop = threading.Event()
        runtime.store.claim.side_effect = OSError("unavailable")
        runtime.stop.wait = Mock(side_effect=lambda timeout: runtime.stop.set())
        with redirect_stdout(io.StringIO()):
            runtime.worker()
        runtime.store.claim.assert_called_once()

    def test_outcome_write_recovery_does_not_repeat_delivery(self):
        # Exercise real durable state, injecting a lost DB acknowledgement both
        # before and after commit, for successful and failed deliveries.
        for delivery_failed in (False, True):
            for committed in (False, True):
                with self.subTest(delivery_failed=delivery_failed, committed=committed):
                    with tempfile.TemporaryDirectory() as directory:
                        runtime = self.runtime()
                        runtime.store = Store(f"{directory}/queue.db")
                        event_id, _ = runtime.store.ingest("crm", "test", {"id": 7})
                        runtime.stop = threading.Event()
                        runtime.stop.wait = Mock(return_value=False)
                        runtime.mapper = SimpleNamespace(apply=lambda value: value)
                        runtime.deliver = Mock(side_effect=RuntimeError("delivery failed") if delivery_failed else None)
                        name = "fail" if delivery_failed else "finish"
                        original = getattr(runtime.store, name)
                        calls = []

                        def flaky_write(*args):
                            calls.append(args)
                            if len(calls) == 1:
                                if committed:
                                    original(*args)
                                raise OSError("private-dsn-password")
                            original(*args)
                            runtime.stop.set()

                        setattr(runtime.store, name, flaky_write)
                        output = io.StringIO()
                        with redirect_stdout(output):
                            runtime.worker()
                        runtime.deliver.assert_called_once_with({"id": 7})
                        self.assertEqual(len(calls), 2)
                        self.assertEqual(calls[0], calls[1])
                        runtime.stop.wait.assert_called_once_with(5)
                        self.assertNotIn("private", output.getvalue())
                        row = runtime.store.list_events()[0]
                        self.assertEqual(row["id"], event_id)
                        self.assertEqual(row["attempts"], 1)
                        self.assertEqual(row["status"], "retry" if delivery_failed else "done")

    def test_shutdown_during_outcome_backoff_does_not_redeliver(self):
        runtime = self.runtime()
        runtime.stop = threading.Event()
        runtime.stop.wait = Mock(side_effect=lambda timeout: (runtime.stop.set() or True))
        runtime.store.claim.return_value = {"id": 7, "payload": {}, "attempts": 0}
        runtime.mapper = SimpleNamespace(apply=lambda value: value)
        runtime.deliver = Mock()
        runtime.store.finish.side_effect = OSError("private-dsn-password")
        with redirect_stdout(io.StringIO()):
            runtime.worker()
        runtime.deliver.assert_called_once()
        runtime.store.finish.assert_called_once_with(7)
        runtime.store.fail.assert_not_called()
        runtime.store.claim.assert_called_once()

    def test_http_health_returns_503_when_database_is_unavailable(self):
        runtime = self.runtime()
        runtime.store.stats.side_effect = OSError("private-dsn-password")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as exc:
                urlopen(f"http://127.0.0.1:{server.server_port}/health")
            with exc.exception as response:
                self.assertEqual(response.code, 503)
                self.assertFalse(json.load(response)["database_ready"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
