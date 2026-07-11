from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from personal_assistant.ml.expense_categorizer import review_queue
from personal_assistant.ml.expense_categorizer.models import CategoryPrediction, ReviewItem


def _item(page_id: str = "page-1", description: str = "Coffee") -> ReviewItem:
    return ReviewItem.new(
        notion_page_id=page_id,
        description=description,
        amount=12.5,
        date="2026-07-10",
        prediction=CategoryPrediction("Lifestyle 🏞️", "Snacks & Drinks 🍫", 0.9),
    )


class ReviewQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"ML_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_add_and_list_pending(self) -> None:
        item = _item()
        review_queue.add_item(item)

        pending = review_queue.pending_items()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].review_id, item.review_id)
        self.assertTrue(review_queue.has_pending_for_page("page-1"))
        self.assertFalse(review_queue.has_pending_for_page("page-2"))

    def test_mark_notified_excludes_from_unnotified(self) -> None:
        first, second = _item("page-1"), _item("page-2", "Fuel")
        review_queue.add_item(first)
        review_queue.add_item(second)

        review_queue.mark_notified([first.review_id])

        unnotified = review_queue.unnotified_pending_items()
        self.assertEqual([item.review_id for item in unnotified], [second.review_id])
        # Notified items are still pending until resolved.
        self.assertEqual(len(review_queue.pending_items()), 2)

    def test_resolve_item_sets_status_and_final_labels(self) -> None:
        item = _item()
        review_queue.add_item(item)

        resolved = review_queue.resolve_item(
            item.review_id,
            status="corrected",
            final_category="Household 🏠",
            final_sub_category="Groceries 🛒",
        )

        self.assertEqual(resolved.status, "corrected")
        self.assertEqual(resolved.final_sub_category, "Groceries 🛒")
        self.assertIsNotNone(resolved.resolved_at)
        self.assertEqual(review_queue.pending_items(), [])
        self.assertFalse(review_queue.has_pending_for_page("page-1"))

    def test_resolve_unknown_item_returns_none(self) -> None:
        self.assertIsNone(review_queue.resolve_item("missing", status="confirmed"))


if __name__ == "__main__":
    unittest.main()
