from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import html
import json
from pathlib import Path
import re
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tg_e2e.config import ConfigError, build_config, validate_no_dangerous_flags  # noqa: E402
from tg_e2e.env_file import load_env_file  # noqa: E402
from tg_e2e.telethon_client import make_telethon_client  # noqa: E402


DEFAULT_CHATS = (
    "chat_infographics",
    "designers_wb_ozon",
    "dizainer_wb",
    "wbnahodkychat",
    "marketplaces_chat",
    "MP_partner",
    "sellery_ozon",
    "wildberries_service",
    "xb_prosmm_chat",
    "neyroseti_chat",
)

SEARCH_TERMS = (
    "\u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0430",
    "\u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438 \u0442\u043e\u0432\u0430\u0440\u0430",
    "\u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444\u0438\u043a\u0430",
    "\u0444\u043e\u0442\u043e \u0442\u043e\u0432\u0430\u0440\u0430",
    "\u0434\u0438\u0437\u0430\u0439\u043d",
    "\u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444\u0438\u043a\u0438",
    "\u043e\u0444\u043e\u0440\u043c\u0438\u0442\u044c",
    "\u043e\u0444\u043e\u0440\u043c\u043b\u0435\u043d\u0438\u0435",
    "\u0438\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c",
    "\u0444\u0440\u0438\u043b\u0430\u043d\u0441\u0435\u0440",
    "\u0441\u043f\u0435\u0446\u0438\u0430\u043b\u0438\u0441\u0442",
    "\u043f\u043e\u0434 \u043a\u043b\u044e\u0447",
    "\u0432\u0438\u0434\u0435\u043e \u0442\u043e\u0432\u0430\u0440\u0430",
    "\u0432\u0438\u0434\u0435\u043e \u0434\u043b\u044f \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438",
    "\u0440\u0438\u043b\u0441",
    "reels",
    "ugc",
    "\u043d\u0435\u0439\u0440\u043e\u0441\u0435\u0442\u044c",
    "\u043d\u0435\u0439\u0440\u043e\u0441\u0435\u0442\u0438",
    "\u043e\u0436\u0438\u0432\u0438\u0442\u044c \u0444\u043e\u0442\u043e",
    "veo",
    "kling",
)

DEMAND_HINTS = (
    "\u043f\u043e\u0434\u0441\u043a\u0430\u0436",
    "\u043f\u043e\u0441\u043e\u0432\u0435\u0442",
    "\u043a\u0442\u043e \u0434\u0435\u043b\u0430\u0435\u0442",
    "\u043a\u0442\u043e \u043c\u043e\u0436\u0435\u0442",
    "\u0435\u0441\u0442\u044c \u043a\u0442\u043e",
    "\u0435\u0441\u0442\u044c \u0441\u043f\u0435\u0446",
    "\u0438\u0449\u0443",
    "\u0438\u0449\u0435\u043c",
    "\u0438\u0449\u0435\u0442",
    "\u043d\u0443\u0436\u0435\u043d",
    "\u043d\u0443\u0436\u043d\u0430",
    "\u043d\u0443\u0436\u043d\u043e",
    "\u043d\u0430\u0434\u043e",
    "\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f",
    "\u0433\u0434\u0435 \u043c\u043e\u0436\u043d\u043e",
    "\u043a\u0430\u043a \u0441\u0434\u0435\u043b\u0430\u0442\u044c",
    "\u043a\u0430\u043a\u043e\u0439 \u0441\u0435\u0440\u0432\u0438\u0441",
    "\u043a\u0430\u043a\u0430\u044f \u043d\u0435\u0439\u0440\u043e",
    "\u043d\u0435 \u043c\u043e\u0433\u0443",
    "\u043d\u0435 \u043f\u043e\u043b\u0443\u0447\u0430\u0435\u0442\u0441\u044f",
    "\u043f\u043e\u043c\u043e\u0447\u044c",
    "\u043f\u043e\u043c\u043e\u0433\u0438\u0442\u0435",
    "\u0437\u0430\u043a\u0430\u0437\u0430\u0442\u044c",
    "\u0434\u043e\u0433\u043e\u0432\u043e\u0440\u0438\u043c\u0441\u044f",
)

HARD_REQUEST_HINTS = (
    "\u043f\u043e\u0434\u0441\u043a\u0430\u0436",
    "\u043f\u043e\u0441\u043e\u0432\u0435\u0442",
    "\u043a\u0442\u043e \u0434\u0435\u043b\u0430\u0435\u0442",
    "\u043a\u0442\u043e \u043c\u043e\u0436\u0435\u0442",
    "\u043a\u0442\u043e \u043f\u043e\u0434\u0441\u043a\u0430\u0436",
    "\u0435\u0441\u0442\u044c \u043a\u0442\u043e",
    "\u0435\u0441\u0442\u044c \u0441\u043f\u0435\u0446",
    "\u0438\u0449\u0443",
    "\u0438\u0449\u0435\u043c",
    "\u0438\u0449\u0435\u0442",
    "\u043d\u0443\u0436\u0435\u043d",
    "\u043d\u0443\u0436\u043d\u0430",
    "\u043d\u0443\u0436\u043d\u043e",
    "\u043d\u0430\u0434\u043e",
    "\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f",
    "\u0433\u0434\u0435 \u043c\u043e\u0436\u043d\u043e",
    "\u0433\u0434\u0435 \u0437\u0430\u043a\u0430\u0437",
    "\u043a\u0430\u043a \u0441\u0434\u0435\u043b\u0430\u0442\u044c",
    "\u043a\u0430\u043a\u043e\u0439 \u0441\u0435\u0440\u0432\u0438\u0441",
    "\u043a\u0430\u043a\u0430\u044f \u043d\u0435\u0439\u0440\u043e",
    "\u043d\u0435 \u043c\u043e\u0433\u0443",
    "\u043d\u0435 \u043f\u043e\u043b\u0443\u0447\u0430\u0435\u0442\u0441\u044f",
    "\u043f\u043e\u043c\u043e\u0433\u0438\u0442\u0435",
)

