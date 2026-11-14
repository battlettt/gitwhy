"""Build the story of a target range: origin, timeline, company, heat, renames."""

import time
from dataclasses import dataclass, field

from .git import GitWhyError, is_shallow, run_git
from .heat import HeatVerdict, judge
from .parse import LOG_FORMAT, REC, parse_blame_porcelain, parse_grep, parse_log, parse_name_status


@dataclass
class TimelineEntry:
    commit: object          # parse.LogCommit
    path: str               # file path in that commit
    old_path: str           # path before the commit (differs on a rename)
    start: int              # target range as it stood after this commit
    end: int
    added: int
    removed: int
    created_file: bool

    @property
    def span(self):
        return "lines %d-%d" % (self.start, self.end) if self.start != self.end else "line %d" % self.start


@dataclass
class Companion:
    path: str
    count: int
    shas: list

    def share(self, total):
        return self.count / total if total else 0.0


@dataclass
class RenameNote:
    kind: str     # "followed", "cutoff", "possible-cutoff", "shallow", "origin"
    sha: str
    text: str
    old_path: str = ""
    new_path: str = ""


@dataclass
class Story:
    target: object
    blame: list
    timeline: list                       # oldest first
    company: list = field(default_factory=list)
    heat: HeatVerdict = None
    renames: list = field(default_factory=list)
    references: list = field(default_factory=list)
    now: float = 0.0

    @property
    def origin(self):
        return self.timeline[0] if self.timeline else None

    @property
    def later(self):
        return self.timeline[1:]

    @property
    def current_shas(self):
        """Commits that wrote the lines as they read today, per git blame."""
        seen = []
        for b in self.blame:
            if b.sha not in seen:
                seen.append(b.sha)
        return seen


def blame_range(target):
    out = run_git(target.repo, "blame", "--porcelain", "-L", "%d,%d" % (target.start, target.end),
                  target.rev, "--", target.path)
    return parse_blame_porcelain(out)


def line_history(repo, path, start, end, rev="HEAD"):
    """Every commit that touched lines start..end of path at rev, oldest first.

    Uses `git log -L`, which follows the range through edits and through
    renames git can detect.
    """
    out = run_git(repo, "log", "--no-ext-diff", "--format=" + LOG_FORMAT,
                  "-L", "%d,%d:%s" % (start, end, path), rev)
    commits = parse_log(out)
    entries = []
    for c in commits:
        fd = c.files[0] if c.files else None
        if fd is not None and fd.hunks:
            hunk = fd.hunks[0]
            s, e = hunk.new_range
            added, removed = fd.added, fd.removed
            new_path = fd.new_path or fd.old_path or path
            old_path = fd.old_path if fd.old_path is not None else ""
            created = fd.old_path is None
        else:
            s, e, added, removed = start, end, 0, 0
            new_path, old_path, created = path, path, False
        entries.append(TimelineEntry(commit=c, path=new_path, old_path=old_path, start=s, end=e,
                                     added=added, removed=removed, created_file=created))
    entries.reverse()
    return entries


def company(repo, timeline, own_paths, top=None):
    """Files that changed in the same commits as the target, most frequent first."""
    if not timeline:
        return []
    shas = [t.commit.sha for t in timeline]
    out = run_git(repo, "show", "--no-ext-diff", "--name-only", "--format=" + REC + "%H", *shas)
    counts = {}
    for chunk in out.split(REC)[1:]:
        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if not lines:
            continue
        sha = lines[0].strip()
        for f in dict.fromkeys(lines[1:]):
            if f in own_paths:
                continue
            counts.setdefault(f, []).append(sha)
    ranked = [Companion(path=p, count=len(s), shas=s) for p, s in counts.items()]
    ranked.sort(key=lambda c: (-c.count, c.path))
    return ranked[:top] if top else ranked


