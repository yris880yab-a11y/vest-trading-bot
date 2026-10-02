#!/bin/sh
cd "$(dirname "$0")" && . .venv/bin/activate && VESTBOT_ENV_FILE=.env.gc exec python -m vestbot >> bot_gc.log 2>&1
