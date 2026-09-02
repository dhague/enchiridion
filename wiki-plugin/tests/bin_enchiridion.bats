#!/usr/bin/env bats
# Covers wiki-plugin/bin/enchiridion's shim behaviour (issue #254) plus its
# artifact resolution order after packaging (D3 #288):
# ENCHIRIDION_BIN → in-plugin $plugin_root/scripts/cli.cjs → sibling
# enchiridion-ts/dist/cli.cjs. ENCHIRIDION_BIN overrides everything, for
# local dev against unbundled source or an alternate runtime.

setup() {
    SCRIPT="$BATS_TEST_DIRNAME/../bin/enchiridion"

    # Recreate the monorepo layout the shim assumes: wiki-plugin/ and
    # enchiridion-ts/ as siblings, with stub cli.cjs files standing in for
    # the real esbuild bundle.
    REPO_ROOT="$BATS_TEST_TMPDIR/repo"
    PLUGIN_ROOT="$REPO_ROOT/wiki-plugin"
    mkdir -p "$PLUGIN_ROOT/bin"
    cp "$SCRIPT" "$PLUGIN_ROOT/bin/enchiridion"
    chmod +x "$PLUGIN_ROOT/bin/enchiridion"

    mkdir -p "$REPO_ROOT/enchiridion-ts/dist"
    cat > "$REPO_ROOT/enchiridion-ts/dist/cli.cjs" <<'EOF'
console.log("bundle invoked: " + process.argv.slice(2).join(" "));
EOF

    # A stub `node` on PATH: proves the shim resolves the right script and
    # forwards arguments, without depending on a real Node install or a
    # real esbuild bundle.
    STUB_BIN_DIR="$BATS_TEST_TMPDIR/stubbin"
    mkdir -p "$STUB_BIN_DIR"
    cat > "$STUB_BIN_DIR/node" <<'EOF'
#!/bin/sh
echo "node invoked with: $*"
EOF
    chmod +x "$STUB_BIN_DIR/node"
}

write_in_plugin_bundle() {
    mkdir -p "$PLUGIN_ROOT/scripts"
    cat > "$PLUGIN_ROOT/scripts/cli.cjs" <<'EOF'
console.log("in-plugin bundle invoked");
EOF
}

@test "default: sibling dev bundle used when scripts/ has no bundle, args forwarded" {
    PATH="$STUB_BIN_DIR:$PATH" run "$PLUGIN_ROOT/bin/enchiridion" search foo --json

    [ "$status" -eq 0 ]
    [[ "$output" == *"node invoked with:"* ]]
    [[ "$output" == *"enchiridion-ts/dist/cli.cjs search foo --json"* ]]
}

@test "no arguments: still execs node against the sibling bundle" {
    PATH="$STUB_BIN_DIR:$PATH" run "$PLUGIN_ROOT/bin/enchiridion"

    [ "$status" -eq 0 ]
    [[ "$output" == *"enchiridion-ts/dist/cli.cjs"* ]]
}

@test "in-plugin bundle wins when both scripts/ and sibling dist/ exist" {
    write_in_plugin_bundle
    PATH="$STUB_BIN_DIR:$PATH" run "$PLUGIN_ROOT/bin/enchiridion" search bar

    [ "$status" -eq 0 ]
    [[ "$output" == *"node invoked with: $PLUGIN_ROOT/scripts/cli.cjs search bar"* ]]
    [[ "$output" != *"enchiridion-ts/dist/cli.cjs"* ]]
}

@test "ENCHIRIDION_BIN overrides the bundle entirely" {
    cat > "$BATS_TEST_TMPDIR/dev-binary" <<'EOF'
#!/bin/sh
echo "dev-binary invoked: $*"
EOF
    chmod +x "$BATS_TEST_TMPDIR/dev-binary"

    ENCHIRIDION_BIN="$BATS_TEST_TMPDIR/dev-binary" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"dev-binary invoked: search foo"* ]]
}

@test "ENCHIRIDION_BIN wins over the in-plugin bundle and node on PATH" {
    write_in_plugin_bundle
    cat > "$BATS_TEST_TMPDIR/dev-binary" <<'EOF'
#!/bin/sh
echo "dev-binary invoked: $*"
EOF
    chmod +x "$BATS_TEST_TMPDIR/dev-binary"

    PATH="$STUB_BIN_DIR:$PATH" ENCHIRIDION_BIN="$BATS_TEST_TMPDIR/dev-binary" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"dev-binary invoked: search foo"* ]]
    [[ "$output" != *"node invoked"* ]]
    [[ "$output" != *"in-plugin bundle invoked"* ]]
}
