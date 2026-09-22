#!/usr/bin/env sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yaml"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mttl-up-standalone}"
SERVICE="mttl-up"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"

cd "$SCRIPT_DIR"

# Secrets and persistent runtime state are private to the backend administrator.
if [ -f .env ]; then chmod 600 .env || true; fi
DATA_DIR=./data
if [ -f .env ]; then
    configured=$(sed -n 's/^[[:space:]]*DATA_PATH[[:space:]]*=[[:space:]]*//p' .env | tail -n 1 | sed 's/^"//;s/"$//;s/^'"'"'//;s/'"'"'$//')
    [ -n "$configured" ] && DATA_DIR=$configured
fi
case "$DATA_DIR" in /*) ;; *) DATA_DIR="$SCRIPT_DIR/$DATA_DIR" ;; esac
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR" || true

if ! command -v docker >/dev/null 2>&1; then
    echo "[ERROR] docker 명령을 찾을 수 없습니다." >&2
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "[ERROR] Docker Compose v2 ('docker compose')를 사용할 수 없습니다." >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "[ERROR] Docker daemon에 연결할 수 없습니다." >&2
    exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "[ERROR] compose 파일이 없습니다: $COMPOSE_FILE" >&2
    exit 1
fi

echo "[1/3] Build + deploy"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d --build --remove-orphans

echo "[2/3] Waiting for health (timeout: ${HEALTH_TIMEOUT}s)"
CID=$(docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps -q "$SERVICE")
if [ -z "$CID" ]; then
    echo "[ERROR] $SERVICE 컨테이너를 찾지 못했습니다." >&2
    exit 1
fi

elapsed=0
while :; do
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CID" 2>/dev/null || echo unknown)
    case "$status" in
        healthy)
            break
            ;;
        running)
            # Accept running only if the image has no healthcheck.
            has_health=$(docker inspect -f '{{if .State.Health}}yes{{else}}no{{end}}' "$CID" 2>/dev/null || echo no)
            if [ "$has_health" = "no" ]; then
                break
            fi
            ;;
        unhealthy|exited|dead)
            echo "[ERROR] container status: $status" >&2
            docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs --tail=100 "$SERVICE" >&2 || true
            exit 1
            ;;
    esac

    if [ "$elapsed" -ge "$HEALTH_TIMEOUT" ]; then
        echo "[ERROR] health check timeout (last status: $status)" >&2
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps >&2 || true
        docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs --tail=100 "$SERVICE" >&2 || true
        exit 1
    fi

    sleep 1
    elapsed=$((elapsed + 1))
done

echo "[3/3] Running"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" ps

echo "[OK] MTTL-W01 backend deployed in detached mode."
