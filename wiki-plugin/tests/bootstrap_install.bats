#!/usr/bin/env bats
# Unit tests for bootstrap/install.sh OS / arch detection and asset naming.
#
# Stubs uname and curl so no network or real binary is needed.
# Each test exercises the OS case table and verifies the correct asset name
# and binary path are derived; the Windows case is the regression target for
# issue #230 (MINGW64_NT-10.0-26200 was previously an "unsupported OS" error).

SCRIPT="$BATS_TEST_DIRNAME/../bootstrap/install.sh"

setup() {
    PLUGIN_ROOT="$BATS_TEST_TMPDIR/plugin"
    mkdir -p "$PLUGIN_ROOT/.claude-plugin"
    cat > "$PLUGIN_ROOT/.claude-plugin/plugin.json" <<'EOF'
{
  "name": "wiki-knowledge",
  "version": "0.8.0"
}
EOF

    # Stub directory that will be prepended to PATH so our fakes win.
    STUB_DIR="$BATS_TEST_TMPDIR/stubs"
    mkdir -p "$STUB_DIR"

    # Stub curl: record calls, always succeed, write an empty file.
    CURL_LOG="$BATS_TEST_TMPDIR/curl.log"
    cat > "$STUB_DIR/curl" <<EOF
#!/bin/sh
echo "\$@" >> "$CURL_LOG"
# --output <path> is the last two args; write a placeholder so the script
# can proceed past the download step.
out=""
prev=""
for a in "\$@"; do
    [ "\$prev" = "--output" ] && out="\$a"
    prev="\$a"
done
[ -n "\$out" ] && : > "\$out"
exit 0
EOF
    chmod +x "$STUB_DIR/curl"

    # Stub sha256sum: always emit a hash that matches itself (we'll override
    # the expected hash below via a checksums.txt stub).
    cat > "$STUB_DIR/sha256sum" <<'EOF'
#!/bin/sh
echo "aabbccddeeff0011aabbccddeeff0011aabbccddeeff0011aabbccddeeff0011  $1"
EOF
    chmod +x "$STUB_DIR/sha256sum"
}

# Helper: write a uname stub that returns a fixed string.
stub_uname() {
    local s_val="$1" m_val="$2"
    cat > "$STUB_DIR/uname" <<EOF
#!/bin/sh
case "\$1" in
    -s) echo "$s_val" ;;
    -m) echo "$m_val" ;;
    *)  echo "$s_val" ;;
esac
EOF
    chmod +x "$STUB_DIR/uname"
}

# Helper: write a checksums.txt stub whose entry matches the sha256sum stub.
write_checksums() {
    local asset="$1"
    local cache_dir="$PLUGIN_ROOT/.enchiridion-cache/v0.8.0"
    mkdir -p "$cache_dir"
    # The script fetches checksums.txt into tmp_dir; we seed it there by
    # intercepting via the curl stub writing a zero-byte file, then the awk
    # lookup would fail.  Instead we patch the script's lookup by providing a
    # real checksums.txt through a second curl stub that writes the content.
    # Simpler: just pre-create the checksums inside the tmp area isn't possible
    # without knowing mktemp's path.  Use a different approach: make the curl
    # stub write a real checksums.txt for the second call (checksums.txt fetch).
    ASSET_NAME="$asset"
    # Rewrite curl stub to produce a meaningful checksums.txt on second call.
    cat > "$STUB_DIR/curl" <<STUBEOF
#!/bin/sh
echo "\$@" >> "$CURL_LOG"
out=""
prev=""
for a in "\$@"; do
    [ "\$prev" = "--output" ] && out="\$a"
    prev="\$a"
done
[ -z "\$out" ] && exit 0
case "\$out" in
    *checksums.txt)
        echo "aabbccddeeff0011aabbccddeeff0011aabbccddeeff0011aabbccddeeff0011  $asset" > "\$out"
        ;;
    *)
        : > "\$out"
        ;;
esac
exit 0
STUBEOF
    chmod +x "$STUB_DIR/curl"
}

