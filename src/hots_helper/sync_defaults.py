"""Built-in defaults for cloud sync.

When set, the squad's app starts up already configured for the shared
Supabase project — no per-user setup. The user can still override these
in Settings (their config.json wins over the defaults) so we can rotate
the key in a future release without bricking the app.

Per Supabase's own docs, ``sb_publishable_...`` keys are explicitly safe
to ship in client source code:
https://supabase.com/docs/guides/api/api-keys

Don't ever paste a ``sb_secret_...`` key here — those bypass RLS.
"""

from __future__ import annotations

# The squad's shared Supabase project. Both must be set for sync to be
# automatically enabled; leave them blank to require manual setup.
DEFAULT_SUPABASE_URL = ""              # e.g. "https://abcdxyz123.supabase.co"
DEFAULT_SUPABASE_ANON_KEY = ""         # e.g. "sb_publishable_xxxxxxxxxxxxx"


def has_defaults() -> bool:
    return bool(DEFAULT_SUPABASE_URL) and bool(DEFAULT_SUPABASE_ANON_KEY)
