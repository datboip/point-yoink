#!/usr/bin/env bash
# Record a py-spy flamegraph of the running PointYoink process. Needs sudo (ptrace_scope=1
# on this machine blocks attaching to a non-child process otherwise).
# Usage: dev/prof/flamegraph.sh [seconds] [outfile.svg]
DUR="${1:-30}"
OUT="${2:-$HOME/pointyoink/dev/prof/flame_$(date +%H%M%S).svg}"
PID=$(pgrep -f "venv/bin/python.*pointyoink\.py" | head -1)
if [ -z "$PID" ]; then echo "PointYoink isn't running."; exit 1; fi
echo "recording pid $PID for ${DUR}s -> $OUT (use the app normally while this runs)"
sudo "$HOME/pointyoink/venv/bin/py-spy" record --pid "$PID" --duration "$DUR" --output "$OUT"
echo "done: $OUT (open in a browser)"
