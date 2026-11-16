#!/bin/sh
# Install video-label: one line, no admin rights, no Homebrew, no Xcode tools.
#
#   curl -fsSL https://raw.githubusercontent.com/giladmanor/video-labeling-app/main/install.sh | sh
#
# What it does (see README.md "Install"):
#   1. installs uv (https://docs.astral.sh/uv/) in ~/.local/bin if it is missing;
#   2. downloads the app source from GitHub (a tarball, so git is not needed);
#   3. installs the app with uv, with its own Python 3.12 (uv downloads it if needed);
#   4. downloads a static ffmpeg build if ffmpeg and ffprobe are not on PATH;
#   5. adds ~/.local/bin to PATH in the shell startup files.
#
# Run it again to update. Options, as environment variables in front of `sh`:
#   VIDEO_LABEL_REF=<branch|tag|commit>   source to install (default main)
#   VIDEO_LABEL_SOURCE=<directory>        install from a local checkout instead of GitHub
#   VIDEO_LABEL_NO_MODIFY_PATH=1          do not change the shell startup files
#
# Uninstall: uv tool uninstall video-label

# Everything is inside main, so a partly downloaded script does nothing.
set -eu

REPO="giladmanor/video-labeling-app"
PYTHON_VERSION="3.12"

if [ -t 1 ]; then B='\033[1m' Y='\033[33m' R='\033[31m' N='\033[0m'; else B='' Y='' R='' N=''; fi
say() { printf "${B}==> %s${N}\n" "$*"; }
warn() { printf "${Y}warning:${N} %s\n" "$*" >&2; }
die() { printf "${R}error:${N} %s\n" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

main() {
    ref="${VIDEO_LABEL_REF:-main}"
    bin_dir="$HOME/.local/bin"

    case "$(uname -s)" in
        Darwin) ;;
        Linux) warn "video-label is made for macOS; on Linux, the Keychain commands may not work." ;;
        *) die "unsupported system: $(uname -s) (video-label needs macOS)" ;;
    esac
    have curl || die "curl is needed"
    have tar || die "tar is needed"

    tmp="$(mktemp -d "${TMPDIR:-/tmp}/video-label-install.XXXXXX")"
    trap 'rm -rf "$tmp"' EXIT INT TERM

    # 1. uv
    if have uv; then
        uv="$(command -v uv)"
    elif [ -x "$bin_dir/uv" ]; then
        uv="$bin_dir/uv"
    else
        say "Installing uv"
        curl -fsSL https://astral.sh/uv/install.sh -o "$tmp/uv-install.sh"
        UV_INSTALL_DIR="$bin_dir" UV_NO_MODIFY_PATH=1 sh "$tmp/uv-install.sh" -q
        uv="$bin_dir/uv"
    fi
    [ -x "$uv" ] || die "uv was not installed"
    say "Using $("$uv" --version)"

    # 2. source
    if [ -n "${VIDEO_LABEL_SOURCE:-}" ]; then
        src="$VIDEO_LABEL_SOURCE"
        [ -f "$src/pyproject.toml" ] || die "$src has no pyproject.toml"
        say "Using the source in $src"
    else
        say "Downloading $REPO ($ref)"
        curl -fsSL "https://codeload.github.com/$REPO/tar.gz/$ref" -o "$tmp/src.tar.gz" \
            || die "could not download $REPO at '$ref'"
        mkdir "$tmp/src"
        tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --strip-components 1
        src="$tmp/src"
    fi

    # 3. the app, with its own Python
    say "Installing video-label and its packages with Python $PYTHON_VERSION (about a minute)"
    UV_TOOL_BIN_DIR="$bin_dir" "$uv" tool install --quiet --force --reinstall \
        --python "$PYTHON_VERSION" "$src"
    app="$bin_dir/video-label"
    [ -x "$app" ] || die "video-label was not installed in $bin_dir"
    "$app" --help >/dev/null || die "video-label was installed but does not start"

    # 4. ffmpeg (the app falls back to the static-ffmpeg package, see docs/design.md §3.1)
    if have ffmpeg && have ffprobe; then
        say "Using ffmpeg from $(command -v ffmpeg)"
    else
        say "Downloading a static ffmpeg build"
        py="$("$uv" tool dir)/video-label/bin/python"
        "$py" -c 'import static_ffmpeg.run as r; print(r.get_or_fetch_platform_executables_else_raise()[0])' \
            || die "could not download ffmpeg; install it with 'brew install ffmpeg' and run this again"
    fi

    # 5. PATH
    # uv fails when the line is there already, so look first.
    if [ -z "${VIDEO_LABEL_NO_MODIFY_PATH:-}" ] && ! grep -qsF "$bin_dir" \
        "$HOME/.zshenv" "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; then
        UV_TOOL_BIN_DIR="$bin_dir" "$uv" tool update-shell -q >/dev/null 2>&1 \
            || warn "could not add $bin_dir to PATH; add it in your shell startup file"
    fi

    say "Installed: $app"
    case ":$PATH:" in
        *":$bin_dir:"*) echo "Run: video-label" ;;
        *) echo "Open a new terminal and run: video-label"
           echo "(or now: export PATH=\"$bin_dir:\$PATH\" && video-label)" ;;
    esac
    echo "The app opens on http://127.0.0.1:8765/. Update: run this installer again."
    echo "Uninstall: uv tool uninstall video-label"
}

main "$@"
