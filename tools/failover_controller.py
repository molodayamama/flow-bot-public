#!/usr/bin/env python3
"""Active-passive failover controller for photozhab.ru.

Designed to run on the NL standby host. It keeps FI as primary, switches DNS
and starts the consumer bot on NL when FI is down, then fails back after FI is
stable again.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


DEFAULT_CONFIG = "/etc/geminifree/failover.env"
DEFAULT_STATE = "/var/lib/geminifree-failover/state.json"
REGRU_BASE = "https://api.reg.ru/api/regru2"
MUTATING_COMMANDS = {
    "tick",
    "failover-to-nl",
    "failback-to-fi",
    "sync",
}


class FailoverError(RuntimeError):
    """Expected failover-controller error."""


@dataclasses.dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    def __init__(self, *, dry_run: bool = False, echo: bool = True):
        self.dry_run = dry_run
        self.echo = echo

    def run(
        self,
        args: list[str],
        *,
        timeout: int = 30,
        mutating: bool = False,
        check: bool = False,
    ) -> CommandResult:
        if self.dry_run and mutating:
            self._log("DRY-RUN skip: " + " ".join(shlex.quote(a) for a in args))
            return CommandResult(args=args, returncode=0, skipped=True)
        proc = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        res = CommandResult(args=args, returncode=proc.returncode,
                            stdout=proc.stdout, stderr=proc.stderr)
        if check and not res.ok:
            raise FailoverError(
                f"command failed rc={res.returncode}: {redact(' '.join(args))}\n"
                f"stdout={redact(res.stdout)[:800]}\n"
                f"stderr={redact(res.stderr)[:800]}"
            )
        return res

    def _log(self, msg: str) -> None:
        if self.echo:
            print(redact(msg), file=sys.stderr)


@dataclasses.dataclass
class Config:
    domain: str = "photozhab.ru"
    dns_records: tuple[str, ...] = ("@", "pay")
    fi_ip: str = "192.0.2.10"
    nl_ip: str = "192.0.2.10"
    fi_ssh_host: str = "root@192.0.2.10"
    services: tuple[str, ...] = ("geminifree-bot",)
    state_path: str = DEFAULT_STATE
    regru_username: str = ""
    regru_password: str = ""
    regru_base_url: str = REGRU_BASE
    dns_ttl: str = "5m"
    failover_failures: int = 3
    failback_successes: int = 10
    ssh_connect_timeout: int = 8
    command_timeout: int = 30
    health_timeout: int = 12
    sync_interval_sec: int = 300
    sync_files: tuple[str, ...] = (
        "/opt/geminifree/.env",
        "/opt/geminifree/.env.seller",
        "/opt/geminifree/.env_flow",
        "/opt/geminifree/api_config.json",
        "/opt/geminifree/account_metadata.json",
        "/opt/geminifree/config_override.json",
        "/opt/geminifree/edit_capture.json",
        "/opt/geminifree/edit_capture_raw.json",
        "/opt/geminifree/flow_accounts_state.json",
        "/opt/geminifree/payments.json",
        "/opt/geminifree/recent_images.json",
        "/opt/geminifree/upload_capture.json",
        "/opt/geminifree/upscale_capture.json",
        "/opt/geminifree/user_credits.json",
        "/opt/geminifree/user_credits_seller.json",
        "/opt/geminifree/user_projects.json",
        "/etc/nginx/sites-available/photozhab",
        "/etc/nginx/sites-enabled/photozhab",
        "/etc/nginx/snippets",
        "/etc/nginx/.htpasswd_admin",
        "/var/www/photozhab",
    )

    @classmethod
    def load(cls, path: str = DEFAULT_CONFIG) -> "Config":
        raw: dict[str, str] = {}
        if path and os.path.exists(path):
            raw.update(parse_env_file(path))
        raw.update({k: v for k, v in os.environ.items() if k.startswith("FAILOVER_") or k.startswith("REGRU_")})

        def get(name: str, default: str) -> str:
            return raw.get(name, raw.get("FAILOVER_" + name, default))

        domain = get("DOMAIN", cls.domain)
        fi_ip = get("FI_IP", cls.fi_ip)
        nl_ip = get("NL_IP", cls.nl_ip)
        records = tuple(split_csv(get("DNS_RECORDS", ",".join(cls.dns_records)))) or cls.dns_records
        services = tuple(split_csv(get("SERVICES", ",".join(cls.services)))) or cls.services
        sync_files = tuple(split_csv(get("SYNC_FILES", ",".join(cls.sync_files)))) or cls.sync_files
        return cls(
            domain=domain,
            dns_records=records,
            fi_ip=fi_ip,
            nl_ip=nl_ip,
            fi_ssh_host=get("FI_SSH_HOST", f"root@{fi_ip}"),
            services=services,
            state_path=get("STATE_PATH", DEFAULT_STATE),
            regru_username=raw.get("REGRU_USERNAME", raw.get("FAILOVER_REGRU_USERNAME", "")),
            regru_password=raw.get("REGRU_PASSWORD", raw.get("FAILOVER_REGRU_PASSWORD", "")),
            regru_base_url=get("REGRU_BASE_URL", REGRU_BASE).rstrip("/"),
            dns_ttl=get("DNS_TTL", cls.dns_ttl),
            failover_failures=to_int(get("FAILOVER_FAILURES", str(cls.failover_failures)), cls.failover_failures),
            failback_successes=to_int(get("FAILBACK_SUCCESSES", str(cls.failback_successes)), cls.failback_successes),
            ssh_connect_timeout=to_int(get("SSH_CONNECT_TIMEOUT", str(cls.ssh_connect_timeout)), cls.ssh_connect_timeout),
            command_timeout=to_int(get("COMMAND_TIMEOUT", str(cls.command_timeout)), cls.command_timeout),
            health_timeout=to_int(get("HEALTH_TIMEOUT", str(cls.health_timeout)), cls.health_timeout),
            sync_interval_sec=to_int(get("SYNC_INTERVAL_SEC", str(cls.sync_interval_sec)), cls.sync_interval_sec),
            sync_files=sync_files,
        )

    def require_regru(self) -> None:
        if not self.regru_username or not self.regru_password:
            raise FailoverError(
                f"REG.RU credentials missing. Create {DEFAULT_CONFIG} with "
                "REGRU_USERNAME and REGRU_PASSWORD (mode 600)."
            )


class RegruClient:
    def __init__(
        self,
        config: Config,
        *,
        dry_run: bool = False,
        transport: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.config = config
        self.dry_run = dry_run
        self.transport = transport or self._post

    def call(self, method: str, payload: dict[str, Any], *, mutating: bool = False) -> dict[str, Any]:
        self.config.require_regru()
        data = {
            "username": self.config.regru_username,
            "password": self.config.regru_password,
            **payload,
        }
        if self.dry_run and mutating:
            return {"result": "dry-run", "answer": {"method": method}}
        return self.transport(method, data)

    def _post(self, method: str, input_data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.regru_base_url}/{method}"
        form = urllib.parse.urlencode({
            "input_format": "json",
            "input_data": json.dumps(input_data, ensure_ascii=False),
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=form,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.command_timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise FailoverError(f"REG.RU API call failed for {method}: {exc}") from exc
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise FailoverError(f"REG.RU API returned non-JSON for {method}") from exc

    def get_records(self) -> dict[str, list[str]]:
        payload = {"domains": [{"dname": self.config.domain}]}
        data = self.call("zone/get_resource_records", payload)
        return extract_a_records(data)

    def update_soa(self) -> None:
        payload = {
            "domains": [{"dname": self.config.domain}],
            "ttl": self.config.dns_ttl,
            "minimum_ttl": self.config.dns_ttl,
        }
        self._expect_success("zone/update_soa", self.call("zone/update_soa", payload, mutating=True))

    def remove_a(self, subdomain: str, ipaddr: str) -> None:
        payload = {
            "domains": [{"dname": self.config.domain}],
            "subdomain": subdomain,
            "record_type": "A",
            "content": ipaddr,
        }
        self._expect_success("zone/remove_record", self.call("zone/remove_record", payload, mutating=True))

    def add_a(self, subdomain: str, ipaddr: str) -> None:
        payload = {
            "domains": [{"dname": self.config.domain}],
            "subdomain": subdomain,
            "ipaddr": ipaddr,
        }
        self._expect_success("zone/add_alias", self.call("zone/add_alias", payload, mutating=True))

    @staticmethod
    def _expect_success(method: str, data: dict[str, Any]) -> None:
        if data.get("result") in {"success", "dry-run"}:
            for domain in ((data.get("answer") or {}).get("domains") or []):
                if domain.get("result") and domain.get("result") != "success":
                    raise FailoverError(
                        f"REG.RU {method} domain error: "
                        f"{redact(json.dumps(domain, ensure_ascii=False))[:800]}"
                    )
                for action in domain.get("action_list") or []:
                    if action.get("result") and action.get("result") != "success":
                        raise FailoverError(
                            f"REG.RU {method} action error: "
                            f"{redact(json.dumps(action, ensure_ascii=False))[:800]}"
                        )
            return
        raise FailoverError(f"REG.RU {method} did not succeed: {redact(json.dumps(data, ensure_ascii=False))[:800]}")


def parse_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = strip_inline_comment(value.strip())
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            out[key] = value
    return out


def strip_inline_comment(value: str) -> str:
    if not value or value[0] in {"'", '"'}:
        return value
    return value.split(" #", 1)[0].strip()


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def to_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def redact(value: str) -> str:
    if not value:
        return value
    redacted = re.sub(r"(?i)(password|passwd|token|secret|key)=([^&\s]+)", r"\1=<redacted>", value)
    redacted = re.sub(r"(?i)(REGRU_PASSWORD|REGRU_USERNAME)=([^\s]+)", r"\1=<redacted>", redacted)
    redacted = re.sub(r"(?P<scheme>https?://)(?P<user>[^:/@\s]+):(?P<pw>[^@\s]+)@", r"\g<scheme><redacted>@", redacted)
    redacted = re.sub(r"(\"password\"\s*:\s*\")[^\"]+\"", r"\1<redacted>\"", redacted)
    redacted = re.sub(r"(\"username\"\s*:\s*\")[^\"]+\"", r"\1<redacted>\"", redacted)
    return redacted


def extract_a_records(data: dict[str, Any]) -> dict[str, list[str]]:
    records: dict[str, list[str]] = {}
    domains = (((data.get("answer") or {}).get("domains")) or [])
    for domain in domains:
        for row in domain.get("rrs") or []:
            rectype = str(row.get("rectype") or row.get("record_type") or "").upper()
            if rectype != "A":
                continue
            sub = str(row.get("subname") or row.get("subdomain") or "@")
            content = str(row.get("content") or row.get("ipaddr") or "")
            if content:
                records.setdefault(sub, []).append(content)
    return records


def switch_dns(client: RegruClient, target_ip: str) -> dict[str, list[str]]:
    current = client.get_records()
    client.update_soa()
    for sub in client.config.dns_records:
        values = current.get(sub, [])
        for old_ip in sorted(set(values)):
            if old_ip != target_ip:
                client.remove_a(sub, old_ip)
        if target_ip not in values:
            client.add_a(sub, target_ip)
    return client.get_records()


def load_state(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        state = {}
    except json.JSONDecodeError:
        state = {"mode": "unknown"}
    state.setdefault("mode", "primary")
    state.setdefault("fi_failures", 0)
    state.setdefault("fi_successes", 0)
    state.setdefault("last_sync", 0)
    return state


def save_state(path: str, state: dict[str, Any], *, dry_run: bool = False) -> None:
    state["updated_at"] = int(time.time())
    if dry_run:
        print("DRY-RUN state: " + redact(json.dumps(state, ensure_ascii=False, sort_keys=True)))
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, p)


def ssh_args(config: Config, host: str, remote_command: str) -> list[str]:
    return [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={config.ssh_connect_timeout}",
        host,
        remote_command,
    ]


def check_fi_health(config: Config, runner: Runner) -> dict[str, Any]:
    remote = (
        "set -e; "
        "systemctl is-active --quiet geminifree-bot; "
        "curl -fsS --max-time 5 http://127.0.0.1:8081/api/admin/ping >/dev/null"
    )
    ssh = runner.run(
        ssh_args(config, config.fi_ssh_host, remote),
        timeout=config.health_timeout,
    )
    direct = runner.run(
        [
            "curl",
            "-fsS",
            "--max-time", "8",
            "--connect-timeout", str(config.ssh_connect_timeout),
            "--resolve", f"{config.domain}:443:{config.fi_ip}",
            f"https://{config.domain}/",
            "-o", os.devnull,
        ],
        timeout=config.health_timeout,
    )
    return {
        "ok": ssh.ok and direct.ok,
        "ssh_service_api": ssh.returncode,
        "direct_https": direct.returncode,
    }


def ensure_nl_ready(config: Config, runner: Runner) -> None:
    commands = [
        (["nginx", "-t"], "nginx config"),
        (["test", "-s", "/etc/nginx/.htpasswd_admin"], "admin htpasswd"),
        (["systemctl", "start", "xvfb99.service"], "xvfb99"),
    ]
    for args, label in commands:
        res = runner.run(args, timeout=config.command_timeout, mutating=(args[0] == "systemctl"))
        if not res.ok:
            raise FailoverError(f"NL readiness failed: {label}")

    gost_units = list_gost_units(runner)
    if gost_units:
        res = runner.run(["systemctl", "start", *gost_units], timeout=config.command_timeout, mutating=True)
        if not res.ok:
            raise FailoverError("failed to start gost units")


def list_gost_units(runner: Runner) -> list[str]:
    res = runner.run(
        ["systemctl", "list-unit-files", "gost-*", "--no-legend", "--no-pager"],
        timeout=20,
    )
    units: list[str] = []
    for line in res.stdout.splitlines():
        parts = line.split()
        if parts and parts[0].startswith("gost-") and parts[0].endswith(".service"):
            units.append(parts[0])
    return units


def start_services(config: Config, runner: Runner, *, remote: str | None = None) -> None:
    for service in config.services:
        args = ["systemctl", "start", service]
        res = run_maybe_remote(config, runner, args, remote=remote, mutating=True)
        if not res.ok:
            raise FailoverError(f"failed to start {service}")


def stop_services(config: Config, runner: Runner, *, remote: str | None = None) -> None:
    for service in config.services:
        args = ["systemctl", "stop", service]
        run_maybe_remote(config, runner, args, remote=remote, mutating=True)


def run_maybe_remote(
    config: Config,
    runner: Runner,
    args: list[str],
    *,
    remote: str | None,
    mutating: bool,
) -> CommandResult:
    if remote:
        cmd = " ".join(shlex.quote(a) for a in args)
        return runner.run(ssh_args(config, remote, cmd), timeout=config.command_timeout, mutating=mutating)
    return runner.run(args, timeout=config.command_timeout, mutating=mutating)


def failover_to_nl(config: Config, runner: Runner, client: RegruClient, state: dict[str, Any]) -> dict[str, Any]:
    ensure_nl_ready(config, runner)
    start_services(config, runner)
    records = switch_dns(client, config.nl_ip)
    state.update({
        "mode": "fallback",
        "active": "nl",
        "last_dns_target": "nl",
        "fi_failures": 0,
        "fi_successes": 0,
        "last_action": "failover-to-nl",
        "records": sanitize_records(records),
    })
    save_state(config.state_path, state, dry_run=runner.dry_run)
    return state


def failback_to_fi(config: Config, runner: Runner, client: RegruClient, state: dict[str, Any]) -> dict[str, Any]:
    stop_services(config, runner, remote=config.fi_ssh_host)
    sync_state(config, runner, direction="nl-to-fi")
    start_services(config, runner, remote=config.fi_ssh_host)
    health = check_fi_health(config, runner)
    if not health["ok"]:
        raise FailoverError(f"FI did not verify after start: {health}")
    records = switch_dns(client, config.fi_ip)
    stop_services(config, runner)
    state.update({
        "mode": "primary",
        "active": "fi",
        "last_dns_target": "fi",
        "fi_failures": 0,
        "fi_successes": 0,
        "last_action": "failback-to-fi",
        "records": sanitize_records(records),
    })
    save_state(config.state_path, state, dry_run=runner.dry_run)
    return state


def sanitize_records(records: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: sorted(set(v)) for k, v in records.items()}


def tick(config: Config, runner: Runner, client: RegruClient) -> dict[str, Any]:
    state = load_state(config.state_path)
    health = check_fi_health(config, runner)
    state["last_fi_health"] = health
    now = int(time.time())
    if state.get("mode") == "fallback":
        if health["ok"]:
            state["fi_successes"] = int(state.get("fi_successes") or 0) + 1
        else:
            state["fi_successes"] = 0
        if state["fi_successes"] >= config.failback_successes:
            return failback_to_fi(config, runner, client, state)
    else:
        if health["ok"]:
            state["fi_failures"] = 0
            if now - int(state.get("last_sync") or 0) >= config.sync_interval_sec:
                try:
                    sync_state(config, runner, direction="fi-to-nl")
                    state["last_sync"] = now
                except FailoverError as exc:
                    state["last_sync_error"] = str(exc)
        else:
            state["fi_failures"] = int(state.get("fi_failures") or 0) + 1
        if state["fi_failures"] >= config.failover_failures:
            return failover_to_nl(config, runner, client, state)
    save_state(config.state_path, state, dry_run=runner.dry_run)
    return state


def create_sync_tar(config: Config, runner: Runner, *, remote: str | None) -> str:
    script = render_create_tar_script(config.sync_files)
    if remote:
        # The generic runner cannot pipe stdin; use subprocess directly here.
        if runner.dry_run:
            print(f"DRY-RUN would create sync tar on {remote}", file=sys.stderr)
            return "/tmp/geminifree-failover-dry-run.tar"
        proc = subprocess.run(
            ssh_args(config, remote, "bash -s"),
            input=script,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise FailoverError(f"remote sync tar failed: {redact(proc.stderr)[:800]}")
        return proc.stdout.strip().splitlines()[-1]

    if runner.dry_run:
        print("DRY-RUN would create local sync tar", file=sys.stderr)
        return "/tmp/geminifree-failover-dry-run.tar"
    proc = subprocess.run(["bash", "-s"], input=script, text=True,
                          capture_output=True, timeout=120, check=False)
    if proc.returncode != 0:
        raise FailoverError(f"local sync tar failed: {redact(proc.stderr)[:800]}")
    return proc.stdout.strip().splitlines()[-1]


def render_create_tar_script(sync_files: Iterable[str]) -> str:
    files_blob = "\n".join(path for path in sync_files)
    return f"""set -euo pipefail
