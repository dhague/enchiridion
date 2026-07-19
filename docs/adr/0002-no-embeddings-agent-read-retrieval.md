# No embeddings — retrieval is an agent reading the map

Retrieval works by having `wiki-researcher` read `_index.md` and follow typed edges, not by embedding pages into a vector store. At the golden-vault scale this build targets (~15–30 pages), an agent reading the index *is* the semantic search, and it additionally sees supersession and volatility signals that cosine similarity has no way to represent. A vector store would also be new infrastructure (an embedding model, an index to keep in sync with every ingestion) for a problem the map-read approach already solves at this scale.

## Consequences

Embeddings only earn their place once `_index.md` no longer fits a single read *and* folder tiering (grouping the index by kind before reading) stops discriminating — several thousand flat pages, well beyond this build's real-vault-scale scope. Don't pre-empt that; revisit only after both conditions are actually measured.
