"""Generate ideal summaries for the PromptGuard golden dataset."""

import asyncio
import json
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from promptguard.config import get_settings

INPUT_PATH = Path("dataset/v1/golden_dataset.json")
OUTPUT_PATH = Path("dataset/v1/golden_dataset.annotated.json")

SYSTEM_PROMPT = """You are annotating a benchmark dataset for a banking
customer-support LLM.

Given a customer message, write an ideal summary.

Requirements:
- Summarize only information explicitly present in the customer message.
- Do not invent facts, causes, actions, dates, amounts, or outcomes.
- Preserve the customer's actual intent or problem.
- Be concise and specific.
- Use one sentence.
- Do not mention that you are generating a summary.
- Do not provide advice or a solution.

Return ONLY valid JSON using exactly this schema:

{
  "ideal_summary": "one concise sentence"
}

The JSON field MUST be named "ideal_summary".
Do not use "summary", "response", "answer", or any other field name.
"""


class SummaryOutput(BaseModel):
    """Structured LLM output for a dataset annotation."""

    ideal_summary: str = Field(
        min_length=1,
        description="A concise one-sentence summary of the customer message.",
    )


def load_dataset() -> list[dict[str, object]]:
    """Load the existing golden dataset."""

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"Golden dataset not found: {INPUT_PATH}"
        )

    try:
        data = json.loads(
            INPUT_PATH.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {INPUT_PATH}"
        ) from exc

    if not isinstance(data, list):
        raise TypeError(
            "Golden dataset must contain a JSON array."
        )

    return data


def save_dataset(data: list[dict[str, object]]) -> None:
    """Save the annotated dataset."""

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT_PATH.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def create_client() -> AsyncOpenAI:
    """Create the NVIDIA OpenAI-compatible client."""

    settings = get_settings()

    return AsyncOpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
    )


@retry(
    retry=retry_if_exception_type(
        (TimeoutError, ConnectionError)
    ),
    wait=wait_exponential(
        multiplier=1,
        min=1,
        max=8,
    ),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def generate_summary(
    client: AsyncOpenAI,
    model: str,
    customer_message: str,
) -> str:
    """Generate one ideal summary."""

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": customer_message,
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("LLM returned an empty response.")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "LLM returned invalid JSON."
        ) from exc

    if "ideal_summary" not in parsed and "summary" in parsed:
      parsed["ideal_summary"] = parsed["summary"]

    result = SummaryOutput.model_validate(parsed)

    return result.ideal_summary.strip()

async def annotate_dataset(
    data: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Generate summaries for dataset cases."""

    settings = get_settings()
    client = create_client()

    annotated: list[dict[str, object]] = []

    for index, case in enumerate(data, start=1):
        case_copy = dict(case)

        existing_summary = case_copy.get("ideal_summary")

        if isinstance(existing_summary, str) and existing_summary.strip():
            print(
                f"[{index}/{len(data)}] "
                f"{case_copy['id']}: already annotated"
            )
            annotated.append(case_copy)
            continue

        customer_input = case_copy.get("input")

        if not isinstance(customer_input, str) or not customer_input.strip():
            raise ValueError(
                f"Case {case_copy.get('id')} has an invalid input."
            )

        print(
            f"[{index}/{len(data)}] "
            f"{case_copy['id']}: generating summary..."
        )

        summary = await generate_summary(
            client=client,
            model=settings.nvidia_model,
            customer_message=customer_input,
        )

        case_copy["ideal_summary"] = summary

        annotated.append(case_copy)

        print(f"    {summary}")

    await client.close()

    return annotated


async def main() -> None:
    """Run dataset annotation."""

    data = load_dataset()

    print(f"Loaded {len(data)} golden cases.")
    print(f"Using model: {get_settings().nvidia_model}")
    print()

    annotated = await annotate_dataset(data)

    save_dataset(annotated)

    print()
    print(f"Annotated cases: {len(annotated)}")
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())