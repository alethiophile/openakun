# syntax=docker/dockerfile:1.5

FROM python:3.12-slim-bookworm AS base

ENV PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PYTHONUNBUFFERED=1 \
    POETRY_HOME=/opt/poetry

WORKDIR /app

FROM base AS builder

RUN apt-get update && apt-get -y upgrade && \
    apt-get -y install curl gcc libpq-dev git procps
RUN curl -sSL https://install.python-poetry.org | python3 - --version 2.1.1

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH" \
    VIRTUAL_ENV=/venv

COPY --link poetry.lock pyproject.toml /app

RUN /opt/poetry/bin/poetry install --no-interaction --no-ansi --no-root

COPY --link . /app
RUN rm -f openakun/.flake8 && /opt/poetry/bin/poetry build && pip install dist/*.whl
# RUN /opt/poetry/bin/poetry install --no-ansi

FROM base AS prod
RUN apt-get update && apt-get -y upgrade && \
    apt-get -y install libpq5
COPY --from=builder --link /venv /venv
COPY --from=builder --link /app/dist/*.whl /tmp
RUN /venv/bin/pip install /tmp/*.whl && rm -f /tmp/*
STOPSIGNAL INT
CMD ["/venv/bin/openakun_server", "--host", "0.0.0.0"]

FROM base AS dev
RUN apt-get update && apt-get -y upgrade && \
    apt-get -y install libpq5
WORKDIR /app
COPY --from=builder --link /venv /venv
COPY --from=builder --link /app /app
COPY --from=builder --link /opt/poetry /opt/poetry
ENV PATH="/venv/bin:$PATH" \
    VIRTUAL_ENV=/venv
RUN /opt/poetry/bin/poetry install
STOPSIGNAL SIGINT
COPY docker_run_with_worker.sh /app
# CMD ["/venv/bin/openakun_server", "--host", "0.0.0.0", "--debug", "--devel"]
CMD ["bash", "/app/docker_run_with_worker.sh"]
