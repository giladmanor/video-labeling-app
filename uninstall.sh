#!/bin/sh
# Uninstall video-label and everything that install.sh added.
#
#   curl -fsSL https://raw.githubusercontent.com/giladmanor/video-labeling-app/main/uninstall.sh | sh
#
# What it removes (see README.md "Uninstall"):
#   1. the app, its packages, and the static ffmpeg build (`uv tool uninstall video-label`);
#   2. what install.sh lists in ~/.local/share/video-label/install-receipt:
#      uv (with its cache, its Python downloads, and the PATH line in the shell startup files)
#      if the installer installed it and no other uv tools are left; otherwise only the Python
#      that the installer downloaded, if no uv tools are left;
#   3. on request, your data: the workspace (labels, exports) and the xAI key in the Keychain.
#      It asks; without a terminal, it keeps the data.
#
# Options, as environment variables in front of `sh`:
#   VIDEO_LABEL_REMOVE_DATA=1|0   remove (1) or keep (0) the data without asking
#   VIDEO_LABEL_WORKSPACE=<dir>   the workspace to remove (default ~/video-labeling)

# Everything is inside main, so a partly downloaded script does nothing.
set -eu

if [ -t 1 ]; then B='\033[1m' Y='\033[33m' N='\033[0m'; else B='' Y='' N=''; fi
say() { printf "${B}==> %s${N}\n" "$*"; }
warn() { printf "${Y}warning:${N} %s\n" "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# The values of KEY in the receipt, one per line.
receipt_get() { sed -n "s/^$1=//p" "$receipt" 2>/dev/null || true; }

# Remove the lines that `uv tool update-shell` adds for $bin_dir from file $1:
# the export line and the "# uv" comment and blank lines just before it.
remove_path_line() {
    [ -f "$1" ] || return 0
    line="export PATH=\"$bin_dir:\$PATH\""
    grep -qxF "$line" "$1" || return 0
    awk -v line="$line" '
        $0 == "" || $0 == "# uv" { held = held $0 "\n"; next }
        $0 == line { held = ""; next }
        { printf "%s", held; held = ""; print }
        END { printf "%s", held }
    ' "$1" >"$1.video-label-tmp" && mv "$1.video-label-tmp" "$1"
    say "Removed the PATH line from $1"
}

main() {
    bin_dir="$HOME/.local/bin"
    receipt_dir="$HOME/.local/share/video-label"
    receipt="$receipt_dir/install-receipt"
    workspace="${VIDEO_LABEL_WORKSPACE:-$HOME/video-labeling}"

    if have uv; then
        uv="$(command -v uv)"
    elif [ -x "$bin_dir/uv" ]; then
        uv="$bin_dir/uv"
    else
        uv=""
    fi
    [ -f "$receipt" ] || warn "no install receipt in $receipt_dir; removing only the app"
    uv_installed="$(receipt_get UV_INSTALLED)"
    path_files="$(receipt_get PATH_FILE)"
    python_dirs="$(receipt_get PYTHON_DIR)"

    # 1. the app (its environment also holds the static ffmpeg build)
    if [ -n "$uv" ] && "$uv" tool list 2>/dev/null | grep -q '^video-label '; then
        say "Removing the video-label app"
        "$uv" tool uninstall video-label
    elif [ -e "$bin_dir/video-label" ]; then
        rm -f "$bin_dir/video-label"
        say "Removed $bin_dir/video-label"
    else
        say "The video-label app is not installed"
    fi

    # 2. what the installer added
    other_tools=""
    [ -n "$uv" ] && other_tools="$("$uv" tool list 2>/dev/null | grep -v '^[-[:space:]]' | grep -v '^No tools' || true)"
    if [ -n "$other_tools" ]; then
        # Keep the receipt, so a later uninstall can still remove what stays now.
        warn "other uv tools are installed, so uv and its Python downloads stay:"
        echo "$other_tools" | sed 's/^/    /' >&2
        [ -f "$receipt" ] && echo "Run this again after you remove them to remove the rest."
    elif [ -n "$uv" ] && [ "$uv_installed" = 1 ]; then
        say "Removing uv, its cache, and its Python downloads"
        rm -rf "$receipt_dir"
        rm -rf "$("$uv" cache dir)" "$("$uv" python dir)" "$("$uv" tool dir)"
        rm -f "$uv" "$(dirname "$uv")/uvx" "$HOME/.config/uv/uv-receipt.json"
        # Directories that are now empty; rmdir keeps any that are not.
        for d in "$HOME/.local/share/uv" "$HOME/.config/uv" "$bin_dir" "$HOME/.local/share" \
            "$HOME/.local" "$HOME/.config" "$HOME/.cache"; do
            rmdir "$d" 2>/dev/null || true
        done
        printf '%s\n' "$path_files" | while IFS= read -r f; do remove_path_line "$f"; done
    elif [ -n "$uv" ]; then
        py_dir="$("$uv" python dir)"
        printf '%s\n' "$python_dirs" | while IFS= read -r d; do
            [ -n "$d" ] && [ -d "$py_dir/$d" ] || continue
            rm -rf "${py_dir:?}/$d"
            say "Removed the Python that the installer downloaded: $d"
        done
        rm -rf "$receipt_dir"
    else
        rm -rf "$receipt_dir"
    fi

    # 3. data, on request
    remove_data="${VIDEO_LABEL_REMOVE_DATA:-}"
    if [ -z "$remove_data" ] && { [ -d "$workspace" ] || have security; } && (exec </dev/tty) 2>/dev/null; then
        printf "Also delete your data: the workspace %s (labels, exports) and the xAI key in the Keychain? [y/N] " "$workspace"
        read -r answer </dev/tty || answer=""
        case "$answer" in [yY]*) remove_data=1 ;; *) remove_data=0 ;; esac
    fi
    if [ "$remove_data" = 1 ]; then
        if [ -d "$workspace" ]; then
            rm -rf "$workspace"
            say "Removed the workspace $workspace"
        fi
        if have security && security delete-generic-password -s video-label -a xai-api-key >/dev/null 2>&1; then
            say "Removed the xAI key from the Keychain"
        fi
    elif [ -d "$workspace" ]; then
        echo "Kept your workspace: $workspace"
    fi

    say "video-label is uninstalled"
    if [ -n "$uv" ] && [ -x "$uv" ]; then
        echo "uv stays in $(dirname "$uv"); it was there before video-label, or other uv tools use it."
    fi
}

main "$@"
