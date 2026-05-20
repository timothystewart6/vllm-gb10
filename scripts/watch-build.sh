#!/bin/bash
# Watch the Docker build on Spark. Run locally on Mac.
# Usage: bash scripts/watch-build.sh [interval_seconds]
INTERVAL=${1:-30}
SSH="ssh -i ~/.ssh/automation_ed25519 automation@asus-gx10-1.local.techtronic.us"

while true; do
    clear
    echo "=========================================="
    echo " vllm-gb10 build monitor  (every ${INTERVAL}s)"
    echo " $(date)"
    echo "=========================================="

    $SSH '
        BUILD_PIDS=$(pgrep -f "docker buildx" | grep -v pgrep)
        if [ -z "$BUILD_PIDS" ]; then
            echo "STATUS: DONE / not running"
        else
            echo "STATUS: RUNNING  (pids: $(echo $BUILD_PIDS | tr "\n" " "))"
        fi

        echo ""
        echo "--- active nvcc jobs (inside runc container) ---"
        RUNC_PID=$(pgrep -f "runc.*buildkit" | grep -v grep | head -1)
        if [ -n "$RUNC_PID" ]; then
            NVCC_COUNT=$(pstree -p "$RUNC_PID" 2>/dev/null | grep -c nvcc || echo 0)
            echo "  runc PID=$RUNC_PID  nvcc count=$NVCC_COUNT"
            pstree -p "$RUNC_PID" 2>/dev/null | grep -oP "\w+\(\d+\)" | grep -v runc | grep -v sh | sort -u | head -10 | sed "s/^/  /"
        else
            echo "  (no runc container running)"
        fi

        echo ""
        echo "--- build log (last 15 lines) ---"
        tail -15 /tmp/docker-build.log 2>/dev/null || echo "  (no log)"

        echo ""
        echo "--- monitor snapshots ---"
        cat /tmp/build-monitor.log 2>/dev/null | tail -5 || echo "  (no monitor log)"
    '

    # Check if build finished and exit
    STILL_RUNNING=$($SSH 'pgrep -f "docker buildx" | grep -v pgrep | wc -l' 2>/dev/null)
    if [ "$STILL_RUNNING" = "0" ]; then
        echo ""
        echo "=========================================="
        echo " BUILD FINISHED (or died). Exiting watch."
        echo "=========================================="
        break
    fi

    echo ""
    echo "(refreshing in ${INTERVAL}s — Ctrl-C to stop)"
    sleep "$INTERVAL"
done
