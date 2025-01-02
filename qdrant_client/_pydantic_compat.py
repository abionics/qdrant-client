import json

from typing import Any, Type, TypeVar

from pydantic import BaseModel
from pydantic.version import VERSION as PYDANTIC_VERSION

PYDANTIC_V2 = PYDANTIC_VERSION.startswith("2.")
Model = TypeVar("Model", bound="BaseModel")


if PYDANTIC_V2:
    import pydantic_core

    to_jsonable_python = pydantic_core.to_jsonable_python
else:
    from pydantic.json import ENCODERS_BY_TYPE

    def to_jsonable_python(x: Any) -> Any:
        return ENCODERS_BY_TYPE[type(x)](x)


def update_forward_refs(model_class: Type[BaseModel], *args: Any, **kwargs: Any) -> None:
    if PYDANTIC_V2:
        model_class.model_rebuild(*args, **kwargs)
    else:
        model_class.update_forward_refs(*args, **kwargs)


def construct(model_class: Type[Model], *args: Any, **kwargs: Any) -> Model:
    if PYDANTIC_V2:
        return model_class.model_construct(*args, **kwargs)
    else:
        return model_class.construct(*args, **kwargs)


def to_dict(model: BaseModel, *args: Any, **kwargs: Any) -> dict[Any, Any]:
    if PYDANTIC_V2:
        return model.model_dump(*args, **kwargs)
    else:
        return model.dict(*args, **kwargs)


def model_fields_set(model: BaseModel) -> set:
    if PYDANTIC_V2:
        return model.model_fields_set
    else:
        return model.__fields_set__


def model_json_schema(model: Type[BaseModel], *args: Any, **kwargs: Any) -> dict[str, Any]:
    if PYDANTIC_V2:
        return model.model_json_schema(*args, **kwargs)
    else:
        return json.loads(model.schema_json(*args, **kwargs))
