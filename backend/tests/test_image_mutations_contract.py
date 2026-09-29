import asyncio
import hashlib

import pytest

from app.config import get_settings
from app.errors import ApiError
from app.services import workspace_asset_service as images


def test_image_move_and_delete_require_revision_and_keep_extension():
    vault = get_settings().vault_path
    vault.mkdir(parents=True)
    original = vault / 'diagram.png'
    content = b'\x89PNG\r\n\x1a\nexample'
    original.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    with pytest.raises(ApiError):
        asyncio.run(images.move_image(images.ImageMoveRequest(
            path='diagram.png', destination='renamed.png', expected_content_hash='0' * 64)))
    assert original.read_bytes() == content
    with pytest.raises(ApiError):
        asyncio.run(images.move_image(images.ImageMoveRequest(
            path='diagram.png', destination='renamed.jpg', expected_content_hash=digest)))
    asyncio.run(images.move_image(images.ImageMoveRequest(
        path='diagram.png', destination='renamed.png', expected_content_hash=digest)))
    assert (vault / 'renamed.png').read_bytes() == content
    asyncio.run(images.delete_image(images.ImageDeleteRequest(
        path='renamed.png', expected_content_hash=digest)))
    assert not (vault / 'renamed.png').exists()


def test_managed_image_cannot_be_moved():
    with pytest.raises(ApiError) as error:
        asyncio.run(images.move_image(images.ImageMoveRequest(
            path=f'attachments/aa/{"a" * 64}.png', destination='other.png',
            expected_content_hash='a' * 64)))
    assert error.value.code == 'WORKSPACE_IMAGE_IMMUTABLE'
