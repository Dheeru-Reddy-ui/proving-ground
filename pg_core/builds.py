"""Build registration and the APK parts manifest (ADR-0010).

Supabase Storage's Free plan accepts files up to 50 MB, and our APK is larger, so an APK is
stored as parts of at most `PART_MAX_BYTES`. The manifest lists each part's key, size and
sha256 plus the whole file's sha256; the agent joins the parts and installs only when the whole
file hashes to the build's sha256.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

PART_MAX_BYTES = 45 * 1024 * 1024  # 47.2 MB, under the 50 MB Free-plan file limit
MAX_PARTS = 20

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
LocatorTag = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{12}$")]


def part_key(apk_sha256: str, index: int) -> str:
    return f"apks/{apk_sha256}/part-{index:03d}"


def split_sizes(size: int, part_max: int = PART_MAX_BYTES) -> list[int]:
    """Sizes of the parts a file of `size` bytes is cut into, in order."""
    if size < 1:
        raise ValueError("an APK cannot be empty")
    full, rest = divmod(size, part_max)
    return [part_max] * full + ([rest] if rest else [])


class ApkPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0, lt=MAX_PARTS)
    key: str
    sha256: Sha256
    size: int = Field(ge=1, le=PART_MAX_BYTES)


class ApkManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: Sha256
    size: int = Field(ge=1)
    parts: tuple[ApkPart, ...] = Field(min_length=1, max_length=MAX_PARTS)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if [p.index for p in self.parts] != list(range(len(self.parts))):
            raise ValueError("parts must be numbered 0, 1, 2, ... in order")
        if sum(p.size for p in self.parts) != self.size:
            raise ValueError("part sizes do not add up to the APK size")
        for part in self.parts:
            if part.key != part_key(self.sha256, part.index):
                raise ValueError(f"part {part.index} has key {part.key!r}, expected the APK's")
        return self


class BuildRegistration(BaseModel):
    """What the CLI and the release webhook send to register a build (`build.json`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha256: Sha256
    label: str = Field(min_length=1, max_length=200)
    locator_tag: LocatorTag
    patch_notes: str = Field(default="", max_length=5000)
    apk: ApkManifest | None = None  # where the agent downloads it from; None = not uploaded

    @model_validator(mode="after")
    def _same_apk(self) -> Self:
        if self.apk is not None and self.apk.sha256 != self.sha256:
            raise ValueError("the APK manifest is for a different sha256")
        return self
