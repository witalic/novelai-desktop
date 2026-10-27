"""Saving images to the user's downloads folder (Settings → General) — straight, no OS dialog. A name is never taken
twice: two saves in one second, or a file already there, get the next free one."""
import itertools
import logging
import os
import shutil
import time
from pathlib import Path

from app import appconfig
from app.settings import Settings

log = logging.getLogger(__name__)


def _claim(dest: Path, stem: str) -> Path:
    """A new, empty ``<stem>.png`` (``<stem>-2.png``, … if taken) — created exclusively, so no other save gets it too."""
    for n in itertools.count(1):
        path = dest / (f"{stem}.png" if n == 1 else f"{stem}-{n}.png")
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            continue
        return path
    raise AssertionError("unreachable")


def save(settings: Settings, images: list[bytes | Path]) -> dict:
    """Write each image — its bytes, or a copy of a vault file — under a name of its own. One that can't be written
    (gone from the vault meanwhile, a full disk) leaves nothing behind and is counted in ``failed``; the rest go on."""
    dest = Path(appconfig.load(settings)["download_dir"])
    dest.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    failed = 0
    for i, image in enumerate(images, start=1):
        path = _claim(dest, f"novelai-{stamp}-{i}")
        try:
            if isinstance(image, Path):
                shutil.copyfile(image, path)
            else:
                path.write_bytes(image)
        except OSError as exc:
            path.unlink(missing_ok=True)
            failed += 1
            log.warning("Couldn't save image %d to %s: %s", i, dest, exc)
    log.info("Saved %d image(s) to %s", len(images) - failed, dest)
    return {"count": len(images) - failed, "dir": str(dest), "failed": failed}
