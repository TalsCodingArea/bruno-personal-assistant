from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from personal_assistant.ml.expense_categorizer import review_queue, service, storage
from personal_assistant.ml.expense_categorizer.models import CategoryPrediction


class FakeModel:
    def predict(self, description: str, amount: float, date: str) -> CategoryPrediction:
        return CategoryPrediction("Lifestyle 🏞️", "Snacks & Drinks 🍫", 0.91)


def _reset_service_model_state() -> None:
    service._model = None
    service._model_load_attempted = False


class ClassifyAndEnqueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"ML_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        _reset_service_model_state()
        self.addCleanup(_reset_service_model_state)

    def test_enqueues_with_prediction_when_model_available(self) -> None:
        with mock.patch.object(service, "_get_model", return_value=FakeModel()):
            item = service.classify_and_enqueue(
                notion_page_id="page-1", description="Cofix", amount=6.0, date="2026-07-10"
            )

        self.assertEqual(item.predicted_sub_category, "Snacks & Drinks 🍫")
        self.assertEqual(item.confidence, 0.91)
        self.assertEqual(len(review_queue.pending_items()), 1)

    def test_enqueues_without_prediction_when_no_model(self) -> None:
        with mock.patch.object(service, "_get_model", return_value=None):
            item = service.classify_and_enqueue(
                notion_page_id="page-1", description="Cofix", amount=6.0, date="2026-07-10"
            )

        self.assertIsNone(item.predicted_sub_category)
        self.assertEqual(len(review_queue.pending_items()), 1)

    def test_does_not_enqueue_same_page_twice(self) -> None:
        with mock.patch.object(service, "_get_model", return_value=None):
            first = service.classify_and_enqueue(
                notion_page_id="page-1", description="Cofix", amount=6.0, date="2026-07-10"
            )
            second = service.classify_and_enqueue(
                notion_page_id="page-1", description="Cofix", amount=6.0, date="2026-07-10"
            )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(review_queue.pending_items()), 1)

    def test_never_raises_even_when_queue_write_fails(self) -> None:
        with mock.patch.object(service, "_get_model", return_value=None), mock.patch.object(
            service.review_queue, "add_item", side_effect=OSError("disk full")
        ):
            item = service.classify_and_enqueue(
                notion_page_id="page-1", description="Cofix", amount=6.0, date="2026-07-10"
            )
        self.assertIsNone(item)


class EnsureModelTrainedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"ML_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        _reset_service_model_state()
        self.addCleanup(_reset_service_model_state)

    def test_trains_when_no_model_exists(self) -> None:
        with mock.patch.object(service, "train_from_notion", return_value={}) as train:
            service.ensure_model_trained()
        train.assert_called_once()

    def test_skips_when_model_already_exists(self) -> None:
        storage.model_path().write_bytes(b"fake model")
        with mock.patch.object(service, "train_from_notion") as train:
            service.ensure_model_trained()
        train.assert_not_called()

    def test_never_raises_when_training_fails(self) -> None:
        with mock.patch.object(service, "train_from_notion", side_effect=RuntimeError("notion down")):
            service.ensure_model_trained()  # must not raise


class ApplyReviewFeedbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"ML_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        _reset_service_model_state()
        self.addCleanup(_reset_service_model_state)

        with mock.patch.object(service, "_get_model", return_value=FakeModel()):
            self.item = service.classify_and_enqueue(
                notion_page_id="page-1", description="Cofix", amount=6.0, date="2026-07-10"
            )

    def _feedback_examples(self) -> list[dict]:
        path = storage.feedback_training_set_path()
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_confirmation_updates_notion_logs_feedback_and_retrains(self) -> None:
        notion_update = mock.Mock()
        retrain = mock.Mock(return_value={})
        with mock.patch.object(service, "_update_notion_expense_category", notion_update), \
                mock.patch.object(service, "_retrain_from_local_data", retrain):
            resolved = service.apply_review_feedback(self.item.review_id)

        self.assertEqual(resolved.status, "confirmed")
        self.assertEqual(resolved.final_sub_category, "Snacks & Drinks 🍫")
        notion_update.assert_called_once_with("page-1", "Lifestyle 🏞️", "Snacks & Drinks 🍫")
        retrain.assert_called_once()
        examples = self._feedback_examples()
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["sub_category"], "Snacks & Drinks 🍫")
        self.assertEqual(examples[0]["source"], "feedback")

    def test_correction_overrides_prediction(self) -> None:
        with mock.patch.object(service, "_update_notion_expense_category") as notion_update, \
                mock.patch.object(service, "_retrain_from_local_data", return_value={}):
            resolved = service.apply_review_feedback(
                self.item.review_id, category="Household 🏠", sub_category="Groceries 🛒"
            )

        self.assertEqual(resolved.status, "corrected")
        notion_update.assert_called_once_with("page-1", "Household 🏠", "Groceries 🛒")
        self.assertEqual(self._feedback_examples()[0]["category"], "Household 🏠")

    def test_notion_failure_leaves_item_pending_and_no_feedback(self) -> None:
        with mock.patch.object(
            service, "_update_notion_expense_category", side_effect=RuntimeError("notion down")
        ):
            with self.assertRaises(RuntimeError):
                service.apply_review_feedback(self.item.review_id)

        self.assertEqual(len(review_queue.pending_items()), 1)
        self.assertEqual(self._feedback_examples(), [])

    def test_unknown_and_double_resolution_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            service.apply_review_feedback("does-not-exist")

        with mock.patch.object(service, "_update_notion_expense_category"), \
                mock.patch.object(service, "_retrain_from_local_data", return_value={}):
            service.apply_review_feedback(self.item.review_id)
            with self.assertRaises(ValueError):
                service.apply_review_feedback(self.item.review_id)

    def test_unpredicted_item_requires_explicit_labels(self) -> None:
        with mock.patch.object(service, "_get_model", return_value=None):
            unpredicted = service.classify_and_enqueue(
                notion_page_id="page-2", description="Mystery", amount=10.0, date="2026-07-10"
            )

        with self.assertRaises(ValueError):
            service.apply_review_feedback(unpredicted.review_id)

    def test_dismiss_skips_notion_and_training(self) -> None:
        with mock.patch.object(service, "_update_notion_expense_category") as notion_update:
            resolved = service.dismiss_review_item(self.item.review_id)

        self.assertEqual(resolved.status, "dismissed")
        notion_update.assert_not_called()
        self.assertEqual(self._feedback_examples(), [])


if __name__ == "__main__":
    unittest.main()
