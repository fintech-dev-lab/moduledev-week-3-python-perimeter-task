#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir=""
forwarded=()

usage() {
  printf 'Usage: %s --repo PATH [--keep-stack]\n' "$0"
}

while (($#)); do
  case "$1" in
    --repo)
      if (($# < 2)); then
        printf 'Option --repo requires a path.\n' >&2
        exit 2
      fi
      repo_dir="$2"
      shift 2
      ;;
    --repo=*)
      repo_dir="${1#--repo=}"
      shift
      ;;
    --fixtures|--fixtures=*|--output|--output=*|--compose-wrapper|--compose-wrapper=*)
      printf 'Option %s is reserved by the public checker.\n' "$1" >&2
      exit 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      forwarded+=("$1")
      shift
      ;;
  esac
done

if [[ -z "$repo_dir" ]]; then
  usage >&2
  exit 2
fi
if [[ ! -d "$repo_dir" ]]; then
  printf 'Candidate repository does not exist: %s\n' "$repo_dir" >&2
  exit 2
fi
repo_dir="$(cd -- "$repo_dir" && pwd -P)"

exec python3 "$script_dir/public_check.py" \
  "${forwarded[@]}" \
  --repo "$repo_dir" \
  --fixtures "$script_dir/fixtures" \
  --output "$repo_dir/week-3-public-report.json" \
  --compose-wrapper "$script_dir/safe_compose.sh"
