"""Fast, no-Docker tests of the demo's state changes and dependencies."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from server import check_health


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class HealthTests(unittest.TestCase):
    def test_start_healthy(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(check_health(Path(folder) / 'fail', ''), (200, 'healthy'))

    def test_inject_and_recover(self):
        with tempfile.TemporaryDirectory() as folder:
            flag = Path(folder) / 'fail'
            flag.touch()
            self.assertEqual(check_health(flag, ''), (503, 'injected_failure'))
            flag.unlink()
            self.assertEqual(check_health(flag, ''), (200, 'healthy'))

    def test_healthy_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('server.urlopen', return_value=FakeResponse()):
                self.assertEqual(check_health(Path(folder) / 'fail', 'http://mock'), (200, 'healthy'))

    def test_failed_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('server.urlopen', side_effect=URLError('unreachable')):
                self.assertEqual(check_health(Path(folder) / 'fail', 'http://mock'), (503, 'upstream_unreachable'))

    def test_real_http_dependency_cascade_and_recovery(self):
        """A real 3-hop HTTP chain behaves as the Compose workloads should."""
        with tempfile.TemporaryDirectory() as folder:
            servers = []
            try:
                upstream = ''
                flags = []
                for service in ('db', 'api', 'web'):
                    flag = Path(folder) / service
                    flags.append(flag)

                    def handler_for(fail_flag, dependency):
                        class Handler(BaseHTTPRequestHandler):
                            def do_GET(self):
                                status, reason = check_health(fail_flag, dependency)
                                body = json.dumps({'reason': reason}).encode()
                                self.send_response(status)
                                self.send_header('Content-Length', str(len(body)))
                                self.end_headers()
                                self.wfile.write(body)

                            def log_message(self, *_args):
                                pass
                        return Handler

                    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(flag, upstream))
                    servers.append(server)
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    upstream = f'http://127.0.0.1:{server.server_port}/health'

                def status(index):
                    try:
                        with urlopen(f'http://127.0.0.1:{servers[index].server_port}/health') as reply:
                            return reply.status
                    except Exception as error:
                        from urllib.error import HTTPError
                        if isinstance(error, HTTPError):
                            return error.code
                        raise

                self.assertEqual([status(i) for i in range(3)], [200, 200, 200])
                flags[0].touch()  # fail DB, affecting API and web through HTTP
                self.assertEqual([status(i) for i in range(3)], [503, 503, 503])
                flags[0].unlink()
                self.assertEqual([status(i) for i in range(3)], [200, 200, 200])
                flags[1].touch()  # fail API only; DB remains healthy
                self.assertEqual([status(i) for i in range(3)], [200, 503, 503])
                flags[1].unlink()
                self.assertEqual([status(i) for i in range(3)], [200, 200, 200])
            finally:
                for instance in servers:
                    instance.shutdown()
                    instance.server_close()

    def test_unhealthy_dependency_response(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('server.urlopen', side_effect=HTTPError('http://mock', 503, 'unhealthy', {}, None)):
                self.assertEqual(check_health(Path(folder) / 'fail', 'http://mock'), (503, 'upstream_unhealthy'))

    def test_injected_failure_does_not_call_upstream(self):
        with tempfile.TemporaryDirectory() as folder:
            flag = Path(folder) / 'fail'
            flag.touch()
            with patch('server.urlopen') as call:
                self.assertEqual(check_health(flag, 'http://mock'), (503, 'injected_failure'))
                call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
