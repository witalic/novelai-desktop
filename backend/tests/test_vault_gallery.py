"""The global gallery and the Works filters over the index: where each image sits (an album or unsorted), every
filter, the facets the filter bar offers, an image's recipe — fully offline (tmp vault)."""
import base64
import sqlite3

import httpx
import pytest

from app import appconfig
from app.main import app
from app.novelai._png import solid_png
from app.settings import Settings, VaultSettings, get_settings
from app.vault import gallery_layout, index, service
from app.vault.models import Component, Image, Snapshot, WorkDoc

PNG = base64.b64encode(solid_png(8, 12)).decode("ascii")


@pytest.fixture
def settings(tmp_path):
    return Settings(vault=VaultSettings(dir=str(tmp_path / "vault"), state_dir=str(tmp_path / "state")), _env_file=None)


@pytest.fixture
async def api(settings):
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def zone(*blocks: dict) -> dict:
    return {"nodes": [{"id": "gallery", "type": "zone", "data": {"role": "gallery", "blocks": list(blocks)}}]}


def grid(gid: str, *ids: str) -> dict:
    return {"id": gid, "type": "grid", "imageIds": list(ids), "cols": 3}


def snap(sid: str, positive: str, model: str = "nai-diffusion-4-5-full", tags: tuple[str, ...] = (),
         name: str = "Block") -> Snapshot:
    return Snapshot(id=sid, assembled_positive=positive, assembled_negative="bad hands, lowres",
                    params={"model": model, "width": 832, "height": 1216, "seed": 1},
                    components=[Component(name=name, text=positive, tags=list(tags))])


def image(iid: str, sid: str | None = None, at: str = "", **kw) -> Image:
    return Image(id=iid, snapshot_id=sid, image_b64=PNG, created_at=at, **kw)


def seed(settings: Settings) -> None:
    """Moon studies: a section with a heading and two albums, plus one unsorted image and a scratch one.
    Rainy city: no gallery zone yet (an agent's), so everything in it is unsorted."""
    service.save_work(settings, WorkDoc(
        id="work-moon", title="Moon studies",
        snapshots=[snap("s-1", "1girl, full moon", tags=("night", "moon"), name="Moonlit night"),
                   snap("s-2", "silver hair, луна", model="nai-diffusion-3", tags=("night",))],
        images=[image("m-1", "s-1", "2026-10-20T10:00:00.000Z", favorite=True, description="first light"),
                image("m-2", "s-2", "2026-10-21T10:00:00.000+00:00", tags=["portrait"]),
                image("m-3", "s-1", "2026-10-22T10:00:00.000Z"),
                image("m-4", "s-1", "2026-10-23T10:00:00.000Z"),
                image("m-scr", "s-1", "2026-10-24T10:00:00.000Z", role="scratch", tags=["scratchy"])],
        canvas=zone({"id": "sec", "type": "section", "title": "Silver night", "collapsed": False},
                    {"id": "h", "type": "heading", "text": "Harvest moon", "level": 2},
                    grid("g-1", "m-1", "m-2"),
                    {"id": "note", "type": "text", "text": "Try the comet next"},
                    grid("g-2", "m-3", "m-1"))))  # m-1 again: it belongs to the first album
    service.save_work(settings, WorkDoc(
        id="work-rain", title="Rainy city", snapshots=[snap("s-r", "rain, city at night", tags=("city", "night"))],
        images=[image("r-1", "s-r", "2026-10-25T10:00:00.000Z")]))


# ---- where an image sits ----
def test_placements_follow_the_gallery_as_the_app_shows_it(settings):
    seed(settings)
    moon = service.load_work(settings, "work-moon")
    placed = gallery_layout.placements(moon)
    assert {i: (p.grid_id, p.number, p.heading, p.section) for i, p in placed.items()} == {
        "m-1": ("g-1", 1, "Harvest moon", "Silver night"), "m-2": ("g-1", 1, "Harvest moon", "Silver night"),
        "m-3": ("g-2", 2, "Harvest moon", "Silver night")}  # m-4 is unsorted; scratch never counts
    assert gallery_layout.texts(moon) == [("section", "Silver night"), ("heading", "Harvest moon"),
                                          ("note", "Try the comet next")]
    assert gallery_layout.placements(service.load_work(settings, "work-rain")) == {}  # no zone: all unsorted
    seeded = WorkDoc(id="w", images=[Image(id="a", created_at="2"), Image(id="b", created_at="1")],
                     canvas={"nodes": [{"id": "gallery", "type": "zone", "data": {"role": "gallery"}}]})
    assert {i: p.grid_id for i, p in gallery_layout.placements(seeded).items()} == {"a": "gb-seed", "b": "gb-seed"}


