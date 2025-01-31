import orjson
from yarl import URL
import asyncio
import aiohttp
from icecream import ic


async def get_request(url: str, retries=3):
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}

    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    return await response.read()
        except aiohttp.ClientConnectionError as e:
            print(f"Connection closed (Attempt {attempt + 1}/{retries}): {e}")
            if attempt == retries - 1:
                raise  # Give up after max retries
            await asyncio.sleep(1)  # Wait before retrying


async def github_proxifly():
    url = URL("https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.json")
    response_text = await get_request(url)

    try:
        response_text = response_text.decode("utf-8")
        proxies = orjson.loads(response_text)
        return proxies
    except orjson.JSONDecodeError:
        print("Failed to parse JSON")
        return []


if __name__ == '__main__':
    import time


    class Timer:
        def __init__(self):
            self.start = 0
            self.end = 0

        def __enter__(self):
            self.start = time.time()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.end = time.time()
            ic(f"Time: {self.end - self.start}")


    timer = Timer()


    async def main():
        with timer:
            proxies = await github_proxifly()
        ic(len(proxies))


    asyncio.run(main())
