#!/usr/bin/env python3
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

START_MARKER = "<!-- COURSE_METADATA_START -->"
END_MARKER = "<!-- COURSE_METADATA_END -->"
COURSE_TYPES = {"course", "learningresource", "trainingmaterial", "event", "educationevent", "courseinstance"}
INSTANCE_TYPES = {"courseinstance", "event", "educationevent"}


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
        elif tag == "head":
            self.in_head = False


class ReadableHTMLParser(HTMLParser):
    """Small standard-library HTML parser used when no JSON-LD is available."""

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
        lines = [x.strip() for x in text.splitlines()]
        cleaned: list[str] = []
        for line in lines:
            if line and (not cleaned or cleaned[-1] != line):
                cleaned.append(line)
        return cleaned


def fetch_html(url: str, timeout: int = 30) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 course-readme-jsonld-updater/1.2",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def extract_balanced_jsonld_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    markers = re.finditer(r'"@context"\s*:\s*"https?://schema\.org/?"|"@context"\s*:', text)
    for marker in markers:
        start = text.rfind("{", 0, marker.start())
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        if "@context" in candidate and "schema.org" in candidate:
                            candidates.append(candidate)
                        break
    return list(dict.fromkeys(candidates))


def jsonld_blocks_from_html(html_text: str, prefer_head: bool = True) -> list[str]:
    parser = JsonLdHTMLParser()
    parser.feed(html_text)
    blocks = parser.head_blocks if prefer_head and parser.head_blocks else parser.all_blocks
    if blocks:
        return blocks

    script_re = re.compile(
        r"<script\b(?=[^>]*\btype\s*=\s*(['\"])application/ld\+json(?:;[^'\"]*)?\1)[^>]*>(.*?)</script>",
        re.I | re.S,
    )
    blocks = [m.group(2).strip() for m in script_re.finditer(html_text) if m.group(2).strip()]
    return blocks or extract_balanced_jsonld_candidates(html_text)


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


