#!/usr/bin/env python
"""Capture the control structure of the selected Schwab tab. No text, data or images.

Select the Schwab tab with the Pacioli toolbar button, then run:

    .venv/bin/python scripts/schwab_probe.py [--save]

Every string is re-checked against the extension's vocabulary; anything else is
replaced, so a content-script bug cannot leak page text.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli.functions.chase_local_bridge import FirefoxBridge


SOCKET = Path('/run/user') / str(os.getuid()) / 'pacioli-schwab-extension.sock'
PUNCTUATION = re.compile(r'^[()\[\]\-/:.,&|+…]+$')
STRUCTURAL_KEYS = {'origin', 'kind', 'tag', 'role', 'type', 'popup', 'expanded', 'checked'}


def vocabulary():
    source = (ROOT / 'extensions/schwab-local/content.js').read_text()
    block = re.search(r'const VOCABULARY = new Set\(`(.*?)`', source, re.S).group(1)
    return set(block.split()) | {'csv', 'json', 'xml', 'ira', 'ytd', 'ok'}


def clean_text(value, words):
    if not isinstance(value, str):
        return value
    for word in value.split():
        if word not in words and word not in ('*', '#') and not PUNCTUATION.match(word):
            return '?'
    return value


def sanitize(value, words, key=None):
    if isinstance(value, dict):
        return {k: sanitize(v, words, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, words, key) for v in value]
    if key in STRUCTURAL_KEYS or key == 'custom_elements':
        if isinstance(value, str) and not re.fullmatch(r'[a-z0-9_\-:/.]{0,64}', value):
            return '?'
        return value
    if key == 'shadow':
        return value if isinstance(value, str) and re.fullmatch(r'[a-z0-9\-]{1,64}', value) else '?'
    return clean_text(value, words)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--save', action='store_true', help='Also save under .local/schwab/probes (0600)')
    parser.add_argument('--open-export', action='store_true', help='Press the History Export button first')
    parser.add_argument('--observe', action='store_true', help='Print the recognizer view instead of the probe')
    parser.add_argument('--act', nargs='+', metavar=('KIND ACTION', 'VALUE'),
                        help='Perform one permitted action on the unique control of KIND')
    parser.add_argument('--click', nargs=2, metavar=('INDEX', 'LABEL'),
                        help='Discovery: press probe control INDEX if its masked label is LABEL')
    args = parser.parse_args()
    bridge = FirefoxBridge()
    bridge.path = SOCKET
    ready = bridge.command('ping')
    if not ready.get('ready') or ready.get('provider') != 'schwab':
        raise SystemExit('Select the Schwab tab with the Pacioli toolbar button first')
    bridge.command('prepare')
    try:
        if args.open_export:
            import time
            observed = bridge.command('observe')
            target = [c for c in observed.get('controls', []) if c['kind'] == 'open_export' and c['enabled']]
            if observed.get('kind') != 'history' or len(target) != 1:
                raise SystemExit('History page with one Export button not recognized')
            bridge.command('act', action='open_export', token=observed['token'], target_id=target[0]['id'])
            time.sleep(3)
        if args.act:
            import time
            kind, action, value = args.act[0], args.act[1], (args.act[2] if len(args.act) > 2 else None)
            observed = bridge.command('observe')
            target = [c for c in observed.get('controls', []) if c['kind'] == kind and
                      (value is None or 'value' not in c or c.get('value') == value)]
            if kind == 'export_cancel':
                target = [c for c in target if c.get('label') == 'cancel']
            if len(target) != 1:
                raise SystemExit('No unique {} control'.format(kind))
            bridge.command('act', action=action, token=observed['token'], target_id=target[0]['id'], value=value)
            time.sleep(3)
        if args.click:
            import time
            bridge.command('act', action='discover_click', index=int(args.click[0]), label=args.click[1])
            time.sleep(4)
        if args.observe:
            observed = bridge.command('observe')
            observed.pop('token', None)
            result = sanitize(observed, vocabulary())
        else:
            result = sanitize(bridge.command('probe'), vocabulary())
    finally:
        bridge.command('release')
    output = json.dumps(result, indent=1, sort_keys=True)
    print(output)
    if args.save:
        os.umask(0o077)
        directory = ROOT / '.local/schwab/probes'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / 'probe-{}.json'.format(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        path.write_text(output)
        print('saved', path.name, file=sys.stderr)


if __name__ == '__main__':
    main()
