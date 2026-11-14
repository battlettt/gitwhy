"""GitWhy test suite. Plain asserts, no third-party runner.

Run: python3 tests/run_tests.py

Every test that touches git runs against small fixture repositories this file
builds with real commits under tests/_fixtures/, so the parsers see genuine
git output rather than hand-written strings.
"""

import contextlib
import datetime
import io
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from gitwhy import cli, heat, parse  # noqa: E402
from gitwhy.filemap import blame_runs, build_filemap, coalesce, Region  # noqa: E402
from gitwhy.git import GitWhyError, run_git  # noqa: E402
from gitwhy.parse import BlameLine, LogCommit  # noqa: E402
from gitwhy.report import story_html  # noqa: E402
from gitwhy.story import build_story, company, line_history  # noqa: E402
from gitwhy.target import Target, defined_symbol, find_function, load_file, resolve  # noqa: E402

FIXTURES = os.path.join(HERE, "_fixtures")
NOW = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc).timestamp()
DAY = 86400

GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
})


# --------------------------------------------------------------- fixture kit

def git(repo, *args, date=None, who="Alice"):
    env = dict(GIT_ENV)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = who
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = who.lower() + "@example.com"
    if date:
        stamp = date + "T12:00:00+0000"
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
    out = subprocess.run(["git", "-C", repo, *args], env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), out.stderr))
    return out.stdout


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def commit(repo, msg, date, who="Alice"):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", msg, date=date, who=who)
    return git(repo, "rev-parse", "HEAD").strip()


def new_repo(name):
    repo = os.path.join(FIXTURES, name)
    if os.path.exists(repo):
        shutil.rmtree(repo)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    return repo


APP_V1 = """import os


def helper(x):
    return x * 2


@cached
def compute(values):
    total = 0
    for v in values:
        total += v
    return total


class Engine:
    def run(self):
        return compute([1, 2, 3])


LIMIT = 10
"""

JS_V1 = """function greet(name) {
  const msg = "hi {" + name;
  return msg;
}

const later = () => 1;
"""

GO_V1 = """package main

func Add(a int, b int) int {
\treturn a + b
}
"""

SHAS = {}


def build_main():
    """Main fixture: renames, co-changes, hot, stable and stale code."""
    r = new_repo("main")
    write(r, "app.py", APP_V1)
    write(r, "util.py", "def util():\n    return 1\n")
    write(r, "README.md", "# fixture\n")
    write(r, "main.js", JS_V1)
    write(r, "main.go", GO_V1)
    write(r, "my file.txt", "spaced\n")
    SHAS["c1"] = commit(r, "Initial import\n\nStart the engine module.", "2021-01-10")
    write(r, "app.py", APP_V1.replace("total += v\n", "total += v * 1\n"))
    write(r, "util.py", "def util():\n    return 2\n")
    SHAS["c2"] = commit(r, "Scale totals\n\nThe billing team needs totals scaled\nbefore export.", "2021-06-01", "Bob")
    os.makedirs(os.path.join(r, "core"), exist_ok=True)
    git(r, "mv", "app.py", "core/app.py")
    SHAS["c3"] = commit(r, "Move app into core package", "2022-03-01")
    write(r, "core/app.py", APP_V1.replace("total += v\n", "total += int(v)\n"))
    write(r, "util.py", "def util():\n    return 3\n")
    write(r, "config.json", '{"limit": 10}\n')
    SHAS["c4"] = commit(r, "Coerce values to int", "2023-05-01", "Carol")
    write(r, "uses.py", "from core.app import compute\n\nprint(compute([4]))\n")
    SHAS["c5"] = commit(r, "Add a caller", "2023-05-02", "Carol")
    write(r, "notes.py", "def middle():\n    return 1\n")
    SHAS["c6"] = commit(r, "Add notes", "2025-08-01", "Dave")
    for i, d in enumerate(["2026-03-01", "2026-05-01", "2026-06-01"]):
        write(r, "main.go", GO_V1.replace("a + b", "a + b" + " + 0" * (i + 1)))
        SHAS["go%d" % i] = commit(r, "Tune Add (%d)" % i, d, "Erin")
    write(r, "main.js", JS_V1.replace('"hi {"', '"hello {"'))
    SHAS["js"] = commit(r, "Friendlier greeting ✨", "2026-10-25", "Dave")
    write(r, "my file.txt", "spaced out\n")
    SHAS["space"] = commit(r, "Edit spaced file", "2026-10-26", "Dave")
    return r


