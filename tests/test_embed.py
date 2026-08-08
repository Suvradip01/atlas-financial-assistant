import asyncio
import os
from openai import AsyncOpenAI

async def test_embed():
    api_key = os.environ.get("GOOGLE_API_KEY", "not-set")
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    
    try:
        response = await client.embeddings.create(
            model="text-embedding-004",
            input=["Hello world"]
        )
        print("SUCCESS text-embedding-004")
    except Exception as e:
        print(f"FAILED text-embedding-004: {e}")

    try:
        response = await client.embeddings.create(
            model="models/text-embedding-004",
            input=["Hello world"]
        )
        print("SUCCESS models/text-embedding-004")
    except Exception as e:
        print(f"FAILED models/text-embedding-004: {e}")

if __name__ == "__main__":
    asyncio.run(test_embed())
