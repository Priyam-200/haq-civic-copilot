import sys
from ingestion.pdf import extract_pages
from ingestion.chunker import chunk_pages

path = sys.argv[1]
with open(path, "rb") as f:
    pages = extract_pages(f.read())

chunks = chunk_pages("test", pages)
print(len(chunks), "chunks\n")
for c in chunks:
    print(f"p{c['page']:>2} | {c['clause']:<32} | {c['text'][:70]}")