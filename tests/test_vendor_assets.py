"""The vendored front-end libraries are exactly the pinned npm releases."""

import hashlib
import json

from scripts.vendor_assets import PACKAGES, VENDOR


def manifest():
    return json.loads((VENDOR / 'manifest.json').read_text())


def test_vendored_files_match_their_recorded_digests():
    entries = manifest()
    on_disk = {path.relative_to(VENDOR).as_posix() for path in VENDOR.rglob('*') if path.is_file()}
    assert on_disk - {'manifest.json'} == set(entries)
    for path, entry in entries.items():
        assert hashlib.sha256((VENDOR / path).read_bytes()).hexdigest() == entry['sha256'], path


def test_manifest_records_every_pinned_file():
    pinned = {target: (name, version) for name, version, _, files in PACKAGES
              for target in files.values()}
    assert {path: (entry['package'], entry['version'])
            for path, entry in manifest().items()} == pinned
