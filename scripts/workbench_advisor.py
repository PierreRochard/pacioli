#!/usr/bin/env python
"""Run one local review job. No browser or cloud access."""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pacioli import create_app
from pacioli.functions.accounting_review import run_advisor

if __name__=='__main__':
    os.umask(0o077)
    app=create_app('pacioli.settings.ProdConfig',env='Prod')
    with app.app_context():
        run_advisor(int(sys.argv[1]))
