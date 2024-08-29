# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc
import warnings

from . import qdrant_pb2 as qdrant__pb2

GRPC_GENERATED_VERSION = '1.66.0'
GRPC_VERSION = grpc.__version__
_version_not_supported = False

try:
    from grpc._utilities import first_version_is_lower
    _version_not_supported = first_version_is_lower(GRPC_VERSION, GRPC_GENERATED_VERSION)
except ImportError:
    _version_not_supported = True

if _version_not_supported:
    raise RuntimeError(
        f'The grpc package installed is at version {GRPC_VERSION},'
        + f' but the generated code in qdrant_pb2_grpc.py depends on'
        + f' grpcio>={GRPC_GENERATED_VERSION}.'
        + f' Please upgrade your grpc module to grpcio>={GRPC_GENERATED_VERSION}'
        + f' or downgrade your generated code using grpcio-tools<={GRPC_VERSION}.'
    )


class QdrantStub(object):
    """Missing associated documentation comment in .proto file."""

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.HealthCheck = channel.unary_unary(
                '/qdrant.Qdrant/HealthCheck',
                request_serializer=qdrant__pb2.HealthCheckRequest.SerializeToString,
                response_deserializer=qdrant__pb2.HealthCheckReply.FromString,
                _registered_method=True)


class QdrantServicer(object):
    """Missing associated documentation comment in .proto file."""

    def HealthCheck(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_QdrantServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'HealthCheck': grpc.unary_unary_rpc_method_handler(
                    servicer.HealthCheck,
                    request_deserializer=qdrant__pb2.HealthCheckRequest.FromString,
                    response_serializer=qdrant__pb2.HealthCheckReply.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'qdrant.Qdrant', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
    server.add_registered_method_handlers('qdrant.Qdrant', rpc_method_handlers)


 # This class is part of an EXPERIMENTAL API.
class Qdrant(object):
    """Missing associated documentation comment in .proto file."""

    @staticmethod
    def HealthCheck(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/qdrant.Qdrant/HealthCheck',
            qdrant__pb2.HealthCheckRequest.SerializeToString,
            qdrant__pb2.HealthCheckReply.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True)
