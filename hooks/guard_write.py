#!/usr/bin/env python3
"""PreToolUse(Edit|Write|MultiEdit|NotebookEdit)。決めた場所の外への書き込みを exit 2 で止める。

guard_delete.py は Bash のコマンドしか見ない。Edit・Write のツールはサンドボックスの外で動くので、
~/.zshrc や ~/Documents の上書きを OS も止めない。その受け皿。

書いてよい場所(決め打ち。「.git があればどこでも」にはしない ── /opt/homebrew や ~/.oh-my-zsh も git リポジトリのため)
- 作業の根(GUARD_WRITE_ROOTS。既定は ~/Projects)の下にある git リポジトリの中。起動した場所には依らない
- 一時ディレクトリ(TMPDIR・tempfile.gettempdir()・/private/tmp)の中
- ~/.claude/projects/*/memory/ と ~/.claude/plans/ の中、~/.claude/skills/ の下の SKILL.md 以外の .md
  (SKILL.md と agents は frontmatter に hook を書けて、hook はサンドボックスの外で動くので止める)
止めるもの(書いてよい場所の中でも)
- .git の中(大文字小文字は区別しない ── macOS の APFS は .GIT も .git として扱う)
- サンドボックスの外で動くものを足せる設定(PROTECTED_NAMES・PROTECTED_DIRS。公式の Protected paths に合わせる)
パスが無い・相対・読めない入力も止める(守りを黙って飛ばさない)。

判定は文字列とパスの実体(realpath)だけで行い、ファイルを書いたり消したりしない。
"""
import json
import os
import sys
import tempfile

TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MSG = "書き込みを止めました(決めた場所の外、または守っている設定ファイル)"
HINT = "この場所のファイルは書き換えず、場所と理由をユーザーに伝える"

# リポジトリの中でも止める(サンドボックスの外で動く hook・MCP・シェルの処理を足せるもの)
PROTECTED_DIRS = {".claude", ".husky", ".githooks", ".vscode", ".idea"}
PROTECTED_NAMES = {".mcp.json", ".envrc", ".gitmodules", ".gitconfig", ".zshrc", ".zprofile", ".zshenv",
                   ".bashrc", ".bash_profile", ".profile"}


def _real(p):
    return os.path.realpath(os.path.expanduser(p))


def _inside(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def default_temp_roots(env):
    out = []
    for r in [env.get("TMPDIR") or "", tempfile.gettempdir(), "/private/tmp"]:
        if not r:
            continue
        rr = _real(r)
        if rr not in out and rr not in ("/", "/private", "/private/var"):
            out.append(rr)
    return out


def default_work_roots(env, home):
    raw = env.get("GUARD_WRITE_ROOTS") or (os.path.join(home, "Projects") if home else "")
    return [_real(r) for r in raw.split(":") if r]


def find_repo_root(path, stop, exists=os.path.exists):
    """path から上へ、stop(作業の根)の手前までたどって .git のある所を返す"""
    p = path
    while _inside(p, stop) and p != stop:
        if exists(os.path.join(p, ".git")):
            return p
        p = os.path.dirname(p)
    return None


def _claude_allowed(r, home_r):
    c = os.path.join(home_r, ".claude")
    parts = os.path.relpath(r, c).split("/") if _inside(r, c) else []
    if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "memory":
        return True
    if len(parts) >= 2 and parts[0] == "plans":
        return True
    if len(parts) >= 3 and parts[0] == "skills" and r.endswith(".md") and parts[-1] != "SKILL.md":
        return True
    return False


def judge(file_path, home, temp_roots, work_roots, exists=os.path.exists, real=_real):
    """問題の一覧を返す(空なら通す)"""
    if not file_path or not isinstance(file_path, str):
        return ["書く先のパスが読めない"]
    if not os.path.isabs(os.path.expanduser(file_path)):
        return [f"書く先が絶対パスでない: {file_path}"]
    r = real(file_path)
    home_r = real(home) if home else None
    parts = [p.casefold() for p in r.split("/")]
    if ".git" in parts:
        return [f".git の中には書かない: {file_path}"]
    for t in temp_roots:
        if _inside(r, t):
            return []
    if home_r and _claude_allowed(r, home_r):
        return []
    for w in work_roots:
        root = find_repo_root(os.path.dirname(r), w, exists)
        if not root:
            continue
        inner = [p.casefold() for p in os.path.relpath(r, root).split("/")]
        # 入れ子(packages/x/.claude/skills など)も読み込まれるので、どの段にあっても止める
        if any(p in PROTECTED_DIRS for p in inner[:-1]) or inner[-1] in PROTECTED_NAMES:
            return [f"サンドボックスの外で動くものを足せる設定なので書かない: {file_path}"]
        return []
    return [f"作業の根の下の git リポジトリの外: {file_path} → {r}"]


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.stderr.write(f"{MSG}: hook への入力の JSON をパースできないので、書く先を確かめられない。止める\n")
        return 2
    if data.get("tool_name") not in TOOLS:
        return 0
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    env = dict(os.environ)
    home = env.get("HOME")
    problems = judge(path, home, default_temp_roots(env), default_work_roots(env, home))
    if problems:
        sys.stderr.write(f"{MSG}: " + " / ".join(problems) + f"。{HINT}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
