"""Read-only Bank Movement and start-of-month cash projection tests."""

import asyncio
from datetime import date, datetime
from decimal import Decimal

from financial_agent.domain.models import (
    BankMovement,
    Budget,
    ExpenseSettlementTotals,
    Income,
)
from financial_agent.domain.profile import (
    FinancialProfileEntry,
    ProfileKind,
    ProfileStatus,
)
from financial_agent.integrations.notion_bank import NotionBankMovementReader
from financial_agent.services.cashflow import CashflowService

from tests.fakes import FakeFinanceReader, FakeNotion


class Bank:
    async def latest(self, as_of: date) -> BankMovement | None:
        return BankMovement(
            "bank-1", "Current", as_of, "Positive", "1", "10000"
        )

    async def movements(
        self, start_date: date, end_date: date, *, limit: int = 50
    ) -> tuple[BankMovement, ...]:
        return ()


class Profile:
    async def active_entries(
        self, *, limit: int = 100
    ) -> tuple[FinancialProfileEntry, ...]:
        now = datetime.fromisoformat("2026-09-01T00:00:00+03:00")
        return (
            FinancialProfileEntry(
                "minimum",
                "Minimum balance",
                "cashflow.minimum_available_balance",
                ProfileKind.CONSTRAINT,
                ("Cashflow",),
                "7500",
                ProfileStatus.ACTIVE,
                last_edited_at=now,
            ),
            FinancialProfileEntry(
                "sweep",
                "Savings sweep",
                "cashflow.savings_sweep_surplus",
                ProfileKind.GOAL,
                ("Savings",),
                "2500",
                ProfileStatus.ACTIVE,
                last_edited_at=now,
            ),
        )


class Finance(FakeFinanceReader):
    async def expense_settlement(self, month: date) -> ExpenseSettlementTotals:
        return ExpenseSettlementTotals(month, "3000", "400", "200")


def test_account_outlook_combines_bank_credit_reimbursement_and_rules() -> None:
    finance = Finance(
        incomes=(Income("salary", "Salary", date(2026, 9, 1), "12000"),),
        budgets=(Budget("rent", "Rent", date(2026, 9, 1), "4000"),),
    )
    service = CashflowService(finance, Bank(), Profile())  # type: ignore[arg-type]

    result = asyncio.run(
        service.account_outlook(date(2026, 8, 1), date(2026, 8, 31))
    )

    assert result.projected_balance_after_settlement == Decimal("15200.00")
    assert result.available_above_minimum == Decimal("7700.00")
    assert result.on_track is True
    assert result.recommended_savings_transfer == Decimal("7700.00")
    assert result.missing_rules == ()


def test_bank_reader_maps_latest_balance_without_write_operations() -> None:
    notion = FakeNotion(
        {
            "bank": [
                {
                    "id": "movement-1",
                    "properties": {
                        "Title": {"type": "title", "title": [{"plain_text": "Salary"}]},
                        "Date": {"type": "date", "date": {"start": "2026-09-01"}},
                        "Select": {"type": "select", "select": {"name": "Positive"}},
                        "Amount": {"type": "number", "number": 12000},
                        "Balance": {"type": "number", "number": 18500.25},
                        "Description": {"type": "rich_text", "rich_text": []},
                        "Action": {"type": "rich_text", "rich_text": []},
                    },
                }
            ]
        }
    )
    reader = NotionBankMovementReader(
        notion, data_source_id="bank", database_id=None
    )

    result = asyncio.run(reader.latest(date(2026, 9, 2)))

    assert result is not None
    assert result.balance_after == Decimal("18500.25")
    assert notion.created == []
    assert notion.updated == []
