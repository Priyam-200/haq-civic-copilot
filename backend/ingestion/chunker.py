import re

# "5. Functions of the Commission" or "2A. New section"
# The text after the number must start with a capital Latin letter, a quote/bracket, or a letter of
# another script (Hindi, Tamil, Bengali...). Lower-case Latin is excluded to skip list items like "5. the".
SECTION_RE = re.compile(r"^(\d{1,3})([A-Z]?)\.\s+(?=[A-Z\"“(]|[^\x00-\x7F])")
# "Article 12." or "Rule 4:" (case-sensitive, so normal sentences do not match)
NAMED_RE = re.compile(r"^(Article|Clause|Rule|Regulation)\s+(\d+[A-Za-z]?)\s*[.:—-]")
CHAPTER_RE = re.compile(r"^CHAPTER\s+([IVXLC\d]+)\b", re.I)
OBJECTS_RE = re.compile(r"^STATEMENT OF OBJECTS AND REASONS", re.I)
SCHEDULE_RE = re.compile(r"^(THE\s+)?[A-Z]*\s*SCHEDULE\b")
WATERMARK_RE = re.compile(r"IndiaCode\s*\d{0,3}")


def _is_toc_page(text: str, prev_was_toc: bool) -> bool:
    if re.search(r"an act to|be it enacted", text, re.I):
        return False                      # real content starts here
    if re.search(r"ARRANGEMENT OF SECTIONS|^\s*CONTENTS\s*$", text, re.I | re.M):
        return True
    if prev_was_toc:
        lines = [l for l in text.split("\n") if l.strip()]
        if lines and sum(len(l.split()) for l in lines) / len(lines) < 8:
            return True
    return False


def chunk_pages(doc_id: str, pages: list, max_words: int = 300):
    chunks, current = [], []
    st = {"clause": "Preamble", "chapter": None, "page": 1,
          "last": 0, "frozen": False}
    in_toc = False

    def flush():
        text = " ".join(current).strip()
        if len(text.split()) >= 5 and not (text.isupper() and len(text.split()) < 12):       # skip tiny leftovers
            label = st["clause"]
            if st["chapter"]:
                label = f"{label} (Chapter {st['chapter']})"
            chunks.append({
                "chunk_id": f"{doc_id}_p{st['page']}_c{len(chunks) + 1}",
                "doc_id": doc_id,
                "page": st["page"],
                "clause": label,
                "text": text,
            })
        current.clear()

    for p in pages:
        in_toc = _is_toc_page(p["text"], in_toc)
        flush()                           # a chunk never crosses a page
        st["page"] = p["page"]
        if in_toc:
            continue                      # skip table of contents pages

        for raw in p["text"].split("\n"):
            line = WATERMARK_RE.sub("", raw).strip()
            if not line or re.fullmatch(r"\d{1,3}", line):
                continue                  # watermark or lone page number

            ch = CHAPTER_RE.match(line)
            if ch:
                flush()
                st["chapter"] = ch.group(1).upper()
                continue

            if OBJECTS_RE.match(line) or SCHEDULE_RE.match(line):
                flush()
                st["clause"] = line.title()[:60]
                st["chapter"] = None
                st["frozen"] = True       # no section numbers after this
                continue

            if not st["frozen"]:
                m = SECTION_RE.match(line)
                if m:
                    num, suf = int(m.group(1)), m.group(2)
                    # real section = next number (or 2A after 2)
                    if num == st["last"] + 1 or (suf and num == st["last"]):
                        flush()
                        st["clause"] = f"Section {num}{suf}"
                        st["last"] = num
                else:
                    n = NAMED_RE.match(line)
                    if n:
                        flush()
                        st["clause"] = f"{n.group(1).title()} {n.group(2)}"

            current.append(line)
            if len(" ".join(current).split()) > max_words:
                flush()
    flush()
    return chunks