# Done-for-you demand: the author wants a person/team to do the work for them,
# not a tool or a tip. These are the highest-value leads for Photozhab.
SERVICE_FIT_HINTS = (
    "\u0438\u0449\u0443 \u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u0438\u0449\u0443 \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u0438\u0449\u0443 \u0438\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b",
    "\u0438\u0449\u0443 \u043f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a",
    "\u0438\u0449\u0443 \u0441\u043f\u0435\u0446\u0438\u0430\u043b\u0438\u0441\u0442",
    "\u0438\u0449\u0443 \u0447\u0435\u043b\u043e\u0432\u0435\u043a",
    "\u0438\u0449\u0443 \u0444\u0440\u0438\u043b\u0430\u043d\u0441\u0435\u0440",
    "\u0438\u0449\u0443 \u0442\u043e\u0433\u043e \u043a\u0442\u043e",
    "\u0438\u0449\u0443 \u0442\u043e\u0433\u043e, \u043a\u0442\u043e",
    "\u043d\u0443\u0436\u0435\u043d \u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u043d\u0443\u0436\u0435\u043d \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u043d\u0443\u0436\u0435\u043d \u0438\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b",
    "\u043d\u0443\u0436\u0435\u043d \u043f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a",
    "\u043d\u0443\u0436\u0435\u043d \u0441\u043f\u0435\u0446\u0438\u0430\u043b\u0438\u0441\u0442",
    "\u043d\u0443\u0436\u0435\u043d \u0447\u0435\u043b\u043e\u0432\u0435\u043a",
    "\u043d\u0443\u0436\u0435\u043d \u0444\u0440\u0438\u043b\u0430\u043d\u0441\u0435\u0440",
    "\u043d\u0443\u0436\u043d\u0430 \u043f\u043e\u043c\u043e\u0449\u044c \u0441 \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u043d\u0443\u0436\u0435\u043d \u0434\u0438\u0437\u0430\u0439\u043d \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u043d\u0443\u0436\u043d\u0430 \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u0438\u0449\u0435\u0442\u0441\u044f \u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u0442\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u043f\u043e\u0441\u043e\u0432\u0435\u0442\u0443\u0439\u0442\u0435 \u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u043f\u043e\u0441\u043e\u0432\u0435\u0442\u0443\u0439\u0442\u0435 \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u043f\u043e\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0443\u0439\u0442\u0435 \u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u043a\u0442\u043e \u0434\u0435\u043b\u0430\u0435\u0442 \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u043a\u0442\u043e \u0434\u0435\u043b\u0430\u0435\u0442 \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u043a\u0442\u043e \u043e\u0444\u043e\u0440\u043c\u043b\u044f\u0435\u0442 \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u043a\u0442\u043e \u043c\u043e\u0436\u0435\u0442 \u0441\u0434\u0435\u043b\u0430\u0442\u044c \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u043a\u0442\u043e \u043c\u043e\u0436\u0435\u0442 \u0441\u0434\u0435\u043b\u0430\u0442\u044c \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u0434\u0430\u0439\u0442\u0435 \u043a\u043e\u043d\u0442\u0430\u043a\u0442",
    "\u0441\u043a\u0438\u043d\u044c\u0442\u0435 \u043a\u043e\u043d\u0442\u0430\u043a\u0442",
    "\u0441\u043a\u0438\u043d\u044c\u0442\u0435 \u043a\u043e\u043d\u0442\u0430\u043a\u0442\u044b",
    "\u0441\u0434\u0435\u043b\u0430\u0442\u044c \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u0441\u0434\u0435\u043b\u0430\u0442\u044c \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u043e\u0444\u043e\u0440\u043c\u0438\u0442\u044c \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u043e\u0444\u043e\u0440\u043c\u043b\u0435\u043d\u0438\u0435 \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u043f\u043e\u0434 \u043a\u043b\u044e\u0447",
    "\u0434\u0435\u043b\u0435\u0433\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
    "\u043d\u0430 \u043f\u043e\u0441\u0442\u043e\u044f\u043d\u043a\u0443",
    "\u0432 \u043a\u043e\u043c\u0430\u043d\u0434\u0443",
    "\u043d\u0430 \u0430\u0443\u0442\u0441\u043e\u0440\u0441",
    "\u0441\u0434\u0435\u043b\u0430\u0439\u0442\u0435 \u043c\u043d\u0435",
    "\u0441\u0434\u0435\u043b\u0430\u0439\u0442\u0435 \u0437\u0430 \u043c\u0435\u043d\u044f",
    "\u043f\u043e\u043c\u043e\u0433\u0438\u0442\u0435 \u0441 \u043a\u0430\u0440\u0442\u043e\u0447",
    "\u043f\u043e\u043c\u043e\u0433\u0438\u0442\u0435 \u0441 \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u0445\u043e\u0447\u0443 \u0437\u0430\u043a\u0430\u0437\u0430\u0442\u044c",
    "\u0433\u043e\u0442\u043e\u0432 \u043e\u043f\u043b\u0430\u0442\u0438\u0442\u044c",
    "\u0431\u044e\u0434\u0436\u0435\u0442 \u0435\u0441\u0442\u044c",
    "\u0437\u0430 \u043e\u043f\u043b\u0430\u0442\u0443",
    "\u043f\u043b\u0430\u0442\u043d\u043e \u0438\u0449\u0443",
)

# Author offering work / looking for clients \u2014 exclude even when they say
# "\u0438\u0449\u0443"/"\u043d\u0443\u0436\u0435\u043d" (e.g. "\u0438\u0449\u0443 \u0437\u0430\u043a\u0430\u0437\u044b", "\u0438\u0449\u0443 \u043a\u043b\u0438\u0435\u043d\u0442\u043e\u0432", "\u0438\u0449\u0443 \u0440\u0430\u0431\u043e\u0442\u0443").
SELLER_SEEKING_HINTS = (
    "\u0438\u0449\u0443 \u0437\u0430\u043a\u0430\u0437",
    "\u0438\u0449\u0443 \u043a\u043b\u0438\u0435\u043d\u0442",
    "\u0438\u0449\u0443 \u0440\u0430\u0431\u043e\u0442\u0443",
    "\u0438\u0449\u0443 \u043f\u043e\u0434\u0440\u0430\u0431\u043e\u0442",
    "\u0438\u0449\u0443 \u043f\u0440\u043e\u0435\u043a\u0442",
    "\u0432\u043e\u0437\u044c\u043c\u0443 \u0437\u0430\u043a\u0430\u0437",
    "\u0431\u0435\u0440\u0443 \u0437\u0430\u043a\u0430\u0437",
    "\u043e\u0442\u043a\u0440\u044b\u0442 \u0434\u043b\u044f \u0437\u0430\u043a\u0430\u0437",
    "\u043e\u0442\u043a\u0440\u044b\u0442\u0430 \u0434\u043b\u044f \u0437\u0430\u043a\u0430\u0437",
    "\u0441\u0432\u043e\u0431\u043e\u0434\u0435\u043d \u0434\u043b\u044f \u0437\u0430\u043a\u0430\u0437",
    "\u0441\u0432\u043e\u0431\u043e\u0434\u043d\u0430 \u0434\u043b\u044f \u0437\u0430\u043a\u0430\u0437",
    "\u0433\u043e\u0442\u043e\u0432 \u0432\u0437\u044f\u0442\u044c",
    "\u0433\u043e\u0442\u043e\u0432\u0430 \u0432\u0437\u044f\u0442\u044c",
    "\u0438\u0449\u0443 \u0432\u0430\u043a\u0430\u043d\u0441\u0438\u044e",
    "\u0438\u0449\u0443 \u0443\u0434\u0430\u043b\u0435\u043d\u043a",
    "\u043c\u043e\u0435 \u043f\u043e\u0440\u0442\u0444\u043e\u043b\u0438\u043e",
    "\u043c\u043e\u0451 \u043f\u043e\u0440\u0442\u0444\u043e\u043b\u0438\u043e",
    "\u043c\u043e\u0438 \u0440\u0430\u0431\u043e\u0442\u044b",
    "\u0440\u0435\u0437\u044e\u043c\u0435",
)

TOPIC_HINTS = (
    "\u043a\u0430\u0440\u0442\u043e\u0447",
    "\u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
    "\u0434\u0438\u0437\u0430\u0439\u043d",
    "\u0444\u043e\u0442\u043e",
    "\u0444\u043e\u043d",
    "\u0441\u043b\u0430\u0439\u0434",
    "\u0442\u043e\u0432\u0430\u0440",
    "\u0432\u0438\u0434\u0435\u043e",
    "\u0440\u0438\u043b\u0441",
    "reels",
    "ugc",
    "\u043e\u0431\u043b\u043e\u0436",
    "\u043e\u0436\u0438\u0432",
    "\u0430\u043d\u0438\u043c\u0430\u0446",
    "\u043d\u0435\u0439\u0440\u043e",
    "\u0438\u0438",
    "ai",
    "veo",
    "kling",
)

SUPPLY_HINTS = (
    "\u0441\u043e\u0437\u0434\u0430\u044e",
    "\u0441\u043e\u0437\u0434\u0430\u0435\u043c",
    "\u0441\u0434\u0435\u043b\u0430\u044e",
    "\u043f\u043e\u043c\u043e\u0433\u0443",
    "\u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u044e",
    "\u0443\u0441\u043b\u0443\u0433",
    "\u043f\u043e\u0440\u0442\u0444\u043e\u043b\u0438\u043e",
    "\u043f\u0440\u0430\u0439\u0441",
    "\u043f\u0438\u0448\u0438\u0442\u0435",
    "\u0441\u043a\u0438\u043d\u0443",
    "\u0437\u0430\u043f\u0443\u0441\u0442\u0438\u043b\u0430\u0441\u044c",
    "\u0431\u043e\u0442",
    "\u0441\u0435\u0440\u0432\u0438\u0441",
)

