import uuid
import warnings
from itertools import tee
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union, get_args, Set

import numpy as np

from qdrant_client import grpc
from qdrant_client.client_base import QdrantBase
from qdrant_client.conversions import common_types as types
from qdrant_client.conversions.conversion import GrpcToRest
from qdrant_client.embed.models import Document
from qdrant_client.fastembed_common import QueryResponse
from qdrant_client.http import models
from qdrant_client.hybrid.fusion import reciprocal_rank_fusion
from qdrant_client.uploader.uploader import iter_batch

try:
    from fastembed import SparseTextEmbedding, TextEmbedding
    from fastembed.common import OnnxProvider
except ImportError:
    TextEmbedding = None
    SparseTextEmbedding = None
    OnnxProvider = None


SUPPORTED_EMBEDDING_MODELS: Dict[str, Tuple[int, models.Distance]] = (
    {
        model["model"]: (model["dim"], models.Distance.COSINE)
        for model in TextEmbedding.list_supported_models()
    }
    if TextEmbedding
    else {}
)

SUPPORTED_SPARSE_EMBEDDING_MODELS: Dict[str, Tuple[int, models.Distance]] = (
    {model["model"]: model for model in SparseTextEmbedding.list_supported_models()}
    if SparseTextEmbedding
    else {}
)

IDF_EMBEDDING_MODELS: Set[str] = (
    {
        model_config["model"]
        for model_config in SparseTextEmbedding.list_supported_models()
        if model_config.get("requires_idf", None)
    }
    if SparseTextEmbedding
    else set()
)


