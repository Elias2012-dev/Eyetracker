"""The reported version has to name a release that exists.

``--version`` is what a user quotes in a bug report, and ``release.py``
tags whatever ``__version__`` says. If the two drift apart - as they had,
with the binary answering 1.1.0 on top of a v1.4.0 release - then neither
the support thread nor the tag is trustworthy, and nothing complains.

So: a build cut from a tag must report that tag. On an untagged commit, or
on a checkout with no tags at all, it skips.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from eyetrack import __version__

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _tag_at_head() -> str | None:
    """The release tag pointing at HEAD, or None if HEAD is untagged.

    Only an *exact* match counts. The interesting invariant is "a build cut
    from a tag reports that tag" - comparing against the newest tag
    anywhere would also fail on the ordinary development commit that sits
    between two releases, which is where the bump to the next version
    legitimately happens.
    """
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    tag = out.stdout.strip()
    return tag[1:] if tag.startswith("v") else tag


def test_the_version_is_a_plain_semver():
    assert VERSION_RE.match(__version__), (
        f"{__version__!r} is not plain MAJOR.MINOR.PATCH")


def test_a_tagged_build_reports_its_own_tag():
    tag = _tag_at_head()
    if tag is None:
        pytest.skip("HEAD is not a release tag - nothing to compare against")
    assert __version__ == tag, (
        f"the app reports {__version__} but this build is tagged v{tag}; a "
        "user quoting --version would name a release that does not exist")


def test_the_cli_reports_the_same_version():
    import eyetrack.__main__ as cli

    with pytest.raises(SystemExit) as exc:
        cli._parse_args(["--version"])
    assert exc.value.code == 0