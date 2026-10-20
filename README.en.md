日本語版: [README.md](README.md)

# claude-code-guards

Two PreToolUse hooks for Claude Code that block deleting or writing files outside your git repository and the temp directories, with tests and example sandbox settings. Tested on macOS. The sandbox is the main defense; the hooks catch some of what it lets through. Neither is complete (see "Known gaps").

While I was running Claude Code in auto mode, many files in my Mac's home directory were deleted at once. This repository contains the two PreToolUse hooks and tests I added afterward, plus example settings for the sandbox and deny rules. What happened, and what each layer **cannot prevent**, is covered in this article (Japanese): https://zenn.dev/ryodevapp/articles/claude-code-home-deleted-guards

About the author: I work as a PM at an IT company and build three iOS apps with Claude Code.

## Requirements

- macOS (tested on macOS 27, Claude Code 2.1.292, and the macOS system python3 3.9.6 and python3 3.12)
- git and python3 (if missing, run `xcode-select --install`)

## Try it in three commands

```sh
git clone https://github.com/ryodevapp/claude-code-guards && cd claude-code-guards
python3 -m unittest discover -s tests
printf '{"tool_name":"Bash","cwd":"%s","tool_input":{"command":"rm -rf ~"}}' "$PWD" | bash hooks/guard-delete.sh; echo "exit=$?"
```

The second command runs 68 tests (53 for the delete hook, 15 for the write hook). They pass fake paths (`/fake/home`) to the decision functions and never touch your files. Temp directories the tests create are cleaned up at the end.

The third command gives the hook an input saying "about to run `rm -rf ~`", and prints `exit=2` (blocked). Change `rm -rf ~` to `rm -rf build` and it prints `exit=0` (allowed), because the target is inside the repository. In both cases the hook only inspects the string. It does not run the command. If you downloaded a ZIP and there is no `.git`, `rm -rf build` also gives `exit=2` (it is outside a repository). When the hook blocks, it writes the reason to stderr, and Claude sees it as is.

## Files

| File | What it is |
|---|---|
| `hooks/guard-delete.sh` | Entry point for PreToolUse (Bash, Monitor). Finds a working python3; if none, blocks commands that contain deletion-like strings |
| `hooks/guard_delete.py` | The delete hook itself. Blocks with exit 2 commands that delete or move files outside the repository and the temp directories, and risky invocations of commands excluded from the sandbox |
| `hooks/guard-write.sh` | Entry point for PreToolUse (Edit, Write, MultiEdit, NotebookEdit). Blocks if python3 does not work |
| `hooks/guard_write.py` | The write hook itself. Blocks with exit 2 writes outside the allowed locations and writes to protected config files |
| `tests/test_guard_delete.py`, `tests/test_guard_write.py` | Tests. They use only fake paths and temp directories the tests create |
| `settings/settings.example.json` | Example settings for the sandbox, deny rules, and hooks |

## Four layers of defense, and what each one misses

The main defense is the sandbox (layer 2), where the OS restricts where writes can go. The hooks (layer 4) catch tools that run outside the sandbox. Every layer lets something through, so each layer is meant to cover what the previous one cannot stop.

| Layer | Blocks | Does not block |
|---|---|---|
| 1. Working directory | Together with the sandbox, writes outside the repository you launched from and the temp directories | If you launch from a parent directory, everything under it counts as "inside" |
| 2. Sandbox | Writes and network access by Bash and Monitor commands and their child processes | Tools such as Read, Edit, and Write; MCP servers; hooks; commands listed in `excludedCommands` |
| 3. Deny rules | Commands and tool calls that match a pattern | Other spellings such as `/bin/rm …`, `bash -c '…'`, `git -C . push` |
| 4. Hooks | Bash commands that delete or move files outside; deletions with an unresolvable target; destructive git operations; risky invocations of commands excluded from the sandbox. Edit and Write outside the allowed locations and to config files | Paths built and passed inside scripts or tests. Patterns the hooks don't catch yet ("Known gaps" below). Hook failures and timeouts |

Separately from the four layers, I push to remotes and keep Time Machine backups. They reduce the loss after files are deleted, but they do not prevent the deletion.

## What the delete hook checks

Deletion is allowed only **inside the git repository that contains the directory the command runs in**, and inside the temp directories (`$TMPDIR`, `tempfile.gettempdir()`, `/private/tmp`). The home directory itself and anything above it never count as a repository. Even inside a repository, the hook blocks deleting the root itself, `.git`, and `rm -rf *` that wipes everything under the root.