OLD_BODY = "".join("shared_line_number_%02d = 'this line is kept across the rewrite'\n" % i for i in range(4))
OLD_BODY += "".join("old_%d = %d\n" % (i, i) for i in range(4))
NEW_BODY = "".join("shared_line_number_%02d = 'this line is kept across the rewrite'\n" % i for i in range(4))
NEW_BODY += "".join("brand_new_value_%d = 'completely rewritten text number %d here'\n" % (i, i) for i in range(6))


def build_cutoff():
    """A rename with heavy edits that git cannot follow at its 50% threshold."""
    r = new_repo("cutoff")
    write(r, "old_name.py", OLD_BODY)
    SHAS["cut1"] = commit(r, "Original module", "2020-01-01")
    os.remove(os.path.join(r, "old_name.py"))
    write(r, "new_name.py", NEW_BODY)
    SHAS["cut2"] = commit(r, "Rewrite and rename module", "2020-02-01")
    return r


def build_unrelated():
    """A new file created in the same commit another file was deleted."""
    r = new_repo("unrelated")
    write(r, "gone.py", "x = 1\n")
    commit(r, "First", "2020-01-01")
    os.remove(os.path.join(r, "gone.py"))
    write(r, "fresh.txt", "alpha beta gamma delta\nepsilon zeta\n")
    SHAS["fresh"] = commit(r, "Replace with fresh notes", "2020-02-01")
    return r


def build_all():
    if os.path.exists(FIXTURES):
        shutil.rmtree(FIXTURES)
    os.makedirs(FIXTURES)
    return {"main": build_main(), "cutoff": build_cutoff(), "unrelated": build_unrelated(),
            "empty": new_repo("empty")}


R = {}


def P(repo, rel):
    return os.path.join(R[repo], rel)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv), stdout=out, stderr=err)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def lc(sha, days_ago, msg="m"):
    t = int(NOW - days_ago * DAY)
    iso = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
    return LogCommit(sha=sha * 40 if len(sha) == 1 else sha, author="A", email="a@x", time=t, iso=iso,
                     parents=[], message=msg)


def story_for(path, **kw):
    t = resolve(path, **kw)
    s = build_story(t, now=NOW)
    s.context = 3
    return s


# --------------------------------------------------------------- blame parsing

def test_blame_single_line_fields():
    out = git(R["main"], "blame", "--porcelain", "-L", "10,10", "HEAD", "--", "core/app.py")
    lines = parse.parse_blame_porcelain(out)
    assert len(lines) == 1
    b = lines[0]
    assert b.sha == SHAS["c1"]
    assert b.author == "Alice"
    assert b.author_mail == "alice@example.com"
    assert b.final_line == 10
    assert b.content == "    total = 0"


def test_blame_author_time_matches_commit_date():
    out = git(R["main"], "blame", "--porcelain", "-L", "1,1", "HEAD", "--", "core/app.py")
    b = parse.parse_blame_porcelain(out)[0]
    assert datetime.datetime.fromtimestamp(b.author_time, datetime.timezone.utc).date() == datetime.date(2021, 1, 10)
    assert b.author_tz == "+0000"


def test_blame_repeated_commit_keeps_info():
    out = git(R["main"], "blame", "--porcelain", "HEAD", "--", "core/app.py")
    lines = parse.parse_blame_porcelain(out)
    assert len(lines) == 21
    c1_lines = [b for b in lines if b.sha == SHAS["c1"]]
    assert len(c1_lines) == 20
    assert all(b.author == "Alice" and b.summary == "Initial import" for b in c1_lines)


def test_blame_line_numbers_in_order():
    out = git(R["main"], "blame", "--porcelain", "HEAD", "--", "core/app.py")
    assert [b.final_line for b in parse.parse_blame_porcelain(out)] == list(range(1, 22))


def test_blame_tab_content_preserved():
    out = git(R["main"], "blame", "--porcelain", "-L", "4,4", "HEAD", "--", "main.go")
    assert parse.parse_blame_porcelain(out)[0].content.startswith("\treturn a + b")


def test_blame_boundary_on_root_commit():
    out = git(R["main"], "blame", "--porcelain", "-L", "1,1", "HEAD", "--", "README.md")
    assert parse.parse_blame_porcelain(out)[0].boundary is True


def test_blame_filename_follows_rename():
    out = git(R["main"], "blame", "--porcelain", "-L", "1,1", "HEAD", "--", "core/app.py")
    assert parse.parse_blame_porcelain(out)[0].filename == "app.py"


def test_blame_previous_field_on_changed_line():
    out = git(R["main"], "blame", "--porcelain", "-L", "12,12", "HEAD", "--", "core/app.py")
    b = parse.parse_blame_porcelain(out)[0]
    assert b.sha == SHAS["c4"]
    assert b.previous.startswith(SHAS["c3"])


