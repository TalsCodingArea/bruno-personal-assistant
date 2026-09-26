# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/Capabilities/financial-agent

RUN groupadd --gid 10001 bruno \
    && useradd --uid 10001 --gid bruno --create-home --shell /usr/sbin/nologin bruno

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement /tmp/requirements.txt

COPY --chown=bruno:bruno bruno ./bruno
COPY --chown=bruno:bruno Capabilities ./Capabilities

RUN mkdir -p /app/data/bruno /app/data/finance \
    && chown -R bruno:bruno /app/data

USER bruno

CMD ["python", "-m", "bruno.app"]
