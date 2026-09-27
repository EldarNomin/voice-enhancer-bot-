import asyncio

from voice_enhancer.config import settings
from voice_enhancer.infrastructure.database import initialize_database, make_engine


async def main() -> None:
    engine = make_engine(settings.database_url)
    try:
        await initialize_database(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
