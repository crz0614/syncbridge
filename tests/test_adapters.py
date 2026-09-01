import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from syncbridge.adapters import send_rest


@contextmanager
def endpoint(status=200, location=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            requests.append((self.command, self.headers.get("Authorization"),
                             self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(b"{}")

        do_GET = respond
        do_POST = respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class AdapterSecurityTests(unittest.TestCase):
    def test_success_preserves_post_and_payload(self):
        with endpoint(201) as (url, requests):
            send_rest(url, "test-token", {"name": "test-fixture"})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][:2], ("POST", "Bearer test-token"))
        self.assertEqual(json.loads(requests[0][2]), {"name": "test-fixture"})

    def test_redirect_never_forwards_credentials_or_payload(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), endpoint() as (target, received):
                with endpoint(status, target + "?secret=private") as (url, sent):
                    with self.assertRaisesRegex(RuntimeError, "HTTP " + str(status)) as exc:
                        send_rest(url, "private-token", {"private": "record"})
                    self.assertNotIn("private", str(exc.exception))
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(received, [])

    def test_http_error_excludes_url_and_response_details(self):
        with endpoint(500) as (url, requests):
            with self.assertRaisesRegex(RuntimeError, "HTTP 500") as exc:
                send_rest(url + "?secret=private", "private-token", {})
            self.assertNotIn("private", str(exc.exception))
