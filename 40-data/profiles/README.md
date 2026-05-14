# `data/profiles/` — Named-profile JSON store

Persistent name-keyed profile store introduced by **KI-040 (2026-05-14)**. Lets a returning visitor say their name and have the bot recognise them + auto-load the stored profile, so they don't have to walk the 9-slot fact-find again.

Canonical store: `backend/profile_store.py`. ADR (deferred — code self-documents).

## File layout

```
data/profiles/
└── <normalised-name>.json     (one file per user)
```

- **Filename slug:** lowercase + alpha-only of the user's first name. `"Rohit"` and `"rohit."` both resolve to `rohit.json`.
- **Inside the file:** the original capitalised display name is preserved.

## JSON shape

```json
{
  "display_name": "Rohit",
  "first_seen": "2026-05-14T09:12:33Z",
  "last_seen":  "2026-05-14T22:41:08Z",
  "sessions":   ["sess_abc…", "sess_def…"],
  "profile": {
    "age": 32,
    "dependents": "spouse+1_child",
    "city_tier": "metro",
    "...": "..."
  }
}
```

Schema: `profile` mirrors the 9-slot `GRAPH` in `backend/needs_finder.py`. Everything else is bookkeeping.

## Two-layer sync

| Layer | Purpose | Where |
| --- | --- | --- |
| JSON (this folder) | Canonical, O(1) name-keyed lookup, deterministic, human-readable, manually editable. | `backend/profile_store.py::save_profile` |
| Chroma vector chunk | Re-embedded on every save so the brain sees the profile alongside policy chunks at retrieval time — powers "what's best for me?" questions. | `backend/profile_rag.py::upsert_profile_chunk` |

Both stay in sync: `save_profile()` fires the Chroma upsert in the same call. Embedding cost is once per update, not per query.

## Why JSON, not Chroma-only

The original design considered embedding-only. The "why JSON" trade-offs:

- **Deterministic name lookup.** `Rohit` → `rohit.json` is exact; vector search is approximate and can collide on common first names.
- **Human-readable.** A BFSI auditor can `cat` the file and see the full profile.
- **Manually editable.** Quick repair without a re-embed pipeline.
- **No HNSW bloat exposure.** Profile updates do not touch the policy vector store ([ADR-029](../70-docs/60-decisions/ADR-029-disk-storage-hardening.md)).

The Chroma chunk is purely a retrieval-time view of the canonical JSON.

## Privacy + retention

- Profiles are local to the deployed instance. No third-party share.
- Per [ADR-010](../70-docs/60-decisions/ADR-010-secret-handling.md), the folder is not exposed via the HTTP API except through the user's own `session_id`.
- The folder is committed empty (placeholder) — actual profiles are runtime artefacts.

## Related

- `backend/profile_store.py` — the canonical store implementation
- `backend/profile_extractor.py` + [ADR-022](../70-docs/60-decisions/ADR-022-conversational-profile-updates.md) — how conversational asides flow into the profile
- `backend/profile_rag.py` — the embedding mirror
- `backend/needs_finder.py::GRAPH` — the 9-slot schema the `profile` block conforms to
