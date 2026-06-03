from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FacetItem(BaseModel):
    name: str
    count: int


class NewsItem(BaseModel):
    id: int
    id_message: int | None = None
    date: str | None = None
    title: str
    text: str
    summary: str
    tag: Any = None
    link_site: str | None = None
    source: str | None = None
    link_photo: str | None = None
    customer: str | None = None
    object: Any = None
    extra_tag: Any = None
    views: int = 0
    subscribers: int = 0
    regions: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class NewsListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[NewsItem]


class NewsMetaResponse(BaseModel):
    total: int
    news_total: int | None = None
    events_total: int | None = None
    topics: list[FacetItem]
    regions: list[FacetItem]
    products: list[FacetItem]
    tags: list[FacetItem]
    sources: list[FacetItem]
    customers: list[FacetItem]


class TimelineDay(BaseModel):
    date: str
    total: int
    topics: dict[str, int]
    related: list[FacetItem]


class TimelineResponse(BaseModel):
    days: int
    date_from: str
    date_to: str
    total: int
    avg_per_day: float
    topics: list[FacetItem]
    items: list[TimelineDay]


class EventSource(BaseModel):
    id: int
    title: str
    source: str | None = None
    date: str | None = None
    link_site: str | None = None


class EventRoleImpact(BaseModel):
    role: str
    label: str
    impact: Literal["positive", "negative", "neutral", "watch"]
    summary: str
    action_hint: str


class EventItem(BaseModel):
    id: str
    title: str
    summary: str
    date_from: str | None = None
    date_to: str | None = None
    news_count: int
    sources_count: int
    sigma: int
    views: int
    tags: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    impacts: list[EventRoleImpact] = Field(default_factory=list)
    sources: list[EventSource] = Field(default_factory=list)
    main_news_id: int | None = None


class EventListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[EventItem]


SortName = Literal["date_desc", "date_asc", "views_desc", "views_asc"]
PeriodName = Literal["today", "week", "month", "quarter"]
