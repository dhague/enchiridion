# Go binary is lazy-fetched by a dependency-free bootstrap, never bundled or PATH-installed

The [Go rewrite](0011-go-rewrite-scope-sequencing-toolchain.md) ships one static binary per platform (six targets) via GitHub Releases rather than checking binaries into the plugin package (bloats the repo and every clone) or requiring a manual install step (defeats the goal for non-coding-agent users on Claude Desktop/Cowork, who won't know what to do with a curl command). Instead, each skill/hook that has been migrated to call `enchiridion` triggers a **lazy fetch on first invocation**: if the expected binary isn't present in a version-namespaced cache path inside the plugin's own directory, it's downloaded, SHA256-checksum-verified against the checksums GoReleaser publishes per release, and cached there. The binary is invoked by absolute path, never assumed to be on `PATH` — skills/hooks already know their own plugin root, so there's no reason to impose PATH management on the user. Cache version is synced to the plugin's release version; a mismatch triggers re-download-and-replace, since this is a cache, not a rollback mechanism, so no old versions are retained.

The critical constraint: **the bootstrap/downloader script itself must not depend on Python** (or any other runtime). If it did, a migrated skill would still silently require Python on its very first call — the exact dependency this whole rewrite exists to eliminate, just moved one level down and hidden. The bootstrap is therefore a platform-native shell script (macOS/Linux, using `curl`) and a PowerShell script (Windows, using `Invoke-WebRequest`, built in since Windows 7) — tools that are essentially always present without installation, unlike Python.

## Consequences

Because signing/notarization is deferred ([ADR-0011](0011-go-rewrite-scope-sequencing-toolchain.md)), the bootstrap also has to handle the resulting friction: it specifically detects macOS's "cannot be opened because the developer cannot be verified" quarantine block and prints the exact remediation (`xattr -d com.apple.quarantine …`) rather than surfacing a generic download-succeeded-but-execution-failed error, since a non-technical user hitting a generic error at that point has no path forward.

The checksum the bootstrap verifies proves the download matches what the release published — it says nothing about who published it. The release workflow therefore also emits a **GitHub build-provenance attestation** over `checksums.txt`, binding every released artifact to the workflow, commit, and repo that built it. Anyone can check a downloaded binary with:

```sh
gh attestation verify <path to enchiridion binary> --repo dhague/enchiridion
```

This is deliberately **out of the bootstrap's path**, and the two facts above are why. Verifying an attestation needs the `gh` CLI and a second network round-trip, which would reintroduce exactly the kind of runtime dependency the bootstrap exists to avoid — and the user who most needs a frictionless first call is the least likely to have `gh` installed. Attestation is an opt-in check for anyone who wants it, not a gate on first use. It is also **not a substitute for code signing** ([#154](https://github.com/dhague/enchiridion/issues/154)): Gatekeeper and SmartScreen do not consult it, so the quarantine remediation above stays load-bearing until signing lands.
