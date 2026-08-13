#!/usr/bin/env python3
"""
Update a course README from JSON-LD embedded in the HTML <head> of a course page.

Typical use:
  python scripts/update_readme_from_jsonld.py \
    --url "https://training.vib.be/all-trainings/nextflow-reproducible-and-automated-data-analysis-7" \
    --readme README.md \
    --marker-mode append

Recommended README placeholder:
  <!-- COURSE_METADATA_START -->
  <!-- COURSE_METADATA_END -->

If these markers exist, the script replaces only the generated metadata between them.
If they do not exist, --marker-mode append will append a generated block to the README.
The script uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

START_MARKER = "<!-- COURSE_METADATA_START -->"
END_MARKER = "<!-- COURSE_METADATA_END -->"

COURSE_TYPES = {
    "course",
    "learningresource",
    "trainingmaterial",
    "event",
    "educationevent",
    "courseinstance",
}
INSTANCE_TYPES = {"courseinstance", "event", "educationevent"}


class JsonLdHTMLParser(HTMLParser):
    """Extract JSON-LD script blocks, explicitly tracking whether they occur in <head>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.in_jsonld = False
        self.current: list[str] = []
        self.current_in_head = False
        self.head_blocks: list[str] = []
        self.all_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "head":
            self.in_head = True
            return

        if tag != "script":
            return

        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        script_type = attr_dict.get("type", "").lower().split(";", 1)[0].strip()
        if script_type == "application/ld+json":
            self.in_jsonld = True
            self.current = []
            self.current_in_head = self.in_head

    def handle_data(self, data: str) -> None:
        if self.in_jsonld:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self.in_jsonld:
            block = "".join(self.current).strip()
            if block:
                self.all_blocks.append(block)
                if self.current_in_head:
                    self.head_blocks.append(block)
            self.in_jsonld = False
            self.current = []
            self.current_in_head = False
            return

        if tag == "head":
            self.in_head = False


@dataclass
class CourseMetadata:
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
    start_date: str = ""
    end_date: str = ""
    duration: str = ""
    location: str = ""
    materials_url: str = ""
    authors: list[dict[str, str]] = field(default_factory=list)
    contributors: list[dict[str, str]] = field(default_factory=list)
    trainers: list[dict[str, str]] = field(default_factory=list)


def fetch_html(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "course-readme-jsonld-updater/1.0"})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def jsonld_blocks_from_html(html_text: str, prefer_head: bool = True) -> list[str]:
    parser = JsonLdHTMLParser()
    parser.feed(html_text)
    if prefer_head and parser.head_blocks:
        return parser.head_blocks
    return parser.all_blocks


def parse_jsonld_blocks(blocks: Iterable[str]) -> list[Any]:
    objects: list[Any] = []
    for block in blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            # Some systems HTML-escape the contents. Try once more after unescaping.
            try:
                parsed = json.loads(html.unescape(block))
            except json.JSONDecodeError:
                continue
        if isinstance(parsed, list):
            objects.extend(parsed)
        else:
            objects.append(parsed)
    return objects


def fetch_jsonld_objects(url: str, prefer_head: bool = True) -> list[Any]:
    html_text = fetch_html(url)
    blocks = jsonld_blocks_from_html(html_text, prefer_head=prefer_head)
    return parse_jsonld_blocks(blocks)


def as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def strip_html_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def text_list(value: Any) -> list[str]:
    items: list[str] = []
    for item in as_list(value):
        if isinstance(item, dict):
            label = item.get("name") or item.get("text") or item.get("@id") or item.get("url")
        else:
            label = str(item)
        label = strip_html_tags(str(label)) if label else ""
        if label and label not in items:
            items.append(label)
    return items


def first_text(*values: Any) -> str:
    for value in values:
        values_as_text = text_list(value)
        if values_as_text:
            return values_as_text[0]
    return ""


def type_names(item: dict[str, Any]) -> set[str]:
    return {str(t).lower() for t in as_list(item.get("@type"))}


