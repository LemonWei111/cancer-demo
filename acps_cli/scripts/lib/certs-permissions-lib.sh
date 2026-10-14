#!/usr/bin/env bash

_certs_perm_log() {
    if declare -F log >/dev/null 2>&1; then
        log "$@"
    else
        echo "[certs-permissions] $*"
    fi
}

_resolve_host_cert_owner() {
    printf '%s:%s\n' "$(id -u)" "$(id -g)"
}

# create_host_client_key_copy <key_file>
# 为宿主机 curl / NSS 创建一份当前用户可读的临时 client key 副本，
# 避免直接修改 bind mount 原文件属主，影响容器内进程读取。
create_host_client_key_copy() {
    local key_file="${1:?缺少 client key 路径}"
    local temp_key_file=""
    local host_owner=""

    if [[ ! -f "${key_file}" ]]; then
        return 1
    fi

    temp_key_file="$(mktemp "${TMPDIR:-/tmp}/acps-client-key.XXXXXX")"
    cp "${key_file}" "${temp_key_file}"
    chmod 600 "${temp_key_file}"

    if [[ "$(id -u)" -eq 0 ]]; then
        host_owner="$(_resolve_host_cert_owner)"
        chown "${host_owner}" "${temp_key_file}"
    fi

    printf '%s\n' "${temp_key_file}"
}

# normalize_host_client_key <key_file>
# 宿主机 curl / NSS 要求 client key 归当前安装用户所有且为 600。
normalize_host_client_key() {
    local key_file="${1:?缺少 client key 路径}"
    local host_owner=""

    if [[ ! -f "${key_file}" ]]; then
        return 0
    fi

    host_owner="$(_resolve_host_cert_owner)"

    if [[ "$(id -u)" -eq 0 ]]; then
        chown "${host_owner}" "${key_file}"
        chmod 600 "${key_file}"
        _certs_perm_log "已规范化宿主机 client key: ${key_file} (owner=${host_owner})"
        return 0
    fi

    if [[ -O "${key_file}" ]]; then
        chmod 600 "${key_file}"
        _certs_perm_log "已规范化宿主机 client key 权限: ${key_file}"
        return 0
    fi

    _certs_perm_log "警告: 无法调整宿主机 client key 属主: ${key_file}"
}

# normalize_bind_mount_host_client_keys <certs_dir> <key_name> [key_name...]
normalize_bind_mount_host_client_keys() {
    local certs_dir="${1:?缺少 certs 目录参数}"
    local key_name=""

    shift
    for key_name in "$@"; do
        [[ -n "${key_name}" ]] || continue
        normalize_host_client_key "${certs_dir}/${key_name}"
    done
}

# normalize_bind_mount_certs_dir <certs_dir> [uid] [gid]
# 默认 uid/gid=1000，对应 release-app 镜像内 appuser。
normalize_bind_mount_certs_dir() {
    local certs_dir="${1:?缺少 certs 目录参数}"
    local target_uid="${2:-1000}"
    local target_gid="${3:-${target_uid}}"

    if [[ ! -d "${certs_dir}" ]]; then
        return 0
    fi

    if [[ "$(id -u)" -eq 0 ]]; then
        chown -R "${target_uid}:${target_gid}" "${certs_dir}"
        find "${certs_dir}" -type d -exec chmod 755 {} \;
        find "${certs_dir}" -type f -name '*.key' -exec chmod 600 {} \;
        find "${certs_dir}" -type f ! -name '*.key' -exec chmod 644 {} \;
        _certs_perm_log "已规范化证书目录权限: ${certs_dir} (owner=${target_uid}:${target_gid})"
        return 0
    fi

    find "${certs_dir}" -type d -exec chmod 755 {} \;
    find "${certs_dir}" -type f -name '*.key' -exec chmod 600 {} \;
    find "${certs_dir}" -type f ! -name '*.key' -exec chmod 644 {} \;
    _certs_perm_log "警告: 当前非 root (uid=$(id -u))，无法 chown ${certs_dir}；已尽量将私钥设为 600"
}
