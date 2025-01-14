from typing import Type, Union

from qdrant_client.http import models

INFERENCE_OBJECT_NAMES: set[str] = {"Document", "Image", "InferenceObject"}
# INFERENCE_OBJECT_TYPES_TUPLE: tuple[
#     Type[models.Document], Type[models.Image], Type[models.InferenceObject]
# ] = (models.Document, models.Image, models.InferenceObject)
INFERENCE_OBJECT_TYPES = Union[models.Document, models.Image, models.InferenceObject]
