#!/usr/bin/env python
"""Run one resumable local item proposal job; never posts journals."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli import create_app
from pacioli.functions.amazon_item_workflow import run_worker

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: classify_amazon_items.py RUN_ID')
    app = create_app('pacioli.settings.ProdConfig', env='Prod')
    with app.app_context():
        run_worker(int(sys.argv[1]))
