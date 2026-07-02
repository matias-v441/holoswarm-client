cd "$(dirname "$0")"
xhost +local:docker

docker compose \
 -f compose.yaml \
 --env-file ./stack.env \
 up -d
