"""Edit・Write の守り(hooks/guard_write.py)のテスト。

    python3 -m unittest discover -s tests    # リポジトリの根で

判定だけを試す。パスは偽の場所(/fake/...)の文字列で、ファイルは書かない。
hook をプロセスとして動かすテストだけは、tempfile の一時ディレクトリに偽のホームとリポジトリを作る。
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks/guard_write.py"
WRAPPER = ROOT / "hooks/guard-write.sh"
sys.path.insert(0, str(HOOK.parent))
import guard_write as w  # noqa: E402

HOME = "/fake/home"
TMP = "/fake/tmp"
WORK = "/fake/home/Projects"
REPOS = {"/fake/home/Projects/app", "/fake/home/Projects/site", "/fake/opt/homebrew", "/fake/home/.oh-my-zsh"}


def fake_exists(p):
    return p.endswith("/.git") and p[: -len("/.git")] in REPOS


def problems(path):
    return w.judge(path, HOME, [TMP], [WORK], exists=fake_exists,
                   real=lambda p: os.path.normpath(p.replace("~", HOME, 1)))


class Allows(unittest.TestCase):
    def test_work_repos_temp_and_claude(self):
        for p in ["/fake/home/Projects/app/src/a.swift", "/fake/home/Projects/site/new/dir/index.html",
                  "/fake/home/Projects/app/CLAUDE.md", "/fake/home/Projects/app/.github/workflows/ci.yml",
                  "/fake/tmp/x.txt", "/fake/tmp/deep/y.py",
                  "/fake/home/.claude/projects/-x/memory/a.md", "/fake/home/.claude/plans/p.md",
                  "/fake/home/.claude/skills/writing/checklist.md"]:
            with self.subTest(p=p):
                self.assertEqual(problems(p), [], p)


class Blocks(unittest.TestCase):
    def test_outside(self):
        for p in ["/fake/home/.zshrc", "/fake/home/Documents/a.txt", "/fake/home/Projects/notes.md",
                  "/fake/home/.claude/settings.json", "/fake/home/.claude/hooks/guard_write.py",
                  "/etc/hosts", "/fake/home/.ssh/config", "~/.zshrc"]:
            with self.subTest(p=p):
                self.assertTrue(problems(p), p)

    def test_git_repos_outside_work_roots(self):
        # .git があっても、作業の根の外(Homebrew・oh-my-zsh)は止める
        for p in ["/fake/opt/homebrew/bin/git", "/fake/home/.oh-my-zsh/custom/evil.zsh"]:
            with self.subTest(p=p):
                self.assertTrue(problems(p), p)

    def test_claude_hooks_and_skills(self):
        for p in ["/fake/home/.claude/skills/writing/SKILL.md", "/fake/home/.claude/skills/writing/run.py",
                  "/fake/home/.claude/agents/a.md", "/fake/home/.claude/projects/-x/session.jsonl"]:
            with self.subTest(p=p):
                self.assertTrue(problems(p), p)

    def test_protected_inside_repo(self):
        for p in ["/fake/home/Projects/app/.claude/settings.json", "/fake/home/Projects/app/.claude/hooks/x.py",
                  "/fake/home/Projects/app/.mcp.json", "/fake/home/Projects/app/.envrc",
                  "/fake/home/Projects/app/.husky/pre-commit", "/fake/home/Projects/app/.githooks/pre-push",
                  "/fake/home/Projects/app/.vscode/tasks.json", "/fake/home/Projects/app/.gitmodules"]:
            with self.subTest(p=p):
                self.assertTrue(problems(p), p)

    def test_nested_protected_dirs(self):
        """入れ子の .claude も読み込まれる(Claude がそこのファイルに触れたとき)ので止める"""
        for p in ["/fake/home/Projects/app/packages/x/.claude/skills/evil/SKILL.md",
                  "/fake/home/Projects/app/sub/.claude/settings.json",
                  "/fake/home/Projects/app/sub/.husky/pre-commit"]:
            with self.subTest(p=p):
                self.assertTrue(problems(p), p)

    def test_git_dir_any_case(self):
        for p in ["/fake/home/Projects/app/.git/hooks/pre-push", "/fake/home/Projects/app/.git/config",
                  "/fake/home/Projects/app/.GIT/hooks/pre-commit", "/fake/home/Projects/app/.Git/config",
                  "/fake/home/Projects/app/sub/.git"]:
            with self.subTest(p=p):
                self.assertTrue(problems(p), p)

    def test_unreadable(self):
        for p in [None, "", "relative/path.txt", 3]:
            with self.subTest(p=p):
                self.assertTrue(problems(p), p)


class HookProcess(unittest.TestCase):
    """hook をプロセスとして動かす。偽のホームとリポジトリは一時ディレクトリに作る(本物は触らない)"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.home = base / "home"
        (self.home / "Projects/proj/.git").mkdir(parents=True)
        self.env = dict(os.environ, HOME=str(self.home), TMPDIR=str(base / "t"),
                        GUARD_WRITE_ROOTS=str(self.home / "Projects"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, payload, cmd=None):
        return subprocess.run(cmd or [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True,
                              env=self.env)

    def test_blocks_outside_and_allows_repo(self):
        # 外の例は、一時ディレクトリの外にある実在しない偽のパス(判定するだけで書かない)
        out = self.run_hook(json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/fake/home/.zshrc"}}))
        self.assertEqual(out.returncode, 2, out.stderr)
        out = self.run_hook(json.dumps({"tool_name": "Edit",
                                        "tool_input": {"file_path": str(self.home / "Projects/proj/a.txt")}}))
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_notebook_and_other_tools(self):
        out = self.run_hook(json.dumps({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "/fake/home/n.ipynb"}}))
        self.assertEqual(out.returncode, 2)
        out = self.run_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}}))
        self.assertEqual(out.returncode, 0)

    def test_broken_json_blocks(self):
        self.assertEqual(self.run_hook("{not json").returncode, 2)

    def test_odd_input_shapes_exit_2(self):
        # 判定の途中で落ちると exit 1(公式では非ブロック)になる形。exit 2 で止める(2026-10-19)
        for payload in ["[]", "1", json.dumps({"tool_name": "Write", "tool_input": "/fake/home/.zshrc"})]:
            with self.subTest(payload=payload):
                self.assertEqual(self.run_hook(payload).returncode, 2)

    def test_exception_in_judge_exits_2(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import guard_write as w\n"
                "def boom(*a, **k): raise RuntimeError('boom')\n"
                "w.judge = boom; sys.exit(w.main())")
        out = self.run_hook(json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/fake/x"}}),
                            cmd=[sys.executable, "-c", code, str(HOOK.parent)])
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertIn("RuntimeError", out.stderr)

    def test_wrapper_turns_abnormal_exit_into_2(self):
        # 一時ディレクトリに .sh の写しと壊した python を置いて、.sh が 2 にするかを見る
        d = pathlib.Path(self.tmp.name, "hooks")
        d.mkdir()
        (d / "guard-write.sh").write_text(WRAPPER.read_text())
        payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/fake/x"}})
        for body, want in [("def (:\n", 2), ("import sys; sys.exit(1)\n", 2), ("import sys; sys.exit(0)\n", 0)]:
            with self.subTest(body=body):
                (d / "guard_write.py").write_text(body)
                self.assertEqual(self.run_hook(payload, cmd=["/bin/bash", str(d / "guard-write.sh")]).returncode, want)

    def test_wrapper_passes_through(self):
        out = self.run_hook(json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/fake/home/.zshrc"}}),
                            cmd=["/bin/bash", str(WRAPPER)])
        self.assertEqual(out.returncode, 2, out.stderr)


if __name__ == "__main__":
    unittest.main()
