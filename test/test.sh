#!/usr/bin/env bash

cd "$(dirname "$0")"

./post.sh mission json/missions/one_drone.json

sleep 1

./post.sh mission/start json/mission_start.sh
