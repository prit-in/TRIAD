"""A small writer for draw.io (mxGraph) files, so every diagram is an editable draw.io source.

Geometry is chosen for the template: a page-width figure is 7.16 in, and the canvases here are
880 px wide with 16 px text, which prints at about 9.4 pt - inside the template's 8-10 pt range.
Exported at scale 5 a canvas is 4 400 px wide, which is 614 DPI at page width.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from pathlib import Path

FONT = "Times New Roman"

# fill, stroke - one colour family per role, printable in greyscale by lightness.
KINDS: dict[str, tuple[str, str]] = {
    "data": ("#DAE8FC", "#6C8EBF"),        # storage and datasets
    "compute": ("#D5E8D4", "#82B366"),     # engines and jobs
    "agent": ("#E1D5E7", "#9673A6"),       # agents and orchestration
    "govern": ("#FFE6CC", "#D79B00"),      # governance, security, policy
    "person": ("#FFF2CC", "#D6B656"),      # people and the interface
    "neutral": ("#F5F5F5", "#666666"),
    "white": ("#FFFFFF", "#666666"),
    "alert": ("#F8CECC", "#B85450"),       # failure and refusal paths
}


def _label(text: str) -> str:
    """HTML for a label (html=1), escaped again for the XML attribute that carries it.

    A literal newline in an XML attribute is normalised to a space by every parser, so line
    breaks become <br>."""
    return escape(escape(text).replace("\n", "<br>"))


@dataclass
class Diagram:
    name: str
    width: int = 880
    height: int = 500
    cells: list[str] = field(default_factory=list)
    _ids: set[str] = field(default_factory=set)

    def _id(self, cid: str) -> str:
        if cid in self._ids:
            raise ValueError(f"duplicate id {cid} in {self.name}")
        self._ids.add(cid)
        return cid

    def box(self, cid: str, label: str, x: int, y: int, w: int = 150, h: int = 50, *,
            kind: str = "neutral", dashed: bool = False, bold: bool = False,
            size: int = 16, shape: str = "rounded", align: str = "center") -> str:
        fill, stroke = KINDS[kind]
        shapes = {
            "rounded": "rounded=1;arcSize=12;",
            "rect": "rounded=0;",
            "cylinder": "shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=8;",
            "ellipse": "ellipse;",
            "hexagon": "shape=hexagon;perimeter=hexagonPerimeter2;size=0.1;",
            "doc": "shape=note;size=12;",
            "actor": "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;",
        }
        style = (f"{shapes[shape]}whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
                 f"fontFamily={FONT};fontSize={size};align={align};"
                 f"{'dashed=1;dashPattern=6 4;' if dashed else ''}"
                 f"{'fontStyle=1;' if bold else ''}")
        self.cells.append(
            f'<mxCell id="{self._id(cid)}" value="{_label(label)}" style="{style}" vertex="1" '
            f'parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
            f'</mxCell>')
        return cid

    def group(self, cid: str, label: str, x: int, y: int, w: int, h: int, *,
              kind: str = "neutral", dashed: bool = False, size: int = 16) -> str:
        fill, stroke = KINDS[kind]
        style = (f"rounded=1;arcSize=4;whiteSpace=wrap;html=1;fillColor={fill};"
                 f"strokeColor={stroke};fontFamily={FONT};fontSize={size};fontStyle=1;"
                 f"verticalAlign=top;align=left;spacingLeft=8;spacingTop=2;opacity=60;"
                 f"{'dashed=1;dashPattern=6 4;' if dashed else ''}")
        self.cells.append(
            f'<mxCell id="{self._id(cid)}" value="{_label(label)}" style="{style}" vertex="1" '
            f'parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
            f'</mxCell>')
        return cid

    def text(self, cid: str, label: str, x: int, y: int, w: int, h: int = 24, *,
             size: int = 16, align: str = "center", italic: bool = False, bold: bool = False) -> str:
        style = (f"text;html=1;whiteSpace=wrap;fontFamily={FONT};fontSize={size};align={align};"
                 f"verticalAlign=middle;fontStyle={(2 if italic else 0) + (1 if bold else 0)};")
        self.cells.append(
            f'<mxCell id="{self._id(cid)}" value="{_label(label)}" style="{style}" vertex="1" '
            f'parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
            f'</mxCell>')
        return cid

    def edge(self, src: str, dst: str, label: str = "", *, dashed: bool = False,
             both: bool = False, curved: bool = False, size: int = 14, color: str = "#333333",
             exit: str | None = None, entry: str | None = None) -> str:
        cid = self._id(f"e{len(self.cells)}")
        style = (f"edgeStyle={'none' if curved else 'orthogonalEdgeStyle'};rounded=1;html=1;"
                 f"endArrow=block;endFill=1;strokeColor={color};strokeWidth=1.5;"
                 f"fontFamily={FONT};fontSize={size};labelBackgroundColor=#FFFFFF;"
                 f"{'startArrow=block;startFill=1;' if both else ''}"
                 f"{'dashed=1;dashPattern=6 4;' if dashed else ''}"
                 f"{self._port('exit', exit)}{self._port('entry', entry)}")
        self.cells.append(
            f'<mxCell id="{cid}" value="{_label(label)}" style="{style}" edge="1" parent="1" '
            f'source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>')
        return cid

    @staticmethod
    def _port(kind: str, side: str | None) -> str:
        if side is None:
            return ""
        x, y = {"top": (0.5, 0), "bottom": (0.5, 1), "left": (0, 0.5), "right": (1, 0.5)}[side]
        return f"{kind}X={x};{kind}Y={y};{kind}Dx=0;{kind}Dy=0;"

    def xml(self) -> str:
        body = "".join(self.cells)
        return (
            '<mxfile host="drawio" type="device">'
            f'<diagram name="{escape(self.name)}" id="d1">'
            f'<mxGraphModel dx="{self.width}" dy="{self.height}" grid="1" gridSize="10" guides="1" '
            f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            f'pageWidth="{self.width}" pageHeight="{self.height}" math="0" shadow="0">'
            f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root>'
            '</mxGraphModel></diagram></mxfile>'
        )

    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.name}.drawio"
        path.write_text(self.xml(), encoding="utf-8")
        return path
