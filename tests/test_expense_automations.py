from __future__ import annotations

import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("NOTION_API_KEY", "test-notion-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

automation_functions = importlib.import_module("personal_assistant.automations")


class ExpenseAutomationTest(unittest.TestCase):
    @patch.object(automation_functions, "datetime")
    @patch.object(automation_functions, "log_expense")
    def test_auto_expense_tool_logs_uncategorized_expense_for_today(self, log_expense, datetime_mock) -> None:
        datetime_mock.now.return_value.isoformat.return_value = "2026-06-27T10:30:00"
        log_expense.return_value = "logged"

        result = automation_functions.auto_expense_tool(description="Coffee", amount=12.5)

        self.assertEqual(result, "logged")
        log_expense.assert_called_once_with(
            Description="Coffee",
            Amount=12.5,
            Date="2026-06-27T10:30:00",
            Category=["Uncategorized"],
            Tag=["Tal 👨🏻"],
        )

    def test_auto_expense_tool_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            automation_functions.auto_expense_tool(description="", amount=12.5)
        with self.assertRaises(ValueError):
            automation_functions.auto_expense_tool(description="Coffee", amount=0)
        with self.assertRaises(ValueError):
            automation_functions.auto_expense_tool(description="Coffee", amount="nope")

    @patch.object(automation_functions, "datetime")
    @patch.object(automation_functions, "log_expense")
    def test_auto_expense_tool_extracts_shekel_string_amount(self, log_expense, datetime_mock) -> None:
        datetime_mock.now.return_value.isoformat.return_value = "2026-06-27T10:30:00"
        log_expense.return_value = "logged"

        result = automation_functions.auto_expense_tool(description="Coffee", amount="₪75.00")

        self.assertEqual(result, "logged")
        log_expense.assert_called_once_with(
            Description="Coffee",
            Amount=75.0,
            Date="2026-06-27T10:30:00",
            Category=["Uncategorized"],
            Tag=["Tal 👨🏻"],
        )

    @patch.object(automation_functions, "datetime")
    @patch.object(automation_functions, "log_expense")
    def test_auto_expense_tool_extracts_comma_formatted_shekel_amount(self, log_expense, datetime_mock) -> None:
        datetime_mock.now.return_value.isoformat.return_value = "2026-06-27T10:30:00"
        log_expense.return_value = "logged"

        result = automation_functions.auto_expense_tool(description="Device", amount="₪1,234.56")

        self.assertEqual(result, "logged")
        log_expense.assert_called_once_with(
            Description="Device",
            Amount=1234.56,
            Date="2026-06-27T10:30:00",
            Category=["Uncategorized"],
            Tag=["Tal 👨🏻"],
        )

    @patch.object(automation_functions, "log_expense")
    def test_auto_expense_tool_skips_non_shekel_string_amount(self, log_expense) -> None:
        result = automation_functions.auto_expense_tool(description="Subscription", amount="$15.00")

        self.assertEqual(result, "Skipped non-shekel expense: Subscription — $15.00")
        log_expense.assert_not_called()

    @patch.object(automation_functions, "auto_expense_tool")
    @patch.object(automation_functions.openai_client.chat.completions, "create")
    def test_log_txt_expense_extracts_hebrew_sms_transaction(self, create, auto_expense_tool) -> None:
        create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"description": "פיזיקל טכנולוגי", "amount": 400}')
                )
            ]
        )
        auto_expense_tool.return_value = "logged"

        result = automation_functions.log_txt_expense(
            "היי, ב 26/06 קיבלנו בקשה לעסקת אינטרנט/טלפון בכרטיס מסטרקארד "
            "המסתיים ב 0273 בפיזיקל טכנולוגי\nבסך 400 שח."
        )

        self.assertEqual(result, "logged")
        auto_expense_tool.assert_called_once_with(description="פיזיקל טכנולוגי", amount=400.0, tag="Tal 👨🏻")

    @patch.object(automation_functions.openai_client.chat.completions, "create")
    def test_log_txt_expense_rejects_malformed_llm_output(self, create) -> None:
        create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))]
        )

        with self.assertRaises(ValueError):
            automation_functions.log_txt_expense("transaction text")


if __name__ == "__main__":
    unittest.main()
