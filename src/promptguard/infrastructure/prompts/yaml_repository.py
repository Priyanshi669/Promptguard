from pathlib import Path

import yaml

from promptguard.infrastructure.path_safety import resolve_identifier_path


class PromptRepository:
    def __init__(self, prompts_dir: str | Path):
        self.prompts_dir = Path(prompts_dir)

    def load(self, version: str) -> dict:
        path = resolve_identifier_path(
            self.prompts_dir,
            version,
            ".yaml",
            kind="prompt version",
        )

        if not path.exists():
            raise FileNotFoundError(f"Prompt version not found: {version}")

        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)

        if not isinstance(data, dict):
            raise TypeError("Prompt configuration must be a mapping")

        return data
