"""Parsers for the raw text git prints.

Each parser takes the exact stdout of one git command and returns plain
dataclasses. Nothing here runs git, so the parsers are easy to test against
output captured from a real repository.
"""

import re
from dataclasses import dataclass, field

# Separators GitWhy asks git to print between log records and fields.
REC = "\x1e"
FLD = "\x1f"
END = "\x1d"

LOG_FORMAT = REC + FLD.join(["%H", "%an", "%ae", "%at", "%ai", "%P", "%B"]) + END

_HUNK_RE = re.compile(r"^@@+ -(\d+)(?:,(\d+))? (?:-\d+(?:,\d+)? )*\+(\d+)(?:,(\d+))? @@")


@dataclass
class BlameLine:
    sha: str
    orig_line: int
    final_line: int
    author: str = ""
    author_mail: str = ""
    author_time: int = 0
    author_tz: str = "+0000"
    summary: str = ""
    filename: str = ""
    previous: str = ""
    boundary: bool = False
    content: str = ""

    @property
    def uncommitted(self):
        return set(self.sha) == {"0"}


def parse_blame_porcelain(text):
    """Parse `git blame --porcelain` output into one BlameLine per line."""
    commits = {}
    lines = []
    current = None
    for raw in text.split("\n"):
        if current is None:
            if not raw:
                continue
            parts = raw.split(" ")
            if len(parts) < 3 or len(parts[0]) < 7:
                raise ValueError("unexpected blame header: %r" % raw)
            sha = parts[0]
            info = commits.setdefault(sha, {})
            current = (sha, int(parts[1]), int(parts[2]), info)
            continue
        sha, orig, final, info = current
        if raw.startswith("\t"):
            lines.append(BlameLine(
                sha=sha, orig_line=orig, final_line=final,
                author=info.get("author", ""),
                author_mail=info.get("author-mail", "").strip("<>"),
                author_time=int(info.get("author-time", "0") or 0),
                author_tz=info.get("author-tz", "+0000"),
                summary=info.get("summary", ""),
                filename=info.get("filename", ""),
                previous=info.get("previous", ""),
                boundary="boundary" in info,
                content=raw[1:],
            ))
            current = None
            continue
        key, _, value = raw.partition(" ")
        info[key] = value
    return lines


def _unquote(path):
    """Undo git's C-style quoting of unusual paths."""
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        body = path[1:-1]
        raw = body.encode("latin-1", "backslashreplace").decode("unicode_escape")
        try:
            return raw.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return raw
    return path


def _strip_prefix(path):
    path = _unquote(path.split("\t")[0])
    if path == "/dev/null":
        return None
    if path[:2] in ("a/", "b/"):
        return path[2:]
    return path


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list = field(default_factory=list)

    @property
    def added(self):
        return sum(1 for ln in self.lines if ln.startswith("+"))

    @property
    def removed(self):
        return sum(1 for ln in self.lines if ln.startswith("-"))

    @property
    def new_range(self):
        """(first, last) line numbers this hunk covers in the new file."""
        if self.new_len == 0:
            return (self.new_start, self.new_start)
        return (self.new_start, self.new_start + self.new_len - 1)


@dataclass
class FileDiff:
    old_path: object  # str or None when the file was added
    new_path: object  # str or None when the file was deleted
    hunks: list = field(default_factory=list)
    rename_from: str = ""
    rename_to: str = ""
    similarity: int = 0

    @property
    def status(self):
        if self.old_path is None:
            return "added"
        if self.new_path is None:
            return "deleted"
        if self.rename_from or (self.old_path != self.new_path):
            return "renamed"
        return "modified"

    @property
    def added(self):
        return sum(h.added for h in self.hunks)

    @property
    def removed(self):
        return sum(h.removed for h in self.hunks)


