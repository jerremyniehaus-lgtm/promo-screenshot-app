import streamlit as st

st.set_page_config(page_title="Promo Screenshot App", layout="wide")

st.title("Promo Screenshot App")
st.caption("Paste URLs, click Run. Next step will generate screenshots and a PDF.")

default_urls = "\n".join(
    [
        "https://www.reeds.com/promotions.html",
        "https://www.helzberg.com/current-promotions.html",
        "https://www.brilliantearth.com/promo-codes-and-offers/",
    ]
)

urls_text = st.text_area(
    "Paste URLs (one per line)",
    value=default_urls,
    height=160,
)

urls = [u.strip() for u in urls_text.splitlines() if u.strip()]

col1, col2 = st.columns([1, 2])
with col1:
    run = st.button("Run")

with col2:
    st.write(f"URLs detected: {len(urls)}")

if run:
    if not urls:
        st.error("No URLs found. Paste at least one URL.")
    else:
        st.success("Button works. Next step: generate screenshots and export a PDF.")
        st.code("\n".join(urls), language="text")

