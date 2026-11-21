"""Model-routing configuration for external clients sharing a local Jev service."""

import json

# Match the published Jev model's context window. The alias selects its own
# effort and advertises text until other input types pass client acceptance.
_CONTEXT_WINDOW = 258400
_CREDENTIAL_REF = "JEV_LOCAL_CALLER_KEY"


def render(target: str, port: int) -> str:
    if not 1024 <= port <= 65535:
        raise ValueError("--port must be between 1024 and 65535")
    base_url = f"http://127.0.0.1:{port}/v1"
    if target == "pi":
        provider = {
            "name": "Jev Router",
            "baseUrl": base_url,
            "api": "openai-responses",
            "apiKey": "$" + _CREDENTIAL_REF,
            "authHeader": True,
            "compat": {"supportsLongCacheRetention": False},
            "models": [{"id": "auto", "name": "Jev Auto", "input": ["text"],
                        "contextWindow": _CONTEXT_WINDOW, "reasoning": False}],
        }
        return json.dumps({"providers": {"jev": provider}}, indent=2) + "\n"
    if target != "dsh":
        raise ValueError("Client must be dsh or pi")
    return (
        "- id: llm-pi-ai\n"
        "  config:\n"
        "    providers:\n"
        "      jev:\n"
        '        displayName: "Jev Router"\n'
        '        api: "openai-responses"\n'
        f'        baseURL: "{base_url}"\n'
        f'        apiKeyEnv: "{_CREDENTIAL_REF}"\n'
        f"        defaultContextWindow: {_CONTEXT_WINDOW}\n"
        "        models:\n"
        '          - id: "auto"\n'
        '            name: "Jev Auto"\n'
        "            input:\n"
        '              - "text"\n'
        f"            contextWindow: {_CONTEXT_WINDOW}\n"
        "            reasoningEfforts: false\n"
    )
