import io
import pymupdf


def extract_pages(pdf_bytes: bytes):
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if doc.needs_pass:
        raise ValueError("This PDF is password protected.")
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        ocr_used = False
        if len(text) < 30:  # almost empty, probably a scanned page
            try:
                import pytesseract
                from PIL import Image
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(img).strip()
                ocr_used = True
            except Exception:
                pass  # Tesseract is not installed yet, so skip OCR
        text = text.replace("\xa0", " ").replace("\u200b", "")      # invisible spaces from some PDFs
        pages.append({"page": i + 1, "text": text, "ocr": ocr_used})
    return pages