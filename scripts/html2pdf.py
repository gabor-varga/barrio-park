"""Print an HTML page to PDF with WeasyPrint (the petition and its signature sheets).

    python scripts/html2pdf.py peticion/peticion.html peticion/peticion.pdf

Needs weasyprint (see README: Print the flyers).
"""
import sys

from weasyprint import HTML

if len(sys.argv) != 3:
    raise SystemExit(__doc__)
HTML(filename=sys.argv[1]).write_pdf(sys.argv[2])
print(f"wrote {sys.argv[2]}")
