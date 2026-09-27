"""Initial notification boundary for expense monitoring."""

from financial_agent.domain.expense_monitoring import (
    AlertDeliveryReceipt,
    PendingAlertDelivery,
)


class TraceExpenseAlertNotifier:
    """A no-network sink whose receipt keeps proposed delivery visible in traces."""

    async def send(self, delivery: PendingAlertDelivery) -> AlertDeliveryReceipt:
        return AlertDeliveryReceipt(
            delivery_id=delivery.delivery_id,
            delivered=True,
            channel="langsmith_trace",
            detail=(
                "Trace-only delivery stub; no external notification was sent. "
                f"Proposed message: {delivery.alert.message}"
            ),
        )
