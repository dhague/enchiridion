# Go port relaxes the byte-identical frontmatter round-trip contract

`wikipage.py` guarantees, property-tested, that a no-op frontmatter `set` round-trips byte-identical — only the frontmatter block is re-serialised, via ruamel.yaml pinned to `indent(mapping=2, sequence=4, offset=2)`. No Go YAML library offers an equivalent guarantee: `gopkg.in/yaml.v3` only "makes an effort" at pleasant formatting and doesn't preserve original text; `goccy/go-yaml` is closer (reversible transformation, preserves comments/anchors) but still isn't byte-identical on arbitrary input.

Two paths were considered for the [Go rewrite](0011-go-rewrite-scope-sequencing-toolchain.md)'s `ingest`/frontmatter-editing subcommand: relax the contract, or reproduce it via surgical text splicing — parsing only to locate the changed key(s) and editing those lines in place, leaving the rest of the document byte-untouched, mirroring the existing move/link-rewrite contract ("a move touches only link lines"). Splicing was rejected: hand-rolled positional YAML editing was judged more likely to introduce subtle corruption bugs than the formatting churn it avoids, and unlike the link-rewrite case (which only ever touches well-delimited markdown link syntax), YAML frontmatter has more structural variation to get wrong.

The contract is therefore **explicitly relaxed** in Go: an edit may reformat unrelated whitespace or reorder keys elsewhere in the frontmatter block, whereas the Python implementation would not. This is a deliberate, documented regression against the Python behaviour, not an oversight — a future reader diffing frontmatter churn in the Go-ingested vault should not treat it as a bug.

## Consequences

The move-touches-only-link-lines contract (including frontmatter markdown links: typed edges, `supersedes`, `raw_source`) is unaffected and must still hold, since link splicing is untouched by this decision — it stays property-tested via `pgregory.net/rapid` per [ADR-0011](0011-go-rewrite-scope-sequencing-toolchain.md). Only the *no-op-is-byte-identical* guarantee is dropped, and only for frontmatter, not links.
