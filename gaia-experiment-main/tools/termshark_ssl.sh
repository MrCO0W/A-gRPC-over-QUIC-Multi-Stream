#!/usr/bin/env bash
# ==============================================================================
# GAIA termshark SSL/TLS Decryption Wrapper
# ==============================================================================
# Seamlessly links a .pcap capture and sslkeys.log into an isolated ephemeral
# XDG sandbox without modifying or hardcoding ~/.config/wireshark/preferences.
# ==============================================================================

set -euo pipefail

usage() {
    echo "Usage: $(basename "$0") [OPTIONS] <pcap_file|run_dir> [sslkeys.log]"
    echo ""
    echo "Options:"
    echo "  -k, --keylog <file>       Explicit path to SSLKEYLOGFILE"
    echo "  -Y, --filter <expr>       Wireshark display filter (e.g. 'tls', 'http2')"
    echo "  -t, --tool <bin>          Binary to run: termshark (default), tshark, wireshark"
    echo "  -h, --help                Show this help message"
    echo ""
    echo "Examples:"
    echo "  $(basename "$0") output/20260830-120000"
    echo "  $(basename "$0") output/20260830-120000/c0.pcap"
    echo "  $(basename "$0") -k /path/to/sslkeys.log output/run/c0.pcap"
    exit 1
}

TARGET=""
KEYLOG=""
FILTER=""
TOOL="termshark"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -k|--keylog)
            KEYLOG="$2"
            shift 2
            ;;
        -Y|--filter)
            FILTER="$2"
            shift 2
            ;;
        -t|--tool)
            TOOL="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            ;;
        *)
            if [[ -z "$TARGET" ]]; then
                TARGET="$1"
            elif [[ -z "$KEYLOG" ]]; then
                KEYLOG="$1"
            fi
            shift
            ;;
    esac
done

if [[ -z "$TARGET" ]]; then
    # Fallback to latest run directory under output/
    if [[ -d "output" ]]; then
        TARGET=$(ls -td output/*/ 2>/dev/null | head -n 1 || true)
    fi
fi

if [[ -z "$TARGET" || ! -e "$TARGET" ]]; then
    echo "Error: Target pcap file or run directory not found: '$TARGET'" >&2
    exit 1
fi

# Locate PCAP
PCAP_FILE=""
if [[ -f "$TARGET" ]]; then
    PCAP_FILE="$TARGET"
elif [[ -d "$TARGET" ]]; then
    # Pick merged.pcap if available, else first *.pcap
    if [[ -f "$TARGET/merged.pcap" ]]; then
        PCAP_FILE="$TARGET/merged.pcap"
    else
        PCAP_FILE=$(find "$TARGET" -maxdepth 1 -name "*.pcap" -o -name "*.pcapng" | sort | head -n 1 || true)
    fi
fi

if [[ -z "$PCAP_FILE" || ! -f "$PCAP_FILE" ]]; then
    echo "Error: No .pcap files found in '$TARGET'" >&2
    exit 1
fi

# Locate Keylog if not explicitly provided
if [[ -z "$KEYLOG" ]]; then
    SEARCH_DIR=$(dirname "$(realpath "$PCAP_FILE")")
    for cand in sslkeys.log sslkey.log tlskeys.log tls.keylog keylog.txt keys.log sslkeylog.log; do
        if [[ -f "$SEARCH_DIR/$cand" && -s "$SEARCH_DIR/$cand" ]]; then
            KEYLOG=$(realpath "$SEARCH_DIR/$cand")
            break
        fi
    done
    if [[ -z "$KEYLOG" ]]; then
        for k in "$SEARCH_DIR"/*key*.log "$SEARCH_DIR"/*.keylog; do
            if [[ -f "$k" && -s "$k" ]]; then
                KEYLOG=$(realpath "$k")
                break
            fi
        done
    fi
fi

# Create ephemeral XDG sandbox
TMP_XDG=$(mktemp -d -t gaia_termshark_XXXXXX)
cleanup() {
    rm -rf "$TMP_XDG"
}
trap cleanup EXIT INT TERM

mkdir -p "$TMP_XDG/wireshark"

# Copy user preferences and colorfilters if present
if [[ -d "$HOME/.config/wireshark" ]]; then
    if [[ -f "$HOME/.config/wireshark/preferences" ]]; then
        grep -v -E '^(tls|ssl)\.keylog_file' "$HOME/.config/wireshark/preferences" > "$TMP_XDG/wireshark/preferences" || true
    fi
    if [[ -f "$HOME/.config/wireshark/colorfilters" ]]; then
        cp -f "$HOME/.config/wireshark/colorfilters" "$TMP_XDG/wireshark/colorfilters" || true
    fi
fi

# Inject TLS keylog configuration with strict 0600 permissions
PREFS_FILE="$TMP_XDG/wireshark/preferences"
touch "$PREFS_FILE"
chmod 0600 "$PREFS_FILE"
if [[ -n "$KEYLOG" && -f "$KEYLOG" ]]; then
    KEYLOG_ABS=$(realpath "$KEYLOG")
    echo "tls.keylog_file: $KEYLOG_ABS" >> "$PREFS_FILE"
    echo "ssl.keylog_file: $KEYLOG_ABS" >> "$PREFS_FILE"
    echo "[*] Attached TLS Keylog: $KEYLOG_ABS"
else
    echo "[!] No SSL/TLS keylog detected. Running without decryption."
fi

echo "[*] Launching $TOOL on $(realpath "$PCAP_FILE")"

CMD=("$TOOL" "-r" "$(realpath "$PCAP_FILE")")
if [[ -n "$FILTER" ]]; then
    CMD+=("-Y" "$FILTER")
fi

export XDG_CONFIG_HOME="$TMP_XDG"
if [[ -n "$KEYLOG" && -f "$KEYLOG" ]]; then
    export SSLKEYLOGFILE="$(realpath "$KEYLOG")"
fi

# Run without exec so trap cleanup triggers on exit
RC=0
"${CMD[@]}" || RC=$?
exit $RC
