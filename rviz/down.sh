cd "$(dirname "$0")"
xhost -local:docker

docker compose \
 -f compose.yaml \
 --env-file ./stack.env \
 down -v --remove-orphans --timeout 1
