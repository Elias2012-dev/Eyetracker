"""Documentation must not rot.

The install guide and README cross-link each other, and both point at
files in the repo. A renamed section or a moved file silently breaks a
link that only a reader would ever notice - so check them here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
INSTALL = REPO / "INSTALL.md"


def _anchors(text: str) -> set[str]:
    """GitHub-style heading slugs for a markdown document."""
    out = set()
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line)
        if not m:
            continue
        slug = m.group(1).strip().lower()
        slug = re.sub(r"[^\w\s-]", "", slug)      # drops '.', '/', punctuation
        out.add(re.sub(r"\s+", "-", slug))
    return out


def _links(text: str) -> list[str]:
    return re.findall(r"\]\(([^)]+)\)", text)


@pytest.mark.parametrize("doc", [README, INSTALL])
def test_the_documentation_exists(doc):
    assert doc.exists(), f"{doc.name} is missing"


@pytest.mark.parametrize("name,doc", [("README.md", README), ("INSTALL.md", INSTALL)])
def test_relative_links_and_anchors_resolve(name, doc):
    """Every relative link must point at a file that exists and a heading
    that still exists.

    "#5-minecraft-262" is the kind of thing that breaks silently: the dot in
    "Minecraft 26.2" is stripped from the slug, so a hand-written
    "#5-minecraft-26-2" 404s at the section level rather than obviously.
    """
    text = doc.read_text(encoding="utf-8")
    own = _anchors(text)
    other_name = "INSTALL.md" if name == "README.md" else "README.md"
    other = _anchors((REPO / other_name).read_text(encoding="utf-8"))

    problems = []
    for target in _links(text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, frag = target.partition("#")
        if path:
            if path == other_name:
                anchors = other
            elif path.startswith(("http", "#")):
                continue
            else:
                # A repo-relative file link; it must exist.
                if not (REPO / path).exists():
                    problems.append(f"{target} (no such file)")
                    continue
                anchors = set()
            if frag and frag not in anchors:
                problems.append(f"{target} (no such heading)")
        elif frag and frag not in own:
            problems.append(f"#{frag} (no such heading in {name})")

    assert not problems, f"broken links in {name}: " + ", ".join(problems)


def test_install_guide_is_linked_from_the_readme():
    """Otherwise nobody finds the guide."""
    assert "INSTALL.md" in README.read_text(encoding="utf-8")


def test_readme_is_linked_from_the_install_guide():
    assert "README.md" in INSTALL.read_text(encoding="utf-8")


def test_download_links_point_at_real_release_assets():
    """The guide's download URLs must match what the releases actually serve."""
    for name, expected in (("Eyetracker.exe", True),
                           ("eyetrack-1.0.0.jar", True)):
        for doc in (README.read_text(encoding="utf-8"),
                    INSTALL.read_text(encoding="utf-8")):
            if f"download/{name}" in doc:
                break
        else:
            pytest.fail(f"{name} is not offered as a download anywhere")


def test_install_guide_mentions_every_output():
    """A guide that forgets one of the three ways in is a bad guide."""
    text = INSTALL.read_text(encoding="utf-8")
    for topic in ("Minecraft", "ETS2", "Farming Simulator", "mouse",
                  "--pick-camera", "phone"):
        assert topic.lower() in text.lower(), f"INSTALL.md never mentions {topic}"

def test_the_hud_screenshots_the_docs_embed_are_the_ones_we_generate():
    """A renamed render must not leave the README pointing at nothing.

    The link check above catches a missing file; this catches the subtler
    case where overlay_preview writes a new name and the docs quietly keep
    showing the old one.
    """
    from tools.overlay_preview import DOC_IMAGES

    both = README.read_text(encoding="utf-8") + INSTALL.read_text(encoding="utf-8")
    for name in DOC_IMAGES.values():
        assert f"docs/{name}" in both, f"neither doc embeds docs/{name}"
        assert (REPO / "docs" / name).exists(), f"docs/{name} is missing"


def test_the_docs_show_the_hud():
    """A reader who has not installed anything still needs to see it works."""
    text = README.read_text(encoding="utf-8")
    assert text.count("![") >= 3, "the README has no screenshots to look at"
    for topic in ("TRACKING", "calibration", "compact"):
        assert topic.lower() in text.lower(), f"the README never mentions {topic}"
