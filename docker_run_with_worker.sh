#!/bin/bash

# This runs in the context of the app server Docker image. It runs
# both the server process and the Celery worker process.

/venv/bin/celery -A openakun.tasks worker -l INFO -B &

/venv/bin/openakun_server --host 0.0.0.0 --debug --devel
