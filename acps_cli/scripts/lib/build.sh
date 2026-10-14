#!/usr/bin/env bash

validate_required_files() {
    local project_dir="$1"
    shift
    local missing=0
    local file_path

    if [[ $# -eq 0 ]]; then
        echo "错误：validate_required_files 至少需要一个待检查路径" >&2
        return 1
    fi

    echo "=== 检查文件完整性 ==="
    for file_path in "$@"; do
        if [[ ! -e "${project_dir}/${file_path}" ]]; then
            echo "  缺失: ${file_path}" >&2
            missing=$((missing + 1))
        fi
    done

    if [[ ${missing} -gt 0 ]]; then
        echo "错误：缺少 ${missing} 个必要文件" >&2
        return 1
    fi

    echo "  所有必要文件检查通过"
}

detect_sha256_cmd() {
    if command -v sha256sum &>/dev/null; then
        printf '%s\n' sha256sum
    elif command -v shasum &>/dev/null; then
        printf '%s\n' shasum -a 256
    else
        echo "错误：未找到 sha256sum 或 shasum 命令，无法生成校验文件" >&2
        return 1
    fi
}

DEFAULT_BUNDLE_EXCLUDE_MAP=(
    "__pycache__"
    "*/__pycache__"
    "*.pyc"
    "*.pyo"
    ".pytest_cache"
    "*/.pytest_cache"
    ".mypy_cache"
    "*/.mypy_cache"
    ".ruff_cache"
    "*/.ruff_cache"
    ".DS_Store"
)

copy_bundle_files() {
    local project_dir="$1"
    local staging_dir="$2"
    local bundle_map_name="${3:-}"
    local exclude_map_name="${4:-}"
    local entry
    local src_path
    local dest_path
    local src
    local dest
    local pattern
    local exclude_args=()
    local bundle_map=()
    local exclude_map=()

    if [[ -z "${bundle_map_name}" ]]; then
        echo "错误：copy_bundle_files 需要提供打包映射数组名" >&2
        return 1
    fi

    eval "bundle_map=(\"\${${bundle_map_name}[@]}\")"
    if [[ ${#bundle_map[@]} -eq 0 ]]; then
        echo "错误：copy_bundle_files 至少需要一个打包映射" >&2
        return 1
    fi

    if [[ -n "${exclude_map_name}" ]]; then
        eval "exclude_map=(\"\${${exclude_map_name}[@]}\")"
    fi

    for pattern in "${exclude_map[@]}"; do
        exclude_args+=("--exclude=${pattern}")
    done

    for entry in "${bundle_map[@]}"; do
        src_path="${entry%%|*}"
        dest_path="${entry#*|}"
        src="${project_dir}/${src_path}"
        dest="${staging_dir}/${dest_path}"
        mkdir -p "$(dirname "${dest}")"
        if [[ -d "${src}" ]]; then
            mkdir -p "${dest}"
            tar "${exclude_args[@]}" -cf - -C "${src}" . | tar -xf - -C "${dest}"
        else
            cp "${src}" "${dest}"
        fi
    done
}

generate_checksums() {
    local staging_dir="$1"
    shift

    if [[ $# -eq 0 ]]; then
        echo "错误：generate_checksums 需要显式传入校验命令" >&2
        return 1
    fi

    (
        cd "${staging_dir}"
        find . -type f ! -name 'checksums.txt' -print0 \
            | LC_ALL=C sort -z \
            | xargs -0 "$@" > checksums.txt
    )
}

create_release_tar() {
    local staging_parent_dir="$1"
    local release_name="$2"
    local output_dir="$3"
    local release_tar

    release_tar="${output_dir}/${release_name}.tar.gz"
    tar czf "${release_tar}" -C "${staging_parent_dir}" "${release_name}"
    printf '%s\n' "${release_tar}"
}