# Fixing LLM Rate Limiting Issues

## Problem
Your logs show you're hitting rate limits with Google's Gemini model:
```
"model": "gemini-3-flash-preview", "event": "llm_rate_limit"
"LLMError('LLM rate limit exceeded for model gemini-3-flash-preview')"
```

## Solution: Improved Rate Limit Handling for Gemini

The code has been updated with better retry logic for Gemini:

### Changes Made:
1. **Enhanced retry logic** in `app/ai/llm/client.py`:
   - Increased retry attempts from 3 to 5
   - Increased exponential backoff (2s to 60s max wait)
   - RateLimitError now triggers automatic retries with backoff

2. **Better Gemini models** in `app/core/config.py`:
   - Auto-switches to `gemini-1.5-flash` and `gemini-1.5-pro` when using Google
   - These have better rate limits than `gemini-3-flash-preview`

3. **Automatic model selection** in `app/ai/llm/model_router.py`:
   - When `LLM_PROVIDER=google`, automatically uses the better Gemini models

### Your `.env` Configuration:
```env
LLM_PROVIDER=google
GOOGLE_API_KEY=your-google-api-key-here
```

The system will now automatically use:
- `gemini-1.5-flash` for small/medium tasks
- `gemini-1.5-pro` for large/vision tasks
- `text-embedding-004` for embeddings

### Quick Test:
After the changes, restart your server:
```bash
# Stop the current server (Ctrl+C)
# Then restart:
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The improved retry logic will automatically handle rate limits by waiting and retrying, reducing incomplete responses.