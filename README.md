# claude-code-guards

Claude Code を auto モードで動かしていたあいだに、Mac のホーム配下がまとめて消えました。そのあとに足した PreToolUse の hook 2つとテスト、サンドボックスと deny ルールの設定例です。何が起きたかと、各層が**防げないもの**は記事に書いています: {{ZENN_URL}}

{{WHO_LINE}}

## 必要なもの

- macOS(確かめたのは macOS 27、Claude Code 2.1.292、macOS 標準の python3 3.9.6 と python3 3.12)
- git、python3(無ければ `xcode-select --install`)

## 3コマンドで試す

```sh
git clone https://github.com/ryodevapp/claude-code-guards && cd claude-code-guards
python3 -m unittest discover -s tests
printf '{"tool_name":"Bash","cwd":"%s","tool_input":{"command":"rm -rf ~"}}' "$PWD" | bash hooks/guard-delete.sh; echo "exit=$?"
```

2つめはテスト67件(削除用52件・書き込み用15件)です。判定の関数に偽のパス(`/fake/home`)を渡して確かめ、手元のファイルには触れません。テストが自分で作った一時ディレクトリは、終わりに片づきます。

3つめは hook に「`rm -rf ~` を打とうとしている」という入力を渡し、`exit=2`(ブロック)が出ます。`rm -rf ~` を `rm -rf build` に変えると、リポジトリの中なので `exit=0`(通す)になります。どちらも文字列を判定するだけで、コマンドは実行しません。ZIP で落として `.git` が無いときは、`rm -rf build` も `exit=2` になります(リポジトリの外なので)。ブロックしたときは理由が標準エラーに出て、Claude にもそのまま見えます。

## ファイル一覧

| ファイル | 何か |
|---|---|
| `hooks/guard-delete.sh` | PreToolUse(Bash・Monitor)の入口。動く python3 を探し、無ければ削除らしい文字を含むコマンドをブロックする |
| `hooks/guard_delete.py` | 削除用の本体。リポジトリと一時ディレクトリの外を消す・動かすコマンドと、サンドボックスの外へ出る危ない呼び方を exit 2 でブロックする |
| `hooks/guard-write.sh` | PreToolUse(Edit・Write・MultiEdit・NotebookEdit)の入口。python3 が動かなければブロックする |
| `hooks/guard_write.py` | 書き込み用の本体。決めた場所の外と、守る設定ファイルへの書き込みを exit 2 でブロックする |
| `tests/test_guard_delete.py`・`tests/test_guard_write.py` | テスト。偽のパスと、テストが作る一時ディレクトリだけを使う |
| `settings/settings.example.json` | サンドボックス・deny ルール・hook の設定例 |

## 4層の防御と、防げないもの

メインの防御は、OS が書き込み先を絞るサンドボックス(②)です。hook(④)は、サンドボックスの外で動くツールの受け皿です。どの層にも通すものがあるので、各層が防げないものを次の層でカバーする形にしています。

| 層 | 防ぐもの | 防げないもの |
|---|---|---|
| ① 作業ディレクトリ | ②と組んで、起動したリポジトリと一時ディレクトリの外への書き込み | 親ディレクトリで起動すると、その下は全部「中」 |
| ② サンドボックス | Bash・Monitor のコマンドと子プロセスの書き込み・通信 | Read・Edit・Write などのツール、MCP サーバー、hook、`excludedCommands` に入れたコマンド |
| ③ deny ルール | パターンに一致するコマンドとツールの呼び出し | `/bin/rm …`・`bash -c '…'`・`git -C . push` のような別の書き方 |
| ④ hook | 外を消す・動かす Bash のコマンド、削除先が分からないもの、破壊的な git 操作、サンドボックスの外へ出る危ない呼び方。外と設定ファイルへの Edit・Write | スクリプトやテストの中で組み立てて渡すパス。まだ判定しない書き方(下の「既知の穴」)。hook の失敗・タイムアウト |

4層とは別に、push と Time Machine のバックアップを持っています。消えたあとの損失は減らせますが、消えること自体は防ぎません。

## 削除用の hook が見るもの