tmp=$(mktemp -d /tmp/geminifree-failover-sync.XXXXXX)
payload="$tmp/payload"
mkdir -p "$payload"
if [ -f /opt/geminifree/metrics.db ]; then
  mkdir -p "$payload/opt/geminifree"
  sqlite3 /opt/geminifree/metrics.db ".backup '$payload/opt/geminifree/metrics.db'"
fi
while IFS= read -r p; do
  [ -z "$p" ] && continue
  [ -e "$p" ] || continue
  cp -a --parents "$p" "$payload"/
done <<'EOF'
{files_blob}
EOF
tar -C "$payload" -cpf "$tmp/state.tar" .
printf '%s\\n' "$tmp/state.tar"
"""


def extract_tar(config: Config, runner: Runner, tar_path: str, *, remote: str | None) -> None:
    if remote:
        remote_tar = f"/tmp/geminifree-failover-{int(time.time())}.tar"
        if not runner.dry_run:
            runner.run(["scp", tar_path, f"{remote}:{remote_tar}"], timeout=120, mutating=True, check=True)
        cmd = (
            f"tar -C / -xpf {shlex.quote(remote_tar)} && "
            "chown -R bot:bot /opt/geminifree && "
            "rm -f " + shlex.quote(remote_tar)
        )
        runner.run(ssh_args(config, remote, cmd), timeout=120, mutating=True, check=True)
        return
    runner.run(["tar", "-C", "/", "-xpf", tar_path], timeout=120, mutating=True, check=True)
    runner.run(["chown", "-R", "bot:bot", "/opt/geminifree"], timeout=60, mutating=True)


def sync_state(config: Config, runner: Runner, *, direction: str) -> None:
    if direction == "fi-to-nl":
        source_remote = config.fi_ssh_host
        dest_remote = None
    elif direction == "nl-to-fi":
        source_remote = None
        dest_remote = config.fi_ssh_host
    else:
        raise FailoverError("sync direction must be fi-to-nl or nl-to-fi")
    tar_path = create_sync_tar(config, runner, remote=source_remote)
    local_tar = tar_path
    if source_remote and not runner.dry_run:
        local_tar = str(Path(tempfile.gettempdir()) / f"geminifree-sync-{int(time.time())}.tar")
        runner.run(["scp", f"{source_remote}:{tar_path}", local_tar], timeout=120, mutating=False, check=True)
        runner.run(ssh_args(config, source_remote, f"rm -f {shlex.quote(tar_path)}"), timeout=30, mutating=True)
    extract_tar(config, runner, local_tar, remote=dest_remote)
    if source_remote and not runner.dry_run:
        try:
            os.remove(local_tar)
        except FileNotFoundError:
            pass


def print_status(config: Config, runner: Runner, client: RegruClient, *, include_dns: bool) -> dict[str, Any]:
    state = load_state(config.state_path)
    fi = check_fi_health(config, runner)
    local_services: dict[str, str] = {}
    for service in (*config.services, "geminifree-seller-bot"):
        res = runner.run(["systemctl", "is-active", service], timeout=10)
        local_services[service] = res.stdout.strip() or "unknown"
    payload: dict[str, Any] = {
        "state": state,
        "fi_health": fi,
        "nl_services": local_services,
    }
    if include_dns:
        payload["dns_records"] = sanitize_records(client.get_records())
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    p_status = sub.add_parser("status")
    p_status.add_argument("--dry-run", dest="sub_dry_run", action="store_true")
    p_dns = sub.add_parser("dns-status")
    p_dns.add_argument("--dry-run", dest="sub_dry_run", action="store_true")
    p_tick = sub.add_parser("tick")
    p_tick.add_argument("--dry-run", dest="sub_dry_run", action="store_true")
    p_failover = sub.add_parser("failover-to-nl")
    p_failover.add_argument("--dry-run", dest="sub_dry_run", action="store_true")
    p_failover.add_argument("--force", action="store_true")
    p_failback = sub.add_parser("failback-to-fi")
    p_failback.add_argument("--dry-run", dest="sub_dry_run", action="store_true")
    p_failback.add_argument("--force", action="store_true")
    p_sync = sub.add_parser("sync")
    p_sync.add_argument("--dry-run", dest="sub_dry_run", action="store_true")
    p_sync.add_argument("--direction", choices=("fi-to-nl", "nl-to-fi"), required=True)
    args = parser.parse_args(argv)

    config = Config.load(args.config)
    dry_run = bool(args.dry_run or getattr(args, "sub_dry_run", False))
    runner = Runner(dry_run=dry_run)
    client = RegruClient(config, dry_run=dry_run)
    try:
        if args.command == "status":
            print_status(config, runner, client, include_dns=False)
        elif args.command == "dns-status":
            print(json.dumps(sanitize_records(client.get_records()), ensure_ascii=False, indent=2, sort_keys=True))
        elif args.command == "tick":
            print(json.dumps(tick(config, runner, client), ensure_ascii=False, indent=2, sort_keys=True))
        elif args.command == "failover-to-nl":
            state = load_state(config.state_path)
            if not args.force:
                health = check_fi_health(config, runner)
                if health["ok"]:
                    raise FailoverError("FI is healthy; use --force to fail over anyway")
            print(json.dumps(failover_to_nl(config, runner, client, state), ensure_ascii=False, indent=2, sort_keys=True))
        elif args.command == "failback-to-fi":
            state = load_state(config.state_path)
            if not args.force:
                health = check_fi_health(config, runner)
                if not health["ok"]:
                    raise FailoverError("FI is not healthy; cannot fail back without --force")
            print(json.dumps(failback_to_fi(config, runner, client, state), ensure_ascii=False, indent=2, sort_keys=True))
        elif args.command == "sync":
            sync_state(config, runner, direction=args.direction)
            print(json.dumps({"ok": True, "direction": args.direction, "dry_run": dry_run}, sort_keys=True))
        else:
            parser.error("unknown command")
    except FailoverError as exc:
        print("ERROR: " + redact(str(exc)), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
