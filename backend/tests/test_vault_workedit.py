"""Backend edits of stored works (``app.vault.workedit``): logged for the open app, and images moved or copied
between works with their files and recipes — fully offline (tmp vault)."""
import base64
from collections import OrderedDict

import pytest
from fastapi import HTTPException

from app import appconfig
from app.novelai._png import solid_png
from app.settings import Settings, VaultSettings
from app.vault import gallery_layout, service, workedit, worklog
from app.vault.models import Component, Image, Snapshot, WorkDoc


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(worklog, "_log", OrderedDict())
    return Settings(vault=VaultSettings(dir=str(tmp_path / "vault"), state_dir=str(tmp_path / "state")), _env_file=None)


def _png(w: int = 16, h: int = 24) -> str:
    return base64.b64encode(solid_png(w, h)).decode("ascii")


def seed(settings: Settings) -> None:
    recipe = Snapshot(id="snap-1", assembled_positive="1girl, moon", params={"model": "nai-diffusion-4-5-full"},
                      components=[Component(name="Moon", text="moon", tags=["night"])])
    src = WorkDoc(id="work-src", title="Moon studies", snapshots=[recipe], images=[
        Image(id="img-a", snapshot_id="snap-1", image_b64=_png(16, 24), favorite=True, tags=["mine"]),
        Image(id="img-b", image_b64=_png(24, 16))])
    gallery_layout.blocks(src).append({"id": "grid-1", "type": "grid", "imageIds": ["img-a", "img-b"], "cols": 3})
    service.save_work(settings, src)
    service.save_work(settings, WorkDoc(id="work-dst", title="Picks"))


def test_a_move_copies_the_files_and_recipes_in_and_takes_them_out_of_the_source(settings):
    seed(settings)
    src_base = service.load_work(settings, "work-src").updated_at
    out = workedit.transfer_images(settings, "work-src", "work-dst", ["img-a", "img-a"], copy=False)
    new = out.ids["img-a"]
    assert (out.copied, out.not_removed, list(out.ids)) == (False, None, ["img-a"])
    dst, src = service.load_work(settings, "work-dst"), service.load_work(settings, "work-src")
    moved = next(im for im in dst.images if im.id == new)
    recipe = next(s for s in dst.snapshots if s.id == moved.snapshot_id)
    assert (moved.favorite, moved.tags, moved.file, recipe.id != "snap-1") == (True, ["mine"], f"images/{new}.png", True)
    assert recipe.assembled_positive == "1girl, moon"
    assert gallery_layout.grids(gallery_layout.stack(dst)) == []  # it lands unsorted, in Quick access
    assert service.image_path(settings, "work-dst", new).read_bytes() == base64.b64decode(_png(16, 24))
    assert [im.id for im in src.images] == ["img-b"]
    assert gallery_layout.grids(gallery_layout.stack(src))[0]["imageIds"] == ["img-b"]  # out of its album too
    # Both open copies catch up by replaying what was logged.
    assert worklog.edits_since("work-src", src_base, src.updated_at) == [{"op": "images_deleted", "ids": ["img-a"]}]


def test_a_copy_leaves_the_source_alone(settings):
    seed(settings)
    src_before = service.load_work(settings, "work-src")
    out = workedit.transfer_images(settings, "work-src", "work-dst", ["img-b"], copy=True)
    assert out.copied and service.load_work(settings, "work-src") == src_before
    assert "work-src" not in worklog._log


def test_a_move_that_cannot_remove_them_lands_as_a_copy_and_says_why(settings, monkeypatch):
    seed(settings)
    edit = workedit.edit_work

    def flaky(settings, work_id, change, adopt=None):
        if work_id == "work-src":
            raise HTTPException(status_code=409, detail="A vault move is in progress — try again in a moment.")
        return edit(settings, work_id, change, adopt)

    monkeypatch.setattr(workedit, "edit_work", flaky)
    out = workedit.transfer_images(settings, "work-src", "work-dst", ["img-a"], copy=False)
    assert out.copied and out.not_removed == ("Removing them from “Moon studies” failed: "
                                              "A vault move is in progress — try again in a moment.")
    assert {im.id for im in service.load_work(settings, "work-src").images} == {"img-a", "img-b"}
    assert [im.id for im in service.load_work(settings, "work-dst").images] == [out.ids["img-a"]]


def test_nothing_moves_unless_every_image_is_a_gallery_image_and_the_target_exists(settings):
    seed(settings)
    with pytest.raises(HTTPException) as missing:
        workedit.transfer_images(settings, "work-src", "work-dst", ["img-a", "img-nope"], copy=False)
    assert missing.value.detail == "No gallery image “img-nope” in “Moon studies”."
    with pytest.raises(HTTPException) as gone:
        workedit.transfer_images(settings, "work-src", "work-nope", ["img-a"], copy=False)
    assert gone.value.status_code == 404
    assert service.load_work(settings, "work-dst").images == []
    assert len(service.load_work(settings, "work-src").images) == 2


def test_an_image_gone_from_the_source_mid_move_changes_nothing_and_leaves_no_files(settings, monkeypatch):
    seed(settings)
    edit = workedit.edit_work

    def source_saved_without_img_b(settings, work_id, change, adopt=None):
        if adopt:  # the app saved the source without img-b between the lookup and the target's save
            service.image_path(settings, "work-src", "img-b").unlink()
        return edit(settings, work_id, change, adopt)

    monkeypatch.setattr(workedit, "edit_work", source_saved_without_img_b)
    with pytest.raises(HTTPException) as exc:
        workedit.transfer_images(settings, "work-src", "work-dst", ["img-a", "img-b"], copy=False)
    assert (exc.value.status_code, exc.value.detail) == (
        409, "An image left “Moon studies” while it was being moved — nothing changed, try again.")
    works = appconfig.active_vault(settings) / "works"
    images = next(d for d in works.iterdir() if d.name.endswith("work-dst")) / "images"
    assert list(images.iterdir()) == []  # the copy made before the failing one is taken back out
    assert service.load_work(settings, "work-dst").images == []
    assert service.image_path(settings, "work-src", "img-a").is_file()


def test_a_transfer_needs_images_and_another_work(settings):
    seed(settings)
    for args, detail in [((["img-a"], "work-src"), "The images are in that work already."),
                         (([], "work-dst"), "Name at least one image.")]:
        ids, dst = args
        with pytest.raises(HTTPException) as exc:
            workedit.transfer_images(settings, "work-src", dst, ids, copy=True)
        assert (exc.value.status_code, exc.value.detail) == (400, detail)


def test_a_save_copies_in_only_the_vaults_own_files(settings, tmp_path):
    seed(settings)
    outside = tmp_path / "secret.png"
    outside.write_bytes(solid_png(8, 8))
    doc = service.load_work(settings, "work-dst")
    doc.images.append(Image(id="img-x"))
    with pytest.raises(ValueError):
        service.save_work(settings, doc, adopt={"img-x": outside})
    assert service.load_work(settings, "work-dst").images == []


def test_an_edit_with_nothing_to_change_saves_nothing(settings):
    seed(settings)
    before = service.load_work(settings, "work-dst").updated_at
    doc, ops = workedit.edit_work(settings, "work-dst", lambda doc: [])
    assert (ops, doc.updated_at, service.load_work(settings, "work-dst").updated_at) == ([], before, before)
