"""Real clients against a synthetic local Jev and OpenAI Responses turn."""

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from harness.client_config import render
import jev_server as jev
from recovery_acceptance import FixtureServer, MARKER, close_servers, response_stream
from routing_policy import SOL, route_choice


class ExternalClientTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pi"), "Pi executable unavailable")
    def test_pi_receives_routed_stream(self):
        self.assert_routed_stream("pi", shutil.which("pi"))

    @unittest.skipUnless(os.environ.get("JEV_TEST_DSH") or shutil.which("dsh"), "DSH executable unavailable")
    def test_dsh_receives_routed_stream(self):
        self.assert_routed_stream("dsh", os.environ.get("JEV_TEST_DSH") or shutil.which("dsh"))

    def assert_routed_stream(self, target, executable):
        requests = []
        decisions = []
        records = []
        recorded = threading.Event()

        def decide(_key, state, **_):
            decisions.append(state)
            return {"answers": {name: {"type": "choice", "choice": choice, "confidence": 1}
                                for name, choice in route_choice(SOL, "high").items()}}

        def record(row):
            records.append(row)
            recorded.set()

        class OpenAIFixture(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                data = response_stream().replace(b"fixture/model-b", SOL.encode())
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        with tempfile.TemporaryDirectory(prefix=f"jev-{target}-") as directory, ExitStack() as stack:
            root = Path(directory)
            for name in ("STATE", "OFF_PATH", "SHADOW_PATH", "DEBUG_PATH", "SIGNATURE_PATH", "DRY_STATE_PATH", "DRY_MANUAL_PATH"):
                stack.enter_context(mock.patch.object(jev, name, str(root / name)))
            stack.enter_context(mock.patch.dict(os.environ, {"JEV_LADDER_MODE": "native"}))
            servers = []
            for handler in (OpenAIFixture, jev.Handler):
                servers.append(stack.enter_context(FixtureServer(("127.0.0.1", 0), handler)))
            upstream, adapter = servers
            stack.enter_context(mock.patch.multiple(
                jev, ROUTER=("127.0.0.1", upstream.server_port),
                local_secret=lambda: "synthetic-local", caller_secret=lambda: "synthetic-parent",
                load_key=lambda: "synthetic-decision", call_jev_routed=decide, log_line=record,
            ))

            for server in servers:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                stack.callback(close_servers, [server], [thread])

            env = {key: value for key, value in os.environ.items() if key.upper() in
                   ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP", "TMPDIR")}
            env.update({"HOME": str(root), "USERPROFILE": str(root)})
            task = "Synthetic connectivity check. Return the fixture marker. Use no tools."
            if target == "pi":
                (root / "models.json").write_text(render(target, adapter.server_port), encoding="utf-8")
                env.update({"PI_CODING_AGENT_DIR": str(root), "JEV_LOCAL_CALLER_KEY": "synthetic-local", "PI_OFFLINE": "1"})
                command = [executable, "-p", "--provider", "jev", "--model", "auto", "--no-session", "--no-tools",
                           "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-context-files", task]
            else:
                overlay = root / "jev.patch.yml"
                overlay.write_text(
                    render(target, adapter.server_port)
                    + "- id: agent-default-model\n  config:\n    provider: jev\n    model: auto\n"
                    + "- id: session-title-llm\n  disabled: true\n",
                    encoding="utf-8",
                )
                env.update({"DSH_HOME": str(root), "JEV_LOCAL_CALLER_KEY": "synthetic-local",
                            "DO_NOT_TRACK": "1", "OTEL_SDK_DISABLED": "true"})
                command = [executable, "--profile", "headless", "--patch", str(overlay),
                           "--permission-mode", "read-only", "--approval-policy", "never", task]
            process = subprocess.run(
                command, cwd=root, env=env, capture_output=True, text=True, timeout=40, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn(MARKER, process.stdout)
            self.assertTrue(recorded.wait(2))
            self.assertEqual(len(decisions), 1)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0][1]["model"], SOL)
            self.assertEqual(requests[0][1]["reasoning"]["effort"], "high")
            self.assertEqual(records[0]["decision_source"], "jev")
