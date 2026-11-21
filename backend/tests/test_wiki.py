"""Project wiki service: sync idempotency, revisions, safe paths, lint. Uses a temp wiki root for isolation;
the real docs/LLM_WIKI is linted in test_real_wiki_passes_lint."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import WikiPage, WikiRevision
from app.services import wiki


@pytest.fixture
def tmp_wiki(tmp_path, monkeypatch):
    root = tmp_path / "LLM_WIKI"
    root.mkdir()
    (root / "START_HERE.md").write_text("# Start\n\nSTATUS: IMPLEMENTED\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "note.md").write_text("# Note\n\nSTATUS: PLANNED\n", encoding="utf-8")
    monkeypatch.setattr(wiki, "wiki_root", lambda: root)
    return root


def _revs(session):
    return session.scalars(select(WikiRevision)).all()


def test_sync_is_idempotent_and_adds_revision_only_on_change(session, tmp_wiki):
    first = wiki.sync_from_disk(session)
    assert first["revisions_added"] == 2 and first["unchanged"] == 0
    second = wiki.sync_from_disk(session)
    assert second["revisions_added"] == 0 and second["unchanged"] == 2
    (tmp_wiki / "START_HERE.md").write_text("# Start\n\nupdated. STATUS: IMPLEMENTED\n", encoding="utf-8")
    third = wiki.sync_from_disk(session)
    assert third["revisions_added"] == 1
    page = wiki.get_page(session, "START_HERE.md")
    assert page["revision"] == 2 and [h["revision"] for h in page["history"]] == [2, 1]


def test_write_page_requires_note_and_writes_file_and_revision(session, tmp_wiki):
    with pytest.raises(ValueError):
        wiki.write_page(session, "START_HERE.md", "# x", "admin", "  ")
    out = wiki.write_page(session, "NEW.md", "# New page\n\nSTATUS: PROPOSED\n", "admin", "add page")
    assert (tmp_wiki / "NEW.md").read_text().startswith("# New page")
    assert out["revision"] == 1 and out["statuses"] == {"PROPOSED": 1}
    again = wiki.write_page(session, "NEW.md", "# New page\n\nSTATUS: PROPOSED\n", "admin", "same content")
    assert again["revision"] == 1  # identical content adds no revision


@pytest.mark.parametrize("bad", ["../escape.md", "/etc/passwd.md", "a/../../b.md", "file.txt", "x\\y.md", "", "a b.md"])
def test_path_traversal_and_bad_names_are_rejected(session, tmp_wiki, bad):
    with pytest.raises(ValueError):
        wiki.write_page(session, bad, "# x", "admin", "note")


def test_lint_reports_missing_pages_and_unknown_markers(session, tmp_wiki):
    (tmp_wiki / "BAD.md").write_text("# Bad\n\nSTATUS: DONE_MAYBE\n", encoding="utf-8")
    problems = wiki.lint(session)
    assert any("missing required page ROADMAP.md" in p for p in problems)
    assert any("unknown status marker DONE_MAYBE" in p for p in problems)
    assert any("decisions/README.md" in p for p in problems)


def test_get_unknown_page_returns_none(session, tmp_wiki):
    assert wiki.get_page(session, "NOPE.md") is None


def test_real_wiki_passes_lint(session):
    """The shipped docs/LLM_WIKI must satisfy its own lint (required pages + valid markers)."""
    assert wiki.wiki_root().is_dir(), "docs/LLM_WIKI is missing"
    assert wiki.lint(session) == []