SUPPLY_ONLY_HINTS = (
    "\u0441\u043e\u0437\u0434\u0430\u044e",
    "\u0441\u043e\u0437\u0434\u0430\u0435\u043c",
    "\u044f \u0441\u043e\u0437\u0434\u0430\u044e",
    "\u0441\u043e\u0437\u0434\u0430\u043c",
    "\u0434\u0435\u043b\u0430\u044e",
    "\u044f \u0434\u0435\u043b\u0430\u044e",
    "\u0441\u0434\u0435\u043b\u0430\u044e",
    "\u0440\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u0430\u044e",
    "\u043f\u043e\u043c\u043e\u0433\u0443",
    "\u043f\u0440\u0435\u0434\u043b\u0430\u0433\u0430\u044e",
    "\u0443\u0441\u043b\u0443\u0433",
    "\u043f\u0440\u0430\u0439\u0441",
    "\u043c\u043e\u0438 \u0440\u0430\u0431\u043e\u0442\u044b",
    "\u043c\u043e\u0435 \u043f\u043e\u0440\u0442\u0444\u043e\u043b\u0438\u043e",
    "\u044f \u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u0440\u0430\u0431\u043e\u0442\u0430\u044e \u0441",
    "\u0441\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c",
    "\u0441\u043a\u0438\u0434\u043a",
    "\u0441\u0440\u043e\u043a \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f",
    "\u0434\u043b\u044f \u043e\u0444\u043e\u0440\u043c\u043b\u0435\u043d\u0438\u044f \u0437\u0430\u043a\u0430\u0437\u0430",
    "\u043f\u043e\u0434 \u0437\u0430\u043a\u0430\u0437",
    "\u0437\u0430\u043a\u0430\u0437\u0430\u0442\u044c",
    "\u043e\u0431\u0440\u0430\u0449\u0430\u0439\u0442\u0435\u0441\u044c",
    "\u043a \u0432\u0430\u0448\u0438\u043c \u0443\u0441\u043b\u0443\u0433\u0430\u043c",
    "\u043f\u0438\u0448\u0438\u0442\u0435 \u0432 \u043b\u0441 \u0437\u0430",
    "\u0432\u0435\u0431-\u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u0432\u0435\u0431\u0434\u0438\u0437\u0430\u0439\u043d\u0435\u0440",
    "\u0441\u0434\u0435\u043b\u0430\u044e \u0434\u043b\u044f \u0432\u0430\u0441",
    "\u043f\u043e\u043c\u043e\u0433\u0443 \u0432\u0430\u043c",
    "\u043f\u043e\u043c\u043e\u0433\u0443 \u0432\u0430\u0448\u0435\u043c\u0443",
    "\u043a\u043e\u043c\u0443 \u043d\u0443\u0436\u043d",
    "\u0432\u044b \u043f\u043e \u0430\u0434\u0440\u0435\u0441\u0443",
    "\u043f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u043e \u0437\u0430\u043d\u0438\u043c\u0430\u044e\u0441\u044c",
    "\u043c\u044b \u0441\u0434\u0435\u043b\u0430\u043b\u0438",
    "ai-\u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442",
    "\u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442, \u043a\u043e\u0442\u043e\u0440\u044b\u0439 \u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u0435\u0442",
    "\u043c\u0438\u043d\u0438-\u043a\u0440\u0435\u0430\u0442\u0438\u0432\u043d\u0430\u044f \u0441\u0442\u0443\u0434\u0438\u044f",
    "\u0443 \u0432\u0430\u0441 \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439 \u0442\u043e\u0432\u0430\u0440",
    "\u043a\u043b\u0438\u0435\u043d\u0442\u044b \u043a\u043b\u0438\u043a\u0430\u044e\u0442",
    "\u043f\u043e\u043a\u0443\u043f\u0430\u0442\u0435\u043b\u044c \u0442\u0440\u0430\u0442\u0438\u0442",
    "\u0435\u0441\u043b\u0438 \u0432\u0430\u0448\u0435",
    "sellercardbot",
    "\u0447\u0442\u043e \u0435\u0441\u043b\u0438 \u0435\u0441\u0442\u044c \u0441\u043f\u043e\u0441\u043e\u0431 \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
    "\u0441\u043e\u0437\u0434\u0430\u0432\u0430\u0442\u044c \u043f\u0440\u043e\u0434\u0430\u044e\u0449\u0438\u0435 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438",
    "\u0432\u043e\u0437\u044c\u043c\u0443 \u0431\u0435\u0441\u043f\u043b\u0430\u0442\u043d\u043e",
    "\u043d\u0443\u0436\u043d\u043e \u0434\u043b\u044f \u043a\u0435\u0439\u0441\u0430",
    "\u043f\u0440\u043e\u0444\u0435\u0441\u0441\u0438\u043e\u043d\u0430\u043b\u044c\u043d\u043e \u0432\u0435\u0434\u0443 \u043b\u043a",
)

NOISE_HINTS = (
    "\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0442",
    "\u043c\u0430\u0440\u043a\u0438\u0440\u043e\u0432",
    "\u0431\u0430\u0440\u043a\u043e\u0434",
    "\u043f\u043e\u0441\u0442\u0430\u0432\u043a",
    "\u0444\u0443\u043b\u0444\u0438\u043b\u043c",
    "\u043b\u043e\u0433\u0438\u0441\u0442",
    "\u0432\u044b\u043a\u0443\u043f",
    "\u0431\u0430\u0439\u0435\u0440",
    "\u043d\u0434\u0441",
    "\u0448\u0442\u0440\u0438\u0445",
    "\u043f\u043e\u0438\u0441\u043a \u043a\u043b\u0438\u0435\u043d\u0442",
    "\u0438\u0449\u0443 \u0440\u0430\u0431\u043e\u0442",
    "\u0438\u0449\u0443 #smm",
    "\u0440\u0435\u0437\u044e\u043c\u0435",
    "\u043a\u043e\u043d\u0442\u0435\u043d\u0442-\u043f\u043b\u0430\u043d",
    "\u0432\u0435\u0441\u0442\u0438 \u0441\u043e\u0446\u0441\u0435\u0442\u0438",
    "\u043e\u0437\u043e\u043d \u043d\u0435 \u0434\u0430\u0451\u0442 \u0443\u043f\u043e\u043c\u0438\u043d\u0430\u0442\u044c",
    "\u0443\u043f\u043e\u043c\u0438\u043d\u0430\u0442\u044c \u0434\u0440\u0443\u0433\u0438\u0435 \u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441\u044b",
    "\u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430 \u043d\u0435 \u043e\u0442\u0432\u0435\u0447\u0430\u0435\u0442",
    "\u043e\u0431\u044a\u0435\u0434\u0438\u043d\u0435\u043d\u0438\u044f \u043a\u0430\u0440\u0442\u043e\u0447\u0435\u043a",
    "\u0441\u043a\u043b\u0435\u0439\u043a",
    "\u0437\u0430\u043c\u0443\u0442\u043d\u0435\u043d",
    "\u0432\u043e\u0440\u0443\u044e\u0442 \u043d\u0430\u0448\u0438 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438",
    "\u043e\u0431\u044b\u0447\u043d\u043e, \u0447\u0442\u043e\u0431\u044b \u0441\u043d\u0438\u0437\u0438\u0442\u044c \u0440\u0438\u0441\u043a\u0438",
    "seo-\u0434\u043e\u0431\u0430\u0432\u043a",
    "\u0440\u0438\u043b\u0441\u043c\u0435\u0439\u043a",
    "\u043c\u043e\u043d\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c reels",
    "\u043e\u0442\u043a\u043b\u0438\u043a \u0432 \u043b\u0441",
    "ozon \u0447\u0430\u0441\u0442\u043e \u043d\u0435 \u043f\u0440\u043e\u043f\u0443\u0441\u043a\u0430\u0435\u0442",
    "\u0442\u0440\u0435\u0431\u0443\u044e\u0442 \u0443\u0431\u0440\u0430\u0442\u044c",
    "\u043d\u0435\u0439\u0442\u0440\u0430\u043b\u044c\u043d\u044b\u0435 \u0444\u043e\u0440\u043c\u0443\u043b\u0438\u0440\u043e\u0432\u043a\u0438",
    "\u0434\u0435\u0432\u0443\u0448\u0435\u043a",
    "\u043c\u0430\u043c \u0432 \u0434\u0435\u043a\u0440\u0435\u0442\u0435",
    "\u0440\u0430\u0431\u043e\u0442\u044b \u0441 \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u0430",
    "\u043e\u0441\u0442\u0430\u0432\u0438\u0442\u044c \u043e\u0442\u0437\u044b\u0432",
    "\u043f\u0440\u043e\u0439\u0442\u0438 \u043e\u043f\u0440\u043e\u0441",
    "\u0432\u0430\u043a\u0430\u043d\u0441\u0438",
)

