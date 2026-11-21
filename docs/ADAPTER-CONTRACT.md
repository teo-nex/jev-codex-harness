# Target-client adapter contract

An installation agent can run in any harness. A **target-client adapter** is
needed for each client receiving Jev behavior. Codex is the bundled adapter;
this document defines the minimum for adding another one.

## Required mapping

Before writing files, identify the target client's supported extension points
and native permission model. Record one of `SUPPORTED`, `UNSUPPORTED`, or
`UNKNOWN` for each capability:

| Capability | Adapter must connect |
| --- | --- |
| Model routing | A model request boundary where Jev can choose among a closed, available model set before execution. Keep one conversation across provider fallback only where the client's history format supports it. |
| Tool Use | A pre-action hook with the real current user goal and proposed side effect. Safe reads may skip Jev; writes and external actions need host permission plus Jev review in auto mode. Manual model selection must not acquire an ordinary Jev veto. |
| Browser/Computer Use | A live observation and element-specific action boundary, host-owned execution, and post-action verification. Never treat a Jev choice as permission. If the client exposes no such boundary, mark it unsupported. |
| Context compact | A before/after compact boundary. Jev may shortlist carry facts; the client retains responsibility for transcript compaction. |
| Skills/workers | A bounded skill index and native worker lifecycle with file ownership, checks and collected results. Do not spawn hidden children or pass whole transcripts by default. |
| Usage/cost | Provider usage and missing-usage coverage. Separate observed usage, synthetic tests, API-price counterfactuals and account debits. |

## Safety and ownership

- The client owns capability discovery, permissions, approvals, execution,
  retries, cancellation, and final answers. Jev receives bounded choices and
  returns advice, never executable shell text or a grant of permission.
- Routing may fall back to a known safe native model before any client-visible
  output. A partially delivered response must not be replayed to a second
  provider without a client-supported recovery contract. Risky tool actions
  fail closed when authorization cannot be established.
- Keep credentials in protected local files or a local hidden prompt. Never
  write values into repository files, tool arguments, agent prompts, reports or
  replay cases. Preserve unrelated client configuration and active sessions.
- Keep a journal of files and service identities owned by the adapter. A
  rollback may remove only exact owned, unchanged resources. An existing
  profile requires an explicit migration plan, not an overwrite.

## Acceptance before claiming support

Use synthetic tasks first, then a real client session. Verify a safe read,
scoped edit with independent tests, denied unrelated destructive action,
manual-model autonomy, one completed routed response, provider failure before
output, and all UI/compact capabilities claimed by the adapter. Check the
target's actual process and config, not only files or localhost health.

Add the adapter's tests and a support-matrix row before describing it as
bundled. Until then the agent-first installer reports `UNSUPPORTED_TARGET` or
`PARTIAL` for that client.

## Preview model routing from DeepSeek Harness and Pi

The routing configuration uses an already running Jev profile. From the repo,
print a config for the target client with the Jev service's port.

```sh
./install.sh --port 4319 client-config dsh
./install.sh --port 4319 client-config pi
```

| Client | Where to merge the printed config | Local credential reference |
| --- | --- | --- |
| DeepSeek Harness 0.2.2-rc.5 | Merge `config.providers.jev` into the `llm-pi-ai` entry in the profile's `cordis.patch.yml`. A fresh profile can load the preview with `--patch`. | `JEV_LOCAL_CALLER_KEY` in the launch environment or the interactive client’s credential store |
| Pi | `providers.jev` in `$PI_CODING_AGENT_DIR/models.json` (default directory `~/.pi/agent`) | `JEV_LOCAL_CALLER_KEY` in the environment of the Pi process |

Set that reference to the **local** caller capability from the Jev profile's
`codex-router/generic-provider-credentials/jev.key` file. Never copy it into the
model config. Select provider `jev` and model `auto` in the client. For a one-shot DSH run, include an
`agent-default-model` patch entry with `config: {provider: jev, model: auto}`
in the invocation's overlay. DSH overlays replace a plugin entry’s whole
configuration. Merge the provider into the existing entry if your profile
already has an `llm-pi-ai` configuration. In Pi,
`--provider jev --model auto` makes that selection for a single launch.

The request goes to the local Jev service using OpenAI Responses. The service's
configured execution routes determine which models can actually serve it. To
keep execution on OpenAI, restrict those routes, including fallback, to
OpenAI models. Native subscription models require the owner's explicit session
sharing for the Jev profile. API-key access needs an allowed OpenAI route
configured on the execution gateway. The Jev decision credential is configured
separately.

These previews cover model routing only. Target-native tool approval, Jev
Tool Use review, browser/computer actions, workers and Jev-assisted compaction
still have separate acceptance requirements. The preview command creates no
files and does not change any existing client.