def file_renames(repo, path, rev):
    """Renames git can follow for path, newest first, as (sha, old, new, score)."""
    out = run_git(repo, "log", "--follow", "-M", "--name-status", "--format=" + REC + "%H", rev, "--", path,
                  check=False)
    found = []
    for sha, entries in parse_name_status(out):
        for letter, score, paths in entries:
            if letter == "R" and len(paths) == 2:
                found.append((sha, paths[0], paths[1], score))
    return found


def rename_notes(repo, path, rev, timeline):
    notes = []
    if timeline:
        oldest = timeline[0].commit.time
        for sha, old, new, score in reversed(file_renames(repo, path, rev)):
            when = run_git(repo, "show", "-s", "--format=%at", sha).strip()
            if when and int(when) < oldest:
                continue  # rename happened before this range existed
            notes.append(RenameNote("followed", sha,
                                    "followed rename %s -> %s in %s (%d%% similar)" % (old, new, sha[:8], score),
                                    old_path=old, new_path=new))
    if not timeline:
        return notes
    first = timeline[0]
    if is_shallow(repo) and not first.commit.parents:
        notes.append(RenameNote("shallow", first.commit.sha,
                                "history is cut off at %s because this is a shallow clone; "
                                "run `git fetch --unshallow` to see older commits" % first.commit.short))
        return notes
    if not first.created_file:
        return notes
    sha = first.commit.sha
    # Was this file really new, or a rename git could not match at its default
    # 50% similarity threshold? Ask again with a much lower threshold.
    low = parse_name_status(run_git(repo, "show", "-M20%", "--name-status", "--format=" + REC + "%H", sha,
                                    check=False))
    match = None
    deleted = []
    for _, entries in low:
        for letter, score, paths in entries:
            if letter == "R" and len(paths) == 2 and paths[1] == first.path:
                match = (paths[0], score)
            if letter == "D":
                deleted.append(paths[0])
    if match:
        old, score = match
        notes.append(RenameNote(
            "cutoff", sha,
            "history is cut off at %s: %s was created there, but it looks like a rename of %s that git "
            "rates only %d%% similar, under its 50%% rename threshold, so git could not follow the line "
            "back into %s. To keep reading, run gitwhy on %s at %s^."
            % (first.commit.short, first.path, old, score, old, old, first.commit.short),
            old_path=old, new_path=first.path))
    elif deleted:
        notes.append(RenameNote(
            "possible-cutoff", sha,
            "history may be cut off at %s: %s was created in the same commit that deleted %s. If that was "
            "a rename with heavy edits, git could not follow it, and older history lives under the old name."
            % (first.commit.short, first.path, ", ".join(deleted[:3])),
            new_path=first.path))
    else:
        notes.append(RenameNote(
            "origin", sha,
            "%s was created in %s and no file was removed in that commit, so this is the true start of "
            "its history." % (first.path, first.commit.short), new_path=first.path))
    return notes


def references(target):
    """Places at rev that mention the target's symbol, excluding the target itself."""
    if not target.symbol:
        return None
    out = run_git(target.repo, "grep", "-n", "-w", "-F", "-I", "-e", target.symbol, target.rev, "--",
                  check=False, ok_codes=(0, 1))
    hits = []
    for p, ln, text in parse_grep(out, target.rev):
        if p == target.path and target.start <= ln <= target.end:
            continue
        hits.append((p, ln, text.strip()))
    return hits


def build_story(target, now=None, top=10):
    now = time.time() if now is None else now
    blame = blame_range(target)
    timeline = line_history(target.repo, target.path, target.start, target.end, target.rev)
    if not timeline:
        raise GitWhyError("git found no history for %s" % target.label)
    own = {target.path} | {t.path for t in timeline} | {t.old_path for t in timeline if t.old_path}
    comp = company(target.repo, timeline, own, top=None)
    refs = references(target)
    heat = judge([t.commit for t in timeline], now, refs, target.symbol)
    story = Story(target=target, blame=blame, timeline=timeline, company=comp, heat=heat,
                  renames=rename_notes(target.repo, target.path, target.rev, timeline),
                  references=refs or [], now=now)
    story.top = top
    return story
