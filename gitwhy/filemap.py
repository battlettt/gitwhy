"""Whole-file mode: split a file into regions and tag each one by age and churn."""

import time
from dataclasses import dataclass, field

from .git import GitWhyError, run_git
from .heat import DAY, region_tag
from .parse import LOG_FORMAT, parse_blame_porcelain, parse_log

MAX_REGIONS = 40


@dataclass
class Region:
    start: int
    end: int
    blame: list = field(default_factory=list)      # BlameLine list
    commits: list = field(default_factory=list)    # LogCommit list from git log -L
    tag: str = ""
    days_since_last: int = 0
    days_since_first: int = 0

    @property
    def size(self):
        return self.end - self.start + 1

    @property
    def span(self):
        return "lines %d-%d" % (self.start, self.end) if self.start != self.end else "line %d" % self.start

    @property
    def newest(self):
        return max(self.blame, key=lambda b: b.author_time)

    @property
    def oldest(self):
        return min(self.blame, key=lambda b: b.author_time)

    @property
    def shas(self):
        return list(dict.fromkeys(b.sha for b in self.blame))


@dataclass
class FileMap:
    target: object
    regions: list
    now: float

    def share(self, tag):
        total = sum(r.size for r in self.regions)
        return sum(r.size for r in self.regions if r.tag == tag) / total if total else 0.0


def blame_runs(blame):
    """Group consecutive blame lines written by the same commit."""
    runs = []
    for b in blame:
        if runs and runs[-1].blame[-1].sha == b.sha and runs[-1].end + 1 == b.final_line:
            runs[-1].end = b.final_line
            runs[-1].blame.append(b)
        else:
            runs.append(Region(start=b.final_line, end=b.final_line, blame=[b]))
    return runs


def coalesce(regions, limit=MAX_REGIONS):
    """Merge the smallest neighbouring regions until at most limit remain."""
    regions = list(regions)
    while len(regions) > limit:
        best = min(range(len(regions) - 1), key=lambda i: regions[i].size + regions[i + 1].size)
        a, b = regions[best], regions[best + 1]
        regions[best:best + 2] = [Region(start=a.start, end=b.end, blame=a.blame + b.blame)]
    return regions


def build_filemap(target, now=None, limit=MAX_REGIONS):
    now = time.time() if now is None else now
    out = run_git(target.repo, "blame", "--porcelain", target.rev, "--", target.path)
    blame = parse_blame_porcelain(out)
    if not blame:
        raise GitWhyError("%s has no lines to map" % target.path)
    regions = coalesce(blame_runs(blame), limit)
    for r in regions:
        log = run_git(target.repo, "log", "--no-ext-diff", "--no-patch", "--format=" + LOG_FORMAT,
                      "-L", "%d,%d:%s" % (r.start, r.end, target.path), target.rev, check=False)
        r.commits = parse_log(log) if log.strip() else []
        r.days_since_last = max(0, int((now - r.newest.author_time) // DAY))
        r.days_since_first = max(0, int((now - r.oldest.author_time) // DAY))
        count = max(len(r.commits), len(r.shas))
        r.tag = region_tag(count, r.days_since_last, r.days_since_first)
    return FileMap(target=target, regions=regions, now=now)
