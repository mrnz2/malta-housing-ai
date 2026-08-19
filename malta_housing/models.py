"""Shared Pydantic data contracts for the Malta Housing AI pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from malta_housing.distances import SeaProximity
from malta_housing.i18n.property_types import normalize_property_type
from malta_housing.parsing.text_normalize import normalize_display_text, normalize_locality_text

SellerType = Literal["OWNER", "AGENT", "SENSAR", "UNKNOWN"]
SourceType = Literal[
    "maltapark",
    "ownersbest",
    "djar",
    "propertymarket",
    "yitaku",
    "remax",
    "simonmamo",
    "belair",
    "re316",
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

    title_en: str = Field(default="", description="Property title in English")
    title_pl: str = Field(default="", description="Property title in Polish")
    title: Optional[str] = Field(default=None, description="Legacy alias for title_en")
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
        description="Canonical code: apartment, maisonette, penthouse, garage, etc.",
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
    ready: Optional[bool] = Field(
        default=None,
        description=(
            "True jeśli mieszkanie jest gotowe do zamieszkania (umeblowane, wykończone, "
            "move-in ready). False jeśli wymaga remontu, jest w stanie shell lub niewykończone. "
            "Null jeśli brak informacji w tekście."
        ),
    )
    key_features_en: list[str] = Field(
        default_factory=list, description="Up to 4 key features in English"
    )
    key_features_pl: list[str] = Field(
        default_factory=list, description="Up to 4 key features in Polish"
    )
    key_features: list[str] = Field(
        default_factory=list, description="Legacy alias for key_features_en"
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_parse_shape(cls, data: Any):
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if not out.get("title_en") and out.get("title"):
            out["title_en"] = out["title"]
        if not out.get("title_pl") and out.get("title_en"):
            out["title_pl"] = out["title_en"]
        if not out.get("key_features_en") and out.get("key_features"):
            out["key_features_en"] = out["key_features"]
        if not out.get("key_features_pl") and out.get("key_features_en"):
            out["key_features_pl"] = out["key_features_en"]
        return out

    @field_validator("title", "title_en", "title_pl", mode="before")
    @classmethod
    def strip_titles(cls, v):
        if v is None:
            return v
        return str(v).strip()

    @field_validator("title", "title_en", "title_pl", mode="after")
    @classmethod
    def normalize_title_text(cls, v):
        if v is None or not str(v).strip():
            return v
        return normalize_display_text(str(v))

    @field_validator("locality", mode="after")
    @classmethod
    def normalize_locality_field(cls, v):
        if v is None or not str(v).strip():
            return v
        return normalize_locality_text(str(v))

    @field_validator("property_type", mode="before")
    @classmethod
    def normalize_property_type_field(cls, v):
        return normalize_property_type(v) if v is not None else None

    @model_validator(mode="after")
    def fill_legacy_and_bilingual(self):
        if not self.title_en and self.title:
            self.title_en = self.title
        if not self.title and self.title_en:
            self.title = self.title_en
        if not self.title_pl and self.title_en:
            self.title_pl = self.title_en
        if not self.key_features_en and self.key_features:
            self.key_features_en = list(self.key_features)
        if not self.key_features and self.key_features_en:
            self.key_features = list(self.key_features_en)
        if not self.key_features_pl and self.key_features_en:
            self.key_features_pl = list(self.key_features_en)
        return self

    @field_validator("price_eur", "bedrooms", mode="before")
    @classmethod
    def coerce_int_fields(cls, v):
        if v is None:
            return None
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, float):
            return round(v)
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            cleaned = v.replace(",", "").replace(" ", "").replace("€", "").strip()
            if not cleaned:
                return None
            try:
                return round(float(cleaned))
            except ValueError:
                return None
        return v

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

    @field_validator("key_features", "key_features_en", "key_features_pl", mode="after")
    @classmethod
    def normalize_key_features_text(cls, v):
        if not v:
            return v
        return [
            normalize_display_text(str(item)) if item and str(item).strip() else item
            for item in v
        ]

    @field_validator("key_features", "key_features_en", "key_features_pl", mode="before")
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
    sea_proximity: Optional[SeaProximity] = Field(
        default=None,
        description="Bliskość morza z to_gzira.csv — uzupełniane poza LLM",
    )
