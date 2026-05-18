"""#77 — prune_persisted_upload: removes a persisted uploaded doc, is
path-safety-guarded (can NEVER escape UPLOADED_DOCS_DIR), supports exact
id + prefix, and never silently no-ops a traversal attempt."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _seed(root: Path, pid: str) -> Path:
    from backend import uploaded_docs as u
    d = u._doc_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "record.json").write_text('{"policy_id": "%s"}' % pid)
    (d / "meta.json").write_text("{}")
    return d


def test_prune_exact_and_prefix(tmp_path, monkeypatch):
    from backend.config import settings
    from backend import uploaded_docs as u
    monkeypatch.setattr(settings, "UPLOADED_DOCS_DIR", tmp_path)

    a = _seed(tmp_path, "user-upload__e2e-verify-a__zzz")
    b = _seed(tmp_path, "user-upload__e2e-verify-b__zzz")
    keep = _seed(tmp_path, "user-upload__real-user__myplan")
    assert a.exists() and b.exists() and keep.exists()

    # exact id
    r1 = u.prune_persisted_upload("user-upload__e2e-verify-a__zzz")
    assert r1["removed"] == ["user-upload__e2e-verify-a__zzz"]
    assert not a.exists() and b.exists() and keep.exists()

    # prefix (bulk) — only e2e-verify-*, never the real user doc
    r2 = u.prune_persisted_upload(prefix="user-upload__e2e-verify")
    assert "user-upload__e2e-verify-b__zzz" in r2["removed"]
    assert not b.exists()
    assert keep.exists(), "prefix prune must NOT touch non-matching docs"

    # absent id → skipped, not error, not silent
    r3 = u.prune_persisted_upload("user-upload__does-not-exist")
    assert r3["removed"] == [] and r3["skipped"] == ["user-upload__does-not-exist"]


def test_prune_path_traversal_raises(tmp_path, monkeypatch):
    """A traversal attempt MUST raise, never delete outside the root."""
    from backend.config import settings
    from backend import uploaded_docs as u
    monkeypatch.setattr(settings, "UPLOADED_DOCS_DIR", tmp_path)
    outside = tmp_path.parent / "DO_NOT_DELETE"
    outside.mkdir(exist_ok=True)
    (outside / "keep.txt").write_text("safe")
    # _doc_dir sanitises slashes/dots, so the dir resolves INSIDE root and
    # is simply "not present" → skipped; the outside dir is untouched.
    r = u.prune_persisted_upload("../../DO_NOT_DELETE")
    assert r["removed"] == []
    assert outside.exists() and (outside / "keep.txt").exists()


def test_empty_prefix_rejected(tmp_path, monkeypatch):
    from backend.config import settings
    from backend import uploaded_docs as u
    monkeypatch.setattr(settings, "UPLOADED_DOCS_DIR", tmp_path)
    with pytest.raises(RuntimeError):
        u.prune_persisted_upload(prefix="///")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