def strip_tags(text: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
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
        raise ValueError("JSON-LD was parsed, but no course-like schema object was found.")
    descriptive = [x for x in courses if not (type_names(x) <= INSTANCE_TYPES)] or courses
    course = max(descriptive, key=lambda x: score_jsonld(x, False))
    instances = [x for x in flat if type_names(x) & INSTANCE_TYPES]
    instance = max(instances, key=lambda x: score_jsonld(x, True)) if instances else {}
    return course, instance


def person_list(value: Any) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
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
        if name:
            person = {"name": name, "url": url, "orcid": orcid}
            if person not in people:
                people.append(person)
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
        license_url=first_text(course.get("license"), instance.get("license")),
        language=first_text(course.get("inLanguage"), instance.get("inLanguage")),
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
        return ""

    title = ""
    for line in lines:
        if line and line not in {"Home", "All training"}:
            title = line
            break

    date_line = ""
    for line in lines[:20]:
        if re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", line):
            date_line = line
            break

    description = "\n".join(lines_between(lines, "General context", ["Learning outcomes", "Approach"]))
    teaches = lines_between(lines, "Learning outcomes", ["Approach", "Event intended for"])
    prerequisites = lines_between(lines, "Required skills", ["Course materials", "Extra information"])

    audience = lines_between(lines, "Target Audience:", ["Location:", "Duration:"]) or [after("Event intended for")]
    audience = [x for x in audience if x and x != ":"]

    location_value = after("Location:")
    duration_lines = lines_between(lines, "Duration:", ["General context"])
    duration_value = " ".join(duration_lines)

    materials_url = ""
    for label, href in parser.links:
        if "github.com" in href or "course materials" in label.lower() or title.lower() in label.lower():
            materials_url = href
            break

    trainers: list[dict[str, str]] = []
    trainer_names = lines_between(lines, "Trainers", ["Program", "Practical info"])
    for idx, item in enumerate(trainer_names):
        if item.startswith("VIB ") or item.startswith("CMGG") or "@" in item or item == "ORCID" or item.startswith("Contact"):
            continue
        if idx + 1 < len(trainer_names) and (trainer_names[idx + 1].startswith("VIB") or trainer_names[idx + 1].startswith("CMGG")):
            trainers.append({"name": item, "url": "", "orcid": ""})

    meta = CourseMetadata(
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
    return meta


def fmt_date(value: str) -> str:
    if not value:
        return ""
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return value


def md_link(label: str, url: str) -> str:
    return f"[{label}]({url})" if label and url else label or url


def people_lines(people: list[dict[str, str]]) -> list[str]:
    return [f"- {md_link(p['name'], p.get('orcid') or p.get('url') or '')}" for p in people]


def render_metadata(meta: CourseMetadata) -> str:
    lines = [START_MARKER, "## Course metadata", "", f"**Title:** {meta.name or 'TBC'}"]
    if meta.description:
        lines += ["", "**Description**", meta.description]
    if meta.audience:
        lines += ["", f"**Target Audience:** {', '.join(meta.audience)}"]
    if meta.educational_level:
        lines.append(f"**Level:** {meta.educational_level}")
    if meta.prerequisites:
        lines += ["", "**Prerequisites**"] + [f"{i}. {x}" for i, x in enumerate(meta.prerequisites, 1)]
    if meta.teaches:
        lines += ["", "**Learning Outcomes:**", "By the end of the course, learners will be able to:"] + [f"{i}. {x}" for i, x in enumerate(meta.teaches, 1)]
    if meta.start_date or meta.end_date:
        date_line = fmt_date(meta.start_date)
        if meta.end_date and meta.end_date != meta.start_date:
            date_line += f" to {fmt_date(meta.end_date)}"
        lines += ["", f"**Date:** {date_line}"]
    for label, value in [("Time estimation", meta.duration), ("Location", meta.location), ("Language", meta.language)]:
        if value:
            lines.append(f"**{label}:** {value}")
    if meta.license_url:
        lines.append(f"**License:** {md_link(meta.license_url, meta.license_url)}")
    if meta.keywords:
        lines.append(f"**Keywords:** {', '.join(meta.keywords)}")
    if meta.materials_url:
        lines += ["", "**Supporting Materials:**", f"1. {md_link('Course materials', meta.materials_url)}"]
    if meta.trainers:
        lines += ["", "**Trainers:**"] + people_lines(meta.trainers)
    if meta.authors or meta.contributors:
        lines += ["", "## Authors and Contributors", ""]
        if meta.authors:
            lines += ["Authors", ""] + people_lines(meta.authors)
        if meta.contributors:
            lines += ["", "Contributors", ""] + people_lines(meta.contributors)
    lines += ["", f"_Metadata source: {meta.source_url}_", END_MARKER]
    return "\n".join(lines).strip() + "\n"


def update_readme(text: str, meta: CourseMetadata, marker_mode: str) -> str:
    block = render_metadata(meta)
    pattern = re.compile(rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.S)
    if pattern.search(text):
        return pattern.sub(block.strip(), text)
    if marker_mode == "require":
        raise ValueError(f"README does not contain {START_MARKER} and {END_MARKER}.")
    if marker_mode == "best-effort" and meta.name:
        return re.sub(r"(?m)^#\s+.+$", f"# {meta.name}", text, count=1)
    return text.rstrip() + "\n\n" + block


COURSE_URL_PATTERNS = [
    "training.vib.be/all-trainings",
    "vibtrainingandconferences.be/training/",
]


def find_first_url(text: str) -> str:
    urls = re.findall(r"https?://[^\s)>'\"]+", text)
    for pattern in COURSE_URL_PATTERNS:
        match = next((u for u in urls if pattern in u), "")
        if match:
            return match
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Update README metadata from JSON-LD in a course page HTML header, with HTML scraping fallback.")
    parser.add_argument("--url", help="Course URL. If omitted, the script tries to detect a URL in the README.")
    parser.add_argument("--readme", default="README.md")
    parser.add_argument("--output")
    parser.add_argument("--marker-mode", choices=["append", "require", "best-effort"], default="append")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-body-jsonld", action="store_true")
    parser.add_argument("--debug-html", help="Save fetched HTML to this path for inspection.")
    parser.add_argument("--no-html-fallback", action="store_true", help="Fail if no JSON-LD is found instead of scraping visible HTML.")
    args = parser.parse_args()

    readme_path = Path(args.readme)
    readme_text = readme_path.read_text(encoding="utf-8")
    url = args.url or find_first_url(readme_text)
    if not url:
        print("No --url supplied and no course URL detected in README. Skipping update.", file=sys.stderr)
        return 0

    html_text = fetch_html(url)
    if args.debug_html:
        Path(args.debug_html).write_text(html_text, encoding="utf-8")

    objects = parse_jsonld_blocks(jsonld_blocks_from_html(html_text, prefer_head=not args.allow_body_jsonld))
    if objects:
        meta = metadata_from_jsonld(objects, url)
        source = "JSON-LD"
    elif args.no_html_fallback:
        raise ValueError("No JSON-LD parsed. Saved fetched HTML to debug file if --debug-html was supplied.")
    else:
        meta = metadata_from_html(html_text, url)
        source = "HTML fallback"
        if not meta.name:
            raise ValueError("No JSON-LD parsed and HTML fallback could not detect a course title.")

    updated = update_readme(readme_text, meta, args.marker_mode)
    if args.dry_run:
        print(updated)
    else:
        output_path = Path(args.output) if args.output else readme_path
        output_path.write_text(updated, encoding="utf-8")
        print(f"Updated {output_path} from {source} at {url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
