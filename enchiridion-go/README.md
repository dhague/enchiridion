# enchiridion-go

The wiki-knowledge plugin's script layer — one static binary (`enchiridion`),
one subcommand per capability. See
[docs/adr/0011](../docs/adr/0011-go-rewrite-scope-sequencing-toolchain.md)
through
[0013](../docs/adr/0013-go-binary-lazy-fetch-dependency-free-bootstrap.md)
for the design decisions behind this layout.

## Layout

- `cmd/enchiridion/` — the `main` package; thin entrypoint into `internal/cli`.
- `internal/cli/` — Cobra root command and subcommand wiring, one file per
  subcommand.
- `internal/version/` — build-time version string, set via `-ldflags` by
  GoReleaser from the git tag driving the release.
- `internal/wikipage/`, `internal/place/`, `internal/pagerecord/`,
  `internal/vault/`, `internal/vaultgit/` — the page model and vault I/O
  layer.
- `internal/searchindex/`, `internal/initwiki/` — what `search` and `init`
  are built on.
- `../wiki-plugin/bootstrap/` and `../wiki-plugin/bin/enchiridion` — the
  runtime side of distribution. They live in the plugin package, not here,
  because the plugin root is what ships to users and what a skill can
  address at invocation time.
- `.goreleaser.yaml` — builds 6 platform targets (macOS/Linux/Windows ×
  amd64/arm64) and publishes SHA256 checksums per release.

## Development

Requires Go (version pinned in `go.mod`).

```sh
go build ./...
go vet ./...
go test ./...
```

To validate the release pipeline locally without publishing:

```sh
goreleaser release --snapshot --clean --skip=publish
```

## Bootstrap scripts

Both live in `../wiki-plugin/bootstrap/`. Skills and hooks do not call them
directly — they call `wiki-plugin/bin/enchiridion`, which reads the plugin's
version from `.claude-plugin/plugin.json`, runs the bootstrap, and execs the
resulting binary with its arguments passed through. Set `ENCHIRIDION_BIN` to
a locally built binary to work against unreleased changes, or
`ENCHIRIDION_VERSION` to pin a different release.

Both scripts take a plugin root and a version, fetch the matching platform
binary from GitHub Releases into
`<plugin-root>/.enchiridion-cache/v<version>/`, verify its SHA256 checksum
against the release's `checksums.txt`, and print the resulting binary's
absolute path — the only thing they write to stdout, so callers can capture
it directly:

```sh
BIN=$(../wiki-plugin/bootstrap/install.sh "$PLUGIN_ROOT" "$VERSION")
```

```powershell
$Bin = powershell -File ../wiki-plugin/bootstrap/install.ps1 -PluginRoot $PluginRoot -Version $Version
```

The cache is version-namespaced, not a rollback mechanism: fetching a new
version prunes every other cached version directory first
([ADR-0013](../docs/adr/0013-go-binary-lazy-fetch-dependency-free-bootstrap.md)).
