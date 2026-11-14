# GitWhy

Point at any line of code and get the story behind it: who wrote it, why it exists, what changed around it, and whether it is still alive.

GitWhy is a local command line tool. You give it a file and a line (or a function name) inside any git repository, and it reads that repository's own history with `git blame`, `git log -L` and `git diff` to answer one question: **why does this code exist?**

- Python 3.10+ standard library only. Nothing to install.
- Reads the local `.git` directory through `subprocess`. No network, no credentials, no GitHub API, no hosted service.
- Every claim it prints cites a commit sha and a line range, so you can check it with plain git.

## Why reading history matters

Code tells you *what* happens. It rarely tells you *why*. The reason a guard clause exists, why a constant is 37 and not 40, why a function takes an odd argument: that context was written down once, in a commit message, on the day the code changed. Git has kept it ever since. Most people never read it because `git blame` only shows the last person to touch a line, often for a whitespace fix, and `git log` shows the whole file's history, most of it unrelated.

GitWhy follows just the lines you care about, back to the commit that introduced them, and lays out everything that happened to them since. Before you delete code that looks dead, or rewrite code that looks wrong, read its story. It takes a second, and it is often the difference between a cleanup and a regression.

GitWhy deliberately does not search or map a codebase. It answers "why does this exist", never "what is here".

## How to run it

From a clone of this repository (or with the `gitwhy` folder on your `PYTHONPATH`):

```sh
# The story of one line: origin, code, timeline, renames, company, heat
python3 -m gitwhy story path/to/file.py --line 42

# The same for a whole function or class
python3 -m gitwhy story path/to/file.py --function parse_config

# A range of lines
python3 -m gitwhy story path/to/file.py --line 40 --end 55

# Only the company list, or only the heat verdict
python3 -m gitwhy company path/to/file.py --line 42
python3 -m gitwhy heat path/to/file.py --function parse_config

# Whole-file heat map: which parts are old, new, or churning
python3 -m gitwhy file path/to/file.py
```

The file can be in any git repository; GitWhy finds the repository from the file's own folder, so you can run it from anywhere.

Options:

| Option | Meaning |
| --- | --- |
| `--line N` | target line (1-based) |
| `--end N` | last line of a range that starts at `--line` |
| `--function NAME` | target a function or class; GitWhy finds its whole definition (Python, JavaScript/TypeScript, Go, Rust, Ruby, C-family) |
| `--rev REV` | read the file as it was at another commit or branch (default `HEAD`) |
| `--html PATH` | where to write the HTML report (default `gitwhy-report.html`) for `story` and `file` |
| `--no-html` | terminal output only |
| `--context N` | lines of code shown around the target (default 3) |
| `--top N` | company files to show (default 10) |
| `--now YYYY-MM-DD` | judge ages as of a fixed date, for reproducible reports |

GitWhy reads the committed version of the file at `--rev`. If your working copy has uncommitted edits, it says so and uses the committed text, because uncommitted lines have no history yet.

## How to read the story

A `story` run prints five sections, and writes the same content to the HTML report.

**STORY.** The commit that introduced the target lines: full sha prefix, author, date and the complete commit message. This is the "why", in the author's own words. If the lines as they read today were written by later commits, a `current text written by:` line says which commit wrote which lines, straight from `git blame`.

**CODE AT HEAD.** The target lines marked with `>`, with a few lines of context.

**TIMELINE.** Every commit that touched the target, oldest first, from `git log -L`. The first row is the introduction; each later row shows how many lines it added and removed, and the bracket under it cites the sha, the line range the target occupied after that commit, and the file name at that time.

**RENAMES.** GitWhy follows the file through renames wherever git can, and lists each one with its sha. It also says plainly where history really starts:

- `... is the true start of its history`: the file was created in that commit and nothing was removed alongside it.
- `WARNING: history is cut off at <sha>`: the file was created in a commit that deleted another file git rates as similar but below its 50% rename threshold. Git could not follow the line into the old file, so GitWhy names the old file and tells you how to keep reading (`gitwhy` on the old path at `<sha>^`).
- `WARNING: history may be cut off`: the file appeared in the same commit another file disappeared, but git sees no real similarity. It might be a rewrite under a new name.
- `WARNING: ... shallow clone`: the oldest commit has no parents only because the clone is shallow.

**COMPANY.** Files that changed in the same commits as the target, ranked by how often. `3/3 util.py` means util.py changed in all three commits that touched these lines. Code that always moves together is coupled, whether or not an import says so; when you change the target, check its company.

## How to read the heat verdict

The **HEAT** section gives one verdict and the exact rule that decided it, followed by every fact it considered, each citing a sha:

| Verdict | Rule |
| --- | --- |
| **HOT** | changed within the last 30 days, or touched by 3 or more commits in the last 365 days |
| **STALE** | untouched for 730 days (two years) or more, and its symbol is referenced fewer than 2 times elsewhere |
| **STABLE** | everything else: either untouched for two years but referenced 2 or more times elsewhere (old and depended on), or settled but not yet old enough to be stale |

"Referenced" means the name the target defines (the function, class or constant) appears as a whole word elsewhere in the repository at `--rev`, outside the target lines, counted with `git grep`. When the lines define no name, GitWhy says it did not count references, and age alone decides between STABLE and STALE.

