#!/bin/sh
# TableForge container entrypoint.
#
#   serve  - coordinator: create TLS material, write a shared enrollment token, serve.
#   work   - worker: wait for the coordinator, enroll once (credentials persist in /data),
#            then run the remote worker loop.
#   *      - any other arguments are passed to the `tableforge` CLI unchanged.
set -eu

DATA_DIR="${TABLEFORGE_DATA_DIR:-/data}"
SHARED_DIR="${TABLEFORGE_SHARED_DIR:-/shared}"
TOKEN_FILE="${TABLEFORGE_ENROLLMENT_TOKEN_FILE:-${SHARED_DIR}/enrollment-token}"
CA_FILE="${TABLEFORGE_CA_FILE:-${SHARED_DIR}/server.crt}"

tf() {
    tableforge --data-dir "${DATA_DIR}" "$@"
}

log() {
    printf '{"component":"entrypoint","event":"%s","detail":"%s"}\n' "$1" "${2:-}"
}

serve() {
    hosts="${TABLEFORGE_TLS_HOSTS:-coordinator}"
    set --
    for host in $(printf '%s' "${hosts}" | tr ',' ' '); do
        set -- "$@" --host "${host}"
    done
    if ! tf coordinator init "$@" >/dev/null 2>&1; then
        log certificate_regenerated "TABLEFORGE_TLS_HOSTS changed; enrolled nodes need a new invite"
        tf coordinator init --regenerate "$@" >/dev/null
    fi
    tf metadata migrate-schema >/dev/null
    if [ -d "${SHARED_DIR}" ] && [ -w "${SHARED_DIR}" ]; then
        cp "${DATA_DIR}/tls/server.crt" "${CA_FILE}.tmp"
        mv "${CA_FILE}.tmp" "${CA_FILE}"
        if [ "${TABLEFORGE_ENROLLMENT_MAX_USES:-0}" -gt 0 ]; then
            tf coordinator enrollment-token \
                --ttl-minutes "${TABLEFORGE_ENROLLMENT_TTL_MINUTES:-1440}" \
                --max-uses "${TABLEFORGE_ENROLLMENT_MAX_USES}" \
                --output "${TOKEN_FILE}" >/dev/null
            log enrollment_token_written "${TOKEN_FILE}"
        fi
    fi
    exec tableforge --data-dir "${DATA_DIR}" coordinator serve \
        --listen "${TABLEFORGE_COORDINATOR__LISTEN:-0.0.0.0:7443}"
}

work() {
    target="${TABLEFORGE_COORDINATOR_ADDRESS:?set TABLEFORGE_COORDINATOR_ADDRESS, e.g. coordinator:7443}"
    if [ ! -f "${DATA_DIR}/coordinator.json" ]; then
        waited=0
        while [ ! -s "${TOKEN_FILE}" ] || [ ! -s "${CA_FILE}" ]; do
            if [ "${waited}" -ge "${TABLEFORGE_ENROLL_TIMEOUT_SECONDS:-180}" ]; then
                log enroll_timeout "missing ${TOKEN_FILE} or ${CA_FILE}"
                exit 1
            fi
            sleep 2
            waited=$((waited + 2))
        done
        attempts=0
        until tf node enroll --coordinator "${target}" \
                --token-file "${TOKEN_FILE}" --ca-file "${CA_FILE}"; do
            attempts=$((attempts + 1))
            if [ "${attempts}" -ge 30 ]; then
                log enroll_failed "${target}"
                exit 1
            fi
            sleep 2
        done
    fi
    exec tableforge --data-dir "${DATA_DIR}" worker start --remote
}

case "${1:-}" in
    serve) shift; serve "$@" ;;
    work) shift; work "$@" ;;
    *) exec tableforge --data-dir "${DATA_DIR}" "$@" ;;
esac
