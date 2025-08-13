#!/bin/bash
# Run Bot 3
source venv/bin/activate
set -a
source .env.bot3
set +a
python -m src.main