- `rm`, `rmdir`, `unlink` (also when wrapped in `env -S`, `arch`, `script`, or `xcrun`), `find -delete`, `find -exec rm`, `rsync --delete` (`--del`) and `--remove-source-files`, `zip -m`, `tar --remove-files`, and `mv` that moves files out or brings files in from outside. It also reads obfuscated names such as `r\m`, `"r""m"`, and `$'\x72m'`
- The contents of `bash -c`, `bash -lc`, `eval`, `source`, heredocs, `<<<`, and `| bash`, plus `find -exec sh` and `xargs sh`. `source <(…)`, whose contents it cannot read, is blocked
- `shutil.rmtree`, `os.remove`, `Path.unlink`, `os.system`, and `rm` via `subprocess` in `python -c` (including `-Ic` and similar) and python heredocs
- Passing an outside path (one starting with `/` or `~`, or a relative path containing `..`) as an argument to a python or sh script that contains deletion code. Variables and `$(...)` in arguments are expanded before the check (`H=~; sh s.sh $H` is also blocked). If they cannot be expanded, the call is blocked
- Python deletions whose target is not a string path (`os.path.join(os.sep, …)`, string concatenation, f-strings, loop variables, and so on). These are blocked as unknown targets
- The code passed to `perl -e`, `node -e`, `ruby -e`, `awk`, `swift -e`, `php -r`, `lua -e`, `osascript -e`, and `deno eval` (including `-E`, `--eval`, `-p`, and heredocs). The code is not parsed. It is blocked if it contains words that look like deletion, moving, or command execution (`rmtree`, `rmSync`, `rm_rf`, `unlink`, `rmdir`, `remove`, `system`, `exec`, backticks, and so on)
- Deletion through symbolic links. If the target is a link (or a component of its path is a link), the link's destination is checked too. A command that creates a link pointing outside with `ln -s` and also deletes or moves in the same command is blocked

Destructive git operations (`git reset --hard`, `git clean -f`, `git restore`, `git checkout -- <path>`, `git checkout HEAD <path>`, `git switch -f`, `git rm -f`, `git stash drop/clear`, `git worktree remove --force`, `git branch -D`, `git update-ref -d`, `git reflog expire`, `git gc --prune`) and `git -c alias.x='!…'` are blocked even inside a repository, because they discard uncommitted changes or commits that exist nowhere else. `git restore --staged` and `git checkout main` are allowed.

For commands listed in `excludedCommands`, which run outside the sandbox, the hook blocks the following forms.

- Running `git push`, `xcodebuild`, `Tools/test.sh`, or `gh` subcommands that run git under the hood (`gh pr checkout`, `gh pr create`, `gh pr merge`, `gh repo sync`, and so on) outside repositories directly under `~/Projects` (and their worktrees). You can change this with `GUARD_WRITE_ROOTS`. Repositories in other places are excluded because their `.git/hooks` can be written from inside the sandbox. A repository whose `.git` is a file or symlink pointing elsewhere, or that is redirected elsewhere with `commondir`, is not allowed even if it sits directly under `~/Projects`
- `git push` whose destination is neither a remote name nor a GitHub URL (a local path, `file:`, `ext::`, other servers), `--repo`, `GIT_*` environment variables, force via short flag clusters such as `-uf`, and `+` or `:` refspecs. A push to a local repository runs the receiving side's hooks outside the sandbox, and a push to another server can send the repository's contents out
- `xcrun simctl spawn`, `simctl io` when its output path is outside, and `xcodebuild -project` when the project is outside the current repository
- `gh` subcommands such as `delete`, `archive`, `rename`, `repo edit`, `gist`, `secret set`, `variable set`, and `auth token` (read-only ones such as `secret list` are allowed), calls that pass outside paths (including link targets), and forms that pass arguments to git after `--`, such as `gh repo clone … -- --template=…`. `gh` running outside the sandbox can also read `~/.ssh`

The hook also blocks deletions whose target it cannot determine (unknown variables, `$(...)`, a variable as the command name, relative paths after `cd -`, `xargs rm`), and blocks when it cannot read the JSON input to the hook. `rm -rf build` inside a directory that is not a git repository is also blocked. If you hit a false positive, `cd` into the repository and retype the command with an explicit path. Replace `xargs rm` with `find … -delete`.

The hook does not use `CLAUDE_PROJECT_DIR`, because launching from a parent directory would make everything under it "inside". Instead, it looks up the repository that contains the command's working directory every time.

## What the write hook checks

Writes are allowed only in these locations (hard-coded).

- Inside git repositories under `~/Projects`. To use other locations, set the `GUARD_WRITE_ROOTS` environment variable (`:`-separated; the delete hook also uses it to check commands that run outside the sandbox)
- Inside the temp directories
- Inside `~/.claude/projects/*/memory/` and `~/.claude/plans/`, and `.md` files under `~/.claude/skills/` other than `SKILL.md`

