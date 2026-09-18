"""Template and slot handling (FR-038, PRD section 21.2).

A template is a *compiling, tested* source file with one or more declared slots:

    // BEGIN GENERATED SLOT: transform
    ...default implementation...
    // END GENERATED SLOT: transform

Generation replaces the body between the markers and nothing else. The renderer then proves
that claim: `diff_outside_slots` compares the rendered file with the template outside every
slot and fails validation if a single character differs, apart from the two substitutions
that are declared and mechanical (the package line and the object name, so several generated
artifacts can coexist).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from triad_core.config import REPO_ROOT
from triad_core.ids import content_hash, normalise_source

TEMPLATE_ROOT = REPO_ROOT / "scala_spark" / "jobs" / "src" / "main" / "scala" / "triad" / "jobs"
GENERATED_ROOT = TEMPLATE_ROOT / "generated"
GENERATED_PACKAGE = "triad.jobs.generated"

BEGIN = re.compile(r"^(?P<indent>[ \t]*)//\s*BEGIN GENERATED SLOT:\s*(?P<slot>[\w.-]+)\s*$")
END = re.compile(r"^[ \t]*//\s*END GENERATED SLOT:\s*(?P<slot>[\w.-]+)\s*$")
PACKAGE_LINE = re.compile(r"^package\s+[\w.]+\s*$")
OBJECT_LINE = re.compile(r"^object\s+(?P<name>\w+)\s+extends\s+(?P<parent>\w+)\s*\{\s*$")


class TemplateError(Exception):
    pass


class SlotViolation(Exception):
    """Raised when a rendered artifact differs from its template outside a slot."""


@dataclass
class Slot:
    slot_id: str
    start_line: int      # index of the BEGIN marker
    end_line: int        # index of the END marker
    indent: str
    default_body: list[str] = field(default_factory=list)


@dataclass
class Template:
    template_id: str
    version: str
    path: Path
    lines: list[str]
    slots: dict[str, Slot]
    object_name: str
    language: str = "scala"

    @property
    def source(self) -> str:
        return "\n".join(self.lines)

    def slot_ids(self) -> list[str]:
        return list(self.slots)

    def default_body(self, slot_id: str) -> str:
        return "\n".join(self.slots[slot_id].default_body)


def parse_template(path: str | Path, *, version: str = "1.0") -> Template:
    p = Path(path)
    if not p.exists():
        raise TemplateError(f"template not found: {p}")
    text = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = text.split("\n")

    slots: dict[str, Slot] = {}
    open_slot: Slot | None = None
    for i, line in enumerate(lines):
        m = BEGIN.match(line)
        if m:
            if open_slot is not None:
                raise TemplateError(
                    f"{p.name}: slot '{m.group('slot')}' opens while '{open_slot.slot_id}' is open"
                )
            open_slot = Slot(m.group("slot"), i, -1, m.group("indent"))
            continue
        e = END.match(line)
        if e:
            if open_slot is None:
                raise TemplateError(f"{p.name}: END marker for '{e.group('slot')}' with no BEGIN")
            if e.group("slot") != open_slot.slot_id:
                raise TemplateError(
                    f"{p.name}: END '{e.group('slot')}' does not match BEGIN "
                    f"'{open_slot.slot_id}'"
                )
            open_slot.end_line = i
            open_slot.default_body = lines[open_slot.start_line + 1 : i]
            slots[open_slot.slot_id] = open_slot
            open_slot = None
    if open_slot is not None:
        raise TemplateError(f"{p.name}: slot '{open_slot.slot_id}' is never closed")
    if not slots:
        raise TemplateError(f"{p.name}: template declares no slots")

    object_name = ""
    for line in lines:
        m = OBJECT_LINE.match(line)
        if m:
            object_name = m.group("name")
            break
    if not object_name:
        raise TemplateError(f"{p.name}: no `object X extends Y {{` declaration found")

    return Template(
        template_id=p.stem, version=version, path=p, lines=lines, slots=slots,
        object_name=object_name,
    )


def load_templates(root: str | Path = TEMPLATE_ROOT) -> dict[str, Template]:
    """Every template in the jobs package (the generated folder is not a template source)."""
    out: dict[str, Template] = {}
    for p in sorted(Path(root).glob("*.scala")):
        try:
            out[p.stem] = parse_template(p)
        except TemplateError:
            continue  # a job file without slots is simply not a template
    return out


@dataclass
class RenderedArtifact:
    template_id: str
    template_version: str
    slot_id: str
    object_name: str
    source: str
    content_hash: str
    language: str = "scala"
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "template_version": self.template_version,
            "slot_id": self.slot_id,
            "object_name": self.object_name,
            "content_hash": self.content_hash,
            "language": self.language,
            "path": str(self.path) if self.path else None,
        }


def render(
    template: Template, slot_bodies: dict[str, str], *, suffix: str | None = None
) -> RenderedArtifact:
    """Produce a compilable artifact with the given slot bodies substituted.

    The artifact moves to the `triad.jobs.generated` package and gets a unique object name,
    so several generated versions can compile side by side without colliding with the
    template they came from.
    """
    unknown = set(slot_bodies) - set(template.slots)
    if unknown:
        raise SlotViolation(f"unknown slot(s) for {template.template_id}: {sorted(unknown)}")

    out: list[str] = []
    skip_to = -1
    for i, line in enumerate(template.lines):
        if i <= skip_to:
            continue
        m = BEGIN.match(line)
        if m and m.group("slot") in slot_bodies:
            slot = template.slots[m.group("slot")]
            out.append(line)
            body = slot_bodies[slot.slot_id].replace("\r\n", "\n").rstrip("\n")
            out.extend(body.split("\n"))
            out.append(template.lines[slot.end_line])
            skip_to = slot.end_line
            continue
        out.append(line)

    source = "\n".join(out)
    provisional_hash = content_hash(source)
    object_suffix = suffix or provisional_hash[:8]
    new_object = f"{template.object_name}_{object_suffix}"

    source = PACKAGE_LINE.sub(f"package {GENERATED_PACKAGE}", source, count=1)
    if not source.startswith(f"package {GENERATED_PACKAGE}"):
        source = re.sub(
            r"^package\s+[\w.]+", f"package {GENERATED_PACKAGE}", source, count=1, flags=re.M
        )
    source = re.sub(
        rf"^object\s+{re.escape(template.object_name)}\s+extends\s",
        f"object {new_object} extends ",
        source,
        count=1,
        flags=re.M,
    )

    return RenderedArtifact(
        template_id=template.template_id,
        template_version=template.version,
        slot_id=",".join(sorted(slot_bodies)),
        object_name=new_object,
        source=normalise_source(source),
        content_hash=content_hash(source),
    )


def _strip_declared_substitutions(text: str, object_name: str, template_object: str) -> str:
    """Undo the two mechanical substitutions so the rest can be compared byte for byte."""
    text = re.sub(r"^package\s+[\w.]+", "package <PKG>", text, count=1, flags=re.M)
    text = text.replace(f"object {object_name} extends", f"object {template_object} extends")
    return text


def diff_outside_slots(template: Template, artifact: RenderedArtifact) -> list[str]:
    """Return the differing lines outside every slot. Empty means the artifact is compliant.

    This is validation gate 2 for slot compliance (FR-038: "any change outside a slot fails
    validation"), and it is what makes 'the agent may only fill slots' a checked property
    rather than an instruction in a prompt.
    """
    rendered = _strip_declared_substitutions(
        artifact.source, artifact.object_name, template.object_name
    ).split("\n")
    original = _strip_declared_substitutions(
        template.source, template.object_name, template.object_name
    ).split("\n")

    def outside_slot_lines(lines: list[str]) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        inside = False
        idx = 0
        for line in lines:
            if BEGIN.match(line):
                inside = True
                out.append((idx, line.strip()))
                idx += 1
                continue
            if END.match(line):
                inside = False
                out.append((idx, line.strip()))
                idx += 1
                continue
            if not inside:
                out.append((idx, line.rstrip()))
                idx += 1
        return out

    a = [t for _, t in outside_slot_lines(original)]
    b = [t for _, t in outside_slot_lines(rendered)]

    diffs: list[str] = []
    for i, (left, right) in enumerate(zip(a, b, strict=False)):
        if left != right:
            diffs.append(f"line {i + 1}: template {left!r} != generated {right!r}")
    if len(a) != len(b):
        diffs.append(f"line count outside slots changed: template {len(a)} != generated {len(b)}")
    return diffs


def write_artifact(artifact: RenderedArtifact, root: str | Path = GENERATED_ROOT) -> Path:
    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{artifact.object_name}.scala"
    path.write_text(artifact.source, encoding="utf-8")
    artifact.path = path
    return path


def clean_generated(root: str | Path = GENERATED_ROOT, keep: set[str] | None = None) -> int:
    """Remove generated sources that are no longer referenced, so builds stay quick."""
    keep = keep or set()
    removed = 0
    p = Path(root)
    if not p.exists():
        return 0
    for f in p.glob("*.scala"):
        if f.stem not in keep:
            f.unlink()
            removed += 1
    return removed
