from __future__ import annotations

import asyncio
import unittest

from billing.credit_gate import Charge, NotEnoughCredits, open_credit_gate


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeStore:
    def __init__(self, balance: int):
        self._balance = balance
        self.charged: list[tuple[int, int]] = []
        self.refunded: list[tuple[int, int]] = []

    def balance(self, user_id: int) -> int:
        return self._balance

    def charge(self, user_id: int, amount: int) -> bool:
        if self._balance < amount:
            return False
        self._balance -= amount
        self.charged.append((user_id, amount))
        return True

    def refund(self, user_id: int, amount: int) -> None:
        self._balance += amount
        self.refunded.append((user_id, amount))


class CreditGateTests(unittest.TestCase):
    def test_free_action_does_not_touch_store(self) -> None:
        store = FakeStore(0)

        async def scenario():
            async with open_credit_gate(store, 1, 0) as charge:
                self.assertIsInstance(charge, Charge)

        run(scenario())
        self.assertEqual(store.charged, [])
        self.assertEqual(store.refunded, [])

    def test_success_charges_and_keeps(self) -> None:
        store = FakeStore(100)

        async def scenario():
            async with open_credit_gate(store, 7, 30) as charge:
                charge.ok = True

        run(scenario())
        self.assertEqual(store.charged, [(7, 30)])
        self.assertEqual(store.refunded, [])
        self.assertEqual(store.balance(7), 70)

    def test_failure_refunds(self) -> None:
        store = FakeStore(100)

        async def scenario():
            async with open_credit_gate(store, 7, 30) as charge:
                pass  # charge.ok stays False -> refund

        run(scenario())
        self.assertEqual(store.charged, [(7, 30)])
        self.assertEqual(store.refunded, [(7, 30)])
        self.assertEqual(store.balance(7), 100)

    def test_exception_in_body_refunds(self) -> None:
        store = FakeStore(100)

        async def scenario():
            async with open_credit_gate(store, 7, 30) as charge:
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            run(scenario())
        self.assertEqual(store.refunded, [(7, 30)])

    def test_insufficient_calls_callback_and_raises(self) -> None:
        store = FakeStore(5)
        seen: list[tuple[int, int]] = []

        async def on_insufficient(have, needed):
            seen.append((have, needed))

        async def scenario():
            async with open_credit_gate(store, 7, 30, on_insufficient=on_insufficient):
                pass

        with self.assertRaises(NotEnoughCredits):
            run(scenario())
        self.assertEqual(seen, [(5, 30)])
        self.assertEqual(store.charged, [])  # never charged
        self.assertEqual(store.refunded, [])

    def test_charge_failure_is_fail_closed(self) -> None:
        """charge() вернул False (гонка/ошибка БД): платное действие НЕ запускается."""

        class RaceStore(FakeStore):
            def charge(self, user_id: int, amount: int) -> bool:
                return False  # баланс при проверке был, но списание не прошло

        store = RaceStore(100)
        seen: list[tuple[int, int]] = []

        async def on_insufficient(have, needed):
            seen.append((have, needed))

        async def scenario():
            async with open_credit_gate(store, 7, 30, on_insufficient=on_insufficient):
                pass

        with self.assertRaises(NotEnoughCredits):
            run(scenario())
        self.assertEqual(seen, [(100, 30)])
        self.assertEqual(store.charged, [])  # списания не было
        self.assertEqual(store.refunded, [])  # и возвращать нечего

    def test_insufficient_without_callback_still_raises(self) -> None:
        store = FakeStore(0)

        async def scenario():
            async with open_credit_gate(store, 7, 30):
                pass

        with self.assertRaises(NotEnoughCredits):
            run(scenario())


if __name__ == "__main__":
    unittest.main()