消してよいのは、**コマンドを実行するディレクトリを含む git リポジトリの中**と、一時ディレクトリ(`$TMPDIR`・`tempfile.gettempdir()`・`/private/tmp`)の中だけです。ホームそのものとその上は、リポジトリとみなしません。リポジトリの中でも、ルートそのもの、`.git`、ルートの下を丸ごと消す `rm -rf *` はブロックします。

- `rm`・`rmdir`・`unlink`(`env -S`・`arch`・`script`・`xcrun` で包んだものも)、`find -delete`・`find -exec rm`、`rsync --delete`(`--del`)・`--remove-source-files`、`zip -m`、`tar --remove-files`、外へ出る・外から持ってくる `mv`。`r\m`・`"r""m"`・`$'\x72m'` のように名前を崩した書き方も読みます
- `bash -c`・`bash -lc`・`eval`・`source`・heredoc・`<<<`・`| bash` の中身、`find -exec sh`・`xargs sh`。中身を読めない `source <(…)` は止めます
- `python -c`(`-Ic` なども)と python の heredoc の `shutil.rmtree`・`os.remove`・`Path.unlink`・`os.system`・`subprocess` の `rm`
- 削除処理を持つ python・sh のスクリプトに、`/` か `~` で始まる外のパスを引数で渡す呼び方
- python の削除の行き先が文字列のパスでないもの(`os.path.join(os.sep, …)`・文字列の連結・f 文字列・ループの変数など)。行き先が分からないとしてブロックします
- `perl -e`・`node -e`・`ruby -e`・`awk`・`swift -e`・`php -r`・`lua -e`・`osascript -e`・`deno eval` の中身(`-E`・`--eval`・`-p` やヒアドキュメントも)。中身は解析せず、削除・移動・コマンド実行らしい語(`rmtree`・`rmSync`・`rm_rf`・`unlink`・`rmdir`・`remove`・`system`・`exec`・バッククォートなど)があればブロックします
- シンボリックリンクを経由した削除。削除の対象がリンク(か途中がリンク)なら、リンクの先でも判定します。同じコマンドの中で `ln -s` で外を指すリンクを作り、削除・移動もするものはブロックします

破壊的な git 操作(`git reset --hard`・`git clean -f`・`git restore`・`git checkout -- <パス>`・`git checkout HEAD <パス>`・`git switch -f`・`git rm -f`・`git stash drop/clear`・`git worktree remove --force`・`git branch -D`・`git update-ref -d`・`git reflog expire`・`git gc --prune`)と `git -c alias.x='!…'` は、リポジトリの中でもブロックします。まだコミットしていない変更や、どこにも無いコミットが消えるためです。`git restore --staged` や `git checkout main` は通します。

`excludedCommands` に入れてサンドボックスの外で動かすコマンドは、次の形をブロックします。

- `git push`・`xcodebuild`・`Tools/test.sh` と、裏で git を動かす `gh pr checkout`・`gh pr create`・`gh pr merge`・`gh repo sync` などを、`~/Projects` 直下のリポジトリ(とその worktree)の外で打つこと(`GUARD_WRITE_ROOTS` で変えられます)。ほかの場所のリポジトリは、`.git/hooks` をサンドボックスの中から書けるためです。`.git` がファイルやシンボリックリンクで別の場所を指すもの、`commondir` で別の場所へ向けたものは、直下にあっても通しません
- `git push` の行き先がリモートの名前でも GitHub の URL でもないとき(手元のパス・`file:`・`ext::`・ほかのサーバー)、`--repo`、`GIT_*` の環境変数、`-uf` のような短いフラグの force、`+`・`:` の refspec。手元のリポジトリへの push では相手側の hook が外で動き、ほかのサーバーへはリポジトリの中身を送れます
- `xcrun simctl spawn`、`simctl io` の書き出し先が外のとき、`xcodebuild -project` がいまのリポジトリの外のとき
- `gh` の `delete`・`archive`・`rename`・`repo edit`・`gist`・`secret set`・`variable set`・`auth token` など(`secret list` のような読むだけのものは通す)、外のパス(リンクの先を含む)を渡す呼び方、`gh repo clone … -- --template=…` のように `--` のあとで git に引数を渡す形。外で動く `gh` は `~/.ssh` も読めます

