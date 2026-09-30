#!/usr/bin/env bash
# 端到端測試 runner：啟動 server → 跑測試客戶端 → 關閉 server
set -u
cd "$(dirname "$0")"   # server/

# 清掉任何殘留 server 程序與 port
pkill -f "server.py" 2>/dev/null
sleep 1

echo "== [start] server =="
.venv/bin/python server.py > /tmp/grpc_server.log 2>&1 &
SERVER_PID=$!
sleep 2.5
if ! kill -0 $SERVER_PID 2>/dev/null; then
  echo "!! server 未能啟動"; cat /tmp/grpc_server.log; exit 1
fi

echo "== [run] client_demo =="
timeout 30 .venv/bin/python tests/client_demo.py 127.0.0.1 50051
RC=$?
echo "client RC=$RC"

kill $SERVER_PID 2>/dev/null
pkill -f "server.py" 2>/dev/null

echo "== [done] server log =="
cat /tmp/grpc_server.log
exit $RC