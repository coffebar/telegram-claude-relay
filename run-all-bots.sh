#!/bin/bash
# Run all Telegram bots for Claude Code monitoring

echo "Starting all Telegram bots..."

# Start Bot 1 (ai-llm-infra)
echo "Starting Bot 1 (ai-llm-infra)..."
./run-bot1.sh &
BOT1_PID=$!

# Start Bot 2 (yabai)
echo "Starting Bot 2 (yabai)..."
./run-bot2.sh &
BOT2_PID=$!

# Start Bot 3 (aws_deploy)
echo "Starting Bot 3 (aws_deploy)..."
./run-bot3.sh &
BOT3_PID=$!

echo "All bots started with PIDs:"
echo "  Bot 1: $BOT1_PID"
echo "  Bot 2: $BOT2_PID"
echo "  Bot 3: $BOT3_PID"

# Wait for all background processes
wait $BOT1_PID $BOT2_PID $BOT3_PID