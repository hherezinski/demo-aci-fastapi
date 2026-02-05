import os
import time
from typing import Optional

import aiomysql
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "app")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "app_password")
MYSQL_DB = os.getenv("MYSQL_DB", "appdb")

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))

app = FastAPI(title="Demo API (FastAPI + Redis + MySQL)")

redis_client: Optional[redis.Redis] = None
mysql_pool: Optional[aiomysql.Pool] = None


class ItemIn(BaseModel):
    value: str


@app.on_event("startup")
async def startup():
    global redis_client, mysql_pool

    # Redis
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    # MySQL pool
    mysql_pool = await aiomysql.create_pool(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        db=MYSQL_DB,
        autocommit=True,
        minsize=1,
        maxsize=5,
    )

    # Ensure table exists
    async with mysql_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    value VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )


@app.on_event("shutdown")
async def shutdown():
    global redis_client, mysql_pool
    if redis_client:
        await redis_client.aclose()
    if mysql_pool:
        mysql_pool.close()
        await mysql_pool.wait_closed()


@app.get("/health")
async def health():
    return {"status": "ok", "ts": int(time.time())}


@app.get("/redis/{key}")
async def redis_hit_no_hit(key: str):
    if not redis_client:
        raise HTTPException(status_code=500, detail="Redis not initialized")

    val = await redis_client.get(key)
    if val is None:
        await redis_client.set(key, "1", ex=CACHE_TTL_SECONDS)
        return {"key": key, "hit": False, "value": None, "ttl": CACHE_TTL_SECONDS}

    return {"key": key, "hit": True, "value": val}


@app.post("/db/item")
async def db_insert(item: ItemIn):
    if not mysql_pool:
        raise HTTPException(status_code=500, detail="MySQL not initialized")

    async with mysql_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("INSERT INTO items(value) VALUES(%s)", (item.value,))
            inserted_id = cur.lastrowid

    return {"id": inserted_id, "value": item.value}


@app.get("/db/item/{item_id}")
async def db_get(item_id: int):
    if not mysql_pool:
        raise HTTPException(status_code=500, detail="MySQL not initialized")

    async with mysql_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT id, value, created_at FROM items WHERE id=%s", (item_id,))
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    return row
