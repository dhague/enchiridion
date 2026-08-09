"""Resolve a candidate set's supersession chains to their current heads.

:func:`page_record.load_records` already inverts every page's ``supersedes``
edge into ``superseded_by``; this module is the retrieval-facing entrypoint
into that derivation.

Takes the retrieval frontier's candidate set of page_refs and returns, for
each, its *active* page: the same page_ref if it's current, or the page at
the end of its supersession chain otherwise. A chain head is returned even
when it falls outside the given candidate set -- ``supersedes`` is a
recorded fact (see the conventions spec's frontmatter schema), so the head
is surfaced rather than left for the reader to notice a bare page_ref is
stale.

Seed-set in, filtered active set out (not the whole vault's map): retrieval
always has a specific candidate set in hand by the time it needs this, and
resolving the chain-walk in the script -- rather than handing back the raw
map for an agent to walk client-side -- is the whole point of moving this
out of prose.

CLI::

    python superseded_by.py wiki/concepts/a.md wiki/concepts/x.md --json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Sequence

import page_record
import vault as vault_mod


@dataclass(frozen=True)
class Resolution:
    """One seed's supersession chain, walked to its current head.

    ``chain`` lists the intermediate/final pages between ``seed`` and
    ``active`` (excluding ``seed``, ending with ``active``); empty when
    ``seed`` is already current.
    """

    seed: str
    active: str
    chain: list[str]


def resolve(
    seeds: Sequence[str], records: dict[str, page_record.PageRecord]
) -> list[Resolution]:
    """Walk each seed's ``superseded_by`` pointers to its current head.

    A page missing from ``records`` (outside the vault) resolves to itself
    with an empty chain. When a page's ``superseded_by`` lists more than one
    successor, the first is followed -- the same first-write-wins
    convention :mod:`search_index` uses, since the schema doesn't model
    forked supersession.
    """
    resolutions = []
    for seed in seeds:
        chain: list[str] = []
        current = seed
        seen = {current}
        while True:
            rec = records.get(current)
            successors = rec.superseded_by if rec else []
            if not successors:
                break
            nxt = successors[0]
            if nxt in seen:
                break  # a supersedes cycle would spin forever otherwise
            chain.append(nxt)
            seen.add(nxt)
            current = nxt
        resolutions.append(Resolution(seed=seed, active=current, chain=chain))
    return resolutions


def _resolution_to_dict(res: Resolution) -> dict:
    return {"seed": res.seed, "active": res.active, "chain": res.chain}


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "page_ref", nargs="+", help="candidate page_ref(s) to resolve (vault-relative)",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="emit results as JSON Lines (one object per line)",
    )
    args = parser.parse_args(argv)

    root = vault_mod.resolve_vault_root()
    v = vault_mod.Vault(root)
    records = v.pages()

    resolutions = resolve(args.page_ref, records)

    if args.as_json:
        for res in resolutions:
            print(json.dumps(_resolution_to_dict(res)))
    else:
        for res in resolutions:
            if res.chain:
                via = "" if len(res.chain) == 1 else f" via {' -> '.join(res.chain[:-1])}"
                print(f"{res.seed}  ->  {res.active}{via}")
            else:
                print(f"{res.seed}  (current)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
