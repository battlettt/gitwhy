"""Command line entry point: python3 -m gitwhy <story|company|heat|file> ..."""

import argparse
import datetime
import os
import sys

from . import __version__
from .filemap import build_filemap
from .git import GitWhyError
from .render import filemap_text, story_text
from .report import filemap_html, story_html
from .story import build_story
from .target import resolve

DEFAULT_HTML = "gitwhy-report.html"


def _positive(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a whole number" % value)
    if n < 1:
        raise argparse.ArgumentTypeError("%d is not a valid line number; lines start at 1" % n)
    return n


def _nonneg(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a whole number" % value)
    if n < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return n


def _date(value):
    try:
        d = datetime.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a date like 2026-01-31" % value)
    return d.timestamp()


def build_parser():
    p = argparse.ArgumentParser(
        prog="gitwhy",
        description="Answer 'why does this code exist' from local git history.",
    )
    p.add_argument("--version", action="version", version="gitwhy " + __version__)
    sub = p.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    def common(sp, html=True):
        sp.add_argument("file", help="file inside a git repository")
        sp.add_argument("--rev", default="HEAD", help="revision to read (default HEAD)")
        sp.add_argument("--now", type=_date, default=None, metavar="YYYY-MM-DD",
                        help="judge ages as of this date instead of today")
        if html:
            sp.add_argument("--html", default=DEFAULT_HTML, metavar="PATH",
                            help="where to write the HTML report (default %s)" % DEFAULT_HTML)
            sp.add_argument("--no-html", action="store_true", help="skip the HTML report")

    def target_args(sp):
        g = sp.add_mutually_exclusive_group(required=True)
        g.add_argument("--line", type=_positive, help="line number (1-based)")
        g.add_argument("--function", help="function or class name; the whole definition is the target")
        sp.add_argument("--end", type=_positive, help="last line of a range starting at --line")
        sp.add_argument("--top", type=_positive, default=10, help="company files to show (default 10)")

    s = sub.add_parser("story", help="who wrote it, why, what changed around it, and whether it is alive")
    common(s)
    target_args(s)
    s.add_argument("--context", type=_nonneg, default=3, help="lines of code shown around the target")

    c = sub.add_parser("company", help="files that changed in the same commits as the target")
    common(c, html=False)
    target_args(c)

    h = sub.add_parser("heat", help="HOT, STABLE or STALE, with the evidence that decided it")
    common(h, html=False)
    target_args(h)

    f = sub.add_parser("file", help="per-region heat map of a whole file")
    common(f)
    return p


def main(argv=None, stdout=None, stderr=None):
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "file":
            from .target import load_file, Target
            repo, rel, lines, dirty = load_file(args.file, args.rev)
            if not lines:
                raise GitWhyError("%s is empty at %s" % (rel, args.rev))
            target = Target(repo=repo, path=rel, rev=args.rev, start=1, end=len(lines), lines=lines, dirty=dirty)
            fm = build_filemap(target, now=args.now)
            stdout.write(filemap_text(fm))
            if not args.no_html:
                _write(args.html, filemap_html(fm), stdout)
            return 0

        if args.end is not None and args.function:
            raise GitWhyError("--end only works with --line")
        target = resolve(args.file, line=args.line, end=args.end, function=args.function, rev=args.rev)
        story = build_story(target, now=args.now, top=args.top)
        story.context = getattr(args, "context", 3)
        sections = {"story": ("story", "company", "heat"), "company": ("company",), "heat": ("heat",)}[args.command]
        stdout.write(story_text(story, sections, top=args.top))
        if args.command == "story" and not args.no_html:
            _write(args.html, story_html(story, top=args.top), stdout)
        return 0
    except GitWhyError as exc:
        stderr.write("gitwhy: error: %s\n" % exc)
        return 1


def _write(path, content, stdout):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    stdout.write("\nHTML report: %s\n" % os.path.abspath(path))
