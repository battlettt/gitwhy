"""Heat verdicts: is this code HOT, STABLE or STALE, and what decided it."""

from dataclasses import dataclass, field

DAY = 86400

# Thresholds. Kept in one place so the README and the verdict text agree.
RECENT_DAYS = 30        # changed within this many days -> HOT
BUSY_WINDOW_DAYS = 365  # look-back window for "changed often"
BUSY_COMMITS = 3        # this many commits inside the window -> HOT
STALE_DAYS = 730        # untouched this long (two years) can be STALE
DEPENDED_REFS = 2       # this many references elsewhere -> depended on

# File-mode region tags.
NEW_DAYS = 90
OLD_DAYS = 365
CHURN_COMMITS = 4


@dataclass
class HeatVerdict:
    verdict: str            # HOT, STABLE or STALE
    reason: str             # the one rule that decided it
    evidence: list = field(default_factory=list)  # every fact considered, each citing a sha
    changes: int = 0        # commits that touched the range, origin included
    last_sha: str = ""
    last_date: str = ""
    days_since: int = 0
    recent: int = 0         # commits inside the busy window
    refs: object = None     # int, or None when there was no symbol to count


def judge(commits, now, refs=None, symbol=""):
    """Decide the verdict from the commits that touched the range.

    commits: parse.LogCommit list (any order). refs: list of reference hits or
    None when no symbol could be counted.
    """
    if not commits:
        raise ValueError("no commits to judge")
    ordered = sorted(commits, key=lambda c: c.time)
    last = ordered[-1]
    days = max(0, int((now - last.time) // DAY))
    recent = [c for c in ordered if now - c.time <= BUSY_WINDOW_DAYS * DAY]
    nrefs = None if refs is None else len(refs)

    ev = [
        "touched by %d commit%s: introduced in %s (%s)%s" % (
            len(ordered), "" if len(ordered) == 1 else "s", ordered[0].sha[:8], ordered[0].iso[:10],
            "" if len(ordered) == 1 else ", changed %d time%s since" % (len(ordered) - 1, "" if len(ordered) == 2 else "s")),
        "last changed %d day%s ago in %s (%s)" % (days, "" if days == 1 else "s", last.sha[:8], last.iso[:10]),
        "%d commit%s in the last %d days%s" % (
            len(recent), "" if len(recent) == 1 else "s", BUSY_WINDOW_DAYS,
            (": " + ", ".join(c.sha[:8] for c in recent)) if recent else ""),
    ]
    if nrefs is None:
        ev.append("no named symbol is defined on these lines, so references were not counted")
    else:
        ev.append("%r is referenced %d time%s elsewhere at the current revision" % (
            symbol, nrefs, "" if nrefs == 1 else "s"))

    if days <= RECENT_DAYS:
        verdict = "HOT"
        reason = "changed %d days ago in %s, within the %d-day hot window" % (days, last.sha[:8], RECENT_DAYS)
    elif len(recent) >= BUSY_COMMITS:
        verdict = "HOT"
        reason = "%d commits in the last %d days (%s), at least %d means it is still moving" % (
            len(recent), BUSY_WINDOW_DAYS, ", ".join(c.sha[:8] for c in recent), BUSY_COMMITS)
    elif days >= STALE_DAYS and (nrefs is None or nrefs < DEPENDED_REFS):
        verdict = "STALE"
        if nrefs is None:
            reason = "untouched for %d days since %s (over %d) and no symbol to show anything depends on it" % (
                days, last.sha[:8], STALE_DAYS)
        else:
            reason = "untouched for %d days since %s (over %d) and %r has only %d reference%s elsewhere" % (
                days, last.sha[:8], STALE_DAYS, symbol, nrefs, "" if nrefs == 1 else "s")
    elif days >= STALE_DAYS:
        verdict = "STABLE"
        reason = "untouched for %d days since %s, but %r is referenced %d times elsewhere, so it is depended on" % (
            days, last.sha[:8], symbol, nrefs)
    else:
        verdict = "STABLE"
        reason = "last changed %d days ago in %s with only %d commit%s in the last %d days: settled, not yet old enough to be stale" % (
            days, last.sha[:8], len(recent), "" if len(recent) == 1 else "s", BUSY_WINDOW_DAYS)
        if nrefs:
            reason += ", and referenced %d time%s elsewhere" % (nrefs, "" if nrefs == 1 else "s")
    return HeatVerdict(verdict=verdict, reason=reason, evidence=ev, changes=len(ordered),
                       last_sha=last.sha, last_date=last.iso[:10], days_since=days,
                       recent=len(recent), refs=nrefs)


def region_tag(commit_count, days_since_last, days_since_first):
    """Tag for a whole-file region: CHURN, NEW, OLD or SETTLED."""
    if commit_count >= CHURN_COMMITS:
        return "CHURN"
    if days_since_first <= NEW_DAYS:
        return "NEW"
    if days_since_last >= OLD_DAYS:
        return "OLD"
    return "SETTLED"
