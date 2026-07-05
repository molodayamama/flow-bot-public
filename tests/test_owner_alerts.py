from __future__ import annotations

import unittest

from channels.telegram.owner_alerts import OwnerAlerts, OwnerAlertsDeps


class _Bot:
    def __init__(self, *, fail_for: set[int] | None = None) -> None:
        self.fail_for = fail_for or set()
        self.sent = []

    async def send_message(self, owner_id: int, text: str, *, parse_mode: str) -> None:
        if owner_id in self.fail_for:
            raise RuntimeError("blocked")
        self.sent.append((owner_id, text, parse_mode))


class _Loop:
    def __init__(self) -> None:
        self.tasks = []

    def create_task(self, coro) -> None:
        self.tasks.append(coro)


class OwnerAlertsTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_is_best_effort_for_each_owner(self) -> None:
        bot = _Bot(fail_for={2})
        alerts = OwnerAlerts(OwnerAlertsDeps(owner_ids=[1, 2, 3], bot=bot))

        await alerts.send("hello")

        self.assertEqual(bot.sent, [(1, "hello", "HTML"), (3, "hello", "HTML")])

    async def test_fire_schedules_send_on_running_loop(self) -> None:
        bot = _Bot()
        loop = _Loop()
        alerts = OwnerAlerts(OwnerAlertsDeps(
            owner_ids=[7],
            bot=bot,
            get_running_loop=lambda: loop,
        ))

        alerts.fire("warn")
        await loop.tasks[0]

        self.assertEqual(bot.sent, [(7, "warn", "HTML")])

    def test_fire_drops_without_running_loop(self) -> None:
        alerts = OwnerAlerts(OwnerAlertsDeps(
            owner_ids=[7],
            bot=_Bot(),
            get_running_loop=lambda: (_ for _ in ()).throw(RuntimeError("no loop")),
        ))

        alerts.fire("warn")
