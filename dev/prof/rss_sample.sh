#!/usr/bin/env bash
# Sample the running PointYoink process's memory (RSS) and thread count every 3s to a CSV.
# Usage: dev/prof/rss_sample.sh [seconds] [outfile]
# Run this while using the app normally; afterward, eyeball the RSS column for steady climb
# (a leak) vs jumps that come back down (normal - a big mesh in flight, GC hasn't run yet).
DUR="${1:-300}"
OUT="${2:-$HOME/pointyoink/dev/prof/rss_$(date +%H%M%S).csv}"
PID=$(pgrep -f "venv/bin/python.*pointyoink\.py" | head -1)
if [ -z "$PID" ]; then echo "PointYoink isn't running."; exit 1; fi
echo "sampling pid $PID for ${DUR}s -> $OUT"
echo "t_sec,rss_mb,threads,cpu_pct" > "$OUT"
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt "$DUR" ]; do
  if ! kill -0 "$PID" 2>/dev/null; then echo "process exited, stopping"; break; fi
  read -r rss threads cpu < <(ps -p "$PID" -o rss=,nlwp=,%cpu= 2>/dev/null)
  [ -z "$rss" ] && break
  echo "$(( $(date +%s) - t0 )),$(( rss / 1024 )),$threads,$cpu" >> "$OUT"
  sleep 3
done
echo "done: $OUT"
