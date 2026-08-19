# Spike #248: node-sqlite3-wasm on Bun — findings

**Date:** 2026-08-19  
**Bun version:** 1.3.14  
**Branch:** spike/247-node-sqlite3-wasm-fts5

## Run command

```
bun spike-bundle.js
```

## Full output

```
Node.js: v24.3.0
PASS 1: WASM module loads without error
PASS 2: FTS5 virtual table creates without error (schema matches production: `page_ref UNINDEXED, title, summary, body, tokenize = 'porter unicode61'`)
PASS 3: porter unicode61 tokenizer registers without error
PASS 4: Three test pages insert successfully
PASS 5: Keyword MATCH returns expected page and no others
PASS 6: bm25(page_fts, 0.0, 10.0, 5.0, 1.0) returns distinct, non-zero values: -0.00000104500, -0.00000196832
PASS 7: Phrase-quoted query "fast retrieval" matches only the page containing that exact phrase

All assertions PASS
```

## Verdict

**(a) node-sqlite3-wasm passes on Bun.**

All 7 assertions pass. FTS5 with porter unicode61 tokenizer, bm25 column weights, phrase queries, and keyword MATCH all work correctly. No bun:sqlite fallback needed.

Bun's Node.js compat layer is sufficient — `node-sqlite3-wasm`'s WASM-based approach means no native bindings to recompile, and Bun's WASM runtime handles the module without issue.
