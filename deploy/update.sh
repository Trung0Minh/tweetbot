#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
branch="${DEPLOY_BRANCH:-main}"
service="${SYSTEMD_SERVICE:-discord-x-feed}"
python="$repo_root/.venv/bin/python"

log() {
    printf '[deploy] %s\n' "$*"
}

rollback() {
    local status=0
    log "Rolling back to $previous_revision"
    git reset --hard "$previous_revision" || status=1
    "$python" -m pip install --requirement requirements.txt || status=1
    sudo -n systemctl restart "$service" || status=1
    sleep 5
    systemctl is-active --quiet "$service" || status=1
    return "$status"
}

cd "$repo_root"
exec 9>"/tmp/discord-x-feed-deploy.lock"
if ! flock -n 9; then
    log "Another deployment is already running"
    exit 1
fi

if [[ ! -d .git ]]; then
    log "Deployment directory is not a Git checkout"
    exit 1
fi
if [[ ! -x "$python" ]]; then
    log "Missing virtual environment at $repo_root/.venv"
    exit 1
fi
git check-ref-format --branch "$branch" >/dev/null
if [[ ! "$service" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
    log "Invalid systemd service name"
    exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    log "Tracked files have local changes; refusing to overwrite them"
    exit 1
fi

current_branch="$(git branch --show-current)"
if [[ "$current_branch" != "$branch" ]]; then
    log "Expected branch $branch, found $current_branch"
    exit 1
fi

previous_revision="$(git rev-parse HEAD)"
log "Fetching origin/$branch"
git fetch --prune origin "$branch"
target_revision="$(git rev-parse "origin/$branch")"
git merge --ff-only "$target_revision"

deployment_failed=false
if ! "$python" -m pip install --requirement requirements.txt; then
    log "Dependency installation failed"
    deployment_failed=true
elif ! sudo -n systemctl restart "$service"; then
    log "Service restart failed"
    deployment_failed=true
else
    sleep 5
    if ! systemctl is-active --quiet "$service"; then
        log "Service did not remain active after restart"
        journalctl -u "$service" -n 50 --no-pager || true
        deployment_failed=true
    fi
fi

if [[ "$deployment_failed" == true ]]; then
    rollback || log "Rollback encountered an error; inspect the VM immediately"
    exit 1
fi

log "Deployed $target_revision successfully"
