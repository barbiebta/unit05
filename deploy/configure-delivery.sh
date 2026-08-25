#!/bin/bash
set -eo pipefail

editor_host="${1:-100.76.35.4}"
editor_user="${2:-Milo}"
editor_dir="${3:-/D:/incomingfrom05}"
env_file="${WORKSPACE:-/workspace}/.env"

touch "${env_file}"
for key in \
    UNIT05_OUTPUT_LOCAL_DIR \
    UNIT05_EDITOR_TAILSCALE_HOST \
    UNIT05_OUTPUT_SFTP_HOST \
    UNIT05_OUTPUT_SFTP_PORT \
    UNIT05_OUTPUT_SFTP_USER \
    UNIT05_OUTPUT_SFTP_KEY \
    UNIT05_OUTPUT_SFTP_DIR
do
    sed -i "/^${key}=/d" "${env_file}"
done

{
    echo "UNIT05_EDITOR_TAILSCALE_HOST=${editor_host}"
    echo "UNIT05_OUTPUT_SFTP_HOST=127.0.0.1"
    echo "UNIT05_OUTPUT_SFTP_PORT=10022"
    echo "UNIT05_OUTPUT_SFTP_USER=${editor_user}"
    echo "UNIT05_OUTPUT_SFTP_KEY=/workspace/secrets/unit05-output-ed25519"
    echo "UNIT05_OUTPUT_SFTP_DIR=${editor_dir}"
} >> "${env_file}"
