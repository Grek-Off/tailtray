#!/usr/bin/bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
desktop_file="$project_dir/data/io.github.tailtray.desktop"
icon_dir="$project_dir/data/icons/hicolor/scalable/status"
tray_icon_dir="$project_dir/data/tray-icons"
current_uid=$(id -u)
running_tailtray_pids=()

# Python keeps imported modules in memory, so copying an update over a running
# TailTray is not enough. Remember only the installed launcher process; do not
# disturb development instances started from a checkout.
while IFS= read -r pid; do
    if [[ -n "$pid" ]]; then
        running_tailtray_pids+=("$pid")
    fi
done < <(pgrep -u "$current_uid" -f '^/usr/bin/python3 /usr/bin/tailtray$' || true)

missing=()
for package in tailscale python3-gobject python3-pyside6 libappindicator-gtk3 libsecret libnotify xorg-x11-server-Xwayland; do
    if ! rpm -q "$package" >/dev/null 2>&1; then
        missing+=("$package")
    fi
done

if ((${#missing[@]})); then
    echo "Missing Fedora packages: ${missing[*]}" >&2
    echo "Install them first with: sudo dnf install ${missing[*]}" >&2
    exit 1
fi

sudo install -d /usr/lib/tailtray/tailtray
sudo install -m 0644 "$project_dir"/src/tailtray/*.py /usr/lib/tailtray/tailtray/
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

if ((${#running_tailtray_pids[@]})); then
    echo "Restarting the running TailTray instance..."
    for pid in "${running_tailtray_pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done

    still_running=true
    for _ in {1..30}; do
        still_running=false
        for pid in "${running_tailtray_pids[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                still_running=true
                break
            fi
        done
        if [[ "$still_running" == false ]]; then
            break
        fi
        sleep 0.1
    done

    if [[ "$still_running" == true ]]; then
        echo "TailTray was updated, but its old process did not stop. Quit and start it manually." >&2
    else
        nohup /usr/bin/tailtray >/dev/null 2>&1 &
        disown || true
        echo "TailTray restarted with the installed update."
    fi
fi

echo
echo "TailTray installed. Start it from the application menu or run: tailtray"
echo "It will start automatically with your next desktop login."
