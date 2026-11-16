import io

import pytest
import respx
from PIL import Image, ImageDraw
from showis_contracts import EdgeType, Entity, EntityType, Platform
from showis_engine.links import (
    build_avatar_match_edges,
    compute_image_dhash,
    fetch_avatar_hashes,
    hamming_distance,
)


def _create_test_image(color="blue", size=(150, 150)) -> bytes:
    im = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(im)
    draw.rectangle([20, 20, size[0] - 20, size[1] - 20], fill=color)
    draw.line([(0, 0), size], fill="red", width=4)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_dhash_similar_images_low_distance():
    # 200x200 PNG vs 80x80 JPEG of same composition
    img1 = _create_test_image(color="blue", size=(200, 200))

    im_orig = Image.open(io.BytesIO(img1))
    im_small = im_orig.resize((80, 80))
    buf = io.BytesIO()
    im_small.save(buf, format="JPEG", quality=75)
    img2 = buf.getvalue()

    h1 = compute_image_dhash(img1)
    h2 = compute_image_dhash(img2)

    assert h1 is not None
    assert h2 is not None
    dist = hamming_distance(h1, h2)
    # A resized and re-encoded image should have a very small Hamming distance (<= 4)
    assert dist <= 4


def test_dhash_different_images_high_distance():
    img_a = _create_test_image(color="blue", size=(100, 100))
    # Different image: all green circle
    im = Image.new("RGB", (100, 100), color="black")
    draw = ImageDraw.Draw(im)
    draw.ellipse([10, 10, 90, 90], fill="green")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    img_b = buf.getvalue()

    h1 = compute_image_dhash(img_a)
    h2 = compute_image_dhash(img_b)

    dist = hamming_distance(h1, h2)
    assert dist >= 10


def test_build_avatar_match_edges_dhash():
    # Entity A on GitHub and Entity B on TryHackMe with slightly different dHash
    # but distance <= 3 -> SAME_PERSON edge produced
    ent_a = Entity(
        id="github:alice",
        type=EntityType.ACCOUNT,
        platform=Platform.GITHUB,
        label="alice",
        attributes={"avatar_dhash": "c0e4f4d8c4d2d8d4"},
    )
    ent_b = Entity(
        id="tryhackme:alice",
        type=EntityType.ACCOUNT,
        platform=Platform.TRYHACKME,
        label="alice",
        attributes={"avatar_dhash": "c0ecb4dcc4d2d8d4"},  # Hamming distance = 3
    )
    ent_c_blank = Entity(
        id="gitlab:blank",
        type=EntityType.ACCOUNT,
        platform=Platform.GITLAB,
        label="blank",
        attributes={"avatar_dhash": "0000000000000000"},  # Blank avatar
    )

    edges = build_avatar_match_edges([ent_a, ent_b, ent_c_blank])
    assert len(edges) == 1
    edge = edges[0]
    assert edge.type == EdgeType.SAME_PERSON
    assert edge.source_entity_id == "github:alice"
    assert edge.target_entity_id == "tryhackme:alice"
    assert "dHash" in edge.reason
    assert edge.confidence >= 0.70


@pytest.mark.asyncio
async def test_fetch_avatar_hashes_success():
    img_bytes = _create_test_image()
    with respx.mock:
        respx.get("https://avatars.example.com/u1.png").respond(
            content=img_bytes, headers={"Content-Type": "image/png"}
        )
        sha, dhash = await fetch_avatar_hashes("https://avatars.example.com/u1.png")

    assert sha is not None
    assert len(sha) == 64
    assert dhash is not None
    assert len(dhash) == 16
