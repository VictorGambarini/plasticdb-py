from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PlasticDBModel(BaseModel):
    """Base for response models. Unknown fields are kept: the API only ever adds them."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)
