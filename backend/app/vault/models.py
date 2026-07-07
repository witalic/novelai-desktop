"""Pydantic DTOs for the vault API + on-disk documents."""
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---- config ----
class VaultConfig(BaseModel):
    vault_dir: str | None
    initialized: bool
    writable: bool
    proposed_default: str


class SetVaultConfig(BaseModel):
    vault_dir: str


# ---- work documents ----
class Component(BaseModel):
    source: Literal["library", "custom"] = "custom"
    block_id: str | None = None
    version: int | None = None
    name: str = ""
    text: str = ""
    polarity: str = "positive"
    category: str | None = None
    tags: list[str] = Field(default_factory=list)


class Snapshot(BaseModel):
    id: str
    hash: str = ""
    components: list[Component] = Field(default_factory=list)
    assembled_positive: str = ""
    assembled_negative: str = ""
    created_at: str = ""


class Image(BaseModel):
    id: str
    snapshot_id: str | None = None
    file: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    group: str | None = None
    favorite: bool = False
    tags: list[str] = Field(default_factory=list)      # manual tags (inherited come from the snapshot)
    description: str = ""
    created_at: str = ""
    source: str = "novelai"
    image_b64: str | None = Field(default=None, exclude=True)  # inline bytes on save; never persisted


class WorkDoc(BaseModel):
    schema_version: int = 1
    id: str
    title: str = ""
    slug: str = ""
    created_at: str = ""
    updated_at: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    canvas: dict[str, Any] = Field(default_factory=dict)   # frontend owns the layout; stored opaquely
    snapshots: list[Snapshot] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    preview_image_id: str | None = None


class BlockDoc(BaseModel):
    id: str
    category: str = "custom"
    name: str = ""
    text: str = ""
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


# ---- list / gallery DTOs ----
class WorkListItem(BaseModel):
    id: str
    title: str
    updated_at: str
    image_count: int
    preview_url: str | None


class WorksPage(BaseModel):
    items: list[WorkListItem]
    total: int
    page: int
    per_page: int


class GalleryItem(BaseModel):
    image_id: str
    work_id: str
    url: str
    favorite: bool
    group: str | None
    created_at: str


class GalleryPage(BaseModel):
    items: list[GalleryItem]
    total: int
    page: int
    per_page: int
