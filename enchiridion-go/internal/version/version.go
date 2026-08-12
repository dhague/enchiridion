// Package version holds the build-time version string.
//
// Version is overridden at build time via -ldflags "-X ...=vX.Y.Z" by
// GoReleaser, which sets it from the git tag driving the release. It stays
// "dev" for local (non-release) builds. Bootstrap scripts compare this
// string against the plugin's release version to decide whether the cached
// binary is stale (docs/adr/0013).
package version

var Version = "dev"
