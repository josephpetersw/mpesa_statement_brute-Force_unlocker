import fitz  # PyMuPDF
import pdfplumber
import io
import logging

logger = logging.getLogger(__name__)

# Dynamic imports for OCR libraries to allow graceful degradation
EASYOCR_AVAILABLE = False
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    pass

TESSERACT_AVAILABLE = False
try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    pass

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extracts plain text from the PDF using PyMuPDF (fitz).
    """
    text_content = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            text_content.append(page.get_text())
        doc.close()
    except Exception as e:
        logger.error(f"Error extracting text with PyMuPDF: {e}")
    
    return "\n".join(text_content)

def extract_tables_from_pdf(pdf_bytes: bytes) -> list:
    """
    Extracts tabular data from the PDF using pdfplumber.
    Returns a list of tables, where each table is a list of rows (lists of strings).
    """
    tables = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                extracted = page.extract_tables()
                if extracted:
                    tables.extend(extracted)
    except Exception as e:
        logger.error(f"Error extracting tables with pdfplumber: {e}")
    return tables

def ocr_fallback_extract(pdf_bytes: bytes, engine: str = "auto") -> str:
    """
    Performs OCR extraction as a fallback for scanned PDFs.
    Supports EasyOCR and Tesseract.
    """
    ocr_text = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num, page in enumerate(doc):
            # Render page to image/pixmap
            pix = page.get_pixmap(dpi=150)
            img_data = pix.tobytes("png")
            
            extracted_page_text = ""
            
            if (engine == "easyocr" or engine == "auto") and EASYOCR_AVAILABLE:
                try:
                    reader = easyocr.Reader(['en'])
                    results = reader.readtext(img_data)
                    extracted_page_text = " ".join([res[1] for res in results])
                except Exception as e:
                    logger.warning(f"EasyOCR page {page_num} failed: {e}")
            
            if not extracted_page_text and (engine == "tesseract" or engine == "auto") and TESSERACT_AVAILABLE:
                try:
                    img = Image.open(io.BytesIO(img_data))
                    extracted_page_text = pytesseract.image_to_string(img)
                except Exception as e:
                    logger.warning(f"Tesseract page {page_num} failed: {e}")
                    
            if not extracted_page_text:
                extracted_page_text = f"[OCR Failed or unavailable for page {page_num + 1}]"
                
            ocr_text.append(extracted_page_text)
        doc.close()
    except Exception as e:
        logger.error(f"OCR overall fallback failed: {e}")
        return f"OCR Error: {str(e)}"
        
    return "\n".join(ocr_text)

def get_ocr_status():
    """Returns library availability details for easy OCR selection in the UI"""
    return {
        "EasyOCR": EASYOCR_AVAILABLE,
        "Tesseract": TESSERACT_AVAILABLE
    }
