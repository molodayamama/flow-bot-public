"""Unit tests for accounts.projects.ProjectManager — no flow_bot import.

Exercises the sticky per-account project lifecycle and the auto-disable circuit
breaker in isolation, proving the core logic moved cleanly out of the monolith.
"""

from __future__ import annotations

import asyncio
import unittest

from accounts.projects import ProjectManager


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Store:
    def __init__(self, initial=None):
        self.data = dict(initial or {})

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value


class _Keeper:
    def __init__(self, pid="proj-new", boom=False):
        self.pid = pid
        self.boom = boom
        self.calls = 0

    async def create_new_project(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("create failed")
        return self.pid


class _Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def exception(self, *a, **k): pass


def _mgr(*, store=None, keeper=None, account_for=None, default="acc-def",
         max_failures=3, enabled=True):
    kp = keeper or _Keeper()
    return ProjectManager(
        project_store=store or _Store(),
        account_for=account_for or (lambda uid: "acc-def"),
        keeper_for_acc=lambda acc: kp,
        default_account_id=default,
        max_failures=max_failures,
        per_user_enabled=enabled,
        log=_Log(),
    ), kp


class ProjectKeyTests(unittest.TestCase):
    def test_key_format(self):
        self.assertEqual(ProjectManager.project_key("acc-1", 42), "acc-1:42")


class EnsureTests(unittest.TestCase):
    def test_returns_existing_without_creating(self):
        store = _Store({"acc-def:7": "proj-old"})
        mgr, kp = _mgr(store=store)
        self.assertEqual(run(mgr.ensure(7)), "proj-old")
        self.assertEqual(kp.calls, 0)

    def test_migrates_legacy_default_account_record(self):
        store = _Store({7: "legacy-proj"})
        mgr, kp = _mgr(store=store, default="acc-def", account_for=lambda uid: "acc-def")
        self.assertEqual(run(mgr.ensure(7)), "legacy-proj")
        self.assertEqual(store.get("acc-def:7"), "legacy-proj")
        self.assertEqual(kp.calls, 0)

    def test_no_legacy_migration_on_non_default_account(self):
        store = _Store({7: "legacy-proj"})
        mgr, kp = _mgr(store=store, keeper=_Keeper(pid="fresh"))
        got = run(mgr.ensure(7, account_id="acc-2"))
        self.assertEqual(got, "fresh")  # legacy ignored, new project created
        self.assertEqual(store.get("acc-2:7"), "fresh")

    def test_creates_and_persists_new_project(self):
        store = _Store()
        mgr, kp = _mgr(store=store, keeper=_Keeper(pid="proj-x"))
        self.assertEqual(run(mgr.ensure(9, account_id="acc-9")), "proj-x")
        self.assertEqual(store.get("acc-9:9"), "proj-x")
        self.assertEqual(kp.calls, 1)

    def test_disabled_returns_none_without_creating(self):
        mgr, kp = _mgr(enabled=False)
        self.assertIsNone(run(mgr.ensure(1, account_id="acc-1")))
        self.assertEqual(kp.calls, 0)

    def test_create_failure_returns_none(self):
        mgr, kp = _mgr(keeper=_Keeper(boom=True))
        self.assertIsNone(run(mgr.ensure(1, account_id="acc-1")))

    def test_circuit_breaker_disables_after_max_failures(self):
        mgr, kp = _mgr(keeper=_Keeper(boom=True), max_failures=2)
        self.assertTrue(mgr.enabled)
        run(mgr.ensure(1, account_id="acc-1"))
        self.assertTrue(mgr.enabled)          # 1 failure, still on
        run(mgr.ensure(2, account_id="acc-2"))
        self.assertFalse(mgr.enabled)         # 2nd failure trips the breaker
        # once disabled it no longer calls the keeper
        calls_before = kp.calls
        run(mgr.ensure(3, account_id="acc-3"))
        self.assertEqual(kp.calls, calls_before)

    def test_success_resets_failure_counter(self):
        kp = _Keeper(boom=True)
        mgr = ProjectManager(
            project_store=_Store(), account_for=lambda uid: "acc-def",
            keeper_for_acc=lambda acc: kp, default_account_id="acc-def",
            max_failures=2, per_user_enabled=True, log=_Log(),
        )
        run(mgr.ensure(1, account_id="acc-1"))   # 1 failure
        kp.boom = False                           # now succeeds
        run(mgr.ensure(2, account_id="acc-2"))    # resets counter
        kp.boom = True
        run(mgr.ensure(3, account_id="acc-3"))    # 1 failure again, not 2
        self.assertTrue(mgr.enabled)

    def test_module_does_not_import_flow_bot(self):
        import inspect
        import accounts.projects as mod
        self.assertNotIn("flow_bot", inspect.getsource(mod))


if __name__ == "__main__":
    unittest.main()