# Helper: patch the downloaded binary stub so `version` succeeds.
# The script does: "$tmp_dir/enchiridion${exe}" version
# We can't predict $tmp_dir, so we make chmod +x of a zero-byte file work by
# replacing the version-check binary after the mv via a wrapper.  Easiest:
# stub the binary with a shell script written by the curl stub.
write_binary_stub() {
    local exe="$1"   # "" or ".exe"
    cat > "$STUB_DIR/curl" <<STUBEOF
#!/bin/sh
echo "\$@" >> "$CURL_LOG"
out=""
prev=""
for a in "\$@"; do
    [ "\$prev" = "--output" ] && out="\$a"
    prev="\$a"
done
[ -z "\$out" ] && exit 0
case "\$out" in
    *checksums.txt)
        echo "aabbccddeeff0011aabbccddeeff0011aabbccddeeff0011aabbccddeeff0011  $ASSET_NAME" > "\$out"
        ;;
    *)
        # Write a tiny shell script as the "binary" so version check passes.
        printf '#!/bin/sh\necho "0.8.0"\n' > "\$out"
        chmod +x "\$out"
        ;;
esac
exit 0
STUBEOF
    chmod +x "$STUB_DIR/curl"
}

run_install() {
    PATH="$STUB_DIR:$PATH" run sh "$SCRIPT" "$PLUGIN_ROOT" "0.8.0"
}

# ---------------------------------------------------------------------------

@test "Linux amd64: asset is enchiridion_linux_amd64, no .exe" {
    stub_uname "Linux" "x86_64"
    ASSET_NAME="enchiridion_linux_amd64"
    write_checksums "$ASSET_NAME"
    write_binary_stub ""

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_linux_amd64" "$CURL_LOG"
    ! grep -q "\.exe" "$CURL_LOG"
}

@test "Darwin arm64: asset is enchiridion_darwin_arm64, no .exe" {
    stub_uname "Darwin" "arm64"
    ASSET_NAME="enchiridion_darwin_arm64"
    write_checksums "$ASSET_NAME"
    write_binary_stub ""

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_darwin_arm64" "$CURL_LOG"
    ! grep -q "\.exe" "$CURL_LOG"
}

@test "MINGW64 (Git Bash Windows): asset is enchiridion_windows_amd64.exe" {
    stub_uname "MINGW64_NT-10.0-26200" "x86_64"
    ASSET_NAME="enchiridion_windows_amd64.exe"
    write_checksums "$ASSET_NAME"
    write_binary_stub ".exe"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_windows_amd64.exe" "$CURL_LOG"
}

@test "MINGW32 (32-bit Git Bash): asset is enchiridion_windows_amd64.exe" {
    stub_uname "MINGW32_NT-6.2" "x86_64"
    ASSET_NAME="enchiridion_windows_amd64.exe"
    write_checksums "$ASSET_NAME"
    write_binary_stub ".exe"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_windows_amd64.exe" "$CURL_LOG"
}

@test "MSYS_NT (MSYS2): asset is enchiridion_windows_amd64.exe" {
    stub_uname "MSYS_NT-10.0-26200" "x86_64"
    ASSET_NAME="enchiridion_windows_amd64.exe"
    write_checksums "$ASSET_NAME"
    write_binary_stub ".exe"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_windows_amd64.exe" "$CURL_LOG"
}

@test "CYGWIN_NT: asset is enchiridion_windows_amd64.exe" {
    stub_uname "CYGWIN_NT-10.0" "x86_64"
    ASSET_NAME="enchiridion_windows_amd64.exe"
    write_checksums "$ASSET_NAME"
    write_binary_stub ".exe"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_windows_amd64.exe" "$CURL_LOG"
}

@test "unknown OS produces a helpful error message" {
    stub_uname "SunOS" "x86_64"

    run_install

    [ "$status" -ne 0 ]
    [[ "$output" == *"unsupported OS"* ]]
    [[ "$output" == *"SunOS"* ]]
}

@test "Windows cached binary is returned without a fetch" {
    stub_uname "MINGW64_NT-10.0-26200" "x86_64"

    # Pre-seed the cache with an executable stub.
    cache_dir="$PLUGIN_ROOT/.enchiridion-cache/v0.8.0"
    mkdir -p "$cache_dir"
    printf '#!/bin/sh\necho "0.8.0"\n' > "$cache_dir/enchiridion.exe"
    chmod +x "$cache_dir/enchiridion.exe"

    run_install

    [ "$status" -eq 0 ]
    [[ "$output" == *"enchiridion.exe"* ]]
    [ ! -e "$CURL_LOG" ]
}

@test "Windows: ENCHIRIDION_NO_FETCH with no cache fails fast" {
    stub_uname "MINGW64_NT-10.0-26200" "x86_64"

    PATH="$STUB_DIR:$PATH" ENCHIRIDION_NO_FETCH=1 run sh "$SCRIPT" "$PLUGIN_ROOT" "0.8.0"

    [ "$status" -ne 0 ]
    [[ "$output" == *"ENCHIRIDION_NO_FETCH"* ]]
    [ ! -e "$CURL_LOG" ]
}