def test_blame_uncommitted_line():
    path = P("main", "README.md")
    with open(path, "a") as fh:
        fh.write("draft\n")
    try:
        out = git(R["main"], "blame", "--porcelain", "-L", "2,2", "--", "README.md")
        assert parse.parse_blame_porcelain(out)[0].uncommitted
    finally:
        git(R["main"], "checkout", "--", "README.md")


def test_blame_rejects_garbage():
    try:
        parse.parse_blame_porcelain("not a blame header\n")
    except ValueError:
        return
    raise AssertionError("garbage should raise ValueError")


# --------------------------------------------------------------- log parsing

def _log(*args):
    return parse.parse_log(git(R["main"], "log", "--format=" + parse.LOG_FORMAT, *args))


def test_log_fields():
    c = _log("-1", SHAS["c2"])[0]
    assert c.sha == SHAS["c2"]
    assert c.author == "Bob" and c.email == "bob@example.com"
    assert c.parents == [SHAS["c1"]]
    assert c.date == "2021-06-01"


def test_log_multiline_message():
    c = _log("-1", SHAS["c2"])[0]
    assert c.subject == "Scale totals"
    assert "The billing team needs totals scaled\nbefore export." in c.message


def test_log_root_has_no_parents():
    assert _log("-1", SHAS["c1"])[0].parents == []


def test_log_without_patch_has_no_files():
    assert all(c.files == [] for c in _log("-3"))


def test_log_newest_first():
    commits = _log()
    assert commits[0].sha == SHAS["space"]
    assert commits[-1].sha == SHAS["c1"]
    assert len(commits) == len(SHAS) - 3  # cutoff and unrelated shas live in other repos


def test_log_empty_text():
    assert parse.parse_log("") == []


def test_log_unicode_subject():
    assert _log("-1", SHAS["js"])[0].subject == "Friendlier greeting ✨"


def test_log_with_patch_attaches_diffs():
    c = _log("-1", "-p", SHAS["c4"])[0]
    assert sorted(f.new_path for f in c.files) == ["config.json", "core/app.py", "util.py"]


def test_log_L_follows_range():
    out = git(R["main"], "log", "--format=" + parse.LOG_FORMAT, "-L", "12,12:core/app.py")
    commits = parse.parse_log(out)
    assert [c.sha for c in commits] == [SHAS["c4"], SHAS["c2"], SHAS["c1"]]


# --------------------------------------------------------------- diff parsing

def _diff(*args):
    return parse.parse_diff(git(R["main"], "diff", *args))


def test_diff_modified_hunk_counts():
    files = _diff(SHAS["c1"], SHAS["c2"], "--", "util.py")
    assert len(files) == 1
    f = files[0]
    assert f.status == "modified"
    assert f.added == 1 and f.removed == 1


def test_diff_hunk_header_numbers():
    h = _diff("-U0", SHAS["c1"], SHAS["c2"], "--", "app.py")[0].hunks[0]
    assert (h.old_start, h.old_len, h.new_start, h.new_len) == (12, 1, 12, 1)
    assert h.new_range == (12, 12)


def test_diff_added_file():
    f = _diff(SHAS["c3"], SHAS["c4"], "--", "config.json")[0]
    assert f.old_path is None and f.new_path == "config.json"
    assert f.status == "added"
    assert f.hunks[0].new_start == 1


def test_diff_deleted_file():
    f = _diff(SHAS["c4"], SHAS["c3"], "--", "config.json")[0]
    assert f.new_path is None and f.status == "deleted"


def test_diff_rename_with_similarity():
    f = _diff("-M", SHAS["c2"], SHAS["c3"])[0]
    assert f.status == "renamed"
    assert f.rename_from == "app.py" and f.rename_to == "core/app.py"
    assert f.similarity == 100
    assert f.hunks == []


def test_diff_multiple_files():
    files = _diff(SHAS["c3"], SHAS["c4"])
    assert len(files) == 3
    assert {f.status for f in files} == {"added", "modified"}


def test_diff_path_with_space():
    f = _diff(SHAS["js"], SHAS["space"])[0]
    assert f.new_path == "my file.txt" and f.old_path == "my file.txt"


def test_diff_pure_deletion_range():
    h = parse.Hunk(old_start=5, old_len=2, new_start=4, new_len=0)
    assert h.new_range == (4, 4)


def test_diff_combined_header():
    files = parse.parse_diff("diff --cc f.py\nindex 1,2..3\n--- a/f.py\n+++ b/f.py\n@@@ -1,2 -1,2 +1,3 @@@\n  x\n++y\n")
    assert files[0].hunks[0].new_start == 1 and files[0].hunks[0].new_len == 3


