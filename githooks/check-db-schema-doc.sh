#!/bin/sh
# PostToolUse hook: after an Edit/Write to src/db.py, warn (don't block) if
# src/db.py now contains a "CREATE TABLE IF NOT EXISTS <name>" whose name
# isn't mentioned anywhere in CLAUDE.md. Same string-match logic as
# githooks/pre-commit, just non-blocking and triggered mid-session instead
# of at commit time.

file_path=$(python -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)

case "$file_path" in
    *db.py) ;;
    *) exit 0 ;;
esac

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
claude_md="$repo_root/CLAUDE.md"
[ -f "$claude_md" ] || exit 0
[ -f "$file_path" ] || exit 0

tables=$(grep -oE 'CREATE TABLE IF NOT EXISTS [A-Za-z_]+' "$file_path" | awk '{print $NF}')
[ -z "$tables" ] && exit 0

for t in $tables; do
    grep -q "$t" "$claude_md" || echo "db.py schema changed - table '$t' not yet mentioned in CLAUDE.md" >&2
done

exit 0
