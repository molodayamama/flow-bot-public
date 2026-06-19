#!/bin/bash
set -e
cd /opt/geminifree
git pull origin main

# Static site: copy every page + stylesheet + assets so new files
# (oferta.html, consent.html, …) ship automatically without editing this list.
mkdir -p /var/www/photozhab/assets
cp deploy/photozhab/*.html      /var/www/photozhab/
cp deploy/photozhab/styles.css  /var/www/photozhab/
cp deploy/photozhab/assets/*    /var/www/photozhab/assets/ 2>/dev/null || true

restart_if_installed() {
    local unit="$1"
    local required_env="${2:-}"
    if systemctl list-unit-files --no-legend "${unit}.service" | grep -q "^${unit}.service"; then
        if [ -n "$required_env" ] && [ ! -r "$required_env" ]; then
            echo "Skipping $unit (missing $required_env)"
            return 0
        fi
        systemctl stop "$unit" || true
        sleep 3
        systemctl start "$unit"
        systemctl is-active "$unit"
    else
        echo "Skipping $unit (unit is not installed)"
    fi
}

restart_if_installed geminifree-bot
restart_if_installed geminifree-seller-bot /opt/geminifree/.env.seller
echo 'Deploy done, bot services restarted'
