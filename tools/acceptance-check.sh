#!/bin/bash
# dsh-workbench 验收检查（plan §9）—— 针对已部署的测试机 worktree 服务（6271）。
# 前置：tools/seed-demo.py 已把 deepmemory 三层样例写入服务。
# 用法: bash tools/acceptance-check.sh [BASE_URL] [TOKEN]
set -u
BASE="${1:-http://127.0.0.1:6271}"
SCRIPT_DIR="$(dirname "$0")"
TOKEN_FILE="${2:-}"
AUTH=()
if [ -n "$TOKEN_FILE" ] && [ -f "$TOKEN_FILE" ]; then
  AUTH=(-H "Authorization: Bearer $(cat "$TOKEN_FILE")")
fi
pass=0; fail=0
ok(){ echo "  ✓ $1"; pass=$((pass+1)); }
bad(){ echo "  ✗ $1"; fail=$((fail+1)); }
jp(){ python3 -c "import sys,json;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

echo "=== [1] 健康 ==="
H=$(curl -s -m 5 "${AUTH[@]}" "$BASE/v1/health" | jp "d.get('ok')")
[ "$H" = "True" ] && ok "health ok" || bad "health: $H"

echo "=== [2] 结构树（deepmemory 三层样例）==="
ROOT=$(curl -s -m 5 "${AUTH[@]}" "$BASE/v1/worktree/tree?root=deepmemory" | jp "d.get('root',{}).get('path')")
[ "$ROOT" = "deepmemory" ] && ok "tree root=deepmemory" || bad "tree root: '$ROOT'"
CH=$(curl -s -m 5 "${AUTH[@]}" "$BASE/v1/worktree/tree?root=deepmemory&depth=2" | jp "len(d.get('children',[]))")
[ "${CH:-0}" -ge 1 ] && ok "tree has children ($CH)" || bad "tree children: $CH"

echo "=== [3] 祖先链（root→node）==="
ANC=$(curl -s -m 5 "${AUTH[@]}" "$BASE/v1/worktree/ancestors/deepmemory/%E4%B8%BB%E4%BD%93/memory-server" | jp "len(d.get('ancestors',[]))")
FIRST=$(curl -s -m 5 "${AUTH[@]}" "$BASE/v1/worktree/ancestors/deepmemory/%E4%B8%BB%E4%BD%93/memory-server" | jp "d.get('ancestors',[{}])[0].get('path')")
LAST=$(curl -s -m 5 "${AUTH[@]}" "$BASE/v1/worktree/ancestors/deepmemory/%E4%B8%BB%E4%BD%93/memory-server" | jp "d.get('ancestors',[{}])[-1].get('path')")
[ "$FIRST" = "deepmemory" ] && [ "$LAST" = "deepmemory/主体/memory-server" ] && ok "ancestors root->node ($ANC)" || bad "ancestors first=$FIRST last=$LAST"

echo "=== [4] 契约 ==="
CR=$(curl -s -m 5 "${AUTH[@]}" "$BASE/v1/worktree/contract/deepmemory/%E4%B8%BB%E4%BD%93/memory-server" | jp "d.get('contract_ref')")
[ -n "$CR" ] && [ "$CR" != "None" ] && ok "contract_ref=$CR" || bad "contract_ref: '$CR'"

echo "=== [5] 会话挂载 + session 解析 ==="
SID="acceptance-test-$$"
curl -s -m 5 -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
  -d "{\"node_path\":\"deepmemory/主体/memory-server\",\"session_id\":\"$SID\"}" "$BASE/v1/worktree/session-link" >/dev/null
SR=$(curl -s -m 5 "${AUTH[@]}" "$BASE/v1/worktree/session/$SID" | jp "d.get('node_path')")
[ "$SR" = "deepmemory/主体/memory-server" ] && ok "session->node resolves" || bad "session node: '$SR'"
curl -s -m 5 -X DELETE "${AUTH[@]}" -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\"}" "$BASE/v1/worktree/session-link" >/dev/null
echo "  (清理 test session link)"

echo ""
echo "=== 结果: PASS=$pass FAIL=$fail ==="
[ "$fail" = "0" ] && echo "全部通过 ✅" || echo "存在失败 ❌"
exit "$fail"