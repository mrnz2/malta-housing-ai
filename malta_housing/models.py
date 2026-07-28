"""Shared Pydantic data contracts for the Malta Housing AI pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SellerType = Literal["OWNER", "AGENT", "SENSAR", "UNKNOWN"]
SourceType = Literal[
    "maltapark", "ownersbest", "djar", "propertymarket", "yitaku", "remax", "simonmamo"
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ScrapedListing(BaseModel):
    """Staging schema written by scrapers into scraped_listings.json."""

    url: str
    title: str
    raw_text: str
    source: SourceType
    scraped_at: str = Field(default_factory=utc_now_iso)


class MaltaPropertySchema(BaseModel):
    """Fields extracted by the LLM from raw listing text."""

    title: str = Field(description="Tytuł nieruchomości")
    price_eur: Optional[int] = Field(
        default=None,
        description="Cena w EUR jako czysta liczba, np. 650000. Szukaj kwot przy symbolu €",
    )
    locality: Optional[str] = Field(
        default=None,
        description="Town/village on mainland Malta only (e.g. Sliema, Qormi, Birzebbuga). Never Gozo.",
    )
    property_type: Optional[str] = Field(
        default=None,
        description="Typ: Apartment, Maisonette, Townhouse, Garage, Terraced House itp.",
    )
    bedrooms: Optional[int] = Field(default=None, description="Liczba sypialni (int)")
    seller_type: Optional[SellerType] = Field(
        default=None,
        description="Wybierz dokładnie jeden: OWNER, AGENT, SENSAR, UNKNOWN",
    )
    is_freehold: bool = Field(
        default=False, description="True tylko jeśli w tekście pojawia się słowo Freehold"
    )
    has_airspace: bool = Field(
        default=False, description="True tylko jeśli w tekście pojawia się Airspace"
    )
    has_sea_view: bool = Field(
        default=False,
        description="True jeśli pojawia się Sea View / Valley View / Breathtaking Views",
    )
    is_shell_form: bool = Field(
        default=False, description="True tylko jeśli Level of Finish to Shell"
    )
    key_features: list[str] = Field(
        default_factory=list, description="Max 4 najważniejsze atuty nieruchomości"
    )

    @field_validator("is_freehold", "has_airspace", "has_sea_view", "is_shell_form", mode="before")
    @classmethod
    def convert_null_to_bool(cls, v):
        if v is None:
            return False
        return bool(v)

    @field_validator("seller_type", mode="before")
    @classmethod
    def normalize_seller_type(cls, v):
        if v is None:
            return None
        normalized = str(v).strip().upper()
        if normalized in {"OWNER", "AGENT", "SENSAR", "UNKNOWN"}:
            return normalized
        return "UNKNOWN"

    @field_validator("key_features", mode="before")
    @classmethod
    def ensure_key_features_list(cls, v):
        if v is None:
            return []
        return v


class ParsedListing(MaltaPropertySchema):
    """Structured listing ready for SQLite (parser output / DB row)."""

    url: str
    source: Optional[SourceType] = None
    scraped_at: Optional[str] = None
    updated_at: Optional[str] = None
    distance_to_gzira_km: Optional[float] = Field(
        default=None,
        description="Szacowana odległość (km) do Gżiry z to_gzira.csv — uzupełniane poza LLM",
    )
