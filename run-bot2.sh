#!/bin/bash
# Run Bot 2
source venv/bin/activate
set -a
source .env.bot2
set +a
python -m src.main