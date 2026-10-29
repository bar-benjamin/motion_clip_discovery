"""Disposable synthetic catalog shared by dashboard entry points."""

from pathlib import Path
from tempfile import mkdtemp

import streamlit as st

from vl_product.source import Config, SyntheticSource
from vl_product.warehouse import ingest


@st.cache_resource(show_spinner="Preparing the synthetic VL catalog...")
def demo_catalog() -> Path:
    """Publish one completed snapshot shared by sessions in this server process."""
    database = Path(mkdtemp(prefix="vl-demo-")) / "catalog.duckdb"
    config = Config()
    ingest(database, SyntheticSource(config))
    return database
