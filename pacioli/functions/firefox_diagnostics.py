"""Bounded, encrypted evidence from the worker's own Firefox tab.

Only fixed error phrases, navigation categories and stages are retained. Never
record arbitrary page text, credentials, URL paths or browser session handles.
"""

import json
import time

from pacioli.extensions import db
from pacioli.models import ChaseBrowserRuns


class FirefoxDiagnostics:
    def __init__(self, run_id):
        self.run_id = run_id
        self.started = time.monotonic()
        self.report = {'version': 1, 'kind': 'chase_firefox', 'events': []}

    def record(self, event, **details):
        events = self.report['events']
        record = dict(event=event, **details)
        if events and {k: v for k, v in events[-1].items() if k != 'elapsed_ms'} == record:
            return
        record['elapsed_ms'] = round((time.monotonic() - self.started) * 1000)
        events.append(record)
        del events[:-40]
        run = db.session.get(ChaseBrowserRuns, self.run_id)
        run.diagnostics = json.dumps(self.report, separators=(',', ':'))
        db.session.commit()
