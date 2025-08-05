#!/bin/bash
# Don't use 'set -e' because we need to handle exit codes ourselves

# Check if virtual environment exists
if [ ! -d "venv" ]; then
	echo "❌ Virtual environment not found. Run ./setup.sh first."
	exit 1
fi

# Store PID of child process
CHILD_PID=""

# Cleanup function - forward signals to child
cleanup() {
	if [ -n "$CHILD_PID" ]; then
		echo -e "\n🛑 Forwarding shutdown signal to bot..."
		kill -TERM "$CHILD_PID" 2> /dev/null || true
		wait "$CHILD_PID" 2> /dev/null || true
	fi
	echo "👋 Wrapper script exiting"
	exit 0
}

# Trap termination signals
trap cleanup SIGHUP SIGINT SIGTERM

# Activate virtual environment
source venv/bin/activate
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

echo "🤖 Starting Claude Code Telegram Bot with auto-restart support..."

# Main loop - restart on exit code 42
while true; do
	# Run bot in background and capture PID
	python src/main.py &
	CHILD_PID=$!

	# Wait for bot to finish
	wait "$CHILD_PID"
	EXIT_CODE=$?
	CHILD_PID="" # Clear PID after process exits

	echo "🔍 Bot exited with code: $EXIT_CODE"

	if [ $EXIT_CODE -eq 42 ]; then
		echo "🔄 Self-update requested (exit code 42)..."
		echo "📥 Pulling latest changes from GitHub..."
		git pull --rebase --autostash
		echo "✅ Repository updated"
		echo "🔄 Restarting bot in 2 seconds..."
		sleep 2
	elif [ $EXIT_CODE -eq 0 ]; then
		echo "✅ Bot stopped normally"
		break
	elif [ $EXIT_CODE -eq 130 ] || [ $EXIT_CODE -eq 143 ]; then
		# 130 = SIGINT (Ctrl+C), 143 = SIGTERM
		echo "✅ Bot stopped by signal"
		break
	else
		echo "❌ Bot exited with error code $EXIT_CODE"
		echo "💡 Tip: Check logs with 'make logs' for details"
		break
	fi
done