def test_diff_quoted_path():
    assert parse._strip_prefix('"a/caf\\303\\251.py"') == "café.py"


def test_name_status_rename():
    out = git(R["main"], "log", "--follow", "--name-status", "--format=" + parse.REC + "%H", "--", "core/app.py")
    entries = dict(parse.parse_name_status(out))
    assert entries[SHAS["c3"]] == [("R", 100, ["app.py", "core/app.py"])]
    assert entries[SHAS["c1"]][0][0] == "A"


def test_grep_parse():
    out = git(R["main"], "grep", "-n", "-w", "compute", "HEAD", "--")
    hits = parse.parse_grep(out, "HEAD")
    assert ("uses.py", 3, "print(compute([4]))") in hits
    assert all(not p.startswith("HEAD:") for p, _, _ in hits)


# --------------------------------------------------------------- targets

def test_resolve_line():
    t = resolve(P("main", "core/app.py"), line=12)
    assert t.path == "core/app.py" and t.start == t.end == 12
    assert t.label == "core/app.py:12"


def test_resolve_range():
    t = resolve(P("main", "core/app.py"), line=10, end=12)
    assert (t.start, t.end) == (10, 12) and t.span == "lines 10-12"


def test_resolve_from_other_cwd():
    old = os.getcwd()
    os.chdir(os.path.join(R["main"], "core"))
    try:
        assert resolve("app.py", line=1).path == "core/app.py"
    finally:
        os.chdir(old)


def test_line_past_end():
    try:
        resolve(P("main", "core/app.py"), line=99)
    except GitWhyError as exc:
        assert "line 99 is past the end of core/app.py (21 lines" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_end_past_end():
    try:
        resolve(P("main", "core/app.py"), line=20, end=30)
    except GitWhyError as exc:
        assert "past the end" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_line_zero():
    try:
        resolve(P("main", "core/app.py"), line=0)
    except GitWhyError as exc:
        assert "start at 1" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_end_before_line():
    try:
        resolve(P("main", "core/app.py"), line=10, end=5)
    except GitWhyError as exc:
        assert "before" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_function_python_with_decorator():
    t = resolve(P("main", "core/app.py"), function="compute")
    assert (t.start, t.end) == (8, 13)
    assert t.symbol == "compute"


def test_function_python_method():
    t = resolve(P("main", "core/app.py"), function="run")
    assert (t.start, t.end) == (17, 18)


def test_function_class():
    t = resolve(P("main", "core/app.py"), function="Engine")
    assert (t.start, t.end) == (16, 18)


def test_function_js_braces():
    t = resolve(P("main", "main.js"), function="greet")
    assert (t.start, t.end) == (1, 4)


def test_function_js_arrow():
    t = resolve(P("main", "main.js"), function="later")
    assert (t.start, t.end) == (6, 6)


def test_function_go():
    t = resolve(P("main", "main.go"), function="Add")
    assert (t.start, t.end) == (3, 5)


def test_function_missing():
    try:
        resolve(P("main", "core/app.py"), function="nope")
    except GitWhyError as exc:
        assert "no function or class named 'nope'" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_find_function_prefers_definition_over_call():
    lines = ["int main() {", "  return compute(1);", "}", "int compute(int x) {", "  return x;", "}"]
    assert find_function(lines, "compute") == (4, 6)


def test_find_function_ignores_brace_in_string():
    lines = ["function f() {", "  s = \"}\";", "  return s;", "}"]
    assert find_function(lines, "f") == (1, 4)


def test_outside_repo():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "x.py")
        with open(path, "w") as fh:
            fh.write("x = 1\n")
        try:
            resolve(path, line=1)
        except GitWhyError as exc:
            assert "not inside a git repository" in str(exc)
            return
        raise AssertionError("expected GitWhyError")
    finally:
        shutil.rmtree(tmp)


def test_missing_path():
    try:
        resolve(os.path.join(R["main"], "nodir", "x.py"), line=1)
    except GitWhyError as exc:
        assert "does not exist" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_directory_path():
    try:
        resolve(os.path.join(R["main"], "core"), line=1)
    except GitWhyError as exc:
        assert "is a directory" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_untracked_file():
    path = P("main", "scratch.py")
    with open(path, "w") as fh:
        fh.write("x = 1\n")
    try:
        resolve(path, line=1)
    except GitWhyError as exc:
        assert "not tracked" in str(exc)
        return
    finally:
        os.remove(path)
    raise AssertionError("expected GitWhyError")