class QdrantFastembedMixin(QdrantBase):
    DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en"

    embedding_models: Dict[str, "TextEmbedding"] = {}
    sparse_embedding_models: Dict[str, "SparseTextEmbedding"] = {}

    _FASTEMBED_INSTALLED: bool

    def __init__(self, **kwargs: Any):
        self._embedding_model_name: Optional[str] = None
        self._sparse_embedding_model_name: Optional[str] = None
        try:
            from fastembed import SparseTextEmbedding, TextEmbedding

            assert len(SparseTextEmbedding.list_supported_models()) > 0
            assert len(TextEmbedding.list_supported_models()) > 0

            self.__class__._FASTEMBED_INSTALLED = True
        except ImportError:
            self.__class__._FASTEMBED_INSTALLED = False

        super().__init__(**kwargs)

    @property
    def embedding_model_name(self) -> str:
        if self._embedding_model_name is None:
            self._embedding_model_name = self.DEFAULT_EMBEDDING_MODEL
        return self._embedding_model_name

    @property
    def sparse_embedding_model_name(self) -> Optional[str]:
        return self._sparse_embedding_model_name

    def set_model(
        self,
        embedding_model_name: str,
        max_length: Optional[int] = None,
        cache_dir: Optional[str] = None,
        threads: Optional[int] = None,
        providers: Optional[Sequence["OnnxProvider"]] = None,
        **kwargs: Any,
    ) -> None:
        """
        Set embedding model to use for encoding documents and queries.
        Args:
            embedding_model_name: One of the supported embedding models. See `SUPPORTED_EMBEDDING_MODELS` for details.
            max_length (int, optional): Deprecated. Defaults to None.
            cache_dir (str, optional): The path to the cache directory.
                                       Can be set using the `FASTEMBED_CACHE_PATH` env variable.
                                       Defaults to `fastembed_cache` in the system's temp directory.
            threads (int, optional): The number of threads single onnxruntime session can use. Defaults to None.
            providers: The list of onnx providers (with or without options) to use. Defaults to None.
                Example configuration:
                https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#configuration-options
        Raises:
            ValueError: If embedding model is not supported.
            ImportError: If fastembed is not installed.

        Returns:
            None
        """

        if max_length is not None:
            warnings.warn(
                "max_length parameter is deprecated and will be removed in the future. "
                "It's not used by fastembed models.",
                DeprecationWarning,
                stacklevel=2,
            )

        self._get_or_init_model(
            model_name=embedding_model_name,
            cache_dir=cache_dir,
            threads=threads,
            providers=providers,
            **kwargs,
        )
        self._embedding_model_name = embedding_model_name

    def set_sparse_model(
        self,
        embedding_model_name: Optional[str],
        cache_dir: Optional[str] = None,
        threads: Optional[int] = None,
        providers: Optional[Sequence["OnnxProvider"]] = None,
    ) -> None:
        """
        Set sparse embedding model to use for hybrid search over documents in combination with dense embeddings.
        Args:
            embedding_model_name: One of the supported sparse embedding models. See `SUPPORTED_SPARSE_EMBEDDING_MODELS` for details.
                        If None, sparse embeddings will not be used.
            cache_dir (str, optional): The path to the cache directory.
                                       Can be set using the `FASTEMBED_CACHE_PATH` env variable.
                                       Defaults to `fastembed_cache` in the system's temp directory.
            threads (int, optional): The number of threads single onnxruntime session can use. Defaults to None.
            providers: The list of onnx providers (with or without options) to use. Defaults to None.
                Example configuration:
                https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#configuration-options
        Raises:
            ValueError: If embedding model is not supported.
            ImportError: If fastembed is not installed.

        Returns:
            None
        """
        if embedding_model_name is not None:
            self._get_or_init_sparse_model(
                model_name=embedding_model_name,
                cache_dir=cache_dir,
                threads=threads,
                providers=providers,
            )
        self._sparse_embedding_model_name = embedding_model_name

    @classmethod
    def _import_fastembed(cls) -> None:
        if cls._FASTEMBED_INSTALLED:
            return

        # If it's not, ask the user to install it
        raise ImportError(
            "fastembed is not installed."
            " Please install it to enable fast vector indexing with `pip install fastembed`."
        )

    @classmethod
    def _get_model_params(cls, model_name: str) -> Tuple[int, models.Distance]:
        cls._import_fastembed()

        if model_name not in SUPPORTED_EMBEDDING_MODELS:
            raise ValueError(
                f"Unsupported embedding model: {model_name}. Supported models: {SUPPORTED_EMBEDDING_MODELS}"
            )

        return SUPPORTED_EMBEDDING_MODELS[model_name]

    @classmethod
    def _get_or_init_model(
        cls,
        model_name: str,
        cache_dir: Optional[str] = None,
        threads: Optional[int] = None,
        providers: Optional[Sequence["OnnxProvider"]] = None,
        **kwargs: Any,
    ) -> "TextEmbedding":
        if model_name in cls.embedding_models:
            return cls.embedding_models[model_name]

        cls._import_fastembed()

        if model_name not in SUPPORTED_EMBEDDING_MODELS:
            raise ValueError(
                f"Unsupported embedding model: {model_name}. Supported models: {SUPPORTED_EMBEDDING_MODELS}"
            )

        cls.embedding_models[model_name] = TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
            threads=threads,
            providers=providers,
            **kwargs,
        )
        return cls.embedding_models[model_name]

    @classmethod
    def _get_or_init_sparse_model(
        cls,
        model_name: str,
        cache_dir: Optional[str] = None,
        threads: Optional[int] = None,
        providers: Optional[Sequence["OnnxProvider"]] = None,
        **kwargs: Any,
    ) -> "SparseTextEmbedding":
        if model_name in cls.sparse_embedding_models:
            return cls.sparse_embedding_models[model_name]

        cls._import_fastembed()

        if model_name not in SUPPORTED_SPARSE_EMBEDDING_MODELS:
            raise ValueError(
                f"Unsupported embedding model: {model_name}. Supported models: {SUPPORTED_SPARSE_EMBEDDING_MODELS}"
            )

        cls.sparse_embedding_models[model_name] = SparseTextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
            threads=threads,
            providers=providers,
            **kwargs,
        )
        return cls.sparse_embedding_models[model_name]

    def _embed_documents(
        self,
        documents: Iterable[str],
        embedding_model_name: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = 32,
        embed_type: str = "default",
        parallel: Optional[int] = None,
    ) -> Iterable[Tuple[str, List[float]]]:
        embedding_model = self._get_or_init_model(model_name=embedding_model_name)
        documents_a, documents_b = tee(documents, 2)
        if embed_type == "passage":
            vectors_iter = embedding_model.passage_embed(
                documents_a, batch_size=batch_size, parallel=parallel
            )
        elif embed_type == "query":
            vectors_iter = (
                list(embedding_model.query_embed(query=query))[0] for query in documents_a
            )
        elif embed_type == "default":
            vectors_iter = embedding_model.embed(
                documents_a, batch_size=batch_size, parallel=parallel
            )
        else:
            raise ValueError(f"Unknown embed type: {embed_type}")

        for vector, doc in zip(vectors_iter, documents_b):
            yield doc, vector.tolist()

    def _sparse_embed_documents(
        self,
        documents: Iterable[str],
        embedding_model_name: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = 32,
        parallel: Optional[int] = None,
    ) -> Iterable[types.SparseVector]:
        sparse_embedding_model = self._get_or_init_sparse_model(model_name=embedding_model_name)

        vectors_iter = sparse_embedding_model.embed(
            documents, batch_size=batch_size, parallel=parallel
        )

        for sparse_vector in vectors_iter:
            yield types.SparseVector(
                indices=sparse_vector.indices.tolist(),
                values=sparse_vector.values.tolist(),
            )

    def get_vector_field_name(self) -> str:
        """
        Returns name of the vector field in qdrant collection, used by current fastembed model.
        Returns:
            Name of the vector field.
        """
        model_name = self.embedding_model_name.split("/")[-1].lower()
        return f"fast-{model_name}"

    def get_sparse_vector_field_name(self) -> Optional[str]:
        """
        Returns name of the vector field in qdrant collection, used by current fastembed model.
        Returns:
            Name of the vector field.
        """
        if self.sparse_embedding_model_name is not None:
            model_name = self.sparse_embedding_model_name.split("/")[-1].lower()
            return f"fast-sparse-{model_name}"
        return None

    def _scored_points_to_query_responses(
        self,
        scored_points: List[types.ScoredPoint],
    ) -> List[QueryResponse]:
        response = []
        vector_field_name = self.get_vector_field_name()
        sparse_vector_field_name = self.get_sparse_vector_field_name()

        for scored_point in scored_points:
            embedding = (
                scored_point.vector.get(vector_field_name, None)
                if isinstance(scored_point.vector, Dict)
                else None
            )
            sparse_embedding = None
            if sparse_vector_field_name is not None:
                sparse_embedding = (
                    scored_point.vector.get(sparse_vector_field_name, None)
                    if isinstance(scored_point.vector, Dict)
                    else None
                )

            response.append(
                QueryResponse(
                    id=scored_point.id,
                    embedding=embedding,
                    sparse_embedding=sparse_embedding,
                    metadata=scored_point.payload,
                    document=scored_point.payload.get("document", ""),
                    score=scored_point.score,
                )
            )
        return response

    def _points_iterator(
        self,
        ids: Optional[Iterable[models.ExtendedPointId]],
        metadata: Optional[Iterable[Dict[str, Any]]],
        encoded_docs: Iterable[Tuple[str, List[float]]],
        ids_accumulator: list,
        sparse_vectors: Optional[Iterable[types.SparseVector]] = None,
    ) -> Iterable[models.PointStruct]:
        if ids is None:
            ids = iter(lambda: uuid.uuid4().hex, None)

        if metadata is None:
            metadata = iter(lambda: {}, None)

        if sparse_vectors is None:
            sparse_vectors = iter(lambda: None, True)

        vector_name = self.get_vector_field_name()
        sparse_vector_name = self.get_sparse_vector_field_name()

        for idx, meta, (doc, vector), sparse_vector in zip(
            ids, metadata, encoded_docs, sparse_vectors
        ):
            ids_accumulator.append(idx)
            payload = {"document": doc, **meta}
            point_vector: Dict[str, models.Vector] = {vector_name: vector}
            if sparse_vector_name is not None and sparse_vector is not None:
                point_vector[sparse_vector_name] = sparse_vector
            yield models.PointStruct(id=idx, payload=payload, vector=point_vector)

    def _validate_collection_info(self, collection_info: models.CollectionInfo) -> None:
        embeddings_size, distance = self._get_model_params(model_name=self.embedding_model_name)
        vector_field_name = self.get_vector_field_name()

        # Check if collection has compatible vector params
        assert isinstance(
            collection_info.config.params.vectors, dict
        ), f"Collection have incompatible vector params: {collection_info.config.params.vectors}"

        assert (
            vector_field_name in collection_info.config.params.vectors
        ), f"Collection have incompatible vector params: {collection_info.config.params.vectors}, expected {vector_field_name}"

        vector_params = collection_info.config.params.vectors[vector_field_name]

        assert (
            embeddings_size == vector_params.size
        ), f"Embedding size mismatch: {embeddings_size} != {vector_params.size}"

        assert (
            distance == vector_params.distance
        ), f"Distance mismatch: {distance} != {vector_params.distance}"

        sparse_vector_field_name = self.get_sparse_vector_field_name()
        if sparse_vector_field_name is not None:
            assert (
                sparse_vector_field_name in collection_info.config.params.sparse_vectors
            ), f"Collection have incompatible vector params: {collection_info.config.params.vectors}"
            if self.sparse_embedding_model_name in IDF_EMBEDDING_MODELS:
                modifier = collection_info.config.params.sparse_vectors[
                    sparse_vector_field_name
                ].modifier
                assert (
                    modifier == models.Modifier.IDF
                ), f"{self.sparse_embedding_model_name} requires modifier IDF, current modifier is {modifier}"

    def get_fastembed_vector_params(
        self,
        on_disk: Optional[bool] = None,
        quantization_config: Optional[models.QuantizationConfig] = None,
        hnsw_config: Optional[models.HnswConfigDiff] = None,
    ) -> Dict[str, models.VectorParams]:
        """
        Generates vector configuration, compatible with fastembed models.

        Args:
            on_disk: if True, vectors will be stored on disk. If None, default value will be used.
            quantization_config: Quantization configuration. If None, quantization will be disabled.
            hnsw_config: HNSW configuration. If None, default configuration will be used.

        Returns:
            Configuration for `vectors_config` argument in `create_collection` method.
        """
        vector_field_name = self.get_vector_field_name()
        embeddings_size, distance = self._get_model_params(model_name=self.embedding_model_name)
        return {
            vector_field_name: models.VectorParams(
                size=embeddings_size,
                distance=distance,
                on_disk=on_disk,
                quantization_config=quantization_config,
                hnsw_config=hnsw_config,
            )
        }

    def get_fastembed_sparse_vector_params(
        self,
        on_disk: Optional[bool] = None,
        modifier: Optional[models.Modifier] = None,
    ) -> Optional[Dict[str, models.SparseVectorParams]]:
        """
        Generates vector configuration, compatible with fastembed sparse models.

        Args:
            on_disk: if True, vectors will be stored on disk. If None, default value will be used.
            modifier: Sparse vector queries modifier. E.g. Modifier.IDF for idf-based rescoring. Default: None.
        Returns:
            Configuration for `vectors_config` argument in `create_collection` method.
        """
        vector_field_name = self.get_sparse_vector_field_name()
        if self.sparse_embedding_model_name in IDF_EMBEDDING_MODELS:
            modifier = models.Modifier.IDF if modifier is None else modifier

        if vector_field_name is None:
            return None

        return {
            vector_field_name: models.SparseVectorParams(
                index=models.SparseIndexParams(
                    on_disk=on_disk,
                ),
                modifier=modifier,
            )
        }

    def add(
        self,
        collection_name: str,
        documents: Iterable[str],
        metadata: Optional[Iterable[Dict[str, Any]]] = None,
        ids: Optional[Iterable[models.ExtendedPointId]] = None,
        batch_size: int = 32,
        parallel: Optional[int] = None,
        **kwargs: Any,
    ) -> List[Union[str, int]]:
        """
        Adds text documents into qdrant collection.
        If collection does not exist, it will be created with default parameters.
        Metadata in combination with documents will be added as payload.
        Documents will be embedded using the specified embedding model.

        If you want to use your own vectors, use `upsert` method instead.

        Args:
            collection_name (str):
                Name of the collection to add documents to.
            documents (Iterable[str]):
                List of documents to embed and add to the collection.
            metadata (Iterable[Dict[str, Any]], optional):
                List of metadata dicts. Defaults to None.
            ids (Iterable[models.ExtendedPointId], optional):
                List of ids to assign to documents.
                If not specified, UUIDs will be generated. Defaults to None.
            batch_size (int, optional):
                How many documents to embed and upload in single request. Defaults to 32.
            parallel (Optional[int], optional):
                How many parallel workers to use for embedding. Defaults to None.
                If number is specified, data-parallel process will be used.

        Raises:
            ImportError: If fastembed is not installed.

        Returns:
            List of IDs of added documents. If no ids provided, UUIDs will be randomly generated on client side.

        """

        # check if we have fastembed installed
        encoded_docs = self._embed_documents(
            documents=documents,
            embedding_model_name=self.embedding_model_name,
            batch_size=batch_size,
            embed_type="passage",
            parallel=parallel,
        )

        encoded_sparse_docs = None
        if self.sparse_embedding_model_name is not None:
            encoded_sparse_docs = self._sparse_embed_documents(
                documents=documents,
                embedding_model_name=self.sparse_embedding_model_name,
                batch_size=batch_size,
                parallel=parallel,
            )

        # Check if collection by same name exists, if not, create it
        try:
            collection_info = self.get_collection(collection_name=collection_name)
        except Exception:
            self.create_collection(
                collection_name=collection_name,
                vectors_config=self.get_fastembed_vector_params(),
                sparse_vectors_config=self.get_fastembed_sparse_vector_params(),
            )
            collection_info = self.get_collection(collection_name=collection_name)

        self._validate_collection_info(collection_info)

        inserted_ids: list = []

        points = self._points_iterator(
            ids=ids,
            metadata=metadata,
            encoded_docs=encoded_docs,
            ids_accumulator=inserted_ids,
            sparse_vectors=encoded_sparse_docs,
        )

        self.upload_points(
            collection_name=collection_name,
            points=points,
            wait=True,
            parallel=parallel or 1,
            batch_size=batch_size,
            **kwargs,
        )

        return inserted_ids

    def _embed_raw_data(
        self,
        data: Document,
    ) -> Union[List[float], models.SparseVector]:
        if isinstance(data, Document):
            return self._embed_document(data)
        raise ValueError(f"Unsupported type: {type(data)}")

    def _embed_document(self, document: Document) -> Union[List[float], models.SparseVector]:
        model_name = document.model
        text = document.text
        if model_name in SUPPORTED_EMBEDDING_MODELS:
            self.set_model(model_name)
            embedding_model_inst = self._get_or_init_model(model_name=model_name)
            return list(embedding_model_inst.embed(documents=[text]))[0].tolist()
        elif model_name in SUPPORTED_SPARSE_EMBEDDING_MODELS:
            self.set_sparse_model(model_name)
            sparse_embedding_model_inst = self._get_or_init_sparse_model(model_name=model_name)
            sparse_embedding = list(sparse_embedding_model_inst.embed(documents=[text]))[0]
            return models.SparseVector(
                indices=sparse_embedding.indices.tolist(), values=sparse_embedding.values.tolist()
            )
        else:
            raise ValueError(f"{model_name} is not among supported models")

    def _embed_query_raw_types(
        self,
        query: Union[
            types.PointId,
            List[float],
            List[List[float]],
            types.SparseVector,
            types.Query,
            types.NumpyArray,
            Document,
            None,
        ],
    ) -> models.Query:
        def _embed_context(
            context: Union[List[models.ContextPair], models.ContextPair],
        ) -> Union[List[models.ContextPair], models.ContextPair]:
            if isinstance(context, list):
                for i, context_pair in enumerate(context):
                    if isinstance(context_pair.positive, types.Document):
                        context[i].positive = self._embed_raw_data(context_pair.positive)
                    if isinstance(context_pair.negative, types.Document):
                        context[i].negative = self._embed_raw_data(context_pair.negative)
            else:
                if isinstance(context.positive, types.Document):
                    context.positive = self._embed_raw_data(context.positive)
                if isinstance(context.negative, types.Document):
                    context.negative = self._embed_raw_data(context.negative)
            return context

        if isinstance(query, Document):
            query = models.NearestQuery(nearest=self._embed_raw_data(query))

        elif isinstance(query, models.NearestQuery):
            if isinstance(query.nearest, models.Document):
                query.nearest = self._embed_raw_data(query.nearest)

        elif isinstance(query, models.RecommendQuery):
            positives = query.recommend.positive or []
            for i, positive in enumerate(positives):
                if isinstance(positive, types.Document):
                    positives[i] = self._embed_raw_data(positive)

            negatives = query.recommend.negative or []
            for i, negative in enumerate(negatives):
                if isinstance(negative, types.Document):
                    negatives[i] = self._embed_raw_data(negative)

        elif isinstance(query, models.DiscoverQuery):
            if isinstance(query.discover.target, types.Document):
                query.discover.target = self._embed_raw_data(query.discover.target)

            query.discover.context = _embed_context(query.discover.context)

        elif isinstance(query, models.ContextQuery):
            query.context = _embed_context(query.context)

        return query

    def _embed_prefetch_raw_types(
        self, prefetch: Union[types.Prefetch, List[types.Prefetch], None] = None
    ) -> Union[types.Prefetch, List[types.Prefetch], None]:
        if isinstance(prefetch, types.Prefetch):
            prefetch.query = self._embed_query_raw_types(prefetch.query)
            prefetch.prefetch = self._embed_prefetch_raw_types(prefetch.prefetch)
        elif isinstance(prefetch, list):
            prefetch = [
                self._embed_prefetch_raw_types(single_prefetch) for single_prefetch in prefetch
            ]
        return prefetch

    def _embed_raw_points(
        self, points: Union[models.Batch, List[models.PointStruct]]
    ) -> Union[models.Batch, List[models.PointStruct]]:
        def contains_documents(docs: models.BatchVectorStruct) -> bool:
            if isinstance(docs, dict):
                # check if inner iter is not empty
                inner = next(iter(docs.values()))
                if len(inner):
                    return isinstance(next(iter(inner)), models.Document)
            return isinstance(next(iter(docs)), models.Document)

        if isinstance(points, models.Batch):
            ids, documents, payloads = points.ids, points.vectors, points.payloads
            if not contains_documents(documents):
                return points

            embeddings = []
            if isinstance(documents, dict):
                keys = list(documents.keys())
                lengths = {len(documents[key]) for key in keys}

                if len(lengths) > 1:
                    raise ValueError(
                        "All named vectors in batch should contain the same number of vectors"
                    )

                batch_size = lengths.pop()
                for i in range(batch_size):
                    embedding = {}
                    for key in keys:
                        document = documents[key][i]
                        embedding[key] = (
                            self._embed_raw_data(document)
                            if isinstance(document, models.Document)
                            else document
                        )
                    embeddings.append(embedding)
            else:
                for document in documents:
                    if isinstance(document, models.Document):
                        if document.model in SUPPORTED_SPARSE_EMBEDDING_MODELS:
                            raise TypeError("Sparse vectors must be provided in dict")
                    embeddings.append(
                        self._embed_raw_data(document)
                        if isinstance(document, models.Document)
                        else document
                    )
            return models.Batch(ids=ids, vectors=embeddings, payloads=payloads)

        updated_points = []
        for point in points:
            vector = point.vector
            if isinstance(vector, models.Document):
                if vector.model in SUPPORTED_SPARSE_EMBEDDING_MODELS:
                    raise TypeError("Sparse vectors must be provided in dict")
                updated_points.append(
                    models.PointStruct(
                        id=point.id,
                        payload=point.payload,
                        vector=(
                            self._embed_raw_data(vector)
                            if isinstance(vector, models.Document)
                            else vector
                        ),
                    )
                )
            if isinstance(vector, Dict):
                embedding = {}
                for key in vector.keys():
                    current_vector = vector[key]
                    embedding[key] = (
                        self._embed_raw_data(current_vector)
                        if isinstance(current_vector, models.Document)
                        else current_vector
                    )
                updated_points.append(
                    models.PointStruct(id=point.id, payload=point.payload, vector=embedding)
                )
        return updated_points

    def _embed_raw_point_vectors(
        self, points: Sequence[types.PointVectors]
    ) -> List[types.PointVectors]:
        return [
            types.PointVectors(
                id=point.id,
                vector=(
                    self._embed_raw_data(point.vector)
                    if isinstance(point.vector, Document)
                    else point.vector
                ),
            )
            for point in points
        ]

    def _embed_query_points_requests(
        self, requests: Sequence[types.QueryRequest]
    ) -> Sequence[types.QueryRequest]:
        for request in requests:
            request.query = self._embed_query_raw_types(request.query)
            request.prefetch = self._embed_prefetch_raw_types(request.prefetch)
        return requests

    def _embed_update_operations(
        self, update_operations: Sequence[types.UpdateOperation]
    ) -> Sequence[types.UpdateOperation]:
        for update_operation in update_operations:
            if isinstance(update_operation, models.UpsertOperation):
                operation = update_operation.upsert
                if isinstance(operation, models.PointsBatch):
                    operation.batch = self._embed_raw_points(operation.batch)
                else:
                    operation.points = self._embed_raw_points(operation.points)
            elif isinstance(update_operation, models.UpdateVectorsOperation):
                operation = update_operation.update_vectors
                operation.points = self._embed_raw_point_vectors(operation.points)

        return update_operations

    def query(
        self,
        collection_name: str,
        query_text: str,
        query_filter: Optional[models.Filter] = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> List[QueryResponse]:
        """
        Search for documents in a collection.
        This method automatically embeds the query text using the specified embedding model.
        If you want to use your own query vector, use `search` method instead.

        Args:
            collection_name: Collection to search in
            query_text:
                Text to search for. This text will be embedded using the specified embedding model.
                And then used as a query vector.
            query_filter:
                - Exclude vectors which doesn't fit given conditions.
                - If `None` - search among all vectors
            limit: How many results return
            **kwargs: Additional search parameters. See `qdrant_client.models.SearchRequest` for details.

        Returns:
            List[types.ScoredPoint]: List of scored points.

        """

        embedding_model_inst = self._get_or_init_model(model_name=self.embedding_model_name)
        embeddings = list(embedding_model_inst.query_embed(query=query_text))
        query_vector = embeddings[0].tolist()

        if self.sparse_embedding_model_name is None:
            return self._scored_points_to_query_responses(
                self.search(
                    collection_name=collection_name,
                    query_vector=models.NamedVector(
                        name=self.get_vector_field_name(), vector=query_vector
                    ),
                    query_filter=query_filter,
                    limit=limit,
                    with_payload=True,
                    **kwargs,
                )
            )

        sparse_embedding_model_inst = self._get_or_init_sparse_model(
            model_name=self.sparse_embedding_model_name
        )
        sparse_vector = list(sparse_embedding_model_inst.embed(documents=query_text))[0]
        sparse_query_vector = models.SparseVector(
            indices=sparse_vector.indices.tolist(),
            values=sparse_vector.values.tolist(),
        )

        dense_request = models.SearchRequest(
            vector=models.NamedVector(
                name=self.get_vector_field_name(),
                vector=query_vector,
            ),
            filter=query_filter,
            limit=limit,
            with_payload=True,
            **kwargs,
        )
        sparse_request = models.SearchRequest(
            vector=models.NamedSparseVector(
                name=self.get_sparse_vector_field_name(),
                vector=sparse_query_vector,
            ),
            filter=query_filter,
            limit=limit,
            with_payload=True,
            **kwargs,
        )

        dense_request_response, sparse_request_response = self.search_batch(
            collection_name=collection_name, requests=[dense_request, sparse_request]
        )
        return self._scored_points_to_query_responses(
            reciprocal_rank_fusion([dense_request_response, sparse_request_response], limit=limit)
        )

    def query_batch(
        self,
        collection_name: str,
        query_texts: List[str],
        query_filter: Optional[models.Filter] = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> List[List[QueryResponse]]:
        """
        Search for documents in a collection with batched query.
        This method automatically embeds the query text using the specified embedding model.

        Args:
            collection_name: Collection to search in
            query_texts:
                A list of texts to search for. Each text will be embedded using the specified embedding model.
                And then used as a query vector for a separate search requests.
            query_filter:
                - Exclude vectors which doesn't fit given conditions.
                - If `None` - search among all vectors
                This filter will be applied to all search requests.
            limit: How many results return
            **kwargs: Additional search parameters. See `qdrant_client.models.SearchRequest` for details.

        Returns:
            List[List[QueryResponse]]: List of lists of responses for each query text.

        """
        embedding_model_inst = self._get_or_init_model(model_name=self.embedding_model_name)
        query_vectors = list(embedding_model_inst.query_embed(query=query_texts))
        requests = []
        for vector in query_vectors:
            request = models.SearchRequest(
                vector=models.NamedVector(
                    name=self.get_vector_field_name(), vector=vector.tolist()
                ),
                filter=query_filter,
                limit=limit,
                with_payload=True,
                **kwargs,
            )

            requests.append(request)

        if self.sparse_embedding_model_name is None:
            responses = self.search_batch(
                collection_name=collection_name,
                requests=requests,
            )
            return [self._scored_points_to_query_responses(response) for response in responses]

        sparse_embedding_model_inst = self._get_or_init_sparse_model(
            model_name=self.sparse_embedding_model_name
        )
        sparse_query_vectors = [
            models.SparseVector(
                indices=sparse_vector.indices.tolist(),
                values=sparse_vector.values.tolist(),
            )
            for sparse_vector in sparse_embedding_model_inst.embed(documents=query_texts)
        ]
        for sparse_vector in sparse_query_vectors:
            request = models.SearchRequest(
                vector=models.NamedSparseVector(
                    name=self.get_sparse_vector_field_name(),
                    vector=sparse_vector,
                ),
                filter=query_filter,
                limit=limit,
                with_payload=True,
                **kwargs,
            )

            requests.append(request)

        responses = self.search_batch(
            collection_name=collection_name,
            requests=requests,
        )

        dense_responses = responses[: len(query_texts)]
        sparse_responses = responses[len(query_texts) :]
        responses = [
            reciprocal_rank_fusion([dense_response, sparse_response], limit=limit)
            for dense_response, sparse_response in zip(dense_responses, sparse_responses)
        ]

        return [self._scored_points_to_query_responses(response) for response in responses]
