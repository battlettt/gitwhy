# GitWhy

Local CLI that answers one question: why does this code exist? Point it at a
file and a line (or function) and it reads local git history to tell the story.

## Rules
- Python 3.10+ standard library only. No dependencies, ever.
- Git is read through `subprocess` only, always via `gitwhy/git.py:run_git`,
  which pins config that would change git's output format.
- No network calls of any kind. No GitHub API, no PR fetching, no hosted
  anything. Everything comes from the local `.git` directory.
- Scope stays "why does this exist". Do not turn it into code search or a
  codebase map (that is Codebase Atlas, a different project).
- Every claim in output cites a commit sha and a line range.
- No em-dashes in docs or output text.

## Layout
- `gitwhy/git.py` runs git, finds the repo root, raises `GitWhyError`.
- `gitwhy/parse.py` parses blame porcelain, log records, unified diffs,
  name-status and grep output. Pure functions, no git calls.
- `gitwhy/target.py` resolves file + `--line`/`--end`/`--function` at a rev.
- `gitwhy/story.py` origin, timeline (`git log -L`), company, renames, refs.
- `gitwhy/heat.py` HOT / STABLE / STALE rules and thresholds, region tags.
- `gitwhy/filemap.py` whole-file regions from blame runs.
- `gitwhy/render.py` terminal text; `gitwhy/report.py` HTML report.
- `gitwhy/cli.py` argparse front end: `story`, `company`, `heat`, `file`.

## Commands
- Run: `python3 -m gitwhy story <file> --line <n>` (or `--function <name>`)
- Whole file: `python3 -m gitwhy file <file>`
- Tests: `python3 tests/run_tests.py` (builds fixture repos under
  `tests/_fixtures/`, deletes them after; `GITWHY_KEEP_FIXTURES=1` keeps them)

## Tests
- Plain asserts in `tests/run_tests.py`; any `test_*` function is collected.
- Parsers are tested against output from real fixture repos the tests commit
  themselves with fixed dates. Judge ages with the fixed `NOW` (2026-11-01).
- Add a test for every parser change and every new error message.

## Errors
- User mistakes raise `GitWhyError`; the CLI prints `gitwhy: error: ...` and
  exits 1. Bad arguments are argparse errors and exit 2.
