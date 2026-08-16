#!/usr/bin/env bats
# Covers the PATH-preference / version-drift decision that bin/enchiridion
# makes before falling back to its lazy-fetch bootstrap (docs/adr/0014,
# issue #210): a PATH-installed `enchiridion` whose `version` output matches
# plugin.json is exec'd; a mismatch halts with a nonzero exit instead of
# silently fetching a second binary; and no PATH binary falls through to the
# existing bootstrap, unchanged.

setup() {
    SCRIPT="$BATS_TEST_DIRNAME/../bin/enchiridion"

    PLUGIN_ROOT="$BATS_TEST_TMPDIR/plugin"
    mkdir -p "$PLUGIN_ROOT/.claude-plugin"
    cat > "$PLUGIN_ROOT/.claude-plugin/plugin.json" <<'EOF'
{
  "name": "wiki-knowledge",
  "version": "0.8.0"
}
EOF
    mkdir -p "$PLUGIN_ROOT/bin"
    cp "$SCRIPT" "$PLUGIN_ROOT/bin/enchiridion"
    chmod +x "$PLUGIN_ROOT/bin/enchiridion"

    # A stub bootstrap that records its arguments and "fetches" a fake
    # binary, so tests can assert whether the fetch path ran at all.
    mkdir -p "$PLUGIN_ROOT/bootstrap"
    FETCH_LOG="$BATS_TEST_TMPDIR/fetch.log"
    cat > "$PLUGIN_ROOT/bootstrap/install.sh" <<EOF
#!/bin/sh
echo "\$@" >> "$FETCH_LOG"
echo "$BATS_TEST_TMPDIR/fetched-binary"
EOF
    chmod +x "$PLUGIN_ROOT/bootstrap/install.sh"
    cat > "$BATS_TEST_TMPDIR/fetched-binary" <<'EOF'
#!/bin/sh
echo "fetched-binary invoked: $*"
EOF
    chmod +x "$BATS_TEST_TMPDIR/fetched-binary"

    PATH_BIN_DIR="$BATS_TEST_TMPDIR/pathbin"
    mkdir -p "$PATH_BIN_DIR"
}

path_binary_with_version() {
    cat > "$PATH_BIN_DIR/enchiridion" <<EOF
#!/bin/sh
if [ "\$1" = "version" ]; then
    echo "$1"
    exit 0
fi
echo "path-binary invoked: \$*"
EOF
    chmod +x "$PATH_BIN_DIR/enchiridion"
}

@test "matching PATH binary version execs the PATH binary, no fetch" {
    path_binary_with_version "0.8.0"

    PATH="$PATH_BIN_DIR:$PATH" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"path-binary invoked: search foo"* ]]
    [ ! -e "$FETCH_LOG" ]
}

@test "mismatched PATH binary version halts, no fetch" {
    path_binary_with_version "0.7.0"

    PATH="$PATH_BIN_DIR:$PATH" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -ne 0 ]
    [[ "$output" == *"0.7.0"* ]]
    [[ "$output" == *"0.8.0"* ]]
    [[ "$output" == *"upgrade"* ]]
    [ ! -e "$FETCH_LOG" ]
}

@test "matching PATH binary version tolerates a v-prefixed version string" {
    path_binary_with_version "v0.8.0"

    PATH="$PATH_BIN_DIR:$PATH" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"path-binary invoked: search foo"* ]]
    [ ! -e "$FETCH_LOG" ]
}

@test "no PATH binary falls through to the existing bootstrap" {
    run env PATH="$PATH_BIN_DIR:$PATH" "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"fetched-binary invoked: search foo"* ]]
    [ -e "$FETCH_LOG" ]
}

@test "a PATH binary that only resolves to this script itself falls through to the bootstrap" {
    # Simulate the plugin's own bin/ directory being on PATH: `command -v
    # enchiridion` would then resolve back to bin/enchiridion itself, which
    # must not be treated as an installed binary (infinite self-exec).
    PATH="$PLUGIN_ROOT/bin:$PATH" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"fetched-binary invoked: search foo"* ]]
    [ -e "$FETCH_LOG" ]
}

@test "ENCHIRIDION_BIN still wins over any PATH binary" {
    path_binary_with_version "0.8.0"

    cat > "$BATS_TEST_TMPDIR/dev-binary" <<'EOF'
#!/bin/sh
echo "dev-binary invoked: $*"
EOF
    chmod +x "$BATS_TEST_TMPDIR/dev-binary"

    PATH="$PATH_BIN_DIR:$PATH" ENCHIRIDION_BIN="$BATS_TEST_TMPDIR/dev-binary" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"dev-binary invoked: search foo"* ]]
    [ ! -e "$FETCH_LOG" ]
}

@test "ENCHIRIDION_VERSION skips the PATH-preference check entirely, even with a matching PATH binary" {
    path_binary_with_version "0.8.0"

    PATH="$PATH_BIN_DIR:$PATH" ENCHIRIDION_VERSION="0.9.0-dev" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"fetched-binary invoked: search foo"* ]]
    [ -e "$FETCH_LOG" ]
    grep -q "0.9.0-dev" "$FETCH_LOG"
}