CATEGORY_HINTS = {
    "product_card": (
        "\u043a\u0430\u0440\u0442\u043e\u0447",
        "\u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444",
        "\u0441\u043b\u0430\u0439\u0434",
        "\u0434\u0438\u0437\u0430\u0439\u043d",
    ),
    "product_photo": (
        "\u0444\u043e\u0442\u043e \u0442\u043e\u0432\u0430\u0440",
        "\u0431\u0435\u043b\u044b\u0439 \u0444\u043e\u043d",
        "\u0444\u043e\u043d",
        "\u043d\u0435\u0439\u0440\u043e\u0444\u043e\u0442\u043e",
    ),
    "short_video": (
        "\u0432\u0438\u0434\u0435\u043e",
        "\u0440\u0438\u043b\u0441",
        "reels",
        "ugc",
        "\u043e\u0431\u043b\u043e\u0436",
    ),
    "photo_animation": (
        "\u043e\u0436\u0438\u0432",
        "\u0430\u043d\u0438\u043c\u0430\u0446",
        "veo",
        "kling",
    ),
    "ai_tool": (
        "\u043d\u0435\u0439\u0440\u043e",
        "\u0438\u0438",
        "ai",
        "gpt",
    ),
}


@dataclass
class Sample:
    date: str
    category: str
    keyword: str
    snippet: str
    link: str | None = None


@dataclass
class LeadCandidate:
    lead_id: str
    date: str
    chat: str
    chat_title: str
    message_id: int
    message_link: str | None
    priority: str
    score: int
    lead_type: str
    pain_context: str
    categories: list[str]
    keyword: str
    snippet: str
    repeat_count: int = 1
    evidence_links: list[str] = field(default_factory=list)


