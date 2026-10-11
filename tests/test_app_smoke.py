"""Smoke tests for app.py and its tab modules.

Runs the app headlessly via `streamlit.testing.v1.AppTest` (no live server,
no network - fixtures mode / fixture data only) and confirms importing the
tab modules never touches a live Streamlit runtime (no `st.*` calls outside
of functions).

`AppTest.from_function` re-executes a function's *source*, so each script
function below is self-contained (local imports, no closures over
module-level fixtures) rather than relying on captured outer variables.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize(
    "module_name",
    [
        "swedish_macro_dashboard.tabs.components",
        "swedish_macro_dashboard.tabs.macro_overview",
        "swedish_macro_dashboard.tabs.stock_market",
        "swedish_macro_dashboard.tabs.macro_market",
    ],
)
def test_tab_module_imports_without_streamlit_runtime(module_name: str) -> None:
    """Importing a tab module must not execute any Streamlit runtime calls -
    it should just define functions/constants."""
    module = importlib.import_module(module_name)
    assert hasattr(module, "render") or module_name.endswith("components")


def test_app_boots_in_fixtures_mode() -> None:
    """The full app, run headlessly with fixtures mode forced on, renders
    all three top-level tabs without raising and without any network access.

    `AppTest.tabs` walks the *entire* render tree, so on the full app it also
    picks up the 9 sector sub-tabs nested inside "Stock Market" (see
    test_stock_market_renders_nine_subtabs_on_fixture, which covers those in
    isolation) - `len(at.tabs) == 12`, not 3. To check only the top-level
    `st.tabs(...)` call from app.py's main(), look at the direct children of
    the top-level tab_container block instead of the flattened `at.tabs` list.
    """
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"))
    at.session_state["fixtures_mode"] = True
    at.run()

    assert not at.exception

    top_level_tab_container = next(
        child for child in at.main.children.values() if child.type == "tab_container"
    )
    top_level_tabs = list(top_level_tab_container.children.values())
    assert [tab.label for tab in top_level_tabs] == [
        "Macro Overview",
        "Stock Market",
        "Macro × Market",
    ]


def _macro_overview_script() -> None:
    from swedish_macro_dashboard.data.fixtures import fixture_macro_df
    from swedish_macro_dashboard.tabs import macro_overview

    macro_overview.render(fixture_macro_df(), False)


def test_macro_overview_renders_on_fixture() -> None:
    at = AppTest.from_function(_macro_overview_script).run()
    assert not at.exception


def _macro_overview_empty_script() -> None:
    import pandas as pd

    from swedish_macro_dashboard.data.schema import COMMON_COLUMNS
    from swedish_macro_dashboard.tabs import macro_overview

    macro_overview.render(pd.DataFrame(columns=COMMON_COLUMNS), False)


def test_macro_overview_renders_on_empty_frame() -> None:
    """An unavailable Riksbank source must render a warning, never a traceback."""
    at = AppTest.from_function(_macro_overview_empty_script).run()
    assert not at.exception


def _stock_market_script() -> None:
    from swedish_macro_dashboard.data.fixtures import fixture_market_df
    from swedish_macro_dashboard.tabs import stock_market

    stock_market.render(fixture_market_df(), True)


def test_stock_market_renders_nine_subtabs_on_fixture() -> None:
    at = AppTest.from_function(_stock_market_script).run()
    assert not at.exception
    assert len(at.tabs) == 9


def _macro_market_script() -> None:
    from swedish_macro_dashboard.data.fixtures import (
        fixture_macro_df,
        fixture_market_df,
    )
    from swedish_macro_dashboard.tabs import macro_market

    macro_market.render(fixture_macro_df(), fixture_market_df())


def test_macro_market_renders_on_fixtures() -> None:
    at = AppTest.from_function(_macro_market_script).run()
    assert not at.exception
