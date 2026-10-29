"""Adapters for tests that alter a synthetic stream without mislabeling its origin."""

from vl_product.source import SyntheticSource


class FixtureSource(SyntheticSource):
    def __init__(self, config, inputs):
        super().__init__(config)
        object.__setattr__(self, "inputs", inputs)

    def frames(self):
        return iter(self.inputs)

    def manifest(self):
        return {"source": "modified-test-fixture", "base": super().manifest()}
