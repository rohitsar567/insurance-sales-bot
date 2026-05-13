#!/bin/sh
# Run once on a fresh clone to enable the eval-regression pre-commit gate.
# Replaces the .github/workflows CI we can't push (PAT scope limitation).

set -e
cd "$(git rev-parse --show-toplevel)"
mkdir -p .git/hooks
cp tools/git-hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo "✓ Installed pre-commit eval gate. Subsequent commits will run eval --limit 10 if you touch backend/, rag/, or eval/ files."
echo "  Block threshold: factual_accuracy ≥ 0.55"
echo "  Override: git commit --no-verify"
