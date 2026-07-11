from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from personal_assistant.ml.expense_categorizer.models import TrainingExample
from personal_assistant.ml.expense_categorizer.pipeline import ExpenseCategorizerModel


def _synthetic_examples() -> list[TrainingExample]:
    """A tiny but learnable dataset: three sub-categories with clear vocab."""
    groceries = ["Shufersal", "Rami Levy groceries", "Victory supermarket", "Shufersal Deal", "Yohananof"]
    fuel = ["Paz gas station", "Sonol fuel", "Delek station", "Ten fuel stop", "Paz Yellow"]
    coffee = ["Cafe Cafe", "Aroma espresso", "Cofix coffee", "Landwer cafe", "Arcaffe"]

    examples = []
    for index in range(3):  # repeat so every class clears stratification minimums
        for description in groceries:
            examples.append(
                TrainingExample(f"{description} {index}", 250.0, "2026-05-03", "Household 🏠", "Groceries 🛒")
            )
        for description in fuel:
            examples.append(
                TrainingExample(f"{description} {index}", 300.0, "2026-05-10", "Car 🚗", "Fuel ⛽")
            )
        for description in coffee:
            examples.append(
                TrainingExample(f"{description} {index}", 18.0, "2026-05-17", "Lifestyle 🏞️", "Snacks & Drinks 🍫")
            )
    return examples


class ExpenseCategorizerModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"ML_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_train_predict_maps_sub_category_to_category(self) -> None:
        model, metadata = ExpenseCategorizerModel.train(_synthetic_examples())

        prediction = model.predict("Shufersal Ramat Aviv", 231.0, "2026-06-02")
        self.assertEqual(prediction.sub_category, "Groceries 🛒")
        self.assertEqual(prediction.category, "Household 🏠")
        self.assertGreater(prediction.confidence, 1 / 3)  # better than uniform guess
        self.assertEqual(metadata["n_examples"], 45)
        self.assertEqual(metadata["n_sub_categories"], 3)

    def test_refuses_to_train_on_tiny_dataset(self) -> None:
        with self.assertRaises(ValueError):
            ExpenseCategorizerModel.train(_synthetic_examples()[:5])

    def test_save_and_load_round_trip(self) -> None:
        model, metadata = ExpenseCategorizerModel.train(_synthetic_examples())
        model.save(metadata)

        loaded = ExpenseCategorizerModel.load()

        self.assertIsNotNone(loaded)
        prediction = loaded.predict("Paz fuel Herzliya", 280.0, "2026-06-05")
        self.assertEqual(prediction.sub_category, "Fuel ⛽")
        self.assertEqual(prediction.category, "Car 🚗")

    def test_load_returns_none_when_no_model_saved(self) -> None:
        self.assertIsNone(ExpenseCategorizerModel.load())


if __name__ == "__main__":
    unittest.main()
