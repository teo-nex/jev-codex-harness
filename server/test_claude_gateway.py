import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import ExitStack

from claude_gateway import AUTO, COUNT_TOKENS, Gateway, MESSAGES, MODELS, shared_decider
from local_runtime import LocalServer
from routing_policy import ASTRA, LUNA, SOL, TERRA, route_choice

MARKER = "CLAUDE_GATEWAY_FIXTURE"
UPSTREAM_KEY = "synthetic-upstream"
CALLER_KEY = "synthetic-gateway"
JUDGE_KEY = "synthetic-judge"
TOOL = {"type": "tool_use", "id": "toolu_gateway_fixture", "name": "fixture_tool", "input": {}}


def replies(body):
    tools = {tool.get("name") for tool in body.get("tools", [])}
    continuation = any(isinstance(msg["content"], list) and
                       any(part.get("type") == "tool_result" for part in msg["content"])
                       for msg in body["messages"])
    block = {"type": "text", "text": MARKER}
    if not continuation:
        if "fixture_tool" in tools:
            block = TOOL
        elif "Bash" in tools:
            block = {**TOOL, "name": "Bash", "input": {"command": "printf JEV_CLAUDE_TOOL_OK"}}
    tool_use = block["type"] == "tool_use"
    stop = "tool_use" if tool_use else "end_turn"
    if len(body["messages"]) == 1 and body["messages"][0]["content"] == "Hard at output limit":
        stop = "max_tokens"
    message = {
        "id": "msg_gateway_fixture", "type": "message", "role": "assistant", "model": body["model"],
        "content": [block], "stop_reason": stop,
        "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 8,
                                        "cache_read_input_tokens": 4},
    }
    if not body.get("stream"):
        return "application/json", json.dumps(message).encode()
    start = {**message, "content": [], "stop_reason": None}
    empty = {**block, **({"input": {}} if tool_use else {"text": ""})}
    events = [
        ("message_start", {"message": start}),
        ("content_block_start", {"index": 0, "content_block": empty}),
        ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta",
                                                        "partial_json": json.dumps(block["input"])}
                                 if tool_use else {"type": "text_delta", "text": MARKER}}),
        ("content_block_stop", {"index": 0}),
        ("message_delta", {"delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                           "usage": {"output_tokens": 8}}),
        ("message_stop", {}),
    ]
    return "text/event-stream", b"".join(
        f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n".encode() for name, data in events)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.requests, self.dossiers = [], []
        self.refuse = False
        owner = self

        class Fixture(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/ask":
                    owner.dossiers.append((dict(self.headers), body))
                    if owner.refuse:
                        self.send_error(503)
                        return
                    task = body["state"]["task"]
                    model, effort = {"Tiny": (LUNA, "low"), "Routine": (TERRA, "medium"),
                                     "Hard": (SOL, "high"), "Hard at output limit": (SOL, "high")}.get(
                                         task, (TERRA, "medium"))
                    answers = {name: {"type": "choice", "choice": choice, "confidence": 1}
                               for name, choice in route_choice(model, effort).items()}
                    mime, data = "application/json", json.dumps({"answers": answers}).encode()
                else:
                    owner.requests.append((self.path, dict(self.headers), body))
                    if self.path.startswith(COUNT_TOKENS):
                        mime, data = "application/json", b'{"input_tokens": 10}'
                    else:
                        mime, data = replies(body)
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        fixture = stack.enter_context(LocalServer(("127.0.0.1", 0), Fixture))
        self.gateway = stack.enter_context(Gateway(
            0, CALLER_KEY, UPSTREAM_KEY, shared_decider(fixture.server_port, JUDGE_KEY),
            connect=lambda: http.client.HTTPConnection("127.0.0.1", fixture.server_port, timeout=5)))
        for server in (fixture, self.gateway):
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
            thread.start()
            stack.callback(thread.join, 2)
            stack.callback(server.shutdown)

    def request(self, path, body=None, headers=None):
        headers = {"x-api-key": CALLER_KEY} if headers is None else headers
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        try:
            connection.request("GET" if body is None else "POST", path,
                               None if body is None else json.dumps(body),
                               {"Content-Type": "application/json", **headers})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_rejects_unapproved_callers_and_does_not_use_caller_credentials_upstream(self):
        self.assertEqual(self.request("/v1/models", headers={})[0], 401)
        self.assertEqual(self.request("/v1/models", headers={"Origin": "https://example.test",
                                                             "x-api-key": CALLER_KEY})[0], 403)
        self.assertEqual(self.request("/v1/models", headers={"Authorization": "Bearer " + CALLER_KEY})[0], 200)
        body = {"model": AUTO, "messages": [{"role": "user", "content": "Tiny"}]}
        self.assertEqual(self.request(MESSAGES, body, headers={"x-api-key": UPSTREAM_KEY})[0], 401)
        self.assertEqual(self.requests, [])

    def test_all_tiers_and_native_payload(self):
        for task, tier, effort in (("Tiny", LUNA, "low"), ("Routine", TERRA, "medium"), ("Hard", SOL, "high")):
            with self.subTest(tier=tier):
                body = {
                    "model": AUTO, "max_tokens": 256, "stream": True, "thinking": {"type": "adaptive"},
                    "system": [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}],
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": task},
                        {"type": "text", "text": "<system-reminder>metadata</system-reminder>"}]}],
                }
                status, data = self.request(MESSAGES + "?beta=true", body, {"Authorization": "Bearer " + CALLER_KEY,
                                            "anthropic-beta": "fixture-beta", "anthropic-version": "2023-06-01"})
                self.assertEqual(status, 200)
                self.assertIn(MARKER.encode(), data)
                self.assertIn(b"event: message_stop", data)
                path, headers, sent = self.requests[-1]
                self.assertEqual(path, MESSAGES + "?beta=true")
                self.assertEqual(headers["x-api-key"], UPSTREAM_KEY)
                self.assertNotIn("Authorization", headers)
                self.assertEqual(headers["anthropic-beta"], "fixture-beta")
                self.assertEqual(sent, {**body, "model": MODELS[tier], "output_config": {"effort": effort}})
                decision_headers, question = self.dossiers[-1]
                self.assertEqual(decision_headers["Authorization"], "Bearer " + JUDGE_KEY)
                self.assertEqual(question["state"], {"task": task, "step": "user_turn"})

    def test_token_count_manual_model_and_judge_failure(self):
        body = {"model": AUTO, "messages": [{"role": "user", "content": "Tiny"}]}
        self.assertEqual(self.request(COUNT_TOKENS, body), (200, b'{"input_tokens": 10}'))
        self.assertEqual(self.requests[-1][2]["model"], MODELS[TERRA])
        explicit = {**body, "model": MODELS[ASTRA], "thinking": {"type": "adaptive"}}
        self.assertEqual(self.request(MESSAGES, explicit)[0], 200)
        self.assertEqual(self.requests[-1][2], explicit)
        auxiliary = {"x-api-key": CALLER_KEY, "x-claude-code-request-class": "auxiliary"}
        self.assertEqual(self.request(MESSAGES, body, headers=auxiliary)[0], 200)
        self.assertEqual(self.requests[-1][2]["model"], MODELS[LUNA])
        self.assertNotIn("x-claude-code-request-class", self.requests[-1][1])
        self.assertEqual(self.dossiers, [])
        self.refuse = True
        self.assertEqual(self.request(MESSAGES, body)[0], 200)
        self.assertEqual(self.requests[-1][2]["model"], MODELS[SOL])
        self.assertEqual(self.requests[-1][2]["output_config"]["effort"], "medium")

    def test_auto_translates_unsupported_thinking_modes(self):
        for task, mode, expected in (("Tiny", "disabled", "disabled"),
                                     ("Routine", "disabled", "between_tools"),
                                     ("Hard", "disabled", "adaptive"),
                                     ("Hard", "enabled", "adaptive")):
            with self.subTest(task=task, mode=mode):
                body = {"model": AUTO, "messages": [{"role": "user", "content": task}],
                        "thinking": {"type": mode}}
                self.assertEqual(self.request(MESSAGES, body)[0], 200)
                self.assertEqual(self.requests[-1][2]["thinking"], {"type": expected})

    def test_streamed_tool_continuation_keeps_the_route_and_thinking(self):
        history = [{"role": "user", "content": "Hard"}]
        body = {"model": AUTO, "messages": history, "stream": True,
                "tools": [{"name": "fixture_tool", "input_schema": {"type": "object"}}]}
        status, data = self.request(MESSAGES, body)
        self.assertEqual(status, 200)
        self.assertIn(TOOL["id"].encode(), data)
        thinking = {"type": "thinking", "thinking": "opaque fixture", "signature": "fixture-signature"}
        body["messages"] = [*history, {"role": "assistant", "content": [thinking, TOOL]},
                            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": TOOL["id"],
                                                           "content": "done"}]},
                            {"role": "system", "content": "fixture reminder"}]
        self.assertEqual(self.request(MESSAGES, body)[0], 200)
        self.assertEqual(self.requests[-1][2]["model"], MODELS[SOL])
        self.assertEqual(self.requests[-1][2]["messages"], body["messages"])
        self.assertEqual(len(self.dossiers), 1)

    def test_output_limit_retry_keeps_the_main_prompt_route_once(self):
        hints = {"x-api-key": CALLER_KEY, "x-claude-code-request-class": "main",
                 "x-claude-code-prompt-id": "synthetic-prompt-id"}
        body = {"model": AUTO, "messages": [{"role": "user", "content": "Hard at output limit"}], "stream": True}
        self.assertIn(b'"stop_reason": "max_tokens"', self.request(MESSAGES, body, headers=hints)[1])
        body["messages"] += [{"role": "assistant", "content": MARKER}, {"role": "user", "content": "Resume"}]
        self.assertEqual(self.request(MESSAGES, body, headers=hints)[0], 200)
        self.assertEqual(self.requests[-1][2]["model"], MODELS[SOL])
        self.assertEqual(len(self.dossiers), 1)
        body["messages"] = [{"role": "user", "content": "Routine"}]
        self.assertEqual(self.request(MESSAGES, body, headers=hints)[0], 200)
        self.assertEqual(self.requests[-1][2]["model"], MODELS[TERRA])
        self.assertEqual(len(self.dossiers), 2)

    @unittest.skipUnless(shutil.which("claude"), "Claude Code not installed")
    def test_installed_claude_code_routes_three_tiers_and_returns_a_tool_result(self):
        with tempfile.TemporaryDirectory() as root:
            env = {key: value for key, value in os.environ.items() if key.upper() in
                   ("PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC", "TMPDIR", "TEMP", "TMP")}
            env.update({
                "HOME": root, "USERPROFILE": root, "CLAUDE_CONFIG_DIR": root,
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.gateway.server_port}",
                "ANTHROPIC_API_KEY": CALLER_KEY, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
            })
            for task, tier in (("Tiny", LUNA), ("Routine", TERRA), ("Hard", SOL)):
                with self.subTest(task=task):
                    start = len(self.requests)
                    tools = ["--tools", "Bash", "--allowedTools", "Bash"] if tier == TERRA else ["--tools", ""]
                    result = subprocess.run(
                        [shutil.which("claude"), "--bare", "--print", "--model", AUTO, *tools,
                         "--no-session-persistence", "--", task],
                        cwd=root, env=env, text=True, capture_output=True, timeout=40,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(MARKER, result.stdout)
                    requests = [body for path, _, body in self.requests[start:] if path.split("?", 1)[0] == MESSAGES]
                    self.assertEqual({body["model"] for body in requests}, {MODELS[tier]})
                    self.assertTrue(any(body["state"]["task"] == task for _, body in self.dossiers))
                    if tier == TERRA:
                        self.assertTrue(any("JEV_CLAUDE_TOOL_OK" in str(block.get("content"))
                                            for body in requests for msg in body["messages"]
                                            for block in (msg["content"] if isinstance(msg["content"], list) else [])
                                            if block.get("type") == "tool_result"))
                        self.assertEqual(sum(body["state"]["task"] == task for _, body in self.dossiers), 1)
