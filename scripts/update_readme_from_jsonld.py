#!/usr/bin/env python3
from __future__ import annotations

import argparse, datetime as dt, html, json, re, sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

START_MARKER = "<!-- COURSE_METADATA_START -->"
END_MARKER = "<!-- COURSE_METADATA_END -->"
COURSE_TYPES = {"course","learningresource","trainingmaterial","event","educationevent","courseinstance"}
INSTANCE_TYPES = {"courseinstance","event","educationevent"}

class JsonLdHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_head=False; self.in_jsonld=False; self.current=[]; self.current_in_head=False
        self.head_blocks=[]; self.all_blocks=[]
    def handle_starttag(self, tag, attrs):
        tag=tag.lower()
        if tag=="head": self.in_head=True; return
        if tag=="script":
            d={k.lower():(v or "") for k,v in attrs}
            st=d.get("type","").lower().split(";",1)[0].strip()
            if st=="application/ld+json":
                self.in_jsonld=True; self.current=[]; self.current_in_head=self.in_head
    def handle_data(self,data):
        if self.in_jsonld: self.current.append(data)
    def handle_endtag(self, tag):
        tag=tag.lower()
        if tag=="script" and self.in_jsonld:
            block="".join(self.current).strip()
            if block:
                self.all_blocks.append(block)
                if self.current_in_head: self.head_blocks.append(block)
            self.in_jsonld=False; self.current=[]; self.current_in_head=False
        elif tag=="head":
            self.in_head=False

@dataclass
class CourseMetadata:
    source_url: str
    name: str=""; description: str=""; keywords: list[str]=field(default_factory=list)
    audience: list[str]=field(default_factory=list); educational_level: str=""
    prerequisites: list[str]=field(default_factory=list); teaches: list[str]=field(default_factory=list)
    license_url: str=""; language: str=""; start_date: str=""; end_date: str=""; duration: str=""
    location: str=""; materials_url: str=""; authors: list[dict[str,str]]=field(default_factory=list)
    contributors: list[dict[str,str]]=field(default_factory=list); trainers: list[dict[str,str]]=field(default_factory=list)

def fetch_html(url: str, timeout:int=30)->str:
    req=Request(url, headers={"User-Agent":"Mozilla/5.0 course-readme-jsonld-updater/1.1", "Accept":"text/html,application/xhtml+xml"})
    with urlopen(req, timeout=timeout) as r:
        charset=r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, errors="replace")

def extract_balanced_jsonld_candidates(text: str)->list[str]:
    c=[]
    for m in re.finditer(r'"@context"\s*:\s*"https?://schema\.org/?"|"@context"\s*:', text):
        start=text.rfind('{',0,m.start())
        if start<0: continue
        depth=0; ins=False; esc=False
        for i,ch in enumerate(text[start:], start):
            if ins:
                if esc: esc=False
                elif ch=='\\': esc=True
                elif ch=='"': ins=False
            else:
                if ch=='"': ins=True
                elif ch=='{': depth+=1
                elif ch=='}':
                    depth-=1
                    if depth==0:
                        obj=text[start:i+1]
                        if '@context' in obj and 'schema.org' in obj: c.append(obj)
                        break
    return list(dict.fromkeys(c))

def jsonld_blocks_from_html(html_text: str, prefer_head: bool=True)->list[str]:
    parser=JsonLdHTMLParser(); parser.feed(html_text)
    blocks = parser.head_blocks if prefer_head and parser.head_blocks else parser.all_blocks
    if blocks: return blocks
    # Fallback: arbitrary attribute order, malformed head, minified HTML.
    script_re=re.compile(r"<script\b(?=[^>]*\btype\s*=\s*(['\"])application/ld\+json(?:;[^'\"]*)?\1)[^>]*>(.*?)</script>", re.I|re.S)
    blocks=[m.group(2).strip() for m in script_re.finditer(html_text) if m.group(2).strip()]
    return blocks or extract_balanced_jsonld_candidates(html_text)

def clean_jsonld_block(block: str)->str:
    block=html.unescape(block.strip())
    block=re.sub(r"^\s*<!--", "", block); block=re.sub(r"-->\s*$", "", block)
    block=re.sub(r"^\s*//\s*<!\[CDATA\[", "", block); block=re.sub(r"//\s*\]\]>\s*$", "", block)
    return block.strip()

def parse_jsonld_blocks(blocks: Iterable[str])->list[Any]:
    out=[]
    for b in blocks:
        b=clean_jsonld_block(b)
        try: data=json.loads(b)
        except json.JSONDecodeError: continue
        out.extend(data if isinstance(data,list) else [data])
    return out

def fetch_jsonld_objects(url: str, prefer_head: bool=True)->list[Any]:
    h=fetch_html(url); blocks=jsonld_blocks_from_html(h, prefer_head=prefer_head); objs=parse_jsonld_blocks(blocks)
    if not objs:
        raise ValueError(f"No JSON-LD objects could be parsed from the HTML page. Found {len(blocks)} candidate JSON-LD block(s). Run with --debug-html fetched.html to inspect the HTML fetched by the action.")
    return objs

