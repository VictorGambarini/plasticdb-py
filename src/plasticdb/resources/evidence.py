"""The evidence tables, and search, pathways and statistics."""

from __future__ import annotations

import builtins
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from typing_extensions import Unpack

from .._endpoints import Endpoint
from .._pagination import Collection, InteractionCollection
from ..models import (
    BindingAssay,
    Construct,
    ExperimentRecord,
    GroupedSearch,
    Interaction,
    InteractionRecord,
    Kinetics,
    ObservationRecord,
    Pathway,
    PathwayGraph,
    PathwaySummary,
    Preparation,
    Properties,
    ProteinActivityStats,
    SearchHit,
    Stats,
    StructureLink,
)
from ._base import Resource
from ._filters import (
    BindingAssayFilters,
    EvidenceLevel,
    EvidenceListFilters,
    EvidenceScope,
    ExperimentListFilters,
    MaterialFilters,
    ObservationListFilters,
    RowEvidenceScope,
    check,
)

_INTERACTION_LIST = Endpoint("GET", "/interactions")
_INTERACTION_GET = Endpoint("GET", "/interactions/{record_id}")
_EXPERIMENT_LIST = Endpoint("GET", "/experiments")
_EXPERIMENT_GET = Endpoint("GET", "/experiments/{record_id}")
_OBSERVATION_LIST = Endpoint("GET", "/observations")
_OBSERVATION_GET = Endpoint("GET", "/observations/{record_id}")

_BINDING_LIST = Endpoint("GET", "/binding-assays")
_BINDING_GET = Endpoint("GET", "/binding-assays/{assay_id}")
_CONSTRUCT_LIST = Endpoint("GET", "/constructs")
_CONSTRUCT_GET = Endpoint("GET", "/constructs/{construct_id}")
_CONSTRUCT_STRUCTURES = Endpoint("GET", "/constructs/{construct_id}/structures")
_PREPARATION_LIST = Endpoint("GET", "/preparations")
_PREPARATION_GET = Endpoint("GET", "/preparations/{preparation_id}")
_EXPERIMENT_PREPARATIONS = Endpoint("GET", "/experiments/{experiment_id}/preparations")

_SEARCH = Endpoint("GET", "/search", live_only=True)
_SEARCH_GROUPED = Endpoint("GET", "/search/grouped", live_only=True)
_AUTOCOMPLETE = Endpoint("GET", "/search/autocomplete", live_only=True)

_PATHWAY_LIST = Endpoint("GET", "/pathways")
_PATHWAY_GET = Endpoint("GET", "/pathways/{pathway_id}")
_PATHWAYS_FOR_PLASTIC = Endpoint("GET", "/pathways/by-plastic/{plastic_id}")
_PATHWAY_GRAPH = Endpoint("GET", "/pathways/by-plastic/{plastic_id}/graph")

_STATS = Endpoint("GET", "/stats", live_only=True)
_STATS_ACTIVITY = Endpoint("GET", "/stats/protein-activity", live_only=True)
_STATS_CHARTS = Endpoint("GET", "/stats/charts", live_only=True)
_STATS_COUNTRY = Endpoint("GET", "/stats/by-country", live_only=True)


R = TypeVar("R", bound=BaseModel)


class _Records(Resource, Generic[R]):
    """Shared plumbing of the three flat evidence lists."""

    _list: Endpoint
    _get: Endpoint
    _model: type[R]
    _schema: Any
    _name: str

    def _collection(
        self, filters: dict[str, Any], page_size: int | None, limit: int | None
    ) -> Collection[R]:
        params = check(filters, self._schema, f"evidence.{self._name}.list")
        return Collection(
            self._client,
            self._list,
            self._model,
            params=params,
            page_size=page_size,
            limit=limit,
        )

    def get(self, record_id: int) -> R:
        """One record by id (``NotFoundError`` when it does not exist or is not public)."""
        data = self._client._request(self._get, record_id=record_id).data
        return self._model.model_validate(data)