@dataclass
class ChatStats:
    chat: str
    link: str
    title: str | None = None
    participants: int | None = None
    about: str | None = None
    scanned_messages: int = 0
    keyword_matches: int = 0
    direct_pain_messages: int = 0
    unique_pain_authors: int = 0
    supply_messages: int = 0
    noise_messages: int = 0
    category_counts: dict[str, int] = field(default_factory=dict)
    samples: list[Sample] = field(default_factory=list)
    leads: list[LeadCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only Telegram pain scanner for Photozhab lead research.",
        allow_abbrev=False,
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output-dir", default="lead_scan_runs")
    parser.add_argument("--session-file")
    parser.add_argument("--tg-api-id")
    parser.add_argument("--tg-api-hash")
    parser.add_argument("--tg-phone")
    parser.add_argument("--tg-proxy-url")
    parser.add_argument("--approve-external-action", action="store_true")
    parser.add_argument("--chat", action="append", dest="chats")
    parser.add_argument(
        "--chats-file",
        help="JSON file with a 'chats' list of {username} objects (e.g. discovered_chats.json).",
    )
    parser.add_argument("--max-chats", type=int, default=0, help="Cap number of chats scanned (0 = no cap).")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit-per-term", type=int, default=80)
    parser.add_argument("--max-samples-per-chat", type=int, default=8)
    parser.add_argument("--json-name", default="pain_scan.json")
    parser.add_argument("--markdown-name", default="pain_scan.md")
    parser.add_argument("--leads-json-name", default="pain_leads.json")
    parser.add_argument("--leads-csv-name", default="pain_leads.csv")
    parser.add_argument("--html-name", default="pain_dashboard.html")
    parser.add_argument("--svg-name", default="pain_infographic.svg")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    try:
        validate_no_dangerous_flags(unknown)
        if unknown:
            raise ConfigError("unsupported flag")
        if not args.approve_external_action:
            raise ConfigError("read-only Telegram scan requires --approve-external-action")
        if args.days < 1 or args.days > 365:
            raise ConfigError("--days must be between 1 and 365")
        if args.limit_per_term < 1 or args.limit_per_term > 500:
            raise ConfigError("--limit-per-term must be between 1 and 500")
        if args.max_samples_per_chat < 0 or args.max_samples_per_chat > 50:
            raise ConfigError("--max-samples-per-chat must be between 0 and 50")

        env = load_env_file(args.env_file)
        config = build_config(
            mode="telegram-login",
            output_dir=args.output_dir,
            approve_external_action=True,
            tg_api_id=args.tg_api_id,
            tg_api_hash=args.tg_api_hash,
            tg_phone=args.tg_phone,
            tg_proxy_url=args.tg_proxy_url,
            session_file=args.session_file,
            env=env,
        )
        chats = resolve_chats(args)
        result = asyncio.run(
            scan_chats(
                config=config,
                chats=chats,
                days=args.days,
                limit_per_term=args.limit_per_term,
                max_samples_per_chat=args.max_samples_per_chat,
            ),
        )

        out_dir = Path(args.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / args.json_name
        markdown_path = out_dir / args.markdown_name
        leads_json_path = out_dir / args.leads_json_name
        leads_csv_path = out_dir / args.leads_csv_name
        html_path = out_dir / args.html_name
        svg_path = out_dir / args.svg_name
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(render_markdown(result), encoding="utf-8")
        leads_json_path.write_text(json.dumps(result["leads"], ensure_ascii=False, indent=2), encoding="utf-8")
        write_leads_csv(leads_csv_path, result["leads"])
        html_path.write_text(render_html_dashboard(result), encoding="utf-8")
        svg_path.write_text(render_svg_infographic(result), encoding="utf-8")
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"error: lead pain scan failed: {exc.__class__.__name__}", file=sys.stderr)
        return 1

    totals = result["totals"]
    print(f"json: {json_path}")
    print(f"markdown: {markdown_path}")
    print(f"leads_json: {leads_json_path}")
    print(f"leads_csv: {leads_csv_path}")
    print(f"html: {html_path}")
    print(f"svg: {svg_path}")
    print(f"direct_pain_messages: {totals['direct_pain_messages']}")
    print(f"unique_pain_authors: {totals['unique_pain_authors']}")
    print(f"lead_candidates: {totals['lead_candidates']}")
    print(f"supply_messages: {totals['supply_messages']}")
    return 0


def resolve_chats(args: argparse.Namespace) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        cleaned = (name or "").strip().lstrip("@")
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            ordered.append(cleaned)

    for name in args.chats or ():
        add(name)
    if args.chats_file:
        raw = json.loads(Path(args.chats_file).read_text(encoding="utf-8"))
        entries = raw.get("chats", raw) if isinstance(raw, dict) else raw
        for entry in entries:
            if isinstance(entry, str):
                add(entry)
            elif isinstance(entry, dict) and entry.get("username"):
                add(str(entry["username"]))
    if not ordered:
        ordered.extend(DEFAULT_CHATS)
    if args.max_chats and args.max_chats > 0:
        ordered = ordered[: args.max_chats]
    return tuple(ordered)


async def scan_chats(
    *,
    config: Any,
    chats: tuple[str, ...],
    days: int,
    limit_per_term: int,
    max_samples_per_chat: int,
) -> dict[str, Any]:
    wrapper = make_telethon_client(config)
    client = await wrapper._get_client()
    await wrapper.ensure_authorized()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[ChatStats] = []
    global_author_keys: set[tuple[str, int]] = set()

    try:
        for chat in chats:
            stats = await scan_one_chat(
                client=client,
                chat=chat,
                since=since,
                limit_per_term=limit_per_term,
                max_samples=max_samples_per_chat,
            )
            rows.append(stats)
            for key in getattr(stats, "_author_keys", set()):
                global_author_keys.add((stats.chat, key))
            await asyncio.sleep(0.6)
    finally:
        await wrapper.close()

    serial_rows = [serialize_chat_stats(row) for row in rows]
    leads = [lead for row in serial_rows for lead in row["leads"]]
    leads.sort(
        key=lambda lead: (_lead_type_rank(lead.get("lead_type", "")), lead["score"], lead["date"], lead["repeat_count"]),
        reverse=True,
    )
    serial_rows.sort(
        key=lambda row: (
            row["direct_pain_messages"],
            row["unique_pain_authors"],
            row["supply_messages"],
        ),
        reverse=True,
    )
    totals = {
        "chats_scanned": len(serial_rows),
        "keyword_matches": sum(row["keyword_matches"] for row in serial_rows),
        "direct_pain_messages": sum(row["direct_pain_messages"] for row in serial_rows),
        "unique_pain_authors": len(global_author_keys),
        "lead_candidates": len(leads),
        "done_for_you_leads": sum(1 for lead in leads if lead.get("lead_type") == "done_for_you"),
        "advice_leads": sum(1 for lead in leads if lead.get("lead_type") == "advice"),
        "supply_messages": sum(row["supply_messages"] for row in serial_rows),
        "noise_messages": sum(row["noise_messages"] for row in serial_rows),
    }
    category_totals: dict[str, int] = {}
    for row in serial_rows:
        for category, count in row["category_counts"].items():
            category_totals[category] = category_totals.get(category, 0) + int(count)
    totals["category_counts"] = dict(sorted(category_totals.items(), key=lambda item: item[1], reverse=True))

    return {
        "generated_at": generated_at,
        "scope": {
            "days": days,
            "since": since.isoformat(timespec="seconds"),
            "search_terms": list(SEARCH_TERMS),
            "privacy": (
                "Counts unique authors in memory only. Sender ids, usernames, and "
                "contact lists are not exported."
            ),
        },
        "totals": totals,
        "chats": serial_rows,
        "leads": leads,
    }


async def scan_one_chat(
    *,
    client: Any,
    chat: str,
    since: datetime,
    limit_per_term: int,
    max_samples: int,
) -> ChatStats:
    stats = ChatStats(chat=chat, link=f"https://t.me/{chat}")
    seen_messages: set[int] = set()
    author_keys: set[int] = set()
    lead_groups: dict[tuple[Any, str], LeadCandidate] = {}
    setattr(stats, "_author_keys", author_keys)
    try:
        entity = await client.get_entity(chat)
        stats.title = getattr(entity, "title", None) or chat
        stats.participants = getattr(entity, "participants_count", None)
        try:
            from telethon import functions

            full = await client(functions.channels.GetFullChannelRequest(entity))
            stats.participants = getattr(full.full_chat, "participants_count", None) or stats.participants
            stats.about = clean_text(getattr(full.full_chat, "about", None) or "", limit=280)
        except Exception as exc:
            stats.errors.append(f"full_chat:{exc.__class__.__name__}")

        for term in SEARCH_TERMS:
            try:
                async for message in client.iter_messages(entity, search=term, limit=limit_per_term):
                    date = getattr(message, "date", None)
                    if date is not None and date < since:
                        break
                    message_id = int(getattr(message, "id", 0) or 0)
                    if not message_id or message_id in seen_messages:
                        continue
                    seen_messages.add(message_id)
                    body = getattr(message, "message", "") or ""
                    if not body.strip():
                        continue
                    stats.keyword_matches += 1
                    classification = classify_message(body)
                    if classification["noise"]:
                        stats.noise_messages += 1
                    if classification["supply"]:
                        stats.supply_messages += 1
                    if classification["pain"]:
                        stats.direct_pain_messages += 1
                        sender_id = getattr(message, "sender_id", None)
                        if isinstance(sender_id, int):
                            author_keys.add(sender_id)
                        for category in classification["categories"]:
                            stats.category_counts[category] = stats.category_counts.get(category, 0) + 1
                        link = None
                        username = getattr(entity, "username", None)
                        if username:
                            link = f"https://t.me/{username}/{message_id}"
                        add_lead_candidate(
                            lead_groups=lead_groups,
                            chat=chat,
                            chat_title=stats.title or chat,
                            message_id=message_id,
                            link=link,
                            date=date,
                            sender_id=sender_id,
                            body=body,
                            keyword=term,
                            categories=classification["categories"],
                            lead_type=classification["lead_type"],
                        )
                        if len(stats.samples) < max_samples:
                            stats.samples.append(
                                Sample(
                                    date=date.date().isoformat() if date else "",
                                    category=", ".join(classification["categories"]) or "uncategorized",
                                    keyword=term,
                                    snippet=clean_text(body),
                                    link=link,
                                ),
                            )
            except Exception as exc:
                stats.errors.append(f"search:{term}:{exc.__class__.__name__}")
            await asyncio.sleep(0.15)
    except Exception as exc:
        stats.errors.append(f"chat:{exc.__class__.__name__}")
    stats.scanned_messages = len(seen_messages)
    stats.unique_pain_authors = len(author_keys)
    for lead in lead_groups.values():
        lead.score += min(max(lead.repeat_count - 1, 0), 2)
        lead.priority = priority_for_score(lead.score)
    stats.leads = sorted(
        lead_groups.values(),
        key=lambda lead: (lead.score, lead.date, lead.repeat_count),
        reverse=True,
    )
    return stats


def add_lead_candidate(
    *,
    lead_groups: dict[tuple[Any, str], LeadCandidate],
    chat: str,
    chat_title: str,
    message_id: int,
    link: str | None,
    date: datetime | None,
    sender_id: Any,
    body: str,
    keyword: str,
    categories: list[str],
    lead_type: str,
) -> None:
    primary_category = categories[0] if categories else "uncategorized"
    private_author_key: Any = sender_id if isinstance(sender_id, int) else f"msg:{message_id}"
    group_key = (private_author_key, primary_category)
    date_value = date.date().isoformat() if date else ""
    snippet = clean_text(body)
    context = infer_pain_context(body, categories, lead_type)
    score = score_lead(body=body, categories=categories, date=date, lead_type=lead_type)
    priority = priority_for_score(score)
    candidate = lead_groups.get(group_key)
    if candidate is None:
        lead_groups[group_key] = LeadCandidate(
            lead_id=f"{chat}/{message_id}",
            date=date_value,
            chat=chat,
            chat_title=chat_title,
            message_id=message_id,
            message_link=link,
            priority=priority,
            score=score,
            lead_type=lead_type,
            pain_context=context,
            categories=categories,
            keyword=keyword,
            snippet=snippet,
            repeat_count=1,
            evidence_links=[link] if link else [],
        )
        return
    candidate.repeat_count += 1
    if link and link not in candidate.evidence_links and len(candidate.evidence_links) < 5:
        candidate.evidence_links.append(link)
    new_rank = (_lead_type_rank(lead_type), score)
    cur_rank = (_lead_type_rank(candidate.lead_type), candidate.score)
    if new_rank > cur_rank:
        # The newest, strongest message becomes the representative for this author.
        candidate.score = score
        candidate.priority = priority
        candidate.lead_type = lead_type
        candidate.lead_id = f"{chat}/{message_id}"
        candidate.date = date_value
        candidate.message_id = message_id
        candidate.message_link = link
        candidate.pain_context = context
        candidate.categories = categories
        candidate.keyword = keyword
        candidate.snippet = snippet


def _lead_type_rank(lead_type: str) -> int:
    return {"done_for_you": 3, "advice": 2, "signal": 1}.get(lead_type, 0)


def infer_pain_context(text: str, categories: list[str], lead_type: str = "") -> str:
    lowered = normalize(text)
    if lead_type == "done_for_you":
        return "Готов делегировать: ищет исполнителя/команду сделать карточки, инфографику или визуал под ключ для WB/Ozon."
    if "product_card" in categories and any(hint in lowered for hint in (
        "\u0438\u0449\u0443",
        "\u0438\u0449\u0435\u043c",
        "\u0434\u0430\u0439\u0442\u0435 \u043a\u043e\u043d\u0442\u0430\u043a\u0442",
        "\u043d\u0443\u0436\u0435\u043d",
        "\u043d\u0443\u0436\u043d\u0430",
        "\u043d\u0443\u0436\u043d\u043e",
        "\u043f\u043e\u043c\u043e\u0433\u0438\u0442\u0435",
    )):
        return "\u0418\u0449\u0435\u0442 \u0438\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044f/\u043f\u043e\u043c\u043e\u0449\u044c \u043f\u043e \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0430\u043c, \u0438\u043d\u0444\u043e\u0433\u0440\u0430\u0444\u0438\u043a\u0435 \u0438\u043b\u0438 \u0432\u0438\u0437\u0443\u0430\u043b\u0443 \u0434\u043b\u044f WB/Ozon."
    if "product_card" in categories and "\u043f\u043e\u0434\u0441\u043a\u0430\u0436" in lowered:
        return "\u041f\u0440\u043e\u0441\u0438\u0442 \u0441\u043e\u0432\u0435\u0442 \u043f\u043e \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0435 \u0442\u043e\u0432\u0430\u0440\u0430, \u0434\u0438\u0437\u0430\u0439\u043d\u0443 \u0438\u043b\u0438 \u043a\u043e\u043d\u0442\u0435\u043d\u0442\u0443 \u043d\u0430 \u043c\u0430\u0440\u043a\u0435\u0442\u043f\u043b\u0435\u0439\u0441\u0435."
    if "short_video" in categories or "photo_animation" in categories:
        return "\u0411\u043e\u043b\u044c \u0432\u043e\u043a\u0440\u0443\u0433 \u0432\u0438\u0434\u0435\u043e/\u043e\u0431\u043b\u043e\u0436\u043a\u0438/\u043a\u043e\u0440\u043e\u0442\u043a\u043e\u0433\u043e AI-\u0432\u0438\u0434\u0435\u043e \u0434\u043b\u044f \u0442\u043e\u0432\u0430\u0440\u0430."
    if "product_photo" in categories:
        return "\u0411\u043e\u043b\u044c \u0432\u043e\u043a\u0440\u0443\u0433 \u0444\u043e\u0442\u043e \u0442\u043e\u0432\u0430\u0440\u0430, \u0444\u043e\u043d\u0430 \u0438\u043b\u0438 \u043d\u0435\u0439\u0440\u043e\u0444\u043e\u0442\u043e."
    if "ai_tool" in categories:
        return "\u0418\u0449\u0435\u0442 AI-\u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442, \u043d\u0435\u0439\u0440\u043e\u0441\u0435\u0442\u044c \u0438\u043b\u0438 \u043f\u043e\u0434\u0441\u043a\u0430\u0437\u043a\u0443 \u043f\u043e AI-\u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438."
    return "\u0415\u0441\u0442\u044c \u0441\u0438\u0433\u043d\u0430\u043b \u0431\u043e\u043b\u0438 \u0432 \u043f\u0443\u0431\u043b\u0438\u0447\u043d\u043e\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0438; \u043d\u0443\u0436\u043d\u0430 \u0440\u0443\u0447\u043d\u0430\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u043f\u0435\u0440\u0435\u0434 \u043e\u0442\u0432\u0435\u0442\u043e\u043c."


def score_lead(*, body: str, categories: list[str], date: datetime | None, lead_type: str = "") -> int:
    lowered = normalize(body)
    score = 0
    if lead_type == "done_for_you":
        score += 6
    if any(hint in lowered for hint in HARD_REQUEST_HINTS):
        score += 4
    if "product_card" in categories:
        score += 3
    if "short_video" in categories or "photo_animation" in categories:
        score += 2
    if "product_photo" in categories:
        score += 2
    if "ai_tool" in categories:
        score += 1
    if any(hint in lowered for hint in (
        "\u0438\u0449\u0443",
        "\u0438\u0449\u0435\u043c",
        "\u043d\u0443\u0436\u0435\u043d",
        "\u043d\u0443\u0436\u043d\u0430",
        "\u043d\u0443\u0436\u043d\u043e",
        "\u043f\u043e\u043c\u043e\u0433\u0438\u0442\u0435",
        "\u0434\u0430\u0439\u0442\u0435 \u043a\u043e\u043d\u0442\u0430\u043a\u0442",
    )):
        score += 2
    if date is not None and datetime.now(timezone.utc) - date <= timedelta(days=7):
        score += 1
    return score


def priority_for_score(score: int) -> str:
    if score >= 9:
        return "high"
    if score >= 6:
        return "medium"
    return "low"


def classify_message(text: str) -> dict[str, Any]:
    lowered = normalize(text)
    has_demand = any(hint in lowered for hint in DEMAND_HINTS)
    has_hard_request = any(hint in lowered for hint in HARD_REQUEST_HINTS)
    has_question = "?" in lowered
    has_topic = any(hint in lowered for hint in TOPIC_HINTS)
    has_supply = any(hint in lowered for hint in SUPPLY_HINTS)
    has_supply_only = any(hint in lowered for hint in SUPPLY_ONLY_HINTS)
    has_noise = any(hint in lowered for hint in NOISE_HINTS)
    categories = [
        category
        for category, hints in CATEGORY_HINTS.items()
        if any(hint in lowered for hint in hints)
    ]
    has_visual_topic = any(
        category in categories
        for category in ("product_card", "product_photo", "short_video", "photo_animation")
    )
    has_service_fit = (
        any(hint in lowered for hint in SERVICE_FIT_HINTS)
        and not any(hint in lowered for hint in SELLER_SEEKING_HINTS)
        # Reject designer self-ads that open with a rhetorical hook
        # ("Нужна инфографика? Создаю карточки…", "Я дизайнер, помогу вам").
        and not any(hint in lowered for hint in SUPPLY_ONLY_HINTS)
    )
    # Done-for-you requests ("ищу дизайнера", "сделайте карточки под ключ") are
    # leads on their own. Advice/curiosity is counted more conservatively, since
    # supply ads often contain rhetorical questions.
    pain = has_visual_topic and not has_noise and (
        has_service_fit
        or (
            has_topic
            and not has_supply_only
            and (has_hard_request or (has_question and has_demand and not has_supply))
        )
    )
    if not pain:
        lead_type = ""
    elif has_service_fit:
        lead_type = "done_for_you"
    elif has_hard_request:
        lead_type = "advice"
    else:
        lead_type = "signal"
    return {
        "pain": pain,
        "service_fit": has_service_fit,
        "lead_type": lead_type,
        "supply": (has_supply or has_supply_only) and has_topic and not has_service_fit,
        "noise": has_noise and not pain,
        "categories": categories or ["uncategorized"],
    }


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def clean_text(text: str, *, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"@\w+", "@...", text)
    text = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[phone]", text)
    return text[:limit]


def serialize_chat_stats(stats: ChatStats) -> dict[str, Any]:
    return {
        "chat": stats.chat,
        "title": stats.title,
        "link": stats.link,
        "participants": stats.participants,
        "about": stats.about,
        "scanned_messages": stats.scanned_messages,
        "keyword_matches": stats.keyword_matches,
        "direct_pain_messages": stats.direct_pain_messages,
        "unique_pain_authors": stats.unique_pain_authors,
        "supply_messages": stats.supply_messages,
        "noise_messages": stats.noise_messages,
        "category_counts": dict(sorted(stats.category_counts.items(), key=lambda item: item[1], reverse=True)),
        "samples": [sample.__dict__ for sample in stats.samples],
        "leads": [lead.__dict__ for lead in stats.leads],
        "errors": stats.errors,
    }


def write_leads_csv(path: Path, leads: list[dict[str, Any]]) -> None:
    fields = [
        "lead_id",
        "lead_type",
        "priority",
        "score",
        "date",
        "chat",
        "chat_title",
        "pain_context",
        "categories",
        "keyword",
        "message_link",
        "repeat_count",
        "snippet",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            row = dict(lead)
            row["categories"] = ", ".join(row.get("categories") or [])
            writer.writerow(row)


def render_markdown(result: dict[str, Any]) -> str:
    totals = result["totals"]
    lines = [
        "# Lead Pain Scan",
        "",
        f"Generated: {result['generated_at']}",
        f"Window: last {result['scope']['days']} days",
        "",
        "Privacy: sender ids, usernames, and contact lists are not exported.",
        "",
        "## Totals",
        "",
        f"- Chats scanned: {totals['chats_scanned']}",
        f"- Keyword-matched public messages: {totals['keyword_matches']}",
        f"- Direct pain messages: {totals['direct_pain_messages']}",
        f"- Unique pain authors: {totals['unique_pain_authors']}",
        f"- Lead candidates: {totals['lead_candidates']}",
        f"- Supply / competitor messages: {totals['supply_messages']}",
        "",
        "## Categories",
        "",
    ]
    for category, count in totals["category_counts"].items():
        lines.append(f"- `{category}`: {count}")
    lines.extend(["", "## Chats", ""])
    lines.append("| Chat | Pain msgs | Unique authors | Supply msgs | Top categories |")
    lines.append("|---|---:|---:|---:|---|")
    for row in result["chats"]:
        categories = ", ".join(f"{name}:{count}" for name, count in row["category_counts"].items()) or "-"
        title = row["title"] or row["chat"]
        lines.append(
            f"| [{escape_md(title)}]({row['link']}) | {row['direct_pain_messages']} | "
            f"{row['unique_pain_authors']} | {row['supply_messages']} | {escape_md(categories)} |",
        )
    lines.extend(["", "## Samples", ""])
    for row in result["chats"]:
        if not row["samples"]:
            continue
        lines.append(f"### {row['title'] or row['chat']}")
        for sample in row["samples"]:
            link = f" [message]({sample['link']})" if sample.get("link") else ""
            lines.append(
                f"- {sample['date']} `{sample['category']}` via `{sample['keyword']}`:{link} "
                f"{sample['snippet']}",
            )
        lines.append("")
    if result.get("leads"):
        lines.extend(["## Lead Candidates", ""])
        lines.append("| Lead ID | Type | Priority | Chat | Context | Link |")
        lines.append("|---|---|---|---|---|---|")
        for lead in result["leads"]:
            link = f"[message]({lead['message_link']})" if lead.get("message_link") else "-"
            lines.append(
                f"| `{escape_md(lead['lead_id'])}` | {escape_md(lead.get('lead_type') or 'signal')} | "
                f"{escape_md(lead['priority'])} | {escape_md(lead['chat'])} | "
                f"{escape_md(lead['pain_context'])} | {link} |",
            )
    return "\n".join(lines).rstrip() + "\n"


def render_html_dashboard(result: dict[str, Any]) -> str:
    totals = result["totals"]
    leads = result.get("leads", [])
    chats = [row for row in result["chats"] if row["direct_pain_messages"] > 0]
    categories = totals.get("category_counts", {})
    max_chat = max([row["direct_pain_messages"] for row in chats] or [1])
    max_category = max([int(count) for count in categories.values()] or [1])
    generated = html.escape(str(result["generated_at"]))
    rows_html = []
    for row in chats:
        width = max(6, int(row["direct_pain_messages"] / max_chat * 100))
        rows_html.append(
            "<div class=\"bar-row\">"
            f"<div class=\"bar-label\"><a href=\"{html.escape(row['link'])}\">{html.escape(row['chat'])}</a>"
            f"<span>{html.escape(row['title'] or row['chat'])}</span></div>"
            f"<div class=\"bar-track\"><div class=\"bar-fill\" style=\"width:{width}%\"></div></div>"
            f"<b>{row['direct_pain_messages']}</b>"
            "</div>"
        )
    category_html = []
    for name, count in categories.items():
        width = max(6, int(int(count) / max_category * 100))
        category_html.append(
            "<div class=\"metric-row\">"
            f"<span>{html.escape(name)}</span>"
            f"<div class=\"mini-track\"><div class=\"mini-fill\" style=\"width:{width}%\"></div></div>"
            f"<b>{int(count)}</b>"
            "</div>"
        )
    lead_type_labels = {
        "done_for_you": "под ключ",
        "advice": "совет",
        "signal": "сигнал",
    }
    lead_rows = []
    for lead in leads:
        categories_text = ", ".join(lead.get("categories") or [])
        link = lead.get("message_link") or ""
        link_html = f"<a class=\"open-link\" href=\"{html.escape(link)}\">open</a>" if link else "-"
        lead_type = lead.get("lead_type") or "signal"
        type_label = lead_type_labels.get(lead_type, lead_type)
        lead_rows.append(
            "<tr>"
            f"<td><code>{html.escape(lead['lead_id'])}</code></td>"
            f"<td><span class=\"tag {html.escape(lead_type)}\">{html.escape(type_label)}</span></td>"
            f"<td><span class=\"pill {html.escape(lead['priority'])}\">{html.escape(lead['priority'])}</span></td>"
            f"<td>{html.escape(lead['date'])}<br><span class=\"muted\">{html.escape(lead['chat'])}</span></td>"
            f"<td>{html.escape(lead['pain_context'])}<br><span class=\"muted\">{html.escape(categories_text)}</span></td>"
            f"<td>{html.escape(lead['snippet'])}</td>"
            f"<td>{link_html}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Photozhab Lead Pain Dashboard</title>
  <style>
    :root {{
      --bg: #f6f7fb;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9e0ea;
      --teal: #0f766e;
      --blue: #2563eb;
      --amber: #b45309;
      --rose: #be123c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, Segoe UI, Arial, sans-serif;
      line-height: 1.45;
    }}
    header {{
      padding: 28px 36px 20px;
      background: #111827;
      color: #fff;
    }}
    header h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    header p {{ margin: 0; color: #cbd5e1; max-width: 980px; }}
    main {{ padding: 28px 36px 42px; max-width: 1440px; margin: 0 auto; }}
    .kpis {{ display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .panel, .kpi {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05);
    }}
    .kpi {{ padding: 16px; }}
    .kpi span {{ display:block; color: var(--muted); font-size: 13px; }}
    .kpi b {{ display:block; font-size: 26px; margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: 1.45fr 0.85fr; gap: 16px; align-items: start; }}
    .panel {{ padding: 18px; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; letter-spacing: 0; }}
    .bar-row {{ display: grid; grid-template-columns: minmax(260px, 0.9fr) 1.6fr 44px; gap: 12px; align-items: center; margin: 12px 0; }}
    .bar-label a {{ color: var(--ink); font-weight: 700; text-decoration: none; }}
    .bar-label span {{ display:block; color: var(--muted); font-size: 12px; margin-top: 2px; }}
    .bar-track, .mini-track {{ height: 14px; border-radius: 999px; background: #e8edf4; overflow: hidden; }}
    .bar-fill {{ height: 100%; background: linear-gradient(90deg, var(--teal), var(--blue)); border-radius: 999px; }}
    .metric-row {{ display: grid; grid-template-columns: 130px 1fr 34px; gap: 10px; align-items: center; margin: 12px 0; }}
    .mini-fill {{ height: 100%; background: linear-gradient(90deg, var(--amber), var(--rose)); border-radius: 999px; }}
    .note {{ margin-top: 14px; padding: 12px; border-radius: 8px; background: #f8fafc; color: var(--muted); border: 1px solid var(--line); }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 11px 12px; border-bottom: 1px solid var(--line); vertical-align: top; text-align: left; font-size: 13px; }}
    th {{ background: #f1f5f9; color: #334155; font-weight: 700; position: sticky; top: 0; }}
    code {{ font-family: Consolas, ui-monospace, monospace; font-size: 12px; }}
    .muted {{ color: var(--muted); font-size: 12px; }}
    .pill {{ display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .pill.high {{ background: #fee2e2; color: #991b1b; }}
    .pill.medium {{ background: #fef3c7; color: #92400e; }}
    .pill.low {{ background: #dbeafe; color: #1d4ed8; }}
    .tag {{ display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 12px; font-weight: 700; }}
    .tag.done_for_you {{ background: #dcfce7; color: #166534; }}
    .tag.advice {{ background: #ede9fe; color: #5b21b6; }}
    .tag.signal {{ background: #f1f5f9; color: #475569; }}
    .open-link {{ color: var(--blue); font-weight: 700; text-decoration: none; }}
    @media (max-width: 980px) {{
      header, main {{ padding-left: 18px; padding-right: 18px; }}
      .kpis, .grid {{ grid-template-columns: 1fr; }}
      .bar-row {{ grid-template-columns: 1fr; }}
      table {{ display:block; overflow-x:auto; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Photozhab Lead Pain Dashboard</h1>
    <p>Generated {generated}. Public-message lead IDs only: no Telegram user ids, usernames, phone numbers, member lists, or contact exports.</p>
  </header>
  <main>
    <section class="kpis">
      <div class="kpi"><span>Candidate leads</span><b>{totals['lead_candidates']}</b></div>
      <div class="kpi"><span>Done-for-you (хотят делегировать)</span><b>{totals.get('done_for_you_leads', 0)}</b></div>
      <div class="kpi"><span>Advice / questions</span><b>{totals.get('advice_leads', 0)}</b></div>
      <div class="kpi"><span>Supply / competitors</span><b>{totals['supply_messages']}</b></div>
      <div class="kpi"><span>Chats scanned</span><b>{totals['chats_scanned']}</b></div>
    </section>
    <section class="grid">
      <div class="panel">
        <h2>Pain by chat</h2>
        {''.join(rows_html)}
      </div>
      <div class="panel">
        <h2>Demand categories</h2>
        {''.join(category_html)}
        <div class="note">Use this as a review queue. Open the public message, verify fit, then reply manually and only where chat rules allow it.</div>
      </div>
    </section>
    <section>
      <table>
        <thead>
          <tr><th>Lead ID</th><th>Type</th><th>Priority</th><th>Date / Chat</th><th>Pain context</th><th>Snippet</th><th>Link</th></tr>
        </thead>
        <tbody>{''.join(lead_rows)}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def render_svg_infographic(result: dict[str, Any]) -> str:
    totals = result["totals"]
    chats = [row for row in result["chats"] if row["direct_pain_messages"] > 0][:6]
    categories = list(result["totals"].get("category_counts", {}).items())[:5]
    max_chat = max([row["direct_pain_messages"] for row in chats] or [1])
    max_category = max([int(count) for _, count in categories] or [1])
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="820" viewBox="0 0 1200 820">',
        '<rect width="1200" height="820" fill="#f6f7fb"/>',
        '<rect x="40" y="34" width="1120" height="108" rx="8" fill="#111827"/>',
        '<text x="72" y="82" font-family="Segoe UI, Arial" font-size="30" font-weight="700" fill="#ffffff">Photozhab lead pain scan</text>',
        f'<text x="72" y="116" font-family="Segoe UI, Arial" font-size="16" fill="#cbd5e1">Public-message IDs only. Candidate leads: {totals["lead_candidates"]}; pain messages: {totals["direct_pain_messages"]}; supply/competitors: {totals["supply_messages"]}.</text>',
    ]
    kpis = [
        ("Candidate leads", totals["lead_candidates"], "#0f766e"),
        ("Done-for-you", totals.get("done_for_you_leads", 0), "#166534"),
        ("Advice / questions", totals.get("advice_leads", 0), "#5b21b6"),
        ("Chats scanned", totals["chats_scanned"], "#be123c"),
    ]
    for index, (label, value, color) in enumerate(kpis):
        x = 40 + index * 280
        lines.extend([
            f'<rect x="{x}" y="166" width="260" height="112" rx="8" fill="#ffffff" stroke="#d9e0ea"/>',
            f'<text x="{x + 22}" y="206" font-family="Segoe UI, Arial" font-size="15" fill="#667085">{html.escape(label)}</text>',
            f'<text x="{x + 22}" y="250" font-family="Segoe UI, Arial" font-size="38" font-weight="700" fill="{color}">{value}</text>',
        ])
    lines.extend([
        '<rect x="40" y="314" width="690" height="430" rx="8" fill="#ffffff" stroke="#d9e0ea"/>',
        '<text x="70" y="356" font-family="Segoe UI, Arial" font-size="22" font-weight="700" fill="#172033">Top chats by candidate pain</text>',
    ])
    y = 392
    for row in chats:
        width = int(row["direct_pain_messages"] / max_chat * 460)
        lines.extend([
            f'<text x="70" y="{y + 15}" font-family="Segoe UI, Arial" font-size="15" fill="#172033">{html.escape(row["chat"])}</text>',
            f'<rect x="245" y="{y}" width="460" height="20" rx="10" fill="#e8edf4"/>',
            f'<rect x="245" y="{y}" width="{max(12, width)}" height="20" rx="10" fill="#0f766e"/>',
            f'<text x="716" y="{y + 15}" font-family="Segoe UI, Arial" font-size="15" font-weight="700" fill="#172033">{row["direct_pain_messages"]}</text>',
        ])
        y += 56
    lines.extend([
        '<rect x="760" y="314" width="400" height="430" rx="8" fill="#ffffff" stroke="#d9e0ea"/>',
        '<text x="790" y="356" font-family="Segoe UI, Arial" font-size="22" font-weight="700" fill="#172033">Demand categories</text>',
    ])
    y = 392
    colors = ["#2563eb", "#b45309", "#be123c", "#0f766e", "#7c3aed"]
    for index, (name, count) in enumerate(categories):
        width = int(int(count) / max_category * 220)
        color = colors[index % len(colors)]
        lines.extend([
            f'<text x="790" y="{y + 15}" font-family="Segoe UI, Arial" font-size="15" fill="#172033">{html.escape(name)}</text>',
            f'<rect x="900" y="{y}" width="220" height="20" rx="10" fill="#e8edf4"/>',
            f'<rect x="900" y="{y}" width="{max(12, width)}" height="20" rx="10" fill="{color}"/>',
            f'<text x="1130" y="{y + 15}" font-family="Segoe UI, Arial" font-size="15" font-weight="700" fill="#172033">{int(count)}</text>',
        ])
        y += 56
    lines.extend([
        '<text x="40" y="786" font-family="Segoe UI, Arial" font-size="14" fill="#667085">Review manually before replying. No user IDs, usernames, phone numbers, or member lists are exported.</text>',
        '</svg>',
    ])
    return "\n".join(lines) + "\n"


def escape_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
