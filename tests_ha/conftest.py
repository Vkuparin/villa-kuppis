"""Home Assistant test harness for the cottage integration (card H2.2).

Runs in a separate environment with pytest-homeassistant-custom-component;
the main unittest suite never imports Home Assistant.
"""

from pathlib import Path

import pytest

OURS = str(Path(__file__).resolve().parent.parent / "custom_components")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    # The test hass puts its own config dir's custom_components first;
    # add this repository's so the loader finds villa_kuppis_cottage.
    import custom_components
    if OURS not in custom_components.__path__:
        custom_components.__path__.append(OURS)
    yield
