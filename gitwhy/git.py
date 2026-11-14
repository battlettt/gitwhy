"""Thin wrapper around the local git binary.

Every git call GitWhy makes goes through run_git, so it is the one place that
pins the config switches which would otherwise change git's output format
(colour, pagers, diff prefixes, signature blocks).
"""

import os
import subprocess


class GitWhyError(Exception):
    """A problem the user can act on: bad path, bad line, missing function."""


# Config overrides that keep git's output machine-readable no matter what the
# user's global config says.
_PINNED_CONFIG = (
    "color.ui=false",
    "core.quotepath=false",
    "diff.noprefix=false",
    "diff.mnemonicPrefix=false",
    "diff.external=",
    "log.showSignature=false",
    "log.decorate=false",
)


def run_git(repo, *args, check=True, ok_codes=(0,)):
    """Run git inside repo and return stdout as text.

    Raises GitWhyError with git's own message when the command fails and
    check is true.
    """
    cmd = ["git", "--no-pager", "-C", repo]
    for item in _PINNED_CONFIG:
        cmd += ["-c", item]
    cmd += list(args)
    env = dict(os.environ)
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat", "LC_ALL": "C", "LANG": "C"})
    try:
        proc = subprocess.run(cmd, capture_output=True, env=env)
    except FileNotFoundError:
        raise GitWhyError("git is not installed or not on PATH") from None
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode not in ok_codes:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = err[-1] if err else "exit code %d" % proc.returncode
        raise GitWhyError("git %s failed: %s" % (args[0] if args else "", detail))
    return out


def find_repo_root(path):
    """Return the top-level directory of the repo that contains path."""
    probe = path if os.path.isdir(path) else os.path.dirname(path) or "."
    if not os.path.isdir(probe):
        raise GitWhyError("%s does not exist" % path)
    out = run_git(probe, "rev-parse", "--show-toplevel", check=False).strip()
    if not out or not os.path.isdir(out):
        raise GitWhyError("%s is not inside a git repository" % path)
    return out


def is_shallow(repo):
    return run_git(repo, "rev-parse", "--is-shallow-repository", check=False).strip() == "true"
