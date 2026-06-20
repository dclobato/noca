#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from enum import Enum


class Timezone(Enum):
    BAKER_ISLAND = ("Baker Island (UTC-12)", -12, "Etc/GMT+12")
    NIUE = ("Niue (UTC-11)", -11, "Pacific/Niue")
    HONOLULU = ("Honolulu (UTC-10)", -10, "Pacific/Honolulu")
    ANCHORAGE = ("Anchorage (UTC-9)", -9, "America/Anchorage")
    LOS_ANGELES = ("Los Angeles (UTC-8)", -8, "America/Los_Angeles")
    DENVER = ("Denver (UTC-7)", -7, "America/Denver")
    CHICAGO = ("Chicago (UTC-6)", -6, "America/Chicago")
    NEW_YORK = ("New York (UTC-5)", -5, "America/New_York")
    MANAUS = ("Manaus (UTC-4)", -4, "America/Manaus")
    CARACAS = ("Caracas (UTC-4:30)", -4.5, "America/Caracas")
    BRASILIA = ("Brasilia (UTC-3)", -3, "America/Sao_Paulo")
    NEWFOUNDLAND = ("Newfoundland (UTC-3:30)", -3.5, "America/St_Johns")
    FERNANDO_DE_NORONHA = ("Fernando de Noronha (UTC-2)", -2, "America/Noronha")
    AZORES = ("Azores (UTC-1)", -1, "Atlantic/Azores")
    LONDON = ("London (UTC+0)", 0, "Europe/London")
    PARIS = ("Paris (UTC+1)", 1, "Europe/Paris")
    ATHENS = ("Athens (UTC+2)", 2, "Europe/Athens")
    MOSCOW = ("Moscow (UTC+3)", 3, "Europe/Moscow")
    DUBAI = ("Dubai (UTC+4)", 4, "Asia/Dubai")
    KABUL = ("Kabul (UTC+4:30)", 4.5, "Asia/Kabul")
    TASHKENT = ("Tashkent (UTC+5)", 5, "Asia/Tashkent")
    MUMBAI = ("Mumbai (UTC+5:30)", 5.5, "Asia/Kolkata")
    KATHMANDU = ("Kathmandu (UTC+5:45)", 5.75, "Asia/Kathmandu")
    DHAKA = ("Dhaka (UTC+6)", 6, "Asia/Dhaka")
    YANGON = ("Yangon (UTC+6:30)", 6.5, "Asia/Yangon")
    BANGKOK = ("Bangkok (UTC+7)", 7, "Asia/Bangkok")
    BEIJING = ("Beijing (UTC+8)", 8, "Asia/Shanghai")
    EUCLA = ("Eucla (UTC+8:45)", 8.75, "Australia/Eucla")
    TOKYO = ("Tokyo (UTC+9)", 9, "Asia/Tokyo")
    ADELAIDE = ("Adelaide (UTC+9:30)", 9.5, "Australia/Adelaide")
    SYDNEY = ("Sydney (UTC+10)", 10, "Australia/Sydney")
    LORD_HOWE = ("Lord Howe Island (UTC+10:30)", 10.5, "Australia/Lord_Howe")
    SOLOMON_ISLANDS = ("Solomon Islands (UTC+11)", 11, "Pacific/Guadalcanal")
    NORFOLK_ISLAND = ("Norfolk Island (UTC+11:30)", 11.5, "Pacific/Norfolk")
    AUCKLAND = ("Auckland (UTC+12)", 12, "Pacific/Auckland")
    CHATHAM_ISLANDS = ("Chatham Islands (UTC+12:45)", 12.75, "Pacific/Chatham")
    SAMOA = ("Samoa (UTC+13)", 13, "Pacific/Apia")
    LINE_ISLANDS = ("Line Islands (UTC+14)", 14, "Pacific/Kiritimati")

    def __init__(self, label: str, offset: float, iana: str) -> None:
        self.label = label
        self.offset = offset
        self.iana = iana

    def __str__(self) -> str:
        return self.label

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        """Return list of (iana_string, label) tuples for use in select fields.

        Returns:
            List of tuples in the format [(iana_string, label), ...]
            Example: [('America/Sao_Paulo', 'Brasília (UTC-3)'), ...]
        """
        return [(tz.iana, tz.label) for tz in cls]

    @classmethod
    def get_by_offset(cls, offset: float) -> Timezone | None:
        """Return timezone enum by numeric offset.

        Args:
            offset: Numeric offset in hours

        Returns:
            Matching Timezone enum member or None if not found
        """
        for tz in cls:
            if tz.offset == offset:
                return tz
        return None

    @classmethod
    def get_by_iana(cls, iana: str) -> Timezone | None:
        """Return timezone enum by IANA string.

        Args:
            iana: IANA timezone string (e.g. 'America/Sao_Paulo')

        Returns:
            Matching Timezone enum member or None if not found
        """
        for tz in cls:
            if tz.iana == iana:
                return tz
        return None
