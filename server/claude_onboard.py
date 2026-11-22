"""Set up Jev and launch Claude Code with its current profile."""

import argparse
import getpass
import http.client
from itertools import takewhile
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import warnings

from claude_gateway import AUTO, CLAUDE_QUESTIONS, Gateway, MODELS, read_key, shared_decider
from decision_contract import DECISION_PROVIDERS, decision_environment
from jev_server import call_jev_routed
from local_runtime import NOFOLLOW, local_secret, private_regular
from routing_policy import ASTRA, LUNA, SOL, TERRA


def ask_key(label):
    if not sys.stdin.isatty():
        raise ValueError(f"{label} is needed. Run setup in a terminal or pass a protected key file.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(f"{label} (hidden input): ").strip()
        except (EOFError, getpass.GetPassWarning):
            raise ValueError("A terminal with hidden input is required.") from None


def read_setup(path):
    try:
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("Jev Claude setup cannot use symlinks.")
        with os.fdopen(os.open(path, os.O_RDONLY | NOFOLLOW | getattr(os, "O_NONBLOCK", 0)),
                       encoding="utf-8") as handle:
            if not private_regular(os.fstat(handle.fileno()), path):
                raise ValueError("Jev Claude settings must be in a protected regular file.")
            config = json.load(handle)
    except FileNotFoundError:
        return {}
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Unrecognized Jev Claude settings.")
    return config


def save_setup(path, config):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Jev Claude setup cannot use symlinks.")
    if os.name == "nt":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from harness.platforms.windows import _private_state_dir
        _private_state_dir(path.parent)
    elif path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o077:
        raise ValueError("The Jev Claude setup directory must be owned by you and mode 0700.")
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if not private_regular(os.fstat(handle.fileno()), Path(temporary)):
                raise ValueError("Could not protect the Jev Claude settings.")
            json.dump(config, handle)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def local_decider_ready(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        health = json.loads(response.read(8192))
        return health.get("service") == "jev-router" and health.get("auth_configured") is True
    except (OSError, ValueError, AttributeError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def valid_key(value):
    return isinstance(value, str) and re.fullmatch(r"[!-~]{1,4096}", value) is not None


def configure(args, path):
    saved = read_setup(path)
    anthropic_key = (read_key(args.anthropic_key_file) if args.anthropic_key_file else
                     saved.get("anthropic_key") or os.environ.get("ANTHROPIC_API_KEY") or
                     ask_key("Claude Console API key"))
    if not valid_key(anthropic_key) or not anthropic_key.startswith("sk-ant-api"):
        raise ValueError("Enter a Claude Console API key. Claude Pro or Max sign-in is used by ordinary Claude launches.")

    decision = saved.get("decision", {})
    if args.jev_provider or args.jev_key_file:
        provider = args.jev_provider or "typesafe"
        decision_environment(provider, args.jev_endpoint, args.jev_model)
        key = read_key(args.jev_key_file) if args.jev_key_file else ask_key("Jev decision API key")
        decision = {"provider": provider, "endpoint": args.jev_endpoint, "model": args.jev_model, "key": key}
    elif not decision:
        if local_secret() and local_decider_ready(4319):
            decision = {"provider": "local"}
        else:
            decision = {"provider": "typesafe", "key": ask_key("TypeSafe Jev API key")}

    provider = decision["provider"]
    if provider == "local":
        key = local_secret()
        if not key or not local_decider_ready(4319):
            raise ValueError("The configured local Jev service is unavailable. Start it or supply a Jev API key.")
        decide = shared_decider(4319, key)
    else:
        endpoint, model = decision.get("endpoint"), decision.get("model")
        decision_environment(provider, endpoint, model)
        key = decision.get("key")

        def decide(state):
            return call_jev_routed(key, state, CLAUDE_QUESTIONS, timeout=7,
                                   provider_config=(provider, endpoint, model))["answers"]

    if not valid_key(key) or key == anthropic_key:
        raise ValueError("A separate Jev decision credential is required.")
    config = {"version": 1, "anthropic_key": anthropic_key, "decision": decision}
    if saved != config:
        save_setup(path, config)
    return anthropic_key, decide


def launch(claude, claude_args, anthropic_key, decide, cwd=None):
    for arg in takewhile(lambda value: value != "--", claude_args):
        if arg == "--settings" or arg.startswith("--settings="):
            raise ValueError("Jev supplies the session's --settings option. Use your usual profile or project settings.")
    caller_key = secrets.token_urlsafe(32)
    with Gateway(0, caller_key, anthropic_key, decide) as gateway, tempfile.TemporaryDirectory() as directory:
        routing = {
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{gateway.server_port}",
            "ANTHROPIC_AUTH_TOKEN": caller_key,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_CUSTOM_HEADERS": "",
            "CLAUDE_CODE_OAUTH_TOKEN": "",
            "CLAUDE_CODE_USE_BEDROCK": "",
            "CLAUDE_CODE_USE_VERTEX": "",
            "CLAUDE_CODE_USE_FOUNDRY": "",
            "CLAUDE_CODE_USE_ANTHROPIC_AWS": "",
            "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD": "",
            "CLAUDE_CODE_USE_MANTLE": "",
            "ANTHROPIC_MODEL": AUTO + "[1m]",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODELS[LUNA],
            "ANTHROPIC_DEFAULT_SONNET_MODEL": MODELS[TERRA],
            "ANTHROPIC_DEFAULT_OPUS_MODEL": MODELS[SOL],
            "ANTHROPIC_DEFAULT_FABLE_MODEL": MODELS[ASTRA],
            "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        }
        for name in ("NO_PROXY", "no_proxy"):
            routing[name] = ",".join(filter(None, (os.environ.get(name), "127.0.0.1")))
        settings = Path(directory) / "settings.json"
        # Claude's session settings override provider values in the existing profile.
        save_setup(settings, {"env": routing})
        server = threading.Thread(target=gateway.serve_forever, daemon=True)
        server.start()
        try:
            # Let the attached Claude process handle the terminal's Ctrl+C.
            previous = signal.signal(signal.SIGINT, lambda *_: None)
            try:
                return subprocess.run([claude, "--settings", str(settings), *claude_args],
                                      cwd=cwd, env={**os.environ, **routing}, check=False).returncode
            finally:
                signal.signal(signal.SIGINT, previous)
        finally:
            gateway.shutdown()
            server.join()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="install claude", description=__doc__)
    parser.add_argument("--setup-only", action="store_true", help="save credentials and return to the calling agent")
    parser.add_argument("--cwd", type=Path, help="open Claude Code in this project directory")
    parser.add_argument("--anthropic-key-file", type=Path)
    parser.add_argument("--jev-provider", choices=DECISION_PROVIDERS)
    parser.add_argument("--jev-key-file", type=Path)
    parser.add_argument("--jev-endpoint")
    parser.add_argument("--jev-model")
    parser.add_argument("claude_args", nargs=argparse.REMAINDER, help="arguments after -- pass to Claude Code")
    args = parser.parse_args(argv)
    if (args.jev_endpoint or args.jev_model) and args.jev_provider != "compatible":
        parser.error("Use --jev-provider compatible with a custom Jev endpoint and model.")
    claude = shutil.which("claude")
    if not claude:
        parser.error("Claude Code is required on PATH. Install it, then run this command again.")
    try:
        key, decide = configure(args, Path.home() / ".jev-claude" / "settings.json")
        if args.setup_only:
            print("Jev settings saved. Run the same command without --setup-only to open Claude Code with Jev.")
            return 0
        claude_args = args.claude_args[1:] if args.claude_args[:1] == ["--"] else args.claude_args
        return launch(claude, claude_args, key, decide, args.cwd)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        # Parsing errors can include private input in their exception representation.
        message = str(error) if type(error) in (ValueError, OSError, PermissionError, RuntimeError) else "Invalid Jev Claude setup."
        print(f"Jev Claude: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