def test_repo_without_commits():
    path = P("empty", "a.py")
    with open(path, "w") as fh:
        fh.write("x\n")
    try:
        resolve(path, line=1)
    except GitWhyError as exc:
        assert "no commits yet" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_bad_revision():
    try:
        resolve(P("main", "core/app.py"), line=1, rev="no-such-branch")
    except GitWhyError as exc:
        assert "does not exist" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


def test_old_revision_reads_old_path():
    t = resolve(P("main", "core/app.py"), line=12, rev=SHAS["c4"])
    assert t.lines[11].strip() == "total += int(v)"


def test_dirty_flag():
    path = P("main", "util.py")
    with open(path, "a") as fh:
        fh.write("# wip\n")
    try:
        assert resolve(path, line=1).dirty is True
    finally:
        git(R["main"], "checkout", "--", "util.py")
    assert resolve(path, line=1).dirty is False


def test_defined_symbol():
    assert defined_symbol(["def compute(values):"]) == "compute"
    assert defined_symbol(["LIMIT = 10"]) == "LIMIT"
    assert defined_symbol(["export const later = () => 1;"]) == "later"
    assert defined_symbol(["    total += v"]) == ""
    assert defined_symbol(["x = 1"]) == ""  # too short to grep usefully


# --------------------------------------------------------------- story

def test_story_origin():
    s = story_for(P("main", "core/app.py"), line=12)
    assert s.origin.commit.sha == SHAS["c1"]
    assert s.origin.created_file is True


def test_story_timeline_order_and_later():
    s = story_for(P("main", "core/app.py"), line=12)
    assert [e.commit.sha for e in s.timeline] == [SHAS["c1"], SHAS["c2"], SHAS["c4"]]
    assert [e.commit.sha for e in s.later] == [SHAS["c2"], SHAS["c4"]]


def test_story_timeline_cites_line_ranges():
    s = story_for(P("main", "core/app.py"), line=12)
    assert all((e.start, e.end) == (12, 12) for e in s.timeline)
    assert s.timeline[1].added == 1 and s.timeline[1].removed == 1


def test_story_follows_rename_paths():
    s = story_for(P("main", "core/app.py"), line=12)
    assert s.origin.path == "app.py"
    assert s.timeline[-1].path == "core/app.py"


def test_story_rename_note():
    s = story_for(P("main", "core/app.py"), line=12)
    followed = [r for r in s.renames if r.kind == "followed"]
    assert len(followed) == 1
    assert followed[0].sha == SHAS["c3"]
    assert "app.py -> core/app.py" in followed[0].text


def test_story_true_origin_note():
    s = story_for(P("main", "core/app.py"), line=12)
    assert any(r.kind == "origin" for r in s.renames)
    assert not any(r.kind in ("cutoff", "possible-cutoff") for r in s.renames)


def test_story_current_shas():
    s = story_for(P("main", "core/app.py"), function="compute")
    assert s.current_shas == [SHAS["c1"], SHAS["c4"]]


def test_story_untouched_line_single_entry():
    s = story_for(P("main", "core/app.py"), line=4)
    assert len(s.timeline) == 1 and s.later == []


def test_company_ranks_cochanged_files():
    s = story_for(P("main", "core/app.py"), line=12)
    top = s.company[0]
    assert top.path == "util.py" and top.count == 3
    assert top.shas == [SHAS["c1"], SHAS["c2"], SHAS["c4"]]


def test_company_excludes_own_names():
    s = story_for(P("main", "core/app.py"), line=12)
    names = [c.path for c in s.company]
    assert "app.py" not in names and "core/app.py" not in names
    assert "config.json" in names


def test_company_empty_for_solo_commits():
    s = story_for(P("main", "main.go"), line=4)
    hist = [e.commit.sha for e in s.timeline]
    assert hist[1:] == [SHAS["go0"], SHAS["go1"], SHAS["go2"]]
    comp = company(R["main"], s.timeline[1:], {"main.go"})
    assert comp == []


def test_line_history_direct():
    entries = line_history(R["main"], "main.js", 2, 2)
    assert [e.commit.sha for e in entries] == [SHAS["c1"], SHAS["js"]]


# --------------------------------------------------------------- heat

def test_heat_hot_recent():
    v = heat.judge([lc("a", 400), lc("b", 5)], NOW)
    assert v.verdict == "HOT" and "5 days ago" in v.reason and "bbbbbbbb" in v.reason


def test_heat_hot_busy():
    v = heat.judge([lc("a", 300), lc("b", 200), lc("c", 100)], NOW)
    assert v.verdict == "HOT" and "3 commits in the last 365 days" in v.reason


def test_heat_stale_without_refs():
    v = heat.judge([lc("a", 1500)], NOW, refs=[], symbol="helper")
    assert v.verdict == "STALE" and "only 0 references" in v.reason