def flatten_jsonld(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from flatten_jsonld(item)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from flatten_jsonld(node["@graph"])
        # Also expose nested CourseInstance objects for merging later.
        for key in ["hasCourseInstance", "courseInstance", "subEvent", "event"]:
            if key in node:
                yield from flatten_jsonld(node[key])


def score_jsonld(item: dict[str, Any], instance: bool = False) -> int:
    types = type_names(item)
    wanted = INSTANCE_TYPES if instance else COURSE_TYPES
    score = 10 * len(types & wanted)
    for key in [
        "name",
        "description",
        "teaches",
        "learningOutcome",
        "audience",
        "educationalLevel",
        "startDate",
        "endDate",
        "location",
        "author",
        "contributor",
        "instructor",
    ]:
        if item.get(key):
            score += 1
    return score


def select_course_and_instance(objects: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    flat = [item for obj in objects for item in flatten_jsonld(obj) if isinstance(item, dict)]
    if not flat:
        raise ValueError("No JSON-LD objects could be parsed from the HTML page.")

    course_candidates = [item for item in flat if type_names(item) & COURSE_TYPES]
    if not course_candidates:
        raise ValueError("JSON-LD objects were found, but no course-like object could be identified.")

    # Prefer non-instance courses as the main descriptive object.
    descriptive_candidates = [
        item for item in course_candidates if not (type_names(item) <= INSTANCE_TYPES)
    ] or course_candidates
    course = max(descriptive_candidates, key=lambda item: score_jsonld(item, instance=False))

    nested_instances = []
    for key in ["hasCourseInstance", "courseInstance", "subEvent", "event"]:
        nested_instances.extend([x for x in flatten_jsonld(course.get(key)) if isinstance(x, dict)])

    instance_candidates = [item for item in flat + nested_instances if type_names(item) & INSTANCE_TYPES]
    instance = max(instance_candidates, key=lambda item: score_jsonld(item, instance=True)) if instance_candidates else {}
    return course, instance


def person_list(value: Any) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    for item in as_list(value):
        if isinstance(item, dict):
            name = strip_html_tags(str(item.get("name", "")))
            url = str(item.get("url") or item.get("@id") or "")
            orcid = ""
            for entry in as_list(item.get("sameAs")):
                if isinstance(entry, str) and "orcid.org" in entry:
                    orcid = entry
                    break
            if not orcid and "orcid.org" in url:
                orcid = url
            if name:
                person = {"name": name, "url": url, "orcid": orcid}
                if person not in people:
                    people.append(person)
        elif item:
            name = strip_html_tags(str(item))
            person = {"name": name, "url": "", "orcid": ""}
            if name and person not in people:
                people.append(person)
    return people


def extract_keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        return [k.strip() for k in re.split(r"[,;]", value) if k.strip()]
    return text_list(value)


def extract_audience(*values: Any) -> list[str]:
    audience_items: list[str] = []
    for value in values:
        for item in as_list(value):
            if isinstance(item, dict):
                audience_items.extend(text_list(item.get("audienceType") or item.get("name") or item.get("@type")))
            else:
                audience_items.extend(text_list(item))
    return list(dict.fromkeys(audience_items))


def extract_material_url(*values: Any) -> str:
    for value in values:
        for item in as_list(value):
            if isinstance(item, dict):
                url = item.get("url") or item.get("@id")
                if url:
                    return str(url)
            elif isinstance(item, str) and item.startswith("http"):
                return item
    return ""


def extract_location(*values: Any) -> str:
    for value in values:
        if not value:
            continue
        if isinstance(value, dict):
            parts: list[str] = []
            if value.get("name"):
                parts.append(str(value["name"]))
            address = value.get("address")
            if isinstance(address, dict):
                for key in ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]:
                    if address.get(key):
                        parts.append(str(address[key]))
            elif address:
                parts.append(str(address))
            clean = ", ".join(dict.fromkeys(strip_html_tags(p) for p in parts if p))
            if clean:
                return clean
        else:
            text = first_text(value)
            if text:
                return text
    return ""


def normalise_metadata(course: dict[str, Any], instance: dict[str, Any], source_url: str) -> CourseMetadata:
    prereq = (
        course.get("competencyRequired")
        or course.get("coursePrerequisites")
        or course.get("educationalPrerequisites")
        or instance.get("competencyRequired")
        or instance.get("coursePrerequisites")
    )

    return CourseMetadata(
        source_url=source_url,
        name=first_text(course.get("name"), instance.get("name"), course.get("headline")),
        description=first_text(course.get("description"), instance.get("description"), course.get("abstract")),
        keywords=extract_keywords(course.get("keywords") or instance.get("keywords")),
        audience=extract_audience(course.get("audience"), instance.get("audience")),
        educational_level=first_text(course.get("educationalLevel"), instance.get("educationalLevel")),
        prerequisites=text_list(prereq),
        teaches=text_list(course.get("teaches") or course.get("learningOutcome") or instance.get("teaches") or instance.get("learningOutcome")),
        license_url=first_text(course.get("license"), instance.get("license")),
        language=first_text(course.get("inLanguage"), instance.get("inLanguage")),
        start_date=first_text(instance.get("startDate"), course.get("startDate")),
        end_date=first_text(instance.get("endDate"), course.get("endDate")),
        duration=first_text(instance.get("duration"), course.get("duration"), course.get("timeRequired")),
        location=extract_location(instance.get("location"), course.get("location")),
        materials_url=extract_material_url(
            course.get("workFeatured"),
            course.get("hasPart"),
            course.get("associatedMedia"),
            course.get("url"),
        ),
        authors=person_list(course.get("author") or course.get("creator")),
        contributors=person_list(course.get("contributor")),
        trainers=person_list(instance.get("instructor") or instance.get("performer") or course.get("instructor")),
    )


def format_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        return value


def md_link(label: str, url: str) -> str:
    label = label.strip()
    url = url.strip()
    if label and url:
        return f"[{label}]({url})"
    return label or url


def render_people(people: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for person in people:
        url = person.get("orcid") or person.get("url") or ""
        lines.append(f"- {md_link(person['name'], url)}")
    return lines


def render_generated_block(meta: CourseMetadata) -> str:
    lines: list[str] = [START_MARKER, "## Course metadata", ""]
    lines.append(f"**Title:** {meta.name or 'TBC'}")

    if meta.description:
        lines.extend(["", "**Description**", meta.description])
    if meta.audience:
        lines.extend(["", f"**Target Audience:** {', '.join(meta.audience)}"])
    if meta.educational_level:
        lines.append(f"**Level:** {meta.educational_level}")
    if meta.prerequisites:
        lines.extend(["", "**Prerequisites**"])
        lines.extend(f"{i}. {item}" for i, item in enumerate(meta.prerequisites, 1))
    if meta.teaches:
        lines.extend(["", "**Learning Outcomes:**", "By the end of the course, learners will be able to:"])
        lines.extend(f"{i}. {item}" for i, item in enumerate(meta.teaches, 1))
    if meta.start_date or meta.end_date:
        lines.append("")
        if meta.end_date and meta.end_date != meta.start_date:
            lines.append(f"**Date:** {format_date(meta.start_date)} to {format_date(meta.end_date)}")
        else:
            lines.append(f"**Date:** {format_date(meta.start_date)}")
    if meta.duration:
        lines.append(f"**Time estimation:** {meta.duration}")
    if meta.location:
        lines.append(f"**Location:** {meta.location}")
    if meta.license_url:
        lines.append(f"**License:** {md_link(meta.license_url, meta.license_url)}")
    if meta.language:
        lines.append(f"**Language:** {meta.language}")
    if meta.keywords:
        lines.append(f"**Keywords:** {', '.join(meta.keywords)}")
    if meta.materials_url:
        lines.extend(["", "**Supporting Materials:**", f"1. {md_link('Course materials', meta.materials_url)}"])
    if meta.trainers:
        lines.extend(["", "**Trainers:**"])
        lines.extend(render_people(meta.trainers))
    if meta.authors or meta.contributors:
        lines.extend(["", "## Authors and Contributors", ""])
        if meta.authors:
            lines.extend(["Authors", ""])
            lines.extend(render_people(meta.authors))
        if meta.contributors:
            lines.extend(["", "Contributors", ""])
            lines.extend(render_people(meta.contributors))

    lines.extend(["", f"_Metadata source: {meta.source_url}_", END_MARKER])
    return "\n".join(lines).strip() + "\n"


def replace_between_markers(readme: str, block: str) -> tuple[str, bool]:
    pattern = re.compile(rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", flags=re.DOTALL)
    if pattern.search(readme):
        return pattern.sub(block.strip(), readme), True
    return readme, False


def replace_heading_section(text: str, heading_regex: str, replacement: str) -> str:
    pattern = re.compile(
        rf"({heading_regex})(.*?)(?=\n\s*(?:>?\s*\*\*[^\n]+\*\*|#{1,6}\s+|$))",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(replacement.rstrip() + "\n", text, count=1)


def best_effort_update(readme: str, meta: CourseMetadata) -> str:
    updated = readme
    if meta.name:
        updated = re.sub(r"(?m)^#\s+.+$", f"# {meta.name}", updated, count=1)
    if meta.description:
        updated = replace_heading_section(updated, r">?\s*\*\*Description\*\*", f"**Description**\n{meta.description}\n")
    if meta.audience:
        updated = replace_heading_section(updated, r">?\s*\*\*Target Audience:\*\*", f"**Target Audience:** {', '.join(meta.audience)}\n")
    if meta.educational_level:
        updated = replace_heading_section(updated, r">?\s*\*\*Level:\*\*", f"**Level:** {meta.educational_level}\n")
    if meta.prerequisites:
        prereq = "**Prerequisites**\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(meta.prerequisites, 1)) + "\n"
        updated = replace_heading_section(updated, r">?\s*\*\*Prerequisites\*\*", prereq)
    if meta.teaches:
        outcomes = "**Learning Outcomes:**\nBy the end of the course, learners will be able to:\n" + "\n".join(
            f"{i}. {x}" for i, x in enumerate(meta.teaches, 1)
        ) + "\n"
        updated = replace_heading_section(updated, r">?\s*\*\*Learning Outcomes:\*\*", outcomes)
    if meta.license_url:
        updated = replace_heading_section(updated, r">?\s*\*\*License:\*\*", f"**License:** {md_link(meta.license_url, meta.license_url)}\n")
    return updated


def update_readme(readme_text: str, meta: CourseMetadata, marker_mode: str) -> str:
    block = render_generated_block(meta)

    if marker_mode == "append":
        text, replaced = replace_between_markers(readme_text, block)
        if replaced:
            return text
        return readme_text.rstrip() + "\n\n" + block

    if marker_mode == "require":
        text, replaced = replace_between_markers(readme_text, block)
        if not replaced:
            raise ValueError(f"README does not contain {START_MARKER} and {END_MARKER}.")
        return text

    # best-effort: prefer markers, otherwise replace known fields in the existing template.
    text, replaced = replace_between_markers(readme_text, block)
    if replaced:
        return text
    return best_effort_update(readme_text, meta)


def find_first_url(text: str) -> str:
    urls = re.findall(r"https?://[^\s)>'\"]+", text)
    if not urls:
        return ""
    for url in urls:
        if "training.vib.be/all-trainings" in url:
            return url
    return urls[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Update README metadata from JSON-LD in a course page HTML header.")
    parser.add_argument("--url", help="Course URL. If omitted, the script tries to detect a URL in the README.")
    parser.add_argument("--readme", default="README.md", help="README path to update.")
    parser.add_argument("--output", help="Optional output path. Defaults to overwriting --readme.")
    parser.add_argument(
        "--marker-mode",
        choices=["best-effort", "append", "require"],
        default="append",
        help="How to update the README when metadata markers are absent.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print updated README to stdout without writing.")
    parser.add_argument("--allow-body-jsonld", action="store_true", help="Fallback to JSON-LD outside <head> if no head JSON-LD exists.")
    args = parser.parse_args()

    readme_path = Path(args.readme)
    if not readme_path.exists():
        raise FileNotFoundError(f"README file not found: {readme_path}")

    readme_text = readme_path.read_text(encoding="utf-8")
    url = args.url or find_first_url(readme_text)
    if not url:
        raise ValueError("No --url was supplied and no URL could be detected in the README.")

    objects = fetch_jsonld_objects(url, prefer_head=not args.allow_body_jsonld)
    course, instance = select_course_and_instance(objects)
    meta = normalise_metadata(course, instance, url)
    updated = update_readme(readme_text, meta, args.marker_mode)

    if args.dry_run:
        print(updated)
    else:
        output_path = Path(args.output) if args.output else readme_path
        output_path.write_text(updated, encoding="utf-8")
        print(f"Updated {output_path} from JSON-LD in the HTML header at {url}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
