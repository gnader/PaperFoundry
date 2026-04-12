"""Thin abstraction over the Ollama local LLM service.

Uses the official `ollama` Python package. The Ollama service is expected to
be installed and running separately (Windows installer sets it up as a
background service listening on http://localhost:11434). This module never
starts the service itself — it only connects and reports clear errors when
the service or a requested model isn't available.

Planned use: deep-mode paper relevance scoring in filter.py.
"""

import argparse
import sys
from typing import List, Optional, Tuple

try:
    import ollama
except ImportError as e:
    raise ImportError("ollama package not installed. Run: pip install ollama") from e


DEFAULT_HOST = "http://localhost:11434"


def _iter_models(response):
    """Yield model entries from an ollama.list() or ollama.ps() response.

    Handles both dict-shaped and object-shaped responses across ollama versions.
    """
    if isinstance(response, dict):
        return response.get("models", []) or []
    return getattr(response, "models", []) or []


def _entry_attr(entry, *keys):
    """Read the first matching key from a dict-or-object entry."""
    for k in keys:
        if isinstance(entry, dict):
            if k in entry and entry[k] is not None:
                return entry[k]
        else:
            v = getattr(entry, k, None)
            if v is not None:
                return v
    return None


def _model_names(response) -> List[str]:
    names = []
    for m in _iter_models(response):
        name = _entry_attr(m, "model", "name")
        if name:
            names.append(name)
    return names


def _connection_error_message(host: str, exc: Exception) -> str:
    return (
        f"Ollama not reachable at {host} ({exc.__class__.__name__}). "
        f"Start the Ollama service or install from https://ollama.com."
    )


def _is_not_found(exc: Exception) -> bool:
    """Detect Ollama's 'model not found' error across package versions."""
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    msg = str(exc).lower()
    return "not found" in msg and "model" in msg


def check_available(
    host: str = DEFAULT_HOST,
    model: Optional[str] = None,
) -> Tuple[bool, str]:
    """Check that the Ollama service is reachable and (optionally) that `model` is pulled."""
    try:
        response = ollama.Client(host=host).list()
    except Exception as e:
        return (False, _connection_error_message(host, e))

    if model is not None:
        if model not in _model_names(response):
            return (False, f"Model '{model}' not pulled. Run: ollama pull {model}")
        return (True, f"Ollama reachable at {host}, model '{model}' available")

    return (True, f"Ollama reachable at {host}")


def check_loaded(
    host: str = DEFAULT_HOST,
    model: Optional[str] = None,
) -> Tuple[bool, str]:
    """Check whether `model` is currently loaded in VRAM.

    If `model` is None, returns (True, summary) listing whatever models are loaded.
    If `model` is given, returns (True, ...) only when that specific model is resident.
    """
    try:
        response = ollama.Client(host=host).ps()
    except Exception as e:
        return (False, _connection_error_message(host, e))

    running = list(_iter_models(response))

    if model is not None:
        for entry in running:
            name = _entry_attr(entry, "model", "name")
            if name == model:
                size_vram = _entry_attr(entry, "size_vram") or 0
                expires_at = _entry_attr(entry, "expires_at") or "unknown"
                gb = size_vram / (1024 ** 3) if size_vram else 0.0
                return (
                    True,
                    f"Model '{model}' is loaded in VRAM (~{gb:.1f} GB, expires {expires_at})",
                )
        return (
            False,
            f"Model '{model}' is not loaded. It will load on first request "
            f"(or run: ollama run {model}).",
        )

    if not running:
        return (True, "No models currently loaded.")
    names = [n for n in (_entry_attr(e, "model", "name") for e in running) if n]
    return (True, f"Loaded models: {', '.join(names)}")