def test_heat_stale_without_symbol():
    v = heat.judge([lc("a", 1500)], NOW, refs=None)
    assert v.verdict == "STALE" and "no symbol" in v.reason
    assert any("not counted" in e for e in v.evidence)


def test_heat_stable_depended_on():
    v = heat.judge([lc("a", 1500)], NOW, refs=[1, 2, 3], symbol="compute")
    assert v.verdict == "STABLE" and "depended on" in v.reason and v.refs == 3


def test_heat_stable_middle_age():
    v = heat.judge([lc("a", 400)], NOW, refs=[], symbol="mid")
    assert v.verdict == "STABLE" and "not yet old enough" in v.reason


def test_heat_boundaries():
    assert heat.judge([lc("a", 30)], NOW).verdict == "HOT"
    assert heat.judge([lc("a", 31)], NOW).verdict == "STABLE"
    assert heat.judge([lc("a", 729)], NOW).verdict == "STABLE"
    assert heat.judge([lc("a", 730)], NOW).verdict == "STALE"


def test_heat_evidence_counts_and_shas():
    v = heat.judge([lc("a", 900), lc("b", 800)], NOW, refs=[], symbol="x1y")
    assert v.changes == 2 and v.last_sha == "b" * 40
    assert "touched by 2 commits" in v.evidence[0] and "aaaaaaaa" in v.evidence[0]
    assert "changed 1 time since" in v.evidence[0]


def test_heat_requires_commits():
    try:
        heat.judge([], NOW)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_heat_fixture_stable_compute():
    s = story_for(P("main", "core/app.py"), function="compute")
    assert s.heat.verdict == "STABLE", s.heat.reason
    assert s.heat.refs == 3  # Engine.run plus two lines in uses.py
    assert {p for p, _, _ in s.references} == {"core/app.py", "uses.py"}


def test_heat_fixture_stale_helper():
    s = story_for(P("main", "core/app.py"), function="helper")
    assert s.heat.verdict == "STALE" and s.heat.refs == 0


def test_heat_fixture_hot_busy_go():
    s = story_for(P("main", "main.go"), line=4)
    assert s.heat.verdict == "HOT" and s.heat.recent == 3


def test_heat_fixture_hot_recent_js():
    s = story_for(P("main", "main.js"), line=2)
    assert s.heat.verdict == "HOT" and s.heat.days_since == 6  # 2026-10-25 12:00 to 2026-11-01 00:00


def test_heat_fixture_stable_middle():
    s = story_for(P("main", "notes.py"), function="middle")
    assert s.heat.verdict == "STABLE" and s.heat.days_since == 456


def test_region_tags():
    assert heat.region_tag(4, 500, 900) == "CHURN"
    assert heat.region_tag(1, 10, 10) == "NEW"
    assert heat.region_tag(1, 400, 400) == "OLD"
    assert heat.region_tag(2, 100, 200) == "SETTLED"


# --------------------------------------------------------------- renames

def test_cutoff_detected():
    s = story_for(P("cutoff", "new_name.py"), line=1)
    cut = [r for r in s.renames if r.kind == "cutoff"]
    assert len(cut) == 1, [r.text for r in s.renames]
    assert cut[0].old_path == "old_name.py" and cut[0].sha == SHAS["cut2"]
    assert "under its 50% rename threshold" in cut[0].text


def test_cutoff_git_really_cannot_follow():
    out = git(R["cutoff"], "log", "--follow", "--format=%H", "--", "new_name.py")
    assert out.split() == [SHAS["cut2"]]


def test_possible_cutoff_when_dissimilar():
    s = story_for(P("unrelated", "fresh.txt"), line=1)
    kinds = [r.kind for r in s.renames]
    assert kinds == ["possible-cutoff"], kinds
    assert "gone.py" in s.renames[0].text


# --------------------------------------------------------------- file mode

def _target(repo, rel):
    root, path, lines, dirty = load_file(P(repo, rel))
    return Target(repo=root, path=path, rev="HEAD", start=1, end=len(lines), lines=lines)


def test_filemap_covers_every_line():
    fm = build_filemap(_target("main", "core/app.py"), now=NOW)
    assert fm.regions[0].start == 1 and fm.regions[-1].end == 21
    for a, b in zip(fm.regions, fm.regions[1:]):
        assert b.start == a.end + 1


def test_filemap_regions_split_by_commit():
    fm = build_filemap(_target("main", "core/app.py"), now=NOW)
    assert [(r.start, r.end) for r in fm.regions] == [(1, 11), (12, 12), (13, 21)]
    assert fm.regions[1].newest.sha == SHAS["c4"]


