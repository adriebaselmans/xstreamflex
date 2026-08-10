#!/usr/bin/env bash
# Xtream Codes provider diagnostics.
#
# Reports what a panel accepts and refuses: whether get.php works, whether a
# User-Agent is mandatory, which stream extensions are allowed, and what the
# account's connection limit is. Writes a log with the username and password
# masked, so it is safe to attach to a bug report.
#
# Usage:
#   IPTV_BASE=http://host:8080 IPTV_USER=name ./iptv-diag.sh
#   ./iptv-diag.sh                     # prompts for anything not set
set -u

BASE="${IPTV_BASE:-}"
USER="${IPTV_USER:-}"
UA="${IPTV_UA:-Mozilla/5.0 (Linux; Android 12) TiviMate/4.7.0}"
STREAM_ID="${IPTV_STREAM_ID:-}"
OUT="${IPTV_OUT:-$PWD/iptv-diag-$(date +%Y%m%d-%H%M%S).log}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

[ -n "$BASE" ] || read -rp 'Server URL (http://host:port): ' BASE
[ -n "$USER" ] || read -rp 'Username: ' USER
read -rsp 'Password: ' PASS; echo
[ -n "$STREAM_ID" ] || read -rp 'A stream id to test (blank to auto-pick): ' STREAM_ID

# mask the password and username in everything on its way to the log
scrub() { sed -e "s/$PASS/***/g" -e "s/$USER/USER/g"; }
log() { printf '%s\n' "$*" | scrub | tee -a "$OUT"; }
hdr() { log ""; log "===== $* ====="; }

api() { printf '%s/player_api.php?username=%s&password=%s%s' "$BASE" "$USER" "$PASS" "$1"; }

W='status=%{http_code} bytes=%{size_download} time=%{time_total}s speed=%{speed_download}B/s redirect=%{redirect_url}\n'

log "IPTV diagnostics $(date -Is)"
log "host: $BASE"

hdr "1. DNS + TCP"
HOST=$(printf '%s' "$BASE" | sed -E 's#https?://##; s#:.*##')
PORT=$(printf '%s' "$BASE" | sed -E 's#.*:([0-9]+).*#\1#')
log "$(getent hosts "$HOST" 2>&1 || echo 'DNS FAILS')"
if timeout 5 bash -c "exec 3<>/dev/tcp/$HOST/$PORT" 2>/dev/null; then
  log "TCP $HOST:$PORT open"
else
  log "TCP $HOST:$PORT CLOSED or slow"
fi

hdr "2. Account (player_api with no action)"
curl -s -m 20 -A "$UA" "$(api '')" -o "$TMP/acct.json" \
  -w "$W" 2>&1 | scrub | tee -a "$OUT"
if command -v python3 >/dev/null; then
  python3 - "$TMP/acct.json" <<'PY' 2>&1 | scrub | tee -a "$OUT"
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception as e: print("not valid JSON:",e); sys.exit()
u=d.get("user_info",{}); s=d.get("server_info",{})
for k in ("auth","status","exp_date","is_trial","max_connections","active_cons","allowed_output_formats"):
    print(f"  {k}: {u.get(k)}")
for k in ("url","port","https_port","server_protocol","timezone"):
    print(f"  server.{k}: {s.get(k)}")
PY
else
  head -c 600 "$TMP/acct.json" | scrub | tee -a "$OUT"
fi

hdr "3. Live categories"
curl -s -m 30 -A "$UA" "$(api '&action=get_live_categories')" -o "$TMP/cats.json" \
  -w "$W" 2>&1 | scrub | tee -a "$OUT"
