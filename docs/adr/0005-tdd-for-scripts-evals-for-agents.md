# TDD for deterministic scripts, evals for agents — two layers, not one

The plugin's two layers get two different testing instruments, deliberately not unified into one suite. The Python script layer (`vault.py`, `frontmatter.py`, `links.py`, `build_index.py`, `commit.py`) has clear inputs and exact outputs, so it's built test-first with `pytest`, including property tests (`hypothesis`) on the two functions that carry real correctness risk: frontmatter round-tripping and link-splicing on move. The agent layer (`wiki-ingest`, `wiki-researcher`) makes judgment calls that don't have a single correct output, so it's checked with evals against a hand-authored golden vault instead — structural assertions wherever possible (does the page carry the expected fields? is the superseded page absent from the result set?), an LLM judge only for genuinely fuzzy questions, and a pass-rate over N runs rather than a single green, since agent behavior varies run to run.

## Consequences

The golden vault and its property list are human-owned, not agent-generated — an agent that writes both the implementation and its own success criteria will converge on criteria its implementation already meets. Retrieval evals run against the fixed golden vault, never against ingestion's own output, so an ingestion bug and a retrieval bug can't cancel each other out and both show green.
