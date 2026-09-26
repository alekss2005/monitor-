#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KYIV MONITOR — единый файл.
Мониторинг Telegram-каналов: только Киев.
События: взрывы, падения БПЛА/шахедов, ракеты (КР, Искандер, Оникс, Кинжал, Калибр и др.),
попадания в жилые/нежилые дома, ТРЦ, АЗС, заправки, маркеты, супермаркеты, магазины,
объекты инфраструктуры. Задача — быстро найти сообщение и извлечь точный адрес.

Карта: OpenStreetMap (https://www.openstreetmap.org/copyright)
"""

import os
import re
import sys
import json
import time
import hashlib
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

# ============================================================
# 1. КОНФИГУРАЦИЯ (правьте здесь)
# ============================================================

# Каналы для слежения (username без @). Список можно пополнять.
CHANNELS = [
    "geran_gerbera",        # ГЕРАНЬ: Черный ящик
    "geranium_chronicles",  # Хроники Гераней
    "milinfolive",          # Военный Осведомитель
    "The_Wrong_Side",       # ИЗНАНКА
    "mon1tor_ua",           # ППО радар
    "insiderUKR",           # INSIDER UA
    "oberezhnovibuhi",      # ОБЕРЕЖНО ВИБУХИ
    "kyiv_golovne",         # Київ Головне
    "nebo_raketa",          # Київський купол
    # добавляйте свои:
    # "kyiv_alerts",
    # "kiev_now",
]

# Telegram API — получить на https://my.telegram.org
# Можно задать через переменные окружения TG_API_ID / TG_API_HASH
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION = os.getenv("TG_SESSION", "kyiv_monitor")

# Окно дедупликации (сек)
DEDUP_WINDOW_SEC = 600

# Вывод
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ============================================================
# 2. ГЕО-ФИЛЬТР: ТОЛЬКО КИЕВ
# ============================================================

KYIV_CORE = [r"\bкиев\b", r"\bкиєв", r"\bkyiv\b", r"\bкиеве\b", r"\bкиєві\b"]

KYIV_DISTRICTS = [
    r"\bголосеев", r"\bголосіїв", r"\bдарниц", r"\bдеснян",
    r"\bднепровск", r"\bдніпровськ", r"\bоболон", r"\bпечерск",
    r"\bподольск", r"\bподільськ", r"\bсвятошин", r"\bсоломен",
    r"\bшевченков", r"\bшевченків",
]

KYIV_MICRO = [
    r"\bтроещин", r"\bтроєщин", r"\bвиноградар", r"\bвиногр",
    r"\bоболон", r"\bкуреневк", r"\bкуренівк", r"\bсырец", r"\bсирець",
    r"\bнивки\b", r"\bникольск", r"\bмикольськ", r"\bбагговут",
    r"\bтатарк", r"\bлукьянов", r"\bлук'янів", r"\bзверинец", r"\bзвіринець",
    r"\bлипки\b", r"\bподол\b", r"\bподіл\b",
    r"\bосокорк", r"\bпозняк", r"\bхарьковск", r"\bхарківськ",
    r"\bберезняк", r"\bрусанівк", r"\bлевобереж",
    r"\bрадужн", r"\bрайдужн", r"\bвоскресенк",
    r"\bмышоловк", r"\bмишоловк", r"\bкорчеватое", r"\bкорчуват",
    r"\bчапаевк", r"\bчапаївк", r"\bжитемирск", r"\bжитомирськ",
    r"\bбеличи\b", r"\bбіличі\b", r"\bновобеличи", r"\bновобіличі",
    r"\bакадемгород", r"\bакадеммістечк",
    r"\bтерьомки\b", r"\bтерёмки\b", r"\bмотор-?сич",
]

KYIV_METRO = [r"\bметро\b", r"\bм\.\s?[а-яa-z]"]

KYIV_LANDMARKS = [
    r"\bмайдан\b", r"\bхрещатик", r"\bкрещатик",
    r"\bарсенальн", r"\bднепр\b", r"\bдніпро\b",
    r"\bпатон", r"\bмост\b", r"\bміст\b",
    r"\bжулян", r"\bтеличк", r"\bвыдубич", r"\bвидубич",
    r"\bтриполь", r"\bтрипілля", r"\bукраинк", r"\bукраїнк",
    r"\bбровар", r"\bирпен", r"\bірпінь", r"\bбуча\b", r"\bбучан",
    r"\bвышгород", r"\bвишгород",
]

GEO_RE = re.compile(
    "|".join(KYIV_CORE + KYIV_DISTRICTS + KYIV_MICRO + KYIV_METRO + KYIV_LANDMARKS),
    re.IGNORECASE,
)

# ============================================================
# 3. ФИЛЬТРЫ СОБЫТИЙ / СРЕДСТВ / ОБЪЕКТОВ
# ============================================================

EVENTS = {
    "взрыв": [r"\bвзрыв", r"\bвибух", r"\bexplosion", r"\bвзрывы", r"\bвибухи",
              r"\bгромко\b", r"\bбахнул", r"\bхлопок", r"\bхлопк"],
    "детонация": [r"\bдетонац", r"\bдетонаці", r"\bdetonation"],
    "попадание": [r"\bпопадан", r"\bпопад", r"\bвлучан", r"\bвлучив", r"\bприлет",
                  r"\bприліт", r"\bприлёт", r"\bhit\b", r"\bstrike\b",
                  r"\bудар\b", r"\bпоражен", r"\bуражен"],
    "падение": [r"\bпадени", r"\bпадінн", r"\bупал", r"\bвпав", r"\bобломк",
                r"\bуламк", r"\bсбит", r"\bзбит", r"\bdowned", r"\bdebris\b"],
    "пожар": [r"\bпожар", r"\bпожеж", r"\bгорит", r"\bгоріт", r"\bвозгоран",
              r"\bзайман", r"\bfire\b", r"\bburning\b", r"\bдым\b", r"\bдим\b"],
}

WEAPONS = {
    "БПЛА": [r"\bбпла\b", r"\bдрон", r"\bдроны", r"\bдрони", r"\buav\b"],
    "шахед": [r"\bшахед", r"\bshahed", r"\bгеран", r"\bgeran", r"\bгерань"],
    "ракета": [r"\bракет", r"\bmissile", r"\bкр\b", r"\bкрылат", r"\bкрилат"],
    "Искандер": [r"\bискандер", r"\biskander"],
    "Оникс": [r"\bоникс", r"\boniks\b"],
    "Кинжал": [r"\bкинжал", r"\bкінжал", r"\bkinzhal"],
    "Калибр": [r"\bкалибр", r"\bкалібр", r"\bkalibr"],
    "С-300": [r"\bс-?300", r"\bs-?300\b"],
    "баллистика": [r"\bбаллист", r"\bбалістич"],
    "МиГ-31К": [r"\bмиг-?31", r"\bміг-?31"],
}

TARGETS = {
    "жилой дом": [r"\bжилой дом", r"\bжилой\b", r"\bжитлов", r"\bмногоэтаж",
                  r"\bбагатоповерх", r"\bмногоквартир", r"\bбагатоквартир",
                  r"\bквартир", r"\bдом\b", r"\bбудинок\b"],
    "АЗС / заправка": [r"\bазс\b", r"\bзаправк", r"\bбензин", r"\bfuel\b",
                       r"\bгазозаправ"],
    "ТРЦ / ТЦ": [r"\bтрц\b", r"\bтц\b", r"\bторгов", r"\bторговельн", r"\bмолл\b",
                 r"\bmall\b", r"\bторговый центр", r"\bторговий центр"],
    "маркет / супермаркет": [r"\bмаркет", r"\bсупермаркет", r"\bмагазин",
                             r"\bгипермаркет", r"\bгіпермаркет", r"\bатб\b",
                             r"\bсильпо", r"\bсільпо", r"\bновус", r"\bnovus",
                             r"\bфора", r"\bvarus"],
    "инфраструктура": [r"\bподстанц", r"\bпідстанц", r"\bтэц\b", r"\bтэс\b",
                       r"\bкотельн", r"\bводоканал", r"\bнасосн",
                       r"\bзавод\b", r"\bсклад\b", r"\bдепо\b"],
    "больница / школа": [r"\bбольниц", r"\bлікарн", r"\bшкол", r"\bсадик",
                         r"\bдетсад", r"\bдитячий садок"],
}

EVENT_RE = {k: re.compile("|".join(v), re.IGNORECASE) for k, v in EVENTS.items()}
WEAPON_RE = {k: re.compile("|".join(v), re.IGNORECASE) for k, v in WEAPONS.items()}
TARGET_RE = {k: re.compile("|".join(v), re.IGNORECASE) for k, v in TARGETS.items()}


def is_kyiv(text: str) -> bool:
    return bool(GEO_RE.search(text))


def detect_events(text: str) -> list:
    return [k for k, rx in EVENT_RE.items() if rx.search(text)]


def detect_weapons(text: str) -> list:
    return [k for k, rx in WEAPON_RE.items() if rx.search(text)]


def detect_targets(text: str) -> list:
    return [k for k, rx in TARGET_RE.items() if rx.search(text)]


# ============================================================
# 4. ИЗВЛЕЧЕНИЕ АДРЕСА
# ============================================================

STREET_HOUSE_RX = re.compile(
    r"(?:ул\.?|улица|вул\.?|вулиця|просп\.?|проспект|пр-?т|"
    r"бул\.?|бульвар|пер\.?|переулок|пров\.?|провулок|"
    r"пл\.?|площадь|площа|шоссе|шосе|наб\.?|набережн|набережна|"
    r"проезд|проїзд)"
    r"\s*[:\-]?\s*([А-ЯA-ZЇІЄҐа-яa-zїієґ0-9\-\s\.']{3,60}?)"
    r"\s*[,\s]\s*(\d+[А-Яа-яA-Za-z]?(?:\s*[/\-]\s*\d+[А-Яа-яA-Za-z]?)?)",
    re.IGNORECASE,
)

STREET_RX = re.compile(
    r"(?:ул\.?|улица|вул\.?|вулиця|просп\.?|проспект|пр-?т|"
    r"бул\.?|бульвар|пер\.?|переулок|пров\.?|провулок|"
    r"пл\.?|площадь|площа|шоссе|шосе|наб\.?|набережн|набережна|"
    r"проезд|проїзд)"
    r"\s*[:\-]?\s*([А-ЯA-ZЇІЄҐа-яa-zїієґ0-9\-\s\.']{3,60})",
    re.IGNORECASE,
)

HOUSE_RX = re.compile(
    r"(?:д\.?|дом|будинок|буд\.?)\s*[:\-]?\s*"
    r"(\d+[А-Яа-яA-Za-z]?(?:\s*[/\-]\s*\d+[А-Яа-яA-Za-z]?)?)",
    re.IGNORECASE,
)

METRO_RX = re.compile(
    r"(?:метро|м\.)\s*[:\-]?\s*([А-ЯA-ZЇІЄҐа-яa-zїієґ\-\s']{3,40})",
    re.IGNORECASE,
)

DISTRICT_RX = re.compile(
    r"\b(голосеевск|голосіївськ|дарницк|деснянск|днепровск|дніпровськ|"
    r"оболонск|оболонськ|печерск|печерськ|подольск|подільськ|"
    r"святошинск|святошинськ|соломенск|солом'янськ|"
    r"шевченковск|шевченківськ)\w*\s*(?:район|р-н)?",
    re.IGNORECASE,
)


def extract_address(text: str) -> dict:
    addr = {"street": None, "house": None, "metro": None, "district": None, "raw": None}

    m = STREET_HOUSE_RX.search(text)
    if m:
        addr["street"] = m.group(1).strip()
        addr["house"] = m.group(2).strip()
    else:
        m = STREET_RX.search(text)
        if m:
            addr["street"] = m.group(1).strip()
        m = HOUSE_RX.search(text)
        if m:
            addr["house"] = m.group(1).strip()

    m = METRO_RX.search(text)
    if m:
        addr["metro"] = m.group(1).strip()

    m = DISTRICT_RX.search(text)
    if m:
        addr["district"] = m.group(1).strip()

    parts = [f"{k}: {addr[k]}" for k in ("district", "street", "house", "metro") if addr[k]]
    if parts:
        addr["raw"] = " | ".join(parts)
    return addr


# ============================================================
# 5. ДЕДУПЛИКАЦИЯ
# ============================================================

class Deduplicator:
    def __init__(self, window_sec: int = DEDUP_WINDOW_SEC):
        self.window_sec = window_sec
        self._seen: dict = {}

    @staticmethod
    def _key(text: str) -> str:
        t = re.sub(r"\d{1,2}:\d{2}(:\d{2})?", "", text)
        t = re.sub(r"\s+", " ", t).strip().lower()[:120]
        return hashlib.sha1(t.encode("utf-8")).hexdigest()

    def is_duplicate(self, text: str) -> bool:
        now = time.time()
        for k in [k for k, ts in self._seen.items() if now - ts > self.window_sec]:
            del self._seen[k]
        key = self._key(text)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


# ============================================================
# 6. МОДЕЛЬ И ХРАНИЛИЩЕ
# ============================================================

@dataclass
class Incident:
    channel: str
    text: str
    events: list = field(default_factory=list)
    weapons: list = field(default_factory=list)
    targets: list = field(default_factory=list)
    address: dict = field(default_factory=dict)
    msg_id: int | None = None
    date: str | None = None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    geo: str = "Киев"


class Storage:
    def __init__(self, output_dir: Path = OUTPUT_DIR):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.file = self.output_dir / "kyiv_incidents.jsonl"

    def save(self, inc: Incident) -> None:
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(inc), ensure_ascii=False) + "\n")

    def print(self, inc: Incident) -> None:
        ev = ", ".join(inc.events) or "—"
        wp = ", ".join(inc.weapons) or "—"
        tg = ", ".join(inc.targets) or "—"
        addr = inc.address.get("raw") or "адрес не распознан"
        print(
            f"\n[{inc.ts}] {inc.channel}\n"
            f"  события : {ev}\n"
            f"  средства: {wp}\n"
            f"  объекты : {tg}\n"
            f"  адрес   : {addr}\n"
            f"  текст   : {inc.text[:300]}\n"
        )


# ============================================================
# 7. ЯДРО ОБРАБОТКИ СООБЩЕНИЯ
# ============================================================

def build_incident(channel: str, text: str, msg_id=None, date=None,
                   dedup: Deduplicator | None = None) -> Incident | None:
    if not text:
        return None

    text = " ".join(text.split())

    if not is_kyiv(text):
        return None

    events = detect_events(text)
    weapons = detect_weapons(text)
    targets = detect_targets(text)

    if not events and not weapons:
        return None

    if dedup and dedup.is_duplicate(text):
        return None

    return Incident(
        channel=channel,
        text=text,
        events=events,
        weapons=weapons,
        targets=targets,
        address=extract_address(text),
        msg_id=msg_id,
        date=date,
    )


# ============================================================
# 8. TELEGRAM-МОНИТОР
# ============================================================

def run_telegram():
    if not TG_API_ID or not TG_API_HASH:
        print("Задайте TG_API_ID и TG_API_HASH (переменные окружения).")
        print("Пример: export TG_API_ID=12345 && export TG_API_HASH=abcdef")
        sys.exit(1)

    try:
        from telethon.sync import TelegramClient, events
    except ImportError:
        print("Установите telethon: pip install telethon")
        sys.exit(1)

    storage = Storage()
    dedup = Deduplicator()

    client = TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)

    @client.on(events.NewMessage(chats=CHANNELS))
    def handler(event):
        try:
            text = event.message.message or ""
            inc = build_incident(
                channel=getattr(event.chat, "username", None) or str(event.chat_id),
                text=text,
                msg_id=event.message.id,
                date=str(event.message.date),
                dedup=dedup,
            )
            if inc:
                storage.save(inc)
                storage.print(inc)
        except Exception as e:
            logging.exception("Ошибка обработки: %s", e)

    print(f"Старт. Каналы ({len(CHANNELS)}): {', '.join(CHANNELS)}")
    client.start()
    client.run_until_disconnected()


# ============================================================
# 9. ENTRYPOINT
# ============================================================

def main():
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(OUTPUT_DIR / "kyiv_monitor.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_telegram()


if __name__ == "__main__":
    main()
