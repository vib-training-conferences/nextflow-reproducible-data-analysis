#!/usr/bin/env python3
"""Update a LiaScript course README from a VIB course page.

The script first tries to read Schema.org/Bioschemas JSON-LD from the HTML page.
If no JSON-LD is available, it falls back to parsing visible HTML sections.
It then updates the original LiaScript-flavoured README template rather than
appending a generic metadata block.

It rewrites, where present:
  - the main Markdown title
  - the LiaScript "Lesson overview" blockquote section
  - the embedded LiaScript @JSONLD block

The rest of the lesson content is preserved.
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
from urllib.parse import urljoin
from urllib.request import Request, urlopen

COURSE_TYPES = {"course", "learningresource", "trainingmaterial", "event", "educationevent", "courseinstance"}
INSTANCE_TYPES = {"courseinstance", "event", "educationevent"}


@dataclass
class Person:
    name: str
    url: str = ""
    orcid: str = ""


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
    license_url: str = "https://creativecommons.org/licenses/by/4.0/"
    language: str = "en-US"
    start_date: str = ""
    end_date: str = ""
    duration: str = ""
    location: str = ""
    materials_url: str = ""
    learning_resource_type: list[str] = field(default_factory=lambda: ["tutorial"])
    authors: list[Person] = field(default_factory=list)
    contributors: list[Person] = field(default_factory=list)
    trainers: list[Person] = field(default_factory=list)


class JsonLdHTMLParser(HTMLParser):
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
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        script_type = attrs_d.get("type", "").lower().split(";", 1)[0].strip()
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
        elif tag == "head":
            self.in_head = False


class ReadableHTMLParser(HTMLParser):
    BLOCK_TAGS = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.current_href = ""
        self.current_link_text: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            attrs_d = {k.lower(): (v or "") for k, v in attrs}
            self.current_href = urljoin(self.base_url, attrs_d.get("href", ""))
            self.current_link_text = []

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        self.parts.append(text)
        self.parts.append(" ")
        if self.current_href:
            self.current_link_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "a" and self.current_href:
            label = re.sub(r"\s+", " ", " ".join(self.current_link_text)).strip()
            if label:
                self.links.append((label, self.current_href))
            self.current_href = ""
            self.current_link_text = []
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def lines(self) -> list[str]:
        text = html.unescape("".join(self.parts))
        text = re.sub(r"[ \t]+", " ", text)
        raw_lines = [x.strip() for x in text.splitlines()]
        out: list[str] = []
        for line in raw_lines:
            if line and (not out or out[-1] != line):
                out.append(line)
        return out


def fetch_html(url: str, timeout: int = 30) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 liascript-course-readme-updater/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def jsonld_blocks_from_html(html_text: str, prefer_head: bool = True) -> list[str]:
    parser = JsonLdHTMLParser()
    parser.feed(html_text)
    blocks = parser.head_blocks if prefer_head and parser.head_blocks else parser.all_blocks
    if blocks:
        return blocks
    # Fallback independent of HTMLParser head tracking.
    script_re = re.compile(
        r"<script\b(?=[^>]*\btype\s*=\s*(['\"])application/ld\+json(?:;[^'\"]*)?\1)[^>]*>(.*?)</script>",
        re.I | re.S,
    )
    return [m.group(2).strip() for m in script_re.finditer(html_text) if m.group(2).strip()]


def parse_jsonld_blocks(blocks: Iterable[str]) -> list[Any]:
    objects: list[Any] = []
    for block in blocks:
        block = html.unescape(block.strip())
        block = re.sub(r"^\s*<!--", "", block)
        block = re.sub(r"-->\s*$", "", block)
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        objects.extend(parsed if isinstance(parsed, list) else [parsed])
    return objects


def as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def strip_tags(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def text_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in as_list(value):
        if isinstance(item, dict):
            item = item.get("name") or item.get("text") or item.get("@id") or item.get("url") or ""
        label = strip_tags(item)
        if label and label not in out:
            out.append(label)
    return out


def first_text(*values: Any) -> str:
    for value in values:
        items = text_list(value)
        if items:
            return items[0]
    return ""


def type_names(item: dict[str, Any]) -> set[str]:
    return {str(t).lower() for t in as_list(item.get("@type"))}


def flatten_jsonld(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from flatten_jsonld(item)
    elif isinstance(node, dict):
        yield node
        for key in ["@graph", "hasCourseInstance", "courseInstance", "subEvent", "event"]:
            if key in node:
                yield from flatten_jsonld(node[key])


def score_jsonld(item: dict[str, Any], instance: bool = False) -> int:
    wanted = INSTANCE_TYPES if instance else COURSE_TYPES
    score = 10 * len(type_names(item) & wanted)
    for key in ["name", "description", "teaches", "learningOutcome", "audience", "educationalLevel", "startDate", "endDate", "location", "author", "contributor", "instructor"]:
        if item.get(key):
            score += 1
    return score


def select_course_and_instance(objects: list[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    flat = [x for obj in objects for x in flatten_jsonld(obj) if isinstance(x, dict)]
    courses = [x for x in flat if type_names(x) & COURSE_TYPES]
    if not courses:
        raise ValueError("JSON-LD was parsed, but no course-like object was found.")
    descriptive = [x for x in courses if not (type_names(x) <= INSTANCE_TYPES)] or courses
    course = max(descriptive, key=lambda x: score_jsonld(x, False))
    instances = [x for x in flat if type_names(x) & INSTANCE_TYPES]
    instance = max(instances, key=lambda x: score_jsonld(x, True)) if instances else {}
    return course, instance


def person_list(value: Any) -> list[Person]:
    people: list[Person] = []
    for item in as_list(value):
        if isinstance(item, dict):
            name = strip_tags(item.get("name", ""))
            url = str(item.get("url") or item.get("@id") or "")
            orcid = ""
            for same in as_list(item.get("sameAs")):
                if isinstance(same, str) and "orcid.org" in same:
                    orcid = same
                    break
            if not orcid and "orcid.org" in url:
                orcid = url
        else:
            name, url, orcid = strip_tags(item), "", ""
        if name and all(p.name != name for p in people):
            people.append(Person(name=name, url=url, orcid=orcid))
    return people


def extract_keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,;]", value) if x.strip()]
    return text_list(value)


def extract_audience(*values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        for item in as_list(value):
            if isinstance(item, dict):
                out.extend(text_list(item.get("audienceType") or item.get("name") or item.get("@type")))
            else:
                out.extend(text_list(item))
    return list(dict.fromkeys(out))


def extract_material_url(*values: Any) -> str:
    for value in values:
        for item in as_list(value):
            if isinstance(item, dict) and (item.get("url") or item.get("@id")):
                return str(item.get("url") or item.get("@id"))
            if isinstance(item, str) and item.startswith("http"):
                return item
    return ""


def extract_location(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            parts: list[str] = []
            if value.get("name"):
                parts.append(str(value["name"]))
            address = value.get("address")
            if isinstance(address, dict):
                parts.extend(str(address[k]) for k in ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"] if address.get(k))
            elif address:
                parts.append(str(address))
            clean = ", ".join(dict.fromkeys(strip_tags(p) for p in parts if p))
            if clean:
                return clean
        else:
            text = first_text(value)
            if text:
                return text
    return ""


def metadata_from_jsonld(objects: list[Any], source_url: str) -> CourseMetadata:
    course, instance = select_course_and_instance(objects)
    prereq = course.get("competencyRequired") or course.get("coursePrerequisites") or course.get("educationalPrerequisites") or instance.get("competencyRequired")
    return CourseMetadata(
        source_url=source_url,
        name=first_text(course.get("name"), instance.get("name")),
        description=first_text(course.get("description"), instance.get("description")),
        keywords=extract_keywords(course.get("keywords") or instance.get("keywords")),
        audience=extract_audience(course.get("audience"), instance.get("audience")),
        educational_level=first_text(course.get("educationalLevel"), instance.get("educationalLevel")),
        prerequisites=text_list(prereq),
        teaches=text_list(course.get("teaches") or course.get("learningOutcome") or instance.get("teaches") or instance.get("learningOutcome")),
        license_url=first_text(course.get("license"), instance.get("license")) or "https://creativecommons.org/licenses/by/4.0/",
        language=first_text(course.get("inLanguage"), instance.get("inLanguage")) or "en-US",
        start_date=first_text(instance.get("startDate"), course.get("startDate")),
        end_date=first_text(instance.get("endDate"), course.get("endDate")),
        duration=first_text(instance.get("duration"), course.get("duration"), course.get("timeRequired")),
        location=extract_location(instance.get("location"), course.get("location")),
        materials_url=extract_material_url(course.get("workFeatured"), course.get("hasPart"), course.get("associatedMedia"), course.get("url")),
        authors=person_list(course.get("author") or course.get("creator")),
        contributors=person_list(course.get("contributor")),
        trainers=person_list(instance.get("instructor") or instance.get("performer") or course.get("instructor")),
    )


def lines_between(lines: list[str], start: str, end: str | list[str]) -> list[str]:
    ends = [end] if isinstance(end, str) else end
    try:
        i = next(idx for idx, line in enumerate(lines) if line.lower() == start.lower()) + 1
    except StopIteration:
        return []
    j = len(lines)
    for idx in range(i, len(lines)):
        if any(lines[idx].lower() == e.lower() for e in ends):
            j = idx
            break
    return [x for x in lines[i:j] if x]


def metadata_from_html(html_text: str, source_url: str) -> CourseMetadata:
    parser = ReadableHTMLParser(source_url)
    parser.feed(html_text)
    lines = parser.lines()
    lower = [x.lower() for x in lines]

    def after(label: str) -> str:
        label_l = label.lower()
        for idx, line in enumerate(lower):
            if line == label_l and idx + 1 < len(lines):
                return lines[idx + 1]
            if line.startswith(label_l) and len(lines[idx]) > len(label):
                return lines[idx][len(label):].strip(" :")
        return ""

    title = ""
    for line in lines:
        if line not in {"Home", "All training"}:
            title = line
            break

    date_line = ""
    for line in lines[:30]:
        if re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", line):
            date_line = line
            break

    description = "\n".join(lines_between(lines, "General context", ["Learning outcomes", "Approach"]))
    teaches = lines_between(lines, "Learning outcomes", ["Approach", "Event intended for"])
    prerequisites = lines_between(lines, "Required skills", ["Course materials", "Extra information"])
    audience = lines_between(lines, "Target Audience:", ["Location:", "Duration:"]) or [after("Event intended for")]
    audience = [x for x in audience if x and x != ":"]
    location_value = after("Location:")
    duration_value = " ".join(lines_between(lines, "Duration:", ["General context"]))

    materials_url = ""
    for label, href in parser.links:
        if "github.com" in href or "course materials" in label.lower() or title.lower() in label.lower():
            materials_url = href
            break

    trainers: list[Person] = []
    trainer_lines = lines_between(lines, "Trainers", ["Program", "Practical info"])
    for idx, item in enumerate(trainer_lines):
        if item.startswith("VIB ") or item.startswith("CMGG") or "@" in item or item == "ORCID" or item.startswith("Contact"):
            continue
        if idx + 1 < len(trainer_lines) and (trainer_lines[idx + 1].startswith("VIB") or trainer_lines[idx + 1].startswith("CMGG")):
            trainers.append(Person(name=item))

    return CourseMetadata(
        source_url=source_url,
        name=title,
        description=description,
        audience=audience,
        prerequisites=prerequisites,
        teaches=teaches,
        start_date=date_line,
        duration=duration_value,
        location=location_value,
        materials_url=materials_url,
        trainers=trainers,
    )


def parse_existing_people(readme_text: str) -> tuple[list[Person], list[Person]]:
    authors: list[Person] = []
    contributors: list[Person] = []
    current: list[Person] | None = None
    for line in readme_text.splitlines():
        clean = line.strip().lstrip(">").strip()
        if clean.lower() == "authors":
            current = authors
            continue
        if clean.lower() == "contributors":
            current = contributors
            continue
        if clean.startswith("## ") and current is not None:
            current = None
        if current is None:
            continue
        m = re.search(r"\]\((https://orcid\.org/[^)]+)\)\s*(.+)$", clean)
        if m:
            current.append(Person(name=m.group(2).strip(), orcid=m.group(1)))
        elif clean and not clean.startswith("**"):
            name = re.sub(r"^[-*]\s*", "", clean).strip()
            if name:
                current.append(Person(name=name))
    return authors, contributors


def merge_missing_metadata(meta: CourseMetadata, readme_text: str) -> CourseMetadata:
    old_authors, old_contributors = parse_existing_people(readme_text)
    if not meta.authors and old_authors:
        meta.authors = old_authors
    if not meta.contributors and old_contributors:
        meta.contributors = old_contributors
    if not meta.educational_level:
        level_match = re.search(r"\*\*Level:\*\*\s*([^\n>]+)", readme_text, re.I)
        if level_match:
            meta.educational_level = level_match.group(1).strip()
    return meta


def markdown_list(items: list[str], quote: bool = True) -> list[str]:
    prefix = "> " if quote else ""
    return [f"{prefix}{i}. {item}" for i, item in enumerate(items, 1)]


def render_person(person: Person, quote: bool = True) -> str:
    prefix = "> " if quote else ""
    if person.orcid:
        return f"{prefix}[ ]({person.orcid}) {person.name}"
    if person.url:
        return f"{prefix}[{person.name}]({person.url})"
    return f"{prefix}{person.name}"


def render_lia_lesson_overview(meta: CourseMetadata) -> str:
    lines: list[str] = []
    lines += ["Lesson overview", "----------------", "> "]
    if meta.license_url:
        lines += [f"> **License:** [Creative Commons Attribution 4.0 International License]({meta.license_url})", ">"]
    if meta.audience:
        lines += [f"> **Target Audience:** {', '.join(meta.audience)}", ">"]
    if meta.educational_level:
        lines += [f"> **Level:** {meta.educational_level}", ">"]
    if meta.prerequisites:
        lines += ["> **Prerequisites**", "> To be able to follow this course, learners should have knowledge in:", ">"]
        lines += markdown_list(meta.prerequisites, quote=True)
        lines += [">"]
    if meta.description:
        lines += ["> **Description**"]
        for paragraph in re.split(r"\n\s*\n|\n", meta.description.strip()):
            if paragraph.strip():
                lines += [f"> {paragraph.strip()}"]
        lines += [">"]
    if meta.teaches:
        lines += ["> **Learning Outcomes:**", "> By the end of the course, learners will be able to:", ">"]
        lines += markdown_list(meta.teaches, quote=True)
        lines += [">"]
    if meta.duration:
        lines += [f"> **Time estimation**: {meta.duration}", ">"]
    if meta.materials_url:
        lines += ["> **Supporting Materials**:", ">", f"> 1. [Course materials]({meta.materials_url})", ">"]
    if meta.location or meta.start_date:
        lines += ["> **Course instance:**"]
        if meta.start_date:
            lines += [f"> Date: {meta.start_date}"]
        if meta.location:
            lines += [f"> Location: {meta.location}"]
        lines += [">"]
    if meta.authors or meta.contributors:
        lines += ["## Authors and Contributors", "", "Authors", ""]
        lines += [render_person(p, quote=True) for p in meta.authors]
        if meta.contributors:
            lines += [">", "Contributors", ">"]
            lines += [render_person(p, quote=True) for p in meta.contributors]
    return "\n".join(lines).rstrip() + "\n"


def url_or_blank(value: str) -> str:
    return value or ""


def person_to_jsonld(person: Person) -> dict[str, str]:
    obj = {"@type": "Person", "name": person.name}
    if person.orcid:
        obj["@id"] = person.orcid
    elif person.url:
        obj["url"] = person.url
    return obj


def render_jsonld_block(meta: CourseMetadata) -> str:
    data: dict[str, Any] = {
        "@context": "https://schema.org/",
        "@type": "LearningResource",
        "@id": meta.materials_url or meta.source_url,
        "http://purl.org/dc/terms/conformsTo": {
            "@type": "CreativeWork",
            "@id": "https://bioschemas.org/profiles/TrainingMaterial/1.0-RELEASE",
        },
        "description": meta.description,
        "keywords": ", ".join(meta.keywords),
        "name": meta.name,
        "license": meta.license_url,
        "educationalLevel": meta.educational_level,
        "competencyRequired": meta.prerequisites,
        "teaches": meta.teaches,
        "audience": ", ".join(meta.audience),
        "inLanguage": meta.language,
        "learningResourceType": meta.learning_resource_type,
        "author": [person_to_jsonld(p) for p in meta.authors],
        "contributor": [person_to_jsonld(p) for p in meta.contributors],
    }
    if meta.start_date or meta.location or meta.trainers:
        data["hasCourseInstance"] = {
            "@type": "CourseInstance",
            "startDate": meta.start_date,
            "endDate": meta.end_date,
            "location": meta.location,
            "instructor": [person_to_jsonld(p) for p in meta.trainers],
        }
    # Drop empty values but keep empty lists where they are semantically useful? No.
    data = {k: v for k, v in data.items() if v not in ("", [], None)}
    return "```json\n@JSONLD " + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n"


def replace_or_insert_title(readme_text: str, title: str) -> str:
    if not title:
        return readme_text
    # Allow an opening LiaScript comment before the first H1.
    return re.sub(r"(?m)^#\s+.+$", f"# {title}", readme_text, count=1)


def replace_lesson_overview(readme_text: str, meta: CourseMetadata) -> str:
    block = render_lia_lesson_overview(meta)
    pattern = re.compile(
        r"(?ms)^Lesson overview\s*\n[-=]+\s*\n.*?(?=^## Proposed Schedule\s*$|^# Workshop and Material organization\s*$|^## Chapters List\s*$|^#\s+)",
    )
    if pattern.search(readme_text):
        return pattern.sub(block + "\n", readme_text, count=1)

    # If no lesson overview exists, insert after first H1.
    h1 = re.search(r"(?m)^#\s+.+$", readme_text)
    if h1:
        insert_at = h1.end()
        return readme_text[:insert_at] + "\n\n" + block + readme_text[insert_at:]
    return block + "\n" + readme_text


def replace_jsonld_block(readme_text: str, meta: CourseMetadata) -> str:
    block = render_jsonld_block(meta)
    pattern = re.compile(r"(?ms)^```json\s*\n@JSONLD\s*\{.*?^```\s*$")
    if pattern.search(readme_text):
        return pattern.sub(block.rstrip(), readme_text, count=1)
    return readme_text.rstrip() + "\n\n" + block


def update_lia_readme(readme_text: str, meta: CourseMetadata, update_jsonld: bool = True) -> str:
    text = replace_or_insert_title(readme_text, meta.name)
    text = replace_lesson_overview(text, meta)
    if update_jsonld:
        text = replace_jsonld_block(text, meta)
    return text


def find_first_url(text: str) -> str:
    urls = re.findall(r"https?://[^\s)>'\"]+", text)
    if not urls:
        return ""
    return next((u for u in urls if "training.vib.be/all-trainings" in u), urls[0])


def main() -> int:
    parser = argparse.ArgumentParser(description="Update a LiaScript README from a course page.")
    parser.add_argument("--url", help="Course URL. If omitted, a URL is detected from the README.")
    parser.add_argument("--readme", default="README.md")
    parser.add_argument("--output")
    parser.add_argument("--debug-html", help="Save fetched HTML for inspection.")
    parser.add_argument("--allow-body-jsonld", action="store_true")
    parser.add_argument("--no-html-fallback", action="store_true")
    parser.add_argument("--no-jsonld-update", action="store_true", help="Do not update/insert the LiaScript @JSONLD block.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    readme_path = Path(args.readme)
    readme_text = readme_path.read_text(encoding="utf-8")
    url = args.url or find_first_url(readme_text)
    if not url:
        raise ValueError("No --url supplied and no URL detected in README.")

    html_text = fetch_html(url)
    if args.debug_html:
        Path(args.debug_html).write_text(html_text, encoding="utf-8")

    objects = parse_jsonld_blocks(jsonld_blocks_from_html(html_text, prefer_head=not args.allow_body_jsonld))
    if objects:
        meta = metadata_from_jsonld(objects, url)
        source = "JSON-LD"
    elif not args.no_html_fallback:
        meta = metadata_from_html(html_text, url)
        source = "HTML fallback"
    else:
        raise ValueError("No parseable JSON-LD found in the fetched HTML.")

    meta = merge_missing_metadata(meta, readme_text)
    updated = update_lia_readme(readme_text, meta, update_jsonld=not args.no_jsonld_update)

    if args.dry_run:
        print(updated)
    else:
        output_path = Path(args.output) if args.output else readme_path
        output_path.write_text(updated, encoding="utf-8")
        print(f"Updated {output_path} from {source} at {url} using LiaScript template style")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
