#!/usr/bin/env python3
"""PreToolUse(Bash)。リポジトリと一時フォルダの外を消す・動かすコマンドを exit 2 で止める(全体用)。

2026-10-07 に、Claude Code の auto で動かしていたあいだにホームの下がまとめて消えたあとに足した守り。
エージェントが書き換えられないように、~/.claude/hooks/ へは人が手で置く(サンドボックスと deny の Edit(...) で書けなくする)。

消してよい場所
- **コマンドを走らせる場所(cwd。cd で移ればそのあとの場所)を含む git リポジトリ**の「中」。根そのものと .git は
  消さない。CLAUDE_PROJECT_DIR は使わない ── 親のフォルダ(~/Projects など)で起動すると、その下が全部「中」になるため。
  ホームそのものやその上はリポジトリと見なさない
- 一時フォルダ(TMPDIR・tempfile.gettempdir()・/private/tmp)の中
それ以外(ホーム・~/Projects・~/.claude・/ など)を消そうとしたら止める。行き先が分からない
(知らない変数・$(...)・cd - のあとの相対パス・xargs rm など)ときも止める。

見るもの
- rm・rmdir・unlink・srm の行き先(sudo・env・command・nohup・time・nice・timeout・xargs の後ろも)
- find … -delete / -exec rm の探す場所、rsync --delete の行き先
- git clean -f・git reset --hard・git checkout -- <パス>・git checkout .・git restore(--staged だけは通す)・
  git stash drop/clear・git worktree remove --force・git branch -D(まだコミットしていない物や、どこにも無いコミットが消える)
- mv の、動かす元と行き先(どちらかが外なら止める)
- bash -c・sh -c・zsh -c・eval の中身、bash・sh・python に渡す heredoc の中身
- python -c と python の heredoc の shutil.rmtree・os.remove・os.unlink・os.rmdir・Path.unlink・Path.rmdir
  (文字列のパス・Path.home()・expanduser・HOME を見る。mkdtemp・TemporaryDirectory は一時フォルダとみなす)
- 消す処理を持つスクリプト(python・sh のファイル)に、外のパス(/… ・~…)を引数で渡すとき
- cd で動いたあとの相対パス、同じコマンドの中の変数の代入(D=$TMPDIR/x; rm -rf "$D")
- サンドボックスの外で動かすコマンド(excludedCommands に入れる git push・xcodebuild・xcrun simctl・gh・Tools/test.sh)
  - git push・xcodebuild・Tools/test.sh は、作業の根(GUARD_WRITE_ROOTS。既定は ~/Projects)の直下のリポジトリ
    (とその worktree)の中でだけ通す。ほかの場所のリポジトリは .git/hooks をサンドボックスの中から書けるため
  - git push の行き先がパス・file:・ext:: のとき、--repo、GIT_* の環境変数、短いフラグの -f・+ の refspec は止める
  - xcrun simctl spawn と、simctl io の書き出し先が外のときは止める
  - gh の delete・archive・rename・repo edit・gist・secret・variable・auth token などと、外のパス(リンクの先を含む)を渡す呼び方は止める
- リポジトリの中でも変更やコミットを捨てる git(switch -f・checkout <rev> <パス>・rm -f・update-ref -d・reflog expire・
  gc --prune)、git -c alias.x='!…'
- env -S・arch・script・xcrun の後ろのコマンド
- 2026-10-19 に足したもの
  - perl・ruby・node・bun・deno eval・awk・swift・php・lua・osascript の -e(-E・--eval・-p・-r など)の中身と
    ヒアドキュメント: 解析せず、消す・動かす・コマンドを動かす語(rmtree・rmSync・rm_rf・unlink・rmdir・remove・
    system・exec・`…` など)があれば止める。perl -e 'alarm N; exec @ARGV' は後ろのコマンドを見る
  - python の消す処理の行き先が文字列のパスでない(os.path.join・連結・f 文字列・ループの変数など)ときは止める
  - 消す対象がシンボリックリンク(か途中がリンク)なら、リンクの先でも判定する。同じコマンドの中で ln -s で
    外を指すリンクを作り、消す・動かす処理もあるものは止める
  - 判定の途中で落ちたら exit 2(exit 1 は公式では非ブロック)。guard-delete.sh も python の異常終了を 2 にする
  - <<'EOF' の本文(シェルが展開しない)の中の $(…)・`…` はコマンドとして見ない

止めないもの: 読むだけのコマンド、リポジトリの中の rm・mv、一時フォルダの中の rm・mv。
判定は文字列だけで行い、ファイルを消したり書いたりしない(読むのは、消す処理を持つかを見るスクリプトと、
gh に渡すパスのリンクの先、worktree の .git ファイルだけ)。
"""
import json
import os
import re
import shlex
import sys
import tempfile

MSG = "消す・動かす処理か、サンドボックスの外へ出る処理を止めました(リポジトリと一時フォルダの外、または行き先が分からない)"
HINT = ("リポジトリの中なら、そのリポジトリへ cd してから。外のものは消さず・動かさず、場所と理由をユーザーに伝える。"
        "消す処理のテストは一時フォルダの中だけで、D=$(mktemp -d) のあと \"${D:?}\" の形で使う(作るのに失敗したら止まる)")

# 消す処理が入っていそうなコマンドの文字(python が無いときに、止めるかどうかを決める目安にも使う)
DELETE_HINT_RE = re.compile(
    r"\brm\b|\brmdir\b|\bunlink\b|\bsrm\b|rmtree|os\.remove|\.unlink\(|\.rmdir\(|removedirs|"
    r"-delete\b|git\s+(?:-\S+\s+)*clean|reset\s+--hard|--delete|--remove-source-files|"
    r"\bmv\b|\bcheckout\b|\brestore\b|\bstash\b|\bworktree\b|\bbranch\b"
)


def delete_hint(cmd):
    """消す処理らしい文字を含むか。r\\m や "r""m" のような書き方でも当たるよう、引用符と \\ を外した文字にも当てる"""
    return bool(DELETE_HINT_RE.search(cmd) or DELETE_HINT_RE.search(re.sub(r"[\\'\"]", "", cmd)))


def _risky(cmd):
    """消す処理か、ほかの言語で消す・コマンドを動かす処理らしい文字を含むか(読めないときに止めるかの目安)"""
    return delete_hint(cmd) or bool(INTERP_DELETE_RE.search(cmd))


ANSI_C_RE = re.compile(r"\$'((?:\\.|[^'\\])*)'")


def decode_ansi_c(cmd):
    """$'\\x72m' のような ANSI-C の引用を、ふつうの引用に戻す(読めないものは消す処理として疑う側に残す)"""
    def sub(m):
        try:
            text = m.group(1).encode("latin-1", "backslashreplace").decode("unicode_escape")
        except Exception:
            return m.group(0)
        return shlex.quote(text)
    return ANSI_C_RE.sub(sub, cmd)


PY_DELETE_RE = re.compile(
    r"shutil\.rmtree\s*\(|\brmtree\s*\(|os\.remove\s*\(|os\.unlink\s*\(|os\.rmdir\s*\(|os\.removedirs\s*\(|"
    r"\.unlink\s*\(|\.rmdir\s*\(|send2trash\s*\("
)
SH_DELETE_RE = re.compile(r"(?:^|[\s;&|(`])(?:sudo\s+)?(?:/bin/)?rm\s+-[a-zA-Z]*[rR]|rm\s+-[a-zA-Z]*[rR]|\brmtree\b")
PY_HOME_RE = re.compile(r"Path\.home\(\)|expanduser\(|environ\s*\[\s*['\"]HOME['\"]\s*\]|"
                        r"environ\.get\(\s*['\"]HOME['\"]|getenv\(\s*['\"]HOME['\"]|Path\(\s*['\"]~")
PY_TEMP_RE = re.compile(r"mkdtemp\(|TemporaryDirectory\(|gettempdir\(|NamedTemporaryFile\(")
STR_LIT_RE = re.compile(r"""(?:[rRbBuUfF]{0,2})(['"])((?:\\.|(?!\1).)*)\1""")
HEREDOC_RE = re.compile(r"(?<![<\w])<<(-?)[ \t]*(['\"]?)([A-Za-z_][\w.-]*)\2")
HEREDOC_TOKEN_RE = re.compile(r"__HEREDOC_(\d+)__")
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# ${D:?} の形(空なら止まる)を見分けるため、:? と ? を2つめの組で取る
VAR_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(:?\?[^}]*)?\}|([A-Za-z_][A-Za-z0-9_]*))")
BRACE_RE = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
MKTEMP_RE = re.compile(r"\s*mktemp\b")
GLOB_CHARS = set("*?[")
PUNCT = set(";&|()<>")
OPERATORS = {";", "&&", "||", "|", "&", "|&", ";;", ";&", "(", ")", ">", ">>", "<", ">&", "&>", "<&", ">|", "&>>",
             "<>", "<<", "<<<"}

