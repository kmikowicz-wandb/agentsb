#!/bin/bash
# Install host-side pf egress rules for Lima VMs (macOS only).
#
# Rules match packets sourced from Apple's vmnet shared subnet
# (192.168.64.0/24, used by Lima's vzNAT driver) before NAT, so code
# running inside the VM cannot modify or bypass them regardless of what
# privileges it holds inside the guest.
set -euo pipefail

ANCHOR="agentsb"
ANCHOR_DEST="/etc/pf.anchors/agentsb"
PF_CONF="/etc/pf.conf"
RULES_SRC="$(cd "$(dirname "$0")" && pwd)/pf-anchor.conf"

is_installed() {
    [[ -f "$ANCHOR_DEST" ]] && cmp -s "$ANCHOR_DEST" "$RULES_SRC"
}

install() {
    if [[ ! -f "$RULES_SRC" ]]; then
        echo "pf rules template not found: $RULES_SRC" >&2
        exit 1
    fi

    echo "Installing pf anchor rules..."
    sudo mkdir -p /etc/pf.anchors
    sudo cp "$RULES_SRC" "$ANCHOR_DEST"
    sudo chmod 644 "$ANCHOR_DEST"

    # Add anchor reference to /etc/pf.conf (idempotent).
    local pf_conf
    pf_conf=$(sudo cat "$PF_CONF")
    local patched=false
    if ! grep -qF "anchor \"$ANCHOR\"" <<< "$pf_conf"; then
        printf '\n# agentsb Lima VM egress filtering\nanchor "%s"\nload anchor "%s" from "%s"\n' \
            "$ANCHOR" "$ANCHOR" "$ANCHOR_DEST" | sudo tee -a "$PF_CONF" > /dev/null
        patched=true
    fi

    # Enable pf if not already; pfctl -e exits 1 when pf is already on (normal on modern macOS).
    sudo pfctl -e 2>/dev/null || true

    # Reload the main ruleset only when we just modified /etc/pf.conf.
    if $patched; then
        echo "Reloading /etc/pf.conf..."
        sudo pfctl -f "$PF_CONF"
    fi

    # Load rules into our anchor — scoped so the main ruleset is untouched.
    echo "Loading agentsb pf anchor..."
    sudo pfctl -a "$ANCHOR" -f "$ANCHOR_DEST"
    echo "Host firewall installed."
}

if ! is_installed; then
    install
fi
