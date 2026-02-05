import io
from datetime import datetime

import streamlit as st
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

st.set_page_config(page_title="Promo Screenshot App", layout="wide")

st.title("Promo Screenshot App")
st.caption("Step 5: Generate a simple PDF from your URL list (screenshots come next).")

default_urls = "\n".join(
    [
        "https://www.reeds.com/promotions.html",
        "https://www.helzberg.com/current-promotions.html",
        "https://www.brilliantearth.com/promo-codes-and-offers/",
    ]
)

urls_text = st.text_area("Paste URLs (one per line)", value=default_urls, height=160)
urls = [u.strip() for u in urls_text.splitlines() if u.strip()]

run = st.button("Build PDF")

def build_pdf(urls_list):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    for i, url in enumerate(urls_list, start=1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(48, height - 72, f"URL {i}")
        c.setFont("Helvetica", 11)
        c.drawString(48, height - 96, url)
        c.setFont("Helvetica", 9)
        c.drawString(48, 48, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        c.showPage()

    c.save()
    buf.seek(0)
    return buf.getvalue()

if run:
    if not urls:
        st.error("No URLs found. Paste at least one URL.")
    else:
        pdf_bytes = build_pdf(urls)
        st.success("PDF built.")
        st.download_button(
            label="Download PDF",
            data=pdf_bytes,
            file_name=f"urls_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.pdf",
            mime="application/pdf",
        )
