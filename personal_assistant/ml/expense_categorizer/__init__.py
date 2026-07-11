"""Expense categorizer: predicts Category / Sub Category for Tal's expenses.

Architecture (each module has one job):

- models.py        dataclasses shared across the package
- storage.py       where everything lives on disk (budget_data/ml/)
- dataset.py       training examples: initial pull from Notion + feedback log
- pipeline.py      the scikit-learn model itself (train / predict / save / load)
- review_queue.py  on-device queue of predictions awaiting human review
- service.py       orchestration: classify new expenses, apply feedback, retrain

Model choice: TF-IDF (word + character n-grams, which handle the
Hebrew/English mix in expense descriptions well) into a logistic regression.
At the scale of personal expense data (thousands of rows) this trains in
well under a second on the Mac Mini, so "further training" on feedback is
implemented as a full retrain -- simpler and strictly more accurate than
incremental partial_fit updates.
"""
