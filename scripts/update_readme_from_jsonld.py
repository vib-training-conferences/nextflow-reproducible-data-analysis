#!/usr/bin/env python3
"""Merge course JSON-LD metadata into a LiaScript README lesson overview.

This script is intentionally conservative:
- It fetches JSON-LD from the courseURL in the README, or from --url.
- It updates only known metadata blocks inside the existing LiaScript
  "Lesson overview" section.
- It preserves unknown/custom LiaScript blocks, including fa/icon classes,
  schedules, acknowledgements, funding, PURL, chapter lists and all course body
  content exactly as much as possible.
- It updates or inserts the final LiaScript @JSONLD block by merging existing
  JSON-LD and fetched JSON-LD metadata.

Typical use:
  python scripts/update_readme_from_jsonld.py --readme README.md
  python scripts/update_readme_from_jsonld.py --url https://training.vib.be/... --readme README.md
  python scripts/update_readme_from_jsonld.py --readme README.md --dry-run
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

COURSE_TYPES = {"course", "learningresource", "trainingmaterial", "event", "educationevent", "courseinstance"}
INSTANCE_TYPES = {"courseinstance", "event", "educationevent"}
KNOWN_FIELDS = [
    "License",
    "Target Audience",
    "Level",
    "Prerequisites",
    "Description",
    "Learning Outcomes",
    "Time estimation",
    "Funding",
    "Course Materials"
]


@dataclass
class Person:
    """Normalized person metadata extracted from JSON-LD."""

    name: str
    url: str = ""
    orcid: str = ""


@dataclass
class CourseMetadata:
    """Internal representation of course fields used to update the README."""

    source_url: str
    name: str = ""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    audience: list[str] = field(default_factory=list)
    educational_level: str = ""
    prerequisites: list[str] = field(default_factory=list)
    teaches: list[str] = field(default_factory=list)
    license_url: str = ""
    language: str = ""
    duration: str = ""
    materials_url: str = ""
    funders: list[str] = field(default_factory=list)
    authors: list[Person] = field(default_factory=list)
    contributors: list[Person] = field(default_factory=list)


class JsonLdHTMLParser(HTMLParser):
    """Collect raw JSON-LD payloads from script tags in an HTML document."""

    def __init__(self) -> None:
        """Initialize parser state for the current JSON-LD block and all matches."""

        super().__init__(convert_charrefs=True)
        self.in_jsonld = False
        self.current: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Start collecting data when encountering a JSON-LD script tag."""

        if tag.lower() != "script":
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        script_type = attr.get("type", "").lower().split(";", 1)[0].strip()
        if script_type == "application/ld+json":
            self.in_jsonld = True
            self.current = []

    def handle_data(self, data: str) -> None:
        """Append script contents while inside a JSON-LD block."""

        if self.in_jsonld:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        """Finalize and store the current JSON-LD block at the closing script tag."""

        if tag.lower() == "script" and self.in_jsonld:
            block = "".join(self.current).strip()
            if block:
                self.blocks.append(block)
            self.in_jsonld = False
            self.current = []


def fetch_html(url: str, timeout: int = 30) -> str:
    """Download the remote course page and decode it to text."""

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 liascript-jsonld-readme-merger/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def jsonld_blocks_from_html(text: str) -> list[str]:
    """Extract raw JSON-LD script bodies from an HTML document."""

    parser = JsonLdHTMLParser()
    parser.feed(text)
    if parser.blocks:
        return parser.blocks
    # Fallback for malformed HTML or unusual attribute order.
    script_re = re.compile(
        r"<script\b(?=[^>]*\btype\s*=\s*(['\"])application/ld\+json(?:;[^'\"]*)?\1)[^>]*>(.*?)</script>",
        re.I | re.S,
    )
    return [m.group(2).strip() for m in script_re.finditer(text) if m.group(2).strip()]


def parse_jsonld_blocks(blocks: Iterable[str]) -> list[Any]:
    """Parse extracted JSON-LD strings into Python objects, skipping invalid blocks."""

    out: list[Any] = []
    for block in blocks:
        block = html.unescape(block.strip())
        block = re.sub(r"^\s*<!--", "", block)
        block = re.sub(r"-->\s*$", "", block)
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        out.extend(parsed if isinstance(parsed, list) else [parsed])
    return out