Even within these, the hook blocks writes inside `.git` (case-insensitive) and to config that can add things that run outside the sandbox (`.claude/`, `.husky/`, `.githooks/`, `.vscode/`, `.mcp.json`, `.envrc`, and so on, including nested ones). It also blocks when there is no path, when the path is relative, or when the input cannot be read.

I did not use "allow if there is a `.git`" because `/opt/homebrew` and `~/.oh-my-zsh` are git repositories too.

## Installation

First, confirm that the tests pass with the three commands above.

1. Copy the hooks

   ```sh
   mkdir -p ~/.claude/hooks
   cp hooks/guard-delete.sh hooks/guard_delete.py hooks/guard-write.sh hooks/guard_write.py ~/.claude/hooks/
   ```

2. Using `settings/settings.example.json` as a reference, add the parts you need to `~/.claude/settings.json`. Do not overwrite the whole file. If you already have `hooks.PreToolUse` or `permissions.deny`, add to those arrays (if the same key appears twice, JSON keeps only the last one)
3. Replace `/Users/YOU` in the hooks' `args` with your home directory, and check that `grep -n '/Users/YOU' ~/.claude/settings.json` prints nothing. With `args`, the hook starts without a shell, so `~` is not expanded. In my environment, leaving `~` made the hook exit with code 127. If you include `onFailure: block`, version 2.1.295 and later also stop on this as a failure. In earlier versions, this exit code is not the blocking one (2), so the command goes through
4. Restart Claude Code and confirm in `/hooks` that both hooks are installed. If the first command shows "hook error", a path is wrong

Four things to know about the example settings before you install them:

- **The only allowed network destination is GitHub.** If you use npm or pip, add them to `sandbox.network.allowedDomains`
- `~/.ssh` and `~/.git-credentials` are unreadable from inside the sandbox. If you `git push` with SSH or stored credentials, push fails inside the sandbox. Run push in your own terminal, or add `git push *` to `excludedCommands`. If you add it, push runs outside the sandbox, and so do the git hooks that run during push. The example makes `.githooks` and `~/Projects/*/.git/hooks` unwritable, and the delete hook restricts where you can push from and to. If you keep repositories outside `~/Projects`, update both `GUARD_WRITE_ROOTS` and `denyWrite`
- The deny rules include `Edit(~/.claude/hooks/**)` and `Edit(~/.claude/settings.json)`, so that Claude's tools cannot modify the defenses themselves. After installing, you change settings and hooks in your own editor or terminal
- Wildcards in `denyWrite` such as `~/**/…` and `~/Projects/*/…` work only on macOS. On Linux and WSL2, `allowWrite` and `denyWrite` entries containing `*`, `?`, or `[`, and the paths that `Edit(...)` rules add to the sandbox, are ignored, so list each path individually (they still work as deny rules for the Edit tool; see the [Settings reference](https://code.claude.com/docs/en/settings-reference))

To uninstall, remove the lines you added from `~/.claude/settings.json`, and delete the four files in `~/.claude/hooks/`.

## Known gaps

- Tests and builds listed in `excludedCommands` run outside the sandbox. Paths built and passed inside them are visible to neither the auto mode classifier nor the hooks. My rule is to write tests for deletion code using only fake paths or temp directories. I use temp directories with `set -eu` and `"${D:?}"`, so the script stops if creating the directory fails
- `excludedCommands` patterns tend to be broad. The hook stops risky forms (such as `simctl spawn`), but it does not look at apps installed and run on the simulator (`simctl install`, `launch`), Xcode Run Script phases, or the contents of build settings passed to `xcodebuild`. For `gh pr merge` and `gh workflow run`, it checks the location but does not stop based on the content. Pushes to GitHub repositories you do not own also go through
- Patterns the delete hook doesn't catch yet: paths built inside script files; paths passed through environment variables instead of arguments (`H=~ sh s.sh` or `export H=~; sh s.sh`, where the script runs `rm -rf "$H"`); relative arguments that the script uses after a `cd` inside it; words assembled with `chr()` or `pack` to hide them; overwrites with awk's `print > "file"`; BSD `find -f <path>` when the working directory is a temp directory; and overwrites (redirection, `cp`, `sed -i`, `truncate`). These run inside the sandbox, so the sandbox (layer 2) prevents writes outside
- If a hook times out, Claude Code proceeds anyway (when an exception occurs during the check, or python exits abnormally, the hook exits 2 and blocks). The example settings add `"onFailure": "block"` to the hooks. On v2.1.295 and later, timeouts and launch failures also block (earlier versions ignore it as an unknown key)

If you find a gap that is not listed here, please let me know in an Issue.

## License

MIT
