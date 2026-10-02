"""Guards in the release script.

A release script that fails open is worse than none: it publishes a broken
tag and the mistake is public before anyone notices. So the refusal paths
get tested here, against a fake git rather than the real repository - these
must never tag, push, or reach the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import release as R  # noqa: E402


@pytest.fixture
def fake_git(monkeypatch):
    """Stand in for git: records calls and replays scripted answers.

    ``responses`` maps a subcommand to either a string or a callable taking
    the full argv and returning a string - needed for ``rev-parse``, which
    is called three times with different arguments in one check.
    """
    calls: list[tuple[str, ...]] = []
    responses: dict[str, str] = {}
    failures: set[str] = set()

    def _git(*args: str) -> str:
        calls.append(args)
        key = args[0]
        if key in failures:
            raise R.ReleaseError(f"git {key} failed")
        answer = responses.get(key, "")
        return answer(*args) if callable(answer) else answer

    monkeypatch.setattr(R, "git", _git)
    return type("FakeGit", (), {
        "calls": calls, "responses": responses, "failures": failures,
        "called": lambda _self, *a: tuple(a) in calls,
    })()


# ------------------------------------------------------------------ version
@pytest.mark.parametrize("raw,expected", [
    ("1.4.0", "1.4.0"),
    ("v1.4.0", "1.4.0"),
    ("  1.4.0  ", "1.4.0"),
])
def test_accepts_plain_and_v_prefixed_versions(raw, expected):
    assert R.check_version(raw) == expected


@pytest.mark.parametrize("bad", ["1.4", "1", "v1.4", "1.4.0-rc1", "next",
                                 "1.4.0.0", "", "1.4.0 2", "release-1"])
def test_rejects_anything_that_is_not_three_numbers(bad):
    with pytest.raises(R.ReleaseError) as exc:
        R.check_version(bad)
    assert "major.minor.patch" in str(exc.value)


# -------------------------------------------------------------- dirty tree
def test_refuses_on_a_modified_tracked_file(monkeypatch):
    """The headline guarantee: no release from uncommitted work."""
    monkeypatch.setattr(R, "dirty_paths", lambda: [" M README.md"])
    with pytest.raises(R.ReleaseError) as exc:
        R.check_clean_tree()
    msg = str(exc.value)
    assert "not clean" in msg
    assert "README.md" in msg, "the message must name the offending file"


def test_refuses_on_an_untracked_file(monkeypatch):
    monkeypatch.setattr(R, "dirty_paths", lambda: ["?? tools/release.py"])
    with pytest.raises(R.ReleaseError):
        R.check_clean_tree()


def test_accepts_a_clean_tree(monkeypatch, capsys):
    monkeypatch.setattr(R, "dirty_paths", list)
    R.check_clean_tree()
    assert "clean" in capsys.readouterr().out


def test_git_does_not_strip_porcelain_whitespace(monkeypatch):
    """git() must trim only the ends, never the per-line status column.

    ``git status --porcelain`` is fixed-width "XY path". Trimming the whole
    output turns " M README.md" into "M README.md", and the filename then
    parses as "EADME.md" - so the refusal message would name a file that
    does not exist.

    Patched at the subprocess boundary rather than by stubbing git(), so
    the trimming inside git() is actually exercised.
    """
    porcelain = " M README.md\n?? tools/release.py\n"

    class _Proc:
        returncode = 0
        stdout = porcelain
        stderr = ""

    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: _Proc())

    out = R.git("status", "--porcelain")
    assert out.splitlines()[0] == " M README.md", (
        f"git() ate a character: {out.splitlines()[0]!r}")

    paths = R.dirty_paths()
    assert paths[0].endswith("README.md"), (
        f"the filename was corrupted to {paths[0]!r}")


def test_rename_reports_the_new_path(monkeypatch):
    monkeypatch.setattr(R, "git",
                        lambda *a: "R  old.py -> new.py\n")
    assert R.dirty_paths() == ["R new.py"]


# --------------------------------------------------------------------- tag
def test_refuses_when_the_tag_exists_locally(fake_git):
    fake_git.responses["tag"] = "v1.4.0"
    with pytest.raises(R.ReleaseError) as exc:
        R.check_tag_free("v1.4.0")
    assert "already exists" in str(exc.value)
    assert "immutable" in str(exc.value)


def test_refuses_when_the_tag_exists_on_the_remote(fake_git):
    fake_git.responses["ls-remote"] = "abc123\trefs/tags/v1.4.0"
    with pytest.raises(R.ReleaseError) as exc:
        R.check_tag_free("v1.4.0")
    assert "remote" in str(exc.value)


def test_tag_free_when_nothing_exists(fake_git):
    R.check_tag_free("v1.4.0")      # must not raise


# ------------------------------------------------------------------ pushed
def _rev_answers(*values):
    """A rev-parse stub replaying one answer per call, in order."""
    seq = list(values)

    def _rev(*_args):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return _rev


def test_refuses_unpushed_commits(fake_git):
    # HEAD, then the branch, then origin/<branch> - which is behind.
    fake_git.responses["rev-parse"] = _rev_answers("aaaa", "main", "bbbb")
    with pytest.raises(R.ReleaseError) as exc:
        R.check_pushed()
    assert "unpushed" in str(exc.value)
    assert "git push" in str(exc.value)


def test_refuses_on_detached_head(fake_git):
    fake_git.responses["rev-parse"] = _rev_answers("aaaa", "HEAD")
    with pytest.raises(R.ReleaseError) as exc:
        R.check_pushed()
    assert "detached" in str(exc.value)


def test_refuses_when_the_branch_was_never_pushed(fake_git):
    # Only the origin/<branch> lookup fails - HEAD and the branch name are
    # fine. An unknown remote branch means the commit is not pushed yet.
    def _rev(*args):
        if args[1].startswith("origin/"):
            raise R.ReleaseError("unknown revision")
        return "aaaa" if args[1] == "HEAD" else "main"

    fake_git.responses["rev-parse"] = _rev
    with pytest.raises(R.ReleaseError) as exc:
        R.check_pushed()
    assert "Push your branch" in str(exc.value)


def test_accepts_a_pushed_head(fake_git):
    fake_git.responses["rev-parse"] = _rev_answers("aaaa", "main", "aaaa")
    assert R.check_pushed() == "aaaa"


# ------------------------------------------------------------------ inputs
def test_flags_a_missing_face_model(monkeypatch):
    """The model is gitignored; without it the exe builds and then fails."""
    missing = R.REPO / "no-such-model.task"
    monkeypatch.setattr(R, "MODEL", missing)
    rep = R.Report()
    R.check_inputs(rep)
    assert "face model present" in rep.failed


def test_flags_a_truncated_face_model(monkeypatch, tmp_path):
    small = tmp_path / "m.task"
    small.write_bytes(b"x" * 10)
    monkeypatch.setattr(R, "MODEL", small)
    rep = R.Report()
    R.check_inputs(rep)
    assert "face model present" in rep.failed


def test_flags_missing_bridge_dlls(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "BRIDGE_DLLS", ("NPClient.dll", "NPClient64.dll"))
    monkeypatch.setattr(R, "REPO", tmp_path)
    rep = R.Report()
    R.check_inputs(rep)
    assert "bridge DLLs present" in rep.failed


def test_accepts_complete_inputs(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    model = tmp_path / "models" / "face_landmarker.task"
    model.write_bytes(b"x" * R.MIN_MODEL_BYTES)
    (tmp_path / "bridge").mkdir()
    for d in R.BRIDGE_DLLS:
        (tmp_path / "bridge" / d).write_bytes(b"x")
    monkeypatch.setattr(R, "REPO", tmp_path)
    monkeypatch.setattr(R, "MODEL", model)
    rep = R.Report()
    R.check_inputs(rep)
    assert rep.ok(), rep.failed


# --------------------------------------------------------------- repo url
@pytest.mark.parametrize("remote,expected", [
    ("https://github.com/Elias2012-dev/Eyetracker.git",
     "https://github.com/Elias2012-dev/Eyetracker"),
    ("git@github.com:someone/Eyetracker.git",
     "https://github.com/someone/Eyetracker"),
])
def test_repo_url_is_derived_from_the_remote(monkeypatch, remote, expected):
    """Hard-coding the URL would leave stale links after a repo rename."""
    monkeypatch.setattr(R, "git", lambda *a: remote)
    assert R.repo_url() == expected


# -------------------------------------------------------------------- main
def test_main_refuses_a_bad_version_without_touching_git(monkeypatch):
    """A malformed version must not get as far as running git."""
    calls = []
    monkeypatch.setattr(R, "git", lambda *a: calls.append(a))
    assert R.main(["nonsense", "--dry-run"]) == 1
    assert calls == [], "git ran despite an invalid version"


def test_main_refuses_a_dirty_tree(monkeypatch):
    monkeypatch.setattr(R, "dirty_paths", lambda: [" M README.md"])
    assert R.main(["1.4.0", "--dry-run"]) == 1


def test_main_dry_run_never_tags_or_pushes(monkeypatch, fake_git):
    """--dry-run is the safe way to inspect a release; prove it changes nothing."""
    monkeypatch.setattr(R, "dirty_paths", list)
    monkeypatch.setattr(R, "check_pushed", lambda: "abc1234")
    monkeypatch.setattr(R, "check_tag_free", lambda tag: None)
    monkeypatch.setattr(R, "check_ci_green", lambda sha: True)
    monkeypatch.setattr(R, "check_inputs", lambda rep: None)
    monkeypatch.setattr(R, "check_tests", lambda: True)
    assert R.main(["1.4.0", "--dry-run"]) == 0
    assert not fake_git.called("tag", "-a"), "dry run created a tag"
    for c in fake_git.calls:
        assert c[0] != "push", "dry run pushed"


def test_main_exits_before_tagging_when_tests_fail(monkeypatch):
    monkeypatch.setattr(R, "dirty_paths", list)
    monkeypatch.setattr(R, "check_pushed", lambda: "abc1234")
    monkeypatch.setattr(R, "check_tag_free", lambda tag: None)
    monkeypatch.setattr(R, "check_ci_green", lambda sha: True)
    monkeypatch.setattr(R, "check_inputs", lambda rep: None)
    monkeypatch.setattr(R, "check_tests", lambda: False)
    assert R.main(["1.4.0"]) == 1


def test_failed_local_build_blocks_the_tag(monkeypatch):
    monkeypatch.setattr(R, "dirty_paths", list)
    monkeypatch.setattr(R, "check_pushed", lambda: "abc1234")
    monkeypatch.setattr(R, "check_tag_free", lambda tag: None)
    monkeypatch.setattr(R, "check_ci_green", lambda sha: True)
    monkeypatch.setattr(R, "check_inputs", lambda rep: None)
    monkeypatch.setattr(R, "check_tests", lambda: True)
    monkeypatch.setattr(R, "build_and_verify", lambda: False)
    monkeypatch.setattr(R, "git", lambda *a: pytest.fail("tagged after a bad build"))
    assert R.main(["1.4.0", "--build"]) == 1


def test_failed_push_deletes_the_local_tag(monkeypatch):
    """No half-finished local tag left behind to confuse the next run."""
    monkeypatch.setattr(R, "dirty_paths", list)
    monkeypatch.setattr(R, "check_pushed", lambda: "abc1234")
    monkeypatch.setattr(R, "check_tag_free", lambda tag: None)
    monkeypatch.setattr(R, "check_ci_green", lambda sha: True)
    monkeypatch.setattr(R, "check_inputs", lambda rep: None)
    monkeypatch.setattr(R, "check_tests", lambda: True)

    calls = []

    def _git(*args):
        calls.append(args)
        if args[:2] == ("push", "origin"):
            raise R.ReleaseError("push failed")

    monkeypatch.setattr(R, "git", _git)
    with pytest.raises(R.ReleaseError):
        R.main(["1.4.0"])
    assert ("tag", "-d", "v1.4.0") in calls, "the local tag was left behind"


# ------------------------------------------------- does it match the workflow
def test_the_workflow_still_owns_publishing():
    """This script must not become a second publisher.

    Publishing lives in .github/workflows/release.yml; if that ever changes,
    the two implementations have to be reconciled deliberately rather than
    by accident.
    """
    wf = (R.REPO / ".github" / "workflows" / "release.yml").read_text("utf-8")
    assert "gh release upload" in wf, "the workflow should still upload assets"
    # release.py is a preflight: it may tag and push, but it uploads nothing.
    src = (R.REPO / "tools" / "release.py").read_text("utf-8")
    assert "gh release upload" not in src
    assert "urllib.request" not in src, "release.py must not call the API"