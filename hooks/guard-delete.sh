#!/bin/bash
# PreToolUse(Bash): リポジトリと一時フォルダの外を消すコマンドを exit 2 で止める。中身は guard_delete.py
# python は /usr/bin/python3 を先に使う(/usr/local/bin/python3 は古い Intel 版で動かないことがある)。
# 動く python が無いときは、消す処理らしい文字を含むコマンドだけを止める(守りを黙って飛ばさない)。
input=$(cat)
py=""
for c in /usr/bin/python3 "$(command -v python3 2>/dev/null)"; do
  if [ -n "$c" ] && [ -x "$c" ] && "$c" -c 'pass' >/dev/null 2>&1; then py="$c"; break; fi
done
if [ -z "$py" ]; then
  if printf '%s' "$input" | grep -Eq '(^|[^[:alnum:]_])(rm|rmdir|unlink|srm)([^[:alnum:]_]|$)|rmtree|os\.remove|-delete|git[^"]*clean|reset --hard|--delete|(^|[^[:alnum:]_])mv([^[:alnum:]_]|$)|checkout|restore|stash|worktree|branch'; then
    echo "guard-delete: 動く python3 が無いので、消すコマンドの行き先を確かめられない。止める" >&2
    exit 2
  fi
  exit 0
fi
printf '%s' "$input" | exec "$py" "$(dirname "$0")/guard_delete.py"
