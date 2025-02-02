# Copyright The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Usage
-----

.. code-block:: python

    from fastapi import FastAPI
    from fastapi_events.dispatcher import dispatch
    from fastapi_events.handlers.local import LocalHandler
    from fastapi_events.middleware import EventHandlerASGIMiddleware
    from opentelemetry.instrumentation.fastapi_events import FastAPIEventsInstrumentor

    app = FastAPI()
    app.add_middleware(
        EventHandlerASGIMiddleware, handlers=[local_handler]
    )

    FastAPIEventsInstrumentor().instrument()

    @local_handler.register(event_name="VISITOR_SPOTTED")
    async def handle_visitor_spotted(event):
        pass

    @app.get("/")
    async def index():
        dispatch("VISITOR_SPOTTED")
        return {"message": "visitor spotted"}
"""

from inspect import getmembers, isclass, ismodule
from typing import Collection

import fastapi_events
import fastapi_events.dispatcher
import wrapt
from fastapi_events.handlers.base import BaseEventHandler

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi_events.package import _instruments
from opentelemetry.instrumentation.instrumentor import BaseInstrumentor
from opentelemetry.instrumentation.utils import unwrap
from opentelemetry.trace import SpanKind


async def _handle_wrapper(wrapped, instance, args, kwargs):
    tracer = trace.get_tracer(__name__)
    event = args[0] if args else kwargs.get("event")
    with tracer.start_as_current_span(
        f"handling event {event[0]}", kind=SpanKind.CONSUMER
    ) as span:
        return await wrapped(event)


async def _handle_many_wrapper(wrapped, instance, args, kwargs):
    tracer = trace.get_tracer(__name__)
    events = args[0] if args else kwargs.get("events")
    with tracer.start_as_current_span(
        f"handling multiple events", kind=SpanKind.CONSUMER
    ) as span:
        return await wrapped(events)


def _dispatch_wrapper(wrapped, instance, args, kwargs):
    event_name = kwargs.get("event_name")
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span(
        f"Event {event_name} dispatched", kind=SpanKind.PRODUCER
    ) as span:
        return wrapped(*args, **kwargs)


class FastAPIEventsInstrumentor(BaseInstrumentor):
    def __init__(self):
        super().__init__()
        self._instrumented_classes = []

    def instrumentation_dependencies(self) -> Collection[str]:
        return _instruments

    def _instrument(self, **kwargs):
        for _, module in getmembers(fastapi_events.handlers, ismodule):
            for _, class_ in getmembers(module, isclass):
                if (
                    issubclass(class_, BaseEventHandler)
                    and class_ is not BaseEventHandler
                ):
                    self._instrumented_classes.append(class_)
                    wrapt.wrap_function_wrapper(
                        class_, "handle", _handle_wrapper
                    )
                    wrapt.wrap_function_wrapper(
                        class_, "handle_many", _handle_many_wrapper
                    )

        wrapt.wrap_function_wrapper(
            fastapi_events.dispatcher, "_dispatch", _dispatch_wrapper
        )

    def _uninstrument(self, **kwargs):
        for class_ in self._instrumented_classes:
            unwrap(class_, "handle")
            unwrap(class_, "handle_many")

        unwrap(fastapi_events.dispatcher, "dispatch")