def parse_embedded_lia_jsonld(readme: str) -> dict[str, Any]:
    """Read the existing LiaScript @JSONLD block from the README if present."""

    # Supports the common LiaScript style:
    # ```json
    # @JSONLD { ... }
    # ```
    match = re.search(r"(?ms)^```json\s*\n@JSONLD\s*(\{.*?\})\s*\n```", readme)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def as_list(value: Any) -> list[Any]:
    """Normalize a scalar or null JSON-LD value into a list."""

    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def clean_text(value: Any) -> str:
    """Strip HTML markup and collapse whitespace in a free-text value."""

    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def unique(items: Iterable[str]) -> list[str]:
    """Return cleaned strings in first-seen order without duplicates."""

    out: list[str] = []
    for item in items:
        item = clean_text(item)
        if item and item not in out:
            out.append(item)
    return out


def text_list(value: Any) -> list[str]:
    """Convert mixed JSON-LD values into a normalized list of display strings."""

    values: list[str] = []
    for item in as_list(value):
        if isinstance(item, dict):
            item = item.get("name") or item.get("text") or item.get("@id") or item.get("url") or ""
        values.append(str(item))
    return unique(values)


def first_text(*values: Any) -> str:
    """Return the first non-empty text extracted from the given candidate values."""

    for value in values:
        items = text_list(value)
        if items:
            return items[0]
    return ""


def flatten_jsonld(node: Any) -> Iterable[dict[str, Any]]:
    """Yield JSON-LD objects recursively from common nested graph structures."""

    if isinstance(node, list):
        for item in node:
            yield from flatten_jsonld(item)
    elif isinstance(node, dict):
        yield node
        for key in ["@graph", "hasCourseInstance", "courseInstance", "subEvent", "event"]:
            if key in node:
                yield from flatten_jsonld(node[key])


def type_names(item: dict[str, Any]) -> set[str]:
    """Return the lower-cased set of JSON-LD type names for an object."""

    return {str(t).lower() for t in as_list(item.get("@type"))}


def score_jsonld(item: dict[str, Any], instance: bool = False) -> int:
    """Score a JSON-LD object by type match and presence of useful course fields."""

    wanted = INSTANCE_TYPES if instance else COURSE_TYPES
    score = 10 * len(type_names(item) & wanted)
    for key in ["name", "description", "teaches", "learningOutcome", "audience", "educationalLevel", "competencyRequired", "license", "author", "contributor"]:
        if item.get(key):
            score += 1
    return score


