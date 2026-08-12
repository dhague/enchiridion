# enchiridion-go

Go port of the wiki-knowledge plugin's script layer — one static binary
(`enchiridion`), one subcommand per current Python script. See
[docs/adr/0011](../docs/adr/0011-go-rewrite-scope-sequencing-toolchain.md)
through
[0013](../docs/adr/0013-go-binary-lazy-fetch-dependency-free-bootstrap.md)
for the design decisions behind this layout, and
[#140](https://github.com/dhague/enchiridion/issues/140) for the rewrite's
tracking issue.

This ticket ([#149](https://github.com/dhague/enchiridion/issues/149)) adds
only the scaffolding — a Cobra CLI skeleton with a `version` subcommand, the
GoReleaser release pipeline, and the dependency-free bootstrap scripts. Real
subcommand logic lands in later child issues (#150-#153).

## Layout

- `cmd/enchiridion/` — the `main` package; thin entrypoint into `internal/cli`.
- `internal/cli/` — Cobra root command and subcommand wiring.
- `internal/version/` — build-time version string, set via `-ldflags` by
  GoReleaser from the git tag driving the release.
- `bootstrap/install.sh` / `bootstrap/install.ps1` — dependency-free
  downloader scripts a migrated skill/hook shells out to on first
  invocation, to lazy-fetch the platform binary into a version-namespaced
  cache. Neither depends on Python or any other runtime.
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

Both scripts take a plugin root and a version, fetch the matching platform
binary from GitHub Releases into
`<plugin-root>/.enchiridion-cache/v<version>/`, verify its SHA256 checksum
against the release's `checksums.txt`, and print the resulting binary's
absolute path — the only thing they write to stdout, so callers can capture
it directly:

```sh
BIN=$(bootstrap/install.sh "$PLUGIN_ROOT" "$VERSION")
```

```powershell
$Bin = powershell -File bootstrap/install.ps1 -PluginRoot $PluginRoot -Version $Version
```

The cache is version-namespaced, not a rollback mechanism: fetching a new
version prunes every other cached version directory first
([ADR-0013](../docs/adr/0013-go-binary-lazy-fetch-dependency-free-bootstrap.md)).
