from datetime import datetime

from pydantic import BaseModel, Field


class GithubProfile(BaseModel):
    login: str
    name: str | None = None
    bio: str | None = None
    company: str | None = None
    location: str | None = None
    blog: str | None = None
    twitter_username: str | None = None
    public_email: str | None = None
    avatar_url: str | None = None
    avatar_sha256: str | None = None
    followers: int | None = None
    following: int | None = None
    public_repos: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    repo_names: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    # {"twitter": "...", "linkedin": "..."}
    social_accounts: dict[str, str] = Field(default_factory=dict)
    commit_emails: list[str] = Field(default_factory=list)  # herkese açık commit e-postaları
    gpg_keys: list[str] = Field(default_factory=list)


class ThmProfile(BaseModel):
    username: str
    display_name: str | None = None
    rank: int | None = None
    streak: int | None = None
    rooms_completed: int | None = None
    badges: list[str] = Field(default_factory=list)
    avatar_url: str | None = None
    avatar_sha256: str | None = None
    links: list[str] = Field(default_factory=list)  # bio'daki sosyal linkler
    github_link: str | None = None
    twitter_link: str | None = None
    telegram_link: str | None = None


class HtbProfile(BaseModel):
    username: str | None = None
    profile_id: str | None = None
    display_name: str | None = None
    rank: str | None = None
    machines_completed: int | None = None
    country: str | None = None
    avatar_url: str | None = None
    avatar_sha256: str | None = None
    links: list[str] = Field(default_factory=list)
    github_link: str | None = None
    twitter_link: str | None = None
    telegram_link: str | None = None


class WebProfile(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None
    extracted_usernames: list[str] = Field(default_factory=list)
    extracted_names: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
