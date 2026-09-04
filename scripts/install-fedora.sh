#!/usr/bin/bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$project_dir/scripts/install-lib.sh"

desktop_file="$project_dir/data/io.github.tailtray.desktop"
icon_dir="$project_dir/data/icons/hicolor/scalable/status"
tray_icon_dir="$project_dir/data/tray-icons"
current_uid=$(id -u)
installed_python_dir="/usr/lib/tailtray/tailtray"

if [[ "${1:-}" == "--check" ]]; then
    tailtray_verify_python_tree "$project_dir/src/tailtray" "$installed_python_dir"
    echo "Installed TailTray modules match this checkout."
    exit 0
fi

if (($#)); then
    echo "Usage: $0 [--check]" >&2
    exit 2
fi

stop_installed_tailtray() {
    local attempt pid
    local -a pids

    # Repeat discovery in case a desktop service is still finishing a restart.
    for ((attempt = 0; attempt < 3; attempt++)); do
        mapfile -t pids < <(tailtray_running_pids "$current_uid")
        if ((${#pids[@]} == 0)); then
            return 0
        fi

        echo "Stopping installed TailTray process(es): ${pids[*]}"
        for pid in "${pids[@]}"; do
            kill "$pid" 2>/dev/null || true
        done

        for _ in {1..30}; do
            local any_running=false
            for pid in "${pids[@]}"; do
                if kill -0 "$pid" 2>/dev/null; then
                    any_running=true
                    break
                fi
            done
            [[ "$any_running" == false ]] && break
            sleep 0.1
        done
    done

    mapfile -t pids < <(tailtray_running_pids "$current_uid")
    if ((${#pids[@]})); then
        echo "Could not stop old TailTray process(es): ${pids[*]}" >&2
        return 1
    fi
}

missing=()
for package in tailscale python3-gobject python3-pyside6 libappindicator-gtk3 libsecret libnotify; do
    if ! rpm -q "$package" >/dev/null 2>&1; then
        missing+=("$package")
    fi
done

if ((${#missing[@]})); then
    echo "Missing Fedora packages: ${missing[*]}" >&2
    echo "Install them first with: sudo dnf install ${missing[*]}" >&2
    exit 1
fi

sudo -v

# Never update modules underneath a live Python process. In addition to
# avoiding mixed versions, this guarantees that the relaunch imports every new
# module (notably status_notifier.py for KDE's left-click menu path).
stop_installed_tailtray

sudo install -d "$installed_python_dir"
sudo install -m 0644 "$project_dir"/src/tailtray/*.py "$installed_python_dir/"
sudo install -m 0755 "$project_dir/bin/tailtray" /usr/bin/tailtray
sudo install -m 0644 "$desktop_file" /usr/share/applications/io.github.tailtray.desktop
sudo install -m 0644 "$project_dir/data/io.github.tailtray.metainfo.xml" /usr/share/metainfo/io.github.tailtray.metainfo.xml
for icon in "$icon_dir"/*.svg; do
    sudo install -m 0644 "$icon" "/usr/share/icons/hicolor/scalable/status/$(basename "$icon")"
done
sudo install -d /usr/share/tailtray/icons
for icon in "$tray_icon_dir"/*.png; do
    sudo install -m 0644 "$icon" "/usr/share/tailtray/icons/$(basename "$icon")"
done
sudo gtk-update-icon-cache -q /usr/share/icons/hicolor || true

sudo systemctl enable --now tailscaled.service
sudo tailscale set --operator="$USER"

install -d "$HOME/.config/autostart"
install -m 0644 "$desktop_file" "$HOME/.config/autostart/io.github.tailtray.desktop"

if ! tailtray_verify_python_tree "$project_dir/src/tailtray" "$installed_python_dir"; then
    echo "TailTray installation verification failed; it was not restarted." >&2
    exit 1
fi
echo "Verified every installed TailTray Python module."

if [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" && -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    start_log="${XDG_RUNTIME_DIR:-/tmp}/tailtray-install-start-$current_uid.log"
    : > "$start_log"
    nohup /usr/bin/tailtray >"$start_log" 2>&1 &
    started_pid=$!
    disown || true
    sleep 1
    if kill -0 "$started_pid" 2>/dev/null; then
        echo "TailTray started with the verified update (PID $started_pid)."
    else
        echo "TailTray was installed but failed to start. Startup output:" >&2
        sed -n '1,80p' "$start_log" >&2
        exit 1
    fi
else
    echo "No graphical session detected; TailTray will start at the next login."
fi

echo
echo "TailTray installed. You can verify this checkout with: $0 --check"
echo "It will start automatically with your next desktop login."
