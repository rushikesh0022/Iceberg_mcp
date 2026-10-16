#!/usr/bin/env bash
# Local ClickHouse for IceGuard: install | start | stop | status | client [args...]
set -euo pipefail

VERSION="26.8.29.10-lts"
# SHA-256 of the downloaded file. The binary decompresses itself in place on its
# first run (about 177 MB -> 890 MB), so only the download can be checked.
SHA256="68c16140d1bf3b84686cbf64a83f30574f926a3c6970b14e22620e311b35ca26"  # clickhouse-macos-aarch64
URL="https://github.com/ClickHouse/ClickHouse/releases/download/v${VERSION}/clickhouse-macos-aarch64"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/.clickhouse"
BIN="$DIR/clickhouse"
PID="$DIR/clickhouse.pid"
STAMP="$DIR/VERSION"  # written after a verified install

running() { [[ -f "$PID" ]] && kill -0 "$(cat "$PID")" 2>/dev/null; }

case "${1:-}" in
  install)
    if [[ "$(uname -sm)" != "Darwin arm64" ]]; then
      echo "This script installs the macOS arm64 build only." >&2; exit 1
    fi
    mkdir -p "$DIR"
    if [[ -x "$BIN" && "$(cat "$STAMP" 2>/dev/null)" == "$VERSION" ]]; then
      echo "ClickHouse $VERSION already installed."; exit 0
    fi
    echo "Downloading ClickHouse $VERSION (about 177 MB)..."
    curl -fL --progress-bar -o "$BIN.download" "$URL"
    echo "$SHA256  $BIN.download" | shasum -a 256 -c --status \
      || { rm -f "$BIN.download"; echo "Checksum mismatch; download removed." >&2; exit 1; }
    chmod +x "$BIN.download" && mv "$BIN.download" "$BIN"
    "$BIN" --version  # first run: decompresses the binary
    echo "$VERSION" > "$STAMP"
    ;;
  start)
    running && { echo "Already running (pid $(cat "$PID"))."; exit 0; }
    [[ -x "$BIN" ]] || { echo "Run: $0 install" >&2; exit 1; }
    mkdir -p "$DIR/logs"
    cd "$DIR"
    nohup "$BIN" server --config-file="$ROOT/clickhouse/config.xml" --pid-file="$PID" \
      >"$DIR/logs/stdout.log" 2>&1 &
    for _ in $(seq 1 60); do
      curl -fs http://127.0.0.1:8123/ping >/dev/null 2>&1 && { echo "ClickHouse is up."; exit 0; }
      sleep 0.5
    done
    echo "ClickHouse did not start; see $DIR/logs/" >&2; exit 1
    ;;
  stop)
    running || { echo "Not running."; rm -f "$PID"; exit 0; }
    kill "$(cat "$PID")"
    for _ in $(seq 1 40); do running || { echo "Stopped."; exit 0; }; sleep 0.5; done
    echo "Still stopping; pid $(cat "$PID")." >&2; exit 1
    ;;
  status)
    if running; then echo "Running (pid $(cat "$PID"))."; else echo "Not running."; fi
    ;;
  client)
    shift
    exec "$BIN" client --host 127.0.0.1 --port 9000 "$@"
    ;;
  *)
    echo "Usage: $0 install | start | stop | status | client [args...]" >&2; exit 2
    ;;
esac