削除先が分からないもの(未知の変数、`$(...)`、コマンド名が変数、`cd -` のあとの相対パス、`xargs rm`)と、hook への入力の JSON が読めないときもブロックします。git でないディレクトリの中の `rm -rf build` もブロックされます。誤検知したときは、リポジトリに `cd` して、パスを直接書いて打ち直せば通ります。`xargs rm` は `find … -delete` に替えます。

`CLAUDE_PROJECT_DIR` は使いません。親ディレクトリで起動すると、その下が全部「中」になるためです。代わりに、コマンドを実行するディレクトリを含むリポジトリを毎回たどります。

## 書き込み用の hook が見るもの

書いてよいのは、次の場所だけです(決め打ち)。

- `~/Projects` の下の git リポジトリの中。ほかの場所にするなら、環境変数 `GUARD_WRITE_ROOTS` で変えます(`:` 区切り。削除用の hook の、外で動くコマンドの判定にも使います)
- 一時ディレクトリの中
- `~/.claude/projects/*/memory/` と `~/.claude/plans/` の中、`~/.claude/skills/` の下の `SKILL.md` 以外の `.md`

その中でも、`.git` の中(大文字小文字を区別しない)と、サンドボックスの外で動くものを足せる設定(`.claude/`・`.husky/`・`.githooks/`・`.vscode/`・`.mcp.json`・`.envrc` など。ネストしたものも)はブロックします。パスが無い・相対パス・入力が読めないときもブロックします。

「`.git` があれば許す」にしなかったのは、`/opt/homebrew` や `~/.oh-my-zsh` も git リポジトリだからです。

## 入れ方

先に上の3コマンドでテストが通ることを確かめてください。

1. hook を置く

   ```sh
   mkdir -p ~/.claude/hooks
   cp hooks/guard-delete.sh hooks/guard_delete.py hooks/guard-write.sh hooks/guard_write.py ~/.claude/hooks/
   ```

2. `settings/settings.example.json` を見ながら、要るところを `~/.claude/settings.json` に足す。ファイルごと上書きはしません。すでに `hooks.PreToolUse` や `permissions.deny` があるなら、その配列に足します(同じキーを2回書くと、JSON では後のものだけが残ります)
3. hook の `args` にある `/Users/YOU` を自分のホームに書き換え、`grep -n '/Users/YOU' ~/.claude/settings.json` で何も出ないことを確かめる。`args` を書くとシェルを通さずに起動するので、`~` は展開されません。私の環境では、`~` のままだと終了コード127で終わりました。ブロックの終了コード(2)ではないので、コマンドはそのまま通ります
4. Claude Code を起動し直し、`/hooks` で2つとも入っていることを確かめる。最初のコマンドで「hook error」が出たら、パスの書き間違いです

設定例について、入れる前に知っておいてほしいことが4つあります。

