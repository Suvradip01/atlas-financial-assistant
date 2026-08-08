import asyncio
import os
import dotenv
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import event
from pgvector.asyncpg import register_vector

async def test_db():
    dotenv.load_dotenv()
    db_url = os.environ.get("DATABASE_URL")
    engine = create_async_engine(db_url)

    @event.listens_for(engine.sync_engine, "connect")
    def receive_connect(dbapi_connection, connection_record):
        dbapi_connection.run_async(register_vector)

    async with engine.connect() as conn:
        print("SUCCESS")
        
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(test_db())
