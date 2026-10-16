# Pacioli's web service. scripts/deploy.py builds it from the committed files
# plus the private wheels in .local/wheels, so the image holds private builds:
# keep it local and never push it to a registry.
ARG PYTHON_IMAGE=python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

FROM ${PYTHON_IMAGE} AS build
LABEL org.opencontainers.image.title=pacioli-web
WORKDIR /src
COPY requirements.txt ./
COPY .local/wheels/ .local/wheels/
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
        --require-hashes -r requirements.txt

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title=pacioli-web
# The same user ID as the checkout's owner: the Chase profile lock and the
# PostgreSQL socket directory are private to that user.
RUN groupadd --gid 1000 pacioli \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /tmp \
        --shell /usr/sbin/nologin pacioli
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY ["manage.py", "Generic Chart of Accounts.csv", "./"]
COPY pacioli/ pacioli/
COPY migrations/ migrations/
COPY scripts/ scripts/
COPY extensions/ extensions/
RUN /opt/venv/bin/python -m compileall -q pacioli scripts migrations
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    pacioli_ENV=Prod
USER 1000:1000
CMD ["gunicorn", "--bind", "127.0.0.1:8001", "--workers", "1", "--threads", "8", \
     "--timeout", "60", "--forwarded-allow-ips=127.0.0.1", "pacioli.wsgi:app"]
