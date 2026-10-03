"""Cut a release in one command, without shipping something broken.

    python tools/release.py 1.4.0              # check, tag, push
    python tools/release.py 1.4.0 --build      # ...and build + smoke-test here
    python tools/release.py 1.4.0 --dry-run    # checks only, changes nothing

There is a GitHub Actions workflow (.github/workflows/release.yml) that
builds the exe and the mod jar and attaches them to the GitHub Release.
This script does **not** duplicate it: publishing happens in one place, so
the two can't drift. What this does is the part that happens *before* the
tag exists, where a mistake is cheap to fix:

* **Refuses to run on a dirty tree.** A release built from uncommitted
  changes is not reproducible from the tag, which defeats the point of
  tagging. This is the check the workflow cannot make - by the time it
  runs, the tag is already public.
* **Fails early on the things that have actually broken a release here**:
  a tag that already exists, a version that does not parse, a commit that
  is not on the remote, a missing face model (the exe would build fine and
  then fail on a user's first launch), missing bridge DLLs, and CI not
  having passed.
* Optionally reproduces the build locally with --build, so a broken
  packaging change is caught on your machine in minutes rather than in the
  Actions log after the tag is pushed.

Publishing is one step: push the tag, and the workflow takes it from there.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The face-landmark model is gitignored and ~3.8 MB. Without it the exe
# still builds - it just cannot detect a face until first run. The spec now
# aborts, but catching it here names the fix instead of raising SystemExit.
MODEL = REPO / "models" / "face_landmarker.task"
BRIDGE_DLLS = ("NPClient.dll", "NPClient64.dll")
MIN_MODEL_BYTES = 1_000_000

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SEMVER_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")


class ReleaseError(SystemExit):
    """A reason not to cut this release. Prints without a traceback."""


@dataclass
class Report:
    """What the preflight checked, so a failure explains itself."""

    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        mark = "ok  " if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" - {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[str]:
        return [n for n, ok, _ in self.checks if not ok]

    def ok(self) -> bool:
        return not self.failed


def git(*args: str) -> str:
    """Run a git command and return stdout, with surrounding blank lines removed.

    Only the ends are trimmed: `git status --porcelain` is fixed-width
    ("XY path"), so stripping every line would eat the leading space of a
    " M file" entry and shift the path by one character.
    """
    proc = subprocess.run(("git", *args), cwd=REPO, capture_output=True,
                          text=True, errors="replace")
    if proc.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout.strip("\r\n")


def repo_url() -> str:
    """The repository's web URL, read from the origin remote.

    Deriving it means renaming or moving the repo does not leave stale
    links hard-coded in this script.
    """
    try:
        url = git("remote", "get-url", "origin")
    except ReleaseError:
        return "https://github.com/Elias2012-dev/Eyetracker"
    url = re.sub(r"\.git$", "", url)
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    url = re.sub(r"^https?://[^/]+/", "https://github.com/", url)
    return url


def dirty_paths() -> list[str]:
    """Tracked modifications and untracked files, minus the ones we ignore."""
    out = git("status", "--porcelain")
    paths = []
    for line in out.splitlines():
        if not line.strip():
            continue
        # Porcelain renames put "old -> new"; the new path is what matters.
        # Keep the raw status codes rather than stripping them, or a path
        # like "README.md" loses its first character to the slicing.
        status = line[:2].rstrip() or "??"
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(f"{status} {path}")
    return paths


def check_clean_tree() -> None:
    paths = dirty_paths()
    if paths:
        listing = "\n".join(f"    {p}" for p in paths[:20])
        more = f"\n    ... and {len(paths) - 20} more" if len(paths) > 20 else ""
        raise ReleaseError(
            "the working tree is not clean; commit or stash first.\n"
            "A release must be reproducible from its tag, and uncommitted\n"
            "changes are not in the tag.\n\n"
            f"  {listing}{more}"
        )
    print("  [ok  ] working tree is clean")


def check_version(version: str) -> str:
    """Accept 1.4.0 or v1.4.0; reject anything else."""
    m = SEMVER_RE.match(version.strip())
    if not m:
        raise ReleaseError(
            f"{version!r} is not a version like 1.4.0 (major.minor.patch).")
    return m.group(1)


def check_app_version(version: str) -> None:
    """The app must report the version we are about to publish.

    ``__version__`` and the tag are two different pieces of information, and
    nothing connected them: v1.5.0 shipped a binary whose ``--version``
    answered 1.4.1. Tests only notice on a tagged commit, which is *after*
    the release exists, so the check has to happen before the tag is cut.
    """
    sys.path.insert(0, str(REPO))
    try:
        from eyetrack import __version__ as app_version
    except ImportError as exc:
        raise ReleaseError(f"could not import eyetrack: {exc}")
    if app_version != version:
        raise ReleaseError(
            f"eyetrack.__version__ is {app_version!r} but you are cutting "
            f"v{version}.\nThe published exe would report a version that "
            "does not exist.\nUpdate eyetrack/__init__.py and commit "
            "first.")


def check_tag_free(tag: str) -> None:
    existing = git("tag", "--list", tag)
    if existing:
        raise ReleaseError(
            f"tag {tag} already exists locally. Releases are immutable -\n"
            "cut a new version instead of moving a published one.")
    remote = git("ls-remote", "--tags", "origin", tag)
    if remote:
        raise ReleaseError(f"tag {tag} already exists on the remote.")


def check_pushed() -> str:
    """HEAD must be reachable from the remote, or the tag will be orphaned."""
    sha = git("rev-parse", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise ReleaseError("detached HEAD - check out a branch before releasing.")
    try:
        remote_sha = git("rev-parse", f"origin/{branch}")
    except ReleaseError:
        raise ReleaseError(
            f"origin/{branch} is unknown. Push your branch first:\n"
            f"  git push origin {branch}")
    if remote_sha != sha:
        raise ReleaseError(
            f"{branch} has unpushed commits. Push before tagging:\n"
            f"  git push origin {branch}")
    print(f"  [ok  ] HEAD is pushed to origin/{branch} ({sha[:12]})")
    return sha


def check_ci_green(sha: str) -> bool:
    """Best-effort: warn rather than fail, since CI is not authoritative here."""
    try:
        proc = subprocess.run(
            ("gh", "run", "list", "--commit", sha, "--limit", "1",
             "--json", "conclusion", "--jq", ".[].conclusion"),
            cwd=REPO, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        print("  [warn] could not check CI (gh unavailable); continuing")
        return False
    conclusion = proc.stdout.strip()
    if proc.returncode != 0:
        print("  [warn] could not check CI; continuing")
        return False
    ok = conclusion == "success"
    print(f"  [{'ok  ' if ok else 'warn'}] CI conclusion: {conclusion or 'none yet'}")
    if not ok and conclusion in ("failure", "cancelled"):
        print("         push the fixes first - do not tag a red build")
    return ok


def check_inputs(rep: Report) -> None:
    """The files the exe must ship: the model and both bridge DLLs."""
    if not MODEL.exists():
        rep.add("face model present", False,
                f"{MODEL} is missing (gitignored). Fetch it by running the "
                "tracker once, or see README.")
    else:
        size = MODEL.stat().st_size
        rep.add("face model present", size >= MIN_MODEL_BYTES,
                f"{size:,} bytes" if size >= MIN_MODEL_BYTES
                else f"{size:,} bytes - looks truncated")

    missing = [d for d in BRIDGE_DLLS if not (REPO / "bridge" / d).exists()]
    rep.add("bridge DLLs present", not missing,
            "missing " + ", ".join(missing) if missing
            else f"{len(BRIDGE_DLLS)} files")


def check_tests() -> bool:
    print("\nRunning the test suite...")
    proc = subprocess.run((sys.executable, "-m", "pytest", "-q"), cwd=REPO)
    ok = proc.returncode == 0
    print(f"  [{'ok  ' if proc.returncode == 0 else 'FAIL'}] pytest")
    return ok


def build_and_verify() -> bool:
    """Reproduce the exe and the jar locally, then smoke-test them.

    Optional, because it takes minutes - but it is the difference between
    finding a broken spec on your machine and finding it in the Actions
    log after the tag is public.
    """
    if sys.platform != "win32":
        print("  [warn] building needs Windows (batch files + a windowed exe)")
        return False

    print("\nBuilding the exe (this takes a few minutes)...")
    proc = subprocess.run(("cmd", "/c", str(REPO / "build_exe.bat")), cwd=REPO)
    exe = REPO / "dist" / "Eyetracker.exe"
    ok = proc.returncode == 0 and exe.exists()
    print(f"  [{'ok  ' if ok else 'FAIL'}] PyInstaller build"
          + ("" if ok else " - see the output above"))
    if not ok:
        return False

    # The model must be *inside* the bundle, not merely next to it.
    blob = exe.read_bytes()
    ok = b"face_landmarker.task" in blob
    print(f"  [{'ok  ' if ok else 'FAIL'}] exe embeds the face model")

    print("\nSmoke-testing the exe (--paths)...")
    proc = subprocess.run((str(exe), "--paths"), cwd=REPO,
                          env={**os.environ, "EYE_TRACKER_NO_DIALOG": "1"},
                          capture_output=True)
    ok = proc.returncode == 0
    print(f"  [{'ok  ' if ok else 'FAIL'}] exe --paths exit {proc.returncode}")
    if not ok:
        print(proc.stdout.decode(errors="replace")[-800:])
    return ok and b"face_landmarker.task" in blob


def build_jar() -> bool:
    """The mod jar, via the Gradle wrapper (PowerShell needs the .\\ prefix)."""
    if sys.platform != "win32":
        print("  [warn] building the mod needs Windows (gradlew.bat)")
        return False
    print("\nBuilding the mod jar...")
    subprocess.run(("cmd", "/c", r"gradlew.bat build --console=plain"),
                   cwd=REPO / "minecraft-mod")
    jars = [p for p in (REPO / "minecraft-mod" / "build" / "libs").glob("*.jar")
            if "sources" not in p.name]
    ok = bool(jars) and jars[0].stat().st_size > 0
    print(f"  [{'ok  ' if ok else 'FAIL'}] mod jar"
          + (f" - {jars[0].name} ({jars[0].stat().st_size:,} bytes)" if ok else ""))
    return ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="release", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("version", help="version to release, e.g. 1.4.0")
    p.add_argument("--build", action="store_true",
                   help="also build and smoke-test the exe and jar locally")
    p.add_argument("--jar-only", action="store_true",
                   help="only build the mod jar (fast)")
    p.add_argument("--dry-run", action="store_true",
                   help="run every check but do not create the tag")
    p.add_argument("--skip-tests", action="store_true",
                   help="do not run pytest (CI already covers it)")
    p.add_argument("--skip-ci-check", action="store_true",
                   help="do not query GitHub for the CI result")
    args = p.parse_args(argv)

    # Everything above this point only reads; a bad version should not be
    # able to change the repo.
    print("Preflight")
    try:
        version = check_version(args.version)
        check_clean_tree()
    except ReleaseError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    tag = f"v{version}"

    rep = Report()
    check_app_version(version)
    check_tag_free(tag)
    sha = check_pushed()
    check_inputs(rep)
    if not args.skip_ci_check:
        check_ci_green(sha)
    if not args.skip_tests and not rep.ok():
        print("\nFix the above first.")
        return 1
    if not rep.ok():
        print("\nFix the above first.")
        return 1
    if not args.skip_tests:
        if not check_tests():
            print("\nTests failed - not releasing.", file=sys.stderr)
            return 1

    if args.jar_only or args.build:
        print("\nLocal build")
        ok = build_jar() if args.jar_only else (build_and_verify() and build_jar())
        if not ok:
            print("\nLocal build failed - not tagging.", file=sys.stderr)
            return 1

    print(f"\n{'Everything checks out' if rep.ok() else 'Some checks failed'}.")
    print(f"\nReady to release {tag} ({sha[:12]}).")
    if args.dry_run:
        print("Dry run - nothing changed.")
        return 0
    if rep.failed:
        print(f"Refusing to continue: {', '.join(rep.failed)}", file=sys.stderr)
        return 1

    print(f"\nTagging {tag} and pushing. This triggers the release workflow:")
    git("tag", "-a", tag, "-m", f"{tag} - released from a clean tree", "HEAD")
    try:
        git("push", "origin", tag)
    except ReleaseError:
        # Leave no half-finished local tag behind to confuse the next run.
        git("tag", "-d", tag)
        raise
    print(f"\nPushed {tag}. Watch it build here:")
    print(f"  {repo_url()}/actions")
    print(f"  {repo_url()}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())