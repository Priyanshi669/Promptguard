"""Test NVIDIA API connectivity."""

import asyncio

from openai import AsyncOpenAI

from promptguard.config import get_settings


async def main() -> None:
    settings = get_settings()

    print("Base URL:", settings.nvidia_base_url)
    print("Model:", settings.nvidia_model)

    client = AsyncOpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
    )

    response = await client.chat.completions.create(
        model=settings.nvidia_model,
        messages=[
            {
                "role": "user",
                "content": "Reply with exactly: test",
            }
        ],
    )

    print("Response:", response.choices[0].message.content)

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())