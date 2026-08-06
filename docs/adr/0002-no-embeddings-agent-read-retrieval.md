# No embeddings — retrieval is an agent reading the map

Retrieval works by having `wiki-researcher` search the vault through `search.py` — the FTS index at `.wiki-knowledge/index.db` ([ADR-0006](0006-stdlib-fts5-not-embeddings.md)) — and follow typed edges from the hits it returns, not by embedding pages into a vector store. The index is a *filter* that narrows a vault to candidates; the agent's summary-first read over those candidates *is* the semantic search. It additionally sees supersession and volatility signals that cosine similarity has no way to represent. A vector store would also be new infrastructure (an embedding model, an index to keep in sync with every ingestion) for a problem the filter-and-read approach already solves at this scale.

## Consequences

Embeddings only earn their place once the FTS filter stops discriminating — a measured ranking failure where the right page is outside the top N of a labelled question set ([ADR-0006](0006-stdlib-fts5-not-embeddings.md)) — not at a vault-size number, and not an a-priori "if we ever hit 10k pages" guess. Don't pre-empt that; revisit only after ranking failure is actually measured.
