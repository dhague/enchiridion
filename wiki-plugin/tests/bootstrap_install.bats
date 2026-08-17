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

    CURL_LOG="$BATS_TEST_TMPDIR/curl.log"

    # Stub sha256sum: always emits a fixed hash; write_stubs seeds checksums.txt
    # with the same fixed hash so the comparison always passes.
    cat > "$STUB_DIR/sha256sum" <<'EOF'
#!/bin/sh
echo "aabbccddeeff0011aabbccddeeff0011aabbccddeeff0011aabbccddeeff0011  $1"
EOF
    chmod +x "$STUB_DIR/sha256sum"
}

# Helper: write a uname stub that returns fixed -s / -m values.
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

# Helper: write a curl stub that records calls and produces the right output
# for each fetch.  checksums.txt gets a matching entry; the binary download
# gets a tiny shell script so the version-check exec succeeds.
# Can't pre-create files under $tmp_dir because mktemp's path isn't known
# until the script runs, so the curl stub is the one place to intercept.
write_stubs() {
    local asset="$1"
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
    write_stubs "enchiridion_linux_amd64"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_linux_amd64" "$CURL_LOG"
    ! grep -q "\.exe" "$CURL_LOG"
}

@test "Darwin arm64: asset is enchiridion_darwin_arm64, no .exe" {
    stub_uname "Darwin" "arm64"
    write_stubs "enchiridion_darwin_arm64"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_darwin_arm64" "$CURL_LOG"
    ! grep -q "\.exe" "$CURL_LOG"
}

@test "MINGW64 (Git Bash Windows): asset is enchiridion_windows_amd64.exe" {
    stub_uname "MINGW64_NT-10.0-26200" "x86_64"
    write_stubs "enchiridion_windows_amd64.exe"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_windows_amd64.exe" "$CURL_LOG"
}

@test "MINGW32 (32-bit Git Bash): asset is enchiridion_windows_amd64.exe" {
    stub_uname "MINGW32_NT-6.2" "x86_64"
    write_stubs "enchiridion_windows_amd64.exe"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_windows_amd64.exe" "$CURL_LOG"
}

@test "MSYS_NT (MSYS2): asset is enchiridion_windows_amd64.exe" {
    stub_uname "MSYS_NT-10.0-26200" "x86_64"
    write_stubs "enchiridion_windows_amd64.exe"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_windows_amd64.exe" "$CURL_LOG"
}

@test "CYGWIN_NT: asset is enchiridion_windows_amd64.exe" {
    stub_uname "CYGWIN_NT-10.0" "x86_64"
    write_stubs "enchiridion_windows_amd64.exe"

    run_install

    [ "$status" -eq 0 ]
    grep -q "enchiridion_windows_amd64.exe" "$CURL_LOG"
}

@test "unknown OS produces a helpful error message" {
    stub_uname "SunOS" "x86_64"

    run_install

    [ "$status" -ne 0 ]
    echo "$output" | grep -q "unsupported OS"
    echo "$output" | grep -q "SunOS"
}

@test "Windows cached binary is returned without a fetch" {
    stub_uname "MINGW64_NT-10.0-26200" "x86_64"

    cache_dir="$PLUGIN_ROOT/.enchiridion-cache/v0.8.0"
    mkdir -p "$cache_dir"
    printf '#!/bin/sh\necho "0.8.0"\n' > "$cache_dir/enchiridion.exe"
    chmod +x "$cache_dir/enchiridion.exe"

    run_install

    [ "$status" -eq 0 ]
    echo "$output" | grep -q "enchiridion.exe"
    [ ! -e "$CURL_LOG" ]
}

@test "Windows: ENCHIRIDION_NO_FETCH with no cache fails fast" {
    stub_uname "MINGW64_NT-10.0-26200" "x86_64"

    PATH="$STUB_DIR:$PATH" ENCHIRIDION_NO_FETCH=1 run sh "$SCRIPT" "$PLUGIN_ROOT" "0.8.0"

    [ "$status" -ne 0 ]
    echo "$output" | grep -q "ENCHIRIDION_NO_FETCH"
    [ ! -e "$CURL_LOG" ]
}
