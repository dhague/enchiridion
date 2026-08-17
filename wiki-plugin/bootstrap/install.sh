#!/bin/sh
# Dependency-free bootstrap for the enchiridion Go binary (macOS/Linux/Windows).
#
# Lazy-fetches the platform binary from a GitHub Release into a
# version-namespaced cache directory, verifies its SHA256 checksum, and
# prints the resulting binary's absolute path on stdout. Every other line of
# output goes to stderr, so callers can safely do:
#
#   BIN=$(bootstrap/install.sh "$PLUGIN_ROOT" "$VERSION")
#
# Deliberately POSIX sh, not bash: on the coding-agent hosts this targets,
# bash is a safe bet, but /bin/sh is the one thing every one of the
# supported non-coding-agent hosts (Claude Desktop, Cowork) is guaranteed to
# have, and this script must not depend on Python (docs/adr/0013) or on
# anything less universal than curl.
#
# Usage: install.sh <plugin-root> <version> [repo]
#   plugin-root  Directory the version-namespaced cache lives under.
#   version      Plugin release version, e.g. "0.7.5" or "v0.7.5".
#   repo         "owner/name" GitHub repo to fetch releases from.
#                Defaults to dhague/enchiridion.

set -eu

die() {
    echo "enchiridion bootstrap: $1" >&2
    exit 1
}

plugin_root=${1:?"usage: install.sh <plugin-root> <version> [repo]"}
version_arg=${2:?"usage: install.sh <plugin-root> <version> [repo]"}
repo=${3:-dhague/enchiridion}

case "$version_arg" in
    v*) version=$version_arg ;;
    *) version="v$version_arg" ;;
esac

os=$(uname -s)
case "$os" in
    Darwin) goos=darwin ;;
    Linux) goos=linux ;;
    # Git Bash / MSYS2 / Cygwin on Windows report MINGW*, MSYS*, or CYGWIN*.
    MINGW* | MSYS* | CYGWIN*) goos=windows ;;
    *) die "unsupported OS '$os' — this script covers macOS, Linux, and Windows (Git Bash / MSYS2 / Cygwin)" ;;
esac

arch=$(uname -m)
case "$arch" in
    arm64 | aarch64) goarch=arm64 ;;
    x86_64 | amd64) goarch=amd64 ;;
    *) die "unsupported architecture '$arch'" ;;
esac

# .exe is required for Windows — the OS won't execute a PE binary without it.
if [ "$goos" = "windows" ]; then
    exe=".exe"
else
    exe=""
fi

cache_dir="$plugin_root/.enchiridion-cache/$version"
binary_path="$cache_dir/enchiridion${exe}"
asset="enchiridion_${goos}_${goarch}${exe}"
base_url="https://github.com/$repo/releases/download/$version"

if [ -x "$binary_path" ]; then
    echo "$binary_path"
    exit 0
fi

# Cache-only mode, for callers that must never pay for a download: the
# PostToolUse hook fires on every single tool call, so if the release asset is
# missing or the machine is offline it would otherwise re-attempt (and re-fail)
# two curls per call, all session, with nobody reading the stderr.
if [ -n "${ENCHIRIDION_NO_FETCH:-}" ]; then
    die "no cached binary for $version and \$ENCHIRIDION_NO_FETCH is set — not fetching"
fi

echo "enchiridion bootstrap: fetching $version for $goos/$goarch" >&2

# This is a cache, not a rollback mechanism (docs/adr/0013) — drop every
# other version directory before fetching the one that's actually needed.
if [ -d "$plugin_root/.enchiridion-cache" ]; then
    for dir in "$plugin_root/.enchiridion-cache"/*; do
        [ -d "$dir" ] || continue
        [ "$dir" = "$cache_dir" ] && continue
        rm -rf "$dir"
    done
fi

mkdir -p "$cache_dir"
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/enchiridion-bootstrap.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT

curl_fetch() {
    curl --fail --silent --show-error --location "$1" --output "$2"
}

if ! curl_fetch "$base_url/$asset" "$tmp_dir/enchiridion${exe}"; then
    die "download failed for $asset from $base_url — check that $version was released for $goos/$goarch"
fi

if ! curl_fetch "$base_url/checksums.txt" "$tmp_dir/checksums.txt"; then
    die "failed to download checksums.txt from $base_url"
fi

expected_sha=$(awk -v asset="$asset" '$2 == asset { print $1 }' "$tmp_dir/checksums.txt")
[ -n "$expected_sha" ] || die "no checksum entry for $asset in checksums.txt"

if command -v sha256sum >/dev/null 2>&1; then
    actual_sha=$(sha256sum "$tmp_dir/enchiridion${exe}" | awk '{ print $1 }')
elif command -v shasum >/dev/null 2>&1; then
    actual_sha=$(shasum -a 256 "$tmp_dir/enchiridion${exe}" | awk '{ print $1 }')
else
    die "neither sha256sum nor shasum is available to verify the download"
fi

[ "$actual_sha" = "$expected_sha" ] || die "checksum mismatch for $asset (expected $expected_sha, got $actual_sha) — download may be corrupt or tampered with"

chmod +x "$tmp_dir/enchiridion${exe}"

if [ "$goos" = "darwin" ] && command -v xattr >/dev/null 2>&1; then
    # curl doesn't normally set com.apple.quarantine, but strip it
    # proactively if present so a freshly-fetched, unsigned binary
    # (docs/adr/0011: signing is deferred) doesn't hit Gatekeeper on first
    # run. Best-effort: a failure here falls through to the execute-time
    # check below, which prints the manual remediation.
    xattr -d com.apple.quarantine "$tmp_dir/enchiridion${exe}" 2>/dev/null || true
fi

if ! "$tmp_dir/enchiridion${exe}" version >/dev/null 2>"$tmp_dir/exec-err"; then
    if [ "$goos" = "darwin" ] && grep -qi "cannot be opened because the developer cannot be verified\|Operation not permitted" "$tmp_dir/exec-err" 2>/dev/null; then
        die "macOS blocked the downloaded binary (Gatekeeper — enchiridion is not yet code-signed, see docs/adr/0011). Remediate with:
  xattr -d com.apple.quarantine '$tmp_dir/enchiridion${exe}'
then re-run this bootstrap. If that doesn't clear it, open System Settings > Privacy & Security and allow enchiridion to run."
    fi
    die "downloaded binary failed to execute: $(cat "$tmp_dir/exec-err")"
fi

mv "$tmp_dir/enchiridion${exe}" "$binary_path"
echo "$binary_path"
