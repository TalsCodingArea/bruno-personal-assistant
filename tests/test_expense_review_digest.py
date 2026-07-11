from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from personal_assistant.ml.expense_categorizer import review_queue
from personal_assistant.ml.expense_categorizer.models import CategoryPrediction, ReviewItem
from personal_assistant.ml.expense_categorizer.digest import build_review_digest


def _item(page_id: str, description: str) -> ReviewItem:
    return ReviewItem.new(
        notion_page_id=page_id,
        description=description,
        amount=42.0,
        date="2026-07-10",
        prediction=CategoryPrediction("Household 🏠", "Groceries 🛒", 0.8),
    )


class ReviewDigestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"ML_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_quiet_when_queue_is_empty(self) -> None:
        message, review_ids = build_review_digest()
        self.assertIsNone(message)
        self.assertEqual(review_ids, [])

    def test_digest_lists_new_items_and_counts_older_ones(self) -> None:
        old = _item("page-1", "Old fuel")
        review_queue.add_item(old)
        review_queue.mark_notified([old.review_id])

        new = _item("page-2", "Shufersal")
        review_queue.add_item(new)

        message, review_ids = build_review_digest()

        self.assertIn("1 new expense(s)", message)
        self.assertIn("Shufersal", message)
        self.assertNotIn("Old fuel", message)
        self.assertIn("1 older item(s) still waiting", message)
        self.assertEqual(review_ids, [new.review_id])

    def test_quiet_when_everything_was_already_notified(self) -> None:
        item = _item("page-1", "Shufersal")
        review_queue.add_item(item)
        review_queue.mark_notified([item.review_id])

        message, review_ids = build_review_digest()
        self.assertIsNone(message)
        self.assertEqual(review_ids, [])


if __name__ == "__main__":
    unittest.main()