- **ネットワークの許可先は GitHub だけです**。npm や pip を使うなら、`sandbox.network.allowedDomains` に足してください
- `~/.ssh` と `~/.git-credentials` を、サンドボックスの中から読めなくしています。SSH や保存した認証で `git push` している場合、サンドボックスの中では push が失敗します。push を自分のターミナルで打つか、`excludedCommands` に `git push *` を足してください。足すと push はサンドボックスの外で動き、push のときに走る git の hook も外になります。例では `.githooks` と `~/Projects/*/.git/hooks` を書き込めなくし、削除用の hook で push する場所と行き先を絞っています。リポジトリを `~/Projects` 以外に置くなら、`GUARD_WRITE_ROOTS` と `denyWrite` の両方を合わせてください
- deny ルールに `Edit(~/.claude/hooks/**)` と `Edit(~/.claude/settings.json)` があります。防御そのものを Claude のツールから書き換えられないようにするためです。入れたあとは、設定と hook の変更を自分のエディタやターミナルで行うことになります
- `denyWrite` の `~/**/…`・`~/Projects/*/…` のようなワイルドカードは、macOS でだけ効きます。Linux・WSL2 では `*`・`?`・`[` を含む `allowWrite`・`denyWrite` と、`Edit(...)` のルールからサンドボックスに足される分が無視されるので、1つずつパスを書いてください(Edit ツールへの deny としては効きます。[Settings reference](https://code.claude.com/docs/en/settings-reference))

外すときは、`~/.claude/settings.json` から足した行を消し、`~/.claude/hooks/` の4つのファイルを消します。

## 既知の穴

- `excludedCommands` に入れたテストやビルドは、サンドボックスの外で動きます。その中で組み立てて渡すパスは、auto モードの分類器にも hook にも見えません。私は、削除処理のテストを偽のパスか一時ディレクトリだけで書くルールにしています。一時ディレクトリは `set -eu` と `"${D:?}"` の形で使い、作るのに失敗したら止まるようにします
- `excludedCommands` のパターンは広くなりがちです。hook は危ない形(`simctl spawn` など)を止めますが、シミュレーターに入れて動かすアプリ(`simctl install`・`launch`)、Xcode の Run Script、`xcodebuild` に渡すビルド設定の中身は見ません。`gh pr merge`・`gh workflow run` も、場所は確かめますが中身は止めていません。GitHub の、自分のものでないリポジトリへの push も通ります
- 削除用の hook がまだ判定しない書き方: perl・node などのスクリプトのファイルの中で組み立てるパス、`chr()` や `pack` で語を組み立てて隠す書き方、awk の `print > "ファイル"` による上書き、作業ディレクトリが一時ディレクトリのときの BSD の `find -f <パス>`、上書き(リダイレクト・`cp`・`sed -i`・`truncate`)。サンドボックスの中で動くので、外への書き込みは②が防ぎます
- hook がタイムアウトすると、Claude Code はそのまま処理を進めます(判定の途中で例外が出たときと、python が異常終了したときは、exit 2 にしてブロックします)。公式の記述では、v2.1.295 以降なら hook に `"onFailure": "block"` を足すとブロックされます(私の版では試していません)

ここに無い穴を見つけた方は、Issue で教えてください。

## English

Two PreToolUse hooks, tests, and example settings for Claude Code, written after an auto-mode session wiped much of my home directory.

- `guard-delete` (Bash and Monitor) blocks (exit 2) commands that delete or move files outside two places: the git repository containing the command's working directory, and the temp directories. It also blocks commands whose target it cannot resolve, unreadable hook input, the main git commands that discard uncommitted work or unmerged commits, and risky forms of commands that run outside the sandbox (`git push` to anything but a remote name or a GitHub URL, `xcrun simctl spawn`, destructive `gh` subcommands, `gh repo clone -- <git args>`, and `git push`/`xcodebuild`/git-running `gh` subcommands outside real repositories directly under `~/Projects`).
- `guard-write` (Edit, Write, MultiEdit, NotebookEdit) blocks writes outside git repositories under `~/Projects` (change with `GUARD_WRITE_ROOTS`) and the temp directories, and blocks writes to `.git` and to config that can run code outside the sandbox (`.claude/`, `.mcp.json`, `.husky/`, `.githooks/`, `.vscode/`, …).
- 67 tests. They never touch your files; they only use fake paths and their own temp folders.
- Tested on macOS only (Claude Code 2.1.292, python3 3.9.6 and 3.12). On Linux and WSL2, wildcards in `allowWrite`/`denyWrite`, and in the paths that `Edit(...)` rules add to the sandbox, have no effect (the rules still apply to the Edit tool).

Quick try: run the three commands above. `exit=2` means blocked.

What each layer does **not** stop: the sandbox does not cover the Read/Edit/Write tools, MCP servers, hooks or `excludedCommands`. Deny rules miss other spellings such as `/bin/rm` or `bash -c`. The hooks do not see paths built inside tests and script files, or overwrites by redirection, `cp` or `sed -i`. Inline perl/node/ruby/awk/swift code is blocked when it contains deletion-like words rather than parsed.

## License

MIT
