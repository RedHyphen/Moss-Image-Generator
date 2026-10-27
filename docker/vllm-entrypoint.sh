#!/bin/sh
set -eu

secret_file=/run/secrets/vllm_api_key
if [ -r "$secret_file" ]; then
  VLLM_API_KEY="$(awk -F= '$1 == "VLLM_API_KEY" {print substr($0, index($0, "=") + 1); exit}' "$secret_file")"
  if [ -z "$VLLM_API_KEY" ]; then
    VLLM_API_KEY="$(cat "$secret_file")"
  fi
  export VLLM_API_KEY
fi

exec "$@"
