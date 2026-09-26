# Interaction profile walkthrough

The interaction profile controls how the assistant communicates. It never changes finance
facts, calculations, currency, tool permissions, or approval requirements.

## Effective settings

The domain model has six controlled settings and safe defaults:

| Setting | Allowed values | Default |
|---|---|---|
| Tone | `warm`, `neutral`, `professional`, `casual` | `warm` |
| Banter | `none`, `light`, `playful` | `light` |
| Verbosity | `concise`, `balanced`, `detailed` | `concise` |
| Coaching style | `gentle`, `direct`, `challenging` | `direct` |
| Proactivity | `reactive`, `balanced`, `proactive` | `balanced` |
| Language | `match_user`, `english`, `hebrew` | `match_user` |

Each override is an Active Financial Rules page with `Kind=Preference`,
`Scope=Conversation`, and one of these keys:

```text
assistant.tone
assistant.banter
assistant.verbosity
assistant.coaching_style
assistant.proactivity
assistant.language
```

The `Statement` contains only the controlled value, making the entry readable by both Tal and
the application. A manually entered invalid value is ignored, the safe default remains active,
and a warning is included in the compiled profile.

## Read and context flow

```text
Financial Rules Active entries (one Notion query)
  ├─ ordinary entries → financial profile context
  └─ assistant.* entries → compiled InteractionProfile
                           → effective interaction profile context
```

This split happens in `manage_context` before every assistant turn. It avoids a second Notion
request and prevents conversation style from being treated as finance policy.

## Update and approval flow

```text
get_interaction_profile
  → draft_interaction_preference_update (validated, no write)
  → apply_interaction_preference_update
  → LangGraph interrupt (Notion still unchanged)
  → resume same thread with approve/reject
  → create new Active assistant.* version when approved
  → mark predecessor Superseded
```

Only one setting is changed per proposal. This keeps reviews narrow and avoids pretending that
multiple Notion writes are an atomic transaction.

## Behavioral guardrails

- Banter never appears in approval payloads, errors, or material risk warnings.
- Tone does not change numerical precision or groundedness.
- Proactivity may change whether the assistant surfaces a concern, but cannot authorize a write.
- Language changes presentation only; Notion keys and controlled values remain stable English
  identifiers.