# ---- the gallery ----
async def ids(api: httpx.AsyncClient, **params) -> list[str]:
    res = await api.get("/api/vault/gallery", params=params)
    assert res.status_code == 200, res.text
    return [i["image_id"] for i in res.json()["items"]]


async def test_newest_first_with_where_each_sits(api, settings):
    seed(settings)
    page = (await api.get("/api/vault/gallery")).json()
    assert [i["image_id"] for i in page["items"]] == ["r-1", "m-4", "m-3", "m-2", "m-1"]  # never the scratch one
    by_id = {i["image_id"]: i for i in page["items"]}
    assert by_id["m-2"]["album"] == {"grid_id": "g-1", "number": 1, "heading": "Harvest moon", "section": "Silver night"}
    assert by_id["m-4"]["album"] is None and by_id["r-1"]["work_title"] == "Rainy city"
    assert (by_id["m-1"]["ar"], by_id["m-2"]["model"]) == (pytest.approx(832 / 1216), "nai-diffusion-3")
    assert await ids(api, order="oldest") == ["m-1", "m-2", "m-3", "m-4", "r-1"]
    assert [await ids(api, per_page=2, page=p) for p in (1, 2, 3)] == [["r-1", "m-4"], ["m-3", "m-2"], ["m-1"]]


async def test_unsorted_images_are_left_out_included_or_alone(api, settings):
    seed(settings)
    assert await ids(api, unsorted="exclude") == ["m-3", "m-2", "m-1"]
    assert await ids(api, unsorted="only") == ["r-1", "m-4"]


async def test_text_search_reads_prompts_descriptions_tags_blocks_and_where_it_sits(api, settings):
    seed(settings)
    assert await ids(api, q="FULL MOON") == ["m-4", "m-3", "m-1"]  # the prompt, any case
    assert await ids(api, q="ЛУНА") == ["m-2"]  # casefolded beyond ASCII
    assert await ids(api, q="first light") == ["m-1"]  # the description
    assert await ids(api, q="portrait") == ["m-2"]  # its own tag
    assert await ids(api, q="moonlit") == ["m-4", "m-3", "m-1"]  # a block it was made of
    assert await ids(api, q="silver night") == ["m-3", "m-2", "m-1"]  # its section
    assert await ids(api, q="bad hands") == []  # never the negative prompt
    assert await ids(api, q="100%") == []  # wildcards are literal


async def test_tags_works_models_favourites_and_dates_narrow_it(api, settings):
    seed(settings)
    assert await ids(api, tags=["night", "moon"]) == ["m-4", "m-3", "m-1"]  # all of them
    assert await ids(api, tags=["portrait", "night"]) == ["m-2"]  # its own and its blocks' alike
    assert await ids(api, work=["work-rain"]) == ["r-1"]
    assert await ids(api, model=["nai-diffusion-3", "nai-diffusion-4-5-full"], work=["work-moon"]) == [
        "m-4", "m-3", "m-2", "m-1"]
    assert await ids(api, model=["nai-diffusion-3"]) == ["m-2"]
    assert await ids(api, favorite="true") == ["m-1"]
    # "Z" and "+00:00" stamps alike; until is exclusive
    assert await ids(api, since="2026-10-21T10:00:00Z", until="2026-10-23T10:00:00+00:00") == ["m-3", "m-2"]
    assert await ids(api, since="2026-10-21T13:00:00+03:00", until="2026-10-21T13:00:00.001+03:00") == ["m-2"]
    for bad in ({"tags": [f"t{i}" for i in range(21)]}, {"since": "2026-10-21"}, {"until": "2026-10-21T00:00"},
                {"after": "soon"}):
        assert (await api.get("/api/vault/gallery", params=bad)).status_code == 422, bad  # a moment needs its offset
    assert await ids(api, tags=["night", " night ", ""]) == await ids(api, tags=["night"])  # as the index keeps them


