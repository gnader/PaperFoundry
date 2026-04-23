"""Thin abstraction over the Ollama local LLM service.

Uses the official `ollama` Python package. The Ollama service is expected to be installed and running
separately (Windows installer sets it up as a background service listening on http://localhost:11434).
This module never starts the service itself — it only connects and reports clear errors when the service
or a requested model isn't available.

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


# ===========================================================================================================================
# Response-parsing helpers (handle dict-shaped and object-shaped responses across ollama package versions)
# ===========================================================================================================================


def _iter_models(response):
    """Return model entries from an ollama.list() or ollama.ps() response."""
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
    return f"Ollama not reachable at {host} ({exc.__class__.__name__}). Start the Ollama service or install from https://ollama.com."


def _is_not_found(exc: Exception) -> bool:
    """Detect Ollama's 'model not found' error across package versions."""
    status = getattr(exc, "status_code", None)
    if status == 404:
        return True
    msg = str(exc).lower()
    return "not found" in msg and "model" in msg


# ===========================================================================================================================
# LLMClient
# ===========================================================================================================================


class LLMClient:
    """Client for a local Ollama LLM service.

    Wraps the official `ollama` Python package. Holds the model name and host so callers don't repeat them on every call.
    Validates on construction: raises RuntimeError if Ollama is unreachable or the model isn't pulled.

    Usage:
        client = LLMClient(model="gemma4:e2b")
        client.load(keep_alive="30m")
        text = client.generate(prompt="...")
        client.unload()
    """

    def __init__(self, model: str, host: str = DEFAULT_HOST):
        self.model = model
        self.host = host
        self._client = ollama.Client(host=host)

        # Validate: Ollama reachable?
        try:
            response = self._client.list()
        except Exception as e:
            raise RuntimeError(_connection_error_message(self.host, e)) from e

        # Validate: model pulled?
        if self.model not in _model_names(response):
            raise RuntimeError(f"Model '{self.model}' not pulled. Run: ollama pull {self.model}")

    def check_loaded(self) -> Tuple[bool, str]:
        """Check whether the model is currently loaded in VRAM."""
        try:
            response = self._client.ps()
        except Exception as e:
            return (False, _connection_error_message(self.host, e))

        for entry in _iter_models(response):
            name = _entry_attr(entry, "model", "name")
            if name == self.model:
                size_vram = _entry_attr(entry, "size_vram") or 0
                expires_at = _entry_attr(entry, "expires_at") or "unknown"
                gb = size_vram / (1024**3) if size_vram else 0.0
                return (True, f"Model '{self.model}' is loaded in VRAM (~{gb:.1f} GB, expires {expires_at})")

        return (False, f"Model '{self.model}' is not loaded. It will load on first request (or run: ollama run {self.model}).")

    def load(self, keep_alive: Optional[str] = None) -> Tuple[bool, str]:
        """Load the model into VRAM by sending an empty-prompt generate request.

        `keep_alive` follows Ollama's format: "5m" (server default if None), "1h", "0" to unload immediately, "-1" to keep forever.
        """
        kwargs = {"model": self.model, "prompt": ""}
        if keep_alive is not None:
            kwargs["keep_alive"] = keep_alive
        try:
            self._client.generate(**kwargs)
        except Exception as e:
            if _is_not_found(e):
                return (False, f"Model '{self.model}' not pulled. Run: ollama pull {self.model}")
            return (False, _connection_error_message(self.host, e))
        return (True, f"Model '{self.model}' loaded into VRAM (keep_alive={keep_alive or 'default'})")

    def unload(self) -> Tuple[bool, str]:
        """Evict the model from VRAM (keep_alive=0). No-op if the model wasn't loaded."""
        try:
            self._client.generate(model=self.model, prompt="", keep_alive=0)
        except Exception as e:
            if _is_not_found(e):
                return (False, f"Model '{self.model}' not pulled. Run: ollama pull {self.model}")
            return (False, _connection_error_message(self.host, e))
        return (True, f"Model '{self.model}' unloaded from VRAM")

    def generate(self, prompt: str, system: Optional[str] = None, format: Optional[str] = None, options: Optional[dict] = None) -> str:
        """Run a one-shot generate and return the generated text.

        Does not auto-load the model: raises RuntimeError if the model isn't already resident in VRAM. Call load() first.
        """
        ok, message = self.check_loaded()
        if not ok:
            raise RuntimeError(message if "not reachable" in message else f"Model '{self.model}' is not loaded. Call load() first.")

        kwargs = {"model": self.model, "prompt": prompt}
        if system is not None:
            kwargs["system"] = system
        if format is not None:
            kwargs["format"] = format
        if options is not None:
            kwargs["options"] = options

        try:
            response = self._client.generate(**kwargs)
        except Exception as e:
            if _is_not_found(e):
                raise RuntimeError(f"Model '{self.model}' not pulled. Run: ollama pull {self.model}") from e
            raise RuntimeError(_connection_error_message(self.host, e)) from e

        text = _entry_attr(response, "response")
        return text if text is not None else ""

    def embed(self, text: str) -> List[float]:
        """Return the embedding vector for `text` using the current model.

        Does not auto-load the model: raises RuntimeError if it isn't already resident in VRAM. Call load() first.
        """
        ok, message = self.check_loaded()
        if not ok:
            raise RuntimeError(message if "not reachable" in message else f"Model '{self.model}' is not loaded. Call load() first.")

        try:
            response = self._client.embed(model=self.model, input=text)
        except Exception as e:
            if _is_not_found(e):
                raise RuntimeError(f"Model '{self.model}' not pulled. Run: ollama pull {self.model}") from e
            raise RuntimeError(_connection_error_message(self.host, e)) from e

        # Response shape: {"embeddings": [[float, ...]]} or object with .embeddings
        embeddings = _entry_attr(response, "embeddings")
        if not embeddings or not embeddings[0]:
            raise RuntimeError(f"Model '{self.model}' returned no embeddings. It may not support embedding.")
        return embeddings[0]


