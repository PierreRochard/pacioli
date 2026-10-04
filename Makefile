.PHONY: help env deps test ci db-init db-start db-stop db-status db-backup

PYTHON ?= python3.11
VENV ?= .venv

help:
	@echo "env   create a Python 3.11 development environment"
	@echo "deps  refresh development dependencies"
	@echo "test  run the PostgreSQL test suite"
	@echo "ci    run all local checks with disposable PostgreSQL"
	@echo "db-init   create or update the persistent local Pacioli database"
	@echo "db-start  start the persistent local PostgreSQL cluster"
	@echo "db-stop   stop the persistent local PostgreSQL cluster"
	@echo "db-status show local PostgreSQL status"
	@echo "db-backup save a private PostgreSQL dump under .local/backups"

env:
	$(PYTHON) install.py --dev --venv $(VENV)

deps:
	$(VENV)/bin/python -m pip install -r requirements-dev.txt

test:
	$(VENV)/bin/python scripts/ci_local.py

ci:
	$(VENV)/bin/python scripts/ci_local.py

db-init:
	$(VENV)/bin/python scripts/local_db.py init
	$(VENV)/bin/python manage.py createdb
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
