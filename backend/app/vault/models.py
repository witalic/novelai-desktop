"""Pydantic DTOs for the vault API + on-disk documents.

The work-document models below (Component, Snapshot, Image, StackItem, WorkDoc) are the persisted
recipe contract — manual parity with ``frontend/src/types.ts``. When one side changes shape, change
the other and bump ``schema_version`` + add a migration (ROADMAP Phase 1). ``WorkDoc.canvas`` stays
an opaque dict on purpose: layout is frontend-owned, and validating it here would turn every
frontend-only layout change into a backend break (deliberate deviation from the roadmap letter).
"""
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---- config ----
class VaultInfo(BaseModel):
    dir: str
    initialized: bool
    writable: bool
    reachable: bool  # the folder answers now (a vault on a share or a drive may not, for a while)
    active: bool


class VaultConfig(BaseModel):
    active: str | None
    vaults: list[VaultInfo]
    proposed_default: str


class VaultPath(BaseModel):
    dir: str


class MoveVault(BaseModel):
    src: str
    dst: str


class MoveStatus(BaseModel):
    active: bool
    total: int
    done: int
    error: str | None = None


class AppSettings(BaseModel):
    autosave_interval_s: int
    theme: str
    accent: str
    download_dir: str


class PatchSettings(BaseModel):
    autosave_interval_s: int | None = None
    theme: str | None = None
    accent: str | None = None
    download_dir: str | None = None


class TokenStatus(BaseModel):
    set: bool


class SetToken(BaseModel):
    token: str


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
    """A frozen recipe: resolved text + params + seed at generation time, plus the component refs
    it was assembled from. Editing a Library block never mutates past snapshots."""

    id: str
    hash: str = ""
    components: list[Component] = Field(default_factory=list)
    assembled_positive: str = ""
    assembled_negative: str = ""
    params: dict[str, Any] = Field(default_factory=dict)  # the generation recipe (model, size, steps, …)
    created_at: str = ""


class Image(BaseModel):
    id: str
    snapshot_id: str | None = None
    # Zones define role, not survival: 'gallery' images surface in galleries/counts/previews;
    # 'scratch' is working material loose on the canvas — persisted, but never listed.
    role: Literal["gallery", "scratch"] = "gallery"
    ar: float | None = None  # true source aspect ratio (v2) — outlives a lost/dangling snapshot
    file: str = ""
    group: str | None = None
    favorite: bool = False
    tags: list[str] = Field(default_factory=list)      # manual tags (inherited come from the snapshot)
    description: str = ""
    created_at: str = ""
    source: str = "novelai"
    image_b64: str | None = Field(default=None, exclude=True)  # inline bytes on save; never persisted


class StackItem(BaseModel):
    id: str
    snapshot_id: str | None = None
    file: str = ""
    created_at: str = ""
    image_b64: str | None = Field(default=None, exclude=True)  # inline bytes on save; never persisted


class ComboBlock(Component):
    """One block of a saved combination — a composition row: the Library ref + frozen text copy of a
    component, plus whether it sat frozen (kept in the area, out of the prompt)."""

    frozen: bool = False


class Combo(BaseModel):
    """A named, ordered set of composition blocks saved in the work (blocks only — no generation params),
    for restoring or seeding the station in one click. ``origin`` marks combinations an agent created."""

    id: str
    name: str = ""
    blocks: list[ComboBlock] = Field(default_factory=list)
    origin: Literal["user", "agent"] = "user"
    created_at: str = ""
    updated_at: str = ""


class WorkDoc(BaseModel):
    schema_version: int = 7  # bump together with a migrate.py step + frontend serialize.ts
    id: str
    title: str = ""
    slug: str = ""
    created_at: str = ""
    updated_at: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    canvas: dict[str, Any] = Field(default_factory=dict)   # frontend owns the layout; stored opaquely (see module docstring)
    snapshots: list[Snapshot] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    stack: list[StackItem] = Field(default_factory=list)   # the generation output pile (drafts)
    favorites: list[str] = Field(default_factory=list)     # Library block ids starred for quick access in this work
    combos: list[Combo] = Field(default_factory=list)      # saved block sets for this work (v7)
    preview_image_id: str | None = None


class BlockDoc(BaseModel):
    id: str
    category: str = "custom"                              # category slug
    name: str = ""
    text: str = ""
    polarity: Literal["positive", "negative"] = "positive"
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


class CategoryDoc(BaseModel):
    slug: str
    name: str
    color: str = "#738496"


class CategoryCount(CategoryDoc):
    count: int = 0
    builtin: bool = False  # a default category — cannot be deleted


class SaveCategory(BaseModel):
    name: str
    color: str = "#738496"
    slug: str | None = None  # set to update an existing category (e.g. recolor) without re-deriving the slug


class RestoreCategories(BaseModel):
    slugs: list[str]  # built-in category slugs to un-tombstone (bring back after deletion)


class ReorderCategories(BaseModel):
    slugs: list[str]  # the full category order (slugs), top to bottom


class BlocksPage(BaseModel):
    items: list[BlockDoc]
    total: int
    page: int
    per_page: int


class TagCount(BaseModel):
    name: str
    count: int


