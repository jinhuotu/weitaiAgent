import asyncio

import asyncmy
from sqlalchemy.engine import make_url

from common.config import get_settings
from db.session import engine  # installs the same compatibility hook as API/Worker


async def main():
    url = make_url(get_settings().database_url)
    connection = await asyncmy.connect(host=url.host or '127.0.0.1', port=url.port or 3306,
                                       user=url.username, password=url.password,
                                       db=url.database, charset='utf8mb4')
    try:
        async with connection.cursor() as cursor:
            for size in (262145, 1048576, 5242880):
                await asyncio.wait_for(cursor.execute(f"SELECT REPEAT('x', {size})"), timeout=15)
                row = await cursor.fetchone()
                assert row[0] == 'x' * size
                print(f'Read-only MySQL receive: {size} bytes complete; loop={type(asyncio.get_running_loop()).__name__}')
    finally:
        await connection.ensure_closed()
        await engine.dispose()


asyncio.run(main())
