import pytest
import respx
from showis_connectors import (
    CodeforcesConnector,
    LeetCodeConnector,
    MediumConnector,
    RedditConnector,
    SoundCloudConnector,
)


@pytest.mark.asyncio
async def test_reddit_connector_success():
    conn = RedditConnector()
    with respx.mock:
        respx.get("https://www.reddit.com/user/johndoe/about.json").respond(
            json={
                "data": {
                    "name": "johndoe",
                    "created_utc": 1600000000,
                    "total_karma": 1250,
                    "icon_img": "https://styles.redditmedia.com/t5_abc/styles/profileIcon_xyz.png?width=256",
                    "verified": True,
                    "is_gold": False,
                    "subreddit": {
                        "title": "John Doe",
                        "public_description": "Security researcher & developer",
                    },
                }
            }
        )
        res = await conn.fetch("johndoe")

    assert res is not None
    assert res.data["username"] == "johndoe"
    assert res.data["name"] == "John Doe"
    assert "Security researcher" in res.data["bio"]
    assert res.data["total_karma"] == 1250
    assert "profileIcon_xyz.png" in res.data["avatar_url"]
    assert "?" not in res.data["avatar_url"]


@pytest.mark.asyncio
async def test_reddit_connector_not_found_or_suspended():
    conn = RedditConnector()
    with respx.mock:
        respx.get("https://www.reddit.com/user/ghost/about.json").respond(status_code=404)
        res = await conn.fetch("ghost")
    assert res is None

    with respx.mock:
        respx.get("https://www.reddit.com/user/banned/about.json").respond(
            json={"data": {"is_suspended": True}}
        )
        res_suspended = await conn.fetch("banned")
    assert res_suspended is None


@pytest.mark.asyncio
async def test_medium_connector_success():
    conn = MediumConnector()
    xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title><![CDATA[Stories by Jane Smith on Medium]]></title>
        <description><![CDATA[Writing about technology, OSINT, and privacy.]]></description>
        <image>
          <url>https://cdn-images-1.medium.com/fit/c/150/150/abc.jpeg</url>
          <title>Jane Smith</title>
        </image>
      </channel>
    </rss>
    """
    with respx.mock:
        respx.get("https://medium.com/feed/@janesmith").respond(
            content=xml_content,
            headers={"Content-Type": "application/rss+xml"},
        )
        res = await conn.fetch("janesmith")

    assert res is not None
    assert res.data["name"] == "Jane Smith"
    assert res.data["username"] == "janesmith"
    assert "technology, OSINT" in res.data["bio"]
    assert "abc.jpeg" in res.data["avatar_url"]


@pytest.mark.asyncio
async def test_medium_connector_not_found():
    conn = MediumConnector()
    with respx.mock:
        respx.get("https://medium.com/feed/@missing").respond(status_code=404)
        res = await conn.fetch("missing")
    assert res is None


@pytest.mark.asyncio
async def test_leetcode_connector_success():
    conn = LeetCodeConnector()
    with respx.mock:
        respx.post("https://leetcode.com/graphql").respond(
            json={
                "data": {
                    "matchedUser": {
                        "username": "coder123",
                        "githubUrl": "https://github.com/coder123",
                        "twitterUrl": "https://twitter.com/coder123",
                        "linkedinUrl": "https://linkedin.com/in/coder123",
                        "profile": {
                            "realName": "Coder One",
                            "aboutMe": "Competitive programmer",
                            "userAvatar": "https://assets.leetcode.com/users/coder123/avatar.png",
                            "ranking": 4200,
                            "countryName": "Turkey",
                            "skillTags": ["Python", "Algorithms"],
                        },
                    }
                }
            }
        )
        res = await conn.fetch("coder123")

    assert res is not None
    assert res.data["username"] == "coder123"
    assert res.data["name"] == "Coder One"
    assert res.data["location"] == "Turkey"
    assert res.data["github_link"] == "https://github.com/coder123"
    assert len(res.data["links"]) == 3
    assert res.data["ranking"] == 4200


@pytest.mark.asyncio
async def test_leetcode_connector_not_found():
    conn = LeetCodeConnector()
    with respx.mock:
        respx.post("https://leetcode.com/graphql").respond(
            json={"data": {"matchedUser": None}}
        )
        res = await conn.fetch("nobody")
    assert res is None


@pytest.mark.asyncio
async def test_codeforces_connector_success():
    conn = CodeforcesConnector()
    with respx.mock:
        respx.get("https://codeforces.com/api/user.info?handles=tourist").respond(
            json={
                "status": "OK",
                "result": [
                    {
                        "handle": "tourist",
                        "firstName": "Gennady",
                        "lastName": "Korotkevich",
                        "country": "Belarus",
                        "city": "Gomel",
                        "organization": "ITMO University",
                        "avatar": "//userpic.codeforces.org/no-avatar.jpg",
                        "rating": 3800,
                        "rank": "legendary grandmaster",
                    }
                ],
            }
        )
        res = await conn.fetch("tourist")

    assert res is not None
    assert res.data["username"] == "tourist"
    assert res.data["name"] == "Gennady Korotkevich"
    assert res.data["location"] == "Gomel, Belarus"
    assert res.data["company"] == "ITMO University"
    assert res.data["avatar_url"] == "https://userpic.codeforces.org/no-avatar.jpg"
    assert res.data["rating"] == 3800


@pytest.mark.asyncio
async def test_codeforces_connector_failed_status():
    conn = CodeforcesConnector()
    with respx.mock:
        respx.get("https://codeforces.com/api/user.info?handles=unknown").respond(
            json={"status": "FAILED", "comment": "handles: User not found"}
        )
        res = await conn.fetch("unknown")
    assert res is None


@pytest.mark.asyncio
async def test_soundcloud_connector_success():
    conn = SoundCloudConnector()
    html_doc = """
    <html>
      <head>
        <meta property="og:title" content="DJ Kerem | Listen to tracks" />
        <meta property="og:description" content="Electronic music producer" />
        <meta property="og:image" content="https://i1.sndcdn.com/avatars-000123-t500x500.jpg" />
      </head>
      <body></body>
    </html>
    """
    with respx.mock:
        respx.get("https://soundcloud.com/djkerem").respond(
            text=html_doc,
            headers={"Content-Type": "text/html"},
        )
        res = await conn.fetch("djkerem")

    assert res is not None
    assert res.data["name"] == "DJ Kerem"
    assert res.data["username"] == "djkerem"
    assert res.data["bio"] == "Electronic music producer"
    assert "avatars-000123" in res.data["avatar_url"]