def load_model(
    model: str,
    host: str = DEFAULT_HOST,
    keep_alive: Optional[str] = None,
) -> Tuple[bool, str]:
    """Load `model` into VRAM by sending an empty-prompt generate request.

    `keep_alive` follows Ollama's format: "5m" (server default if None), "1h",
    "0" to unload immediately, "-1" to keep forever.
    """
    kwargs = {"model": model, "prompt": ""}
    if keep_alive is not None:
        kwargs["keep_alive"] = keep_alive
    try:
        ollama.Client(host=host).generate(**kwargs)
    except Exception as e:
        if _is_not_found(e):
            return (False, f"Model '{model}' not pulled. Run: ollama pull {model}")
        return (False, _connection_error_message(host, e))
    return (True, f"Model '{model}' loaded into VRAM (keep_alive={keep_alive or 'default'})")


def unload_model(model: str, host: str = DEFAULT_HOST) -> Tuple[bool, str]:
    """Evict `model` from VRAM (keep_alive=0). No-op if the model wasn't loaded."""
    try:
        ollama.Client(host=host).generate(model=model, prompt="", keep_alive=0)
    except Exception as e:
        if _is_not_found(e):
            return (False, f"Model '{model}' not pulled. Run: ollama pull {model}")
        return (False, _connection_error_message(host, e))
    return (True, f"Model '{model}' unloaded from VRAM")


def generate(
    model: str,
    prompt: str,
    system: Optional[str] = None,
    format: Optional[str] = None,
    options: Optional[dict] = None,
    host: str = DEFAULT_HOST,
) -> str:
    """Run a one-shot generate against `model` and return the generated text.

    Not a chat — no history is tracked. Each call is independent.

    Refuses to auto-load the model: raises RuntimeError if the model isn't
    already resident in VRAM. Call load_model(...) explicitly first.
    VRAM residency is owned by load_model/unload_model, so this function
    never touches keep_alive.
    """
    ok, message = check_loaded(host=host, model=model)
    if not ok:
        raise RuntimeError(
            message if "not reachable" in message
            else f"Model '{model}' is not loaded. Call load_model('{model}') first."
        )

    kwargs = {"model": model, "prompt": prompt}
    if system is not None:
        kwargs["system"] = system
    if format is not None:
        kwargs["format"] = format
    if options is not None:
        kwargs["options"] = options

    try:
        response = ollama.Client(host=host).generate(**kwargs)
    except Exception as e:
        if _is_not_found(e):
            raise RuntimeError(f"Model '{model}' not pulled. Run: ollama pull {model}") from e
        raise RuntimeError(_connection_error_message(host, e)) from e

    text = _entry_attr(response, "response")
    return text if text is not None else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Check / control Ollama service + model state.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Ollama host (default: {DEFAULT_HOST}).")
    parser.add_argument("--model", help="Model name (e.g. gemma4:e2b).")
    parser.add_argument(
        "--keep-alive",
        help='Keep-alive duration for --load (e.g. "5m", "1h", "-1"). Default: server default.',
    )
    parser.add_argument("--prompt", help="Run generate() with this prompt and print the response.")
    parser.add_argument("--system", help="Optional system prompt for --prompt.")
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for --prompt (json forces structured output).",
    )

    action = parser.add_mutually_exclusive_group()
    action.add_argument("--loaded", action="store_true", help="Check if the model is in VRAM.")
    action.add_argument("--load", action="store_true", help="Load the model into VRAM.")
    action.add_argument("--unload", action="store_true", help="Evict the model from VRAM.")

    args = parser.parse_args()

    if args.load or args.unload or args.prompt is not None:
        if not args.model:
            parser.error("--load, --unload, and --prompt require --model")

    if args.load:
        ok, message = load_model(model=args.model, host=args.host, keep_alive=args.keep_alive)
    elif args.unload:
        ok, message = unload_model(model=args.model, host=args.host)
    elif args.prompt is not None:
        try:
            text = generate(
                model=args.model,
                prompt=args.prompt,
                system=args.system,
                format=args.format if args.format == "json" else None,
                host=args.host,
            )
        except RuntimeError as e:
            print(str(e))
            return 1
        print(text)
        return 0
    elif args.loaded:
        ok, message = check_loaded(host=args.host, model=args.model)
    else:
        ok, message = check_available(host=args.host, model=args.model)

    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
