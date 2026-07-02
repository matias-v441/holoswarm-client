#!/usr/bin/env bash
ENDPOINT=${1:-robots}
SRV=localhost:8080
URL="$SRV/$ENDPOINT"
echo "GET $URL"
curl -i $URL
echo ""