def test_filemap_tags():
    fm = build_filemap(_target("main", "main.go"), now=NOW)
    tags = {(r.start, r.end): r.tag for r in fm.regions}
    assert tags[(4, 4)] == "CHURN"
    assert tags[(1, 3)] == "OLD"


def test_filemap_new_tag():
    fm = build_filemap(_target("main", "my file.txt"), now=NOW)
    assert fm.regions[0].tag == "NEW"
    assert fm.share("NEW") == 1.0


def test_coalesce_limit():
    regions = [Region(start=i, end=i, blame=[BlameLine(sha=str(i) * 8, orig_line=i, final_line=i)])
               for i in range(1, 11)]
    merged = coalesce(regions, limit=4)
    assert len(merged) == 4
    assert merged[0].start == 1 and merged[-1].end == 10
    assert sum(r.size for r in merged) == 10


def test_blame_runs_group_same_commit():
    bl = [BlameLine(sha="a" * 8, orig_line=i, final_line=i) for i in (1, 2)] + \
         [BlameLine(sha="b" * 8, orig_line=3, final_line=3), BlameLine(sha="a" * 8, orig_line=4, final_line=4)]
    assert [(r.start, r.end) for r in blame_runs(bl)] == [(1, 2), (3, 3), (4, 4)]


# --------------------------------------------------------------- CLI

def test_cli_story_writes_html():
    out_html = os.path.join(FIXTURES, "story.html")
    code, out, err = run_cli("story", P("main", "core/app.py"), "--line", "12", "--now", "2026-11-01",
                             "--html", out_html)
    assert code == 0, err
    assert "Introduced in %s by Alice" % SHAS["c1"][:8] in out
    assert "HEAT: STABLE" in out or "HEAT: STALE" in out
    with open(out_html, encoding="utf-8") as fh:
        page = fh.read()
    for sha in (SHAS["c1"], SHAS["c2"], SHAS["c4"]):
        assert sha[:8] + " line 12" in page
    assert "Company" in page and "util.py" in page


def test_cli_story_text_sections():
    code, out, _ = run_cli("story", P("main", "core/app.py"), "--line", "12", "--no-html", "--now", "2026-11-01")
    assert code == 0
    for part in ("== STORY", "== TIMELINE (3 commits)", "== RENAMES", "== COMPANY", "== HEAT"):
        assert part in out, part
    assert ">    12 |         total += int(v)" in out


def test_cli_no_html_writes_nothing():
    old = os.getcwd()
    os.chdir(FIXTURES)
    try:
        if os.path.exists("gitwhy-report.html"):
            os.remove("gitwhy-report.html")
        code, _, _ = run_cli("story", P("main", "core/app.py"), "--line", "1", "--no-html")
        assert code == 0 and not os.path.exists("gitwhy-report.html")
        code, out, _ = run_cli("story", P("main", "core/app.py"), "--line", "1")
        assert code == 0 and os.path.exists("gitwhy-report.html")
    finally:
        os.chdir(old)


def test_cli_company_only():
    code, out, _ = run_cli("company", P("main", "core/app.py"), "--line", "12")
    assert code == 0 and "COMPANY" in out and "util.py" in out and "== STORY" not in out


def test_cli_heat_only():
    code, out, _ = run_cli("heat", P("main", "core/app.py"), "--function", "helper", "--now", "2026-11-01")
    assert code == 0 and "HEAT: STALE" in out and "Verdict STALE because untouched" in out


def test_cli_file_mode():
    out_html = os.path.join(FIXTURES, "file.html")
    code, out, _ = run_cli("file", P("main", "main.go"), "--now", "2026-11-01", "--html", out_html)
    assert code == 0 and "CHURN" in out and "heat map of main.go" in out
    with open(out_html, encoding="utf-8") as fh:
        assert SHAS["go2"][:8] + " line 4" in fh.read()


def test_cli_missing_target_flag():
    code, _, err = run_cli("story", P("main", "core/app.py"))
    assert code == 2 and "one of the arguments --line --function is required" in err


def test_cli_line_and_function_conflict():
    code, _, err = run_cli("story", P("main", "core/app.py"), "--line", "1", "--function", "compute")
    assert code == 2 and "not allowed with" in err


def test_cli_line_not_a_number():
    code, _, err = run_cli("story", P("main", "core/app.py"), "--line", "abc")
    assert code == 2 and "not a whole number" in err


def test_cli_line_negative():
    code, _, err = run_cli("story", P("main", "core/app.py"), "--line", "-3")
    assert code == 2


def test_cli_line_zero():
    code, _, err = run_cli("heat", P("main", "core/app.py"), "--line", "0")
    assert code == 2 and "lines start at 1" in err


