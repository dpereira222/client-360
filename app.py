"""
FalconX Client 360 — cross-product client intelligence and sales CRM.

Router only. Top nav = one client page + three book-wide desk pages:
  Client 360     — one relationship, every product (views/client.py)
  Expiries · Idea targeting · Daily color — the desk's tools (views/*.py)
The selected client lives in st.session_state["client"] and follows you across
pages. All data comes through data.py (mock today, Databricks once wired).
"""
import streamlit as st

import theme

st.set_page_config(page_title="FalconX Client 360", page_icon=":material/hub:",
                   layout="wide")
theme.install_template()
st.markdown(theme.PAGE_CSS, unsafe_allow_html=True)

pg = st.navigation([
    st.Page("views/client.py", title="Client 360", icon=":material/person_search:",
            default=True),
    st.Page("views/expiries.py", title="Expiries", icon=":material/event:",
            url_path="expiries"),
    st.Page("views/targeting.py", title="Idea targeting", icon=":material/target:",
            url_path="targeting"),
    st.Page("views/daily_color.py", title="Daily color", icon=":material/schedule:",
            url_path="daily_color"),
], position="top")
pg.run()
