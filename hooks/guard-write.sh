#!/bin/bash
# PreToolUse(Edit|Write|MultiEdit|NotebookEdit): 決めた場所の外への書き込みを exit 2 で止める。中身は guard_write.py
# 動く python が無いときは、書く先を確かめられないので止める(守りを黙って飛ばさない)。
input=$(cat)
py=""
for c in /usr/bin/python3 "$(command -v python3 2>/dev/null)"; do
  if [ -n "$c" ] && [ -x "$c" ] && "$c" -c 'pass' >/dev/null 2>&1; then py="$c"; break; fi
done
if [ -z "$py" ]; then
  echo "guard-write: 動く python3 が無いので、書く先を確かめられない。止める" >&2
  exit 2
fi
printf '%s' "$input" | exec "$py" "$(dirname "$0")/guard_write.py"
