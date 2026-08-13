#!/usr/bin/env python3
"""
Update a course README from JSON-LD metadata embedded in a course web page.

Typical use:
  python scripts/update_readme_from_jsonld.py \
    --url "https://training.vib.be/all-trainings/nextflow-reproducible-and-automated-data-analysis-7" \
    --readme README.md

The script supports two README update styles:

1. Marker-based replacement, recommended for stable automation:

   <!-- COURSE_METADATA_START -->
   old generated metadata
   <!-- COURSE_METADATA_END -->

2. Best-effort section replacement for common lesson-template headings such as:
   **Target Audience:**, **Level:**, **Prerequisites**, **Description**,
   **Learning Outcomes:**, **Time estimation**, **Supporting Materials**,
   **Authors and Contributors**.

If the markers are absent, the script updates known sections where possible and
leaves the rest of the README untouched.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen


START_MARKER = "<!-- COURSE_METADATA_START -->"
END_MARKER = "<!-- COURSE_METADATA_END -->"


@dataclass
class CourseMetadata:
    source_url: str
    name: str = ""
    description: str = ""
    keywords: list[str] | None = None
    audience: list[str] | None = None
    educational_level: str = ""
    prerequisites: list[str] | None = None
    teaches: list[str] | None = None
    license_url: str = ""
    language: str = ""
    start_date: str = ""
    end_date: str = ""
    duration: str = ""
    location: str = ""
    materials_url: str = ""
    authors: list[dict[str, str]] | None = None
    contributors: list[dict[str, str]] | None = None


def fetch_html(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "course-readme-jsonld-updater/1.0"})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def strip_html_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def text_list(value: Any) -> list[str]:
    out: list[str] = []
    for item in as_list(value):
        if isinstance(item, dict):
            label = item.get("name") or item.get("text") or item.get("@id") or item.get("url")
        else:
            label = str(item)
        label = strip_html_tags(str(label)) if label else ""
        if label and label not in out:
            out.append(label)
    return out


def person_list(value: Any) -> list[dict[str, str]]:
    people: list[dict[str, str]] = []
    for item in as_list(value):
        if isinstance(item, dict):
            name = strip_html_tags(str(item.get("name", "")))
            url = str(item.get("url") or item.get("@id") or "")
            orcid = ""
            same_as = as_list(item.get("sameAs"))
            for entry in same_as:
                if isinstance(entry, str) and "orcid.org" in entry:
                    orcid = entry
                    break
            if not orcid and "orcid.org" in url:
                orcid = url
            if name:
                people.append({"name": name, "url": url, "orcid": orcid})
        elif item:
            people.append({"name": strip_html_tags(str(item)), "url": "", "orcid": ""})
    return people


def iter_jsonld_candidates(html_text: str) -> Iterable[Any]:
    # Standard JSON-LD script tags
    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html_text):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            # Some sites include trailing commas or escaped slashes. Keep failure non-fatal.
            continue

    # LiaScript-style / markdown-style JSON-LD blocks, as in README-example.md:
    # ```json
    # @JSONLD { ... }
    # ```
    block_pattern = re.compile(r"@JSONLD\s*(\{.*?\})\s*```", flags=re.DOTALL)
    for match in block_pattern.finditer(html_text):
        try:
            yield json.loads(match.group(1))
        except json.JSONDecodeError:
            continue


def flatten_jsonld(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, list):
        for item in node:
            yield from flatten_jsonld(item)
    elif isinstance(node, dict):
        yield node
        graph = node.get("@graph")
        if graph:
            yield from flatten_jsonld(graph)


def score_jsonld(item: dict[str, Any]) -> int:
    types = as_list(item.get("@type"))
    type_text = " ".join(str(t).lower() for t in types)
    score = 0
    for term in ["course", "courseinstance", "learningresource", "event", "educationevent"]:
        if term.lower() in type_text:
            score += 5
    for key in ["name", "description", "teaches", "audience", "educationalLevel", "startDate", "endDate"]:
        if item.get(key):
            score += 1
    return score


def select_course_jsonld(html_text: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for candidate in iter_jsonld_candidates(html_text):
        candidates.extend(flatten_jsonld(candidate))
    candidates = [c for c in candidates if isinstance(c, dict)]
    if not candidates:
        raise ValueError("No JSON-LD object found in the supplied URL.")
    candidates.sort(key=score_jsonld, reverse=True)
    best = candidates[0]
    if score_jsonld(best) == 0:
        raise ValueError("JSON-LD was found, but no course-like object could be identified.")
    return best


def first_text(*values: Any) -> str:
    for value in values:
        values_as_text = text_list(value)
        if values_as_text:
            return values_as_text[0]
    return ""


def extract_material_url(value: Any) -> str:
    for item in as_list(value):
        if isinstance(item, dict):
            url = item.get("url") or item.get("@id")
            if url:
                return str(url)
        elif isinstance(item, str) and item.startswith("http"):
            return item
    return ""


def extract_location(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        name = value.get("name")
        if name:
            parts.append(str(name))
        address = value.get("address")
        if isinstance(address, dict):
            for key in ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]:
                if address.get(key):
                    parts.append(str(address[key]))
        elif address:
            parts.append(str(address))
        return ", ".join(dict.fromkeys(strip_html_tags(p) for p in parts if p))
    return first_text(value)


def normalise_metadata(data: dict[str, Any], source_url: str) -> CourseMetadata:
    keywords = data.get("keywords")
    if isinstance(keywords, str):
        keywords_list = [k.strip() for k in re.split(r"[,;]", keywords) if k.strip()]
    else:
        keywords_list = text_list(keywords)

    audience_items: list[str] = []
    for item in as_list(data.get("audience")):
        if isinstance(item, dict):
            audience_items.extend(text_list(item.get("audienceType") or item.get("name")))
        else:
            audience_items.extend(text_list(item))

    prereq = data.get("competencyRequired") or data.get("coursePrerequisites") or data.get("educationalPrerequisites")

    return CourseMetadata(
        source_url=source_url,
        name=first_text(data.get("name"), data.get("headline")),
        description=first_text(data.get("description"), data.get("abstract")),
        keywords=keywords_list,
        audience=audience_items,
        educational_level=first_text(data.get("educationalLevel")),
        prerequisites=text_list(prereq),
        teaches=text_list(data.get("teaches") or data.get("learningOutcome")),
        license_url=first_text(data.get("license")),
        language=first_text(data.get("inLanguage")),
        start_date=first_text(data.get("startDate")),
        end_date=first_text(data.get("endDate")),
        duration=first_text(data.get("duration") or data.get("timeRequired")),
        location=extract_location(data.get("location")),
        materials_url=extract_material_url(data.get("workFeatured") or data.get("hasPart") or data.get("associatedMedia")),
        authors=person_list(data.get("author") or data.get("creator")),
        contributors=person_list(data.get("contributor")),
    )


def format_date(value: str) -> str:
    if not value:
        return ""
    # Keep dates readable while avoiding locale-specific formatting.
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


def render_generated_block(meta: CourseMetadata) -> str:
    lines: list[str] = []
    lines.append(START_MARKER)
    lines.append("## Course metadata")
    lines.append("")
    lines.append(f"**Title:** {meta.name or 'TBC'}")
    if meta.description:
        lines.append("")
        lines.append("**Description**")
        lines.append(meta.description)
    if meta.audience:
        lines.append("")
        lines.append(f"**Target Audience:** {', '.join(meta.audience)}")
    if meta.educational_level:
        lines.append(f"**Level:** {meta.educational_level}")
    if meta.prerequisites:
        lines.append("")
        lines.append("**Prerequisites**")
        lines.extend(f"{i}. {item}" for i, item in enumerate(meta.prerequisites, 1))
    if meta.teaches:
        lines.append("")
        lines.append("**Learning Outcomes:**")
        lines.append("By the end of the course, learners will be able to:")
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
        lines.append("")
        lines.append("**Supporting Materials:**")
        lines.append(f"1. {md_link('Course materials', meta.materials_url)}")
    if meta.authors:
        lines.append("")
        lines.append("## Authors and Contributors")
        lines.append("")
        lines.append("Authors")
        lines.append("")
        for person in meta.authors:
            label = person["name"]
            url = person.get("orcid") or person.get("url") or ""
            lines.append(f"- {md_link(label, url)}")
    if meta.contributors:
        if not meta.authors:
            lines.append("")
            lines.append("## Authors and Contributors")
            lines.append("")
        lines.append("")
        lines.append("Contributors")
        lines.append("")
        for person in meta.contributors:
            label = person["name"]
            url = person.get("orcid") or person.get("url") or ""
            lines.append(f"- {md_link(label, url)}")
    lines.append("")
    lines.append(f"_Metadata source: {meta.source_url}_")
    lines.append(END_MARKER)
    return "\n".join(lines).strip() + "\n"


def replace_between_markers(readme: str, block: str) -> tuple[str, bool]:
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
        flags=re.DOTALL,
    )
    if pattern.search(readme):
        return pattern.sub(block.strip(), readme), True
    return readme, False


def replace_heading_section(text: str, heading_regex: str, replacement: str) -> str:
    # Replaces a section starting at a known markdown/bold heading until the next bold heading or markdown heading.
    pattern = re.compile(
        rf"({heading_regex})(.*?)(?=\n\s*(?:\*\*[^\n]+\*\*|#{1,6}\s+|$))",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(replacement.rstrip() + "\n", text, count=1)


def best_effort_update(readme: str, meta: CourseMetadata) -> str:
    updated = readme

    if meta.name:
        updated = re.sub(r"(?m)^#\s+.+$", f"# {meta.name}", updated, count=1)

    if meta.description:
        updated = replace_heading_section(updated, r"\*\*Description\*\*", f"**Description**\n{meta.description}\n")

    if meta.audience:
        updated = replace_heading_section(updated, r"\*\*Target Audience:\*\*", f"**Target Audience:** {', '.join(meta.audience)}\n")

    if meta.educational_level:
        updated = replace_heading_section(updated, r"\*\*Level:\*\*", f"**Level:** {meta.educational_level}\n")

    if meta.prerequisites:
        prereq = "**Prerequisites**\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(meta.prerequisites, 1)) + "\n"
        updated = replace_heading_section(updated, r"\*\*Prerequisites\*\*", prereq)

    if meta.teaches:
        outcomes = "**Learning Outcomes:**\nBy the end of the course, learners will be able to:\n" + "\n".join(
            f"{i}. {x}" for i, x in enumerate(meta.teaches, 1)
        ) + "\n"
        updated = replace_heading_section(updated, r"\*\*Learning Outcomes:\*\*", outcomes)

    if meta.license_url:
        updated = replace_heading_section(
            updated,
            r"\*\*License:\*\*",
            f"**License:** {md_link(meta.license_url, meta.license_url)}\n",
        )

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
            raise ValueError(
                f"README does not contain {START_MARKER} and {END_MARKER}. "
                "Add the markers or run with --marker-mode append."
            )
        return text

    # marker_mode == best-effort
    text, replaced = replace_between_markers(readme_text, block)
    if replaced:
        return text
    return best_effort_update(readme_text, meta)


def main() -> int:
    parser = argparse.ArgumentParser(description="Update README metadata from JSON-LD embedded in a course URL.")
    parser.add_argument("--url", help="Course URL. If omitted, the script tries to read it from the README.")
    parser.add_argument("--readme", default="README.md", help="Path to README file to update.")
    parser.add_argument("--output", help="Optional output file. Defaults to overwriting --readme.")
    parser.add_argument(
        "--marker-mode",
        choices=["best-effort", "append", "require"],
        default="best-effort",
        help="How to update the README when metadata markers are missing.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the updated README to stdout without writing a file.")
    args = parser.parse_args()

    readme_path = Path(args.readme)
    if not readme_path.exists():
        raise FileNotFoundError(f"README file not found: {readme_path}")

    readme_text = readme_path.read_text(encoding="utf-8")
    url = args.url or find_first_url(readme_text)
    if not url:
        raise ValueError("No --url was supplied and no URL could be detected in the README.")

    page_html = fetch_html(url)
    jsonld = select_course_jsonld(page_html)
    meta = normalise_metadata(jsonld, url)
    updated = update_readme(readme_text, meta, args.marker_mode)

    if args.dry_run:
        print(updated)
    else:
        output_path = Path(args.output) if args.output else readme_path
        output_path.write_text(updated, encoding="utf-8")
        print(f"Updated {output_path} from JSON-LD at {url}")
    return 0


def find_first_url(text: str) -> str:
    # Prefer VIB training URLs if present, otherwise return the first http(s) URL.
    urls = re.findall(r"https?://[^\s)>'\"]+", text)
    if not urls:
        return ""
    for url in urls:
        if "training.vib.be/all-trainings" in url:
            return url
    return urls[0]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
