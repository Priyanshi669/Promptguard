from pathlib import Path

import yaml


class PromptRepository:
    def __init__(self, prompts_dir: str | Path):
        self.prompts_dir = Path(prompts_dir)

    def load(self, version: str) -> dict:
        path = self.prompts_dir / f"{version}.yaml"

        if not path.exists():
            raise FileNotFoundError(f"Prompt version not found: {version}")

        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)

        if not isinstance(data, dict):
            raise TypeError("Prompt configuration must be a mapping")

        return data