def test_cli_no_command():
    code, _, err = run_cli()
    assert code == 2


def test_cli_unknown_command():
    code, _, err = run_cli("blame", "x.py")
    assert code == 2 and "invalid choice" in err


def test_cli_bad_now():
    code, _, err = run_cli("heat", P("main", "core/app.py"), "--line", "1", "--now", "yesterday")
    assert code == 2 and "not a date" in err


def test_cli_line_past_end_exit_1():
    code, out, err = run_cli("story", P("main", "core/app.py"), "--line", "500", "--no-html")
    assert code == 1 and out == ""
    assert err.startswith("gitwhy: error: line 500 is past the end of core/app.py")


def test_cli_outside_repo_exit_1():
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "a.py")
        open(path, "w").close()
        code, _, err = run_cli("file", path, "--no-html")
        assert code == 1 and "not inside a git repository" in err
    finally:
        shutil.rmtree(tmp)


def test_cli_function_missing_exit_1():
    code, _, err = run_cli("story", P("main", "core/app.py"), "--function", "ghost", "--no-html")
    assert code == 1 and "no function or class named 'ghost'" in err


def test_cli_end_with_function_exit_1():
    code, _, err = run_cli("story", P("main", "core/app.py"), "--function", "compute", "--end", "3", "--no-html")
    assert code == 1 and "--end only works with --line" in err


def test_cli_cutoff_warning_printed():
    code, out, _ = run_cli("story", P("cutoff", "new_name.py"), "--line", "1", "--no-html")
    assert code == 0 and "WARNING: history is cut off at %s" % SHAS["cut2"][:8] in out


def test_html_escapes_code_and_messages():
    r = R["main"]
    write(r, "evil.html", "<script>alert(1)</script>\n")
    sha = commit(r, "Add <b>bold</b> & risky", "2026-10-30")
    try:
        s = story_for(P("main", "evil.html"), line=1)
        page = story_html(s)
        assert "<script>alert" not in page and "&lt;script&gt;" in page
        assert "&lt;b&gt;bold&lt;/b&gt; &amp; risky" in page
    finally:
        git(r, "reset", "-q", "--hard", sha + "^")


def test_module_runs_as_subprocess():
    proc = subprocess.run([sys.executable, "-m", "gitwhy", "heat", P("main", "main.js"), "--line", "2",
                           "--now", "2026-11-01"], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "HEAT: HOT" in proc.stdout


def test_runs_on_its_own_repo():
    proc = subprocess.run([sys.executable, "-m", "gitwhy", "story", os.path.join(ROOT, "README.md"),
                           "--line", "1", "--no-html"], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "== TIMELINE" in proc.stdout and "== HEAT" in proc.stdout


def test_cli_keeps_message_line_breaks():
    code, out, _ = run_cli("story", P("main", "core/app.py"), "--line", "12", "--no-html")
    assert code == 0
    assert "    | The billing team needs totals scaled\n    | before export." not in out  # origin is c1
    assert "    | Initial import\n    |\n    | Start the engine module." in out


def test_cli_long_function_is_trimmed_in_terminal():
    r = R["main"]
    body = "def big():\n" + "".join("    x%d = %d\n" % (i, i) for i in range(60)) + "    return 0\n"
    write(r, "big.py", body)
    sha = commit(r, "Add a long function", "2026-10-30")
    try:
        code, out, _ = run_cli("story", P("main", "big.py"), "--function", "big", "--no-html")
        assert code == 0 and "lines not shown (the HTML report has them all)" in out
        assert ">     1 | def big():" in out and ">    62 |     return 0" in out
        s = story_for(P("main", "big.py"), function="big")
        assert "x40 = 40" in story_html(s)
    finally:
        git(r, "reset", "-q", "--hard", sha + "^")


def test_cli_file_mode_region_plural():
    code, out, _ = run_cli("file", P("main", "README.md"), "--no-html")
    assert code == 0 and "(1 lines, 1 region)" in out


def test_run_git_error_message():
    try:
        run_git(R["main"], "rev-parse", "--verify", "nope-nope")
    except GitWhyError as exc:
        assert "git rev-parse failed" in str(exc)
        return
    raise AssertionError("expected GitWhyError")


# --------------------------------------------------------------- runner

def main():
    R.update(build_all())
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print("ok    " + name)
        except Exception:
            failed.append(name)
            print("FAIL  " + name)
            traceback.print_exc()
    print("\n%d passed, %d failed, %d total" % (passed, len(failed), len(tests)))
    if not os.environ.get("GITWHY_KEEP_FIXTURES"):
        shutil.rmtree(FIXTURES, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
