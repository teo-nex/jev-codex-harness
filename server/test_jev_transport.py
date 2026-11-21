"""Node transport keeps TypeSafe credentials off argv and returns typed answers."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import shutil
import threading
import subprocess
import unittest
from unittest import mock

import jev_server as jev


class JevTransportTests(unittest.TestCase):
    def test_node_transport_uses_stdin_and_parses_response(self):
        response = subprocess.CompletedProcess([], 0, stdout=json.dumps({
            "model": "jev-1.13.0", "answers": {"choice": {"choice": "open"}}}), stderr="")
        with mock.patch.object(jev, "_node_binary", return_value="/opt/homebrew/bin/node"), \
             mock.patch.object(jev.subprocess, "run", return_value=response) as run:
            result = jev.call_jev("private-key", {"task": "open"}, {"choice": {"type": "choice"}})
        self.assertEqual(result["answers"]["choice"]["choice"], "open")
        args, kwargs = run.call_args
        self.assertNotIn("private-key", " ".join(args[0]))
        self.assertEqual(json.loads(kwargs["input"])["key"], "private-key")

    def test_node_provider_error_never_echoes_secret(self):
        response = subprocess.CompletedProcess([], 1, stdout="", stderr="provider HTTP 403\n")
        with mock.patch.object(jev, "_node_binary", return_value="/opt/homebrew/bin/node"), \
             mock.patch.object(jev.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "provider HTTP 403") as error:
                jev.call_jev("private-key", "state", {"choice": {"type": "choice"}})
        self.assertNotIn("private-key", str(error.exception))

    def test_openrouter_uses_decisions_contract_and_normalizes_local_identity(self):
        response = subprocess.CompletedProcess([], 0, stdout=json.dumps({
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"choice": {"type": "choice", "choice": "open", "confidence": 1}},
            "usage": {"input_tokens": 15, "output_tokens": 3}}), stderr="")
        with mock.patch.dict(jev.os.environ, {"JEV_DECISION_PROVIDER": "openrouter"}), \
             mock.patch.object(jev, "_node_binary", return_value="/fake/node"), \
             mock.patch.object(jev.subprocess, "run", return_value=response) as run:
            result = jev.call_jev_routed("private-key", {"task": "open"},
                                         {"choice": {"type": "choice"}})
        envelope = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(envelope["provider"], "openrouter")
        self.assertEqual(envelope["body"]["model"], "typesafe/jev-1.13")
        self.assertNotIn("private-key", " ".join(run.call_args.args[0]))
        self.assertEqual(result["model"], jev.MODEL)
        self.assertEqual(result["answers"]["choice"]["choice"], "open")

    def test_python_fallback_targets_only_openrouter_decisions_api(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({"model": "typesafe/jev-1.13-20260917",
            "answers": {"choice": {"type": "choice", "choice": "open"}}}).encode()
        with mock.patch.dict(jev.os.environ, {"JEV_DECISION_PROVIDER": "openrouter"}), \
             mock.patch.object(jev, "_node_binary", return_value=None), \
             mock.patch.object(jev.urllib.request, "build_opener") as build:
            build.return_value.open.return_value.__enter__.return_value = response
            jev.call_jev_routed("private-key", "synthetic", {"choice": {"type": "choice"}})
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(json.loads(request.data)["model"], "typesafe/jev-1.13")
        self.assertTrue(any(isinstance(handler, jev._NoJevRedirect) for handler in build.call_args.args))


class CompatibleGatewayTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.mode = "typed"
        test = self

        class Gateway(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.requests.append((self.path, self.headers.get("Authorization"), body))
                if test.mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "/other")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                answers = {"pick": {"type": "choice", "choice": "yes", "confidence": 1}}
                response = {"model": body["model"], "answers": answers}
                if test.mode == "chat":
                    response = {"model": body["model"], "choices": []}
                elif test.mode == "wrong-model":
                    response["model"] = "other-model"
                elif test.mode == "large":
                    response["padding"] = "x" * jev.ASK_MAX_BYTES
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        gateway = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), Gateway))
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(gateway.shutdown)
        self.env = {"JEV_DECISION_PROVIDER": "compatible",
                    "JEV_DECISION_ENDPOINT": f"http://127.0.0.1:{gateway.server_port}/custom/decide",
                    "JEV_DECISION_MODEL": "vendor/jev:alias"}
        self.questions = {"pick": {"type": "choice", "criteria": {"yes": "marker exists"}}}

    def call(self, node):
        with mock.patch.dict(jev.os.environ, self.env), mock.patch.object(jev, "_node_binary", return_value=node):
            return jev.call_jev_routed("synthetic-gateway-key", {"marker": True}, self.questions)

    def test_node_routes_to_custom_gateway(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        self.assert_gateway_contract(node)

    def test_python_routes_to_custom_gateway(self):
        self.assert_gateway_contract(None)

    def assert_gateway_contract(self, node):
        result = self.call(node)
        self.assertEqual(result["model"], jev.MODEL)
        self.assertEqual(result["answers"]["pick"]["choice"], "yes")
        self.assertEqual(self.requests, [("/custom/decide", "Bearer synthetic-gateway-key",
                                          {"model": "vendor/jev:alias", "state": {"marker": True},
                                           "questions": self.questions})])
        for mode in ("chat", "wrong-model", "large", "redirect"):
            with self.subTest(mode=mode):
                self.requests.clear()
                self.mode = mode
                with self.assertRaises((ValueError, RuntimeError)):
                    self.call(node)
                self.assertEqual([path for path, *_ in self.requests], ["/custom/decide"])

    def test_ask_uses_service_configuration_and_returns_the_stable_model(self):
        router = self.enterContext(jev.LocalServer(("127.0.0.1", 0), jev.Handler))
        thread = threading.Thread(target=router.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(router.shutdown)
        client = http.client.HTTPConnection("127.0.0.1", router.server_port, timeout=4)
        self.addCleanup(client.close)
        with mock.patch.dict(jev.os.environ, self.env), \
             mock.patch.object(jev, "local_secret", return_value="synthetic-local-key"), \
             mock.patch.object(jev, "load_key", return_value="synthetic-gateway-key"):
            client.request("POST", "/ask", json.dumps({
                "model": "untrusted-model", "endpoint": "http://127.0.0.1:1/other",
                "state": "synthetic request",
                "questions": {"pick": {**self.questions["pick"], "instructions": "Check the marker"}},
            }), {"Authorization": "Bearer synthetic-local-key", "Content-Type": "application/json"})
            response = client.getresponse()
            result = json.load(response)
        self.assertEqual(response.status, 200)
        self.assertEqual(result["model"], jev.MODEL)
        self.assertEqual(result["answers"]["pick"]["choice"], "yes")
        self.assertEqual(len(self.requests), 1)
        path, auth, body = self.requests[0]
        self.assertEqual((path, auth, body["model"]),
                         ("/custom/decide", "Bearer synthetic-gateway-key", self.env["JEV_DECISION_MODEL"]))

    def test_bad_endpoints_are_rejected_before_transport(self):
        for url in ("http://example.test/decide", "http://127.0.0.2/decide",
                    "https://user:secret@127.0.0.1/decide", "https://example.test/decide?secret=y",
                    "https://example.test/decide#fragment", "https://example.test",
                    "https://[::1]other.test/decide", "https://example.test:65536/decide"):
            with self.subTest(url=url), mock.patch.dict(jev.os.environ, {**self.env, "JEV_DECISION_ENDPOINT": url}), \
                 mock.patch.object(jev, "_node_binary") as node:
                with self.assertRaises(ValueError):
                    jev.call_jev_routed("synthetic-key", {}, self.questions)
                node.assert_not_called()


if __name__ == "__main__":
    unittest.main()