CAT=$(python3 -c '
import json,sys
try: d=json.load(open("'"$TMP"'/cats.json"))
except Exception: print(""); sys.exit()
print(len(d),"categories", file=sys.stderr)
print(d[0]["category_id"] if d else "")
' 2>>"$OUT")
log "first category_id: ${CAT:-none}"

hdr "4. Channels in the first category"
if [ -n "${CAT:-}" ]; then
  curl -s -m 60 -A "$UA" "$(api "&action=get_live_streams&category_id=$CAT")" -o "$TMP/ch.json" \
    -w "$W" 2>&1 | scrub | tee -a "$OUT"
  log "channels: $(python3 -c 'import json;print(len(json.load(open("'"$TMP"'/ch.json"))))' 2>/dev/null || echo '? parse failed')"
  if [ -z "$STREAM_ID" ]; then
    STREAM_ID=$(python3 -c '
import json
try: d=json.load(open("'"$TMP"'/ch.json"))
except Exception: d=[]
print(d[0]["stream_id"] if d else "")
' 2>/dev/null)
    log "auto-picked stream id: ${STREAM_ID:-none}"
  fi
fi

hdr "5. get.php m3u_plus -- this is what IPTV Simple needs"
for Q in "type=m3u_plus" "type=m3u_plus&output=ts" "type=m3u_plus&output=m3u8"; do
  log "-- variant: $Q"
  curl -s -m 180 -A "$UA" "$BASE/get.php?username=$USER&password=$PASS&$Q" \
    -o "$TMP/pl.m3u" -w "$W" 2>&1 | scrub | tee -a "$OUT"
  log "   lines=$(wc -l < "$TMP/pl.m3u")  EXTINF=$(grep -c '^#EXTINF' "$TMP/pl.m3u" || true)"
  log "   last line: $(tail -1 "$TMP/pl.m3u" | head -c 100)"
  log "   sample URL: $(grep -m1 '^http' "$TMP/pl.m3u" | head -c 120)"
done

hdr "6. get.php WITHOUT a User-Agent"
curl -s -m 180 -H 'User-Agent:' "$BASE/get.php?username=$USER&password=$PASS&type=m3u_plus" \
  -o "$TMP/pl2.m3u" -w "$W" 2>&1 | scrub | tee -a "$OUT"
log "   lines=$(wc -l < "$TMP/pl2.m3u")  EXTINF=$(grep -c '^#EXTINF' "$TMP/pl2.m3u" || true)"

hdr "7. XMLTV EPG"
curl -s -m 300 -A "$UA" "$BASE/xmltv.php?username=$USER&password=$PASS" \
  -o "$TMP/epg.xml" -w "$W" 2>&1 | scrub | tee -a "$OUT"
log "   programmes=$(grep -c '<programme' "$TMP/epg.xml" 2>/dev/null || echo 0)"
log "   closes with </tv>: $(tail -c 200 "$TMP/epg.xml" | grep -q '</tv>' && echo yes || echo NO-TRUNCATED)"

hdr "8. Short EPG through the API (alternative)"
curl -s -m 30 -A "$UA" "$(api "&action=get_short_epg&stream_id=$STREAM_ID&limit=2")" \
  -o "$TMP/sepg.json" -w "$W" 2>&1 | scrub | tee -a "$OUT"
head -c 400 "$TMP/sepg.json" | scrub | tee -a "$OUT"; echo | tee -a "$OUT"

hdr "9. Fetching a stream"
for EXT in ts m3u8 ""; do
  URL="$BASE/live/$USER/$PASS/$STREAM_ID${EXT:+.$EXT}"
  log "-- ext: ${EXT:-none}"
  curl -s -o /dev/null -m 15 -A "$UA" -L \
    -w "   status=%{http_code} type=%{content_type} bytes=%{size_download} finalurl=%{url_effective}\n" \
    "$URL" 2>&1 | scrub | tee -a "$OUT"
done

hdr "10. Stream WITHOUT a User-Agent"
curl -s -o /dev/null -m 15 -H 'User-Agent:' -L \
  -w "   status=%{http_code} type=%{content_type} bytes=%{size_download}\n" \
  "$BASE/live/$USER/$PASS/$STREAM_ID.ts" 2>&1 | scrub | tee -a "$OUT"

hdr "11. Two concurrent streams (max_connections test)"
curl -s -o /dev/null -m 12 -A "$UA" -w "   A: status=%{http_code} bytes=%{size_download}\n" \
  "$BASE/live/$USER/$PASS/$STREAM_ID.ts" 2>&1 | scrub >> "$OUT" &
sleep 2
curl -s -o /dev/null -m 12 -A "$UA" -w "   B: status=%{http_code} bytes=%{size_download}\n" \
  "$BASE/live/$USER/$PASS/$STREAM_ID.ts" 2>&1 | scrub >> "$OUT" &
wait
tail -2 "$OUT"

log ""
log "===== done ====="
log "log file: $OUT"
echo
echo "The password and username are masked in $OUT -- safe to share."
