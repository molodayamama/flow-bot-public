#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/geminifree}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/geminifree}"
PYTHON="${PYTHON:-${APP_ROOT}/.venv/bin/python}"
ENV_FILE="${ENV_FILE:-${APP_ROOT}/.env}"
SELLER_ENV_FILE="${SELLER_ENV_FILE:-${APP_ROOT}/.env.seller}"
SERVICE_USER="${SERVICE_USER:-bot}"

cd "$APP_ROOT"

if [[ ! "$DEPLOY_BRANCH" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] \
    || [[ "$DEPLOY_BRANCH" == *..* ]] \
    || [[ "$DEPLOY_BRANCH" == *@\{* ]]; then
    echo "Refusing deploy: DEPLOY_BRANCH is invalid" >&2
    exit 1
fi
if [ -n "${DEPLOY_SHA:-}" ] && [[ ! "$DEPLOY_SHA" =~ ^[0-9a-fA-F]{40}$ ]]; then
    echo "Refusing deploy: DEPLOY_SHA must be a full 40-character commit id" >&2
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Refusing deploy: tracked worktree changes are present" >&2
    exit 1
fi
if [ ! -x "$PYTHON" ]; then
    echo "Refusing deploy: Python runtime is missing: $PYTHON" >&2
    exit 1
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "Refusing deploy: service user does not exist" >&2
    exit 1
fi
if [ ! -r "$ENV_FILE" ]; then
    echo "Refusing deploy: production ENV_FILE is unreadable" >&2
    exit 1
fi

previous_sha="$(git rev-parse HEAD)"
checked_out_target=0
backup_tool=""
backup_tool_dir=""
compile_dir=""
declare -a restarted_units=()

unit_is_installed() {
    systemctl cat "$1.service" >/dev/null 2>&1
}

run_as_service() {
    if [ "$(id -u)" -eq 0 ]; then
        runuser -u "$SERVICE_USER" -- "$@"
    else
        "$@"
    fi
}

copy_static() {
    install -d -m 0755 /var/www/photozhab/assets
    install -m 0644 \
        deploy/photozhab/*.html \
        deploy/photozhab/*.css \
        deploy/photozhab/*.js \
        deploy/photozhab/*.txt \
        deploy/photozhab/*.xml \
        /var/www/photozhab/
    if compgen -G "deploy/photozhab/assets/*" >/dev/null; then
        install -m 0644 deploy/photozhab/assets/* /var/www/photozhab/assets/
    fi
}

restart_if_installed() {
    local unit="$1"
    local required_env="${2:-}"
    if ! unit_is_installed "$unit"; then
        echo "Skipping $unit (unit is not installed)"
        return 0
    fi
    if [ -n "$required_env" ] && [ ! -r "$required_env" ]; then
        echo "Skipping $unit (required env file is missing)"
        return 0
    fi
    systemctl restart "$unit"
    restarted_units+=("$unit")
    systemctl is-active --quiet "$unit"
}

rollback_code() {
    local status=$?
    trap - ERR
    set +e
    if [ -n "$backup_tool" ]; then
        rm -f -- "$backup_tool"
    fi
    if [ -n "$backup_tool_dir" ]; then
        rmdir -- "$backup_tool_dir" 2>/dev/null || true
    fi
    if [[ "$compile_dir" == /tmp/geminifree-compile.* ]] && [ -d "$compile_dir" ]; then
        rm -rf -- "$compile_dir"
    fi
    if [ "$checked_out_target" -eq 1 ]; then
        echo "Deploy failed; rolling code back to $previous_sha" >&2
        git checkout --detach "$previous_sha"
        copy_static
        for unit in "${restarted_units[@]}"; do
            systemctl restart "$unit"
        done
    fi
    echo "Runtime state was not restored automatically; inspect and use the guarded restore command if required." >&2
    exit "$status"
}
trap rollback_code ERR

git fetch --prune origin "$DEPLOY_BRANCH"
target_ref="${DEPLOY_SHA:-origin/${DEPLOY_BRANCH}}"
target_sha="$(git rev-parse --verify "${target_ref}^{commit}")"
if ! git merge-base --is-ancestor "$target_sha" "origin/${DEPLOY_BRANCH}"; then
    echo "Refusing deploy: target is not contained in origin/$DEPLOY_BRANCH" >&2
    exit 1
fi

# The currently deployed revision may predate the backup tool. Bootstrap the
# reviewed implementation from the immutable target SHA so the first migration
# to this deployment contract still backs up before checkout.
backup_tool_dir="$(mktemp -d)"
backup_tool="${backup_tool_dir}/runtime_backup.py"
git show "${target_sha}:tools/runtime_backup.py" > "$backup_tool"
chmod 0700 "$backup_tool"

install -d -m 0700 "$BACKUP_ROOT"
backup_dir="${BACKUP_ROOT}/runtime-$(date -u +%Y%m%dT%H%M%SZ)-${previous_sha:0:12}"
backup_env_args=(--env-file "$ENV_FILE")
if [ -r "$SELLER_ENV_FILE" ]; then
    backup_env_args+=(--env-file "$SELLER_ENV_FILE")
fi
"$PYTHON" "$backup_tool" backup \
    --root "$APP_ROOT" \
    "${backup_env_args[@]}" \
    --output "$backup_dir"
"$PYTHON" "$backup_tool" verify "$backup_dir"
rm -f -- "$backup_tool"
rmdir -- "$backup_tool_dir"
backup_tool=""
backup_tool_dir=""

git checkout --detach "$target_sha"
checked_out_target=1

"$PYTHON" tools/check_tracked_secrets.py
mapfile -d '' tracked_python < <(git ls-files -z -- '*.py')
if [ "${#tracked_python[@]}" -eq 0 ]; then
    echo "Refusing deploy: target contains no tracked Python files" >&2
    exit 1
fi
compile_dir="$(mktemp -d /tmp/geminifree-compile.XXXXXX)"
chown "$SERVICE_USER" "$compile_dir"
run_as_service env PYTHONPYCACHEPREFIX="$compile_dir" \
    "$PYTHON" -m py_compile "${tracked_python[@]}"
rm -rf -- "$compile_dir"
compile_dir=""
run_as_service "$PYTHON" tools/production_preflight.py --root "$APP_ROOT" --env-file "$ENV_FILE"
if [ -r "$SELLER_ENV_FILE" ]; then
    run_as_service "$PYTHON" tools/production_preflight.py --root "$APP_ROOT" --env-file "$SELLER_ENV_FILE"
fi

copy_static
restart_if_installed geminifree-bot
restart_if_installed geminifree-seller-bot "$SELLER_ENV_FILE"

trap - ERR
echo "Deploy complete: $target_sha"
echo "Verified runtime backup: $backup_dir"
