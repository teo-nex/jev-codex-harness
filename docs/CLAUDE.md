# Jev model routing for Claude

## Quick start for Claude Code

Open this repository in Claude Code and ask **“Install Jev for Claude Code.”**
Claude reads the repository instructions and prepares Jev for your current profile.
If prompted for keys, run this from the checkout in a terminal:

```sh
./install.sh claude
```

On Windows use `install.cmd claude`. The first run reuses an available
`ANTHROPIC_API_KEY` or asks for your Claude Console key in a hidden prompt.
It reuses a running local Jev service if available. Otherwise it asks for a
TypeSafe Jev key. Subsequent launches use the protected setup in
`~/.jev-claude/settings.json` and start Claude Code with **Jev Claude Auto**
selected. The local gateway starts and stops with that launch. Claude Code
uses your existing settings, permissions, and sessions. Launching Claude Code
normally still uses its usual authentication and routing.

Use `./install.sh claude -- --continue` to resume the latest conversation for
the current project. Add `--cwd /path/to/project` before the separator to open
another workspace. To set up without opening a session, use `--setup-only`.
Provide protected key files with `--anthropic-key-file` and `--jev-key-file`.
For an alternate Jev decision provider add `--jev-provider openrouter`, or
`--jev-provider compatible --jev-endpoint URL --jev-model MODEL_ID`.
Use your profile or project settings for other Claude options. The launcher
supplies its own per-session `--settings` file for the gateway.

## Routing and billing

The gateway sends a bounded
description of the current task to that service and relays the original
Anthropic Messages request to the Claude API using your Claude Console API key.
Tool parameters, tool results, stream events, cache controls, and usage remain
in Anthropic format.

| Selection | Model |
| --- | --- |
| Small self-contained tasks | Claude Haiku 5.5 |
| Routine work | Claude Sonnet 5.5 |
| Complex work | Claude Opus 5.5 |
| Manual selection | Claude Fable 5.2 |

The `claude-jev-auto` alias uses the first three models. On a technical Jev
decision error it uses Opus at medium effort. Explicit model IDs bypass Jev's
decision and keep the caller's thinking settings. Within a running gateway,
a tool result with a recognized call ID reuses the model and effort that
issued the call so the tool continues with the same model. The upstream
account must have access to the selected models. Claude API billing and
provider errors are handled by the upstream account. For the automatic alias,
the gateway adapts thinking modes to the chosen model and effort level.

## Run a fixed-port gateway for other clients

1. Start the existing Jev decision service. The examples use its default port
   `4319` and the credential in its profile's
   `codex-router/generic-provider-credentials/jev.key`.
2. Store your Claude Console API key in a new protected file outside the
   repository. Create a second protected file containing a separate random
   gateway key for the Claude client. On macOS/Linux, files must be owned by
   you and have mode `0600` or stricter. On Windows, restrict their ACLs to
   your account and system administrators.
3. Run this command from the repository root. Use `--jev-caller-key-file` and
   `--jev-port` if the decision service uses an alternate profile or port.

```sh
python3 server/claude_gateway.py \
  --anthropic-key-file /path/to/claude-console.key \
  --caller-key-file /path/to/claude-client.key
```

The listener binds to `127.0.0.1:4322`. Keep the Claude Console key on the gateway
side. The gateway takes no Claude subscription login or OAuth tokens.

## Point Claude Code at a fixed-port gateway

Set the gateway address and the local client's gateway key in the environment
for one CLI launch. The example reads the key from the protected client file.
Set both variables together. `ANTHROPIC_BASE_URL` alone can leave a saved
Claude.ai subscription active as the request credential. With both variables
set, Claude Code uses the local gateway key for this launch. Regular launches
can continue using the saved subscription.

```sh
ANTHROPIC_BASE_URL=http://127.0.0.1:4322 \
ANTHROPIC_AUTH_TOKEN="$(cat /path/to/claude-client.key)" \
CLAUDE_CODE_GATEWAY_HINT_HEADERS=1 \
claude --model claude-jev-auto
```

Use `claude-fable-5-2`, or another explicit Claude ID from the table, to select
a model manually. For a disposable CLI check, add `--bare --print
--no-session-persistence` and supply a short prompt. Permission settings and
tools still belong to Claude Code.

With gateway hint headers enabled, automatic side requests such as session
titles use Haiku at low effort. A main prompt that reaches the output token
limit keeps its model when Claude Code resumes it under the same prompt ID.
The gateway uses these hints locally.

## Claude Desktop

In Anthropic's Third-Party Inference settings, select **Gateway** and enter
`http://127.0.0.1:4322` as the base URL. Use **Static API key** with the local
client's gateway key, and choose **Bearer**. The gateway lists
the four pinned Claude model IDs. To show Jev Auto in the Desktop model
picker, set an explicit `inferenceModels` list. The first entry is the default.

```json
{
  "inferenceModels": [
    {
      "name": "claude-jev-auto",
      "labelOverride": "Jev Claude Auto",
      "behavesAs": "claude-sonnet-5-5"
    },
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5-2"
  ]
}
```

These are configuration steps for the Desktop preview. Validate Chat, Cowork,
and Code separately in the installed app before relying on those surfaces.

- [Anthropic's Claude Code gateway guide](https://code.claude.com/docs/en/llm-gateway-connect)
- [How Claude Code handles subscriptions and gateways](https://code.claude.com/docs/en/llm-gateway#subscriptions-and-gateways)
- [Anthropic's Claude Desktop gateway guide](https://claude.com/docs/third-party/claude-desktop/gateway)
- [Anthropic's authentication and credential use rules](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use)

## Verification

```sh
python3 -m unittest discover -s server -p 'test_claude_gateway.py' -v
```

The tests check local authorization, the three automatic routes, explicit
Fable selection, token counts, a failed Jev decision, and a streamed tool
continuation against synthetic loopback APIs. If `claude` is on PATH, they
also launch it in a throwaway profile for all three tiers and a real local
tool round trip. A Claude Console key is needed to verify direct provider
access and live billing. The preview covers model routing.
