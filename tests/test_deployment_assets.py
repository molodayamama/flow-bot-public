"""Offline source guards for production deployment assets."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        cls.consumer_unit = (
            ROOT / "deploy/systemd/geminifree-bot.service"
        ).read_text(encoding="utf-8")
        cls.seller_unit = (
            ROOT / "deploy/systemd/geminifree-seller-bot.service"
        ).read_text(encoding="utf-8")
        cls.nginx = (
            ROOT / "deploy/nginx/geminifree-locations.conf.example"
        ).read_text(encoding="utf-8")
        cls.web_nginx = (
            ROOT / "deploy/nginx/photozhab-web-app-locations.conf.example"
        ).read_text(encoding="utf-8")
        cls.consumer_runner = (
            ROOT / "deploy/bin/geminifree-bot-run"
        ).read_text(encoding="utf-8")
        cls.seller_runner = (
            ROOT / "deploy/bin/geminifree-seller-bot-run"
        ).read_text(encoding="utf-8")
        cls.requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    def test_security_dependency_floors_are_preserved(self) -> None:
        self.assertIn("aiogram>=3.29.1", self.requirements)
        self.assertIn("aiohttp>=3.14.1", self.requirements)
        self.assertIn("setuptools>=83.0.0", self.requirements)

    def test_deploy_uses_immutable_remote_target_and_no_pull(self) -> None:
        self.assertIn('git fetch --prune origin "$DEPLOY_BRANCH"', self.deploy)
        self.assertIn('git checkout --detach "$target_sha"', self.deploy)
        self.assertIn("git merge-base --is-ancestor", self.deploy)
        self.assertIn("DEPLOY_SHA must be a full 40-character commit id", self.deploy)
        self.assertIn('git rev-parse --verify "${target_ref}^{commit}"', self.deploy)
        self.assertNotIn("git pull", self.deploy)
        self.assertNotIn("git reset --hard", self.deploy)

    def test_deploy_reexecs_target_contract_before_state_mutation(self) -> None:
        compare = self.deploy.index('current_deploy_hash="$(git hash-object deploy.sh)"')
        backup = self.deploy.index('"$PYTHON" "$backup_tool" backup')
        self.assertLess(compare, backup)
        self.assertIn('target_deploy_hash="$(git rev-parse "${target_sha}:deploy.sh")"', self.deploy)
        self.assertIn('git show "${target_sha}:deploy.sh" > "$target_deploy"', self.deploy)
        self.assertIn("DEPLOY_BOOTSTRAPPED=1", self.deploy)
        self.assertIn('exit "$bootstrap_status"', self.deploy)
        self.assertNotIn("eval ", self.deploy)

    def test_backup_precedes_checkout_and_preflight_precedes_restart(self) -> None:
        backup = self.deploy.index('"$PYTHON" "$backup_tool" backup')
        checkout = self.deploy.index('git checkout --detach "$target_sha"')
        preflight = self.deploy.index("tools/production_preflight.py")
        restart = self.deploy.index("restart_if_installed geminifree-bot\n")
        self.assertLess(backup, checkout)
        self.assertLess(checkout, preflight)
        self.assertLess(preflight, restart)
        self.assertIn('"$PYTHON" "$backup_tool" verify', self.deploy)
        self.assertIn(
            'git show "${target_sha}:tools/runtime_backup.py" > "$backup_tool"',
            self.deploy,
        )
        self.assertLess(
            self.deploy.index('git show "${target_sha}:tools/runtime_backup.py"'),
            backup,
        )
        self.assertIn("rollback_code", self.deploy)
        self.assertIn('runuser -u "$SERVICE_USER"', self.deploy)

    def test_deploy_compiles_only_tracked_python_in_service_writable_cache(self) -> None:
        self.assertIn("git ls-files -z -- '*.py'", self.deploy)
        self.assertIn('PYTHONPYCACHEPREFIX="$compile_dir"', self.deploy)
        self.assertIn('"$PYTHON" -m py_compile "${tracked_python[@]}"', self.deploy)
        self.assertIn('chown "$SERVICE_USER" "$compile_dir"', self.deploy)
        self.assertNotIn('compileall -q', self.deploy)

    def test_deploy_publishes_search_discovery_assets(self) -> None:
        self.assertIn("deploy/photozhab/*.txt", self.deploy)
        self.assertIn("deploy/photozhab/*.xml", self.deploy)
        self.assertIn("deploy/photozhab/*.js", self.deploy)

    def test_deploy_recursively_publishes_nested_static_assets(self) -> None:
        self.assertIn("find deploy/photozhab/assets -type d -print0", self.deploy)
        self.assertIn("find deploy/photozhab/assets -type f -print0", self.deploy)
        self.assertIn('relative_dir="${source_dir#deploy/photozhab/}"', self.deploy)
        self.assertIn('relative_file="${source_file#deploy/photozhab/}"', self.deploy)
        self.assertIn('install -d -m 0755 "/var/www/photozhab/${relative_dir}"', self.deploy)
        self.assertIn('install -m 0644 "$source_file" "/var/www/photozhab/${relative_file}"', self.deploy)
        self.assertNotIn("install -m 0644 deploy/photozhab/assets/*", self.deploy)

    def test_main_site_proxies_only_bounded_public_web_api(self) -> None:
        self.assertIn("location /web/api/", self.web_nginx)
        self.assertIn("if ($host != photozhab.ru) { return 404; }", self.web_nginx)
        self.assertIn("client_max_body_size 12m", self.web_nginx)
        self.assertIn("proxy_pass http://127.0.0.1:8081", self.web_nginx)
        self.assertIn("proxy_read_timeout 480s", self.web_nginx)
        self.assertNotIn("/internal/", self.web_nginx)

    def test_services_limit_restart_loops_and_private_file_modes(self) -> None:
        for unit in (self.consumer_unit, self.seller_unit):
            self.assertIn("StartLimitIntervalSec=300", unit)
            self.assertIn("StartLimitBurst=5", unit)
            self.assertIn("UMask=0077", unit)

    def test_every_service_restart_runs_preflight_before_python(self) -> None:
        for runner, env_name in (
            (self.consumer_runner, ".env"),
            (self.seller_runner, ".env.seller"),
        ):
            preflight = runner.index("tools/production_preflight.py")
            process = runner.index("flow_bot.py")
            self.assertLess(preflight, process)
            self.assertIn('--env-file "$ENV_FILE"', runner)
            self.assertIn(env_name, runner)
            self.assertIn("set -euo pipefail", runner)

    def test_nginx_routes_public_ingress_but_limits_health_details(self) -> None:
        self.assertIn("location /robokassa/", self.nginx)
        self.assertIn("location = /max/webhook", self.nginx)
        self.assertIn("X-Max-Bot-Api-Secret", self.nginx)
        self.assertIn("location = /max/health", self.nginx)
        self.assertIn("deny all", self.nginx)


if __name__ == "__main__":
    unittest.main()
