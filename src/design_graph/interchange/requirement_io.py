# SPDX-License-Identifier: Apache-2.0
"""Load typed requirement cards from JSON, YAML, or ReqIF 1.x documents."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

import yaml

from design_graph.model.cards import RequirementCard, RequirementStatus

_TAG_PREFIX = re.compile(r"\{[^}]+\}")


def load_requirement_cards(path: str | Path) -> list[RequirementCard]:
    """Load cards from a JSON/YAML list or importable ReqIF XML document."""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".reqif", ".xml"}:
        return load_reqif(source)
    text = source.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        raise ValueError(f"unsupported requirement file extension: {suffix}")
    records = data.get("requirements", data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError("requirement file must contain a list or a 'requirements' list")
    return [RequirementCard.model_validate(record) for record in records]


def load_reqif(path: str | Path) -> list[RequirementCard]:
    """Import ReqIF SPEC-OBJECTS with named string, XHTML, numeric, and enum attributes.

    The supported ReqIF mapping uses attribute names `kind`, `params`, `quote`, `method`, `tbc`,
    and `status`. `params` contains JSON. The SPEC-OBJECT IDENTIFIER becomes the card ID.
    """

    source = Path(path)
    if source.stat().st_size > 10_000_000:
        raise ValueError("ReqIF document exceeds the 10 MB import limit")
    xml_text = source.read_text(encoding="utf-8")
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise ValueError("ReqIF documents with DTD or entity declarations are not accepted")
    root = ET.fromstring(xml_text)
    attribute_names = {
        element.attrib["IDENTIFIER"]: element.attrib.get("LONG-NAME", "")
        for element in root.iter()
        if _tag(element)
        in {
            "ATTRIBUTE-DEFINITION-STRING",
            "ATTRIBUTE-DEFINITION-XHTML",
            "ATTRIBUTE-DEFINITION-INTEGER",
            "ATTRIBUTE-DEFINITION-REAL",
            "ATTRIBUTE-DEFINITION-BOOLEAN",
            "ATTRIBUTE-DEFINITION-ENUMERATION",
        }
        and "IDENTIFIER" in element.attrib
    }
    enum_names = {
        element.attrib["IDENTIFIER"]: element.attrib.get("LONG-NAME", "")
        for element in root.iter()
        if _tag(element) == "ENUM-VALUE" and "IDENTIFIER" in element.attrib
    }
    cards: list[RequirementCard] = []
    for spec_object in root.iter():
        if _tag(spec_object) != "SPEC-OBJECT":
            continue
        identifier = spec_object.attrib.get("IDENTIFIER", "")
        attributes: dict[str, Any] = {}
        for value_element in spec_object.iter():
            tag = _tag(value_element)
            if not tag.startswith("ATTRIBUTE-VALUE-"):
                continue
            definition_ref = next(
                (
                    ref.text.strip()
                    for ref in value_element.iter()
                    if _tag(ref).endswith("-REF") and ref.text and ref.text.strip()
                ),
                None,
            )
            if definition_ref is None:
                continue
            name = attribute_names.get(definition_ref, "").strip().casefold()
            if not name:
                continue
            if tag == "ATTRIBUTE-VALUE-XHTML":
                content = next(
                    (
                        child
                        for child in value_element.iter()
                        if _tag(child) in {"XHTML-CONTENT", "THE-VALUE"}
                    ),
                    None,
                )
                attribute_value: Any = (
                    " ".join("".join(content.itertext()).split()) if content is not None else ""
                )
            elif tag == "ATTRIBUTE-VALUE-ENUMERATION":
                references = [
                    ref.text.strip()
                    for ref in value_element.iter()
                    if _tag(ref) == "ENUM-VALUE-REF" and ref.text
                ]
                resolved = [enum_names.get(reference, reference) for reference in references]
                attribute_value = resolved[0] if len(resolved) == 1 else resolved
            elif tag in {"ATTRIBUTE-VALUE-INTEGER", "ATTRIBUTE-VALUE-REAL"}:
                raw_value = value_element.attrib.get("THE-VALUE", "")
                attribute_value = (
                    float(raw_value) if tag == "ATTRIBUTE-VALUE-REAL" else int(raw_value)
                )
            elif tag == "ATTRIBUTE-VALUE-BOOLEAN":
                attribute_value = (
                    value_element.attrib.get("THE-VALUE", "false").casefold() == "true"
                )
            else:
                attribute_value = value_element.attrib.get("THE-VALUE", "")
            attributes[name] = attribute_value
        if not attributes:
            continue
        try:
            kind = attributes["kind"]
            params_value = attributes["params"]
            params = json.loads(params_value) if isinstance(params_value, str) else params_value
            quote = attributes["quote"]
            method = attributes.get("method", "T")
            tbc = _as_bool(attributes.get("tbc", False))
            status = cast(RequirementStatus, str(attributes.get("status", "active")).casefold())
            cards.append(
                RequirementCard(
                    id=identifier,
                    kind=kind,
                    params=params,
                    quote=quote,
                    method=method,
                    tbc=tbc,
                    status=status,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"cannot map ReqIF SPEC-OBJECT {identifier!r} to a requirement card: {error}"
            ) from error
    return cards


def _tag(element: ET.Element) -> str:
    return _TAG_PREFIX.sub("", element.tag)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes"}