def select_course_and_instance(objects: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Choose the best course object and optional instance/event object from JSON-LD."""

    flat = [x for obj in objects for x in flatten_jsonld(obj) if isinstance(x, dict)]
    courses = [x for x in flat if type_names(x) & COURSE_TYPES]
    if not courses:
        # Some pages only publish a generic CreativeWork with useful fields.
        courses = [x for x in flat if x.get("name") or x.get("description")]
    if not courses:
        raise ValueError("No course-like JSON-LD object found.")
    descriptive = [x for x in courses if not (type_names(x) <= INSTANCE_TYPES)] or courses
    course = max(descriptive, key=lambda x: score_jsonld(x, False))
    instances = [x for x in flat if type_names(x) & INSTANCE_TYPES]
    instance = max(instances, key=lambda x: score_jsonld(x, True)) if instances else {}
    return course, instance


def person_list(value: Any) -> list[Person]:
    """Normalize author or contributor values into deduplicated Person entries."""

    people: list[Person] = []
    for item in as_list(value):
        if isinstance(item, dict):
            name = clean_text(item.get("name", ""))
            url = str(item.get("url") or item.get("@id") or "")
            orcid = ""
            for same in as_list(item.get("sameAs")):
                if isinstance(same, str) and "orcid.org" in same:
                    orcid = same
                    break
            if not orcid and "orcid.org" in url:
                orcid = url
        else:
            name, url, orcid = clean_text(item), "", ""
        if name and all(p.name != name for p in people):
            people.append(Person(name=name, url=url, orcid=orcid))
    return people


def keywords(value: Any) -> list[str]:
    """Normalize keyword values from either a string or list representation."""

    if isinstance(value, str):
        return unique(re.split(r"[,;]", value))
    return text_list(value)


def audience_values(*values: Any) -> list[str]:
    """Extract audience labels from plain or structured JSON-LD values."""

    out: list[str] = []
    for value in values:
        for item in as_list(value):
            if isinstance(item, dict):
                out.extend(text_list(item.get("audienceType") or item.get("name") or item.get("@type")))
            else:
                out.extend(text_list(item))
    return unique(out)


def metadata_from_jsonld(objects: list[Any], source_url: str) -> CourseMetadata:
    """Map selected JSON-LD objects into the script's normalized CourseMetadata."""

    course, instance = select_course_and_instance(objects)
    prereq = (
        course.get("competencyRequired")
        or course.get("coursePrerequisites")
        or course.get("educationalPrerequisites")
        or instance.get("competencyRequired")
    )
    material = ""
    for value in [course.get("workFeatured"), course.get("hasPart"), course.get("associatedMedia"), course.get("url"), course.get("@id")]:
        for item in as_list(value):
            if isinstance(item, dict) and (item.get("url") or item.get("@id")):
                material = str(item.get("url") or item.get("@id"))
                break
            if isinstance(item, str) and item.startswith("http"):
                material = item
                break
        if material:
            break
    return CourseMetadata(
        source_url=source_url,
        name=first_text(course.get("name"), instance.get("name")),
        description=first_text(course.get("description"), instance.get("description")),
        keywords=keywords(course.get("keywords") or instance.get("keywords")),
        audience=audience_values(course.get("audience"), instance.get("audience")),
        educational_level=first_text(course.get("educationalLevel"), instance.get("educationalLevel")),
        prerequisites=text_list(prereq),
        teaches=text_list(course.get("teaches") or course.get("learningOutcome") or instance.get("teaches") or instance.get("learningOutcome")),
        license_url=first_text(course.get("license"), instance.get("license")),
        language=first_text(course.get("inLanguage"), instance.get("inLanguage")),
        duration=first_text(course.get("duration"), course.get("timeRequired"), instance.get("duration")),
        materials_url=material,
        funders=text_list(course.get("funder") or instance.get("funder") or course.get("sponsor") or instance.get("sponsor")),
        authors=person_list(course.get("author") or course.get("creator")),
        contributors=person_list(course.get("contributor")),
    )


def extract_course_url(readme: str) -> str:
    """Find the source course URL from README metadata or the first matching link."""

    # Prefer frontmatter/comment key courseURL used by the reference README.
    match = re.search(r"courseURL:\s*(https?://\S+)", readme)
    if match:
        return match.group(1).strip().rstrip("->")
    urls = re.findall(r"https?://[^\s)>'\"]+", readme)
    return next((u for u in urls if "training.vib.be/all-trainings" in u), urls[0] if urls else "")


def locate_lesson_overview(readme: str) -> tuple[int, int, str]:
    """Locate the Lesson overview section boundaries within the README text."""

    start_match = re.search(r"(?m)^Lesson overview\s*\n[-=]+\s*$", readme)
    if not start_match:
        raise ValueError("Could not find a 'Lesson overview' section in the README.")
    start = start_match.start()
    # In the reference README, the Lesson overview ends before the first real course body H1.
    end_match = re.search(r"(?m)^# Workshop and Material organization\s*$", readme[start_match.end() :])
    if end_match:
        end = start_match.end() + end_match.start()
    else:
        # Fallback: next top-level heading after the overview heading.
        next_h1 = re.search(r"(?m)^#\s+", readme[start_match.end() :])
        end = start_match.end() + next_h1.start() if next_h1 else len(readme)
    return start, end, readme[start:end]


def field_name_from_line(line: str) -> str | None:
    """Recognize a known field label from one quoted README line."""

    cleaned = line.strip()
    cleaned = cleaned[1:].strip() if cleaned.startswith(">") else cleaned
    cleaned = re.sub(r"^(?:<[^>]+>\s*)+", "", cleaned)
    match = re.match(r"\*\*([^*]+?)\*\*\s*:?:?", cleaned)
    if not match:
        return None
    name = match.group(1).strip().rstrip(":")
    # Normalize common variants.
    if name.lower() == "time estimation":
        return "Time estimation"


def split_overview_blocks(section: str) -> tuple[list[str], list[tuple[str | None, list[str]]]]:
    """Split the Lesson overview into its heading and field/content blocks."""

    lines = section.splitlines()
    # Keep the heading lines untouched.
    heading: list[str] = []
    idx = 0
    while idx < len(lines):
        heading.append(lines[idx])
        if re.match(r"^[-=]+\s*$", lines[idx]):
            idx += 1
            break
        idx += 1

    blocks: list[tuple[str | None, list[str]]] = []
    current_name: str | None = None
    current: list[str] = []
    for line in lines[idx:]:
        name = field_name_from_line(line)
        is_known_start = name in KNOWN_FIELDS
        # Do not treat headings such as > ## Proposed Schedule as fields.
        if is_known_start and current:
            blocks.append((current_name, current))
            current_name = name
            current = [line]
        elif is_known_start:
            current_name = name
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append((current_name, current))
    return heading, blocks


def quote_line(text: str = "") -> str:
    """Render a line in the quoted format used by the Lesson overview."""

    return ">" if text == "" else f"> {text}"


def render_list(items: list[str]) -> list[str]:
    """Render numbered list items using quoted README lines."""

    return [quote_line(f"{i}. {item}") for i, item in enumerate(items, 1)]


def decorate_field_line(text: str, existing_block: list[str]) -> str:
    """Preserve any decorative prefix that appears before a known field label."""

    if not existing_block:
        return quote_line(text)

    first_line = existing_block[0].strip()
    first_line = first_line[1:].strip() if first_line.startswith(">") else first_line
    match = re.match(r"(.*?)(\*\*[^*]+?\*\*\s*:?:?)", first_line)
    if not match:
        return quote_line(text)

    prefix = match.group(1).rstrip()
    if prefix:
        return quote_line(f"{prefix} {text}")
    return quote_line(text)


def render_field(name: str, meta: CourseMetadata, existing_block: list[str] | None = None) -> list[str]:
    """Render one known overview field from metadata, or keep the existing block."""

    # If JSON-LD has no value for this field, preserve the existing block exactly.
    existing_block = existing_block or []
    if name == "License":
        if not meta.license_url:
            return existing_block
        label = "Creative Commons Attribution 4.0 International License"
        return [decorate_field_line(f"**License:** [{label}]({meta.license_url})", existing_block), quote_line()]
    if name == "Target Audience":
        if not meta.audience:
            return existing_block
        return [decorate_field_line(f"**Target Audience:** {', '.join(meta.audience)}", existing_block), quote_line()]
    if name == "Level":
        if not meta.educational_level:
            return existing_block
        return [decorate_field_line(f"**Level:** {meta.educational_level}", existing_block), quote_line()]
    if name == "Prerequisites":
        if not meta.prerequisites:
            return existing_block
        return [
            decorate_field_line("**Prerequisites**", existing_block),
            quote_line("To be able to follow this course, learners should have knowledge in:"),
            quote_line(),
            *render_list(meta.prerequisites),
            quote_line(),
        ]
    if name == "Description":
        if not meta.description:
            return existing_block
        lines = [decorate_field_line("**Description**", existing_block)]
        for paragraph in re.split(r"\n\s*\n|\n", meta.description):
            paragraph = paragraph.strip()
            if paragraph:
                lines.append(quote_line(paragraph))
                lines.append(quote_line())
        return lines
    if name == "Learning Outcomes":
        if not meta.teaches:
            return existing_block
        return [
            decorate_field_line("**Learning Outcomes:**", existing_block),
            quote_line("By the end of the course, learners will be able to:"),
            quote_line(),
            *render_list(meta.teaches),
            quote_line(),
        ]
    if name == "Time estimation":
        if not meta.duration:
            return existing_block
        return [decorate_field_line(f"**Time estimation**: {meta.duration}", existing_block), quote_line()]
    if name == "Funding":
        if not meta.funders:
            return existing_block
        return [decorate_field_line(f"**Funding:** {', '.join(meta.funders)}", existing_block), quote_line()]
    if name in {"Course Materials"}:
        if not meta.materials_url:
            return existing_block
        return [
            decorate_field_line("**Course Materials**:", existing_block),
            quote_line(),
            quote_line(f"1. [Course materials]({meta.materials_url})"),
            quote_line(),
        ]
    return existing_block


def merge_lesson_overview(readme: str, meta: CourseMetadata) -> str:
    """Rewrite only known Lesson overview fields while preserving other content."""

    start, end, section = locate_lesson_overview(readme)
    heading, blocks = split_overview_blocks(section)

    seen: set[str] = set()
    merged_lines: list[str] = heading[:]
    for name, block in blocks:
        if name in KNOWN_FIELDS:
            merged_lines.extend(render_field(name, meta, block))
            seen.add(name)
        else:
            merged_lines.extend(block)

    # Insert missing JSON-LD-backed fields before the first preserved subsection such as Proposed Schedule.
    missing_blocks: list[str] = []
    for name in KNOWN_FIELDS:
        if name not in seen:
            rendered = render_field(name, meta, [])
            if rendered:
                missing_blocks.extend(rendered)
    if missing_blocks:
        # Place missing metadata immediately after the heading if no known fields were present,
        # otherwise after the last rendered known field by appending before the rest is acceptable.
        merged_lines = heading + missing_blocks + merged_lines[len(heading):]

    new_section = "\n".join(merged_lines).rstrip() + "\n\n"
    return readme[:start] + new_section + readme[end:]


def person_to_jsonld(person: Person) -> dict[str, str]:
    """Convert a Person record back into a minimal JSON-LD Person object."""

    obj = {"@type": "Person", "name": person.name}
    if person.orcid:
        obj["@id"] = person.orcid
    elif person.url:
        obj["url"] = person.url
    return obj


def merge_jsonld_objects(existing: dict[str, Any], meta: CourseMetadata) -> dict[str, Any]:
    """Merge fetched metadata into the existing embedded JSON-LD object."""

    result = dict(existing) if existing else {}
    defaults = {
        "@context": "https://schema.org/",
        "@type": "LearningResource",
        "http://purl.org/dc/terms/conformsTo": {
            "@type": "CreativeWork",
            "@id": "https://bioschemas.org/profiles/TrainingMaterial/1.0-RELEASE",
        },
    }
    for key, value in defaults.items():
        result.setdefault(key, value)

    updates: dict[str, Any] = {
        "@id": meta.materials_url or meta.source_url,
        "description": meta.description,
        "keywords": ", ".join(meta.keywords),
        "name": meta.name,
        "license": meta.license_url,
        "educationalLevel": meta.educational_level,
        "competencyRequired": meta.prerequisites,
        "teaches": meta.teaches,
        "audience": ", ".join(meta.audience),
        "funder": meta.funders,
        "inLanguage": meta.language,
        "learningResourceType": result.get("learningResourceType") or ["tutorial"],
        "author": [person_to_jsonld(p) for p in meta.authors] or result.get("author"),
        "contributor": [person_to_jsonld(p) for p in meta.contributors] or result.get("contributor"),
    }
    for key, value in updates.items():
        if value not in ("", [], None):
            result[key] = value
    return {k: v for k, v in result.items() if v not in ("", [], None)}


def replace_or_append_lia_jsonld(readme: str, new_obj: dict[str, Any]) -> str:
    """Replace the existing LiaScript JSON-LD block or append a new one."""

    block = "```json\n@JSONLD " + json.dumps(new_obj, ensure_ascii=False, indent=2) + "\n```"
    pattern = re.compile(r"(?ms)^```json\s*\n@JSONLD\s*\{.*?\}\s*\n```\s*$")
    if pattern.search(readme):
        return pattern.sub(block, readme, count=1)
    return readme.rstrip() + "\n\n" + block + "\n"


def update_title(readme: str, title: str) -> str:
    """Update the first top-level README heading when a title is available."""

    if not title:
        return readme
    return re.sub(r"(?m)^#\s+.+$", f"# {title}", readme, count=1)


def merge_missing_from_existing(meta: CourseMetadata, existing_jsonld: dict[str, Any]) -> CourseMetadata:
    """Backfill missing fetched metadata with values already stored in the README."""

    # Preserve valid existing README JSON-LD values where the remote object has no value.
    if not meta.name:
        meta.name = first_text(existing_jsonld.get("name"))
    if not meta.description:
        meta.description = first_text(existing_jsonld.get("description"))
    if not meta.keywords:
        meta.keywords = keywords(existing_jsonld.get("keywords"))
    if not meta.audience:
        meta.audience = audience_values(existing_jsonld.get("audience"))
    if not meta.educational_level:
        meta.educational_level = first_text(existing_jsonld.get("educationalLevel"))
    if not meta.prerequisites:
        prereq = existing_jsonld.get("competencyRequired")
        meta.prerequisites = text_list(prereq)
    if not meta.teaches:
        meta.teaches = text_list(existing_jsonld.get("teaches"))
    if not meta.license_url:
        meta.license_url = first_text(existing_jsonld.get("license"))
    if not meta.language:
        meta.language = first_text(existing_jsonld.get("inLanguage"))
    if not meta.funders:
        meta.funders = text_list(existing_jsonld.get("funder") or existing_jsonld.get("sponsor"))
    if not meta.authors:
        meta.authors = person_list(existing_jsonld.get("author"))
    if not meta.contributors:
        meta.contributors = person_list(existing_jsonld.get("contributor"))
    return meta


def update_readme(readme: str, meta: CourseMetadata, update_jsonld: bool = True) -> str:
    """Apply all README updates: title, lesson overview, and optional JSON-LD block."""

    existing_jsonld = parse_embedded_lia_jsonld(readme)

    # Keep lesson-overview updates tied to values extracted from the remote JSON-LD.
    # This prevents overwriting existing Markdown fields when the remote source has no match.
    meta_for_overview = meta
    updated = update_title(readme, meta.name)
    updated = merge_lesson_overview(updated, meta_for_overview)

    if update_jsonld:
        # Backfill only for the embedded @JSONLD output block.
        meta_for_jsonld = merge_missing_from_existing(copy.deepcopy(meta), existing_jsonld)
        merged_jsonld = merge_jsonld_objects(existing_jsonld, meta_for_jsonld)
        updated = replace_or_append_lia_jsonld(updated, merged_jsonld)
    return updated


def main() -> int:
    """Parse CLI arguments, fetch metadata, update the README, and write the result."""

    parser = argparse.ArgumentParser(description="Merge course URL JSON-LD metadata into a LiaScript README lesson overview.")
    parser.add_argument("--readme", default="README.md", help="README file to update")
    parser.add_argument("--url", help="Course URL. If omitted, courseURL is read from README.")
    parser.add_argument("--output", help="Optional output file. Defaults to overwriting --readme.")
    parser.add_argument("--dry-run", action="store_true", help="Print updated README instead of writing it")
    parser.add_argument("--debug-html", help="Optional path to save fetched HTML for debugging")
    parser.add_argument("--no-jsonld-update", action="store_true", help="Do not update the embedded LiaScript @JSONLD block")
    args = parser.parse_args()

    readme_path = Path(args.readme)
    readme = readme_path.read_text(encoding="utf-8")
    course_url = args.url or extract_course_url(readme)
    if not course_url:
        raise ValueError("No course URL provided and no courseURL found in README.")

    html_text = fetch_html(course_url)
    if args.debug_html:
        Path(args.debug_html).write_text(html_text, encoding="utf-8")

    objects = parse_jsonld_blocks(jsonld_blocks_from_html(html_text))
    if not objects:
        raise ValueError("No parseable JSON-LD found at the courseURL. Use --debug-html to inspect the fetched HTML.")

    meta = metadata_from_jsonld(objects, course_url)
    updated = update_readme(readme, meta, update_jsonld=not args.no_jsonld_update)

    if args.dry_run:
        print(updated)
    else:
        output = Path(args.output) if args.output else readme_path
        output.write_text(updated, encoding="utf-8")
        print(f"Updated {output} by merging Lesson overview with JSON-LD from {course_url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