# ---- presets (generation-param bundles) ----
class PresetParams(BaseModel):
    """The generation-param subset a preset stores — mirrors GenerateParams minus prompt/negative
    (the canvas) and seed (per-generation). Keep the field set + constraints in sync with
    ``novelai/models.py::GenerateParams`` and ``frontend/src/types.ts::PanelParams``."""

    model: str = "nai-diffusion-5-full"
    width: int = Field(default=832, ge=64, le=2048)
    height: int = Field(default=1216, ge=64, le=2048)
    steps: int = Field(default=28, ge=1, le=50)
    scale: float = Field(default=7.0, ge=0, le=10)  # the default model's catalog scale (the single source of truth)
    sampler: str = "k_euler_ancestral"
    n_samples: int = Field(default=1, ge=1, le=4)
    noise_schedule: str = "karras"
    cfg_rescale: float = Field(default=0.0, ge=0, le=1)
    quality_toggle: bool = True
    uc_preset: int = Field(default=4, ge=0, le=7)
    # Frontend-only prompt optimisation (merge repeated tags), not a NovelAI field — persisted so a
    # preset remembers the choice. See frontend/src/canvas/dedup.ts.
    dedupe: bool = False


class PresetDoc(BaseModel):
    """On-disk user preset (``presets/<id>.json``). Built-ins are code-shipped, never written here."""

    id: str
    name: str = ""
    params: PresetParams = Field(default_factory=PresetParams)
    created_at: str = ""
    updated_at: str = ""


class Preset(BaseModel):
    """API view of a preset: the doc plus overlay flags (``presets/.state.json``) and the read-only
    ``builtin`` marker. ``default``/``favorite`` live in the overlay because they also apply to
    built-ins, which have no writable file."""

    id: str
    name: str
    params: PresetParams
    builtin: bool = False
    favorite: bool = False
    is_default: bool = False
    created_at: str = ""
    updated_at: str = ""


class SetDefault(BaseModel):
    id: str


class SetFavorite(BaseModel):
    favorite: bool


# ---- list / gallery DTOs ----
class WorkMatch(BaseModel):
    """Where a work matched a search its title didn't: a section, heading or note of its gallery (``text``) — or
    ``count`` of its gallery images."""

    kind: Literal["section", "heading", "note", "images"]
    text: str | None = None
    count: int = 0


class WorkListItem(BaseModel):
    id: str
    title: str
    updated_at: str
    image_count: int
    preview_url: str | None
    match: WorkMatch | None = None


class WorksPage(BaseModel):
    items: list[WorkListItem]
    total: int
    page: int
    per_page: int
    in_vault: int  # every work, the filters aside


class GalleryItem(BaseModel):
    image_id: str
    work_id: str
    url: str
    favorite: bool
    group: str | None
    created_at: str


class GalleryAlbum(BaseModel):
    """The album a gallery image sits in (``gallery_layout.Placement``)."""

    grid_id: str
    number: int
    heading: str | None = None
    section: str | None = None


class GalleryImage(BaseModel):
    """An image as the global gallery lists it. ``album`` None: unsorted — in its work's Quick access."""

    image_id: str
    work_id: str
    work_title: str
    url: str
    favorite: bool
    created_at: str
    ar: float | None = None
    model: str | None = None
    album: GalleryAlbum | None = None


class GalleryPage(BaseModel):
    items: list[GalleryImage]
    total: int
    page: int
    per_page: int
    next: str | None = None  # the cursor of the page after this one (``after``); None: this is the last


class WorkCount(BaseModel):
    id: str
    title: str
    count: int


class GalleryFacets(BaseModel):
    """What the gallery's filters offer for the current ones: tags of the listed images, the models and works to pick
    (each with what it would list), and ``unsorted`` — how many images in no album match the rest."""

    total: int
    unsorted: int
    tags: list[TagCount]
    models: list[TagCount]
    works: list[WorkCount]


class WorkChanges(BaseModel):
    at: str  # the stored copy's updated_at — the app's new lock base once it has applied ``ops``
    ops: list[dict]


class AlbumInfo(GalleryAlbum):
    count: int  # the gallery images it shows


class ImageRef(BaseModel):
    work_id: str = Field(max_length=100)
    image_id: str = Field(max_length=100)


class ImagesBody(BaseModel):
    """Gallery images picked across works — the gallery's selection."""

    images: list[ImageRef] = Field(min_length=1, max_length=200)

    def pairs(self) -> list[tuple[str, str]]:
        return [(r.work_id, r.image_id) for r in self.images]


class TransferBody(ImagesBody):
    to_work_id: str = Field(max_length=100)
    copy_: bool = Field(default=False, alias="copy")  # move unless asked to copy
    grid_id: str | None = Field(default=None, max_length=100)  # one of its albums — None: its Quick access


class Deleted(BaseModel):
    work_id: str
    deleted: list[str]
    error: str | None = None  # why this work's images couldn't go


class Transferred(BaseModel):
    work_id: str
    ids: dict[str, str]  # each image's id in the target
    copied: bool
    not_removed: str | None = None  # a move that landed as a copy: why they stayed here too
    error: str | None = None  # why this work's images couldn't go


class ImageDetail(GalleryImage):
    """One gallery image with its recipe: the prompt as sent, the blocks it was made of, its settings."""

    description: str = ""
    tags: list[str] = Field(default_factory=list)        # its own
    block_tags: list[str] = Field(default_factory=list)  # from the positive blocks it was made of
    positive: str = ""
    negative: str = ""
    components: list[Component] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
