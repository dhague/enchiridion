# Attribution comes from ingested content, not git identity

Commits carry no operator-identity mechanism (no `WIKI_AUTHOR`, no reliance on git author/committer) — `commit.py` always commits under the default git identity, and it is the only thing allowed to create ingestion commits (never the agent freehand, which keeps commit messages uniform and machine-parseable). "Who's working on what" is deliberately read from the *ingested material itself* — an email's sender, a document's byline — because the person running an ingestion often isn't the person who did the work it describes, making git authorship the wrong signal even though it's the obvious one to reach for.

## Consequences

Any future feature that wants attribution (e.g. a "what changed this week" or "who's working on X" report) must capture it as structured frontmatter at ingestion time, not by mining `git log --author`. Content attribution may also name someone outside the team (a vendor, a customer) — a consumer of that data must filter deliberately rather than assume every name is a teammate.
