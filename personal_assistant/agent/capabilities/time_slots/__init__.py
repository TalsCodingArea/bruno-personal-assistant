"""Time Slots capability: keep time-block names in sync with their tasks.

Tal time-blocks his week as pages in the Notion "Time Slots" DB; the page
name is what his calendar shows at a glance, so it must summarize the tasks
inside the block (e.g. "Numeric - Ex.4 Q1-Q4", "Watch Comp Lec 5"). When he
reshuffles tasks between blocks, a Notion automation posts
{"tool": "update_time_slot_name", "args": {"url": "<page url>"}} to the
Telegram automations channel, which runs the LangGraph workflow in graph.py:

    fetch slot -> fetch its tasks & courses -> gather naming context
    -> LLM proposes a name -> update Notion only if it changed

Course short names ("Numeric Analysis" -> "Numeric") are learned from the
LLM's own outputs and persisted in budget_data/time_slots/ (see naming.py),
so naming stays consistent across runs.
"""
