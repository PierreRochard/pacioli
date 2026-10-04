#!/usr/bin/env python
"""Import original Amazon CSV/ZIP evidence or export all saved accounting CSVs."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pacioli import create_app
from pacioli.extensions import db
from pacioli.functions import amazon_csv


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    load=commands.add_parser('import')
    load.add_argument('path',type=Path)
    load.add_argument('--apply',action='store_true',help='Save encrypted source evidence; otherwise validate and roll back')
    save=commands.add_parser('export')
    save.add_argument('directory',type=Path,help='Private output directory; existing files are never overwritten')
    args=parser.parse_args()
    app=create_app('pacioli.settings.ProdConfig',env='Prod')
    with app.app_context():
        if args.command=='import':
            with args.path.open('rb') as source:raw=source.read(amazon_csv.MAX_UPLOAD+1)
            doc,created=amazon_csv.import_archive(raw,args.path.name,'Owner-requested Amazon CSV import')
            manifest=json.loads(doc.payload)
            print(json.dumps(dict(new_export=created,files=len(manifest['tables']),
                rows=sum(t['rows'] for t in manifest['tables']),unparsed_files=len(manifest['unparsed_members']),
                journals_posted=0,applied=args.apply)))
            if args.apply:db.session.commit()
            else:db.session.rollback()
        else:
            if args.directory.is_symlink():raise SystemExit('Choose a real output directory')
            args.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            if args.directory.stat().st_mode & 0o077:raise SystemExit('Output directory must be private (mode 700)')
            files=amazon_csv.accounting_exports()
            files['pacioli-amazon-evidence.zip']=amazon_csv.export_zip()
            if any((args.directory/name).exists() for name in files):raise SystemExit('An output file already exists')
            for name,content in files.items():
                with (args.directory/name).open('xb') as output:output.write(content)
            print(json.dumps(dict(directory=str(args.directory.resolve()),files=list(files))))


if __name__=='__main__':main()
