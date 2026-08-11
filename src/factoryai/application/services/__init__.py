"""Long-lived orchestration services shared across use cases within one process.

Unlike a use case (built fresh per call from :class:`~factoryai.bootstrap.container.
Container`), a service here holds state across calls — a warmed detector cache is the
whole point of not reloading a model on every request.
"""
