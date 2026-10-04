"""Read bounded Firefox diagnostics captured by a Pacioli worker."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id', type=int)
    arguments = parser.parse_args()
    from pacioli.wsgi import app
    from pacioli.models import ChaseBrowserRuns
    with app.app_context():
        run = ChaseBrowserRuns.query.get(arguments.run_id)
        report = json.loads(run.diagnostics) if run and run.diagnostics else {}
        if report.get('kind') != 'chase_firefox':
            raise SystemExit('This run has no Firefox diagnostic report.')
        print(json.dumps({'run_id': run.id, 'status': run.status,
                          'error_code': run.error_code, 'report': report}, indent=2))


if __name__ == '__main__':
    main()
