"""Turn the user's file + line/function into a checked target range."""

import os
import re
from dataclasses import dataclass, field

from .git import GitWhyError, find_repo_root, run_git


@dataclass
class Target:
    repo: str
    path: str          # repo-relative, forward slashes
    rev: str
    start: int
    end: int
    lines: list = field(default_factory=list)  # whole file at rev
    symbol: str = ""   # function or name defined at the target, if any
    function: str = ""  # set when the user asked for --function
    dirty: bool = False  # working tree differs from rev for this file

    @property
    def label(self):
        if self.start == self.end:
            return "%s:%d" % (self.path, self.start)
        return "%s:%d-%d" % (self.path, self.start, self.end)

    @property
    def span(self):
        return "lines %d-%d" % (self.start, self.end) if self.start != self.end else "line %d" % self.start


def load_file(file_arg, rev="HEAD"):
    """Resolve a user path to (repo_root, repo_relative_path, lines_at_rev, dirty)."""
    abspath = os.path.abspath(file_arg)
    if not os.path.exists(abspath):
        # A path that only exists in history is fine as long as its folder is in a repo.
        if not os.path.isdir(os.path.dirname(abspath)):
            raise GitWhyError("%s does not exist" % file_arg)
    if os.path.isdir(abspath):
        raise GitWhyError("%s is a directory; point GitWhy at a file" % file_arg)
    repo = find_repo_root(abspath)
    rel = os.path.relpath(os.path.realpath(abspath), os.path.realpath(repo)).replace(os.sep, "/")
    if rel.startswith("../"):
        raise GitWhyError("%s is outside the git repository at %s" % (file_arg, repo))
    if not run_git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}", check=False).strip():
        if rev == "HEAD":
            raise GitWhyError("the repository at %s has no commits yet" % repo)
        raise GitWhyError("revision %r does not exist in %s" % (rev, repo))
    if not _exists_at(repo, rev, rel):
        raise GitWhyError("%s is not tracked by git at %s (commit it first)" % (rel, rev))
    text = run_git(repo, "show", "%s:%s" % (rev, rel))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    dirty = False
    if rev == "HEAD" and os.path.exists(abspath):
        dirty = bool(run_git(repo, "diff", "--name-only", "HEAD", "--", rel, check=False).strip())
    return repo, rel, lines, dirty


def _exists_at(repo, rev, rel):
    out = run_git(repo, "ls-tree", "--name-only", rev, "--", rel, check=False)
    return rel in out.split("\n")


def resolve(file_arg, line=None, end=None, function=None, rev="HEAD"):
    repo, rel, lines, dirty = load_file(file_arg, rev)
    total = len(lines)
    if total == 0:
        raise GitWhyError("%s is empty at %s" % (rel, rev))
    if function:
        start, stop = find_function(lines, function)
        if start is None:
            raise GitWhyError("no function or class named %r in %s at %s" % (function, rel, rev))
        symbol = function
    else:
        if line is None:
            raise GitWhyError("give --line or --function")
        if line < 1:
            raise GitWhyError("line numbers start at 1 (got %d)" % line)
        if line > total:
            raise GitWhyError("line %d is past the end of %s (%d lines at %s)" % (line, rel, total, rev))
        stop = line if end is None else end
        if stop < line:
            raise GitWhyError("--end %d is before --line %d" % (stop, line))
        if stop > total:
            raise GitWhyError("line %d is past the end of %s (%d lines at %s)" % (stop, rel, total, rev))
        start = line
        symbol = defined_symbol(lines[start - 1:stop])
    return Target(repo=repo, path=rel, rev=rev, start=start, end=stop, lines=lines,
                  symbol=symbol or "", function=function or "", dirty=dirty)


# Patterns that introduce a named function or class in common languages.
def _def_patterns(name):
    n = re.escape(name)
    return [
        re.compile(r"^(\s*)(?:async\s+)?def\s+%s\s*[\(:\[]" % n),                 # Python def
        re.compile(r"^(\s*)class\s+%s\b" % n),                                    # Python/JS/Java class
        re.compile(r"^(\s*)(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*%s\s*\(" % n),
        re.compile(r"^(\s*)(?:export\s+)?(?:const|let|var)\s+%s\s*=\s*(?:async\s*)?(?:function|\(|[A-Za-z_$][\w$]*\s*=>)" % n),
        re.compile(r"^(\s*)func\s+(?:\([^)]*\)\s*)?%s\s*\(" % n),                  # Go
        re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+%s\b" % n),   # Rust
        re.compile(r"^(\s*)(?:def|sub)\s+%s\b" % n),                               # Ruby, Perl
        re.compile(r"^(\s*)(?!(?:return|else|if|while|for|switch|case|new|throw|await|yield)\b)"
                   r"[A-Za-z_][\w\s\*&:<>,\[\]]*?\b%s\s*\([^;]*$" % n),             # C, C++, Java, C#
    ]


def find_function(lines, name):
    """Return (start, end) 1-based for the first definition of name, or (None, None)."""
    for pat in _def_patterns(name):
        for idx, text in enumerate(lines):
            m = pat.match(text)
            if m:
                start = idx
                # Pull decorators above a Python def into the range.
                while start > 0 and lines[start - 1].strip().startswith("@") and \
                        _indent(lines[start - 1]) == len(m.group(1)):
                    start -= 1
                stop = _block_end(lines, idx, len(m.group(1)))
                return start + 1, stop + 1
    return None, None


def _indent(text):
    return len(text) - len(text.lstrip(" \t"))


def _block_end(lines, idx, indent):
    """Find the last line of the block that starts at idx (0-based)."""
    # Brace languages: match braces from the first "{" within 3 lines.
    for look in range(idx, min(idx + 3, len(lines))):
        if "{" in _code(lines[look]):
            depth = 0
            for j in range(look, len(lines)):
                code = _code(lines[j])
                depth += code.count("{") - code.count("}")
                if depth <= 0 and j >= look:
                    return j
            return len(lines) - 1
        if lines[look].rstrip().endswith(":"):
            break
    # Indentation languages (Python): block ends before the next line at or
    # left of the definition's indent.
    last = idx
    for j in range(idx + 1, len(lines)):
        if not lines[j].strip():
            continue
        if _indent(lines[j]) <= indent:
            break
        last = j
    return last


def _code(text):
    """Drop string literals and line comments so braces in them do not count."""
    text = re.sub(r'"(?:\\.|[^"\\])*"' + r"|'(?:\\.|[^'\\])*'", '""', text)
    return re.split(r"//|#", text, maxsplit=1)[0]


_SYMBOL_RES = [
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"^([A-Za-z_][\w]*)\s*(?::[^=]*)?=(?!=)"),  # module-level NAME = value
]


def defined_symbol(lines):
    """Return the name the given lines define (def, class, constant), or ''."""
    for text in lines:
        for pat in _SYMBOL_RES:
            m = pat.match(text)
            if m and len(m.group(1)) >= 3:
                return m.group(1)
    return ""