# ===========================================================================================================================
# CLI
# ===========================================================================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Check / control Ollama service + model state.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Ollama host (default: {DEFAULT_HOST}).")
    parser.add_argument("--model", help="Model name (e.g. gemma4:e2b).")
    parser.add_argument("--keep-alive", help='Keep-alive duration for --load (e.g. "5m", "1h", "-1"). Default: server default.')
    parser.add_argument("--prompt", help="Run generate() with this prompt and print the response.")
    parser.add_argument("--system", help="Optional system prompt for --prompt.")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format for --prompt (json forces structured output).")

    action = parser.add_mutually_exclusive_group()
    action.add_argument("--loaded", action="store_true", help="Check if the model is in VRAM.")
    action.add_argument("--load", action="store_true", help="Load the model into VRAM.")
    action.add_argument("--unload", action="store_true", help="Evict the model from VRAM.")

    args = parser.parse_args()

    # No --model: just check if Ollama service is reachable
    if not args.model:
        if args.load or args.unload or args.prompt is not None:
            parser.error("--load, --unload, and --prompt require --model")
        try:
            ollama.Client(host=args.host).list()
        except Exception as e:
            print(_connection_error_message(args.host, e))
            return 1
        if args.loaded:
            try:
                response = ollama.Client(host=args.host).ps()
            except Exception as e:
                print(_connection_error_message(args.host, e))
                return 1
            running = list(_iter_models(response))
            if not running:
                print("No models currently loaded.")
            else:
                names = [n for n in (_entry_attr(e, "model", "name") for e in running) if n]
                print(f"Loaded models: {', '.join(names)}")
        else:
            print(f"Ollama reachable at {args.host}")
        return 0

    # --model given: construct client (validates service + model)
    try:
        client = LLMClient(model=args.model, host=args.host)
    except RuntimeError as e:
        print(str(e))
        return 1

    if args.load:
        ok, message = client.load(keep_alive=args.keep_alive)
    elif args.unload:
        ok, message = client.unload()
    elif args.prompt is not None:
        try:
            text = client.generate(prompt=args.prompt, system=args.system, format=args.format if args.format == "json" else None)
        except RuntimeError as e:
            print(str(e))
            return 1
        print(text)
        return 0
    elif args.loaded:
        ok, message = client.check_loaded()
    else:
        print(f"Ollama reachable at {args.host}, model '{args.model}' available")
        return 0

    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
