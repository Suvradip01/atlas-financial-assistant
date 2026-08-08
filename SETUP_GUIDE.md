# Atlas Financial Assistant - Quick Setup Guide

## Getting Started with Real Telegram Users

This guide will help you set up Atlas to work with real Telegram users typing messages.

## Step 1: Get Required API Keys

### 1. Telegram Bot Token
1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the instructions
3. Copy the bot token (looks like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
4. Save this for your `.env` file

### 2. OpenAI API Key (for AI functionality)
1. Go to https://platform.openai.com/api-keys
2. Create an API key
3. Copy the key (starts with `sk-`)
4. Save this for your `.env` file

### 3. Finnhub API Key (for financial data)
1. Go to https://finnhub.io/register
2. Sign up for free account
3. Get your API key from the dashboard
4. Save this for your `.env` file

### 4. Tavily API Key (for web search)
1. Go to https://tavily.com/
2. Sign up for free account
3. Get your API key
4. Save this for your `.env` file

### 5. PostgreSQL Database
You have several options:
- **Local**: Install PostgreSQL locally
- **Cloud**: Use Supabase (free), Neon (free), or Railway
- **Connection string format**: `postgresql+asyncpg://user:password@host:port/database`

### 6. Redis (optional but recommended)
- **Local**: Install Redis locally
- **Cloud**: Use Upstash Redis (free tier available)
- **Connection string format**: `redis://localhost:6379/0` or `rediss://url`

## Step 2: Configure Environment Variables

1. Copy the example environment file:
```bash
cp .env.example .env
```

2. Edit `.env` and fill in your actual values:
```env
# Telegram
TELEGRAM_BOT_TOKEN=your-actual-bot-token-here
TELEGRAM_WEBHOOK_SECRET=generate-random-secret-here
TELEGRAM_WEBHOOK_URL=https://your-ngrok-url.ngrok-free.dev/api/v1/telegram/webhook

# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/atlas_db

# Redis (optional but recommended)
REDIS_URL=redis://localhost:6379/0

# AI
OPENAI_API_KEY=your-actual-openai-key-here

# Financial Data
FINNHUB_API_KEY=your-actual-finnhub-key-here
TAVILY_API_KEY=your-actual-tavily-key-here

# App Secret (generate any random string >32 chars)
APP_SECRET_KEY=your-random-secret-key-at-least-32-characters-long
```

## Step 3: Set Up ngrok (for local development)

1. Download ngrok from https://ngrok.com/download
2. Install and start ngrok:
```bash
ngrok http 8000
```
3. Copy the ngrok URL (looks like: `https://random-name.ngrok-free.dev`)
4. Update `TELEGRAM_WEBHOOK_URL` in your `.env` file with this URL

## Step 4: Run Database Migrations

```bash
cd atlas-financial-assistant
alembic upgrade head
```

## Step 5: Start the Server

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Step 6: Test Your Bot

1. Open Telegram and search for your bot (by the name you gave it)
2. Send a message like: "hi"
3. You should get a response from Atlas
4. Try more messages:
   - "Compare Apple and Microsoft"
   - "What's Tesla's stock price?"
   - "Tell me about Nvidia"

## How Real Users Will Use Your App

When real users interact with your bot:

1. **First-time users** will send "hi" or similar greeting
2. **Atlas will respond** with conversational onboarding (no buttons!)
3. **Users will ask questions** like:
   - "What's happening with Apple today?"
   - "Compare Tesla and Ford"
   - "Alert me if Nvidia drops 5%"
   - "Remind me before earnings"
4. **Atlas will respond** with helpful, concise answers
5. **Atlas will learn** user preferences over time
6. **Atlas will send proactive messages** with daily briefs and important alerts

## Troubleshooting

### Server won't start
- Check that all required environment variables are set in `.env`
- Verify database connection string is correct
- Make sure PostgreSQL is running

### Telegram webhook fails
- Verify ngrok is running
- Check the webhook URL in `.env` matches your ngrok URL
- Make sure the bot token is correct

### No response from bot
- Check server logs for errors
- Verify OpenAI API key is valid
- Make sure database migrations ran successfully

### Database errors
- Run `alembic upgrade head` to ensure schema is up to date
- Check database connection string format
- Verify PostgreSQL is accessible

## Testing Checklist

- [ ] Server starts without errors
- [ ] Telegram webhook is registered successfully
- [ ] Bot responds to "hi" message
- [ ] Bot handles follow-up questions
- [ ] Bot provides financial data (not made up)
- [ ] Bot learns user preferences
- [ ] No errors in server logs

## Next Steps

Once your bot is working:

1. **Share the bot** with friends/colleagues
2. **Monitor the logs** to see how users interact
3. **Improve responses** based on real usage
4. **Add more features** as needed

The demo data script I created earlier is only for testing/assignment purposes. For real usage, your app will create users naturally as people interact with your Telegram bot!