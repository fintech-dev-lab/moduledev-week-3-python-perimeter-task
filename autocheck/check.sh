#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$script_dir/.." && pwd)"

for argument in "$@"; do
  case "$argument" in
    --repo|--repo=*|--fixtures|--fixtures=*|--output|--output=*|--compose-wrapper|--compose-wrapper=*)
      printf 'Option %s is reserved by the public checker.\n' "$argument" >&2
      exit 2
      ;;
  esac
done

exec python3 "$script_dir/public_check.py" \
  "$@" \
  --repo "$repo_dir" \
  --fixtures "$script_dir/fixtures" \
  --output "$repo_dir/week-3-public-report.json" \
  --compose-wrapper "$script_dir/safe_compose.sh"
