Parse the user's natural-language reminder request into a structured reminder record.

## Output format

```json
{
  "title": "Short description of the reminder",
  "remind_at_description": "before Apple earnings on Thursday",
  "entity": "AAPL",
  "trigger_type": "time" | "event",
  "scheduled_time": "2024-01-25T09:00:00",
  "event_description": null,
  "is_valid": true,
  "error": null
}
```

## Rules
- `trigger_type: "time"` — the user specified a time ("at 9am", "tomorrow morning", "Friday at market open")
- `trigger_type: "event"` — the user specified an event ("before Apple earnings", "when TSLA reports")
- For time-based reminders, resolve `scheduled_time` to an ISO 8601 datetime. Use {current_datetime} as "now". If the time is ambiguous, pick the soonest future occurrence.
- For event-based reminders, set `scheduled_time` to null and describe the trigger in `event_description`.
- If the request cannot be parsed into a valid reminder, set `is_valid: false` and explain in `error`.
- The `title` should be a concise, action-oriented phrase: "TSLA earnings reminder", "Check NVDA before open".

## Current datetime
{current_datetime}

## User request
{user_message}