async def test_pages_follow_the_last_image_seen_so_new_ones_shift_nothing(api, settings):
    seed(settings)
    first = (await api.get("/api/vault/gallery", params={"per_page": 2})).json()
    assert [i["image_id"] for i in first["items"]] == ["r-1", "m-4"] and first["next"]
    service.save_work(settings, WorkDoc(id="work-new", title="New", images=[image("n-1", at="2026-10-26T00:00:00Z")]))
    second = (await api.get("/api/vault/gallery", params={"per_page": 2, "after": first["next"]})).json()
    third = (await api.get("/api/vault/gallery", params={"per_page": 2, "after": second["next"]})).json()
    assert [i["image_id"] for i in second["items"] + third["items"]] == ["m-3", "m-2", "m-1"]
    assert (third["next"], third["total"]) == (None, 6)
    oldest = (await api.get("/api/vault/gallery", params={"per_page": 2, "order": "oldest"})).json()
    after = (await api.get("/api/vault/gallery", params={"per_page": 2, "order": "oldest", "after": oldest["next"]}))
    assert [i["image_id"] for i in after.json()["items"]] == ["m-3", "m-4"]


async def test_a_negative_block_is_no_part_of_what_the_gallery_finds_or_offers(api, settings):
    seed(settings)
    negative = Component(name="Quality negative", text="lowres", polarity="negative", tags=["quality"])
    recipe = snap("s-n", "a cat")
    recipe.components.append(negative)
    service.save_work(settings, WorkDoc(id="work-cat", title="Cats", snapshots=[recipe],
                                        images=[image("c-1", "s-n", "2026-10-26T00:00:00Z")]))
    assert await ids(api, q="quality negative") == [] and await ids(api, tags=["quality"]) == []
    facets = (await api.get("/api/vault/gallery/facets", params={"work": ["work-cat"]})).json()
    assert [t["name"] for t in facets["tags"]] == []
    assert "quality" not in {t["name"] for t in (await api.get("/api/vault/works/tags")).json()}
    assert (await api.get("/api/vault/gallery/work-cat/c-1")).json()["block_tags"] == []
    # The Library's examples of that block still find where it was used.
    examples = (await api.get("/api/vault/library/examples", params={"tags": ["quality"]})).json()
    assert [e["image_id"] for e in examples] == ["c-1"]


async def test_facets_offer_what_each_filter_could_pick(api, settings):
    seed(settings)
    f = (await api.get("/api/vault/gallery/facets", params={"unsorted": "exclude", "model": ["nai-diffusion-3"]})).json()
    assert (f["total"], f["unsorted"]) == (1, 0)
    assert {t["name"]: t["count"] for t in f["tags"]} == {"night": 1, "portrait": 1}
    # picking another model would list these — the model filter itself is left out of its own counts
    assert f["models"] == [{"name": "nai-diffusion-4-5-full", "count": 2}, {"name": "nai-diffusion-3", "count": 1}]
    f = (await api.get("/api/vault/gallery/facets", params={"unsorted": "exclude", "work": ["work-rain"],
                                                            "tags": ["city"]})).json()
    assert (f["total"], f["unsorted"]) == (0, 1)  # r-1 matches, but it's unsorted
    assert f["works"] == [{"id": "work-rain", "title": "Rainy city", "count": 0}]  # picked, so offered
    assert f["tags"] == [{"name": "city", "count": 0}]
    f = (await api.get("/api/vault/gallery/facets")).json()
    assert [(w["id"], w["count"]) for w in f["works"]] == [("work-moon", 4), ("work-rain", 1)]
    assert f["tags"][0] == {"name": "night", "count": 5}
    f = (await api.get("/api/vault/gallery/facets", params={"work": ["work-rain"], "model": ["nai-diffusion-3"]})).json()
    assert f["total"] == 0  # a picked model nothing matches stays on offer, to be unpicked
    assert f["models"] == [{"name": "nai-diffusion-4-5-full", "count": 1}, {"name": "nai-diffusion-3", "count": 0}]


