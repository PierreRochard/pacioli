"""Vendor Pacioli's pinned front-end libraries from the npm registry.

Each package tarball must match the sha512 pinned here (npm's published
dist.integrity) before anything is read from it. The files Pacioli serves are
copied byte for byte into pacioli/static/vendor, and their sha256 digests are
written to manifest.json, which tests/test_vendor_assets.py checks.

To upgrade a library, change its version and integrity, taken from
https://registry.npmjs.org/<name>/<version> (dist.integrity), run this
script, then run make ci, which includes the browser tests.
"""

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'pacioli' / 'static' / 'vendor'
PACKAGES = (
    ('bootstrap', '5.3.8',
     'sha512-HP1SZDqaLDPwsNiqRqi5NcP0SSXciX2s9E+RyqJIIqGo+vJeN5AJVM98CXmW/Wux0nQ5L7jeWUdplCEf0Ee+tg==',
     {'dist/css/bootstrap.min.css': 'bootstrap/bootstrap.min.css',
      'dist/js/bootstrap.bundle.min.js': 'bootstrap/bootstrap.bundle.min.js',
      'LICENSE': 'bootstrap/LICENSE'}),
    ('bootstrap-icons', '1.13.1',
     'sha512-ijombt4v6bv5CLeXvRWKy7CuM3TRTuPEuGaGKvTV5cz65rQSY8RQ2JcHt6b90cBBAC7s8fsf2EkQDldzCoXUjw==',
     {'font/bootstrap-icons.min.css': 'bootstrap-icons/bootstrap-icons.min.css',
      'font/fonts/bootstrap-icons.woff2': 'bootstrap-icons/fonts/bootstrap-icons.woff2',
      'font/fonts/bootstrap-icons.woff': 'bootstrap-icons/fonts/bootstrap-icons.woff',
      'LICENSE': 'bootstrap-icons/LICENSE'}),
    # Not 4.x: daterangepicker 3.1 uses the positional selectors jQuery 4 removed.
    ('jquery', '3.7.1',
     'sha512-m4avr8yL8kmFN8psrbFFFmB/If14iN5o9nw/NgnnM+kybDJpRsAynV2BsfpTYrTRysYUdADVD7CkUUizgkpLfg==',
     {'dist/jquery.min.js': 'jquery/jquery.min.js',
      'LICENSE.txt': 'jquery/LICENSE.txt'}),
    ('select2', '4.1.0',
     'sha512-i9KalWOP4/LRRGc8+rj2krNm0ZqP14cV+j1TRCEBSsOhCPkKH8rYZ2MCRXcgvqIqN+llqGci0hj9aVkCMvL0+g==',
     {'dist/js/select2.min.js': 'select2/select2.min.js',
      'dist/css/select2.min.css': 'select2/select2.min.css',
      'LICENSE.md': 'select2/LICENSE.md'}),
    ('select2-bootstrap-5-theme', '1.3.0',
     'sha512-uEJDruP4tmwyKcs3V0oP7CIsyC45PGF5ddo8unwTp8OucJ1PSuTOBr+xbWaHTQPGnvp7N96psVQ5UBMQvFCcHA==',
     {'dist/select2-bootstrap-5-theme.min.css':
      'select2-bootstrap-5-theme/select2-bootstrap-5-theme.min.css',
      'LICENSE': 'select2-bootstrap-5-theme/LICENSE'}),
    # MIT; the package ships no separate license file, the header carries it.
    ('daterangepicker', '3.1.0',
     'sha512-DxWXvvPq4srWLCqFugqSV+6CBt/CvQ0dnpXhQ3gl0autcIDAruG1PuGG3gC7yPRNytAD1oU1AcUOzaYhOawhTw==',
     {'daterangepicker.js': 'daterangepicker/daterangepicker.js',
      'daterangepicker.css': 'daterangepicker/daterangepicker.css'}),
    ('moment', '2.31.0',
     'sha512-0acOTfMiWOheYS4eoWb80yYMb/JLvVv9SHbs2PehaDzfUG0Bw855SKyk0IKTnPGa5+U2bmi3W68l1+sGLX/pvw==',
     {'min/moment.min.js': 'moment/moment.min.js',
      'LICENSE': 'moment/LICENSE'}),
)


def fetch(name, version, integrity):
    url = 'https://registry.npmjs.org/{0}/-/{0}-{1}.tgz'.format(name, version)
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    digest = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if digest != integrity:
        raise SystemExit('{}@{} does not match its pinned integrity'.format(name, version))
    return tarfile.open(fileobj=io.BytesIO(data))


def main():
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    manifest = {}
    for name, version, integrity, files in PACKAGES:
        with fetch(name, version, integrity) as archive:
            for source, target in files.items():
                content = archive.extractfile('package/' + source).read()
                destination = VENDOR / target
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                manifest[target] = {'package': name, 'version': version,
                                    'sha256': hashlib.sha256(content).hexdigest()}
        print('{}@{}: {} files'.format(name, version, len(files)))
    (VENDOR / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
