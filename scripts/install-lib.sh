#!/usr/bin/bash

# Shared, side-effect-free helpers for the Fedora installer. Keeping process
# detection and tree verification here makes the update path testable without
# sudo or a running desktop session.

tailtray_verify_repository_key() {
    local key_file="$1" expected="$2" listing primary_count actual
    listing=$(gpg --batch --show-keys --with-colons "$key_file" 2>/dev/null) || return 1
    primary_count=$(awk -F: '$1 == "pub" {n++} END {print n+0}' <<< "$listing")
    actual=$(awk -F: '$1 == "fpr" {print $10; exit}' <<< "$listing")
    # Do not trust extra primary keys bundled after the pinned one. Native
    # apt/dnf will trust every imported primary key in this file.
    [[ $primary_count == 1 && $actual == "$expected" ]] || {
        echo "Repository key must contain only the pinned primary key" >&2; return 1;
    }
}

tailtray_process_matches() {
    local executable="${1:-}"
    local launcher="${2:-}"

    [[ "$launcher" == "/usr/bin/tailtray" ]] || return 1
    [[ "$executable" =~ (^|/)python3([.][0-9]+)?$ ]]
}

tailtray_running_pids() {
    local target_uid="${1:-$(id -u)}"
    local proc_root="${2:-/proc}"
    local process_dir owner argument
    local -a arguments

    for process_dir in "$proc_root"/[0-9]*; do
        [[ -r "$process_dir/cmdline" ]] || continue
        owner=$(stat -c '%u' -- "$process_dir" 2>/dev/null) || continue
        [[ "$owner" == "$target_uid" ]] || continue

        arguments=()
        while IFS= read -r -d '' argument; do
            arguments+=("$argument")
        done < "$process_dir/cmdline"

        if ((${#arguments[@]} >= 2)) && tailtray_process_matches "${arguments[0]}" "${arguments[1]}"; then
            basename -- "$process_dir"
        fi
    done
}

tailtray_verify_python_tree() {
    local source_dir="$1"
    local installed_dir="$2"
    local source_file installed_file
    local found=false

    for source_file in "$source_dir"/*.py; do
        [[ -f "$source_file" ]] || continue
        found=true
        installed_file="$installed_dir/$(basename -- "$source_file")"
        if [[ ! -f "$installed_file" ]]; then
            echo "Missing installed module: $installed_file" >&2
            return 1
        fi
        if ! cmp -s -- "$source_file" "$installed_file"; then
            echo "Installed module differs from checkout: $installed_file" >&2
            return 1
        fi
    done

    if [[ "$found" == false ]]; then
        echo "No Python modules found in: $source_dir" >&2
        return 1
    fi
}
