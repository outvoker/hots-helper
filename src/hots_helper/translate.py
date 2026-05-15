"""Pluggable translation client.

The translation hotkeys (chat capture and compose-to-target) call
:func:`translate` with a list of source strings and a target language
code. The actual API is fronted by a Supabase Edge Function so the
provider's secret keys never live in client binaries — every shipped
.exe gets only the (rate-limited, anon-key-gated) Function URL.

The Edge Function in turn calls VolcEngine MT (火山翻译). We picked
VolcEngine because:

* It's directly reachable from mainland China without a VPN.
* The free tier is 2 M characters / month, more than enough for a
  five-person squad.
* East-Asian language quality (ko/ja → zh) is better than
  Google/DeepL for casual game chat.

The Function URL is configured at build time in ``sync_defaults`` (next
to the cloud-sync defaults) so squad members don't have to type it in.
A custom URL/key combo can be set in the UI for private deployments.

Public surface:

* :func:`translate` — synchronous; raises :class:`TranslateError`.
* :data:`SUPPORTED_LANGS` — codes accepted as ``target``.

The function blocks; callers are expected to invoke from a worker
QThread, never from the Qt main thread.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .sync_defaults import (
    DEFAULT_SUPABASE_ANON_KEY,
    DEFAULT_SUPABASE_URL,
)

# Subset of VolcEngine MT codes we actually expose — just the ones
# squad members are likely to encounter on KR/JP/EU servers, plus zh
# as the canonical "translate to Chinese" target.
SUPPORTED_LANGS: tuple[tuple[str, str], ...] = (
    ("zh", "中文"),
    ("en", "English"),
    ("ko", "한국어"),
    ("ja", "日本語"),
)


class TranslateError(RuntimeError):
    """Raised when the translation backend is unreachable or rejects the
    request. Carries a short user-facing message in the first arg."""


@dataclass
class TranslateResult:
    text: str
    detected_source: str  # e.g. "ko", "ja", "en". May be empty.


def translate(
    texts: list[str],
    *,
    target: str = "zh",
    source: str = "auto",
    supabase_url: str | None = None,
    supabase_anon_key: str | None = None,
    timeout: float = 12.0,
) -> list[TranslateResult]:
    """Translate ``texts`` to ``target`` via the squad's Supabase Edge
    Function.

    ``source="auto"`` lets VolcEngine detect each line independently —
    important for the chat-OCR flow because different players may type
    in different languages.

    Returns one :class:`TranslateResult` per input string, in the same
    order. Empty inputs come back as empty results without an API call.
    """
    if not texts:
        return []
    # Filter blanks but keep their slots so we can reassemble in order.
    payload_indices: list[int] = []
    payload_strings: list[str] = []
    for i, t in enumerate(texts):
        s = (t or "").strip()
        if s:
            payload_indices.append(i)
            payload_strings.append(s)
    out: list[TranslateResult] = [TranslateResult(text="", detected_source="") for _ in texts]
    if not payload_strings:
        return out

    base_url = (supabase_url or DEFAULT_SUPABASE_URL).rstrip("/")
    if not base_url:
        raise TranslateError("translation backend URL not configured")
    anon_key = supabase_anon_key or DEFAULT_SUPABASE_ANON_KEY
    if not anon_key:
        raise TranslateError("translation backend key not configured")

    body = json.dumps(
        {
            "texts": payload_strings,
            "target": target,
            "source": source,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    url = f"{base_url}/functions/v1/translate"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {anon_key}",
            "apikey": anon_key,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")[:300]
        raise TranslateError(f"translate HTTP {e.code}: {raw}") from e
    except urllib.error.URLError as e:
        raise TranslateError(f"translate network error: {e.reason}") from e
    except Exception as e:
        raise TranslateError(f"translate failed: {type(e).__name__}: {e}") from e

    items = data.get("translations") or data.get("Translations") or []
    if not isinstance(items, list) or len(items) != len(payload_strings):
        raise TranslateError(
            f"translate response shape unexpected: {data!r}"[:200]
        )
    for slot, item in zip(payload_indices, items):
        if isinstance(item, dict):
            out[slot] = TranslateResult(
                text=str(item.get("text") or item.get("Translation") or ""),
                detected_source=str(item.get("source") or item.get("DetectedSourceLanguage") or ""),
            )
        else:
            out[slot] = TranslateResult(text=str(item), detected_source="")
    return out
