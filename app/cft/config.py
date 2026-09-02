from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    spoolman_enabled: bool = False
    spoolman_url: str = 'http://spoolman:8000'
    spoolman_timeout_seconds: float = 10
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')


settings = Settings()