SEPARATORS = {";", "&&", "||", "|", "&", "|&", ";;", ";&", "(", ")", "{", "}", "!"}
RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "in", "case", "esac", "function"}
REDIRECTS = {">", ">>", "<", ">&", "&>", "<&", ">|", "&>>", "<>"}
WRAPPERS = {"sudo", "env", "command", "builtin", "exec", "nohup", "time", "nice", "caffeinate", "timeout",
            "gtimeout", "doas", "stdbuf", "arch"}
OUTSIDE_SCRIPTS = {"Tools/test.sh"}  # excludedCommands に入れて、サンドボックスの外で動かすスクリプト
GH_BLOCK_SUB = {"delete", "archive", "rename", "unarchive"}
GH_BLOCK_GROUP = {"gist", "secret", "variable", "ssh-key", "gpg-key", "alias", "extension"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
RM_NAMES = {"rm", "rmdir", "unlink", "srm"}
UNKNOWN_CMD = "<コマンド名が分からない>"
PYTHONS_RE = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
UNKNOWN = "<不明>"
CMDSUB = "__CMDSUB_{}__"
CMDSUB_RE = re.compile(r"__CMDSUB_(\d+)__")
FIND_TESTS = {"-name", "-iname", "-path", "-ipath", "-regex", "-iregex", "-newer", "-mtime", "-mmin",
              "-size", "-type", "-empty", "-user", "-perm", "-wholename", "-lname"}


def _real_git_dir(d):
    """シンボリックリンクでない本物の .git ディレクトリで、commondir で別の場所へ向けていないか"""
    return os.path.isdir(d) and not os.path.islink(d) and not os.path.lexists(os.path.join(d, "commondir"))


def trusted_git(r, work_roots):
    """r が作業の根の直下の本物のリポジトリか、その worktree か。偽の .git(ファイル・リンク・commondir)ではかわせない"""
    g = os.path.join(r, ".git")
    if os.path.dirname(r) in work_roots and _real_git_dir(g):
        return True
    if not os.path.isfile(g) or os.path.islink(g):
        return False
    try:
        line = open(g).read().strip()
    except OSError:
        return False
    if not line.startswith("gitdir:"):
        return False
    gd = os.path.join(r, line[len("gitdir:"):].strip())
    if os.path.islink(gd) or not os.path.isdir(gd):
        return False
    gd = _real(gd)
    main = None
    for w in work_roots:
        m = re.fullmatch(re.escape(w) + r"/([^/]+)/\.git/worktrees/[^/]+", gd)
        if m:
            main = os.path.join(w, m.group(1), ".git")
    if not main or not _real_git_dir(main):
        return False
    try:
        common = open(os.path.join(gd, "commondir")).read().strip()
    except OSError:
        return False
    return _real(os.path.join(gd, common)) == _real(main)


class Ctx:
    """判定に使う場所(テストでは偽の場所を渡す)"""

    def __init__(self, project_dir, home, temp_roots, cwd, env=None, repo_of=None):
        self.fixed_project = _real(project_dir) if project_dir else None
        self.repo_of = repo_of
        self.home = _real(home) if home else None
        self.temps = [_real(t) for t in temp_roots if t]
        self.cwd = _real(cwd) if cwd else None
        self.env = dict(env or {})
        self.vars = {}
        self.mktemp_vars = set()  # D=$(mktemp -d) で決めた変数。失敗すると空になる
        self.cmdsubs = []
        self.problems = []
        self.links = {}  # 同じコマンドの中で ln -s で作るリンク(リンクの場所 → 指す先)
        self.full_cmd = ""
        raw = self.env.get("GUARD_WRITE_ROOTS") or (os.path.join(home, "Projects") if home else "")
        self.work_roots = [_real(r) for r in raw.split(":") if r]
        self.dev_dir = _real(os.path.join(home, "Library/Developer")) if home else None

    def trusted_repo(self):
        """いまの場所のリポジトリが、作業の根の直下(か、その worktree)か。.git/hooks を
        サンドボックスの中から書けない場所でだけ、サンドボックスの外で動くコマンドを通す"""
        r = self.project
        if not r:
            return False
        if self.repo_of is not None and getattr(self.repo_of, "fake", False):
            return os.path.dirname(r) in self.work_roots  # テストの偽の場所(ファイルは無い)
        return trusted_git(r, self.work_roots)

    @property
    def project(self):
        """いまの場所を含むリポジトリ(repo_of があれば cd のたびに引き直す)。分からなければ None"""
        if self.repo_of:
            if not self.cwd:
                return None
            r = self.repo_of(self.cwd)
            return _real(r) if r else None
        return self.fixed_project

    def roots(self):
        return [r for r in [self.project] + self.temps if r]


def _real(p):
    return os.path.realpath(os.path.normpath(p))


def _inside(path, root, strict):
    if path == root:
        return not strict
    return path.startswith(root.rstrip("/") + "/")


# ---- パスの判定 ----

def expand(word, ctx, whole_ok=True):
    """~ と変数を広げる。広げられないものがあれば None

    mktemp は失敗すると空を返す。$(mktemp -d) と、それを入れた変数は、語の全体として使うとき
    (rm -rf "$D" は空なら何も消さない)か、${D:?} の形のときだけ一時フォルダの中とみなす。
    "$D"/* や cd "$D" は、失敗すると / やいまの場所を指すので、行き先が分からないとする(whole_ok=False)。
    """
    if CMDSUB_RE.search(word):
        m = CMDSUB_RE.search(word)
        body = ctx.cmdsubs[int(m.group(1))]
        if whole_ok and MKTEMP_RE.match(body) and ctx.temps and CMDSUB_RE.fullmatch(word.strip('"')):
            return os.path.join(ctx.temps[0], "mktemp.XXXXXX")
        return None
    if "`" in word:
        return None
    if word == "~" or word.startswith("~/"):
        if not ctx.home:
            return None
        word = ctx.home + word[1:]
    elif word.startswith("~"):
        return None  # ~user
    unknown = []

    def sub(m):
        name = m.group(1) or m.group(3)
        guarded = bool(m.group(2))
        if name in ctx.mktemp_vars and not guarded and not (whole_ok and VAR_RE.fullmatch(word)):
            unknown.append(name)
            return ""
        if name in ctx.vars:
            return ctx.vars[name]
        if name == "HOME" and ctx.home:
            return ctx.home
        if name == "PWD" and ctx.cwd:
            return ctx.cwd
        if name == "CLAUDE_PROJECT_DIR" and ctx.fixed_project:
            return ctx.fixed_project
        if name == "TMPDIR" and ctx.temps:
            return ctx.temps[0]
        if name in ctx.env and name not in ("OLDPWD",):
            return ctx.env[name]
        unknown.append(name)
        return ""

    out = VAR_RE.sub(sub, word)
    if unknown or "$" in out:
        return None
    return out


def resolve(path, ctx, follow_last=False):
    """パスの実体。途中のリンクはたどる。最後の名前は、follow_last か末尾の / のときだけたどる"""
    if not os.path.isabs(path):
        if not ctx.cwd:
            return None
        path = os.path.join(ctx.cwd, path)
    trailing = path.endswith("/")
    path = os.path.normpath(path)
    base = os.path.basename(path)
    follow = follow_last or trailing or base in ("", ".", "..")
    out = _real(path) if follow else os.path.join(_real(os.path.dirname(path)), base)
    return _through_links(out, ctx, follow)


def _through_links(r, ctx, follow):
    """同じコマンドの中で ln -s で作ったリンクを通るパスを、リンクの指す先に置き換える
    (まだ作られていないので realpath ではたどれない)"""
    for _ in range(8):
        for link, target in ctx.links.items():
            if _inside(r, link, strict=True):
                r = os.path.normpath(target + r[len(link):])
                break
            if follow and r == link:
                r = target
                break
        else:
            return r
    return r


def link_target(r, ctx):
    """r がリンク(本物か、同じコマンドの中で作るもの)なら、指す先の実体を返す"""
    if r in ctx.links:
        return ctx.links[r]
    if os.path.islink(r):
        return _real(r)
    return None


def judge_target(word, ctx, kind="rm", recursive=True):
    """消す行き先1つを判定する。問題があれば ctx.problems に足す"""
    if BRACE_RE.search(word):
        ctx.problems.append(f"行き先が分からない(ブレース展開): {word}")
        return
    p = expand(word, ctx)
    if p is None:
        ctx.problems.append(f"行き先が分からない: {word}")
        return
    if any(c in GLOB_CHARS for c in p):
        cut = min(i for i, c in enumerate(p) if c in GLOB_CHARS)
        head = p[:cut]
        d = head if head.endswith("/") or head == "" else os.path.dirname(head)
        d = resolve(d or ".", ctx, follow_last=True)
        if d is None:
            ctx.problems.append(f"行き先が分からない: {word}")
            return
        _judge_dir_children(word, d, ctx, recursive, kind_glob=True)
        return
    dir_kind = kind.startswith("find") or kind == "rsync"
    r = resolve(p, ctx, follow_last=dir_kind)  # find -L・rsync の行き先はリンクの先を消す
    if r is None:
        ctx.problems.append(f"行き先が分からない(cd のあと): {word}")
        return
    if dir_kind:
        filtered = {"find-filtered": True, "find-keeps-git": "keeps-git"}.get(kind, False)
        _judge_dir_children(word, r, ctx, recursive, filtered=filtered)
        return
    _judge_path(word, r, ctx)
    t = link_target(r, ctx)
    if t is not None and t != r:
        # 対象がリンクなら、指す先でも判定する(rm -r link/ や、あとで中身を消す形でリンクの先が消えるため)
        n = len(ctx.problems)
        _judge_path(word, t, ctx)
        if len(ctx.problems) > n:
            ctx.problems[-1] = f"リンクの先が外: {word} → {t}"


def _git_dir_problem(r, ctx):
    if not ctx.project:
        return False
    # macOS の APFS は大文字小文字を区別しないので、.GIT・.Git も .git として比べる
    g = os.path.join(ctx.project, ".git").casefold()
    rc = r.casefold()
    if rc == g:
        return True
    if _inside(rc, g, strict=True) and not rc.endswith(".lock"):
        return True
    return False


def _judge_path(word, r, ctx):
    if _git_dir_problem(r, ctx):
        ctx.problems.append(f".git の中は消さない: {word}")
        return
    for root in ctx.roots():
        if _inside(r, root, strict=True):
            return
    if r in ctx.roots():
        ctx.problems.append(f"リポジトリや一時フォルダの根そのもの: {word} → {r}")
    else:
        ctx.problems.append(f"外の場所: {word} → {r}")


def _judge_dir_children(word, d, ctx, recursive, filtered=False, kind_glob=False):
    """d の下を消すもの(グロブ・find・rsync --delete)"""
    if _git_dir_problem(d, ctx):
        ctx.problems.append(f".git の中は消さない: {word}")
        return
    if ctx.project and d == ctx.project:
        if recursive and not filtered:
            ctx.problems.append(f"リポジトリの根の下を丸ごと消す形: {word}")
        elif filtered == "keeps-git":
            ctx.problems.append(f"リポジトリの根から find で消すと .git の中も当たる"
                                f"(-not -path './.git/*' で外すか、-name で消すものを絞る): {word}")
        return
    for root in ctx.temps:
        if d == root and recursive and not filtered and kind_glob:
            ctx.problems.append(f"一時フォルダの根の下を丸ごと消す形: {word}")
            return
    for root in ctx.roots():
        if _inside(d, root, strict=False):
            return
    ctx.problems.append(f"外の場所: {word} → {d}")


# ---- コマンドを読む ----

def _literal_heredoc_ranges(cmd):
    """<<'EOF'・<<"EOF" の本文(シェルが展開しない所)の (始め, 終わり) の一覧"""
    ranges, pos = [], 0
    lines = cmd.split("\n")
    k = 0
    offsets = []
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    while k < len(lines):
        marks = [m for m in HEREDOC_RE.finditer(lines[k])]
        k += 1
        for m in marks:
            dash, quoted, word = m.group(1), m.group(2), m.group(3)
            start = offsets[k] if k < len(lines) else pos
            stop = pos  # 終わりの行が無ければ、最後まで本文
            while k < len(lines):
                end = lines[k].lstrip("\t") if dash else lines[k]
                k += 1
                if end.strip() == word:
                    stop = offsets[k - 1]
                    break
            if quoted and stop > start:
                ranges.append((start, stop))
    return ranges


def strip_cmdsubs(cmd, ctx):
    """$(...) と `...` を置き換える(中身は別のコマンドとしても見る)"""
    out, i, n = [], 0, len(cmd)
    inner = []
    skips = dict(_literal_heredoc_ranges(cmd)) if "<<" in cmd else {}
    while i < n:
        if i in skips:
            out.append(cmd[i:skips[i]])
            i = max(skips[i], i + 1)
            continue
        if cmd.startswith("$(", i) and not cmd.startswith("$((", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if cmd[j] == "(":
                    depth += 1
                elif cmd[j] == ")":
                    depth -= 1
                j += 1
            body = cmd[i + 2:j - 1]
            ctx.cmdsubs.append(body)
            inner.append(body)
            out.append(CMDSUB.format(len(ctx.cmdsubs) - 1))
            i = j
        elif cmd[i] == "`":
            j = cmd.find("`", i + 1)
            if j < 0:
                j = n
            body = cmd[i + 1:j]
            ctx.cmdsubs.append(body)
            inner.append(body)
            out.append(CMDSUB.format(len(ctx.cmdsubs) - 1))
            i = j + 1
        else:
            out.append(cmd[i])
            i += 1
    return "".join(out), inner


def split_heredocs(cmd):
    """heredoc の中身を外し、印(<<EOF)を __HEREDOC_n__ に置き換える。中身の一覧も返す"""
    lines = cmd.split("\n")
    kept, docs = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        marks = list(HEREDOC_RE.finditer(line))
        i += 1
        for m in marks:
            dash, word = m.group(1), m.group(3)
            body = []
            while i < len(lines):
                end = lines[i].lstrip("\t") if dash else lines[i]
                if end.strip() == word:
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            docs.append("\n".join(body))
        for k, m in reversed(list(enumerate(marks))):
            n = len(docs) - len(marks) + k
            line = line[:m.start()] + f" __HEREDOC_{n}__ " + line[m.end():]
        kept.append(line)
    return "\n".join(kept), docs


def prescan(cmd):
    """引用符の外の改行を ; に、コメント(語の頭の #)を外す。行の続き(\\改行)はつなぐ"""
    out, i, n = [], 0, len(cmd)
    quote = None
    while i < n:
        c = cmd[i]
        if quote:
            out.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                out.append(cmd[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            if cmd[i + 1] == "\n":
                out.append(" ")
            else:
                out.append(c + cmd[i + 1])
            i += 2
            continue
        if c in "'\"":
            quote = c
            out.append(c)
        elif c == "\n":
            out.append(" ; ")
        elif c == "#" and (i == 0 or cmd[i - 1] in " \t;&|("):
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def tokenize(cmd):
    lex = shlex.shlex(prescan(cmd), posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    out = []
    for t in lex:
        if t and set(t) <= PUNCT and t not in OPERATORS:
            out.extend(_split_punct(t))
        else:
            out.append(t)
    return out


def _split_punct(t):
    """');' のようにくっついた記号を、演算子ごとに分ける"""
    out, i = [], 0
    while i < len(t):
        for op in sorted(OPERATORS, key=len, reverse=True):
            if t.startswith(op, i):
                out.append(op)
                i += len(op)
                break
        else:
            out.append(t[i])
            i += 1
    return out


def segments(tokens):
    """(区切りまでの語, 区切り) を順に返す"""
    seg = []
    for t in tokens:
        if t in SEPARATORS:
            yield seg, t
            seg = []
        else:
            seg.append(t)
    yield seg, None


def drop_redirects(seg):
    out, skip = [], False
    for i, t in enumerate(seg):
        if skip:
            skip = False
            continue
        if t in REDIRECTS or (t and set(t) <= set("<>&|") and ("<" in t or ">" in t)):
            skip = True
            continue
        if t.isdigit() and i + 1 < len(seg) and seg[i + 1] and seg[i + 1][0] in "<>":
            continue
        out.append(t)
    return out


def command_words(seg, ctx):
    """前に付く代入・包むコマンドを外して (名前, 引数) を返す。代入だけなら覚える"""
    i = 0
    assigns = []
    while i < len(seg) and ASSIGN_RE.match(seg[i]):
        assigns.append(seg[i])
        i += 1
    while i < len(seg) and seg[i] in RESERVED:
        i += 1
    if i >= len(seg):
        for a in assigns:
            name, val = a.split("=", 1)
            v = expand(val, ctx)
            ctx.vars[name] = v if v is not None else "$" + "__UNKNOWN__"
            m = CMDSUB_RE.fullmatch(val.strip('"'))
            if m and MKTEMP_RE.match(ctx.cmdsubs[int(m.group(1))]):
                ctx.mktemp_vars.add(name)
            else:
                ctx.mktemp_vars.discard(name)
        return None, []
    while i < len(seg):
        w = os.path.basename(seg[i])
        if w in WRAPPERS:
            i += 1
            while i < len(seg) and (seg[i].startswith("-") or ASSIGN_RE.match(seg[i])
                                    or (w in ("timeout", "gtimeout") and re.match(r"^\d", seg[i]))):
                if w == "sudo" and seg[i] in ("-u", "-g", "-C") and i + 1 < len(seg):
                    i += 1
                if w == "nice" and seg[i] == "-n" and i + 1 < len(seg):
                    i += 1
                if w == "arch" and seg[i] in ("-arch", "-e") and i + 1 < len(seg):
                    i += 1
                if w == "env" and (seg[i] in ("-S", "--split-string") or seg[i].startswith("--split-string=")):
                    # env -S 'rm -rf ~': 文字列を分けてコマンドとして読む
                    s_ = seg[i].split("=", 1)[1] if "=" in seg[i] else (seg[i + 1] if i + 1 < len(seg) else "")
                    skip = 1 if "=" in seg[i] else 2
                    try:
                        words = shlex.split(s_)
                    except ValueError:
                        return UNKNOWN_CMD, seg[i + skip:]
                    return command_words(words + seg[i + skip:], ctx)
                if w == "env" and seg[i] in ("-u", "-C", "-P") and i + 1 < len(seg):
                    i += 1
                i += 1
            continue
        if w == "xcrun" and i + 1 < len(seg) and os.path.basename(seg[i + 1]) != "simctl":
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                if seg[i] in ("--sdk", "--toolchain") and i + 1 < len(seg):
                    i += 1
                i += 1
            continue
        if w == "script":
            # script [-aqkF] [-t 時間] [ファイル [コマンド …]]
            j = i + 1
            while j < len(seg) and seg[j].startswith("-"):
                j += 2 if seg[j] == "-t" else 1
            if j + 1 < len(seg):
                i = j + 1
                continue
        break
    if i >= len(seg):
        return None, []
    if "$" in seg[i] or CMDSUB_RE.search(seg[i]) or "`" in seg[i]:
        # $C・$X・$(which rm) のようにコマンド名が変数のとき: 広げて読み直す。広げられなければ分からない
        v = expand(seg[i], ctx)
        if v is None or not v.strip():
            return UNKNOWN_CMD, seg[i + 1:]
        try:
            words = shlex.split(v)
        except ValueError:
            return UNKNOWN_CMD, seg[i + 1:]
        if any("$" in w or CMDSUB_RE.search(w) for w in words):
            return UNKNOWN_CMD, seg[i + 1:]
        return command_words(words + seg[i + 1:], ctx)
    return os.path.basename(seg[i]), seg[i + 1:]


def split_opts(args):
    opts, targets, end = [], [], False
    for a in args:
        if not end and a == "--":
            end = True
        elif not end and a.startswith("-") and a != "-":
            opts.append(a)
        else:
            targets.append(a)
    return opts, targets


def check_rm(name, args, ctx):
    opts, targets = split_opts(args)
    recursive = name != "rm" or any(o in ("--recursive",) or (not o.startswith("--") and ("r" in o or "R" in o))
                                    for o in opts)
    if not targets:
        return
    for t in targets:
        judge_target(t, ctx, "rm", recursive)


def check_find(args, ctx):
    paths, i = [], 0
    while i < len(args) and args[i] in ("-H", "-L", "-P", "-x", "-E", "-s", "-X", "-d"):
        i += 1
    while i < len(args) and not args[i].startswith("-") and args[i] not in ("(", "!"):
        paths.append(args[i])
        i += 1
    expr = args[i:]
    deleting = "-delete" in expr
    for k, a in enumerate(expr):
        if a in ("-exec", "-execdir", "-ok", "-okdir") and k + 1 < len(expr):
            prog = os.path.basename(expr[k + 1])
            if prog in RM_NAMES:
                deleting = True
            elif _runs_code(prog):
                ctx.problems.append(f"find {a} でシェルや python を動かすと、中身の行き先を確かめられない: {prog}")
                return
    if not deleting:
        return
    filtered = any(a in FIND_TESTS for a in expr)
    if filtered and not _find_spares_git(expr):
        filtered = "keeps-git"
    for p in paths or ["."]:
        judge_target(p, ctx, "find-filtered" if filtered is True else
                     ("find-keeps-git" if filtered else "find"), True)


def _runs_code(prog):
    """中身を別に読むプログラム(シェル・python・包むコマンド・xargs)か"""
    return prog in SHELLS or bool(PYTHONS_RE.match(prog)) or prog in WRAPPERS or prog in (
        "xargs", "eval", "perl", "ruby", "node", "osascript", "flock")


def _find_spares_git(expr):
    """.git の中に当たらない絞り込みか: .git を条件で外している、または -name に字の決まった部分がある"""
    if any(".git" in a.casefold() for a in expr):
        return True
    for k, a in enumerate(expr):
        if a in ("-name", "-iname") and k + 1 < len(expr):
            pat = expr[k + 1]
            if any(c not in "*?[]" for c in pat):
                return True
    return False


def check_rsync(args, ctx):
    deletes = any(a.startswith("--delete") or a == "--del" for a in args)
    removes_src = "--remove-source-files" in args
    if not (deletes or removes_src):
        return
    _, pos = split_opts(args)
    if pos and deletes:
        judge_target(pos[-1], ctx, "rsync", True)
    if removes_src:
        for src in pos[:-1]:
            judge_target(src, ctx, "rm", True)


def check_archive(name, args, ctx):
    """zip -m・tar --remove-files は、入れた元を消す"""
    opts, pos = split_opts(args)
    if name == "zip":
        moves = "--move" in opts or any(not o.startswith("--") and "m" in o[1:] for o in opts)
        files = pos[1:]
    else:
        moves = "--remove-files" in opts
        files = [a for a in pos if not a.endswith((".tar", ".tgz", ".tar.gz", ".tbz", ".txz", ".zst"))]
    if moves:
        for f in files:
            judge_target(f, ctx, "rm", True)


def _flag(rest, short, longs):
    """短いフラグの束(-uf)や長いフラグ(--force=…)を含むか"""
    for a in rest:
        if a == "--":
            break
        if a.startswith("--"):
            if a.split("=", 1)[0] in longs:
                return True
        elif a.startswith("-") and len(a) > 1 and short in a[1:]:
            return True
    return False


REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REMOTE_URL_RE = re.compile(r"^(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)")


def check_push(rest, ctx, git_env):
    if git_env:
        ctx.problems.append("GIT_* の環境変数を付けた git push は止める(サンドボックスの外で、git の動きを変えられる)")
    if not ctx.trusted_repo():
        ctx.problems.append("git push は、作業の根(~/Projects)の直下のリポジトリの中でだけ打つ"
                            "(ほかの場所の .git/hooks はサンドボックスの中から書けて、push のときに外で動く)")
    if _flag(rest, "f", ("--force", "--force-with-lease", "--force-if-includes")):
        ctx.problems.append("force push は止める(GitHub の上のコミットが消える)")
    pos, i = [], 0
    while i < len(rest):
        a = rest[i]
        if a.startswith("--repo"):
            ctx.problems.append("git push --repo は止める(行き先を確かめられない)")
        elif a in ("-o", "--push-option", "--receive-pack", "--exec"):
            i += 1
        elif not a.startswith("-"):
            pos.append(a)
        i += 1
    if pos:
        repo = pos[0]
        if not (REMOTE_NAME_RE.match(repo) or REMOTE_URL_RE.match(repo)):
            ctx.problems.append(f"git push の行き先がリモートの名前でも GitHub の URL でもない({repo})。手元のパスや file:・ext:: への push は"
                                "相手側の hook がサンドボックスの外で動き、ほかの URL へはリポジトリの中身を送れる")
        for ref in pos[1:]:
            if ref.startswith("+") or ref.startswith(":"):
                ctx.problems.append(f"git push の {ref} は止める(GitHub の上のコミットやブランチが消える)")


def check_git(args, ctx, git_env=False):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-c", "--config-env") and i + 1 < len(args):
            if re.match(r"(?i)alias\.[^=]*=\s*!", args[i + 1]):
                ctx.problems.append("git -c alias.…='!…' は止める(エイリアスでシェルのコマンドを動かせる)")
        if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env") and i + 1 < len(args):
            i += 1
        i += 1
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1:]
    if sub == "push":
        check_push(rest, ctx, git_env)
        return
    if sub == "switch" and (_flag(rest, "f", ("--force", "--discard-changes"))):
        ctx.problems.append("git switch -f・--discard-changes は使わない(まだコミットしていない変更が消える)")
    elif sub == "rm" and _flag(rest, "f", ("--force",)):
        ctx.problems.append("git rm -f は使わない(まだコミットしていない変更が消える)")
    elif sub == "update-ref" and _flag(rest, "d", ("--delete",)):
        ctx.problems.append("git update-ref -d は使わない(どこからもたどれないコミットになる)")
    elif sub == "reflog" and rest and rest[0] in ("expire", "delete"):
        ctx.problems.append(f"git reflog {rest[0]} は使わない(消えたコミットを戻す手がかりが消える)")
    elif sub == "gc" and any(a.startswith("--prune") for a in rest):
        ctx.problems.append("git gc --prune は使わない(どこからもたどれないコミットが消える)")
    if sub == "clean":
        if any(a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in rest):
            ctx.problems.append("git clean -f は使わない(まだコミットしていない物が消える)")
    elif sub == "reset" and "--hard" in rest:
        ctx.problems.append("git reset --hard は使わない(まだコミットしていない変更が消える)")
    elif sub == "checkout":
        pos = [a for a in rest if not a.startswith("-")]
        makes_branch = any(a in ("-b", "-B", "--orphan", "-t", "--track") for a in rest)
        if "--" in rest or "." in rest or _flag(rest, "f", ("--force",)) or (len(pos) >= 2 and not makes_branch):
            ctx.problems.append("git checkout -- <パス>・checkout .・checkout -f は使わない(まだコミットしていない変更が消える)")
    elif sub == "restore":
        if not ("--staged" in rest or "-S" in rest) or "--worktree" in rest or "-W" in rest:
            ctx.problems.append("git restore は使わない(まだコミットしていない変更が消える。取り消すのは --staged だけ)")
    elif sub == "stash" and rest and rest[0] in ("drop", "clear"):
        ctx.problems.append(f"git stash {rest[0]} は使わない(しまった変更が消える)")
    elif sub == "worktree" and rest and rest[0] == "remove" and any(
            a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in rest[1:]):
        ctx.problems.append("git worktree remove --force は使わない(まだコミットしていない物が消える)")
    elif sub == "branch" and ("-D" in rest or (_flag(rest, "d", ("--delete",)) and _flag(rest, "f", ("--force",)))
                              or any(re.fullmatch(r"-[a-zA-Z]*D[a-zA-Z]*|-[a-zA-Z]*d[a-zA-Z]*f[a-zA-Z]*|"
                                                  r"-[a-zA-Z]*f[a-zA-Z]*d[a-zA-Z]*", a) for a in rest)):
        ctx.problems.append("git branch -D は使わない(取り込んでいないコミットが消える)")


def check_simctl(args, ctx):
    _, pos = split_opts(args)
    if "spawn" in pos:
        ctx.problems.append("xcrun simctl spawn は止める(サンドボックスの外で、好きなプログラムを動かせる)")
    if pos[:1] == ["io"]:
        for k, a in enumerate(pos):
            if a in ("screenshot", "recordVideo") and k + 1 < len(pos):
                p = resolve(expand(pos[k + 1], ctx), ctx) if expand(pos[k + 1], ctx) is not None else None
                if p is None or not any(_inside(p, r, False) for r in ctx.roots() + [ctx.dev_dir] if r):
                    ctx.problems.append(f"simctl io の書き出し先がリポジトリと一時フォルダの外: {pos[k + 1]}")


def check_xcodebuild(args, ctx):
    if not ctx.trusted_repo():
        ctx.problems.append("xcodebuild は、作業の根(~/Projects)の直下のリポジトリの中でだけ打つ(Run Script がサンドボックスの外で動く)")
    for k, a in enumerate(args):
        if a in ("-project", "-workspace") and k + 1 < len(args):
            v = expand(args[k + 1], ctx)
            p = resolve(v, ctx) if v is not None else None
            if p is None or not (ctx.project and _inside(p, ctx.project, True)):
                ctx.problems.append(f"xcodebuild の {a} がいまのリポジトリの外: {args[k + 1]}")


GH_RUNS_GIT = {("pr", "checkout"), ("pr", "create"), ("pr", "merge"), ("repo", "sync"), ("repo", "fork"),
               ("repo", "set-default")}


def check_gh(args, ctx):
    if "--" in args:
        k = args.index("--")
        if (args[:2] in (["repo", "clone"], ["repo", "fork"])) and args[k + 1:]:
            ctx.problems.append("gh repo clone・fork の -- のあとに git の引数を渡す形は止める"
                                "(--template・-c で、サンドボックスの外で動く git の hook を足せる)")
        args = args[:k]
    pos = [a for a in args if not a.startswith("-")]
    group, sub = (pos + ["", ""])[:2]
    if (group, sub) in GH_RUNS_GIT and not ctx.trusted_repo():
        ctx.problems.append(f"gh {group} {sub} は git を動かすので、作業の根(~/Projects)の直下のリポジトリの中でだけ打つ")
    if group in ("secret", "variable") and sub == "list":
        group = ""
    if group in GH_BLOCK_GROUP or sub in GH_BLOCK_SUB or (group, sub) in (("repo", "edit"), ("auth", "token"),
                                                                       ("auth", "refresh"), ("auth", "logout"),
                                                                       ("auth", "setup-git"), ("release", "delete-asset")):
        ctx.problems.append(f"gh {group} {sub} は止める(サンドボックスの外で、GitHub の上のものを消す・書き換える・持ち出す)".replace("  ", " "))
    ok_roots = ctx.roots() + ctx.work_roots
    api_path = pos[1] if group == "api" and len(pos) > 1 else None  # gh api /user の /user は API のパス
    for a in args:
        v = a.split("=", 1)[1] if a.startswith("-") and "=" in a else a
        if v is api_path:
            continue
        if v.startswith(("/", "~", "$")):
            e = expand(v, ctx)
            p = resolve(e, ctx) if e is not None else None
            if p is None or not any(_inside(p, r, False) for r in ok_roots):
                ctx.problems.append(f"gh に、リポジトリと一時フォルダの外のパスを渡している: {v}")
        elif ctx.cwd and v and not v.startswith("-") and os.path.lexists(os.path.join(ctx.cwd, v)):
            p = _real(os.path.join(ctx.cwd, v))
            if not any(_inside(p, r, False) for r in ok_roots):
                ctx.problems.append(f"gh に渡すファイルの実体(リンクの先)がリポジトリと一時フォルダの外: {v}")


def check_ln(args, ctx):
    """ln -s: 外を指すリンクを覚える(あとの rm・mv はリンクの先で判定する)。
    同じコマンドの中に消す・動かす処理があれば、外を指すリンクを作る時点で止める"""
    opts, pos = split_opts(args)
    symbolic = any(o == "--symbolic" or (not o.startswith("--") and "s" in o[1:]) for o in opts)
    if not symbolic or not pos:
        return
    no_deref = any(o in ("--no-dereference", "--no-target-directory") or
                   (not o.startswith("--") and ("n" in o[1:] or "h" in o[1:] or "T" in o[1:])) for o in opts)
    risky = _risky(ctx.full_cmd)
    srcs, dest = (pos[:-1], pos[-1]) if len(pos) >= 2 else (pos, ".")
    dp = expand(dest, ctx)
    d = resolve(dp, ctx) if dp is not None else None
    if d is None:
        if risky:
            ctx.problems.append(f"ln -s で作るリンクの場所が分からない: {dest}")
        return
    into_dir = len(srcs) > 1 or dest.endswith("/") or (not no_deref and os.path.isdir(_real(d)))
    for src in srcs:
        sp = expand(src, ctx)
        link = os.path.join(_real(d), os.path.basename(os.path.normpath(sp or src))) if into_dir else d
        if sp is None:
            if risky:
                ctx.problems.append(f"ln -s で作るリンクの指す先が分からない: {src}")
            continue
        if sp == "~" or sp.startswith("~/"):
            sp = (ctx.home or "") + sp[1:]
        target = _real(sp if os.path.isabs(sp) else os.path.join(os.path.dirname(link), sp))
        ctx.links[link] = target
        if risky and not any(_inside(target, root, strict=True) for root in ctx.roots()):
            ctx.problems.append(f"同じコマンドの中で、外を指すリンクを作ってから消す・動かす形: {src} → {target}")


def check_mv(args, ctx):
    """動かす元と行き先が、どちらもリポジトリか一時フォルダの中か"""
    opts, targets = split_opts(args)
    if len(targets) < 2:
        return
    *sources, dest = targets
    for t in sources:
        judge_target(t, ctx, "rm", True)
    p = expand(dest, ctx)
    if p is None:
        ctx.problems.append(f"動かす先が分からない: {dest}")
        return
    r = resolve(p, ctx, follow_last=True)
    if r is None:
        ctx.problems.append(f"動かす先が分からない(cd のあと): {dest}")
        return
    if _git_dir_problem(r, ctx):
        ctx.problems.append(f".git の中へは動かさない: {dest}")
        return
    if not any(_inside(r, root, strict=False) for root in ctx.roots()):
        ctx.problems.append(f"外へ動かす: {dest} → {r}")


PY_SHELL_RE = re.compile(r"os\.system\s*\(|os\.popen\s*\(|subprocess|\bPopen\s*\(|check_call\s*\(|check_output\s*\(")
PY_ALIAS_RE = re.compile(r"\b(?:rmtree|remove|unlink|rmdir|removedirs)\s+as\s+([A-Za-z_]\w*)")
PY_MOD_ALIAS_RE = re.compile(r"\bimport\s+(?:shutil|os)\s+as\s+([A-Za-z_]\w*)")
RM_LIST_RE = re.compile(r"\[\s*(['\"])(?:/bin/)?(?:rm|rmdir|unlink)\1")


def check_python_shell(code, ctx):
    """os.system('rm -rf ~')・subprocess.run(['rm', '-rf', p]) の形"""
    if not PY_SHELL_RE.search(code):
        return
    for lm in STR_LIT_RE.finditer(code):
        lit = lm.group(2)
        if " " in lit and delete_hint(lit):
            judge_command(lit, ctx)
    for m in RM_LIST_RE.finditer(code):
        depth, j = 1, m.start() + 1
        while j < len(code) and depth:
            depth += {"[": 1, "]": -1}.get(code[j], 0)
            j += 1
        body = code[m.start() + 1:j - 1]
        lits = [x.group(2) for x in STR_LIT_RE.finditer(body)]
        rest = STR_LIT_RE.sub("", body).replace(",", "").strip()
        if rest:
            ctx.problems.append("python から rm に渡す行き先が分からない(変数が入っている)")
        else:
            judge_command(" ".join(shlex.quote(x) for x in lits), ctx)


# ---- python 以外の言語(perl -e・node -e・ruby -e・awk・swift -e など) ----
# 中身は解析しない。消す・動かす・外のコマンドを動かす処理らしい語があれば「行き先が分からない」として止める。
INTERP_DELETE_RE = re.compile(
    r"rmtree|remove_tree|rmSync|rm_rf|\brm_r\b|\brm_f\b|\bFileUtils\b|\bunlink|\brmdir|\bremove|\btrash|"
    r"(?:\bFile|\bDir|\bPathname)\.delete\b|\bdelete\s+(?:POSIX\s+)?(?:file|folder|alias|item|every|disk)\b|"
    r"\brename|moveItem|\bmv\b|\brm\b|symlink|"
    r"\bsystem\b|\bexec(?:Sync|File|FileSync|v|vp|vpe|l|lp|le|ve)?\b|\bspawn|\bpopen\b|child_process|\bProcess\b|\bqx\b|%x[{(\[<]|\bdo shell script\b|doShellScript|"
    r"\bshell_exec\b|\bpassthru\b|\bproc_open\b|\bpcntl_exec\b|\bDeno\.(?:remove|run|Command)\b|\bBun\.(?:spawn|\$)"
)
# awk の delete arr[i] は配列の操作なので見ない。system() と、コマンドへのパイプ(print | "sh"・"cmd" | getline)を見る
AWK_DELETE_RE = re.compile(r"\bsystem\s*\(|\|&|\|\s*\"|\|\s*getline\b|\bprintf?\b[^;{}/]*\|\s*[A-Za-z_(]|"
                           r"\brm\b|\bunlink\b|\brmdir\b")
PERL_TIMEOUT_RE = re.compile(r"\s*alarm\s*\(?\s*\d+\s*\)?\s*;\s*exec\s*\{?\s*(?:\$ARGV\[0\]\s*\}?\s*)?@ARGV\s*;?\s*"
                             r"(?:or\s+die[^;]*;?\s*)?")
AWKS = {"awk", "gawk", "mawk", "nawk", "busybox-awk"}
# (名前, 中身を取る短いフラグの文字, 後ろに値を取る(くっつけて書く)フラグの文字, 長いフラグ, 決まった形だけのフラグ)
INTERPS = {
    "perl": ("eE", "iIMmxCdDF", ("--eval",), ()),
    "ruby": ("e", "IrCFiKTWxE", (), ()),
    "node": ("ep", "r", ("--eval", "--print"), ()),
    "nodejs": ("ep", "r", ("--eval", "--print"), ()),
    "bun": ("ep", "r", ("--eval", "--print"), ()),
    "swift": ("", "", (), ("-e",)),
    "php": ("", "", (), ("-r", "-R", "-B", "-E")),
    "lua": ("e", "l", (), ()),
    "luajit": ("e", "l", (), ()),
    "osascript": ("", "", (), ("-e",)),
}
VALUE_FLAGS = {"osascript": ("-l", "-s", "-i"), "php": ("-c", "-d", "-z")}


def _interp_name(name):
    """perl5.30・ruby3.3・node22 のような版つきの名前も同じ言語として見る"""
    if name in INTERPS or name in AWKS:
        return name
    m = re.fullmatch(r"(perl|ruby|node|php|lua)[\d.]*", name)
    return m.group(1) if m else None


def _interp_codes(lang, args):
    """引数から、その場で動かすコード(-e の中身など)を集める。(コード, 最初の位置引数) を返す"""
    if lang in AWKS:
        codes, i, has_file = [], 0, False
        while i < len(args):
            a = args[i]
            if a == "--":
                i += 1
                break
            if not a.startswith("-") or a == "-":
                break
            if a in ("-f", "--file", "-e", "--source", "-F", "-v", "--assign", "-i", "--include", "-l", "--load"):
                if a in ("-f", "--file", "-i", "--include", "-l", "--load"):
                    has_file = True  # 読み込むプログラムのファイル
                if a in ("-e", "--source") and i + 1 < len(args):
                    codes.append(args[i + 1])
                i += 2
                continue
            if a.startswith(("--source=", "--file=")):
                if a.startswith("--source="):
                    codes.append(a.split("=", 1)[1])
                else:
                    has_file = True
            elif a.startswith("-f"):
                has_file = True
            i += 1
        if not codes and not has_file and i < len(args):
            codes.append(args[i])  # awk 'プログラム' ファイル…
        return codes, None, has_file
    letters, params, longs, exact = INTERPS[lang]
    codes, first_pos, i = [], None, 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if a in VALUE_FLAGS.get(lang, ()):
            i += 2  # 値を1つ取るフラグ(osascript -l JavaScript など)
            continue
        if a in exact:
            if i + 1 < len(args):
                codes.append(args[i + 1])
            i += 2
            continue
        if a.startswith("--"):
            key, eq, val = a.partition("=")
            if key in longs:
                if eq:
                    codes.append(val)
                elif i + 1 < len(args):
                    codes.append(args[i + 1])
                    i += 1
            i += 1
            continue
        if a.startswith("-") and len(a) > 1 and letters:
            flags = a[1:]
            for k, c in enumerate(flags):
                if c in letters:
                    tail = flags[k + 1:]
                    if tail:
                        codes.append(tail)
                    elif i + 1 < len(args):
                        codes.append(args[i + 1])
                        i += 1
                    break
                if c in params:
                    break
            i += 1
            continue
        if not a.startswith("-") and first_pos is None:
            first_pos = a
        i += 1
    return codes, first_pos, False


def check_interp(name, args, ctx, heredoc_body=None, here=None, infile=None):
    """perl -e・node -e・ruby -e・awk・swift -e などの中の、消す・動かす処理らしい語を見る"""
    lang = _interp_name(name)
    if name == "deno" or lang is None:
        if name == "deno" and args[:1] == ["eval"]:
            lang, codes, first_pos, has_file = "deno", args[1:2], None, False
        else:
            return
    else:
        codes, first_pos, has_file = _interp_codes(lang, args)
    regex = AWK_DELETE_RE if lang in AWKS else INTERP_DELETE_RE
    if lang in ("perl", "ruby", "php"):
        regex = re.compile(regex.pattern + r"|`")  # perl・ruby・php のバッククォートはシェルのコマンドを動かす
    if not codes and lang not in AWKS:
        if heredoc_body is not None:
            codes = [heredoc_body]
        elif here is not None:
            codes = [here]
        elif first_pos is not None:
            check_script_file(first_pos, args[args.index(first_pos) + 1:], ctx, "interp")
            return
        elif infile is not None:
            check_script_file(infile, [], ctx, "interp", unknown_is_problem=True)
            return
        elif not [a for a in args if a != "-"]:  # 引数なし(か - だけ)で、パイプから読む形
            ctx.problems.append(f"{name} が標準入力(| {name} など)から読むコードは、中身を確かめられない")
            return
    if lang == "perl" and len(codes) == 1 and PERL_TIMEOUT_RE.fullmatch(codes[0]) and codes[0] in args:
        # 時間切れの包み(alarm N; exec @ARGV): 後ろの引数がそのままコマンドになるので、それを見る
        rest = args[args.index(codes[0]) + 1:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        if rest:
            check_segment(rest, ctx)
        return
    for code in codes:
        if lang in ("perl", "ruby", "php") and CMDSUB_RE.search(code):
            # 引用符の中の `…` も、読むときに先に取り出している(印が残る)。perl・ruby・php ではシェルを動かす
            ctx.problems.append(f"{name} の中のコードに `…`(シェルのコマンドを動かす)があり、行き先が分からない")
            return
        m = regex.search(code)
        if m:
            ctx.problems.append(f"{name} の中のコードに、消す・動かす・ほかのコマンドを動かす処理らしい語"
                                f"({m.group(0).strip()})があり、行き先が分からない。シェルの rm・mv に書き直すか、"
                                "python なら文字列のパスで書く")
            return


def check_python_code(code, argv_rest, ctx):
    check_python_shell(code, ctx)
    pats = [PY_DELETE_RE.pattern]
    pats += [rf"\b{re.escape(a)}\s*\(" for a in PY_ALIAS_RE.findall(code)]
    pats += [rf"\b{re.escape(a)}\.(?:rmtree|remove|unlink|rmdir|removedirs)\s*\(" for a in PY_MOD_ALIAS_RE.findall(code)]
    finder = re.compile("|".join(pats))
    if not finder.search(code):
        return
    for m in finder.finditer(code):
        # 呼び出しの引数(閉じかっこまで)と、その行
        start = m.end()
        depth, j = 1, start
        while j < len(code) and depth:
            if code[j] == "(":
                depth += 1
            elif code[j] == ")":
                depth -= 1
            j += 1
        arg = code[start:j - 1]
        text = "(" + arg + ")"
        if m.group(0).startswith("."):
            # Path('…').unlink() の形: 呼び出しの前の式(同じ行の、区切りまで)も見る
            line_start = code.rfind("\n", 0, m.start()) + 1
            receiver = _tail_expr(code[line_start:m.start()])
            text = receiver + text
            target = receiver
        else:
            target = _py_first_arg(arg)
        n = len(ctx.problems)
        _judge_py_expr(text, code, argv_rest, ctx, depth=0)
        if len(ctx.problems) == n and not _py_known(target, code, 0):
            ctx.problems.append(f"python で消す行き先が文字列のパスでない({(target or '').strip()[:60]})ので、"
                                "行き先が分からない。'…' のような文字列のパスで書く")


PY_STR_ONLY_RE = re.compile(r"""\s*(?:[rRuU]|[rR][bB]|[bB][rR]|[bB])?(['"])((?:\\.|(?!\1).)*)\1\s*""", re.S)
PY_PATH_CALL_RE = re.compile(r"\s*(?:pathlib\.)?(?:Pure)?(?:Posix)?Path\s*\((.*)\)\s*", re.S)
PY_NAME_RE = re.compile(r"\s*([A-Za-z_]\w*)\s*")
PY_ARGV_RE = re.compile(r"\s*sys\.argv\s*\[\s*-?\d+\s*\]\s*")
PY_PATH_KW = ("path", "name", "src", "file", "filename", "fspath")


def _split_top(arg):
    """かっこと引用符の外のカンマで分ける"""
    parts, depth, quote, cur, i = [], 0, None, [], 0
    while i < len(arg):
        c = arg[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(arg):
                cur.append(arg[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _py_first_arg(arg):
    """shutil.rmtree(p, ignore_errors=True) の p のように、消す行き先の引数を取り出す"""
    parts = _split_top(arg)
    for p in parts:
        m = re.match(r"\s*([A-Za-z_]\w*)\s*=(?!=)(.*)$", p, re.S)
        if not m:
            return p
        if m.group(1) in PY_PATH_KW:
            return m.group(2)
    return None


def _py_known(expr, code, depth):
    """消す行き先の式が、文字列のパス・Path('…')・一時フォルダ・sys.argv[n](またはそれを入れた変数)だけか。
    os.path.join(…)・連結・f 文字列・ループの変数など、組み立てた行き先は分からないとする"""
    if expr is None:
        return False
    e = expr.strip()
    while e.startswith("(") and e.endswith(")") and len(_split_top(e[1:-1])) == 1 and _balanced(e[1:-1]):
        e = e[1:-1].strip()
    if not e:
        return False
    if PY_HOME_RE.search(e):
        return True  # ホームの下を消す形は _judge_py_expr が止める
    if PY_TEMP_RE.search(e) and re.fullmatch(r"[\w.]*\([^()]*\)", e):
        return True  # tempfile.mkdtemp() そのもの
    if PY_STR_ONLY_RE.fullmatch(e) or PY_ARGV_RE.fullmatch(e):
        return True
    m = PY_PATH_CALL_RE.fullmatch(e)
    if m and _balanced(m.group(1)):
        inner = _split_top(m.group(1))
        return len(inner) == 1 and _py_known(inner[0], code, depth)
    m = PY_NAME_RE.fullmatch(e)
    if m and depth < 4:
        name = re.escape(m.group(1))
        found = False
        # 名前を決める所: 代入(; のあとも)・with … as 名前
        for am in re.finditer(rf"(?m)(?:^|;)\s*{name}\s*(=(?!=)|[-+*/%|&]=)\s*([^;\n]+)", code):
            if am.group(1) != "=":
                return False  # d += '/x' のように足して変える
            found = True
            if not _py_known(am.group(2), code, depth + 1):
                return False
        for am in re.finditer(rf"(?m)^[^\n#]*\bas\s+{name}\b", code):
            found = True
            if not PY_TEMP_RE.search(am.group(0)):
                return False
        if re.search(rf"\bfor\s+[^\n;]*\b{name}\b[^\n;]*\bin\b|\bdef\s+\w+\s*\([^)]*\b{name}\b|"
                     rf"\b{name}\s*:=|\bimport\b[^\n;]*\b{name}\b|\bglobal\b[^\n;]*\b{name}\b", code):
            return False  # ループの変数・関数の引数・:=・import した名前は、中身を追えない
        return found
    return False


def _balanced(s):
    depth = 0
    for c in STR_LIT_RE.sub("''", s):
        depth += {"(": 1, ")": -1}.get(c, 0)
        if depth < 0:
            return False
    return depth == 0


def _tail_expr(before):
    """'x = Path("/a").parent' から 'Path("/a").parent' を取り出す(かっこと引用符の外の区切りまで)"""
    depth, quote, k = 0, None, len(before)
    while k > 0:
        c = before[k - 1]
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c in ")]":
            depth += 1
        elif c in "([":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and c in " \t;=,:":
            break
        k -= 1
    return before[k:]


def _judge_py_expr(text, code, argv_rest, ctx, depth):
    if PY_HOME_RE.search(text):
        ctx.problems.append("python でホームの下を消す形(Path.home・expanduser・HOME)")
        return
    if PY_TEMP_RE.search(text):
        return
    for lm in STR_LIT_RE.finditer(text):
        s = lm.group(2)
        if s.startswith(("/", "~", "$")) or ("/" in s and " " not in s):
            judge_target(s, ctx, "rm", True)
    if "sys.argv" in text:
        for a in argv_rest:
            judge_target(a, ctx, "rm", True)
    if depth >= 3:
        return
    for name in set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", STR_LIT_RE.sub("", text))):
        for am in re.finditer(rf"(?m)^\s*{re.escape(name)}\s*=\s*(.+)$", code):
            _judge_py_expr(am.group(1), code, argv_rest, ctx, depth + 1)


def check_script_file(path_word, args, ctx, lang, unknown_is_problem=False):
    """消す処理を持つスクリプトに、外のパスを渡していないか。

    unknown_is_problem: 標準入力に流すファイル(bash < x.sh)のように、読めなければ止めるとき
    """
    p = expand(path_word, ctx)
    r = resolve(p, ctx, follow_last=True) if p is not None else None
    if not r or not os.path.isfile(r):
        if unknown_is_problem:
            ctx.problems.append(f"標準入力に流すスクリプトを読めない: {path_word}")
        return
    try:
        with open(r, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(1_000_000)
    except OSError:
        return
    if lang == "interp":
        deletes = INTERP_DELETE_RE.search(text)
    elif lang == "py":
        deletes = PY_DELETE_RE.search(text)
    else:
        deletes = SH_DELETE_RE.search(text) or PY_DELETE_RE.search(text)
    if not deletes:
        return
    for a in args:
        val = a.split("=", 1)[1] if a.startswith("--") and "=" in a else a
        if val.startswith(("/", "~", "$HOME", "${HOME}")):
            judge_target(val, ctx, "rm", True)


def parse_shell_args(args):
    """bash・sh の引数から (-c の中身, スクリプト, その引数, 標準入力から読むか) を返す。-lc・-ec も読む"""
    has_c = stdin = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "+o", "-O", "+O") and i + 1 < len(args):
            i += 2
            continue
        if a == "--":
            i += 1
            break
        if a == "-":
            stdin = True
            i += 1
            break
        if a.startswith("--"):
            i += 1
            continue
        if a[:1] in "-+" and len(a) > 1:
            if "c" in a[1:]:
                has_c = True
            if "s" in a[1:]:
                stdin = True
            i += 1
            continue
        break
    rest = args[i:]
    if has_c:
        return (rest[0] if rest else ""), None, [], False
    if stdin or not rest:
        return None, None, [], True
    return None, rest[0], rest[1:], False


def parse_python_args(args):
    """python の引数から (種類, 中身, 残り) を返す。種類は code・module・script・stdin。-Ic・-c'…' も読む"""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-":
            return "stdin", None, args[i + 1:]
        if a in ("-V", "-VV", "--version", "-h", "--help", "-?"):
            return "none", None, []  # 版や使い方を出すだけで、コードは読まない
        if a == "--" or not a.startswith("-") or a.startswith("--"):
            if a.startswith("--") and a != "--":
                i += 1
                continue
            if a == "--":
                i += 1
            break
        flags = a[1:]
        for k, c in enumerate(flags):
            if c == "c":
                tail = flags[k + 1:]
                if tail:
                    return "code", tail, args[i + 1:]
                return "code", (args[i + 1] if i + 1 < len(args) else ""), args[i + 2:]
            if c == "m":
                return "module", None, []
            if c in "WX":
                if not flags[k + 1:]:
                    i += 1
                break
        i += 1
    rest = args[i:]
    if not rest:
        return "stdin", None, []
    return "script", rest[0], rest[1:]


def stdin_sources(seg):
    """<<< の中身と < のファイルを取り出す"""
    here, infile = None, None
    for k, t in enumerate(seg[:-1]):
        if t == "<<<":
            here = seg[k + 1]
        elif t == "<":
            infile = seg[k + 1]
    return here, infile


def check_segment(seg, ctx, heredoc_body=None):
    words0 = [w for w in seg if not ASSIGN_RE.match(w)]
    if words0[:1] and words0[0] in ("source", ".") and "<" in words0[1:2]:
        # source <(…) は、プロセス置換の中身を読めない
        ctx.problems.append(f"{words0[0]} <(…) で読むものの中身を確かめられない")
        return
    here, infile = stdin_sources(seg)
    assigns_cdpath = any(w.startswith("CDPATH=") for w in seg)
    seg = drop_redirects(seg)
    git_env = any(re.match(r"^GIT_[A-Za-z0-9_]*=", w) for w in seg)
    first = next((w for w in seg if not ASSIGN_RE.match(w)), "")
    name, args = command_words(seg, ctx)
    if name is None:
        return
    if name == UNKNOWN_CMD:
        ctx.problems.append(f"コマンド名が変数や $(...) で、何を動かすか確かめられない: {seg[0] if seg else ''}")
        return
    if name == "flock":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in ("-c", "--command") and i + 1 < len(args):
                judge_command(args[i + 1], ctx)
                return
            if args[i] in ("-w", "--timeout", "-E", "--conflict-exit-code") and i + 1 < len(args):
                i += 1
            i += 1
        rest = args[i + 1:]  # ロックのファイルの次から
        if rest and rest[0] in ("-c", "--command") and len(rest) > 1:
            judge_command(rest[1], ctx)
        elif rest:
            check_segment(rest, ctx, heredoc_body)
        return
    if name in ("source", "."):
        if args and (CMDSUB_RE.search(args[0]) or args[0].startswith(("<(", "/dev/fd", "/dev/stdin"))):
            ctx.problems.append(f"{name} で読むものの中身を確かめられない: {args[0]}")
        elif args:
            check_script_file(args[0], args[1:], ctx, "sh")
        return
    if name in ("zip", "tar"):
        check_archive(name, args, ctx)
        return
    if name in ("export", "local", "declare", "typeset", "readonly"):
        command_words([a for a in args if ASSIGN_RE.match(a)], ctx)
        return
    if name in ("cd", "pushd"):
        _, pos = split_opts(args)
        if not pos:
            ctx.cwd = ctx.home
        elif pos[0] == "-":
            ctx.cwd = None
        elif (assigns_cdpath or ctx.env.get("CDPATH") or "CDPATH" in ctx.vars) and \
                not pos[0].startswith(("/", ".", "~", "$")):
            ctx.cwd = None  # CDPATH があると、相対の名前がどこへ行くか分からない
        else:
            # mktemp が失敗すると cd は引数なし(ホーム)か空(いまの場所)になるので、広げるときは whole_ok=False
            p = expand(pos[0], ctx, whole_ok=False)
            ctx.cwd = resolve(p, ctx, follow_last=True) if p is not None else None
        return
    if name == "xargs":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] in ("-I", "-n", "-L", "-P", "-s", "-E", "-J", "-R", "-S", "-d") and i + 1 < len(args):
                i += 1
            i += 1
        prog = os.path.basename(args[i]) if i < len(args) else ""
        if prog in RM_NAMES:
            ctx.problems.append("xargs で rm に渡す行き先が分からない(find … -delete か、パスを書いて rm する)")
        elif _runs_code(prog):
            ctx.problems.append(f"xargs でシェルや python を動かすと、中身の行き先を確かめられない: {prog}")
        return
    if name in RM_NAMES:
        check_rm(name, args, ctx)
    elif name in ("ln", "gln"):
        check_ln(args, ctx)
    elif name == "mv":
        check_mv(args, ctx)
    elif name == "find":
        check_find(args, ctx)
    elif name == "rsync":
        check_rsync(args, ctx)
    elif name == "git":
        check_git(args, ctx, git_env)
    elif name == "simctl":
        check_simctl(args, ctx)
    elif name == "xcrun" and args and os.path.basename(args[0]) == "simctl":
        check_simctl(args[1:], ctx)
    elif name == "xcodebuild":
        check_xcodebuild(args, ctx)
    elif name == "gh":
        check_gh(args, ctx)
    elif name == "eval":
        judge_command(" ".join(args), ctx)
    elif name in SHELLS:
        code, script, rest, stdin = parse_shell_args(args)
        if code is not None:
            judge_command(code, ctx)
        elif script is not None:
            check_script_file(script, rest, ctx, "sh")
        elif heredoc_body is not None:
            judge_command(heredoc_body, ctx)
        elif here is not None:
            judge_command(here, ctx)
        elif infile is not None:
            check_script_file(infile, [], ctx, "sh", unknown_is_problem=True)
        else:
            ctx.problems.append(f"{name} が標準入力(| {name} など)から読むコマンドは、中身を確かめられない")
    elif _interp_name(name) or name == "deno":
        check_interp(name, args, ctx, heredoc_body, here, infile)
    elif PYTHONS_RE.match(name):
        kind, body, rest = parse_python_args(args)
        if kind == "code":
            check_python_code(body, rest, ctx)
        elif kind == "script":
            check_script_file(body, rest, ctx, "py")
        elif kind == "stdin":
            if heredoc_body is not None:
                check_python_code(heredoc_body, rest, ctx)
            elif here is not None:
                check_python_code(here, rest, ctx)
            elif infile is not None:
                check_script_file(infile, rest, ctx, "py", unknown_is_problem=True)
            else:
                ctx.problems.append(f"{name} が標準入力(| {name} など)から読むコードは、中身を確かめられない")
    elif first in OUTSIDE_SCRIPTS or first.lstrip("./") in OUTSIDE_SCRIPTS:
        if not ctx.trusted_repo():
            ctx.problems.append(f"{first} は、作業の根(~/Projects)の直下のリポジトリの中でだけ打つ(サンドボックスの外で動く)")
        check_script_file(first, args, ctx, "sh")
    elif "/" in (seg[0] if seg else "") or name.endswith((".sh", ".py")):
        # ./x.sh・scripts/x.py を直に動かすとき
        word = next((w for w in seg if not ASSIGN_RE.match(w)), "")
        check_script_file(word, args, ctx, "py" if name.endswith(".py") else "sh")


def judge_command(cmd, ctx):
    cmd, inner = strip_cmdsubs(cmd, ctx)
    for body in inner:
        saved = ctx.cwd
        judge_command(body, ctx)
        ctx.cwd = saved
    main, docs = split_heredocs(cmd)
    try:
        toks = tokenize(main)
    except ValueError:
        if _risky(main):
            ctx.problems.append("コマンドが読めない(引用符が閉じていない)ので、消す処理の行き先を確かめられない")
        return
    stack = []
    for seg, sep in segments(toks):
        body = None
        rest = []
        for w in seg:
            m = HEREDOC_TOKEN_RE.fullmatch(w)
            if m:
                if body is None:
                    body = docs[int(m.group(1))]
            else:
                rest.append(w)
        if rest:
            check_segment(rest, ctx, body)
        if sep == "(":
            stack.append(ctx.cwd)
        elif sep == ")" and stack:
            ctx.cwd = stack.pop()


def judge(command, cwd, project_dir, home, temp_roots, env=None, repo_of=None):
    """問題の一覧を返す(空なら通す)。ファイルは消さない・書かない"""
    ctx = Ctx(project_dir, home, temp_roots, cwd, env, repo_of)
    ctx.full_cmd = command
    try:
        judge_command(decode_ansi_c(command), ctx)
    except Exception as e:  # 読み違いで素通りさせない
        if _risky(command):
            return [f"確かめに失敗した({type(e).__name__})ので止める"]
        return []
    seen, out = set(), []
    for p in ctx.problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def find_repo_root(path):
    p = os.path.normpath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def repo_finder(home):
    """cwd を含む git の根を返す。ホームそのもの・その上・/ はリポジトリと見なさない"""
    home_r = _real(home) if home else None

    def find(cwd):
        root = find_repo_root(cwd)
        if not root:
            return None
        r = _real(root)
        if r == "/" or (home_r and (r == home_r or _inside(home_r, r, strict=True))):
            return None
        return r
    return find


def default_temp_roots(env):
    roots = []
    if env.get("TMPDIR"):
        roots.append(env["TMPDIR"])
    roots.append(tempfile.gettempdir())
    roots.append("/private/tmp")
    out = []
    for r in roots:
        rr = _real(r)
        if rr not in out and rr not in ("/", "/private", "/private/var"):
            out.append(rr)
    return out


def main():
    """想定外の例外で落ちると exit 1 になり、Claude Code はコマンドを止めない(公式: exit 2 だけがブロック)。
    なので判定の全体を包み、落ちたら理由を出して exit 2 にする"""
    try:
        return _main()
    except Exception as e:
        sys.stderr.write(f"{MSG}: hook が確かめる途中で失敗した({type(e).__name__}: {e})ので、止める。{HINT}\n")
        return 2


def _main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.stderr.write(f"{MSG}: hook への入力が読めないので、行き先を確かめられない。止める\n")
        return 2
    if not isinstance(data, dict):
        sys.stderr.write(f"{MSG}: hook への入力の形が違うので、行き先を確かめられない。止める\n")
        return 2
    # Monitor も Bash と同じくシェルのコマンドを動かす(公式 tools-reference)
    if data.get("tool_name") not in ("Bash", "Monitor"):
        return 0
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd.strip():
        return 0
    env = dict(os.environ)
    cwd = data.get("cwd") or os.getcwd()
    home = env.get("HOME")
    problems = judge(cmd, cwd, None, home, default_temp_roots(env), env, repo_of=repo_finder(home))
    if problems:
        sys.stderr.write(f"{MSG}: " + " / ".join(problems) + f"。{HINT}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