class InteractionRecords(_Records[InteractionRecord]):
    """``client.evidence.interactions``: every public interaction as a flat record.

    ``.list(**filters)`` pages ``GET /interactions`` (ordered by id; 500 a page at
    most); ``.get(id)`` reads one. Called with a protein accession,
    ``client.evidence.interactions("PBFCTW")`` is the older per-protein call
    (:meth:`Proteins.interactions`: nested experiments and observations).
    """

    _list = _INTERACTION_LIST
    _get = _INTERACTION_GET
    _model = InteractionRecord
    _schema = EvidenceListFilters
    _name = "interactions"

    def list(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[EvidenceListFilters],
    ) -> Collection[InteractionRecord]:
        """Interactions matching the filters, as flat records (pinnable)."""
        return self._collection(dict(filters), page_size, limit)

    def __call__(
        self,
        protein: str,
        *,
        evidence_level: EvidenceLevel | None = None,
        include_sequenceless: bool = False,
        page_size: int | None = None,
        limit: int | None = None,
        kind: EvidenceLevel | None = None,
    ) -> InteractionCollection[Interaction]:
        """A protein's interactions, each with its experiments and observations."""
        return self._client.proteins.interactions(
            protein,
            evidence_level=evidence_level,
            include_sequenceless=include_sequenceless,
            page_size=page_size,
            limit=limit,
            kind=kind,
        )


class ExperimentRecords(_Records[ExperimentRecord]):
    """``client.evidence.experiments``: ``.list(**filters)`` and ``.get(id)``."""

    _list = _EXPERIMENT_LIST
    _get = _EXPERIMENT_GET
    _model = ExperimentRecord
    _schema = ExperimentListFilters
    _name = "experiments"

    def list(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[ExperimentListFilters],
    ) -> Collection[ExperimentRecord]:
        """Experiments matching the filters (``interaction_id`` narrows to one interaction)."""
        return self._collection(dict(filters), page_size, limit)


class ObservationRecords(_Records[ObservationRecord]):
    """``client.evidence.observations``: ``.list(**filters)`` and ``.get(id)``."""

    _list = _OBSERVATION_LIST
    _get = _OBSERVATION_GET
    _model = ObservationRecord
    _schema = ObservationListFilters
    _name = "observations"

    def list(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[ObservationListFilters],
    ) -> Collection[ObservationRecord]:
        """Measured values matching the filters (``interaction_id``, ``experiment_id``)."""
        return self._collection(dict(filters), page_size, limit)


