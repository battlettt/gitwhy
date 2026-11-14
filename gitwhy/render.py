"""Terminal output for story, company, heat and file modes."""

import textwrap

WIDTH = 78


def _rule(title):
    return "\n== %s %s" % (title, "=" * max(3, WIDTH - len(title) - 4))


def _wrap(text, indent="  "):
    return "\n".join(textwrap.fill(p, WIDTH, initial_indent=indent, subsequent_indent=indent) if p.strip()
                     else indent.rstrip() for p in text.split("\n"))


def header(target):
    out = ["GitWhy: why does %s exist?" % target.label]
    if target.function:
        out.append("  function %s spans %s at %s" % (target.function, target.span, target.rev))
    if target.dirty:
        out.append("  note: %s has uncommitted edits; GitWhy reads the committed version at %s"
                   % (target.path, target.rev))
    return out


def story_section(story):
    t = story.target
    o = story.origin
    c = o.commit
    out = [_rule("STORY")]
    out.append("Introduced in %s by %s <%s> on %s" % (c.short, c.author, c.email, c.iso[:16]))
    out.append("  as %s of %s [%s %s]" % (o.span, o.path, c.short, o.span))
    out.append("  message:")
    out.append(_wrap(c.message or "(empty message)", "    | "))
    current = story.current_shas
    if current and (current[0] != c.sha or len(current) > 1):
        parts = []
        for sha in current:
            rows = [b.final_line for b in story.blame if b.sha == sha]
            parts.append("%s (%s)" % (sha[:8], _ranges(rows)))
        out.append("  current text written by: " + ", ".join(parts))
    out.append(_rule("CODE AT %s" % t.rev))
    lo = max(1, t.start - story.context)
    hi = min(len(t.lines), t.end + story.context)
    for n in range(lo, hi + 1):
        mark = ">" if t.start <= n <= t.end else " "
        out.append("%s %5d | %s" % (mark, n, t.lines[n - 1]))
    out.append(_rule("TIMELINE (%d commit%s)" % (len(story.timeline), "" if len(story.timeline) == 1 else "s")))
    for i, e in enumerate(story.timeline):
        c = e.commit
        what = "introduced" if i == 0 else "+%d -%d" % (e.added, e.removed)
        out.append("  %s  %s  %-18s %-11s %s" % (c.date, c.short, c.author[:18], what, c.subject[:40]))
        out.append("  %s  [%s %s of %s]" % (" " * 10, c.short, e.span, e.path))
    if story.renames:
        out.append(_rule("RENAMES"))
        for r in story.renames:
            out.append(_wrap(("WARNING: " if r.kind in ("cutoff", "possible-cutoff", "shallow") else "") + r.text))
    return out


def _ranges(nums):
    nums = sorted(nums)
    parts = []
    start = prev = nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
        if n is not None:
            start = prev = n
    return "lines " + ", ".join(parts) if len(nums) > 1 else "line %d" % nums[0]


def company_section(story, top=10):
    total = len(story.timeline)
    out = [_rule("COMPANY: files that changed in the same %d commit%s" % (total, "" if total == 1 else "s"))]
    if not story.company:
        out.append("  none: every commit that touched %s changed only this file" % story.target.span)
        return out
    for comp in story.company[:top]:
        bar = "#" * max(1, round(comp.share(total) * 20))
        out.append("  %3d/%-3d %-20s %s" % (comp.count, total, bar, comp.path))
        out.append("          [%s]" % ", ".join(s[:8] for s in comp.shas[:6]) + (" ..." if len(comp.shas) > 6 else ""))
    if len(story.company) > top:
        out.append("  ... and %d more file%s" % (len(story.company) - top, "" if len(story.company) - top == 1 else "s"))
    return out


def heat_section(story):
    h = story.heat
    out = [_rule("HEAT: %s" % h.verdict)]
    out.append(_wrap("Verdict %s because %s." % (h.verdict, h.reason)))
    out.append("  Evidence [%s]:" % story.target.span)
    for e in h.evidence:
        out.append(_wrap("- " + e, "    "))
    for p, ln, text in story.references[:5]:
        out.append("      ref %s:%d  %s" % (p, ln, text[:50]))
    return out


def story_text(story, sections=("story", "company", "heat"), top=10):
    out = header(story.target)
    if "story" in sections:
        out += story_section(story)
    if "company" in sections:
        out += company_section(story, top)
    if "heat" in sections:
        out += heat_section(story)
    return "\n".join(out) + "\n"


def filemap_text(fm):
    t = fm.target
    out = ["GitWhy: heat map of %s at %s (%d lines, %d regions)" % (t.path, t.rev, len(t.lines), len(fm.regions))]
    out.append("  CHURN %d%%   NEW %d%%   SETTLED %d%%   OLD %d%%   (share of lines)" % tuple(
        round(fm.share(tag) * 100) for tag in ("CHURN", "NEW", "SETTLED", "OLD")))
    out.append("")
    out.append("  %-13s %-8s %8s %8s %-9s %s" % ("lines", "tag", "commits", "age", "last", "subject"))
    for r in fm.regions:
        n = r.newest
        out.append("  %-13s %-8s %8d %7dd %-9s %s" % (
            "%d-%d" % (r.start, r.end), r.tag, max(len(r.commits), len(r.shas)), r.days_since_last,
            n.sha[:8], n.summary[:34]))
    out.append("")
    out.append("  tags: CHURN = touched by 4+ commits, NEW = every line under 90 days old,")
    out.append("        OLD = untouched for a year or more, SETTLED = everything else.")
    out.append("  Ask why a region exists: python3 -m gitwhy story %s --line <n>" % t.path)
    return "\n".join(out) + "\n"
