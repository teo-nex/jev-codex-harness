# CLAUDE.md

When the user asks to install Jev in Claude Code, use their current Claude Code
profile. Run `./install.sh claude --setup-only` from this checkout
(`install.cmd claude --setup-only` on Windows). If the user supplied protected
key file paths, pass them with `--anthropic-key-file` and `--jev-key-file`.
If the command needs keys and its terminal cannot accept hidden input, give the
user one local command, `./install.sh claude` (`install.cmd claude` on Windows).
That command saves the keys and opens Claude Code in the same profile with Jev
Auto selected. Do not obtain keys in chat or read the user's Claude login files.
Read [the Claude quickstart](docs/CLAUDE.md) for providers and verification.

For a Codex installation request, follow [AGENT_INSTALL.md](AGENT_INSTALL.md).
