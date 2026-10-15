#!/usr/bin/env python3
"""Check that localized docs cover every English page and match its source hash.

This mirrors the contract Core's documentation build (tools/autodoc.py) enforces
when it imports these docs, so a stale translation fails here before it merges.
"""

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / "sima-i18n.config.json").read_text(encoding="utf-8"))
    source_dir = config["sourceDir"]
    manifest = json.loads((root / config["manifest"]).read_text(encoding="utf-8"))
    excluded = config.get("excludedPrefixes", [])

    def in_scope(key):
        return key.endswith((".md", ".mdx")) and not any(key.startswith(p) for p in excluded)

    sources = sorted(
        path.relative_to(root).as_posix()
        for path in (root / source_dir).rglob("*")
        if in_scope(path.relative_to(root).as_posix())
    )

    failures = []
    for locale in config["locales"]:
        translation_root = root / config["translationDir"].replace("{locale}", locale)
        hashes = manifest.get(locale, {})
        for key in sources:
            relative = Path(key).relative_to(source_dir)
            if not (translation_root / relative).is_file():
                failures.append(f"{locale} translation is missing: {key}")
        for path in sorted(translation_root.rglob("*")):
            key = (Path(source_dir) / path.relative_to(translation_root)).as_posix()
            if not in_scope(key):
                continue
            source = root / key
            if not source.is_file():
                failures.append(f"{locale} translation has no English source: {key}")
            elif not hashes.get(key):
                failures.append(f"{locale} translation has no source-hash record: {key}")
            elif hashlib.sha256(source.read_bytes()).hexdigest() != hashes[key]:
                failures.append(f"{locale} translation is stale: {key}")

    if failures:
        print("Documentation translations are out of date:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        print(
            "Update docs/i18n/<locale>/ to match the English page, then update its "
            f"hash in {config['manifest']}.",
            file=sys.stderr,
        )
        return 1
    print(f"Translations are current for {len(sources)} pages in {len(config['locales'])} locales.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
