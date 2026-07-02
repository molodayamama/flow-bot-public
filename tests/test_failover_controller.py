import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import failover_controller as fc


class FakeRegruTransport:
    def __init__(self, records=None):
        self.records = records or {"@": ["192.0.2.10"], "pay": ["192.0.2.10"]}
        self.calls = []

    def __call__(self, method, payload):
        safe_payload = {k: v for k, v in payload.items() if k not in {"username", "password"}}
        self.calls.append((method, safe_payload))
        if method == "zone/get_resource_records":
            rrs = []
            for sub, values in self.records.items():
                for value in values:
                    rrs.append({"rectype": "A", "subname": sub, "content": value})
            return {"result": "success", "answer": {"domains": [{"rrs": rrs}]}}
        if method == "zone/update_soa":
            return {"result": "success", "answer": {"domains": [{"result": "success"}]}}
        if method == "zone/remove_record":
            sub = payload["subdomain"]
            value = payload["content"]
            self.records[sub] = [v for v in self.records.get(sub, []) if v != value]
            return {"result": "success", "answer": {"domains": [{"result": "success"}]}}
        if method == "zone/add_alias":
            sub = payload["subdomain"]
            value = payload["ipaddr"]
            self.records.setdefault(sub, [])
            if value not in self.records[sub]:
                self.records[sub].append(value)
            return {"result": "success", "answer": {"domains": [{"result": "success"}]}}
        raise AssertionError(method)


def make_config(tmp_state):
    return fc.Config(
        state_path=tmp_state,
        regru_username="user",
        regru_password="super-secret",
        failover_failures=3,
        failback_successes=2,
        sync_interval_sec=999999,
    )


class DnsSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")

    def test_switch_dns_replaces_root_and_pay_idempotently(self):
        transport = FakeRegruTransport()
        cfg = make_config(self.state)
        client = fc.RegruClient(cfg, transport=transport)

        records = fc.switch_dns(client, cfg.nl_ip)

        self.assertEqual(records["@"], [cfg.nl_ip])
        self.assertEqual(records["pay"], [cfg.nl_ip])
        methods = [m for m, _ in transport.calls]
        self.assertEqual(methods.count("zone/remove_record"), 2)
        self.assertEqual(methods.count("zone/add_alias"), 2)
        self.assertIn("zone/update_soa", methods)

        transport.calls.clear()
        records = fc.switch_dns(client, cfg.nl_ip)

        self.assertEqual(records["@"], [cfg.nl_ip])
        self.assertEqual(records["pay"], [cfg.nl_ip])
        methods = [m for m, _ in transport.calls]
        self.assertNotIn("zone/remove_record", methods)
        self.assertNotIn("zone/add_alias", methods)

    def test_switch_dns_keeps_target_and_removes_extra_a_records(self):
        cfg = make_config(self.state)
        transport = FakeRegruTransport(records={
            "@": [cfg.fi_ip, cfg.nl_ip],
            "pay": [cfg.nl_ip],
        })
        client = fc.RegruClient(cfg, transport=transport)

        records = fc.switch_dns(client, cfg.nl_ip)

        self.assertEqual(records["@"], [cfg.nl_ip])
        self.assertEqual(records["pay"], [cfg.nl_ip])
        remove_calls = [p for m, p in transport.calls if m == "zone/remove_record"]
        self.assertEqual(remove_calls[0]["content"], cfg.fi_ip)


class TickThresholdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")
        self.cfg = make_config(self.state)
        self.runner = fc.Runner(dry_run=False, echo=False)
        self.client = mock.Mock()

    def test_tick_fails_over_after_configured_failures(self):
        with mock.patch.object(fc, "check_fi_health", return_value={"ok": False}), \
             mock.patch.object(fc, "failover_to_nl") as failover:
            failover.side_effect = lambda cfg, runner, client, state: {**state, "mode": "fallback"}

            fc.tick(self.cfg, self.runner, self.client)
            fc.tick(self.cfg, self.runner, self.client)
            self.assertEqual(failover.call_count, 0)
            state = fc.tick(self.cfg, self.runner, self.client)

        self.assertEqual(failover.call_count, 1)
        self.assertEqual(state["mode"], "fallback")

    def test_tick_fails_back_after_configured_successes(self):
        fc.save_state(self.state, {"mode": "fallback", "fi_successes": 0}, dry_run=False)
        with mock.patch.object(fc, "check_fi_health", return_value={"ok": True}), \
             mock.patch.object(fc, "failback_to_fi") as failback:
            failback.side_effect = lambda cfg, runner, client, state: {**state, "mode": "primary"}

            fc.tick(self.cfg, self.runner, self.client)
            self.assertEqual(failback.call_count, 0)
            state = fc.tick(self.cfg, self.runner, self.client)

        self.assertEqual(failback.call_count, 1)
        self.assertEqual(state["mode"], "primary")


class RedactionTests(unittest.TestCase):
    def test_redact_hides_credentials(self):
        text = (
            'REGRU_USERNAME=alice REGRU_PASSWORD=secret '
            'http://REDACTED:REDACTED@proxy.example.invalid:8080/path '
            '{"username":"alice","password":"secret"}'
        )

        redacted = fc.redact(text)

        self.assertNotIn("secret", redacted)
        self.assertNotIn("user:pass", redacted)
        self.assertNotIn("alice", redacted)
        self.assertIn("<redacted>", redacted)


if __name__ == "__main__":
    unittest.main()