Read STALE as "check this", not "delete this". Old code that nothing names may still be reached dynamically, by configuration, or from outside the repository. The verdict tells you where to look; the story tells you why it was written.

### Whole-file heat map

`python3 -m gitwhy file <file>` splits the file into regions by the commit that last wrote each line (merging small neighbours so there are at most 40), runs `git log -L` on each region, and tags it:

| Tag | Meaning |
| --- | --- |
| **CHURN** | touched by 4 or more commits |
| **NEW** | every line in the region is under 90 days old |
| **OLD** | untouched for a year or more |
| **SETTLED** | everything else |

The summary line gives the share of lines in each tag, so a newcomer sees at a glance whether a file is mostly settled ground or still moving.

## Examples against its own repository

GitWhy works on itself. This repository is young, so its own history is short and everything in it is HOT; the output below is real, from this repository.

```text
$ python3 -m gitwhy story README.md --line 1 --no-html
GitWhy: why does README.md:1 exist?

== STORY =====================================================================
Introduced in f93b7470 by battlettt <...> on 2026-11-13 21:59
  as line 1 of README.md [f93b7470 line 1]
  message:
    | Seed README

== TIMELINE (1 commit) =======================================================
  2026-11-13  f93b7470  battlettt          introduced  Seed README
              [f93b7470 line 1 of README.md]

== RENAMES ===================================================================
  README.md was created in f93b7470 and no file was removed in that commit, so
  this is the true start of its history.

== HEAT: HOT =================================================================
  Verdict HOT because changed 0 days ago in f93b7470, within the 30-day hot
  window.
```

```text
$ python3 -m gitwhy heat gitwhy/heat.py --function judge
GitWhy: why does gitwhy/heat.py:33-89 exist?
  function judge spans lines 33-89 at HEAD

== HEAT: HOT =================================================================
  Verdict HOT because changed 0 days ago in bcf5b96b, within the 30-day hot
  window.
  Evidence [lines 33-89]:
    - touched by 1 commit: introduced in bcf5b96b (2026-11-13)
    - last changed 0 days ago in bcf5b96b (2026-11-13)
    - 1 commit in the last 365 days: bcf5b96b
    - 'judge' is referenced 15 times elsewhere at the current revision
      ref gitwhy/story.py:7  from .heat import HeatVerdict, judge
```

For a richer history, the test suite builds a fixture repository with renames, co-changes, and years of commits. Keep it with `GITWHY_KEEP_FIXTURES=1 python3 tests/run_tests.py` and try:

```text
$ python3 -m gitwhy story tests/_fixtures/main/core/app.py --function compute --now 2026-11-01
...
== TIMELINE (3 commits) ======================================================
  2021-01-10  2a558666  Alice              introduced  Initial import
              [2a558666 lines 8-13 of app.py]
  2021-06-01  c61259da  Bob                +1 -1       Scale totals
              [c61259da lines 8-13 of app.py]
  2023-05-01  a7b4629f  Carol              +1 -1       Coerce values to int
              [a7b4629f lines 8-13 of core/app.py]

== RENAMES ===================================================================
  followed rename app.py -> core/app.py in 35fc2bb5 (100% similar)

== COMPANY: files that changed in the same 3 commits =========================
    3/3   #################### util.py
          [2a558666, c61259da, a7b4629f]

== HEAT: STABLE ==============================================================
  Verdict STABLE because untouched for 1279 days since a7b4629f, but 'compute'
  is referenced 3 times elsewhere, so it is depended on.
```

(Fixture shas change on every run, because the fixture is rebuilt each time.)

## Errors

GitWhy explains mistakes instead of printing a traceback, and exits with status 1:

```text
gitwhy: error: /tmp/notes.py is not inside a git repository
gitwhy: error: line 500 is past the end of gitwhy/cli.py (127 lines at HEAD)
gitwhy: error: no function or class named 'ghost' in gitwhy/heat.py at HEAD
gitwhy: error: scratch.py is not tracked by git at HEAD (commit it first)
```

Malformed arguments (no `--line` or `--function`, both at once, `--line abc`, `--line 0`) are rejected by the argument parser with status 2.

## Tests

```sh
python3 tests/run_tests.py
```

The suite uses plain asserts and no third-party runner. It builds its own small git repositories under `tests/_fixtures/` with real commits at fixed dates (renames, a rename git cannot follow, co-changed files, hot, stable and stale code), runs GitWhy against them, and deletes them afterwards. Blame, log, diff, name-status and grep parsing are all tested against real git output. Set `GITWHY_KEEP_FIXTURES=1` to keep the fixtures for exploring.

## Project layout

```
gitwhy/
  git.py       runs git with output-stabilising config, finds the repo root
  parse.py     blame porcelain, log records, unified diff, name-status, grep
  target.py    file + line / range / function resolution and error checks
  story.py     origin, timeline, company, rename notes, references
  heat.py      HOT / STABLE / STALE rules and file-mode region tags
  filemap.py   whole-file regions
  render.py    terminal output
  report.py    HTML report
  cli.py       command line interface
tests/run_tests.py
```

Built with Claude Opus as portfolio project 26 (Software Engineering).
