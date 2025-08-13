#!/bin/bash
# Run Bot 1
source venv/bin/activate
set -a
source .env.bot1
set +a
python -m src.main