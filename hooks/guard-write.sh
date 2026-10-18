#!/bin/bash
# PreToolUse(Edit|Write|MultiEdit|NotebookEdit): 決めた場所の外への書き込みを exit 2 で止める。中身は guard_write.py
# 動く python が無いときは、書く先を確かめられないので止める(守りを黙って飛ばさない)。
# python が 0・2 以外で終わったとき(構文エラー・読み込みの失敗・強制終了など)も止める。
# exit 1 などは公式では「ブロックしないエラー」で、書き込みがそのまま動いてしまうため。
input=$(cat)
py=""
for c in /usr/bin/python3 "$(command -v python3 2>/dev/null)"; do
  if [ -n "$c" ] && [ -x "$c" ] && "$c" -c 'pass' >/dev/null 2>&1; then py="$c"; break; fi
done
if [ -z "$py" ]; then
  echo "guard-write: 動く python3 が無いので、書く先を確かめられない。止める" >&2
  exit 2
fi
printf '%s' "$input" | "$py" "$(dirname "$0")/guard_write.py"
st=$?
if [ "$st" -ne 0 ] && [ "$st" -ne 2 ]; then
  echo "guard-write: 確かめる python が異常な終わり方をした(終了コード $st)ので、止める" >&2
  exit 2
fi
exit "$st"
