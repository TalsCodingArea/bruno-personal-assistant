# Jev model research for `handle_cal_notification`

Research date: 2026-09-27. Sources are limited to TypeSafe AI's official documentation and LangChain's official documentation/source.

## Verified identity and API

- The spelling is **Jev**. It is TypeSafe AI's flagship and first “System One” decision model: it reads natural-language or structured state and returns typed decisions and probabilities rather than generated prose ([TypeSafe introduction](https://docs.typesafe.ai/introduction)).
- Native endpoint: `POST https://api.typesafe.ai/v1/systemone`, with `Authorization: Bearer <API_KEY>` and JSON content ([TypeSafe quick start](https://docs.typesafe.ai/introduction/quickstart), [API reference](https://docs.typesafe.ai/api)).
- Official credential environment variable: `TYPESAFE_API_KEY`. Optional configuration variables in the official Python SDK are `TYPESAFE_BASE_URL` and `TYPESAFE_DEFAULT_MODEL`; the default base URL is `https://api.typesafe.ai` ([SDK constants](https://docs.typesafe.ai/sdk/python/api/constants)).
- Current stable alias: `jev-latest`, currently resolving to pinned model `jev-1.13.0`. `jev-preview` currently resolves to the same version. Aliases can move, so pin `jev-1.13.0` if calibrated thresholds must remain reproducible and log the response's `model` field ([models](https://docs.typesafe.ai/models)).
- Current published limits/specification: text/JSON input only, 64k tokens per request, 32k for state plus the longest question, and $0.042 per million input tokens with output tokens free. English is the primary/best-supported language; other languages work unevenly and require workload-specific testing ([models](https://docs.typesafe.ai/models)).

The native request is **not an OpenAI Chat Completions request**: its contract is `state` + `model` + named `questions` at `/v1/systemone`, and it does not generate chat text. Use the TypeSafe SDK or dedicated LangChain integration, not `ChatOpenAI` pointed at the TypeSafe base URL ([API reference](https://docs.typesafe.ai/api), [LangChain TypeSafe integration](https://docs.langchain.com/oss/python/integrations/providers/typesafe)).

## LangChain integration

Install `langchain-typesafe` and use `TypeSafeClassifier`, a LangChain `Runnable` supporting `invoke`, `ainvoke`, batching, composition, callbacks, and LangSmith usage tracing. It reads `TYPESAFE_API_KEY`; `TYPESAFE_BASE_URL` is optional and defaults to `https://api.typesafe.ai` ([LangChain integration guide](https://docs.langchain.com/oss/python/integrations/providers/typesafe), [official integration source](https://github.com/langchain-ai/langchain/blob/master/libs/partners/typesafe/langchain_typesafe/classifier.py)).

The package is currently `0.0.1a3`, labels the classifier beta, and requires Python 3.10+ plus `langchain-core >=1.6.2,<2.0.0`. Agent middleware is a separate experimental extra whose API may change; it is unnecessary for this notification gate ([official `pyproject.toml`](https://github.com/langchain-ai/langchain/blob/master/libs/partners/typesafe/pyproject.toml), [integration README](https://github.com/langchain-ai/langchain/blob/master/libs/partners/typesafe/README.md)).

Jev exposes three bounded result types:

- `Noul`: yes/no probability (`noul`), with no separate confidence field.
- `Choice`: one option plus the full probability map and confidence.
- `Score`: a bounded ordinal score plus probabilities and confidence.

These are typed decision outputs, not arbitrary Pydantic/JSON generation ([LangChain integration guide](https://docs.langchain.com/oss/python/integrations/providers/typesafe), [TypeSafe API reference](https://docs.typesafe.ai/api)).

## Recommended role in `handle_cal_notification`

Use Jev only as the transaction gate. A suitable atomic `Noul` question is:

> Is this a completed credit-card purchase or charge notification that explicitly contains both a monetary amount and a merchant or business name?

Give explicit criteria:

- `true`: a completed spend/charge, with an amount and merchant/business both present.
- `false`: promotion, informational update, balance statement, OTP/security message, declined/cancelled/reversed transaction, or a notification missing either amount or merchant. Adjust pending/refund treatment to the product's intended accounting policy.

Then branch in code on `response.nouls["is_expense"].noul`. TypeSafe recommends selecting thresholds according to the cost of errors and sending uncertain values to review/fallback; a Noul near `0.5` means the model is split, not “medium” ([Noul guidance](https://docs.typesafe.ai/primitives/noul), [confidence guidance](https://docs.typesafe.ai/confidence)). Start conservatively, evaluate on labeled real CAL notifications, and tune the threshold rather than hard-coding `0.5` as a production decision rule.

If the gate passes:

1. Run the existing normal generative LLM extraction for exact `amount`, `currency`, and `merchant`.
2. Validate that amount and merchant are non-empty and that the amount parses safely.
3. Invoke `log_expense` from application control flow.

This separation matches TypeSafe's intended design: code owns side effects and control flow, while Jev supplies narrow decisions ([how to build with TypeSafe](https://docs.typesafe.ai/concepts/how-to-build-with-system-one)). Jev can classify among a bounded list of tool/function names, but execution and non-enumerated arguments still belong to code; numbers, dates, and free text are not native generated arguments ([function-calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling)).

## Important caveats

- **Do not ask Jev to generate the merchant or amount.** Jev 1.13 is not a text generator and is weak on numeric precision. TypeSafe recommends regex/NER/a generative model to produce candidates, then optionally letting Jev select among those candidates; arithmetic and normalization stay in code ([pre-parsed extraction cookbook](https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook), [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)).
- **Hebrew needs explicit evaluation.** English is Jev's primary training language. If CAL notifications are Hebrew, build a labeled Hebrew test set containing purchases plus the roughly 10% non-expense cases before enabling automatic logging; keep an uncertain/fallback path ([models](https://docs.typesafe.ai/models)).
- **Be literal and narrow.** Jev 1.13 can read conditions literally, loses accuracy with irrelevant context, is vulnerable to adversarially framed state, and should receive only relevant notification metadata/title/body ([Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)).
- **Version and threshold drift matter.** Pin `jev-1.13.0` during calibration or record the resolved model and re-run the labeled set when moving `jev-latest` ([models](https://docs.typesafe.ai/models)).
- **Handle provider failures.** The API documents `401`, `422`, `429`, and `529`; SDKs retry rate-limit/overload responses with backoff, while a direct HTTP client must implement that behavior ([API reference](https://docs.typesafe.ai/api)). A classifier outage should not silently log an expense.

## Minimal LangChain shape

```python
from langchain_typesafe import Noul, NoulCriteria, TypeSafeClassifier

classifier = TypeSafeClassifier(model="jev-1.13.0")
result = classifier.invoke(
    {
        "state": {
            "app": notification.app,
            "title": notification.title,
            "body": notification.body,
        },
        "questions": {
            "is_expense": Noul(
                instructions=(
                    "Is this a completed credit-card purchase or charge notification "
                    "that explicitly contains both a monetary amount and a merchant "
                    "or business name?"
                ),
                criteria=NoulCriteria(
                    true=(
                        "A completed spend/charge; an amount and merchant/business "
                        "are both explicitly present."
                    ),
                    false=(
                        "Not a completed spend/charge, or either amount or merchant is "
                        "missing. Includes promotions, OTP/security, informational, "
                        "declined, cancelled, and reversed notifications."
                    ),
                ),
            )
        },
    }
)

transaction_probability = result.nouls["is_expense"].noul
# Application code applies a threshold calibrated on real CAL notifications.
# On pass: normal LLM extraction -> validation -> log_expense.
```
