---
description: "Commit uncommitted changes following project standards with logical grouping"
allowed-tools: ["Bash", "TodoWrite"]
---

# Git Commit Following Project Standards

Commit uncommitted changes following project standards:

## Requirements

- Use format: "type: description" (lowercase type, concise description)
- Types: docs, feat, fix, test, enhance, refactor
- Balance: 1-5 logical commits (avoid both single massive commit and excessive fragmentation)

## Workflow

1. Run git status and git diff to understand changes
2. Group changes into 2-4 logical commits:
   - Core documentation updates (CLAUDE.md files + critical docs)
   - Code implementation changes (src/ files + related tests)
   - Supporting documentation (general docs/ updates)
   - Project planning/structure updates (PLANS.md, configs)
3. Stage and commit each group with appropriate type prefix
4. Follow project's commit message style from git log

$ARGUMENTS