def as_list(v):
    if v is None or v=="": return []
    return v if isinstance(v,list) else [v]

def strip_html_tags(t): return re.sub(r"\s+"," ", re.sub(r"<[^>]+>"," ", html.unescape(str(t or "")))).strip()
def text_list(v):
    out=[]
    for item in as_list(v):
        label=(item.get("name") or item.get("text") or item.get("@id") or item.get("url")) if isinstance(item,dict) else str(item)
        label=strip_html_tags(label)
        if label and label not in out: out.append(label)
    return out
def first_text(*vals):
    for v in vals:
        l=text_list(v)
        if l: return l[0]
    return ""
def type_names(item): return {str(t).lower() for t in as_list(item.get("@type"))}
def flatten_jsonld(node):
    if isinstance(node,list):
        for x in node: yield from flatten_jsonld(x)
    elif isinstance(node,dict):
        yield node
        for k in ["@graph","hasCourseInstance","courseInstance","subEvent","event"]:
            if k in node: yield from flatten_jsonld(node[k])
def score(item, instance=False):
    wanted=INSTANCE_TYPES if instance else COURSE_TYPES
    s=10*len(type_names(item)&wanted)
    for k in ["name","description","teaches","learningOutcome","audience","educationalLevel","startDate","endDate","location","author","contributor","instructor"]:
        if item.get(k): s+=1
    return s
def select_course_and_instance(objects):
    flat=[x for o in objects for x in flatten_jsonld(o) if isinstance(x,dict)]
    if not flat: raise ValueError("No JSON-LD objects could be parsed from the HTML page.")
    courses=[x for x in flat if type_names(x)&COURSE_TYPES]
    if not courses: raise ValueError("JSON-LD was parsed, but no course-like schema object was found.")
    desc=[x for x in courses if not (type_names(x)<=INSTANCE_TYPES)] or courses
    course=max(desc, key=lambda x: score(x,False))
    inst=[x for x in flat if type_names(x)&INSTANCE_TYPES]
    instance=max(inst, key=lambda x: score(x,True)) if inst else {}
    return course, instance

def person_list(v):
    people=[]
    for item in as_list(v):
        if isinstance(item,dict):
            name=strip_html_tags(item.get("name","")); url=str(item.get("url") or item.get("@id") or ""); orcid=""
            for s in as_list(item.get("sameAs")):
                if isinstance(s,str) and "orcid.org" in s: orcid=s; break
            if not orcid and "orcid.org" in url: orcid=url
        else: name=strip_html_tags(item); url=orcid=""
        if name:
            p={"name":name,"url":url,"orcid":orcid}
            if p not in people: people.append(p)
    return people
def keywords(v): return [x.strip() for x in re.split(r"[,;]",v) if x.strip()] if isinstance(v,str) else text_list(v)
def audience(*vals):
    out=[]
    for v in vals:
        for item in as_list(v):
            out += text_list(item.get("audienceType") or item.get("name") or item.get("@type")) if isinstance(item,dict) else text_list(item)
    return list(dict.fromkeys(out))
def material_url(*vals):
    for v in vals:
        for item in as_list(v):
            if isinstance(item,dict) and (item.get("url") or item.get("@id")): return str(item.get("url") or item.get("@id"))
            if isinstance(item,str) and item.startswith("http"): return item
    return ""
def location(*vals):
    for v in vals:
        if isinstance(v,dict):
            parts=[]
            if v.get("name"): parts.append(str(v["name"]))
            a=v.get("address")
            if isinstance(a,dict):
                parts += [str(a[k]) for k in ["streetAddress","addressLocality","addressRegion","postalCode","addressCountry"] if a.get(k)]
            elif a: parts.append(str(a))
            res=", ".join(dict.fromkeys(strip_html_tags(p) for p in parts if p))
            if res: return res
        else:
            res=first_text(v)
            if res: return res
    return ""
def normalise(course, instance, url):
    prereq=course.get("competencyRequired") or course.get("coursePrerequisites") or course.get("educationalPrerequisites") or instance.get("competencyRequired")
    return CourseMetadata(url, name=first_text(course.get("name"),instance.get("name")), description=first_text(course.get("description"),instance.get("description")), keywords=keywords(course.get("keywords") or instance.get("keywords")), audience=audience(course.get("audience"),instance.get("audience")), educational_level=first_text(course.get("educationalLevel"),instance.get("educationalLevel")), prerequisites=text_list(prereq), teaches=text_list(course.get("teaches") or course.get("learningOutcome") or instance.get("teaches") or instance.get("learningOutcome")), license_url=first_text(course.get("license"),instance.get("license")), language=first_text(course.get("inLanguage"),instance.get("inLanguage")), start_date=first_text(instance.get("startDate"),course.get("startDate")), end_date=first_text(instance.get("endDate"),course.get("endDate")), duration=first_text(instance.get("duration"),course.get("duration"),course.get("timeRequired")), location=location(instance.get("location"),course.get("location")), materials_url=material_url(course.get("workFeatured"),course.get("hasPart"),course.get("associatedMedia"),course.get("url")), authors=person_list(course.get("author") or course.get("creator")), contributors=person_list(course.get("contributor")), trainers=person_list(instance.get("instructor") or instance.get("performer") or course.get("instructor")))
