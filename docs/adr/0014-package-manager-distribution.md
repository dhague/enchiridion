# Distribute enchiridion via Homebrew and Chocolatey as an optional accelerator, not a signing substitute

[#190](https://github.com/dhague/enchiridion/issues/190) floated package managers as "simpler than getting signing keys." Grilling settled it as a **distribution/update channel, orthogonal to signing** — Homebrew and Chocolatey sidestep Gatekeeper/SmartScreen *prompts* (curl never quarantines; WebClient never writes MotW), but neither confers the AV reputation that only code signing gives, so [#154](https://github.com/dhague/enchiridion/issues/154) stays open.

The distribution is an **optional accelerator**, not a new install requirement: the dependency-free lazy-fetch bootstrap ([ADR-0013](0013-go-binary-lazy-fetch-dependency-free-bootstrap.md)) stays the default, and `bin/enchiridion`/`.cmd` gain a PATH-preference step that uses an already-installed binary when present. Delivery is one Homebrew tap plus one self-hosted Chocolatey package, both produced by GoReleaser:

- **Homebrew** — a separate `dhague/homebrew-enchiridion` tap repo holding `Formula/enchiridion.rb`: a `url`+`sha256` *pointer* at the GitHub Release asset, not an artifact store (binaries stay in the main repo's releases). Covers macOS **and Linux** (Linuxbrew, ~4 extra formula lines). A third-party tap, not homebrew-core, which has a notability bar the plugin won't meet.
- **Chocolatey** — a `.nupkg` built by GoReleaser and attached to the GitHub Release, **not** the community repository, which imposes per-version human moderation by a Chocolatey moderator — an external human in the release loop, rejected on Q6.

Both are `brews:` and `chocolateys:` config blocks in the existing `.goreleaser.yaml` — no custom release scripting, and GoReleaser fills `url`/`sha256` automatically.

## Consequences

- **Repo-specific PAT as a GitHub secret.** The release workflow's `GITHUB_TOKEN` only has rights to *this* repo, so pushing the formula to `homebrew-enchiridion` requires a PAT/fine-grained token stored as a repo secret. One-time setup, but load-bearing.
- **Version reconciliation: halt on drift.** When a PATH-installed binary's version differs from `plugin.json`'s, `bin/enchiridion` **halts** with `enchiridion X is behind plugin Y; run: brew upgrade enchiridion` (exit nonzero) rather than trusting blindly (new skills/hooks prose could call a subcommand an older binary lacks) or silently fetching a second copy (two divergent binaries). Halting is harmless in hooks — the existing `|| exit 0` swallows it — and surfaces the one corrective action in skills. It's viable because the release publishes the formula/`.nupkg` in lockstep with the binary bump, so the upgrade command is always immediately available. This extends ADR-0013's "cache synced to plugin version" invariant to "package-managed binary must also match," at the cost of a `command -v`/`where` lookup plus one local `enchiridion version` subprocess call per invocation in both entrypoints.
