#!/usr/bin/env bash
# 通用一鍵跑（可重複執行 = 自動續跑）。用法：
#   ./run_canonical.sh canonical_mini.yaml
#   ./run_canonical.sh canonical_gemma.yaml --kg-dbs db1,db2,db3,db4,db5,db6
set -u
cd "$(dirname "$0")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
CFG="${1:-canonical_mini.yaml}"; shift || true
MAX=30; N=0
while true; do
  python runners/run_canonical.py --config "$CFG" "$@" && { echo "[wrapper] 全部完成。"; exit 0; }
  N=$((N+1)); [ "$N" -ge "$MAX" ] && { echo "[wrapper] 重試 $MAX 次仍失敗，請看 RUN_STATE.md。"; exit 1; }
  echo "[wrapper] 中斷，60 秒後自動續跑（第 $N/$MAX 次）..."; sleep 60
done
