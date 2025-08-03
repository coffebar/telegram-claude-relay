---
description: "Format code and run linting checks, fixing issues automatically"
allowed-tools: ["Bash", "TodoWrite"]
---

# Lint and Format Code

Format code and run linting checks for the project, automatically fixing issues where possible.

## Requirements

- Run `make format` to format all Python files with black and isort
- Run `make lint` to check for linting issues with ruff
- Fix any linting issues that can be automatically resolved
- Report any remaining issues that require manual intervention

## Workflow

1. Run `make format` to format all Python files with black and isort
2. Run `./venv/bin/ruff check --fix src/ hooks/ scripts/` to automatically fix linting issues
3. Run `make lint` to verify all issues are resolved
4. Report any remaining issues that require manual intervention

$ARGUMENTS