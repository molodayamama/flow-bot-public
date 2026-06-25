"""Offline unit tests for proxy_supervisor (pure logic only — no gost, no net)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy_supervisor as ps


class NormalizeTests(unittest.TestCase):
    def test_bare_credentials_get_http_scheme(self) -> None:
        url = ps.normalize_upstream("user:pass@192.0.2.10:10000")
        self.assertEqual(url, "http://REDACTED:REDACTED@proxy.example.invalid:8080")

    def test_label_strips_credentials(self) -> None:
        url = ps.normalize_upstream("user:pass@192.0.2.10:10000")
        self.assertEqual(ps.proxy_label(url), "http://192.0.2.10:10000")
        self.assertNotIn("user", ps.proxy_label(url))
        self.assertNotIn("pass", ps.proxy_label(url))

    def test_empty_and_direct_rejected(self) -> None:
        for bad in ("", "   ", "direct", "off", "none"):
            with self.assertRaises(ps.ProxyError):
                ps.normalize_upstream(bad)

    def test_missing_port_rejected(self) -> None:
        with self.assertRaises(ps.ProxyError):
            ps.normalize_upstream("http://192.0.2.10")

    def test_bad_scheme_rejected(self) -> None:
        with self.assertRaises(ps.ProxyError):
            ps.normalize_upstream("ftp://192.0.2.10:10000")


class PortTests(unittest.TestCase):
    def test_first_free_skips_used(self) -> None:
        self.assertEqual(ps.first_free_port({8129, 8130}, 8129, 8140), 8131)

    def test_first_free_exhausted_raises(self) -> None:
        with self.assertRaises(ps.ProxyError):
            ps.first_free_port({8129, 8130}, 8129, 8130)

    def test_local_url(self) -> None:
        self.assertEqual(ps.local_url(8131), "http://127.0.0.1:8131")


class EgressParseTests(unittest.TestCase):
    def test_ipify_json(self) -> None:
        self.assertEqual(ps._parse_egress_ip('{"ip":"192.0.2.10"}'), "192.0.2.10")

    def test_ifconfig_plain(self) -> None:
        self.assertEqual(ps._parse_egress_ip("192.0.2.10\n"), "192.0.2.10")

    def test_cloudflare_trace(self) -> None:
        trace = "fl=1\nip=192.0.2.10\nts=123\n"
        self.assertEqual(ps._parse_egress_ip(trace), "192.0.2.10")

    def test_empty(self) -> None:
        self.assertIsNone(ps._parse_egress_ip("  "))


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "local_proxies.json")

    def test_persist_and_reload_entries(self) -> None:
        sup = ps.LocalProxySupervisor(state_path=self.path)
        sup._entries[8131] = {
            "port": 8131,
            "upstream": "http://REDACTED:REDACTED@proxy.example.invalid:8080",
            "label": "http://1.2.3.4:10000",
            "created_at": 123.0,
        }
        sup._save()

        reloaded = ps.LocalProxySupervisor(state_path=self.path)
        self.assertIn(8131, reloaded._entries)
        self.assertEqual(reloaded._entries[8131]["upstream"],
                         "http://REDACTED:REDACTED@proxy.example.invalid:8080")

    def test_list_status_has_no_credentials(self) -> None:
        sup = ps.LocalProxySupervisor(state_path=self.path)
        sup._entries[8131] = {
            "port": 8131,
            "upstream": "http://REDACTED:REDACTED@proxy.example.invalid:8080",
            "label": "http://1.2.3.4:10000",
            "created_at": 1.0,
        }
        rows = sup.list_status()
        blob = repr(rows)
        self.assertNotIn("secretpw", blob)
        self.assertNotIn("upstream", blob)
        self.assertEqual(rows[0]["local_url"], "http://127.0.0.1:8131")
        self.assertEqual(rows[0]["label"], "http://1.2.3.4:10000")

    def test_ports_view_shape_and_no_creds(self) -> None:
        sup = ps.LocalProxySupervisor(state_path=self.path)
        sup._entries[8131] = {
            "port": 8131, "upstream": "http://REDACTED:REDACTED@proxy.example.invalid:8080",
            "label": "http://1.2.3.4:10000", "created_at": 1.0,
        }
        view = sup.ports_view()
        self.assertIn(8131, view["used_ports"])
        self.assertIn("suggested_free", view)
        self.assertNotIn("p@1.2.3.4", repr(view))

    def test_teardown_unknown_port_is_false(self) -> None:
        sup = ps.LocalProxySupervisor(state_path=self.path)
        self.assertFalse(sup.teardown(9999))

    def test_teardown_removes_entry(self) -> None:
        sup = ps.LocalProxySupervisor(state_path=self.path)
        sup._entries[8131] = {
            "port": 8131, "upstream": "http://REDACTED:REDACTED@proxy.example.invalid:8080",
            "label": "http://1.2.3.4:10000", "created_at": 1.0,
        }
        sup._save()
        self.assertTrue(sup.teardown(8131))
        self.assertNotIn(8131, sup._entries)
        # persisted removal survives reload
        self.assertNotIn(8131, ps.LocalProxySupervisor(state_path=self.path)._entries)


if __name__ == "__main__":
    unittest.main()
