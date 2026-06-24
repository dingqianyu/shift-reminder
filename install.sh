#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(pwd)}"
APP_PASSWORD="${APP_PASSWORD:-change-me}"

cd "$APP_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：未检测到 docker，请先安装 Docker。"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "错误：未检测到 docker compose，请先安装 Docker Compose 插件。"
  exit 1
fi

if [ ! -f .env ]; then
  printf 'APP_PASSWORD=%s\n' "$APP_PASSWORD" > .env
fi

mkdir -p data
docker compose up -d --build

echo
echo "共享排班已启动。"
echo "访问地址：http://服务器IP:18080"
echo "如果团队密码还是 change-me，请编辑 .env 后执行：docker compose up -d"