def parse_diff(text):
    """Parse unified diff text (one or more files) into FileDiff objects."""
    files = []
    cur = None
    hunk = None
    for raw in text.split("\n"):
        if raw.startswith("diff --git ") or raw.startswith("diff --cc ") or raw.startswith("diff --combined "):
            cur = FileDiff(old_path="", new_path="")
            files.append(cur)
            hunk = None
            m = re.match(r'^diff --git (?:"?a/(.*?)"?) (?:"?b/(.*?)"?)$', raw)
            if m:
                cur.old_path, cur.new_path = m.group(1), m.group(2)
            else:
                name = raw.split(" ", 2)[2]
                cur.old_path = cur.new_path = name
            continue
        if cur is None:
            continue
        if hunk is None:
            if raw.startswith("--- "):
                cur.old_path = _strip_prefix(raw[4:])
                continue
            if raw.startswith("+++ "):
                cur.new_path = _strip_prefix(raw[4:])
                continue
            if raw.startswith("rename from "):
                cur.rename_from = _unquote(raw[len("rename from "):])
                continue
            if raw.startswith("rename to "):
                cur.rename_to = _unquote(raw[len("rename to "):])
                continue
            if raw.startswith("similarity index "):
                cur.similarity = int(raw.rstrip("%").split()[-1])
                continue
            if raw.startswith("new file mode"):
                cur.old_path = None
                continue
            if raw.startswith("deleted file mode"):
                cur.new_path = None
                continue
        m = _HUNK_RE.match(raw)
        if m:
            hunk = Hunk(
                old_start=int(m.group(1)),
                old_len=int(m.group(2)) if m.group(2) is not None else 1,
                new_start=int(m.group(3)),
                new_len=int(m.group(4)) if m.group(4) is not None else 1,
            )
            cur.hunks.append(hunk)
            continue
        if hunk is not None and raw[:1] in ("+", "-", " ") :
            hunk.lines.append(raw)
    return files


@dataclass
class LogCommit:
    sha: str
    author: str
    email: str
    time: int
    iso: str
    parents: list
    message: str
    files: list = field(default_factory=list)  # FileDiff list

    @property
    def subject(self):
        return self.message.split("\n", 1)[0].strip()

    @property
    def date(self):
        return self.iso[:10]

    @property
    def short(self):
        return self.sha[:8]


def parse_log(text):
    """Parse `git log --format=LOG_FORMAT [-p|-L ...]` output.

    Each record is the commit header followed by whatever diff git printed
    for it (empty with --no-patch).
    """
    commits = []
    for chunk in text.split(REC)[1:]:
        head, _, diff = chunk.partition(END)
        fields = head.split(FLD)
        if len(fields) < 7:
            raise ValueError("unexpected log record: %r" % head[:80])
        sha, author, email, at, iso, parents, message = fields[:7]
        commits.append(LogCommit(
            sha=sha, author=author, email=email, time=int(at), iso=iso,
            parents=parents.split(), message=message.strip("\n"),
            files=parse_diff(diff),
        ))
    return commits


def parse_name_status(text):
    """Parse `git log --format=%x1e%H --name-status` into (sha, entries).

    Each entry is (status_letter, score, paths) where paths has one item, or
    two for renames and copies (old, new).
    """
    out = []
    for chunk in text.split(REC)[1:]:
        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if not lines:
            continue
        sha = lines[0].strip()
        entries = []
        for ln in lines[1:]:
            parts = ln.split("\t")
            code = parts[0]
            letter = code[:1]
            score = int(code[1:]) if code[1:].isdigit() else 0
            entries.append((letter, score, [_unquote(p) for p in parts[1:]]))
        out.append((sha, entries))
    return out


def parse_grep(text, rev=None):
    """Parse `git grep -n` output into (path, line, text) tuples."""
    hits = []
    prefix = (rev + ":") if rev else ""
    for raw in text.split("\n"):
        if not raw:
            continue
        if prefix and raw.startswith(prefix):
            raw = raw[len(prefix):]
        m = re.match(r"^(.*?):(\d+):(.*)$", raw)
        if m:
            hits.append((m.group(1), int(m.group(2)), m.group(3)))
    return hits
