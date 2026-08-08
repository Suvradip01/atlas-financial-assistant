# Atlas Scripts

This directory contains utility scripts for the Atlas Financial Assistant.

## Available Scripts

### populate_demo_data.py

Populates the database with comprehensive demo data for testing and assignment demonstration.

**Features:**
- Creates 5 test users with different roles (portfolio manager, VC analyst, retail investor, founder, corporate analyst)
- Sets up user preferences and communication styles
- Creates watchlists with public/private companies and sectors
- Adds conversation history showing different workflows
- Creates memory facts demonstrating personalization
- Adds research history for context
- Creates sample documents for document QA testing
- Sets up alerts and reminders

**Usage:**
```bash
cd atlas-financial-assistant
python scripts/populate_demo_data.py
```

**What it creates:**
- 5 Users with telegram_ids 1001-1005
- User preferences (followed sectors, alert preferences, communication styles)
- Watchlist items (mix of public companies, private companies, and sectors)
- Conversation history with realistic Q&A examples
- Memory facts showing learned user preferences
- Research history for personalization
- Sample SEC filings and earnings call documents
- Alerts and reminders for proactive features

**Testing with Demo Data:**
After running the script, you can:
1. Update your telegram_id in the database to match a demo user
2. Send test messages via Telegram
3. See the pre-populated context and personalization in action

**Database Schema:**
The script works with the existing schema and creates data for:
- users, user_preferences
- watchlist_items
- conversations, messages
- memory_facts
- research_history
- documents, document_chunks
- alerts, reminders, notification_logs

**Important Notes:**
- The script does NOT clear existing data by default
- To clear existing data, uncomment the `clear_existing_data()` call
- Demo users have telegram_ids 1001-1005 for easy identification
- All timestamps are set to realistic past dates for testing

## Future Scripts

Potential additions:
- `run_daily_brief.py` - Manually trigger the daily brief pipeline
- `test_webhook.py` - Test Telegram webhook locally
- `clear_demo_data.py` - Remove only demo data
- `backup_database.py` - Database backup utility
- `migrate_user_data.py` - User data migration tools