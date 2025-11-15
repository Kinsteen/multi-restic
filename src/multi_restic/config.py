import tomllib

from multi_restic.types import AgentConfig


class Config:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config_data = self.load_config()

    def load_config(self):
        with open(self.config_path, "rb") as f:
            return tomllib.load(f)

    def get_agents(self) -> dict[str, AgentConfig]:
        return self.config_data.get("agent", {})
