.PHONY: help env deps lock upgrade-deps audit ofx prove test ci deploy db-init db-start db-stop db-status db-backup lan-backup

# The interpreter in .python-version, installed by uv; set PYTHON to override.
PYTHON ?=
VENV ?= .venv

help:
	@echo "env   create a development environment on the Python in .python-version"
	@echo "deps  install the hashed development lock into the environment"
	@echo "lock  re-resolve the hashed locks after editing requirements*.in"
	@echo "upgrade-deps  move every locked dependency to its latest release"
	@echo "audit check the installed environment for known vulnerabilities"
	@echo "ofx   build the SPARK OFX program in ofx/ with Alire's GNAT"
	@echo "prove prove the SPARK OFX program with GNATprove"
	@echo "test  run the PostgreSQL test suite"
	@echo "ci    run all local checks with disposable PostgreSQL"
	@echo "deploy  back up, sync .venv, build the web image, migrate and restart pacioli-web"
	@echo "db-init   create or update the persistent local Pacioli database"
	@echo "db-start  start the persistent local PostgreSQL cluster"
	@echo "db-stop   stop the persistent local PostgreSQL cluster"
	@echo "db-status show local PostgreSQL status"
	@echo "db-backup save a private PostgreSQL dump under .local/backups"
	@echo "lan-backup  store an encrypted restic snapshot on the LAN backup host"

env:
	uv python install
	$(or $(PYTHON),$$(uv python find --system)) install.py --dev --venv $(VENV)

deps:
	$(VENV)/bin/python -m pip install --require-hashes -r requirements-dev.txt

LOCK = uv pip compile --python-version 3.14 --generate-hashes --quiet

lock:
	$(LOCK) requirements.in -o requirements.txt
	$(LOCK) requirements-dev.in -o requirements-dev.txt

upgrade-deps:
	$(LOCK) --upgrade requirements.in -o requirements.txt
	$(LOCK) --upgrade requirements-dev.in -o requirements-dev.txt

audit:
	uvx pip-audit --path $(VENV)/lib/python3.14/site-packages

# Alire supplies GNAT and GNATprove (see ofx/README.md).
ALR = alr --non-interactive -C ofx exec --

ofx:
	$(ALR) gprbuild -P pacioli_ofx.gpr -j0 -p -q

prove:
	$(ALR) gnatprove -P pacioli_ofx.gpr -U -j0

test:
	$(VENV)/bin/python scripts/ci_local.py

ci:
	$(VENV)/bin/python scripts/ci_local.py

deploy:
	$(VENV)/bin/python scripts/deploy.py

db-init:
	$(VENV)/bin/python scripts/local_db.py init
	$(VENV)/bin/python manage.py db upgrade
	$(VENV)/bin/python manage.py populate_chart_of_accounts
	$(VENV)/bin/python manage.py accounting_check

db-start:
	$(VENV)/bin/python scripts/local_db.py start

db-stop:
	$(VENV)/bin/python scripts/local_db.py stop

db-status:
	$(VENV)/bin/python scripts/local_db.py status

db-backup:
	$(VENV)/bin/python scripts/local_db.py backup

lan-backup:
	$(VENV)/bin/python scripts/lan_backup.py run