class Evidence(Resource):
    """The evidence behind the catalogue.

    Evidence is organised interaction -> experiment -> observation (see
    :class:`~plasticdb.models.Interaction`). Three namespaces list it flat, across
    the whole database: ``evidence.interactions``, ``evidence.experiments`` and
    ``evidence.observations`` (each ``.list(**filters)`` and ``.get(id)``; records
    link by ``interaction_id`` / ``experiment_id``). ``evidence.interactions(protein)``
    is the per-protein call with the children nested;
    ``.to_pandas(level="observation")`` of it gives one row per measured value.
    The material tables describe what was assayed: constructs (sequences as
    expressed), preparations (host, purification, tags) and binding assays.
    """

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.interactions = InteractionRecords(client)
        self.experiments = ExperimentRecords(client)
        self.observations = ObservationRecords(client)

    def kinetics(
        self, protein: str, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[Kinetics]:
        """Kinetic parameters of a protein and its variants."""
        return self._client.proteins.kinetics(protein, page_size=page_size, limit=limit)

    def properties(
        self, protein: str, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[Properties]:
        """Stability and activity properties of a protein and its variants."""
        return self._client.proteins.properties(protein, page_size=page_size, limit=limit)

    def binding_assays(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[BindingAssayFilters],
    ) -> Collection[BindingAssay]:
        """Binding (adsorption) assays, filtered by protein, paper or plastic."""
        params = check(filters, BindingAssayFilters, "evidence.binding_assays")
        return Collection(
            self._client,
            _BINDING_LIST,
            BindingAssay,
            params=params,
            page_size=page_size,
            limit=limit,
        )

    def binding_assay(self, assay_id: int) -> BindingAssay:
        data = self._client._request(_BINDING_GET, assay_id=assay_id).data
        return BindingAssay.model_validate(data)

    def constructs(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[MaterialFilters],
    ) -> Collection[Construct]:
        """Expressed constructs (they carry sequences: ``.to_fasta()`` works)."""
        params = check(filters, MaterialFilters, "evidence.constructs")
        return Collection(
            self._client,
            _CONSTRUCT_LIST,
            Construct,
            params=params,
            page_size=page_size,
            limit=limit,
        )

    def construct(self, construct_id: int) -> Construct:
        data = self._client._request(_CONSTRUCT_GET, construct_id=construct_id).data
        return Construct.model_validate(data)

    def construct_structures(self, construct_id: int) -> builtins.list[StructureLink]:
        """The structures linked to one construct."""
        data = self._client._request(_CONSTRUCT_STRUCTURES, construct_id=construct_id).data
        return [StructureLink.model_validate(item) for item in _items(data)]

    def preparations(
        self,
        *,
        page_size: int | None = None,
        limit: int | None = None,
        **filters: Unpack[MaterialFilters],
    ) -> Collection[Preparation]:
        """Prepared materials (expression host, purification, tags, modifications)."""
        params = check(filters, MaterialFilters, "evidence.preparations")
        return Collection(
            self._client,
            _PREPARATION_LIST,
            Preparation,
            params=params,
            page_size=page_size,
            limit=limit,
        )

    def preparation(self, preparation_id: int) -> Preparation:
        data = self._client._request(_PREPARATION_GET, preparation_id=preparation_id).data
        return Preparation.model_validate(data)

    def experiment_preparations(self, experiment_id: int) -> builtins.list[Preparation]:
        """The preparations one experiment assayed."""
        data = self._client._request(_EXPERIMENT_PREPARATIONS, experiment_id=experiment_id).data
        return [Preparation.model_validate(item) for item in _items(data)]


def _items(data: Any) -> builtins.list[Any]:
    if isinstance(data, dict):
        return list(data.get("items") or [])
    return list(data or [])


class Search(Resource):
    """Full-text search across every record type (live only).

    ``client.search("PETase")`` returns ranked hits. A past release cannot be
    searched: a pinned client gets :class:`~plasticdb.errors.LiveOnlyError`.
    """

    def __call__(
        self, q: str, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[SearchHit]:
        return Collection(
            self._client,
            _SEARCH,
            SearchHit,
            params={"q": q},
            page_size=page_size or 20,
            limit=limit,
        )

    def grouped(self, q: str, *, limit: int = 10) -> GroupedSearch:
        """The top ``limit`` hits (at most 100) for each record type."""
        data = self._client._request(_SEARCH_GROUPED, params={"q": q, "limit": limit}).data
        return GroupedSearch.model_validate(data)

    def autocomplete(self, q: str) -> dict[str, list[SearchHit]]:
        """Name suggestions per record type, as the site's search box shows them."""
        data = self._client._request(_AUTOCOMPLETE, params={"q": q}).data or {}
        return {
            kind: [SearchHit.model_validate({"entity_type": kind, **hit}) for hit in hits]
            for kind, hits in data.items()
            if isinstance(hits, list)
        }


class Pathways(Resource):
    """Curated degradation pathways."""

    def list(
        self, *, page_size: int | None = None, limit: int | None = None
    ) -> Collection[PathwaySummary]:
        return Collection(
            self._client, _PATHWAY_LIST, PathwaySummary, page_size=page_size, limit=limit
        )

    def get(self, pathway_id: int) -> Pathway:
        """One pathway with its ordered reaction steps."""
        data = self._client._request(_PATHWAY_GET, pathway_id=pathway_id).data
        return Pathway.model_validate(data)

    def for_plastic(self, plastic_id: int) -> builtins.list[Pathway]:
        """Every pathway that degrades one plastic."""
        data = self._client._request(_PATHWAYS_FOR_PLASTIC, plastic_id=plastic_id).data
        return [Pathway.model_validate(item) for item in data or []]

    def graph(self, plastic_id: int, *, pathway_id: int | None = None) -> PathwayGraph:
        """A plastic's pathways as a graph of compounds and reactions."""
        data = self._client._request(
            _PATHWAY_GRAPH, params={"pathway_id": pathway_id}, plastic_id=plastic_id
        ).data
        return PathwayGraph.model_validate(data)


class StatsResource(Resource):
    """Summary statistics of the live database (live only)."""

    def summary(self, *, evidence_scope: EvidenceScope = "organism_or_enzyme") -> Stats:
        """Headline counts: proteins, microorganisms, plastics, papers, pathways.

        ``evidence_scope`` decides which organisms count (``enzyme_only``: those
        credited only through an enzyme).
        """
        data = self._client._request(_STATS, params={"evidence_scope": evidence_scope}).data
        return Stats.model_validate(data)

    def protein_activity(self) -> ProteinActivityStats:
        """How many proteins degrade, fail to degrade, or are untested."""
        return ProteinActivityStats.model_validate(self._client._request(_STATS_ACTIVITY).data)

    def charts(self, *, evidence_scope: RowEvidenceScope = "organism_or_enzyme") -> dict[str, Any]:
        """The data behind the site's statistics charts (no ``enzyme_only``: see the API docs)."""
        data = self._client._request(_STATS_CHARTS, params={"evidence_scope": evidence_scope})
        return dict(data.data or {})

    def by_country(self) -> dict[str, Any]:
        """Record counts by the corresponding author's country."""
        return dict(self._client._request(_STATS_COUNTRY).data or {})
