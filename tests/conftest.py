"""Aisla cada test del estado real del usuario.

Sin esto, un test que llama a `daemon.run()` dispara `setup_logging()` sobre
el LOG_PATH real y, desde ahi, el resto de la suite escribe lineas falsas
(`https://x/?nada=1`, grants mockeados) en ~/.local/state/club-autotalplay/
watchdog.log — el mismo log que se usa como evidencia de cortes reales.
"""
from __future__ import annotations

import logging
import os

import pytest

from club_autotalplay import core


@pytest.fixture(autouse=True)
def _rutas_aisladas(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(core, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(core, "STATE_PATH", os.path.join(state_dir, "state.json"))
    monkeypatch.setattr(core, "LOG_PATH", os.path.join(state_dir, "watchdog.log"))
    logger = logging.getLogger("club_autotalplay")
    previos = list(logger.handlers)
    yield
    # setup_logging es idempotente por handlers: quitar los que agrego el
    # test para que el siguiente no herede un handler a otra ruta.
    for h in list(logger.handlers):
        if h not in previos:
            logger.removeHandler(h)
            h.close()
