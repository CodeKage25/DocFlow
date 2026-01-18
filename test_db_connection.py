import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def test_connection():
    db_url = os.getenv("DATABASE_URL")
    print(f"Testing connection to: {db_url}")
    
    try:
        conn = await asyncpg.connect(db_url)
        print("✅ Success! Connected to database.")
        
        version = await conn.fetchval('SELECT version()')
        print(f"   Database version: {version}")
        
        await conn.close()
    except Exception as e:
        print(f"❌ Connection FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())
