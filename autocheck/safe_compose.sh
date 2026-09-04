#!/usr/bin/env bash
set -euo pipefail

home="${HOME:-/tmp}"
path="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
docker_config="${DOCKER_CONFIG:-$home/.docker}"
docker_host="${DOCKER_HOST:-}"
docker_context="${DOCKER_CONTEXT:-}"

exec env -i \
  PATH="$path" \
  HOME="$home" \
  DOCKER_CONFIG="$docker_config" \
  DOCKER_HOST="$docker_host" \
  DOCKER_CONTEXT="$docker_context" \
  COURSE_GATEWAY_PORT="${COURSE_GATEWAY_PORT:-8080}" \
  COURSE_TEST_PROFILE="${COURSE_TEST_PROFILE:-1}" \
  COURSE_JWT_ISSUER="${COURSE_JWT_ISSUER:-}" \
  COURSE_JWT_AUDIENCE="${COURSE_JWT_AUDIENCE:-}" \
  COURSE_JWT_SIGNING_KEY="${COURSE_JWT_SIGNING_KEY:-}" \
  PROVIDER_CALLBACK_CAPABILITY="${PROVIDER_CALLBACK_CAPABILITY:-}" \
  PROVIDER_CALLBACK_TOKEN="${PROVIDER_CALLBACK_TOKEN:-}" \
  PROVIDER_HMAC_SECRET="${PROVIDER_HMAC_SECRET:-}" \
  PROVIDER_AUDIT_TOKEN="${PROVIDER_AUDIT_TOKEN:-}" \
  COMPOSE_DISABLE_ENV_FILE=1 \
  docker compose "$@"
