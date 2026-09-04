#!/usr/bin/env bash
#
# dsh-workbench repo-level verification gate.
#
# Verifies, in order:
#   1. python syntax (py_compile) : worktree-server/*.py  +  tools/*.py
#   2. node --check                : agent-preset/_worktree-plugin/plugin-v1.js
#   3. node --check                : web-plugin/index.js, web-plugin/client.js
#   4. ESM load smoke              : the P1 preset plugin (import must not throw)
#   5. P0 test suite               : worktree-server/tests/test_worktree.py
#
# Prints PASS / SKIP / FAIL per step and exits non-zero on the first FAIL.
#
# SKIP semantics: an input artifact is *not present* in this checkout because a
# sibling module that owns it (P0 service code, P1 plugin JS, P2 web plugin) has
# not been landed yet. A missing file is NOT a defect in anything being verified
# here, so SKIP does not fail the run. FAIL = a present artifact genuinely broke
# its check. This keeps the gate useful now and turns fully active once all
# modules land in the same repo.
#
# Run from the repo root:  bash tools/verify.sh

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

# Pick the project python (stdlib-check only; must be present on the test box).
PY="${PY:-/opt/AstrBot/venv/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

NODE="${NODE:-node}"

OVERALL_FAIL=0

pass() { printf 'PASS  %s\n' "$1"; }
skip() { printf 'SKIP  %s\n' "$1"; }
fail() {
    printf 'FAIL  %s\n' "$1"
    OVERALL_FAIL=1
}

# has <glob...>  -> 0 if at least one matching file exists, else 1
has() {
    local pat
    for pat in "$@"; do
        if compgen -G "$pat" >/dev/null; then return 0; fi
    done
    return 1
}

echo "=== dsh-workbench verify ==="
echo "repo: $REPO"
echo "python: $PY"
echo "node: $NODE"
echo

# ---------------------------------------------------------------------
# 1. python syntax on worktree-server/*.py  and  tools/*.py
# ---------------------------------------------------------------------
echo "--- step 1: python syntax (py_compile) ---"
if ! has worktree-server/*.py tools/*.py; then
    skip "no python sources under worktree-server/ or tools/ (P0/P3 not landed)"
else
    # shellcheck disable=SC2086
    if "$PY" -m py_compile worktree-server/*.py tools/*.py 2>/tmp/verify_py.err; then
        pass "py_compile worktree-server/*.py tools/*.py"
    else
        fail "py_compile worktree-server/*.py tools/*.py"
        cat /tmp/verify_py.err
    fi
fi
echo

# ---------------------------------------------------------------------
# 2. node --check : P1 preset plugin
# ---------------------------------------------------------------------
echo "--- step 2: node --check plugin-v1.js ---"
PLUGIN="agent-preset/_worktree-plugin/plugin-v1.js"
if [ -f "$PLUGIN" ]; then
    if "$NODE" --check "$PLUGIN"; then
        pass "node --check $PLUGIN"
    else
        fail "node --check $PLUGIN"
    fi
else
    skip "$PLUGIN not present (P1 owns it)"
fi
echo

# ---------------------------------------------------------------------
# 3. node --check : web plugin
# ---------------------------------------------------------------------
echo "--- step 3: node --check web-plugin ---"
ok=1
for f in web-plugin/index.js web-plugin/client.js; do
    if [ -f "$f" ]; then
        if ! "$NODE" --check "$f"; then
            fail "node --check $f"
            ok=0
        fi
    else
        skip "$f not present (P2 owns it)"
    fi
done
if [ "$ok" -eq 1 ]; then
    pass "node --check web-plugin/index.js web-plugin/client.js"
fi
echo

# ---------------------------------------------------------------------
# 4. ESM load smoke on the P1 preset plugin
# ---------------------------------------------------------------------
echo "--- step 4: ESM load smoke (must import without throwing) ---"
if [ -f "$PLUGIN" ]; then
    PLUGIN_ABS="$REPO/$PLUGIN"
    if "$NODE" --input-type=module -e "await import('$PLUGIN_ABS')" 2>/tmp/verify_esm.err; then
        pass "ESM import smoke $PLUGIN"
    else
        fail "ESM import smoke $PLUGIN"
        cat /tmp/verify_esm.err
    fi
else
    skip "$PLUGIN not present (P1 owns it)"
fi
echo

# ---------------------------------------------------------------------
# 5. P0 test suite
# ---------------------------------------------------------------------
echo "--- step 5: worktree-server test suite ---"
TEST="worktree-server/tests/test_worktree.py"
if [ -f "$TEST" ]; then
    if "$PY" -m unittest discover -s worktree-server/tests -p 'test_*.py' -v \
            2>/tmp/verify_test.err; then
        pass "test_worktree.py (unittest discover)"
    else
        fail "test_worktree.py"
        cat /tmp/verify_test.err
    fi
else
    skip "$TEST not present (P0 owns it)"
fi
echo

# ---------------------------------------------------------------------
echo
if [ "$OVERALL_FAIL" -eq 0 ]; then
    echo "verify: PASS"
    exit 0
else
    echo "verify: FAIL"
    exit 1
fi
