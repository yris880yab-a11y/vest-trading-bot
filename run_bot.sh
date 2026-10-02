#!/bin/sh
cd "$(dirname "$0")" && . .venv/bin/activate && exec python -m vestbot >> bot.log 2>&1
