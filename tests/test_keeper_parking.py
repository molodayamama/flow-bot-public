"""Offline tests for SessionKeeper idle-tab parking (no real browser)."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flow_bot


class FakePage:
    def __init__(self, url="https://labs.google/fx/tools/flow/project/pid123"):
        self.url = url
        self.goto_calls = []
        self._closed = False

        self.reload_calls = 0

    async def goto(self, url, **kw):
        self.goto_calls.append(url)
        self.url = url

    async def reload(self, **kw):
        self.reload_calls += 1

    def is_closed(self):
        return self._closed


async def _noop_sleep(*a, **kw):
    return None


def _keeper(parked=False, url="https://labs.google/fx/tools/flow/project/pid123"):
    k = flow_bot.SessionKeeper(account_id="t1", profile_dir="./_x")
    k._project_id = "pid123"
    k._context = object()          # truthy → _browser_alive checks page only
    k._page = FakePage(url)
    k._parked = parked
    # fresh bearer so _navigate_to_flow_locked's wait loop exits immediately
    k._bearer = "tok"
    k._bearer_ts = time.time()
    return k


class WakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_wake_when_parked_navigates_to_flow(self):
        k = _keeper(parked=True, url="about:blank")
        await k._wake_locked()
        self.assertFalse(k._parked)
        self.assertTrue(k._page.goto_calls)
        self.assertIn("pid123", k._page.goto_calls[-1])
        self.assertNotEqual(k._last_use, 0.0)

    async def test_wake_when_not_parked_is_noop(self):
        k = _keeper(parked=False)
        await k._wake_locked()
        self.assertEqual(k._page.goto_calls, [])   # no navigation
        self.assertFalse(k._parked)


class RefreshBearerTests(unittest.IsolatedAsyncioTestCase):
    """Regression: _refresh_bearer must navigate back to Flow when the tab is
    parked (a plain reload of about:blank never re-captures the Bearer)."""

    async def asyncSetUp(self):
        import unittest.mock as mock
        self._p = mock.patch("flow_bot.asyncio.sleep", new=_noop_sleep)
        self._p.start()

    async def asyncTearDown(self):
        self._p.stop()

    async def test_refresh_navigates_to_flow_when_parked(self):
        k = _keeper(parked=True, url="about:blank")
        await k._refresh_bearer()
        self.assertFalse(k._parked)
        self.assertTrue(any("pid123" in u for u in k._page.goto_calls))
        self.assertEqual(k._page.reload_calls, 0)  # no blank reload

    async def test_refresh_reloads_when_on_flow(self):
        k = _keeper(parked=False)
        await k._refresh_bearer()
        self.assertEqual(k._page.reload_calls, 1)
        self.assertEqual(k._page.goto_calls, [])


class ParkDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_should_park_true_after_idle(self):
        k = _keeper(parked=False)
        k._last_use = time.time() - (flow_bot.IDLE_PARK_SEC + 5)
        self.assertTrue(k._should_park())

    async def test_should_not_park_when_recently_used(self):
        k = _keeper(parked=False)
        k._mark_use()
        self.assertFalse(k._should_park())

    async def test_should_not_park_when_already_parked(self):
        k = _keeper(parked=True)
        k._last_use = time.time() - (flow_bot.IDLE_PARK_SEC + 5)
        self.assertFalse(k._should_park())

    async def test_should_not_park_when_browser_dead(self):
        k = _keeper(parked=False)
        k._page = None  # _browser_alive() → False
        k._last_use = time.time() - (flow_bot.IDLE_PARK_SEC + 5)
        self.assertFalse(k._should_park())

    async def test_should_not_park_when_keep_warm_active(self):
        k = _keeper(parked=False)
        k._last_use = time.time() - (flow_bot.IDLE_PARK_SEC + 5)
        k.keep_warm_for(flow_bot.IDLE_PARK_SEC + 10, "image")
        self.assertFalse(k._should_park())

    async def test_park_locked_navigates_to_blank(self):
        k = _keeper(parked=False)
        await k._park_locked()
        self.assertTrue(k._parked)
        self.assertEqual(k._page.goto_calls[-1], "about:blank")


if __name__ == "__main__":
    unittest.main()