async def test_an_images_recipe(api, settings):
    seed(settings)
    d = (await api.get("/api/vault/gallery/work-moon/m-2")).json()
    assert (d["positive"], d["negative"], d["params"]["model"]) == ("silver hair, луна", "bad hands, lowres",
                                                                   "nai-diffusion-3")
    assert (d["tags"], d["block_tags"], [c["name"] for c in d["components"]]) == (["portrait"], ["night"], ["Block"])
    assert (d["work_title"], d["album"]["heading"], d["url"]) == ("Moon studies", "Harvest moon",
                                                                  "/api/vault/works/work-moon/images/m-2")
    assert (await api.get("/api/vault/gallery/work-moon/m-scr")).status_code == 404  # scratch is no gallery image


# ---- the Works list ----
async def test_works_are_found_by_what_they_hold_and_say_where(api, settings):
    seed(settings)
    found = (await api.get("/api/vault/works", params={"search": "harvest"})).json()["items"]
    assert [(w["id"], w["match"]) for w in found] == [
        ("work-moon", {"kind": "heading", "text": "Harvest moon", "count": 0})]
    found = (await api.get("/api/vault/works", params={"search": "night"})).json()["items"]
    assert {w["id"]: w["match"] for w in found} == {
        "work-rain": {"kind": "images", "text": None, "count": 1},  # "rain, city at night"
        "work-moon": {"kind": "section", "text": "Silver night", "count": 0}}
    found = (await api.get("/api/vault/works", params={"search": "moon studies"})).json()["items"]
    assert [(w["id"], w["match"]) for w in found] == [("work-moon", None)]  # its title: nothing to explain


async def test_works_filter_by_tags_their_gallery_carries(api, settings):
    seed(settings)
    listed = lambda res: [w["id"] for w in res.json()["items"]]  # noqa: E731
    assert listed(await api.get("/api/vault/works", params={"tags": ["night"]})) == ["work-rain", "work-moon"]
    # between its images: m-2 has portrait, m-1 has moon
    assert listed(await api.get("/api/vault/works", params={"tags": ["portrait", "moon"]})) == ["work-moon"]
    assert listed(await api.get("/api/vault/works", params={"tags": ["scratchy"]})) == []  # scratch carries none
    tags = (await api.get("/api/vault/works/tags")).json()
    assert {t["name"]: t["count"] for t in tags} == {"night": 2, "moon": 1, "portrait": 1, "city": 1}
    assert tags[0] == {"name": "night", "count": 2}
    narrowed = (await api.get("/api/vault/works/tags", params={"search": "rain", "tags": ["moon"]})).json()
    assert narrowed == [{"name": "moon", "count": 0}]


async def test_works_tags_are_taken_as_the_index_keeps_them(api, settings):
    seed(settings)
    res = await api.get("/api/vault/works", params={"tags": ["night", "night ", " "]})
    assert [w["id"] for w in res.json()["items"]] == ["work-rain", "work-moon"]
    assert (await api.get("/api/vault/works/tags", params={"tags": ["moon", "moon"]})).json() == [
        {"name": "moon", "count": 1}, {"name": "night", "count": 1}, {"name": "portrait", "count": 1}]


# ---- the index ----
def test_a_hand_edited_gallery_never_drops_its_work_from_the_index(settings):
    work = WorkDoc(id="work-odd", title="Odd", images=[image("o-1"), image("o-2")],
                   canvas=zone({"type": ["junk"]}, {"type": "grid", "imageIds": [{"x": 1}, "o-1"]},
                               {"id": "g", "type": "grid", "imageIds": "o-2"}))
    service.save_work(settings, work)
    placed = gallery_layout.placements(service.load_work(settings, "work-odd"))
    assert {i: (p.grid_id, p.number) for i, p in placed.items()} == {"o-1": ("grid-1", 1)}
    assert service.list_works(settings, 1, 10).total == 1


async def test_an_older_index_is_rebuilt_with_the_new_columns(api, settings):
    seed(settings)
    db = index.location(appconfig.cache_dir(settings), appconfig.active_vault(settings))
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    assert await ids(api, unsorted="only", q="rain") == ["r-1"]
