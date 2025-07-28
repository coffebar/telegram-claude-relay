#!/bin/bash
set -e

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

# Activate virtual environment and run bot
echo "🤖 Starting Claude Code Telegram Bot..."
source venv/bin/activate
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
python src/main.py