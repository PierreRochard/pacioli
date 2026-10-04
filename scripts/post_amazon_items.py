#!/usr/bin/env python
"""Execute one saved explicit financial approval outside the HTTP request."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli import create_app
from pacioli.functions.amazon_item_posting import run_worker


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: post_amazon_items.py POST_REQUEST_ID')
    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    with app.app_context():
        try:
            run_worker(int(sys.argv[1]))
        except Exception as exc:
            # Safe durable status contains the actionable error category.
            # Exception text or traceback can contain private financial data.
            print('Posting worker stopped: ' + type(exc).__name__, file=sys.stderr)
            raise SystemExit(1) from None
