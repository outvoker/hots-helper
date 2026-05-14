"""Build the offline talent-id → {en, zh} lookup table.

Source: HeroesToolChest/heroes-data on GitHub, which mirrors Blizzard's
own gamestrings (zh-CN + en-US among others). We fetch the latest
patch's strings and extract the ``abiltalent.name`` block, indexing by
``talentTreeId`` (the part before the first ``|`` in each key).

Run this whenever you want to refresh the talent table — usually once
per HotS patch::

    python scripts/fetch_talent_names.py

The output lands at
``src/hots_helper/data/talent_names.json`` and is shipped with the app.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO_API = "https://api.github.com/repos/HeroesToolChest/heroes-data/contents/heroesdata"
RAW = "https://raw.githubusercontent.com/HeroesToolChest/heroes-data/master/heroesdata"

LOCALES = {
    "en": "enus",
    "zh": "zhcn",
}


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def _latest_release_version() -> str:
    """The newest non-PTR version directory in the heroes-data repo."""
    entries = _http_json(REPO_API)
    versions = sorted(
        e["name"]
        for e in entries
        if e["type"] == "dir"
        and e["name"][:1].isdigit()
        and not e["name"].endswith("_ptr")
    )
    if not versions:
        raise RuntimeError("no version dirs found in heroes-data")
    return versions[-1]


def _index_by_first(names: dict[str, str]) -> dict[str, str]:
    """Each key is ``<talentTreeId>|<…>|<Type>|<flag>``. Collapse to the
    first segment, preferring entries we see for ``False`` (= visible to
    the player) over hidden/cancel sub-entries when both exist."""
    out: dict[str, str] = {}
    for k, v in names.items():
        first = k.split("|", 1)[0]
        if first not in out or k.endswith("|False"):
            out[first] = v
    return out


def fetch(version: str) -> dict[str, dict[str, str]]:
    talents: dict[str, dict[str, str]] = {}
    for lang, code in LOCALES.items():
        url = f"{RAW}/{version}/gamestrings/gamestrings_{version.split('.')[-1]}_{code}.json"
        print(f"  fetching {lang} ({code}) …", file=sys.stderr)
        data = _http_json(url)
        names = data["gamestrings"]["abiltalent"]["name"]
        idx = _index_by_first(names)
        for tid, n in idx.items():
            talents.setdefault(tid, {})[lang] = n
    return talents


def main() -> None:
    version = _latest_release_version()
    print(f"using heroes-data version {version}", file=sys.stderr)
    talents = fetch(version)

    out_dir = Path(__file__).resolve().parent.parent / "src" / "hots_helper" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "talent_names.json"
    payload = {
        "_meta": {
            "source_version": version,
            "source_repo": "HeroesToolChest/heroes-data",
            "count": len(talents),
        },
        "talents": talents,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {out_path} ({len(talents)} talents)", file=sys.stderr)


if __name__ == "__main__":
    main()
