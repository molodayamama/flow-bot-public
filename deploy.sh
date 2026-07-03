#!/bin/bash
set -euo pipefail

cd /opt/geminifree

mode="${1:-auto}"
case "$mode" in
    auto|all|static|bot) ;;
    *)
        echo "Usage: $0 [auto|all|static|bot]" >&2
        exit 2
        ;;
esac

before="$(git rev-parse HEAD)"
git pull --ff-only origin main
after="$(git rev-parse HEAD)"

changed_files=""
if [ "$before" != "$after" ]; then
    changed_files="$(git diff --name-only "$before" "$after")"
fi

has_changed_path() {
    local pattern="$1"
    [ -n "$changed_files" ] && printf '%s\n' "$changed_files" | grep -Eq "$pattern"
}

needs_static=0
needs_bot=0

if [ "$mode" = "all" ] || [ "$mode" = "static" ]; then
    needs_static=1
elif [ "$mode" = "auto" ] && has_changed_path '^deploy/photozhab/'; then
    needs_static=1
fi

if [ "$mode" = "all" ] || [ "$mode" = "bot" ]; then
    needs_bot=1
elif [ "$mode" = "auto" ] && has_changed_path '(^[^/]+\.py$|^(channels|flow_profiler|tg_e2e)/.*\.py$|^requirements\.txt$|^deploy/bin/geminifree-bot-run$|^deploy/systemd/geminifree-bot\.service$)'; then
    needs_bot=1
fi

if [ "$needs_static" -eq 1 ]; then
    mkdir -p /var/www/photozhab/assets
    cp deploy/photozhab/*.html      /var/www/photozhab/
    cp deploy/photozhab/styles.css  /var/www/photozhab/
    cp deploy/photozhab/assets/*    /var/www/photozhab/assets/ 2>/dev/null || true
    echo "Static site updated"
else
    echo "Static site unchanged; copy skipped"
fi

compile_changed_python() {
    local files
    files="$(printf '%s\n' "$changed_files" | grep -E '(^[^/]+\.py$|^(channels|flow_profiler|tg_e2e)/.*\.py$)' || true)"
    if [ -n "$files" ]; then
        # shellcheck disable=SC2086
        /opt/geminifree/.venv/bin/python -m py_compile $files
    else
        /opt/geminifree/.venv/bin/python -m py_compile \
            flow_bot.py flow_core.py flow_copy.py metrics.py admin_api.py
    fi
}

restart_if_installed() {
    local unit="$1"
    if systemctl list-unit-files --no-legend "${unit}.service" | grep -q "^${unit}.service"; then
        systemctl restart "$unit"
        systemctl is-active "$unit"
    else
        echo "Skipping $unit (unit is not installed)"
    fi
}

if [ "$needs_bot" -eq 1 ]; then
    compile_changed_python
    restart_if_installed geminifree-bot
    echo "Bot service restarted"
else
    echo "Bot runtime unchanged; restart skipped"
fi

if [ "$needs_static" -eq 0 ] && [ "$needs_bot" -eq 0 ]; then
    echo "No deployable changes detected"
fi

echo "Deploy done"
