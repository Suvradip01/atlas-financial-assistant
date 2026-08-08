import asyncio
import os
import httpx
import json

async def test_embed_rest():
    api_key = os.environ.get("GOOGLE_API_KEY", "not-set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
    
    payload = {
        "model": "models/text-embedding-004",
        "content": {
            "parts": [{"text": "Hello world"}]
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload)
        result = response.json()
        if "embedding" in result:
            print("SUCCESS REST")
        else:
            print(f"FAILED REST: {result}")

if __name__ == "__main__":
    asyncio.run(test_embed_rest())
