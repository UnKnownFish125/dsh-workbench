#!/bin/bash
# 测试机部署（仅测试机 3091/6271 —— 两阶段铁律第 1 步，不得触碰生产 3081/6270）
# 部署内容：worktree 服务 / preset 插件 / web client 插件 bundle
# 用法: bash tools/deploy-test.sh [--from-dev]   (默认从工作区源码部署)
set -e

# ── 源（新仓库工作区）──
SRC="/www/deepseek harness workspace/dsh-workbench"
WORKTREE_SRV=/www/dsh-test-workbench            # python 服务（6271）
PRESET_DST=/www/dsh-test-home/.agent-presets/_worktree-plugin
TEST_PROFILE=/www/dsh-test-home/profiles/web
TEST_PKG="$TEST_PROFILE/package.json"
PKG_DIR=/www/dsh-packages
PORT_TEST=6271

echo "=== ① 部署 worktree 服务 → $WORKTREE_SRV (端口 $PORT_TEST) ==="
mkdir -p "$WORKTREE_SRV/data"
cp "$SRC/worktree-server/server.py" "$SRC/worktree-server/worktree.py" "$WORKTREE_SRV/"
# token 文件（回环+token 鉴权；测试用）
if [ ! -f "$WORKTREE_SRV/data/api-token" ]; then
  openssl rand -hex 24 > "$WORKTREE_SRV/data/api-token" 2>/dev/null || head -c 48 /dev/urandom | xxd -p -c 48 > "$WORKTREE_SRV/data/api-token"
  chmod 600 "$WORKTREE_SRV/data/api-token"
fi
# host web 插件经 /worktree-api 代理注入同一 token → 同步到 $DSH_HOME
cp "$WORKTREE_SRV/data/api-token" /www/dsh-test-home/.dsh-workbench-api-token
chmod 600 /www/dsh-test-home/.dsh-workbench-api-token
echo "  ✓ token 同步到 /www/dsh-test-home/.dsh-workbench-api-token"
cat > /etc/systemd/system/dsh-test-workbench.service <<UNIT
[Unit]
Description=DSH test workbench server (worktree, isolated $PORT_TEST)
After=network.target

[Service]
Type=simple
WorkingDirectory=$WORKTREE_SRV
Environment=HOME=/root
Environment=WORKBENCH_SERVER_PORT=$PORT_TEST
Environment=WORKBENCH_DATA_DIR=$WORKTREE_SRV/data
ExecStart=/opt/AstrBot/venv/bin/python3 $WORKTREE_SRV/server.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now dsh-test-workbench.service >/dev/null 2>&1 || true
systemctl restart dsh-test-workbench.service
sleep 2
echo "  ✓ active: $(systemctl is-active dsh-test-workbench.service) | health: $(curl -s -m 5 http://127.0.0.1:$PORT_TEST/v1/health)"

echo "=== ② 部署 preset 插件 → $PRESET_DST ==="
mkdir -p "$PRESET_DST"
cp "$SRC/agent-preset/_worktree-plugin/plugin-v1.js" "$PRESET_DST/"
echo "  ✓ plugin-v1.js: $([ -f $PRESET_DST/plugin-v1.js ] && echo ok)"
# 把 worktree 行加到 harness-memory-task preset（若未加则追加）
TASK_CFG=/www/dsh-test-home/.agent-presets/harness-memory-task/agent.cordis.yml
if ! grep -q "id: worktree" "$TASK_CFG"; then
  # 在 literature-kb 行后追加
  python3 - "$TASK_CFG" <<'PY'
import sys
p=sys.argv[1]
s=open(p,encoding="utf-8").read()
anchor="  name: '../_literature-kb-plugin/plugin-v1.js'\n"
add=anchor+"\n# 非线性工作台：结构树/契约/会话继承注入（[WORKTREE] 段 order:46）\n- id: worktree\n  name: '../_worktree-plugin/plugin-v1.js'\n"
if "id: worktree" not in s:
    s=s.replace(anchor, add, 1)
    open(p,"w",encoding="utf-8").write(s)
    print("  ✓ added worktree to harness-memory-task/agent.cordis.yml")
else:
    print("  ✓ worktree already present")
PY
else
  echo "  ✓ worktree already present"
fi

echo "=== ③ 部署 web client 插件 bundle → $PKG_DIR + test profile ==="
# 打包 web-plugin 为 tgz（dsh-workbench）；顶层用 npm pack 惯例 `package/`
cd "$SRC/web-plugin"
tar -czf /tmp/dsh-workbench-0.1.0.tgz --transform 's,^,package/,' package.json index.js client.js dsh.patch.yml 2>/dev/null \
  || tar -czf /tmp/dsh-workbench-0.1.0.tgz --transform 's,^,package/,' -C "$SRC/web-plugin" .
cp /tmp/dsh-workbench-0.1.0.tgz "$PKG_DIR/dsh-workbench-0.1.0.tgz"
echo "  ✓ tgz 打包: $PKG_DIR/dsh-workbench-0.1.0.tgz ($(du -h $PKG_DIR/dsh-workbench-0.1.0.tgz | cut -f1), top: $(tar -tzf $PKG_DIR/dsh-workbench-0.1.0.tgz | head -1))"
# 加入 test profile deps + bundles（若未加）
python3 - "$TEST_PKG" <<'PY'
import json,sys,os
p=sys.argv[1]
d=json.load(open(p,encoding="utf-8"))
deps=d.setdefault("dependencies",{})
if "dsh-workbench" not in deps:
    deps["dsh-workbench"]="file:/www/dsh-packages/dsh-workbench-0.1.0.tgz"
b=d.setdefault("dsh",{}).setdefault("profile",{}).setdefault("bundles",[])
if "dsh-workbench" not in b: b.append("dsh-workbench")
json.dump(d,open(p,"w",encoding="utf-8"),ensure_ascii=False,indent=2)
print("  ✓ deps+bundles updated")
PY
cd "$TEST_PROFILE" && pnpm install --offline=false >/tmp/wb-install.log 2>&1 || echo "  ⚠ install 见 /tmp/wb-install.log"
grep -qiE "done|added" /tmp/wb-install.log && echo "  ✓ pnpm install done" || echo "  ⚠ 检查 /tmp/wb-install.log"

echo "=== ④ 重启测试机 dsh-test.service (3091) ==="
systemctl restart dsh-test.service
sleep 8
echo "  ✓ active: $(systemctl is-active dsh-test.service) | 3091: $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3091/)"

echo "=== 部署完成（仅测试机）==="
