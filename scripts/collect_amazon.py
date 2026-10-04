#!/usr/bin/env python
"""Collect a bounded on-demand Amazon receipt batch; no journal posting."""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pacioli import create_app
from pacioli.functions.amazon_collection import collect
if __name__=='__main__':
    os.umask(0o077)
    app=create_app('pacioli.settings.ProdConfig',env='Prod')
    with app.app_context():collect(int(sys.argv[1]))
