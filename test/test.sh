#!/usr/bin/env bash

cd "$(dirname "$0")"

./post.sh mission json/missions/two_drones.json

sleep 1

./post.sh mission/start json/mission_start.sh
