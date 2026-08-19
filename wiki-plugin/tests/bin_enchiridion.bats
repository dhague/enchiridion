#!/usr/bin/env bats
# Covers wiki-plugin/bin/enchiridion's post-ADR-0017 shim behaviour (issue
# #254): by default it execs `node` against the sibling enchiridion-ts
# bundle, forwarding every argument; ENCHIRIDION_BIN overrides that
# entirely, for local dev against unbundled source or an alternate
# runtime. This replaces the pre-#254 lazy-fetch/PATH-preference suite,
# which tested logic that no longer exists (ADR-0013 is retired).

setup() {
    SCRIPT="$BATS_TEST_DIRNAME/../bin/enchiridion"

    # Recreate the monorepo layout the shim assumes: wiki-plugin/ and
    # enchiridion-ts/ as siblings, with a stub dist/cli.js standing in for
    # the real esbuild bundle.
    REPO_ROOT="$BATS_TEST_TMPDIR/repo"
    PLUGIN_ROOT="$REPO_ROOT/wiki-plugin"
    mkdir -p "$PLUGIN_ROOT/bin"
    cp "$SCRIPT" "$PLUGIN_ROOT/bin/enchiridion"
    chmod +x "$PLUGIN_ROOT/bin/enchiridion"

    mkdir -p "$REPO_ROOT/enchiridion-ts/dist"
    cat > "$REPO_ROOT/enchiridion-ts/dist/cli.js" <<'EOF'
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

@test "default: execs node against the sibling enchiridion-ts bundle, args forwarded" {
    PATH="$STUB_BIN_DIR:$PATH" run "$PLUGIN_ROOT/bin/enchiridion" search foo --json

    [ "$status" -eq 0 ]
    [[ "$output" == *"node invoked with:"* ]]
    [[ "$output" == *"enchiridion-ts/dist/cli.js search foo --json"* ]]
}

@test "no arguments: still execs node against the bundle" {
    PATH="$STUB_BIN_DIR:$PATH" run "$PLUGIN_ROOT/bin/enchiridion"

    [ "$status" -eq 0 ]
    [[ "$output" == *"enchiridion-ts/dist/cli.js"* ]]
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

@test "ENCHIRIDION_BIN wins even when node is on PATH" {
    cat > "$BATS_TEST_TMPDIR/dev-binary" <<'EOF'
#!/bin/sh
echo "dev-binary invoked: $*"
EOF
    chmod +x "$BATS_TEST_TMPDIR/dev-binary"

    PATH="$STUB_BIN_DIR:$PATH" ENCHIRIDION_BIN="$BATS_TEST_TMPDIR/dev-binary" run "$PLUGIN_ROOT/bin/enchiridion" search foo

    [ "$status" -eq 0 ]
    [[ "$output" == *"dev-binary invoked: search foo"* ]]
    [[ "$output" != *"node invoked"* ]]
}
