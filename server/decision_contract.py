"""Shared identity contract for the typed Jev v1 response schema."""

import re
from urllib.parse import urlsplit

REQUEST_MODEL = "jev-latest"
_MODEL = re.compile(r"(?:typesafe/)?jev-(?:latest|1\.\d+(?:\.\d+)?(?:-\d{8})?)\Z")


def compatible_model(value):
    """Accept v1 identities; callers must still validate their typed answers."""
    return isinstance(value, str) and _MODEL.fullmatch(value) is not None


DECISION_PROVIDERS = ("typesafe", "openrouter", "compatible")
COMPATIBLE_ENV = ("JEV_DECISION_ENDPOINT", "JEV_DECISION_MODEL")
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")


def decision_environment(provider, endpoint=None, model=None):
    """Validate operator-owned settings and return their service environment."""
    if provider not in DECISION_PROVIDERS:
        raise ValueError("Jev provider must be typesafe, openrouter or compatible")
    if provider != "compatible":
        if endpoint is not None or model is not None:
            raise ValueError("Jev endpoint and model require --jev-provider compatible")
        return {}
    if not isinstance(endpoint, str) or not re.fullmatch(r"[!-~]{1,2048}", endpoint):
        raise ValueError("Set a valid full URL with --jev-endpoint")
    try:
        url = urlsplit(endpoint)
        port = url.port
    except ValueError:
        raise ValueError("Invalid Jev decision endpoint") from None
    if (not re.fullmatch(r"(?:[A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]+)?", url.netloc)
            or not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*", url.path)
            or "?" in endpoint or "#" in endpoint or port == 0
            or (url.scheme != "https" and (url.scheme != "http" or url.hostname not in LOOPBACK_HOSTS))):
        raise ValueError("Jev endpoint requires HTTPS or HTTP on 127.0.0.1 or [::1], with a full path")
    if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
        raise ValueError("Set a valid Jev model ID with --jev-model")
    return dict(zip(COMPATIBLE_ENV, (endpoint, model)))
