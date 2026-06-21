#!/usr/bin/env sh
set -eu

required_version="$1"
installed_version="$(ruff --version | awk '{print $2}')"

if [ "${installed_version}" != "${required_version}" ]; then
  printf "Error: ruff %s is required, but %s is installed\n" \
    "${required_version}" "${installed_version}" >&2
  exit 1
fi
