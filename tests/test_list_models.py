import asyncio
import os
import httpx
import json

async def list_models():
    api_key = os.environ.get("GOOGLE_API_KEY", "not-set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        result = response.json()
        if "models" in result:
            for m in result["models"]:
                print(f"Model: {m['name']} - Methods: {m.get('supportedGenerationMethods', [])}")
        else:
            print(f"FAILED: {result}")

if __name__ == "__main__":
    asyncio.run(list_models())
