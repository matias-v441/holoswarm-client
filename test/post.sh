#!/usr/bin/env bash
ENDPOINT=${1:-mission}
JSON_PATH=${2:-"$(dirname "$0")/json/missions/one_drone"}
SRV=localhost:8080
REQ="$(cat "$2")"
URL="$SRV/$ENDPOINT"
echo "POST $URL $REQ"
curl -X POST -d "$REQ" "$URL"
echo ""