def fmt_date(v):
    if not v: return ""
    try: return dt.datetime.fromisoformat(v.replace("Z","+00:00")).strftime("%Y-%m-%d")
    except ValueError: return v
def md_link(label,url): return f"[{label}]({url})" if label and url else label or url
def people_lines(people): return [f"- {md_link(p['name'], p.get('orcid') or p.get('url') or '')}" for p in people]
def render(meta):
    l=[START_MARKER,"## Course metadata","",f"**Title:** {meta.name or 'TBC'}"]
    if meta.description: l += ["","**Description**",meta.description]
    if meta.audience: l += ["",f"**Target Audience:** {', '.join(meta.audience)}"]
    if meta.educational_level: l.append(f"**Level:** {meta.educational_level}")
    if meta.prerequisites: l += ["","**Prerequisites**"] + [f"{i}. {x}" for i,x in enumerate(meta.prerequisites,1)]
    if meta.teaches: l += ["","**Learning Outcomes:**","By the end of the course, learners will be able to:"] + [f"{i}. {x}" for i,x in enumerate(meta.teaches,1)]
    if meta.start_date or meta.end_date: l += ["", f"**Date:** {fmt_date(meta.start_date)}" + (f" to {fmt_date(meta.end_date)}" if meta.end_date and meta.end_date!=meta.start_date else "")]
    for label,val in [("Time estimation",meta.duration),("Location",meta.location),("Language",meta.language)]:
        if val: l.append(f"**{label}:** {val}")
    if meta.license_url: l.append(f"**License:** {md_link(meta.license_url,meta.license_url)}")
    if meta.keywords: l.append(f"**Keywords:** {', '.join(meta.keywords)}")
    if meta.materials_url: l += ["","**Supporting Materials:**",f"1. {md_link('Course materials',meta.materials_url)}"]
    if meta.trainers: l += ["","**Trainers:**"] + people_lines(meta.trainers)
    if meta.authors or meta.contributors:
        l += ["","## Authors and Contributors",""]
        if meta.authors: l += ["Authors",""] + people_lines(meta.authors)
        if meta.contributors: l += ["","Contributors",""] + people_lines(meta.contributors)
    l += ["",f"_Metadata source: {meta.source_url}_",END_MARKER]
    return "\n".join(l).strip()+"\n"
def replace_markers(txt, block):
    pat=re.compile(rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.S)
    return (pat.sub(block.strip(),txt), True) if pat.search(txt) else (txt,False)
def best_effort(txt, meta):
    if meta.name: txt=re.sub(r"(?m)^#\s+.+$", f"# {meta.name}", txt, count=1)
    return txt
def update_readme(txt, meta, mode):
    block=render(meta); txt2, ok=replace_markers(txt,block)
    if ok: return txt2
    if mode=="require": raise ValueError(f"README does not contain {START_MARKER} and {END_MARKER}.")
    if mode=="best-effort": return best_effort(txt,meta)
    return txt.rstrip()+"\n\n"+block
def find_first_url(txt):
    urls=re.findall(r"https?://[^\s)>'\"]+", txt)
    return next((u for u in urls if "training.vib.be/all-trainings" in u), urls[0] if urls else "")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--url"); ap.add_argument("--readme", default="README.md"); ap.add_argument("--output")
    ap.add_argument("--marker-mode", choices=["append","require","best-effort"], default="append")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--allow-body-jsonld", action="store_true")
    ap.add_argument("--debug-html", help="Save fetched HTML here and parse that copy")
    a=ap.parse_args(); readme=Path(a.readme); txt=readme.read_text(encoding="utf-8")
    url=a.url or find_first_url(txt)
    if not url: raise ValueError("No --url supplied and no URL detected in README.")
    if a.debug_html:
        h=fetch_html(url); Path(a.debug_html).write_text(h, encoding="utf-8")
        objs=parse_jsonld_blocks(jsonld_blocks_from_html(h, prefer_head=not a.allow_body_jsonld))
        if not objs: raise ValueError(f"No JSON-LD parsed. Saved fetched HTML to {a.debug_html}.")
    else:
        objs=fetch_jsonld_objects(url, prefer_head=not a.allow_body_jsonld)
    course,inst=select_course_and_instance(objs); meta=normalise(course,inst,url); out=update_readme(txt,meta,a.marker_mode)
    if a.dry_run: print(out)
    else:
        op=Path(a.output) if a.output else readme; op.write_text(out, encoding="utf-8"); print(f"Updated {op} from JSON-LD at {url}")
if __name__=="__main__":
    try: sys.exit(main())
    except Exception as e: print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)
