# Saving an answer as a synthesis page

For **session holding the conversation** — invoked `wiki-researcher` and got `save-candidate` block back (or ran procedure and reached step 8 itself). Researcher subagent never gets here.

**Gate:** vault not written unless user says yes to question you actually asked. Silence isn't yes; "sounds useful" isn't yes; fresh session isn't holding earlier yes. If unsure whether told to save — you were not.

1. **Put the offer** in one line after relaying answer, naming what would be written and where:

   > *Worth saving as `wiki/synthesis/how-connection-pooling-is-configured.md`, sourced from the 3 pages it cites? (y/n)*

2. **On anything but explicit yes, stop.** No page, no plan file, no commit, no "I'll prepare just in case" — declining leaves vault byte-identical. Acknowledge in few words and move on; don't re-offer later in same session.

3. **On explicit yes, write the plan.** Save is `IngestPlan` run through same executor ingestion uses — placement, frontmatter, index regeneration, commit are mechanics, and mechanics belongs in tested script, not re-derived here. Write scratch `plan.json` (not a vault file) from `save-candidate` block:

   ```jsonc
   {
     "title": "<the candidate's title>",
     "action": "synthesize",              // NOT "ingest" — this is a researcher-saved page; the commit subject says so
     "source_date": "<today>",
     "pages": [
       {
         "op": "create",
         "kind": "synthesis",             // always — a saved query result is synthesis/ by the placement algorithm's step 2
         "title": "<the candidate's title>",
         "body": "<the answer, in full markdown, written as a page rather than a chat reply>",
         "frontmatter": {
           "summary": "<the candidate's summary line>",
           "tags": ["<the candidate's tags>"],
           "source_date": "<today>",
           "volatility": "<the candidate's volatility>"
         },
         "edges": {
           "source": ["wiki/concepts/db-connection-pooling.md"]   // one per cited page — the block's vault-relative paths, unchanged; `enchiridion ingest` composes the actual link
         }
       }
     ]
   }
   ```

   One conversion the block leaves to you:
   - **`body`** — rewrite answer as a page, not a transcript: no "you asked", no search-trajectory line, no "per the vault". Keep citations as inline relative links, keep temporal framing (synthesis inherits inputs' uncertainty and must not launder it into confidence).

   `source:` needs no conversion — block's paths are already vault-relative, exactly what `edges` takes; `enchiridion ingest` composes title, `../` relativisation, and encoding, and its validation rejects target that doesn't resolve to real page.

   No `raw` field and no `raw_source` — synthesis page has no raw artifact; stands on `source` edges to other pages. That is the `raw_source:`/`source:` split the schema draws.

4. **Run it and report.**

   ```bash
   "<plugin-root>/bin/enchiridion" ingest --plan <plan.json>
   ```

   Validates whole plan before touching disk, then writes page and makes one structured commit — printing SHA. Report path and SHA in one line. If it raises, nothing was committed; fix plan and rerun (writes are idempotent).

   If title collides with existing `synthesis/` page, validation fails with *create target … already exists*. Don't work around by renaming to near-duplicate — existing page is either the answer already (cite it instead) or genuinely superseded, which is an ingestion decision, not retrieval.
