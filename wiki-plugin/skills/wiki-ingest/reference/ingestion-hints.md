Raw folder may carry `raw/<folder>/INGESTION.md`: freeform human instructions for ingesting that folder's documents — e.g. "take `source_date` from `Date:` header, list recipients in body, prefer `correspondence` tag". Read like `SKILL.md`: prose to interpret, not schema to parse.

- **Lookup is document's own folder only.** Ingesting `raw/emails/foo.eml` looks for `raw/emails/INGESTION.md`. **No ancestor walk** — `raw/INGESTION.md` and vault-root not consulted.
- **Hints win on conflict.** Explicit override, not tiebreaker. Folder's `INGESTION.md` "file as `entities/` pages, one per person" beats default placement algorithm.
- **Cannot extend frontmatter schema or waive chain of evidence.** `wiki-conventions` schema is fixed contract; hint steers judgment inside procedure — chunking, tag preference, `source_date` derivation, kind, typed edges — never adds frontmatter key, kind, or folder. "List recipients" puts recipients in page **body**; does not mint `recipients:` key. `source/` stub and back-edges not optional — `enchiridion ingest` rejects plan without them regardless.
- **An `INGESTION.md` is never itself ingested.** Instructions, not content — if handed as `<path>`, skip.
